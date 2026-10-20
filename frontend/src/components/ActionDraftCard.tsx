/**
 * ActionDraftCard — 行动清单弹层（Radix Dialog）
 *
 * v3.11 B2: 改造为"计划头 + N 批次行"布局。
 * - 执行计划: 1 个计划头(symbol+side+总量) + N 个批次行(触发价/限价/数量 可编辑)
 * - 旧路径(单笔): 兼容，走原布局
 * - 恢复 trigger_price 可编辑
 */
import React, { useState, useRef, useEffect } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X, AlertTriangle, CheckCircle, Loader2, ChevronDown, ChevronUp } from 'lucide-react'
import type { ActionDraftResponse, SymbolStrategyDraft, IBKRLinkPreview, IBKRQuote, ExecutionBatchResponse } from '@/lib/api'
import { actionApi } from '@/lib/api'
import { tradeError } from '@/lib/trade-display'
import { formatDataSource, formatDegraded, formatFactors, formatConstraints } from '@/lib/plan-display'

interface MissingField {
  target_type: string
  target_index: number
  field: string
  description: string
}

interface PlanMeta {
  plan_id?: string
  factor_snapshot?: Record<string, unknown>
  constraints_applied?: Record<string, unknown>
  rationale?: string
  risk_notes_text?: string
}

interface IBKRPrefill {
  stage: 'edit' | 'review'
  preview: IBKRLinkPreview
  order: Record<string, string>
  quote: IBKRQuote | null
  limit: string
  dirty: boolean
  busy: boolean
  error: string
  onLimitChange: (value: string) => void
  onOrderChange: (field: string, value: string) => void
  onJoin: () => Promise<void>
  onBack: () => void
  onRecalculate: () => void
  onConfirm: () => Promise<void>
}

interface Props {
  open: boolean
  onClose: () => void
  draft: ActionDraftResponse | null
  onConfirmed: (draftId?: string) => void
  ibkr?: IBKRPrefill
  executionLabel?: string
  planMeta?: PlanMeta | null  // v3.11: 执行计划附加信息
}

const SIDE_LABEL: Record<string, string> = { BUY: '买入', SELL: '卖出' }
const TRIGGER_LABEL: Record<string, string> = {
  PRICE_BELOW: '低于', PRICE_ABOVE: '高于', IMMEDIATE: '立即', MANUAL: '手动',
}

function normalizeMissingFields(draft: ActionDraftResponse | null): MissingField[] {
  const rawMissing = draft?.missing_fields || draft?.payload?.missing_fields || []
  return rawMissing.map((field: MissingField | string) =>
    typeof field === 'string'
      ? { target_type: 'unknown', target_index: 0, field: 'unknown', description: field }
      : field
  )
}

export default function ActionDraftCard(props: Props) {
  return <ActionDraftCardContent key={props.ibkr?.preview.request_id ?? props.draft?.id ?? 'empty'} {...props} />
}

function ActionDraftCardContent({ open, onClose, draft, onConfirmed, planMeta, executionLabel, ibkr }: Props) {
  const busy = useRef(false)
  const [strategies, setStrategies] = useState<SymbolStrategyDraft[]>(
    () => draft?.payload?.symbol_strategies || [],
  )
  const intents = draft?.payload?.allocation_intents || []
  const riskNotes = draft?.risk_notes || draft?.payload?.risk_notes || []
  const [missingFields, setMissingFields] = useState<MissingField[]>(
    () => normalizeMissingFields(draft),
  )
  const summary = draft?.decision_summary || ''
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showFactors, setShowFactors] = useState(false)
  const [showConstraints, setShowConstraints] = useState(false)

  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (!ibkr) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [ibkr])
  const leg = ibkr?.quote?.batch.legs[0]
  const fresh = Boolean(leg?.quote_as_of && now - Date.parse(leg.quote_as_of) <= Number(ibkr?.quote?.batch.execution_policy.quote_max_age_seconds ?? 30) * 1000)
  const validEntry = ibkr && Boolean(ibkr.order.symbol.trim() && Number(ibkr.order.target_amount) > 0
    && Number.isFinite(Number(ibkr.order.target_amount))
    && (!ibkr.limit || (Number.isFinite(Number(ibkr.limit)) && Number(ibkr.limit) > 0)))
  const canConfirm = ibkr ? !ibkr.busy && (ibkr.stage === 'edit' ? validEntry
    : Boolean(ibkr.quote?.batch.status === 'READY' && fresh && !ibkr.dirty)) : missingFields.length === 0

  // 判断是否为执行计划模式(N 条同 symbol 同 side 的批次)
  const isExecutionPlan = strategies.length > 0 && planMeta?.plan_id
  const planSymbol = strategies[0]?.symbol || ''
  const planSide = strategies[0]?.side || 'BUY'
  const totalQty = strategies.reduce((s, st) => s + (st.quantity || 0), 0)

  async function handleConfirm() {
    if (!canConfirm || busy.current) return
    busy.current = true
    setConfirming(true)
    setError(null)
    try {
      if (ibkr) { await (ibkr.stage === 'edit' ? ibkr.onJoin() : ibkr.onConfirm()); return }
      if (planMeta?.plan_id) {
        // 执行计划路径: 先同步编辑后的数量到 DB，再 confirm
        const tranchesPayload = strategies.map((s, i) => ({
          sequence: i + 1,
          quantity: s.quantity || 0,
        }))
        const updateRes = await fetch(`/api/execution-plan/${planMeta.plan_id}/update-tranches`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tranches: tranchesPayload }),
        })
        if (!updateRes.ok) {
          const err = await updateRes.json().catch(() => ({ detail: updateRes.statusText }))
          const violations = err.detail?.violations || err.violations
          if (violations && Array.isArray(violations)) {
            throw new Error(violations.join('\n'))
          }
          const msg = err.error || err.detail || updateRes.statusText
          throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
        }

        const res = await fetch(`/api/execution-plan/${planMeta.plan_id}/confirm`, { method: 'POST' })
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: res.statusText }))
          const msg = err.error || err.detail || res.statusText
          throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
        }
      } else if (draft) {
        // 旧路径: ActionDraft confirm
        const updatedPayload = {
          symbol_strategies: strategies,
          allocation_intents: intents,
          risk_notes: riskNotes,
          missing_fields: missingFields,
        }
        let saved = draft
        if (draft.prefill_request_id) {
          saved = await actionApi.createDraft({
            prefill_request_id: draft.prefill_request_id,
            decision_summary: summary, payload: updatedPayload,
          })
          if (!['draft', 'confirmed'].includes(saved.status)) throw new Error('此链接的草稿已丢弃，请发起新的交易指令')
        }
        if (saved.status === 'draft') {
          await actionApi.updateDraft(saved.id, {
          decision_summary: summary,
          payload: draft.prefill_request_id ? saved.payload : updatedPayload,
          })
          await actionApi.confirmDraft(saved.id)
        }
        onConfirmed(saved.id)
        return
      }
      onConfirmed()
    } catch (e: unknown) {
      setError((e as Error).message || '确认失败')
    } finally {
      busy.current = false
      setConfirming(false)
    }
  }

  function handleFieldChange(idx: number, fieldName: string, value: string | number | null) {
    setStrategies(prev => {
      const next = [...prev]
      next[idx] = { ...next[idx], [fieldName]: value }
      return next
    })
    if (value !== null && value !== undefined && value !== '' && value !== 0) {
      setMissingFields(prev => prev.filter(mf =>
        !(mf.target_type === 'symbol_strategy' && mf.target_index === idx && mf.field === fieldName)
      ))
    }
  }

  const fs = planMeta?.factor_snapshot as Record<string, unknown> | undefined
  const ca = planMeta?.constraints_applied as Record<string, unknown> | undefined
  const dsm = (fs?.data_source_meta ?? {}) as Record<string, unknown>
  const degraded = (dsm.degraded_fields ?? []) as string[]

  return (
    <Dialog.Root open={open} onOpenChange={v => { if (!v) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', zIndex: 1000 }} />
        <Dialog.Content style={{
          position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
          background: '#fff', borderRadius: 16, padding: 0,
          width: 'min(800px, 96vw)', maxHeight: '85vh', overflow: 'hidden', display: 'flex', flexDirection: 'column',
          boxShadow: '0 20px 60px rgba(0,0,0,0.2)', zIndex: 1001,
        }}>
          {/* 头部 */}
          <div style={{
            padding: '20px 24px 16px', borderBottom: '1px solid #E5E7EB',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          }}>
            <Dialog.Title style={{ fontSize: 16, fontWeight: 700, color: '#1B2A4A', margin: 0 }}>
              {isExecutionPlan ? '📋 执行计划确认' : ibkr?.stage === 'review' ? '📋 投资行动核对' : '📋 行动清单'}
            </Dialog.Title>
            <Dialog.Close asChild>
              <button aria-label="关闭行动清单" style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 4, color: '#9CA3AF' }}>
                <X size={18} />
              </button>
            </Dialog.Close>
          </div>

          <div style={{ padding: '16px 24px', overflowY: 'auto', minHeight: 0, display: 'flex', flexDirection: 'column', gap: 16 }}>
            {executionLabel && <div role="status">交易环境：{executionLabel}。此步骤仅加入投资行动，最终提交仍需确认。</div>}

            {ibkr?.stage === 'edit' && <>
              <div role="status" style={{fontSize: 13, color: '#374151'}}>交易环境：IBKR。此步骤仅加入投资行动，最终提交仍需确认。</div>
              <Section title="标的策略">
                <div style={{border: '1px solid #E5E7EB', borderRadius: 10, padding: '14px 16px', background: '#FAFBFC'}}>
                  <div style={{display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12, alignItems: 'center'}}>
                    <input aria-label="标的及市场" value={ibkr.order.symbol}
                      onChange={event => ibkr.onOrderChange('symbol', event.target.value)} style={{...inputStyle, width: 148, maxWidth: '100%'}} />
                    <select aria-label="买卖方向" value={ibkr.order.side} onChange={event => ibkr.onOrderChange('side', event.target.value)}><option value="BUY">BUY</option></select>
                    <span style={{fontSize: 11, padding: '2px 8px', borderRadius: 4, background: '#DCFCE7', color: '#16A34A'}}>买入</span>
                  </div>
                  <div style={{display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10}}>
                    <EditableField label="总预算（USD，含费用）" value={ibkr.order.target_amount}
                      onChange={value => ibkr.onOrderChange('target_amount', value)} />
                    <EditableField label="限价（可选）" value={ibkr.limit} onChange={ibkr.onLimitChange} />
                    <label style={{fontSize: 13}}>订单类型 <select aria-label="订单类型" value={ibkr.order.order_type} onChange={event => ibkr.onOrderChange('order_type', event.target.value)}><option value="LIMIT">LIMIT</option></select></label>
                    <label style={{fontSize: 13}}>交易币种 <select aria-label="交易币种" value={ibkr.order.currency} onChange={event => ibkr.onOrderChange('currency', event.target.value)}><option value="USD">USD</option></select></label>
                    <label style={{fontSize: 13}}>有效期 <select aria-label="有效期" value={ibkr.order.time_in_force} onChange={event => ibkr.onOrderChange('time_in_force', event.target.value)}><option value="DAY">DAY</option></select></label>
                    <div style={{fontSize: 12, color: '#6B7280'}}>数量（股）：报价后计算整数股数</div>
                  </div>
                  <p style={{fontSize: 12, color: '#6B7280', marginTop: 12, marginBottom: 0}}>预算包含费用和资金安全垫；未填写限价时，下一步获取建议限价。</p>
                </div>
              </Section>
              <Section title="风险提示">
                <div style={{fontSize: 12, color: '#D97706', lineHeight: 1.6, paddingLeft: 8, borderLeft: '2px solid #F59E0B'}}>来自 ChatGPT 的预填参数，请核对后继续；最终订单仍需单独确认。</div>
              </Section>
              {ibkr.error && <p role="alert">{ibkr.error}</p>}
            </>}

            {ibkr?.stage === 'review' && <>
              <IBKRReviewFields batch={ibkr.quote?.batch ?? null} preview={ibkr.preview}
                limit={ibkr.limit} onLimitChange={ibkr.onLimitChange} />
              {ibkr.dirty && <p role="status">参数已修改，请重新计算股数、费用和 WhatIf；旧预览不可确认。</p>}
              {!fresh && ibkr.quote && <p role="status">报价已过期，请重新获取报价。</p>}
              <button disabled={ibkr.busy} onClick={ibkr.onRecalculate}>{ibkr.busy ? '正在读取 IBKR…' : '重新计算股数、费用及 WhatIf'}</button>
              {ibkr.error && <p role="alert">{ibkr.error}</p>}
              <p>正在核对已编辑的投资意图，尚未保存。报价、费用及资金校验通过后，才可保存并进入最终确认；此处不提交订单。</p>
              <button disabled={ibkr.busy} onClick={ibkr.onBack}>返回编辑参数</button>
            </>}

            {/* ── 执行计划模式: 计划头 + N 批次行 ── */}
            {isExecutionPlan && (
              <>
                {/* 计划头 */}
                <div style={{ background: '#EFF6FF', border: '1px solid #93C5FD', borderRadius: 10, padding: '14px 16px' }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
                    <span style={{ fontSize: 16, fontWeight: 700, color: '#1B2A4A' }}>{planSymbol}</span>
                    <span style={{
                      fontSize: 12, fontWeight: 600, padding: '2px 10px', borderRadius: 4,
                      background: planSide === 'BUY' ? '#DCFCE7' : '#FEE2E2',
                      color: planSide === 'BUY' ? '#16A34A' : '#DC2626',
                    }}>{SIDE_LABEL[planSide] || planSide}</span>
                  </div>
                  <div style={{ fontSize: 13, color: '#374151' }}>
                    共 <strong>{strategies.length}</strong> 批, 总计 <strong>{totalQty.toLocaleString()}</strong> 股
                  </div>
                </div>

                {/* 批次行 */}
                <Section title={`分批计划 (${strategies.length} 批)`}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                    <thead>
                      <tr style={{ borderBottom: '2px solid #E5E7EB' }}>
                        {['批次', '触发', '触发价', '限价', '数量'].map((h, hi) => (
                          <th key={h} style={{
                            padding: '6px 8px', textAlign: hi >= 2 ? 'right' : 'left',
                            color: '#9CA3AF', fontWeight: 500, fontSize: 11,
                          }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {strategies.map((s, i) => {
                        const triggerType = (s as unknown as Record<string, unknown>)._trigger_type as string || 'PRICE_BELOW'
                        return (
                          <tr key={i} style={{ borderBottom: '1px solid #F3F4F6' }}>
                            <td style={{ padding: '8px', color: '#374151', fontWeight: 500 }}>第 {i + 1} 批</td>
                            <td style={{ padding: '8px', color: '#6B7280', fontSize: 12 }}>
                              {TRIGGER_LABEL[triggerType] || triggerType}
                            </td>
                            <td style={{ padding: '8px', textAlign: 'right' }}>
                              <input type="text"
                                value={s.trigger_price ?? ''}
                                onChange={e => handleFieldChange(i, 'trigger_price', e.target.value ? parseFloat(e.target.value) : null)}
                                style={{ ...inputStyle, width: 80, textAlign: 'right' }}
                              />
                            </td>
                            <td style={{ padding: '8px', textAlign: 'right' }}>
                              <input type="text"
                                value={s.limit_price ?? ''}
                                onChange={e => handleFieldChange(i, 'limit_price', e.target.value ? parseFloat(e.target.value) : null)}
                                style={{ ...inputStyle, width: 80, textAlign: 'right' }}
                              />
                            </td>
                            <td style={{ padding: '8px', textAlign: 'right' }}>
                              <input type="text"
                                value={s.quantity ?? ''}
                                onChange={e => handleFieldChange(i, 'quantity', e.target.value ? parseInt(e.target.value) : null)}
                                style={{ ...inputStyle, width: 70, textAlign: 'right' }}
                              />
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </Section>

                {/* 决策依据(rationale) */}
                {planMeta?.rationale && (
                  <Section title="规则引擎解释">
                    <div style={{ fontSize: 12, color: '#4B5563', lineHeight: 1.6, background: '#F9FAFB',
                      borderRadius: 6, padding: '8px 10px', border: '1px solid #E5E7EB' }}>
                      {planMeta.rationale}
                    </div>
                  </Section>
                )}

                {/* 数据来源(人话) */}
                {fs && (
                  <Section title="数据来源">
                    <div style={{ fontSize: 11, color: '#6B7280' }}>
                      {formatDataSource(dsm)}
                    </div>
                    {degraded.length > 0 && (
                      <div style={{ fontSize: 11, color: '#D97706', marginTop: 4 }}>
                        {formatDegraded(degraded)}
                      </div>
                    )}
                  </Section>
                )}

                {/* 计划依据的关键指标 */}
                {fs && (
                  <div>
                    <button onClick={() => setShowFactors(!showFactors)} style={expandBtn}>
                      {showFactors ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                      计划依据的关键指标
                    </button>
                    {showFactors && (
                      <div style={{ background: '#F9FAFB', borderRadius: 6, padding: '8px 10px', marginTop: 4 }}>
                        {formatFactors(fs).map((f, i) => (
                          <div key={i} style={{ display: 'flex', gap: 6, marginBottom: 4, fontSize: 11, lineHeight: 1.5 }}>
                            <span style={{ color: '#374151', fontWeight: 600, minWidth: 80 }}>{f.label}</span>
                            <span style={{ color: '#1D4ED8', fontWeight: 600, minWidth: 45 }}>{f.value}</span>
                            <span style={{ color: '#6B7280' }}>{f.desc}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* 本计划遵守的纪律 */}
                {ca && (
                  <div>
                    <button onClick={() => setShowConstraints(!showConstraints)} style={expandBtn}>
                      {showConstraints ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                      本计划遵守的纪律
                    </button>
                    {showConstraints && (
                      <div style={{ background: '#F9FAFB', borderRadius: 6, padding: '8px 10px', marginTop: 4 }}>
                        {formatConstraints(ca, strategies.length, strategies[0]?.quantity ? 0.08 : 0).map((c, i) => (
                          <div key={i} style={{ fontSize: 11, color: c.checked ? '#059669' : '#D97706', marginBottom: 3, display: 'flex', gap: 4 }}>
                            <span>{c.checked ? '✓' : '⚠'}</span>
                            <span>{c.text}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </>
            )}

            {/* ── 旧路径模式: 逐条策略卡(兼容) ── */}
            {!isExecutionPlan && strategies.length > 0 && (
              <Section title="标的策略">
                {strategies.map((s, i) => (
                  <div key={i} style={{
                    border: '1px solid #E5E7EB', borderRadius: 10, padding: '14px 16px',
                    marginBottom: 10, background: '#FAFBFC',
                  }}>
                    <div style={{ display: 'flex', gap: 8, marginBottom: 10, alignItems: 'center' }}>
                      {draft?.prefill_request_id ? <>
                        <input aria-label="标的及市场" value={s.symbol} onChange={e => handleFieldChange(i, 'symbol', e.target.value)} style={inputStyle} />
                        <select aria-label="买卖方向" value={s.side} onChange={e => handleFieldChange(i, 'side', e.target.value)}><option>BUY</option><option>SELL</option></select>
                      </> : <span style={{ fontSize: 15, fontWeight: 700, color: '#1B2A4A' }}>{s.symbol}</span>}
                      <span style={{
                        fontSize: 11, fontWeight: 500, padding: '2px 8px', borderRadius: 4,
                        background: s.side === 'BUY' ? '#DCFCE7' : '#FEE2E2',
                        color: s.side === 'BUY' ? '#16A34A' : '#DC2626',
                      }}>{SIDE_LABEL[s.side] || s.side}</span>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                      <EditableField label="数量（股）" value={s.quantity ?? ''}
                        onChange={v => handleFieldChange(i, 'quantity', v ? (draft?.prefill_request_id ? Number(v) : parseInt(v)) : null)} />
                      <EditableField label="限价" value={s.limit_price ?? ''}
                        onChange={v => handleFieldChange(i, 'limit_price', v ? parseFloat(v) : null)} />
                      {draft?.prefill_request_id && <>
                        <label>订单类型<select aria-label="订单类型" value={s.order_type} onChange={e => handleFieldChange(i, 'order_type', e.target.value)}><option>LIMIT</option><option>CONDITIONAL_LIMIT</option></select></label>
                        <label>交易币种<input aria-label="交易币种" value={s.currency || ''} onChange={e => handleFieldChange(i, 'currency', e.target.value)} style={inputStyle} /></label>
                        <div>有效期：{s.time_in_force}</div>
                        {s.order_type === 'CONDITIONAL_LIMIT' && <EditableField label="触发价" value={s.trigger_price ?? ''} onChange={v => handleFieldChange(i, 'trigger_price', v ? Number(v) : null)} />}
                      </>}
                    </div>
                  </div>
                ))}
              </Section>
            )}

            {/* 资产配置(旧路径) */}
            {intents.length > 0 && (
              <Section title="资产配置调整">
                {intents.map((a, i) => (
                  <div key={i} style={{ border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 14px', marginBottom: 8, background: '#FAFBFC' }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: '#1B2A4A', marginBottom: 6 }}>{a.title}</div>
                    <div style={{ fontSize: 12, color: '#6B7280' }}>
                      {Object.entries(a.target_allocation).map(([k, v]) => (
                        <span key={k} style={{ marginRight: 12 }}>{k}: {typeof v === 'number' ? `${(v * 100).toFixed(0)}%` : String(v)}</span>
                      ))}
                    </div>
                  </div>
                ))}
              </Section>
            )}

            {/* 风险提示 */}
            {riskNotes.length > 0 && (
              <Section title="风险提示">
                {riskNotes.map((note, i) => (
                  <div key={i} style={{ fontSize: 12, color: '#D97706', lineHeight: 1.6, paddingLeft: 8, borderLeft: '2px solid #F59E0B', marginBottom: 4 }}>
                    {note}
                  </div>
                ))}
              </Section>
            )}

            {error && (
              <div style={{
                padding: '10px 14px', borderRadius: 8, fontSize: 13, lineHeight: 1.6,
                display: 'flex', alignItems: 'flex-start', gap: 8,
                background: error.includes('演示模式') ? '#FFF7ED' : '#FEF2F2',
                border: `1px solid ${error.includes('演示模式') ? '#FED7AA' : '#FECACA'}`,
                color: error.includes('演示模式') ? '#9A3412' : '#DC2626',
              }}>
                <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: 1 }} />
                <span>{error}</span>
              </div>
            )}
          </div>

          {/* 底部 */}
          <div style={{
            flexShrink: 0, padding: '16px 24px', borderTop: '1px solid #E5E7EB',
            display: 'flex', justifyContent: 'flex-end', gap: 10,
          }}>
            <button onClick={onClose} style={{
              padding: '8px 16px', fontSize: 13, borderRadius: 8,
              border: '1px solid #E5E7EB', background: '#fff', color: '#374151', cursor: 'pointer',
            }}>取消</button>
            <button onClick={handleConfirm} disabled={!canConfirm || confirming}
              style={{
                padding: '8px 20px', fontSize: 13, borderRadius: 8, fontWeight: 600,
                border: 'none',
                background: canConfirm ? '#1F2937' : '#D1D5DB',
                color: canConfirm ? '#fff' : '#9CA3AF',
                cursor: canConfirm ? 'pointer' : 'not-allowed',
                display: 'flex', alignItems: 'center', gap: 6,
              }}
            >
              {confirming
                ? <><Loader2 size={14} className="animate-spin" />确认中...</>
                : <><CheckCircle size={14} />{ibkr?.stage === 'review' ? '核对并进入最终确认' : '加入投资行动'}</>}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 8 }}>{title}</div>
      {children}
    </div>
  )
}

function EditableField({ label, value, onChange }: {
  label: string; value: string | number; onChange: (v: string) => void
}) {
  return (
    <div>
      <label style={{ fontSize: 11, color: '#6B7280', marginBottom: 2, display: 'block' }}>{label}</label>
      <input aria-label={label} type="text" value={value} onChange={e => onChange(e.target.value)} style={{
        width: '100%', padding: '6px 10px', fontSize: 13,
        border: '1px solid #E5E7EB', borderRadius: 6, outline: 'none', fontFamily: 'inherit',
      }} />
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  padding: '4px 8px', fontSize: 13, border: '1px solid #E5E7EB',
  borderRadius: 4, outline: 'none', fontFamily: 'inherit', fontVariantNumeric: 'tabular-nums',
}
const expandBtn: React.CSSProperties = {
  background: 'none', border: 'none', cursor: 'pointer',
  fontSize: 11, color: '#6B7280', display: 'flex', alignItems: 'center', gap: 2,
}


/** Shared fields inside the two existing P2 dialogs; no separate trading page. */
export function IBKRReviewFields({ batch, preview, limit, onLimitChange, readOnly = false }: {
  batch: ExecutionBatchResponse | null
  preview?: IBKRLinkPreview
  limit: string
  onLimitChange: (value: string) => void
  readOnly?: boolean
}) {
  const leg = batch?.legs[0]
  const instrument = leg ?? preview?.instrument
  const amount = (value: number | null | undefined) => value == null ? '待获取' : `${value.toLocaleString('en-US', {maximumFractionDigits: 4})} USD`
  const attention = batch?.attention_reason
  return <section data-testid="ibkr-review-fields" style={{fontSize: 13, lineHeight: 1.8, overflowWrap: 'anywhere'}}>
    <div><b>标的：</b>IB01:LSE · <b>方向：</b>买入 BUY · <b>券商：</b>IBKR · LIMIT / DAY</div>
    <details><summary>证券身份及诊断</summary><div><b>合约：</b>conId {instrument && ('resolved_con_id' in instrument ? instrument.resolved_con_id : instrument.con_id)} · {instrument?.isin}</div></details>
    <div><b>上市 / 执行 / 币种：</b>LSEETF / SMART / USD · Acc</div>
    <div><b>账户：</b>{batch?.account_masked ?? '现有 IBKR 账户，读取中'} · Portfolio {String(batch?.execution_policy.portfolio_id ?? '待核实')}</div>
    <div><b>总支出上限（含费用和安全垫）：</b><span data-testid="ibkr-link-target">{amount(batch?.stated_cash ?? Number(preview?.order.target_amount))}</span></div>
    <div><b>报价 Ask：</b>{amount(leg?.quote_ask)} · {leg?.quote_quality ?? '待获取'}</div>
    <div><b>报价时间：</b>{leg?.quote_as_of ?? '未知，不可提交'}</div>
    <div><b>建议限价：</b>{amount(leg?.suggested_limit)}</div>
    <label style={{display: 'block', margin: '8px 0'}}>限价（USD） <input aria-label="IBKR 限价" disabled={readOnly} inputMode="decimal" value={limit}
      onChange={event => onLimitChange(event.target.value)} style={{padding: 8, border: '1px solid #CBD5E1', borderRadius: 6, maxWidth: '100%'}} /></label>
    <div><b>整数股数：</b><span data-testid="ibkr-quantity">{leg?.final_quantity ?? '待计算'}</span></div>
    <div><b>订单名义金额：</b>{amount(batch?.estimated_total)}</div>
    <div><b>预计费用：</b>{leg?.what_if?.status === 'PASS' ? amount(batch?.estimated_fees) : '未取得，不可提交'}</div>
    <div><b>WhatIf：</b>{String(leg?.what_if?.status ?? '待校验')}</div>
    <div><b>可用原币现金：</b>{amount(batch?.usable_cash)}</div>
    <div><b>本次总预算剩余（含安全垫预留后）：</b><span data-testid="ibkr-budget-remaining">{amount(batch?.all_in_budget_remaining)}</span></div>
    <div><b>预计账户现金余量：</b><span data-testid="ibkr-account-cash-remaining">{amount(batch?.estimated_residual)}</span></div>
    <div><b>报价来源与有效期：</b>IBKR · {String(batch?.execution_policy.quote_max_age_seconds ?? 30)} 秒</div>
    <div><b>预计总支出（不含安全垫）：</b>{batch ? amount(batch.estimated_total + batch.estimated_fees) : '待计算'}</div>
    <div><b>资金安全垫：</b>{amount(batch?.safety_cushion)}</div>
    <div><b>资金占用（含费用及安全垫）：</b><span data-testid="ibkr-total-outlay">{batch ? amount(batch.estimated_total + batch.estimated_fees + batch.safety_cushion) : '待计算'}</span></div>
    <div><b>状态：</b>{batch?.status ?? '读取中'}</div>
    {batch && <details><summary>批次诊断</summary><div>{batch.id} / {batch.confirmation_version}</div></details>}
    {attention && <p role="alert">风险及阻断：{tradeError(Array.isArray(attention) ? attention.join('；') : attention)}</p>}
    <p style={{color: '#B45309'}}>金额、数量和费用均须经 IBKR 校验；报价过期、费用未知或资金不足时不可提交。</p>
  </section>
}

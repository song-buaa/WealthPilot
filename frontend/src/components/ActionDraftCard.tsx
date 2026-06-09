/**
 * ActionDraftCard — 行动清单弹层（Radix Dialog）
 *
 * v3.11 B2: 改造为"计划头 + N 批次行"布局。
 * - 执行计划: 1 个计划头(symbol+side+总量) + N 个批次行(触发价/限价/数量 可编辑)
 * - 旧路径(单笔): 兼容，走原布局
 * - 恢复 trigger_price 可编辑
 */
import React, { useState, useEffect } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X, AlertTriangle, CheckCircle, Loader2, ChevronDown, ChevronUp } from 'lucide-react'
import type { ActionDraftResponse, SymbolStrategyDraft } from '@/lib/api'
import { actionApi } from '@/lib/api'

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

interface Props {
  open: boolean
  onClose: () => void
  draft: ActionDraftResponse | null
  onConfirmed: () => void
  planMeta?: PlanMeta | null  // v3.11: 执行计划附加信息
}

const SIDE_LABEL: Record<string, string> = { BUY: '买入', SELL: '卖出' }
const TRIGGER_LABEL: Record<string, string> = {
  PRICE_BELOW: '低于', PRICE_ABOVE: '高于', IMMEDIATE: '立即', MANUAL: '手动',
}

export default function ActionDraftCard({ open, onClose, draft, onConfirmed, planMeta }: Props) {
  const [strategies, setStrategies] = useState<SymbolStrategyDraft[]>([])
  const [intents, setIntents] = useState<Array<{ title: string; target_allocation: Record<string, number> }>>([])
  const [riskNotes, setRiskNotes] = useState<string[]>([])
  const [missingFields, setMissingFields] = useState<MissingField[]>([])
  const [summary, setSummary] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showFactors, setShowFactors] = useState(false)
  const [showConstraints, setShowConstraints] = useState(false)

  useEffect(() => {
    if (draft?.payload) {
      setStrategies(draft.payload.symbol_strategies || [])
      setIntents(draft.payload.allocation_intents || [])
      setRiskNotes(draft.risk_notes || draft.payload.risk_notes || [])
      const rawMissing = draft.missing_fields || draft.payload.missing_fields || []
      const parsed: MissingField[] = rawMissing
        .map((mf: MissingField | string) =>
          typeof mf === 'string'
            ? { target_type: 'unknown', target_index: 0, field: 'unknown', description: mf }
            : mf
        )
      setMissingFields(parsed)
      setSummary(draft.decision_summary || '')
      setError(null)
    }
  }, [draft])

  const canConfirm = missingFields.length === 0

  // 判断是否为执行计划模式(N 条同 symbol 同 side 的批次)
  const isExecutionPlan = strategies.length > 0 && planMeta?.plan_id
  const planSymbol = strategies[0]?.symbol || ''
  const planSide = strategies[0]?.side || 'BUY'
  const totalQty = strategies.reduce((s, st) => s + (st.quantity || 0), 0)

  async function handleConfirm() {
    if (!canConfirm) return
    setConfirming(true)
    setError(null)
    try {
      if (planMeta?.plan_id) {
        // 执行计划路径: 调 /api/execution-plan/{plan_id}/confirm
        const res = await fetch(`/api/execution-plan/${planMeta.plan_id}/confirm`, { method: 'POST' })
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: res.statusText }))
          throw new Error(typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail))
        }
      } else if (draft) {
        // 旧路径: ActionDraft confirm
        const updatedPayload = {
          symbol_strategies: strategies,
          allocation_intents: intents,
          risk_notes: riskNotes,
          missing_fields: missingFields,
        }
        await actionApi.updateDraft(draft.id, {
          decision_summary: summary,
          payload: updatedPayload,
        })
        await actionApi.confirmDraft(draft.id)
      }
      onConfirmed()
    } catch (e: unknown) {
      setError((e as Error).message || '确认失败')
    } finally {
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
          width: 800, maxHeight: '85vh', overflow: 'auto',
          boxShadow: '0 20px 60px rgba(0,0,0,0.2)', zIndex: 1001,
        }}>
          {/* 头部 */}
          <div style={{
            padding: '20px 24px 16px', borderBottom: '1px solid #E5E7EB',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          }}>
            <Dialog.Title style={{ fontSize: 16, fontWeight: 700, color: '#1B2A4A', margin: 0 }}>
              {isExecutionPlan ? '📋 执行计划确认' : '📋 行动清单'}
            </Dialog.Title>
            <Dialog.Close asChild>
              <button style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 4, color: '#9CA3AF' }}>
                <X size={18} />
              </button>
            </Dialog.Close>
          </div>

          <div style={{ padding: '16px 24px', display: 'flex', flexDirection: 'column', gap: 16 }}>

            {/* ── 执行计划模式: 计划头 + N 批次行 ── */}
            {isExecutionPlan && (
              <>
                {/* 计划头 */}
                <div style={{ background: '#F0F9FF', border: '1px solid #BAE6FD', borderRadius: 10, padding: '14px 16px' }}>
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
                        const triggerType = (s as Record<string, unknown>)._trigger_type as string || 'PRICE_BELOW'
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

                {/* 数据来源 */}
                {fs && (
                  <Section title="数据来源">
                    <div style={{ fontSize: 11, color: '#9CA3AF', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      <span>K线: {String(dsm.kline_source || 'N/A')} ({String(dsm.kline_points || 0)}根)</span>
                      <span>行情: {String(dsm.price_source || 'N/A')}{dsm.is_realtime ? ' (实时)' : ''}</span>
                    </div>
                    {degraded.length > 0 && (
                      <div style={{ fontSize: 11, color: '#D97706', marginTop: 4 }}>
                        数据降级: {degraded.join(', ')}
                      </div>
                    )}
                  </Section>
                )}

                {/* 因子快照(折叠) */}
                {fs && (
                  <div>
                    <button onClick={() => setShowFactors(!showFactors)} style={expandBtn}>
                      {showFactors ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                      计划依据指标
                    </button>
                    {showFactors && (
                      <pre style={preStyle}>
                        {JSON.stringify({
                          current_price: fs.current_price, atr14: fs.atr14,
                          volatility_annual: fs.volatility_annual,
                          price_percentile: fs.price_percentile,
                          drawdown_from_high: fs.drawdown_from_high,
                          trend_signal: fs.trend_signal, rsi14: fs.rsi14,
                        }, null, 2)}
                      </pre>
                    )}
                  </div>
                )}

                {/* 纪律约束(折叠) */}
                {ca && (
                  <div>
                    <button onClick={() => setShowConstraints(!showConstraints)} style={expandBtn}>
                      {showConstraints ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                      遵守的纪律约束
                    </button>
                    {showConstraints && (
                      <pre style={preStyle}>{JSON.stringify(ca, null, 2)}</pre>
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
                      <span style={{ fontSize: 15, fontWeight: 700, color: '#1B2A4A' }}>{s.symbol}</span>
                      <span style={{
                        fontSize: 11, fontWeight: 500, padding: '2px 8px', borderRadius: 4,
                        background: s.side === 'BUY' ? '#DCFCE7' : '#FEE2E2',
                        color: s.side === 'BUY' ? '#16A34A' : '#DC2626',
                      }}>{SIDE_LABEL[s.side] || s.side}</span>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                      <EditableField label="数量（股）" value={s.quantity ?? ''}
                        onChange={v => handleFieldChange(i, 'quantity', v ? parseInt(v) : null)} />
                      <EditableField label="限价" value={s.limit_price ?? ''}
                        onChange={v => handleFieldChange(i, 'limit_price', v ? parseFloat(v) : null)} />
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
              <div style={{ padding: '8px 12px', background: '#FEF2F2', borderRadius: 8, fontSize: 12, color: '#DC2626', display: 'flex', alignItems: 'center', gap: 6 }}>
                <AlertTriangle size={14} /> {error}
              </div>
            )}
          </div>

          {/* 底部 */}
          <div style={{
            padding: '16px 24px', borderTop: '1px solid #E5E7EB',
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
                background: canConfirm ? '#1B2A4A' : '#D1D5DB',
                color: canConfirm ? '#fff' : '#9CA3AF',
                cursor: canConfirm ? 'pointer' : 'not-allowed',
                display: 'flex', alignItems: 'center', gap: 6,
              }}
            >
              {confirming
                ? <><Loader2 size={14} className="animate-spin" />确认中...</>
                : <><CheckCircle size={14} />加入投资行动</>}
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
      <input type="text" value={value} onChange={e => onChange(e.target.value)} style={{
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
const preStyle: React.CSSProperties = {
  fontSize: 10, color: '#6B7280', background: '#F9FAFB', borderRadius: 4,
  padding: 6, overflow: 'auto', maxHeight: 150, margin: '4px 0 0',
}

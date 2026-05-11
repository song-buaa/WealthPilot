/**
 * ActionDraftCard — 行动清单弹层（Radix Dialog）
 *
 * v0.6 P0 修复：
 * - 确认前先 PATCH draft.payload 到后端（持久化用户编辑 + 清理 missing_fields）
 * - 单一数据源：strategies / missingFields 是可编辑的本地 state，
 *   提交时组装为完整 payload 发给后端
 */
import React, { useState, useEffect } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X, AlertTriangle, CheckCircle, Loader2, Lightbulb } from 'lucide-react'
import type { ActionDraftResponse, SymbolStrategyDraft } from '@/lib/api'
import { actionApi } from '@/lib/api'

interface MissingField {
  target_type: string
  target_index: number
  field: string
  description: string
}

interface Props {
  open: boolean
  onClose: () => void
  draft: ActionDraftResponse | null
  onConfirmed: () => void
}

export default function ActionDraftCard({ open, onClose, draft, onConfirmed }: Props) {
  const [strategies, setStrategies] = useState<SymbolStrategyDraft[]>([])
  const [intents, setIntents] = useState<Array<{ title: string; target_allocation: Record<string, number> }>>([])
  const [riskNotes, setRiskNotes] = useState<string[]>([])
  const [missingFields, setMissingFields] = useState<MissingField[]>([])
  const [summary, setSummary] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [editedFields, setEditedFields] = useState<Set<string>>(new Set())

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
        // 兜底：过滤 trigger_price（v3.2 MVP 不支持条件单，即使 LLM 误输出也不卡住用户）
        .filter(mf => mf.field !== 'trigger_price')
      setMissingFields(parsed)
      setSummary(draft.decision_summary || '')
      setEditedFields(new Set())
      setError(null)
    }
  }, [draft])

  const canConfirm = missingFields.length === 0

  /**
   * P0 修复核心：确认前先 PATCH payload 到后端。
   * 组装当前 state 为完整 payload，包含用户编辑后的字段值和清理后的 missing_fields。
   */
  async function handleConfirm() {
    if (!draft || !canConfirm) return
    setConfirming(true)
    setError(null)
    try {
      // Step 1: 组装当前编辑后的 payload
      const updatedPayload = {
        symbol_strategies: strategies,
        allocation_intents: intents,
        risk_notes: riskNotes,
        missing_fields: missingFields, // 已被用户编辑清空
      }

      // Step 2: PATCH 到后端持久化（用户编辑的值 + 清空的 missing_fields）
      await actionApi.updateDraft(draft.id, {
        decision_summary: summary,
        payload: updatedPayload,
      })

      // Step 3: 确认
      await actionApi.confirmDraft(draft.id)
      onConfirmed()
    } catch (e: unknown) {
      setError((e as Error).message || '确认失败')
    } finally {
      setConfirming(false)
    }
  }

  function handleStrategyFieldChange(
    idx: number, fieldName: string, value: string | number | null,
  ) {
    // a. 更新 strategies state（本地数据源，提交时用这个）
    setStrategies(prev => {
      const next = [...prev]
      next[idx] = { ...next[idx], [fieldName]: value }
      return next
    })

    // b. 标记为用户已编辑
    setEditedFields(prev => new Set(prev).add(`strategy_${idx}_${fieldName}`))

    // c. 非空时从 missingFields 移除对应项
    if (value !== null && value !== undefined && value !== '' && value !== 0) {
      setMissingFields(prev => prev.filter(mf =>
        !(mf.target_type === 'symbol_strategy' &&
          mf.target_index === idx &&
          mf.field === fieldName)
      ))
    }
  }

  function isMissing(targetType: string, targetIndex: number, fieldName: string): boolean {
    return missingFields.some(mf =>
      mf.target_type === targetType &&
      mf.target_index === targetIndex &&
      mf.field === fieldName
    )
  }

  function getMissingDescription(targetType: string, targetIndex: number, fieldName: string): string | null {
    const mf = missingFields.find(m =>
      m.target_type === targetType && m.target_index === targetIndex && m.field === fieldName
    )
    return mf?.description || null
  }

  function getValueSource(idx: number, fieldName: string): string | null {
    const key = `strategy_${idx}_${fieldName}`
    if (editedFields.has(key)) return null
    const vs = strategies[idx]?.value_sources as Record<string, string> | null | undefined
    return vs?.[fieldName] || null
  }

  const sideLabel: Record<string, string> = { BUY: '买入', SELL: '卖出' }

  return (
    <Dialog.Root open={open} onOpenChange={v => { if (!v) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', zIndex: 1000,
        }} />
        <Dialog.Content style={{
          position: 'fixed', top: '50%', left: '50%',
          transform: 'translate(-50%, -50%)',
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
              📋 行动清单（基于本次对话）
            </Dialog.Title>
            <Dialog.Close asChild>
              <button style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 4, color: '#9CA3AF' }}>
                <X size={18} />
              </button>
            </Dialog.Close>
          </div>

          {/* 内容 */}
          <div style={{ padding: '16px 24px', display: 'flex', flexDirection: 'column', gap: 16 }}>

            <Section title="决策依据">
              <textarea
                value={summary}
                onChange={e => setSummary(e.target.value)}
                rows={3}
                style={{
                  width: '100%', border: '1px solid #E5E7EB', borderRadius: 8,
                  padding: '10px 14px', fontSize: 13, resize: 'vertical',
                  fontFamily: 'inherit', lineHeight: 1.6, color: '#374151',
                }}
              />
            </Section>

            {strategies.length > 0 && (
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
                      }}>{sideLabel[s.side] || s.side}</span>
                      <span style={{ fontSize: 11, color: '#9CA3AF' }}>限价单</span>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                      <FieldWithSource
                        label="数量（股）"
                        value={s.quantity ?? ''}
                        onChange={v => handleStrategyFieldChange(i, 'quantity', v ? parseInt(v) : null)}
                        missing={isMissing('symbol_strategy', i, 'quantity')}
                        missingDesc={getMissingDescription('symbol_strategy', i, 'quantity')}
                        valueSource={getValueSource(i, 'quantity')}
                      />
                      <FieldWithSource
                        label="限价"
                        value={s.limit_price ?? ''}
                        onChange={v => handleStrategyFieldChange(i, 'limit_price', v ? parseFloat(v) : null)}
                        missing={isMissing('symbol_strategy', i, 'limit_price')}
                        missingDesc={getMissingDescription('symbol_strategy', i, 'limit_price')}
                        valueSource={getValueSource(i, 'limit_price')}
                      />
                      {/* trigger_price 已移除 — v3.2 MVP 仅支持 LIMIT */}
                    </div>
                  </div>
                ))}
              </Section>
            )}

            {intents.length > 0 && (
              <Section title="资产配置调整">
                {intents.map((a, i) => (
                  <div key={i} style={{
                    border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 14px',
                    marginBottom: 8, background: '#FAFBFC',
                  }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: '#1B2A4A', marginBottom: 6 }}>{a.title}</div>
                    <div style={{ fontSize: 12, color: '#6B7280' }}>
                      {Object.entries(a.target_allocation).map(([k, v]) => (
                        <span key={k} style={{ marginRight: 12 }}>
                          {k}: {typeof v === 'number' ? `${(v * 100).toFixed(0)}%` : String(v)}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </Section>
            )}

            {riskNotes.length > 0 && (
              <Section title="⚠️ 风险提示">
                {riskNotes.map((note, i) => (
                  <div key={i} style={{
                    fontSize: 12, color: '#D97706', lineHeight: 1.6,
                    paddingLeft: 8, borderLeft: '2px solid #F59E0B', marginBottom: 4,
                  }}>{note}</div>
                ))}
              </Section>
            )}

            {missingFields.length > 0 && (
              <Section title="❗ 需要补充">
                {missingFields.map((mf, i) => (
                  <div key={i} style={{
                    fontSize: 12, color: '#DC2626', lineHeight: 1.6,
                    paddingLeft: 8, borderLeft: '2px solid #EF4444', marginBottom: 4,
                  }}>{mf.description || `${mf.field} 缺失`}</div>
                ))}
              </Section>
            )}

            {error && (
              <div style={{
                padding: '8px 12px', background: '#FEF2F2', borderRadius: 8,
                fontSize: 12, color: '#DC2626', display: 'flex', alignItems: 'center', gap: 6,
              }}>
                <AlertTriangle size={14} />
                {error}
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
            <button
              onClick={handleConfirm}
              disabled={!canConfirm || confirming}
              title={!canConfirm ? `请先补齐 ${missingFields.length} 项缺失字段` : ''}
              style={{
                padding: '8px 20px', fontSize: 13, borderRadius: 8, fontWeight: 600,
                border: 'none',
                background: canConfirm ? '#1B2A4A' : '#D1D5DB',
                color: canConfirm ? '#fff' : '#9CA3AF',
                cursor: canConfirm ? 'pointer' : 'not-allowed',
                display: 'flex', alignItems: 'center', gap: 6,
              }}
            >
              {confirming ? <><Loader2 size={14} className="animate-spin" />确认中...</> : <><CheckCircle size={14} />加入投资行动</>}
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

function FieldWithSource({ label, value, onChange, missing, missingDesc, valueSource }: {
  label: string; value: string | number; onChange: (v: string) => void
  missing?: boolean; missingDesc?: string | null; valueSource?: string | null
}) {
  return (
    <div>
      <label style={{ fontSize: 11, color: '#6B7280', marginBottom: 2, display: 'block' }}>
        {label}{missing && <span style={{ color: '#DC2626' }}> *必填</span>}
      </label>
      <input type="text" value={value} onChange={e => onChange(e.target.value)} style={{
        width: '100%', padding: '6px 10px', fontSize: 13,
        border: `1px solid ${missing ? '#EF4444' : '#E5E7EB'}`,
        borderRadius: 6, outline: 'none', fontFamily: 'inherit',
        background: missing ? '#FEF2F2' : '#fff',
      }} />
      {missing && missingDesc && <div style={{ fontSize: 11, color: '#DC2626', marginTop: 2 }}>{missingDesc}</div>}
      {!missing && valueSource && (
        <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 2, display: 'flex', alignItems: 'center', gap: 3 }}>
          <Lightbulb size={10} /> AI 建议：{valueSource}
        </div>
      )}
    </div>
  )
}

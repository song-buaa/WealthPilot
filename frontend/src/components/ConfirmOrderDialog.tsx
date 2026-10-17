/**
 * ConfirmOrderDialog — M6 人工最终确认下单弹窗
 *
 * 交互规则：
 * - 打开时自动调 check_risk API 获取风控结果
 * - 风控全通过 → 绿色 checkmarks，无文字确认框
 * - 触发警告 → 显示警告 + 文字确认框（精确输入"我已知晓风险并坚持下单"）
 * - checkbox "我已确认订单参数无误" 必须勾选
 * - 两个条件同时满足才能点"确认提交"
 */
import { useState, useEffect, useRef } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { Loader2 } from 'lucide-react'
import { actionApi, executionBatchApi, batchRiskWarnings, type ExecutionBatchResponse, type SymbolStrategyResponse } from '@/lib/api'
import { IBKRReviewFields } from './ActionDraftCard'
import { useToast } from '@/hooks/useToast'

interface RiskWarning {
  rule: string
  severity: string
  message: string
  detail: Record<string, unknown>
}

interface RiskCheckResponse {
  review_id: number
  portfolio_id: number
  account_masked: string
  broker: string
  time_in_force: 'DAY' | 'GTC'
  passed: boolean
  requires_confirmation: boolean
  warnings: RiskWarning[]
  portfolio_total_value: number
  confirmation_text_required: string | null
}

type IBKRConfirmation = { batch: ExecutionBatchResponse; requestId: string; onChanged: (batch: ExecutionBatchResponse) => void }
type Props = { open: boolean; onClose: () => void; onOrderPlaced: () => void } & (
  {strategy: SymbolStrategyResponse; ibkr?: never} | {strategy?: never; ibkr: IBKRConfirmation}
)

const CONFIRMATION_TEXT = '我已知晓风险并坚持下单'

export default function ConfirmOrderDialog({ open, onClose, strategy = {} as SymbolStrategyResponse, onOrderPlaced, ibkr }: Props) {
  const [submitError, setSubmitError] = useState<string | null>(null)
  const busy = useRef(false)
  const [limit, setLimit] = useState(ibkr?.batch.legs[0].final_limit?.toString() ?? '')
  const [dirty, setDirty] = useState(false)
  const [now, setNow] = useState(Date.now)
  const [safety, setSafety] = useState<{ibkr_read_only_mode: boolean; live_trading_enabled: boolean} | null>(null)
  const batch = ibkr?.batch
  const leg = batch?.legs[0]
  const executed = Boolean(batch && (batch.status === 'COMPLETED' || batch.legs.some(item => item.linked_order_id)))
  const fresh = Boolean(leg?.quote_as_of && now - Date.parse(leg.quote_as_of) <= Number(batch?.execution_policy.quote_max_age_seconds ?? 30) * 1000)
  useEffect(() => {
    if (!ibkr) return
    executionBatchApi.safety().then(setSafety).catch(() => setSafety(null))
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [ibkr])
  useEffect(() => {
    if (!ibkr) return
    const pending = ibkr.batch.legs.filter(item => item.linked_order_id && !['FILLED','CANCELLED','REJECTED'].includes(item.status))
    if (!pending.length) return
    const timer = setTimeout(async () => {
      try {
        await Promise.all(pending.map(item => actionApi.getOrder(item.linked_order_id!, true)))
        ibkr.onChanged(await executionBatchApi.get(ibkr.batch.id))
      } catch (err) { setSubmitError((err as Error).message) }
    }, 5000)
    return () => clearTimeout(timer)
  }, [ibkr])
  const [executionLabel, setExecutionLabel] = useState('正在核实券商模式')
  const { showToast } = useToast()

  const [riskLoading, setRiskLoading] = useState(true)
  const [riskResult, setRiskResult] = useState<RiskCheckResponse | null>(null)
  const [riskError, setRiskError] = useState<string | null>(null)

  const [confirmText, setConfirmText] = useState('')
  const [checkboxChecked, setCheckboxChecked] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const quantity = strategy.target_quantity || 0
  const limitPrice = strategy.limit_price || 0
  const estimatedAmount = quantity * limitPrice
  const sideLabel = strategy.side === 'BUY' ? '买入' : '卖出'

  // Run risk check when dialog opens
  useEffect(() => {
    if (!open || ibkr) return

    let active = true
    actionApi.executionContext().then(result => { if (active) setExecutionLabel(result.label) })
      .catch(() => { if (active) setExecutionLabel('无法核实券商模式，请勿将结果视为实盘') })
    actionApi.checkRisk(strategy.id, {
      quantity,
      limit_price: limitPrice,
    })
      .then(res => { if (active) setRiskResult(res) })
      .catch(err => { if (active) setRiskError(err.message || '风控检查失败') })
      .finally(() => { if (active) setRiskLoading(false) })
    return () => { active = false }
  }, [open, strategy.id, quantity, limitPrice, ibkr])

  const ibkrWarnings = batch ? batchRiskWarnings(batch) : []
  const needsConfirmation = ibkr ? ibkrWarnings.length > 0 && !batch?.execution_policy.risk_acknowledged : riskResult?.requires_confirmation ?? false
  const textMatch = confirmText === CONFIRMATION_TEXT

  const canSubmit = ibkr ? Boolean(!executed && checkboxChecked && !submitting && !dirty && fresh && batch?.status === 'CONFIRMED'
    && safety?.live_trading_enabled && !safety.ibkr_read_only_mode && (!needsConfirmation || textMatch)) :
    checkboxChecked &&
    !submitting &&
    riskResult !== null &&
    !riskError &&
    (!needsConfirmation || textMatch)

  async function recalculateIBKR() {
    if (!ibkr || busy.current) return
    busy.current = true; setSubmitting(true); setCheckboxChecked(false); setSubmitError(null); setDirty(true)
    try {
      const next = await executionBatchApi.applyManualLimits(ibkr.batch.id, {IB01: Number(limit)}, ibkr.batch.confirmation_version)
      ibkr.onChanged(next)
    } catch (err) { setSubmitError(`${(err as Error).message}。旧确认已撤销，请取消并重新生成交易链接。`) }
    finally { busy.current = false; setSubmitting(false) }
  }

  async function confirmIBKRPlan() {
    if (!ibkr || dirty || !fresh || busy.current) return
    busy.current = true; setSubmitting(true); setSubmitError(null)
    try { ibkr.onChanged(await executionBatchApi.confirm(ibkr.batch.id, ibkr.batch.confirmation_version, confirmText)) }
    catch (err) { setSubmitError((err as Error).message) }
    finally { busy.current = false; setSubmitting(false) }
  }

  async function handleSubmit() {
    if (!canSubmit || busy.current) return
    busy.current = true
    setSubmitting(true)
    setSubmitError(null)

    // v3.4 M5: 超时提示
    const timeoutWarning = setTimeout(() => {
      setSubmitError('正在等待券商确认，请稍候...')
    }, 15000)
    const timeoutCritical = setTimeout(() => {
      setSubmitError('响应较慢，可能是网络问题，建议登录券商 App 核实')
    }, 30000)

    try {
      if (ibkr) {
        const current = ibkr.batch
        for (const item of current.legs) {
          if (!item.linked_order_id) await executionBatchApi.submitLeg(current.id, item.id, current.confirmation_version,
            `确认并提交 ${current.legs.length} 笔 IBKR 实盘限价单`)
        }
        ibkr.onChanged(await executionBatchApi.get(current.id))
        clearTimeout(timeoutWarning); clearTimeout(timeoutCritical)
        return
      }
      const result = await actionApi.placeOrder(strategy.id, {
        quantity,
        limit_price: limitPrice,
        confirmation_text: needsConfirmation ? confirmText : '',
        review_id: riskResult!.review_id,
        time_in_force: riskResult!.time_in_force,
      })
      clearTimeout(timeoutWarning)
      clearTimeout(timeoutCritical)

      // v3.4 M5: 根据返回状态显示不同提示
      if (result?.status === 'rejected') {
        showToast('error', '订单被券商拒绝，请检查标的代码和下单参数')
      } else if (result?.status === 'unknown') {
        showToast('error', '提交结果未知，正在核验券商状态；请勿补单')
      } else {
        showToast('success', result?.status === 'filled' ? '订单已成交' : '订单已提交至券商，等待成交')
      }
      onOrderPlaced()
      onClose()
    } catch (err: unknown) {
      clearTimeout(timeoutWarning)
      clearTimeout(timeoutCritical)
      const msg = err instanceof Error ? err.message : '下单失败'
      setSubmitError(msg)
    } finally {
      busy.current = false
      setSubmitting(false)
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={v => { if (!v) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', zIndex: 8000,
        }} />
        <Dialog.Content data-testid="confirm-order-dialog" style={{
          position: 'fixed', top: '50%', left: '50%',
          transform: 'translate(-50%, -50%)',
          background: '#fff', borderRadius: 12, padding: 0,
          width: ibkr ? 'min(640px, 96vw)' : 'min(480px, 96vw)', maxHeight: '90vh', overflow: 'hidden', display: 'flex', flexDirection: 'column',
          boxShadow: '0 8px 32px rgba(0,0,0,0.18)',
          zIndex: 8001,
        }}>
          {/* Header */}
          <div style={{
            flexShrink: 0, padding: '16px 20px', borderBottom: '1px solid #E5E7EB',
            fontSize: 16, fontWeight: 700, color: '#1B2A4A',
          }}>
            <span style={{ marginRight: 8 }}>&#9888;&#65039;</span>
            {executed ? '交易结果' : '最终确认下单'}
          </div>

          <div style={{ padding: '16px 20px', overflowY: 'auto', minHeight: 0 }}>
            <div role="status" style={{ marginBottom: 12 }}>交易环境：{ibkr ? 'IBKR · LSEETF / USD / SMART' : executionLabel}</div>
            {ibkr && <>
              <p>当前请求：{ibkr.requestId}</p>
              {executed && <p role="status" data-testid="ibkr-existing-order">此请求已关联订单：{batch?.status} / {leg?.status}。不会重复准备或提交买单。</p>}
              <IBKRReviewFields batch={ibkr.batch} readOnly={executed} limit={limit} onLimitChange={value => {setLimit(value); setDirty(true); setCheckboxChecked(false)}} />
              <button disabled={submitting || executed} onClick={recalculateIBKR}>重新计算股数、费用及 WhatIf</button>
              {dirty && <p role="status">限价已修改或重新计算失败，旧确认不可提交。</p>}
              {!executed && !fresh && <p role="status">报价已过期，请重新计算并重新确认。</p>}
              {(!safety?.live_trading_enabled || safety.ibkr_read_only_mode) && <p role="status">IBKR 当前保留只读保护，尚不可提交真实订单。</p>}
            </>}
            {!ibkr && <>
            {/* Order Summary */}
            <div style={{
              background: '#F9FAFB', borderRadius: 8, padding: '12px 16px',
              marginBottom: 16, fontSize: 13, lineHeight: 1.8,
            }}>
              <div>即将提交以下订单：</div>
              <div style={{ marginTop: 8 }}>
                <div><b>标的：</b>{strategy.symbol}</div>
                <div><b>方向：</b><span style={{ color: strategy.side === 'BUY' ? '#16A34A' : '#DC2626', fontWeight: 600 }}>{sideLabel}</span></div>
                <div><b>数量：</b>{quantity} 股</div>
                <div><b>限价：</b>{strategy.currency ? `${limitPrice} ${strategy.currency}` : `$${limitPrice}`}</div>
                <div><b>订单类型：</b>{strategy.order_type === 'LIMIT' ? '限价单' : strategy.order_type}</div>
                {strategy.trigger_price != null && <div><b>策略触发价：</b>{strategy.trigger_price}（按原策略/适配器语义，不代表独立券商止损单）</div>}
                <div><b>有效期：</b>{riskResult?.time_in_force ?? 'GTC'}</div>
                <div><b>预估金额：</b>约 {strategy.currency ? `${estimatedAmount.toLocaleString()} ${strategy.currency}` : `$${estimatedAmount.toLocaleString()}`}</div>
              </div>
            </div>

            {/* Risk Check */}
            <div style={{
              borderRadius: 8, padding: '12px 16px', marginBottom: 16,
              border: '1px solid #E5E7EB',
            }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: '#374151' }}>
                风险检查：
              </div>
              {riskLoading && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#6B7280', fontSize: 13 }}>
                  <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} />
                  正在执行风控检查...
                </div>
              )}
              {riskError && (
                <div style={{ color: '#DC2626', fontSize: 13 }}>
                  风控检查失败：{riskError}
                </div>
              )}
              {riskResult && !riskError && (
                <>
                  {riskResult.passed ? (
                    <div data-testid="risk-all-passed" style={{ color: '#16A34A', fontSize: 13 }}>
                      <span style={{ marginRight: 4 }}>&#10003;</span> 所有风控检查通过
                    </div>
                  ) : (
                    <div>
                      {riskResult.warnings.map((w, i) => (
                        <div key={i} data-testid={`risk-warning-${w.rule}`} style={{
                          color: '#D97706', fontSize: 13, marginBottom: 4,
                          display: 'flex', alignItems: 'flex-start', gap: 6,
                        }}>
                          <span style={{ flexShrink: 0 }}>&#9888;&#65039;</span>
                          <span>{w.message}</span>
                        </div>
                      ))}
                      {/* Check rules that passed (not in warnings) */}
                      {!riskResult.warnings.some(w => w.rule === 'single_order_pct') && (
                        <div style={{ color: '#16A34A', fontSize: 13, marginBottom: 4 }}>
                          <span style={{ marginRight: 4 }}>&#10003;</span> 单笔金额未超 5% 警戒线
                        </div>
                      )}
                      {!riskResult.warnings.some(w => w.rule === 'concentration') && (
                        <div style={{ color: '#16A34A', fontSize: 13, marginBottom: 4 }}>
                          <span style={{ marginRight: 4 }}>&#10003;</span> 单标的持仓未超 40% 上限
                        </div>
                      )}
                      {!riskResult.warnings.some(w => w.rule === 'discipline') && (
                        <div style={{ color: '#16A34A', fontSize: 13, marginBottom: 4 }}>
                          <span style={{ marginRight: 4 }}>&#10003;</span> 无纪律违反
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>

            </>}

            {ibkrWarnings.map((warning, index) => <p key={index} role="alert">{warning.message}</p>)}
            {!ibkr && riskResult && <p>Portfolio {riskResult.portfolio_id} · {riskResult.broker} · {riskResult.account_masked}</p>}
            {/* Confirmation text input (only when risk warnings exist) */}
            {needsConfirmation && (
              <div style={{
                background: '#FFFBEB', border: '1px solid #FDE68A',
                borderRadius: 8, padding: '12px 16px', marginBottom: 16,
              }}>
                <div style={{ fontSize: 13, color: '#92400E', marginBottom: 8 }}>
                  <span style={{ marginRight: 4 }}>&#9888;&#65039;</span>
                  此订单触发了风险提示，请输入以下文字确认您已了解风险：
                </div>
                <input
                  data-testid="risk-confirm-input"
                  type="text"
                  value={confirmText}
                  onChange={e => setConfirmText(e.target.value)}
                  placeholder={CONFIRMATION_TEXT}
                  style={{
                    width: '100%', padding: '8px 12px', fontSize: 13,
                    border: `1px solid ${textMatch ? '#86EFAC' : '#D1D5DB'}`,
                    borderRadius: 8, outline: 'none', boxSizing: 'border-box',
                    background: textMatch ? '#F0FDF4' : '#fff',
                    transition: 'border-color 0.15s',
                  }}
                  onFocus={e => { if (!textMatch) e.currentTarget.style.borderColor = '#3B82F6' }}
                  onBlur={e => { if (!textMatch) e.currentTarget.style.borderColor = '#D1D5DB' }}
                />
                {confirmText.length > 0 && !textMatch && (
                  <div style={{ fontSize: 11, color: '#DC2626', marginTop: 4 }}>
                    输入不匹配，请精确输入"{CONFIRMATION_TEXT}"
                  </div>
                )}
              </div>
            )}

            {/* Checkbox */}
            <label data-testid="confirm-checkbox-label" style={{
              display: 'flex', alignItems: 'flex-start', gap: 8,
              fontSize: 13, color: '#374151', cursor: 'pointer', marginBottom: 16,
            }}>
              <input
                data-testid="confirm-checkbox"
                disabled={executed}
                type="checkbox"
                checked={checkboxChecked}
                onChange={e => setCheckboxChecked(e.target.checked)}
                style={{ marginTop: 2 }}
              />
              <span>{ibkr ? '我已确认订单参数及费用，并确认 Gateway 已获本人实盘授权；最终提交才会发送订单' : '我已确认订单参数无误，理解此操作不可撤回'}</span>
            </label>

            {/* Submit error */}
            {submitError && (
              <div data-testid="submit-error" style={{
                background: '#FEF2F2', border: '1px solid #FECACA',
                borderRadius: 8, padding: '10px 14px', marginBottom: 16,
                color: '#991B1B', fontSize: 13,
              }}>
                下单失败：{submitError}
              </div>
            )}
          </div>

          {/* Footer */}
          <div style={{
            flexShrink: 0, padding: '12px 20px', borderTop: '1px solid #E5E7EB',
            display: 'flex', justifyContent: 'flex-end', gap: 12,
          }}>
            <button
              data-testid="cancel-btn"
              onClick={onClose}
              disabled={submitting}
              style={{
                padding: '8px 20px', fontSize: 13, borderRadius: 6,
                border: '1px solid #E5E7EB', background: '#fff', color: '#6B7280',
                cursor: 'pointer',
              }}
            >
              取消
            </button>
            {ibkr && batch?.status === 'READY' && <button data-testid="confirm-ibkr-plan" disabled={dirty || !fresh || submitting || (needsConfirmation && !textMatch)} onClick={confirmIBKRPlan}>确认交易计划</button>}
            <button
              data-testid="submit-btn"
              onClick={handleSubmit}
              disabled={!canSubmit}
              title={
                !checkboxChecked
                  ? '请先勾选确认复选框'
                  : needsConfirmation && !textMatch
                    ? '请先输入风险确认文字'
                    : undefined
              }
              style={{
                padding: '8px 20px', fontSize: 13, borderRadius: 8, fontWeight: 600,
                border: 'none',
                background: canSubmit ? '#1F2937' : '#F3F4F6',
                color: canSubmit ? '#fff' : '#9CA3AF',
                cursor: canSubmit ? 'pointer' : 'not-allowed',
                display: 'flex', alignItems: 'center', gap: 6,
                transition: 'background 0.15s',
              }}
            >
              {submitting && <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} />}
              {submitting ? '提交中...' : '确认提交'}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

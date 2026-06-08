/**
 * ExecutionPlanPanel — 执行计划草案预览面板
 *
 * v3.11 M7 最小版: 只读展示,不接 confirm/下单。
 */
import { useState } from 'react'
import { Loader2, ChevronDown, ChevronUp, AlertTriangle, CheckCircle, Info } from 'lucide-react'

interface Tranche {
  sequence: number
  quantity: number
  trigger_type: string
  trigger_price: number | null
  limit_price: number | null
}

interface PlanResult {
  plan_summary_block: {
    symbol: string
    side: string
    total_quantity: number
    num_tranches: number
    tranches: Tranche[]
    target_position_pct: number
    current_position_pct: number
    current_price: number
  }
  factor_snapshot: Record<string, unknown>
  constraints_applied: Record<string, unknown>
  rationale: string
  risk_notes: string
  warnings: string[]
  violations: string[]
  tick_degraded: boolean
}

interface Props {
  symbol: string
  market: string
  side: string
  onClose: () => void
}

const SIDE_LABEL: Record<string, string> = {
  BUY: '买入', ADD: '加仓', REDUCE: '减仓', SELL: '卖出',
}

const TRIGGER_LABEL: Record<string, string> = {
  IMMEDIATE: '立即', PRICE_BELOW: '低于', PRICE_ABOVE: '高于', MANUAL: '手动',
}

export default function ExecutionPlanPanel({ symbol, market, side, onClose }: Props) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [plan, setPlan] = useState<PlanResult | null>(null)
  const [showFactors, setShowFactors] = useState(false)
  const [showConstraints, setShowConstraints] = useState(false)

  async function handleGenerate() {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch('/api/execution-plan/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol,
          market,
          side,
          target_position_pct: 0.08,
          current_position_pct: 0.0,
          current_price: 0,
          total_assets: 1000000,
        }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail))
      }
      const data = await res.json()
      setPlan(data)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '生成失败')
    } finally {
      setLoading(false)
    }
  }

  if (!plan && !loading && !error) {
    return (
      <div style={{ background: '#F0F9FF', border: '1px solid #BAE6FD', borderRadius: 10,
        padding: '12px 16px', marginTop: 8 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: '#0369A1' }}>
            执行计划
          </span>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#9CA3AF',
            cursor: 'pointer', fontSize: 12 }}>关闭</button>
        </div>
        <p style={{ fontSize: 12, color: '#6B7280', margin: '6px 0 10px' }}>
          基于纪律手册自动生成分批执行计划(价格/数量/批次由规则引擎确定性产出,AI 只写解释)。
        </p>
        <button onClick={handleGenerate} style={{
          padding: '6px 16px', fontSize: 12, fontWeight: 600, borderRadius: 6,
          border: 'none', cursor: 'pointer', color: '#fff', background: '#0284C7',
        }}>
          生成 {SIDE_LABEL[side] || side} 计划
        </button>
      </div>
    )
  }

  if (loading) {
    return (
      <div style={{ background: '#F0F9FF', border: '1px solid #BAE6FD', borderRadius: 10,
        padding: '16px', marginTop: 8, textAlign: 'center' }}>
        <Loader2 size={18} className="animate-spin" style={{ color: '#0284C7' }} />
        <span style={{ fontSize: 12, color: '#6B7280', marginLeft: 8 }}>
          正在生成执行计划(因子→规则引擎→解释)...
        </span>
      </div>
    )
  }

  if (error) {
    return (
      <div style={{ background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: 10,
        padding: '12px 16px', marginTop: 8 }}>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', color: '#DC2626', fontSize: 13 }}>
          <AlertTriangle size={14} /> 执行计划生成失败
        </div>
        <p style={{ fontSize: 12, color: '#7F1D1D', margin: '4px 0 0' }}>{error}</p>
        <button onClick={() => { setError(null) }} style={{
          marginTop: 8, padding: '4px 12px', fontSize: 11, borderRadius: 4,
          border: '1px solid #FECACA', background: '#fff', cursor: 'pointer', color: '#DC2626',
        }}>关闭</button>
      </div>
    )
  }

  if (!plan) return null
  const psb = plan.plan_summary_block
  const fs = plan.factor_snapshot as Record<string, unknown>
  const dsm = (fs.data_source_meta ?? {}) as Record<string, unknown>
  const degraded = (dsm.degraded_fields ?? []) as string[]

  return (
    <div style={{ background: '#F0F9FF', border: '1px solid #BAE6FD', borderRadius: 10,
      padding: '14px 16px', marginTop: 8 }}>
      {/* 标题 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <CheckCircle size={14} style={{ color: '#059669' }} />
          <span style={{ fontSize: 13, fontWeight: 600, color: '#0369A1' }}>
            执行计划草案 — {psb.symbol} {SIDE_LABEL[psb.side] || psb.side}
          </span>
        </div>
        <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#9CA3AF',
          cursor: 'pointer', fontSize: 12 }}>关闭</button>
      </div>

      {/* 违规/警告 */}
      {plan.violations.length > 0 && (
        <div style={{ background: '#FEF3C7', borderRadius: 6, padding: '6px 10px', marginBottom: 8, fontSize: 11, color: '#92400E' }}>
          {plan.violations.map((v, i) => <div key={i}>⚠️ {v}</div>)}
        </div>
      )}

      {/* 批次列表 */}
      <div style={{ marginBottom: 10 }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: '#374151', marginBottom: 4 }}>
          分批计划 ({psb.num_tranches} 批, 共 {psb.total_quantity} 股)
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #E5E7EB' }}>
              <th style={{ padding: '4px 6px', textAlign: 'left', color: '#9CA3AF', fontWeight: 500 }}>批次</th>
              <th style={{ padding: '4px 6px', textAlign: 'right', color: '#9CA3AF', fontWeight: 500 }}>触发</th>
              <th style={{ padding: '4px 6px', textAlign: 'right', color: '#9CA3AF', fontWeight: 500 }}>触发价</th>
              <th style={{ padding: '4px 6px', textAlign: 'right', color: '#9CA3AF', fontWeight: 500 }}>限价</th>
              <th style={{ padding: '4px 6px', textAlign: 'right', color: '#9CA3AF', fontWeight: 500 }}>数量</th>
            </tr>
          </thead>
          <tbody>
            {psb.tranches.map(t => (
              <tr key={t.sequence} style={{ borderBottom: '1px solid #F3F4F6' }}>
                <td style={{ padding: '4px 6px', color: '#374151' }}>第 {t.sequence} 批</td>
                <td style={{ padding: '4px 6px', textAlign: 'right', color: '#6B7280' }}>
                  {TRIGGER_LABEL[t.trigger_type] || t.trigger_type}
                </td>
                <td style={{ padding: '4px 6px', textAlign: 'right', fontWeight: 600, color: '#111827', fontVariantNumeric: 'tabular-nums' }}>
                  {t.trigger_price != null ? `$${t.trigger_price}` : '—'}
                </td>
                <td style={{ padding: '4px 6px', textAlign: 'right', color: '#6B7280', fontVariantNumeric: 'tabular-nums' }}>
                  {t.limit_price != null ? `$${t.limit_price}` : '—'}
                </td>
                <td style={{ padding: '4px 6px', textAlign: 'right', fontWeight: 600, color: '#111827', fontVariantNumeric: 'tabular-nums' }}>
                  {t.quantity}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* rationale */}
      <div style={{ background: '#fff', borderRadius: 6, padding: '8px 10px', marginBottom: 6, border: '1px solid #E5E7EB' }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: '#374151', marginBottom: 2 }}>规则引擎解释</div>
        <div style={{ fontSize: 12, color: '#4B5563', lineHeight: 1.6 }}>{plan.rationale}</div>
      </div>

      {/* risk_notes */}
      {plan.risk_notes && (
        <div style={{ background: '#FEF3C7', borderRadius: 6, padding: '6px 10px', marginBottom: 6, fontSize: 11, color: '#92400E' }}>
          <strong>风险提示:</strong> {plan.risk_notes}
        </div>
      )}

      {/* 数据来源 + degraded */}
      <div style={{ fontSize: 11, color: '#9CA3AF', marginBottom: 4, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <span>K线: {String(dsm.kline_source || 'N/A')} ({String(dsm.kline_points || 0)}根)</span>
        <span>行情: {String(dsm.price_source || 'N/A')}{dsm.is_realtime ? ' (实时)' : ''}</span>
        {plan.tick_degraded && <span style={{ color: '#D97706' }}>tick降级</span>}
      </div>
      {degraded.length > 0 && (
        <div style={{ fontSize: 11, color: '#D97706', display: 'flex', gap: 4, alignItems: 'center' }}>
          <Info size={12} /> 数据降级: {degraded.join(', ')}
          {dsm.degraded_reason && <span> — {String(dsm.degraded_reason)}</span>}
        </div>
      )}

      {/* 折叠: 因子快照 */}
      <button onClick={() => setShowFactors(!showFactors)} style={{
        marginTop: 8, background: 'none', border: 'none', cursor: 'pointer',
        fontSize: 11, color: '#6B7280', display: 'flex', alignItems: 'center', gap: 2,
      }}>
        {showFactors ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        因子快照
      </button>
      {showFactors && (
        <pre style={{ fontSize: 10, color: '#6B7280', background: '#F9FAFB', borderRadius: 4,
          padding: 6, overflow: 'auto', maxHeight: 150, margin: '4px 0 0' }}>
          {JSON.stringify({
            current_price: fs.current_price, atr14: fs.atr14,
            volatility_annual: fs.volatility_annual,
            price_percentile: fs.price_percentile,
            drawdown_from_high: fs.drawdown_from_high,
            trend_signal: fs.trend_signal, rsi14: fs.rsi14,
            ma_position: fs.ma_position,
          }, null, 2)}
        </pre>
      )}

      {/* 折叠: 纪律约束 */}
      <button onClick={() => setShowConstraints(!showConstraints)} style={{
        marginTop: 4, background: 'none', border: 'none', cursor: 'pointer',
        fontSize: 11, color: '#6B7280', display: 'flex', alignItems: 'center', gap: 2,
      }}>
        {showConstraints ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        套用的纪律约束
      </button>
      {showConstraints && (
        <pre style={{ fontSize: 10, color: '#6B7280', background: '#F9FAFB', borderRadius: 4,
          padding: 6, overflow: 'auto', maxHeight: 150, margin: '4px 0 0' }}>
          {JSON.stringify(plan.constraints_applied, null, 2)}
        </pre>
      )}

      <div style={{ marginTop: 10, fontSize: 11, color: '#9CA3AF', textAlign: 'center' }}>
        草案预览(第一阶段) — 确认/下单功能待第二阶段接入
      </div>
    </div>
  )
}

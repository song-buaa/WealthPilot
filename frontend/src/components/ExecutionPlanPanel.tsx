/**
 * ExecutionPlanPanel — 执行计划草案预览面板
 *
 * v3.11 M7+StepA: 只读展示 + 锚点价输入 + 降级诚实拒绝。
 */
import { useState } from 'react'
import { Loader2, ChevronDown, ChevronUp, AlertTriangle, CheckCircle, Info } from 'lucide-react'
import { formatDataSource, formatDegraded, formatFactors, formatConstraints } from '@/lib/plan-display'

interface Tranche {
  sequence: number
  quantity: number
  trigger_type: string
  trigger_price: number | null
  limit_price: number | null
}

interface PlanResult {
  insufficient_data?: boolean
  insufficient_data_reason?: string
  plan_summary_block?: {
    symbol: string
    side: string
    total_quantity: number
    num_tranches: number
    tranches: Tranche[]
    target_position_pct: number
    current_position_pct: number
    current_price: number
  }
  factor_snapshot?: Record<string, unknown>
  constraints_applied?: Record<string, unknown>
  rationale?: string
  risk_notes?: string
  warnings?: string[]
  violations?: string[]
  tick_degraded?: boolean
}

interface Props {
  symbol: string
  market: string
  side: string
  onClose: () => void
  onConfirmPlan?: (planResult: PlanResult & { plan_id: string }) => void
}

const SIDE_LABEL: Record<string, string> = {
  BUY: '买入', ADD: '加仓', REDUCE: '减仓', SELL: '卖出',
}

const TRIGGER_LABEL: Record<string, string> = {
  IMMEDIATE: '立即', PRICE_BELOW: '低于', PRICE_ABOVE: '高于', MANUAL: '手动',
}

export default function ExecutionPlanPanel({ symbol, market, side, onClose, onConfirmPlan }: Props) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [plan, setPlan] = useState<PlanResult | null>(null)
  const [showFactors, setShowFactors] = useState(false)
  const [showConstraints, setShowConstraints] = useState(false)

  // 用户输入
  const [targetPct, setTargetPct] = useState('8')
  const [anchorInput, setAnchorInput] = useState('')

  function parseAnchors(): number[] {
    if (!anchorInput.trim()) return []
    return anchorInput.split(/[,，\s]+/)
      .map(s => parseFloat(s.trim()))
      .filter(n => !isNaN(n) && n > 0)
  }

  async function handleGenerate() {
    setLoading(true)
    setError(null)
    setPlan(null)
    const anchors = parseAnchors()
    try {
      const res = await fetch('/api/execution-plan/persist-draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol,
          market,
          side,
          target_position_pct: parseFloat(targetPct) / 100 || 0.08,
          current_position_pct: 0.0,
          current_price: 0,
          total_assets: 1000000,
          user_anchor_prices: anchors.length > 0 ? anchors : undefined,
        }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail))
      }
      const data: PlanResult = await res.json()
      setPlan(data)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '生成失败')
    } finally {
      setLoading(false)
    }
  }

  // ── 初始态: 输入表单 ──
  if (!plan && !loading && !error) {
    return (
      <div style={{ background: '#EFF6FF', border: '1px solid #93C5FD', borderRadius: 10,
        padding: '12px 16px', marginTop: 8 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: '#1D4ED8' }}>
            执行计划 — {symbol} {SIDE_LABEL[side] || side}
          </span>
          <button onClick={onClose} style={closeBtnStyle}>关闭</button>
        </div>
        <p style={{ fontSize: 12, color: '#6B7280', margin: '6px 0 8px' }}>
          基于纪律手册自动生成分批执行计划。价格/数量/批次由规则引擎确定性产出,AI 只写解释。
        </p>

        <div style={{ display: 'flex', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
          <label style={{ fontSize: 12, color: '#374151' }}>
            目标仓位
            <input type="text" value={targetPct} onChange={e => setTargetPct(e.target.value)}
              style={inputStyle} placeholder="8" /> %
          </label>
        </div>

        <div style={{ marginBottom: 10 }}>
          <label style={{ fontSize: 12, color: '#374151' }}>
            目标价位(可选,用逗号分隔,如 440,420,400)
          </label>
          <input type="text" value={anchorInput} onChange={e => setAnchorInput(e.target.value)}
            style={{ ...inputStyle, width: 200 }} placeholder="如 440,420,400" />
          <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 2 }}>
            填了则按你的价位分批(优先级最高);不填则由因子自动生成
          </div>
        </div>

        <button onClick={handleGenerate} style={primaryBtnStyle}>
          生成 {SIDE_LABEL[side] || side} 计划
        </button>
      </div>
    )
  }

  // ── 加载中 ──
  if (loading) {
    return (
      <div style={{ background: '#EFF6FF', border: '1px solid #93C5FD', borderRadius: 10,
        padding: '16px', marginTop: 8, textAlign: 'center' }}>
        <Loader2 size={18} className="animate-spin" style={{ color: '#1D4ED8' }} />
        <span style={{ fontSize: 12, color: '#6B7280', marginLeft: 8 }}>
          正在生成执行计划(因子→规则引擎→解释)...
        </span>
      </div>
    )
  }

  // ── 错误 ──
  if (error) {
    return (
      <div style={{ background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: 10,
        padding: '12px 16px', marginTop: 8 }}>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', color: '#DC2626', fontSize: 13 }}>
          <AlertTriangle size={14} /> 生成失败
        </div>
        <p style={{ fontSize: 12, color: '#7F1D1D', margin: '4px 0 0' }}>{error}</p>
        <button onClick={() => setError(null)} style={closeBtnStyle2}>关闭</button>
      </div>
    )
  }

  if (!plan) return null

  // ── 数据不足拒绝 ──
  if (plan.insufficient_data) {
    return (
      <div style={{ background: '#FFF7ED', border: '1px solid #FED7AA', borderRadius: 10,
        padding: '12px 16px', marginTop: 8 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', color: '#C2410C', fontSize: 13 }}>
            <Info size={14} /> 无法生成有依据的分批计划
          </div>
          <button onClick={onClose} style={closeBtnStyle}>关闭</button>
        </div>
        <p style={{ fontSize: 12, color: '#9A3412', margin: '6px 0 10px', lineHeight: 1.6 }}>
          {plan.insufficient_data_reason}
        </p>
        <div style={{ fontSize: 12, color: '#374151', marginBottom: 6 }}>
          请在下方填入你的目标价位,系统将按你的价位生成分批计划:
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input type="text" value={anchorInput} onChange={e => setAnchorInput(e.target.value)}
            style={{ ...inputStyle, width: 200 }} placeholder="如 13.5,13.0,12.5" />
          <button onClick={() => { setPlan(null); handleGenerate() }} style={primaryBtnStyle}>
            用锚点价生成
          </button>
        </div>
      </div>
    )
  }

  // ── 正常计划展示 ──
  const psb = plan.plan_summary_block!
  const fs = (plan.factor_snapshot ?? {}) as Record<string, unknown>
  const dsm = (fs.data_source_meta ?? {}) as Record<string, unknown>
  const degraded = (dsm.degraded_fields ?? []) as string[]

  return (
    <div style={{ background: '#EFF6FF', border: '1px solid #93C5FD', borderRadius: 10,
      padding: '14px 16px', marginTop: 8 }}>
      {/* 标题 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <CheckCircle size={14} style={{ color: '#059669' }} />
          <span style={{ fontSize: 13, fontWeight: 600, color: '#1D4ED8' }}>
            执行计划草案 — {psb.symbol} {SIDE_LABEL[psb.side] || psb.side}
          </span>
        </div>
        <button onClick={onClose} style={closeBtnStyle}>关闭</button>
      </div>

      {/* 违规/警告 */}
      {(plan.violations ?? []).length > 0 && (
        <div style={{ background: '#FEF3C7', borderRadius: 6, padding: '6px 10px', marginBottom: 8, fontSize: 11, color: '#92400E' }}>
          {plan.violations!.map((v, i) => <div key={i}>&#9888;&#65039; {v}</div>)}
        </div>
      )}

      {/* 批次列表 */}
      <div style={{ marginBottom: 10 }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: '#374151', marginBottom: 4 }}>
          分批计划 ({psb.num_tranches} 批, 共 {psb.total_quantity} 股, 目标 {(psb.target_position_pct * 100).toFixed(0)}% 仓位)
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #E5E7EB' }}>
              {['批次', '触发', '触发价', '限价', '数量'].map(h => (
                <th key={h} style={{ padding: '4px 6px', textAlign: h === '批次' || h === '触发' ? 'left' : 'right',
                  color: '#9CA3AF', fontWeight: 500, fontSize: 11 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {psb.tranches.map(t => (
              <tr key={t.sequence} style={{ borderBottom: '1px solid #F3F4F6' }}>
                <td style={tdLeft}>第 {t.sequence} 批</td>
                <td style={tdLeft}>{TRIGGER_LABEL[t.trigger_type] || t.trigger_type}</td>
                <td style={tdRight}>{t.trigger_price != null ? fmtPrice(t.trigger_price, market) : '—'}</td>
                <td style={tdRight}>{t.limit_price != null ? fmtPrice(t.limit_price, market) : '—'}</td>
                <td style={{ ...tdRight, fontWeight: 600 }}>{t.quantity}</td>
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

      {/* 数据来源(人话) */}
      <div style={{ fontSize: 11, color: '#6B7280', marginBottom: 4 }}>
        {formatDataSource(dsm)}
      </div>
      {degraded.length > 0 && (
        <div style={{ fontSize: 11, color: '#D97706', display: 'flex', gap: 4, alignItems: 'center' }}>
          <Info size={12} /> {formatDegraded(degraded)}
        </div>
      )}

      {/* 计划依据的关键指标 */}
      <button onClick={() => setShowFactors(!showFactors)} style={expandBtnStyle}>
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

      {/* 本计划遵守的纪律 */}
      <button onClick={() => setShowConstraints(!showConstraints)} style={expandBtnStyle}>
        {showConstraints ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        本计划遵守的纪律
      </button>
      {showConstraints && (
        <div style={{ background: '#F9FAFB', borderRadius: 6, padding: '8px 10px', marginTop: 4 }}>
          {formatConstraints(
            plan.constraints_applied as Record<string, unknown>,
            psb.num_tranches,
            psb.target_position_pct,
          ).map((c, i) => (
            <div key={i} style={{ fontSize: 11, color: c.checked ? '#059669' : '#D97706', marginBottom: 3, display: 'flex', gap: 4 }}>
              <span>{c.checked ? '✓' : '⚠'}</span>
              <span>{c.text}</span>
            </div>
          ))}
        </div>
      )}

      {/* 确认按钮 */}
      {onConfirmPlan && plan.plan_summary_block && (
        <div style={{ marginTop: 10, textAlign: 'center' }}>
          <button onClick={() => onConfirmPlan({ ...plan, plan_id: (plan as Record<string,unknown>).plan_id as string })}
            style={{
              padding: '8px 24px', fontSize: 13, fontWeight: 600, borderRadius: 8,
              border: 'none', background: '#1F2937', color: '#fff', cursor: 'pointer',
              display: 'inline-flex', alignItems: 'center', gap: 6,
            }}>
            确认计划 → 加入投资行动
          </button>
        </div>
      )}
    </div>
  )
}

function fmtPrice(p: number, market: string): string {
  return market === 'HK' ? `HK$${p}` : `$${p}`
}

const closeBtnStyle: React.CSSProperties = {
  background: 'none', border: 'none', color: '#9CA3AF', cursor: 'pointer', fontSize: 12,
}
const closeBtnStyle2: React.CSSProperties = {
  marginTop: 8, padding: '4px 12px', fontSize: 11, borderRadius: 4,
  border: '1px solid #FECACA', background: '#fff', cursor: 'pointer', color: '#DC2626',
}
const primaryBtnStyle: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 6,
  padding: '6px 14px', fontSize: 12, fontWeight: 600, borderRadius: 8,
  border: '1px solid #93C5FD', background: '#EFF6FF', color: '#1D4ED8',
  cursor: 'pointer',
}
const inputStyle: React.CSSProperties = {
  padding: '3px 8px', fontSize: 12, border: '1px solid #D1D5DB', borderRadius: 4,
  width: 50, marginLeft: 4, marginRight: 2,
}
const expandBtnStyle: React.CSSProperties = {
  marginTop: 4, background: 'none', border: 'none', cursor: 'pointer',
  fontSize: 11, color: '#6B7280', display: 'flex', alignItems: 'center', gap: 2,
}
const preStyle: React.CSSProperties = {
  fontSize: 10, color: '#6B7280', background: '#F9FAFB', borderRadius: 4,
  padding: 6, overflow: 'auto', maxHeight: 150, margin: '4px 0 0',
}
const tdLeft: React.CSSProperties = { padding: '4px 6px', color: '#374151' }
const tdRight: React.CSSProperties = { padding: '4px 6px', textAlign: 'right', color: '#111827', fontVariantNumeric: 'tabular-nums' }

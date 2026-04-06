/**
 * Dashboard — 投资账户总览
 * 结构与 app_pages/overview.py 完整一致：
 *   1. PageHeader
 *   2. KPI 三卡（2fr 1fr 1fr）
 *   3. 图表区（大类偏差 + 平台环形图）
 *   4. 资产明细表格
 *   5. 导入/导出 区块
 *   6. 负债明细表格
 *   7. 负债导入/导出
 *   8. AI 综合分析报告
 */
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { PieChart, Pie, Cell, Sector, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import { Upload, Download, AlertTriangle, Loader2, ChevronDown, ChevronUp, Sparkles, ImageIcon } from 'lucide-react'
import {
  portfolioApi, decisionApi,
  streamDecisionChat,
  type PortfolioSummary, type Position, type Alert, type Liability,
} from '@/lib/api'
import { fmtCny, fmtCnySigned, fmtPct, fmtDelta, fmtQty, fmtFx, fmtLeverage } from '@/lib/fmt'
import { allocationApi } from '@/lib/allocation-api'
import EmptyState from '@/components/shared/EmptyState'
import AssetAllocationCard from '@/components/allocation/AssetAllocationCard'
import DataTip from '@/components/shared/DataTip'

// ── 调色板（与原版一致）──────────────────────────────────────
const CHART_PALETTE = [
  '#3B82F6', '#10B981', '#F59E0B', '#8B5CF6',
  '#EF4444', '#06B6D4', '#84CC16', '#F97316',
]

// ── 大类配置偏差：类别定义 ─────────────────────────────────────
const ALLOC_CATS = [
  { key: 'monetary',     label: '货币', color: '#3B82F6', minPct: 0.8,  maxPct: 8.2  },
  { key: 'fixed_income', label: '固收', color: '#10B981', minPct: 20,   maxPct: 60   },
  { key: 'equity',       label: '权益', color: '#F59E0B', minPct: 40,   maxPct: 80   },
  { key: 'alternative',  label: '另类', color: '#8B5CF6', minPct: 0,    maxPct: 10   },
  { key: 'derivative',   label: '衍生', color: '#EF4444', minPct: 0,    maxPct: 10   },
] as const

// ── 杠杆分级 ──────────────────────────────────────────────────
function leverageGrade(mult: number) {
  if (mult < 1.5) return { icon: '🟢', label: '安全（低风险）',   color: '#059669', tip: '当前杠杆水平较低，风险可控' }
  if (mult < 2.0) return { icon: '🟡', label: '可控（适度杠杆）', color: '#D97706', tip: '已使用杠杆，建议控制仓位集中度' }
  if (mult < 2.5) return { icon: '🟠', label: '警戒（偏高）',     color: '#EA580C', tip: '杠杆偏高，需关注市场波动与回撤风险' }
  if (mult < 3.0) return { icon: '🔴', label: '高风险',           color: '#DC2626', tip: '杠杆较高，建议降低仓位或增加安全垫' }
  return               { icon: '🔴', label: '危险（爆仓风险高）', color: '#DC2626', tip: '杠杆过高，存在较大爆仓风险' }
}

// ── 大类资产示例文本 ─────────────────────────────────────────
const ALLOC_EXAMPLES: Record<string, string> = {
  monetary:     '余额宝、货币基金、活期存款等',
  fixed_income: '债券基金、银行理财、信托等',
  equity:       '股票、股票基金、指数ETF等',
  alternative:  '黄金、大宗商品、REITs 等',
  derivative:   '期权、期货等',
}

// DataTip 已抽取为共享组件 @/components/shared/DataTip

// ── concentration 按名称累加 ─────────────────────────────────
function getPct(concentration: Record<string, number>, name: string): number {
  return Object.entries(concentration)
    .filter(([k]) => k.split(':')[1] === name)
    .reduce((sum, [, v]) => sum + v, 0)
}

// ── 主组件 ────────────────────────────────────────────────────
export default function Dashboard() {
  const [summary, setSummary]       = useState<PortfolioSummary | null>(null)
  const [positions, setPositions]   = useState<Position[]>([])
  const [liabilities, setLiabilities] = useState<Liability[]>([])
  const [alerts, setAlerts]         = useState<Alert[]>([])
  const [loading, setLoading]       = useState(true)
  const [error, setError]           = useState<string | null>(null)
  const [cashRange, setCashRange]   = useState<{ min: number; max: number } | undefined>(undefined)

  // 导入/导出展开状态
  const [importOpen, setImportOpen]         = useState(false)
  const [liabImportOpen, setLiabImportOpen] = useState(false)

  const fetchAll = () => {
    setLoading(true)
    setError(null)
    Promise.all([
      portfolioApi.getSummary(),
      portfolioApi.getPositions(),
      portfolioApi.getLiabilities(),
      portfolioApi.getAlerts(),
      allocationApi.getTargets().catch(() => []),
    ])
      .then(([s, p, l, a, targets]) => {
        setSummary(s)
        setPositions(p.items)
        setLiabilities(l.items)
        setAlerts(a.items)
        const ct = targets.find((t: { asset_class: string }) => t.asset_class === 'cash')
        if (ct?.cash_min_amount != null && ct?.cash_max_amount != null) {
          setCashRange({ min: ct.cash_min_amount, max: ct.cash_max_amount })
        }
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : '加载失败'))
      .finally(() => setLoading(false))
  }

  useEffect(fetchAll, [])

  // ── 加载中 ──
  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 300, gap: 8, color: '#9CA3AF' }}>
        <Loader2 size={18} className="animate-spin" />
        <span style={{ fontSize: 13 }}>加载中…</span>
      </div>
    )
  }

  // ── 错误 ──
  if (error) {
    return (
      <div style={{ background: '#FEE2E2', border: '1px solid #FECACA', borderRadius: 10, padding: '12px 16px', color: '#7F1D1D', fontSize: 13, display: 'flex', gap: 8 }}>
        <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: 1 }} /> {error}
      </div>
    )
  }

  // ── 空状态 ──
  const isEmpty = !summary || (summary.total_assets === 0 && positions.length === 0)
  if (isEmpty) {
    return (
      <div>
        <PageHeader posCount={0} />
        <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12, boxShadow: 'var(--shadow-sm)' }}>
          <EmptyState icon={Upload} title="暂无持仓数据" desc="请通过导入 CSV 添加持仓，导入后数据将自动刷新" />
        </div>
        {/* 仍然显示导入面板 */}
        <ImportSection
          open={importOpen} onToggle={() => setImportOpen(v => !v)}
          onRefresh={fetchAll}
        />
      </div>
    )
  }

  const bs = summary!
  const pnl = bs.total_profit_loss
  const cost = bs.total_assets - pnl
  const returnRate = cost > 0 ? (pnl / cost) * 100 : 0
  const netWorth = bs.net_worth
  const levMult = bs.total_assets / Math.max(netWorth, 1)
  const levGrade = leverageGrade(levMult)

  // 平台分布
  const platEntries = Object.entries(bs.platform_distribution)
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1])

  // 持仓排序
  const platTotals: Record<string, number> = {}
  positions.forEach(p => { platTotals[p.platform] = (platTotals[p.platform] ?? 0) + p.market_value_cny })
  const sortedPos = [...positions].sort(
    (a, b) => (platTotals[b.platform] ?? 0) - (platTotals[a.platform] ?? 0) || b.market_value_cny - a.market_value_cny
  )

  // 负债汇总
  const liabTotal = liabilities.reduce((s, l) => s + (l.amount ?? 0), 0)

  return (
    <div>
      <DataTip />
      {/* ── 页面标题 ── */}
      <PageHeader posCount={positions.length} />


      {/* ── KPI 三卡 ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr', gap: 12, marginBottom: 16 }}>
        {/* Primary：总资产 */}
        <div style={{
          background: 'linear-gradient(135deg, #1B2A4A 0%, #0F1E35 100%)',
          borderRadius: 12, padding: '20px 24px',
          boxShadow: 'var(--shadow-dark)',
          display: 'flex', flexDirection: 'column', justifyContent: 'space-between', minHeight: 100,
        }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'rgba(255,255,255,0.48)', textTransform: 'uppercase', letterSpacing: '0.6px' }}>
            总资产（投资）
          </div>
          <div style={{ fontSize: 28, fontWeight: 700, color: '#fff', letterSpacing: '-1px', fontVariantNumeric: 'tabular-nums', margin: '4px 0 8px', lineHeight: 1.1 }}>
            {fmtCny(bs.total_assets)}
          </div>
          <div style={{ display: 'flex', gap: 20 }}>
            <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.55)' }}>
              净资产 <strong style={{ color: 'rgba(255,255,255,0.9)', fontWeight: 600 }}>{fmtCny(netWorth)}</strong>
            </div>
            <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.55)' }}>
              负债 <strong style={{ color: 'rgba(255,255,255,0.9)', fontWeight: 600 }}>{fmtCny(bs.total_liabilities)}</strong>
            </div>
          </div>
        </div>

        {/* Secondary：浮动盈亏 */}
        <div style={{
          background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12,
          padding: '16px 18px', boxShadow: 'var(--shadow-sm)',
          display: 'flex', flexDirection: 'column', justifyContent: 'space-between', minHeight: 100,
        }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: '#9CA3AF', textTransform: 'uppercase', letterSpacing: '0.5px' }}>浮动盈亏</div>
          <div style={{ fontSize: 20, fontWeight: 700, letterSpacing: '-0.5px', fontVariantNumeric: 'tabular-nums', marginTop: 4, color: pnl >= 0 ? '#DC2626' : '#16A34A' }}>
            {fmtCnySigned(pnl)}
          </div>
          {cost > 0 && (
            <div style={{ fontSize: 11, fontWeight: 600, marginTop: 4, display: 'flex', alignItems: 'center', gap: 3, color: returnRate >= 0 ? '#DC2626' : '#16A34A' }}>
              {returnRate >= 0 ? '↑' : '↓'} 收益率 {fmtDelta(returnRate)}
            </div>
          )}
        </div>

        {/* Tertiary：杠杆倍数 */}
        <div data-tip={levGrade.tip} style={{
          background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12,
          padding: '16px 18px', boxShadow: 'var(--shadow-sm)',
          display: 'flex', flexDirection: 'column', justifyContent: 'space-between', minHeight: 100,
          cursor: 'default',
        }}>
          <div style={{ fontSize: 11, fontWeight: 500, color: '#9CA3AF', textTransform: 'uppercase', letterSpacing: '0.5px' }}>杠杆倍数</div>
          <div style={{ fontSize: 18, fontWeight: 600, letterSpacing: '-0.3px', fontVariantNumeric: 'tabular-nums', marginTop: 4, color: levMult > 1.05 ? '#DC2626' : '#374151' }}>
            {fmtLeverage(levMult)}
          </div>
          <div style={{ fontSize: 11, fontWeight: 600, color: levGrade.color, marginTop: 4 }}>
            {levGrade.icon} {levGrade.label}
          </div>
        </div>
      </div>

      {/* ── 图表区 3fr 2fr ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '3fr 2fr', gap: 16, marginBottom: 16 }}>
        <AssetAllocationCard allocation={bs.allocation} cashRange={cashRange} />

        <PlatformCard platEntries={platEntries} />
      </div>

      {/* ── 资产明细表格 ── */}
      <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12, padding: '20px 20px 16px', marginBottom: 16, boxShadow: 'var(--shadow-sm)' }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#374151', display: 'flex', alignItems: 'center', gap: 6, marginBottom: 16 }}>
          📋 资产明细
          <span style={{ marginLeft: 'auto', fontSize: 11, fontWeight: 400, color: '#9CA3AF' }}>{positions.length} 只持仓</span>
        </div>
        {sortedPos.length === 0 ? (
          <EmptyState icon={Upload} title="暂无持仓" desc="请先导入 CSV" />
        ) : (
          <div style={{ maxHeight: 494, overflowY: 'auto', borderRadius: 6 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, tableLayout: 'fixed' }}>
              <colgroup>
                <col style={{ width: 72 }} /><col style={{ width: 120 }} /><col style={{ width: 64 }} />
                <col style={{ width: 60 }} /><col style={{ width: 56 }} /><col style={{ width: 84 }} />
                <col style={{ width: 84 }} /><col style={{ width: 92 }} /><col style={{ width: 52 }} />
                <col style={{ width: 92 }} /><col style={{ width: 56 }} />
              </colgroup>
              <thead>
                <tr>
                  {['平台','资产名称','资产代码','资产大类','头寸','市值(美元)','市值(港币)','市值(人民币)','占比%','盈亏(人民币)','盈亏%'].map((h, i) => (
                    <th key={h} style={{ padding: '8px 10px', fontSize: 11, fontWeight: 600, color: '#9CA3AF', textTransform: 'uppercase', letterSpacing: '0.4px', borderBottom: '1px solid #E5E7EB', whiteSpace: 'nowrap', background: '#fff', position: 'sticky', top: 0, zIndex: 1, textAlign: i >= 4 ? 'right' : 'left' }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sortedPos.map(p => {
                  const pnlV = p.profit_loss_value ?? 0
                  const costV = (p.market_value_cny ?? 0) - pnlV
                  const rate = costV > 0 ? (pnlV / costV) * 100 : 0
                  const pct = getPct(bs.concentration, p.name)
                  const isUSD = p.original_currency === 'USD'
                  const isHKD = p.original_currency === 'HKD'
                  return (
                    <tr key={p.id}
                      onMouseEnter={e => (e.currentTarget.style.background = '#F9FAFB')}
                      onMouseLeave={e => (e.currentTarget.style.background = '')}>
                      <td style={td}><span style={tag('#DBEAFE','#1E40AF')}>{p.platform}</span></td>
                      <td style={{ ...td, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={p.name}>{p.name}</td>
                      <td style={{ ...td, color: '#6B7280', fontSize: 12 }}>{p.ticker || '—'}</td>
                      <td style={{ ...td, textAlign: 'center' }}><span style={tag('#F3F4F6','#374151')}>{p.asset_class}</span></td>
                      <td style={{ ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtQty(p.quantity)}</td>
                      <td style={{ ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{isUSD ? fmtFx(p.original_value,'USD') : '—'}</td>
                      <td style={{ ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{isHKD ? fmtFx(p.original_value,'HKD') : '—'}</td>
                      <td style={{ ...td, textAlign: 'right', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{fmtCny(p.market_value_cny)}</td>
                      <td style={{ ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: '#6B7280', fontSize: 12 }}>{pct.toFixed(2)}%</td>
                      <td style={{ ...td, textAlign: 'right', fontWeight: 600, fontVariantNumeric: 'tabular-nums', color: pnlV > 0 ? '#DC2626' : pnlV < 0 ? '#16A34A' : '#9CA3AF' }}>
                        {pnlV === 0 ? '¥0' : pnlV > 0 ? `+${fmtCny(pnlV)}` : `-${fmtCny(-pnlV)}`}
                      </td>
                      <td style={{ ...td, textAlign: 'right', fontWeight: 600, fontVariantNumeric: 'tabular-nums', color: rate > 0 ? '#DC2626' : rate < 0 ? '#16A34A' : '#9CA3AF' }}>
                        {rate === 0 ? '0.00%' : fmtDelta(rate)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── 资产导入/导出 ── */}
      <ImportSection open={importOpen} onToggle={() => setImportOpen(v => !v)} onRefresh={fetchAll} />

      {/* ── 负债明细表格 ── */}
      <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12, padding: '20px 20px 16px', marginBottom: 16, boxShadow: 'var(--shadow-sm)' }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#374151', display: 'flex', alignItems: 'center', gap: 6, marginBottom: 16 }}>
          🏦 负债明细
          <span style={{ marginLeft: 'auto', fontSize: 11, fontWeight: 400, color: '#9CA3AF' }}>
            {liabTotal > 0 ? fmtCny(liabTotal) : '无负债'}
          </span>
        </div>
        {liabilities.length === 0 ? (
          <div style={{ textAlign: 'center', color: '#9CA3AF', fontSize: 13, padding: '20px 0' }}>暂无负债数据</div>
        ) : (
          <div style={{ maxHeight: 300, overflowY: 'auto', borderRadius: 6 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, tableLayout: 'fixed' }}>
              <colgroup>
                <col /><col style={{ width: 80 }} /><col style={{ width: 80 }} />
                <col style={{ width: 120 }} /><col style={{ width: 60 }} /><col style={{ width: 80 }} />
              </colgroup>
              <thead>
                <tr>
                  {['负债名称','类型','用途','金额(人民币)','占比','年利率'].map((h, i) => (
                    <th key={h} style={{ padding: '8px 10px', fontSize: 11, fontWeight: 600, color: '#9CA3AF', textTransform: 'uppercase', letterSpacing: '0.4px', borderBottom: '1px solid #E5E7EB', background: '#fff', position: 'sticky', top: 0, zIndex: 1, textAlign: i >= 3 ? 'right' : 'left' }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {liabilities.map((l, i) => (
                  <tr key={i}
                    onMouseEnter={e => (e.currentTarget.style.background = '#F9FAFB')}
                    onMouseLeave={e => (e.currentTarget.style.background = '')}>
                    <td style={{ ...td, fontWeight: 500, color: '#1B2A4A' }}>{l.name}</td>
                    <td style={td}>{l.category || '—'}</td>
                    <td style={td}>{l.purpose || '—'}</td>
                    <td style={{ ...td, textAlign: 'right', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{fmtCny(l.amount)}</td>
                    <td style={{ ...td, textAlign: 'right', color: '#6B7280', fontSize: 12 }}>
                      {liabTotal > 0 ? `${((l.amount / liabTotal) * 100).toFixed(1)}%` : '—'}
                    </td>
                    <td style={{ ...td, textAlign: 'right', color: '#6B7280' }}>
                      {l.interest_rate != null ? `${l.interest_rate.toFixed(2)}%` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── 负债导入/导出 ── */}
      <LiabImportSection open={liabImportOpen} onToggle={() => setLiabImportOpen(v => !v)} onRefresh={fetchAll} />

      {/* ── 投资预警 ── */}
      {alerts.length > 0 && <AlertsSection alerts={alerts} />}

      {/* ── AI 综合分析报告 ── */}
      <AIReportSection sessionId={crypto.randomUUID()} />
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// 子组件
// ══════════════════════════════════════════════════════════════

function PageHeader({ posCount }: { posCount: number }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
      <div style={{ width: 38, height: 38, borderRadius: 10, background: 'linear-gradient(135deg, #1B2A4A, #2D4A7A)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 17 }}>📊</div>
      <div>
        <div style={{ fontSize: 20, fontWeight: 700, color: '#1B2A4A', letterSpacing: '-0.3px' }}>投资账户总览</div>
        <div style={{ fontSize: 12, color: '#9CA3AF', marginTop: 1 }}>
          账户总览 · 持仓分析
        </div>
      </div>
    </div>
  )
}

function AlertsSection({ alerts }: { alerts: Alert[] }) {
  function alertColors(severity: string) {
    if (severity === 'danger')  return { bg: '#FFF5F5', border: '#FECACA', title: '#991B1B', body: '#B91C1C', icon: '🔴' }
    if (severity === 'warning') return { bg: '#FFFBEB', border: '#FDE68A', title: '#92400E', body: '#B45309', icon: '⚠️' }
    return                             { bg: '#EFF6FF', border: '#BFDBFE', title: '#1E40AF', body: '#1D4ED8', icon: 'ℹ️' }
  }
  return (
    <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12, padding: '16px 20px', marginBottom: 16, boxShadow: 'var(--shadow-sm)' }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
        <AlertTriangle size={14} color="#DC2626" /> 投资预警
        <span style={{ marginLeft: 'auto', fontSize: 11, fontWeight: 400, color: '#9CA3AF' }}>{alerts.length} 条</span>
      </div>
      {alerts.map((a, i) => {
        const c = alertColors(a.severity)
        return (
          <div key={i} style={{ display: 'flex', gap: 10, padding: '10px 12px', background: c.bg, border: `1px solid ${c.border}`, borderRadius: 8, marginBottom: i < alerts.length - 1 ? 8 : 0 }}>
            <span style={{ fontSize: 15, flexShrink: 0, marginTop: 1 }}>{c.icon}</span>
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, color: c.title }}>{a.title}</div>
              <div style={{ fontSize: 12, color: c.body, marginTop: 2, lineHeight: 1.5 }}>{a.description}</div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

/** 平台分布环形图（hover 放大扇区，复刻 ECharts emphasis 效果）*/
function PlatformCard({ platEntries }: { platEntries: [string, number][] }) {
  const [activeIndex, setActiveIndex] = useState<number | undefined>(undefined)
  const platTotal = platEntries.reduce((s, [, v]) => s + v, 0)
  const pieData   = platEntries.map(([name, value]) => ({ name, value }))

  // hover 扇区：外径 +8px，加轻阴影，模拟 ECharts emphasis
  const renderActiveShape = (props: Record<string, unknown>) => {
    const { cx, cy, innerRadius, outerRadius, startAngle, endAngle, fill } = props as {
      cx: number; cy: number; innerRadius: number; outerRadius: number
      startAngle: number; endAngle: number; fill: string
    }
    return (
      <Sector
        cx={cx} cy={cy}
        innerRadius={innerRadius}
        outerRadius={(outerRadius as number) + 8}
        startAngle={startAngle}
        endAngle={endAngle}
        fill={fill}
        style={{ filter: 'drop-shadow(0 2px 6px rgba(0,0,0,0.08))' }}
      />
    )
  }

  return (
    <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12, padding: '16px 14px 8px', boxShadow: 'var(--shadow-sm)' }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 10 }}>🏦 平台分布</div>
      {platEntries.length === 0 ? (
        <div style={{ textAlign: 'center', color: '#9CA3AF', fontSize: 12, padding: '40px 0' }}>暂无数据</div>
      ) : (
        <ResponsiveContainer width="100%" height={230}>
          <PieChart>
            <Pie
              data={pieData} cx="40%" cy="50%"
              innerRadius={52} outerRadius={82} paddingAngle={2}
              dataKey="value"
              startAngle={90} endAngle={-270}
              activeIndex={activeIndex}
              activeShape={renderActiveShape}
              onMouseEnter={(_, index) => setActiveIndex(index)}
              onMouseLeave={() => setActiveIndex(undefined)}
            >
              {pieData.map((_, i) => (
                <Cell key={i} fill={CHART_PALETTE[i % CHART_PALETTE.length]} />
              ))}
            </Pie>
            <Tooltip
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null
                const { name, value } = payload[0] as { name: string; value: number }
                const pct = platTotal > 0 ? (value / platTotal * 100).toFixed(2) : '0.00'
                return (
                  <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 8, padding: '8px 12px', fontSize: 12, color: '#1F2937', boxShadow: '0 3px 10px rgba(0,0,0,0.1)', lineHeight: 1.8 }}>
                    <div style={{ fontWeight: 600, marginBottom: 2 }}>{name}</div>
                    <div>市值: <b>{fmtCny(value)}</b></div>
                    <div>占比: <b>{pct}%</b></div>
                  </div>
                )
              }}
            />
            <Legend
              layout="vertical" align="right" verticalAlign="middle"
              iconType="circle" iconSize={8}
              formatter={(name: string, entry) => {
                const val = (entry.payload as { value?: number }).value ?? 0
                return (
                  <span style={{ fontSize: 11, color: '#6B7280' }}>
                    {name}{'  '}
                    <span style={{ color: '#9CA3AF' }}>
                      {((val / platTotal) * 100).toFixed(1)}%
                    </span>
                  </span>
                )
              }}
            />
          </PieChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}

/** 大类资产配置偏差视图（复刻原版4列 grid 结构）*/
// AllocationCard 已抽取为共享组件 AssetAllocationCard

// ── 截图识别平台提示 ────────────────────────────────────────────
const SS_PLATFORMS = ['招商银行', '支付宝', '建设银行', '国金证券', '雪盈证券'] as const
type SSPlatform = typeof SS_PLATFORMS[number]
const SS_HINTS: Record<SSPlatform, string> = {
  '招商银行': '将识别：活钱管理、稳健投资、进取投资',
  '支付宝':   '将识别：活钱管理、稳健投资、进取投资',
  '建设银行': '将识别：活钱、理财产品、债券、基金',
  '国金证券': '将识别：所有港股持仓（名称、头寸、市值人民币、盈亏人民币、盈亏%）',
  '雪盈证券': '将识别：所有美股持仓（名称、代码、头寸、市值美元、盈亏美元、盈亏%）',
}

/** 资产导入/导出 折叠面板（通用 CSV + broker CSV + 截图识别）*/
function ImportSection({ open, onToggle, onRefresh }: { open: boolean; onToggle: () => void; onRefresh: () => void }) {
  const [activeTab, setActiveTab] = useState<'csv' | 'broker' | 'screenshot'>('csv')

  // 通用 CSV tab
  const [csvLoading, setCsvLoading] = useState(false)
  const [csvMsg,     setCsvMsg]     = useState<string | null>(null)
  const csvRef = useRef<HTMLInputElement>(null)

  // Broker CSV tab
  const [broker,       setBroker]       = useState<'老虎证券' | '富途证券'>('老虎证券')
  const [brokerLoading, setBrokerLoading] = useState(false)
  const [brokerMsg,    setBrokerMsg]    = useState<string | null>(null)
  const brokerRef = useRef<HTMLInputElement>(null)

  // 截图 tab
  const [ssPlatform, setSsPlatform] = useState<SSPlatform>('招商银行')
  const [ssLoading,  setSsLoading]  = useState(false)
  const [ssMsg,      setSsMsg]      = useState<string | null>(null)
  const [ssPreview,  setSsPreview]  = useState<string | null>(null)
  const ssRef = useRef<HTMLInputElement>(null)

  async function handleCsv(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]; if (!file) return
    setCsvLoading(true); setCsvMsg(null)
    try {
      const r = await portfolioApi.importCsv(file)
      setCsvMsg(`✅ 导入成功：${r.imported} 条${r.errors.length ? `，${r.errors.length} 个错误` : ''}`)
      onRefresh()
    } catch (err) {
      setCsvMsg(`❌ ${err instanceof Error ? err.message : '导入失败'}`)
    } finally {
      setCsvLoading(false)
      if (csvRef.current) csvRef.current.value = ''
    }
  }

  async function handleBrokerCsv(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]; if (!file) return
    setBrokerLoading(true); setBrokerMsg(null)
    try {
      const r = await portfolioApi.importBrokerCsv(file, broker)
      setBrokerMsg(`✅ ${broker}导入成功：${r.imported} 条，汇率 USD/CNY = ${r.rate.toFixed(4)}`)
      onRefresh()
    } catch (err) {
      setBrokerMsg(`❌ ${err instanceof Error ? err.message : '导入失败'}`)
    } finally {
      setBrokerLoading(false)
      if (brokerRef.current) brokerRef.current.value = ''
    }
  }

  async function handleScreenshot(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]; if (!file) return
    const reader = new FileReader()
    reader.onload = ev => setSsPreview(ev.target?.result as string)
    reader.readAsDataURL(file)
    setSsLoading(true); setSsMsg(null)
    try {
      const r = await portfolioApi.importScreenshot(file, ssPlatform)
      setSsMsg(`✅ 识别导入成功：${r.imported} 条`)
      onRefresh()
    } catch (err) {
      setSsMsg(`❌ ${err instanceof Error ? err.message : '识别失败'}`)
    } finally {
      setSsLoading(false)
      if (ssRef.current) ssRef.current.value = ''
    }
  }

  const tabStyle = (active: boolean): React.CSSProperties => ({
    padding: '6px 14px', fontSize: 12, fontWeight: active ? 600 : 400,
    color: active ? '#1E40AF' : '#6B7280',
    background: active ? '#EFF6FF' : 'transparent',
    border: '1px solid', borderColor: active ? '#BFDBFE' : '#E5E7EB',
    borderRadius: 6, cursor: 'pointer', whiteSpace: 'nowrap',
  })

  const radioLabel = (val: string, cur: string): React.CSSProperties => ({
    display: 'flex', alignItems: 'center', gap: 5, cursor: 'pointer',
    fontSize: 12, fontWeight: cur === val ? 600 : 400,
    color: cur === val ? '#1E40AF' : '#374151',
    padding: '4px 10px', borderRadius: 6,
    background: cur === val ? '#EFF6FF' : '#F9FAFB',
    border: `1px solid ${cur === val ? '#BFDBFE' : '#E5E7EB'}`,
  })

  return (
    <div style={{ marginBottom: 16 }}>
      <button onClick={onToggle} style={{
        width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        background: '#fff', border: '1px solid #E5E7EB', borderRadius: open ? '12px 12px 0 0' : 12,
        padding: '12px 20px', fontSize: 13, fontWeight: 500, color: '#374151', cursor: 'pointer',
        boxShadow: 'var(--shadow-sm)',
      }}>
        <span>📥  导入 / 导出数据（持仓）</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {!open && (
            <a href="/api/portfolio/export/positions.csv" download onClick={e => e.stopPropagation()}
               style={{ ...btnSecondary, fontSize: 11, padding: '3px 10px' }}>
              <Download size={11} /> 导出 CSV
            </a>
          )}
          {open ? <ChevronUp size={16} color="#9CA3AF" /> : <ChevronDown size={16} color="#9CA3AF" />}
        </div>
      </button>

      {open && (
        <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderTop: 'none', borderRadius: '0 0 12px 12px', padding: '20px', boxShadow: 'var(--shadow-sm)' }}>
          {/* Tab 行 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 18, flexWrap: 'wrap' }}>
            <button style={tabStyle(activeTab === 'csv')}    onClick={() => setActiveTab('csv')}>通用 CSV（全量覆盖）</button>
            <button style={tabStyle(activeTab === 'broker')} onClick={() => setActiveTab('broker')}>CSV 导入（按平台替换）</button>
            <button style={tabStyle(activeTab === 'screenshot')} onClick={() => setActiveTab('screenshot')}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><ImageIcon size={11} /> 截图识别（按平台替换）</span>
            </button>
          </div>

          {/* ── 通用 CSV ── */}
          {activeTab === 'csv' && (
            <div>
              <p style={{ fontSize: 12, color: '#6B7280', marginTop: 0, marginBottom: 12, lineHeight: 1.6 }}>
                上传后将<strong>全量覆盖</strong>全部投资持仓数据，养老/公积金数据不受影响。
              </p>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <button onClick={() => csvRef.current?.click()} disabled={csvLoading}
                  style={{ ...btnPrimary, display: 'flex', alignItems: 'center', gap: 6 }}>
                  {csvLoading ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
                  选择文件导入
                </button>
                <a href="/api/portfolio/export/positions.csv" download style={{ ...btnSecondary, marginLeft: 'auto', marginRight: 8 }}>
                  <Download size={13} /> 导出持仓 CSV
                </a>
              </div>
              <input ref={csvRef} type="file" accept=".csv" style={{ display: 'none' }} onChange={handleCsv} />
              {csvMsg && <MsgBanner msg={csvMsg} style={{ marginTop: 12 }} />}
            </div>
          )}

          {/* ── Broker CSV ── */}
          {activeTab === 'broker' && (
            <div>
              <p style={{ fontSize: 12, color: '#6B7280', marginTop: 0, marginBottom: 12, lineHeight: 1.6 }}>
                直接导入老虎证券对账单或富途持仓 CSV，<strong>只替换该平台数据</strong>，其他平台不受影响。
              </p>
              <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
                {(['老虎证券', '富途证券'] as const).map(b => (
                  <label key={b} style={radioLabel(b, broker)}>
                    <input type="radio" name="broker" value={b} checked={broker === b}
                      onChange={() => { setBroker(b); setBrokerMsg(null) }} style={{ display: 'none' }} />
                    {b}
                  </label>
                ))}
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button onClick={() => brokerRef.current?.click()} disabled={brokerLoading}
                  style={{ ...btnPrimary, display: 'flex', alignItems: 'center', gap: 6 }}>
                  {brokerLoading ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
                  上传 {broker} CSV
                </button>
              </div>
              <input ref={brokerRef} type="file" accept=".csv" style={{ display: 'none' }} onChange={handleBrokerCsv} />
              {brokerMsg && <MsgBanner msg={brokerMsg} style={{ marginTop: 12 }} />}
            </div>
          )}

          {/* ── 截图识别 ── */}
          {activeTab === 'screenshot' && (
            <div>
              <p style={{ fontSize: 12, color: '#6B7280', marginTop: 0, marginBottom: 12, lineHeight: 1.6 }}>
                上传 APP 截图，AI 自动识别持仓数据，<strong>只替换所选平台数据</strong>，其他平台不受影响。
              </p>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
                {SS_PLATFORMS.map(p => (
                  <label key={p} style={radioLabel(p, ssPlatform)}>
                    <input type="radio" name="ss_platform" value={p} checked={ssPlatform === p}
                      onChange={() => { setSsPlatform(p); setSsMsg(null); setSsPreview(null) }}
                      style={{ display: 'none' }} />
                    {p}
                  </label>
                ))}
              </div>
              <div style={{ fontSize: 11, color: '#6B7280', marginBottom: 12, padding: '6px 10px', background: '#F9FAFB', borderRadius: 6, border: '1px solid #F3F4F6' }}>
                ℹ️ {SS_HINTS[ssPlatform]}
              </div>
              <button onClick={() => { setSsMsg(null); setSsPreview(null); ssRef.current?.click() }}
                disabled={ssLoading} style={{ ...btnPrimary, display: 'flex', alignItems: 'center', gap: 6 }}>
                {ssLoading ? <><Loader2 size={13} className="animate-spin" /> AI 识别中…</> : <><ImageIcon size={13} /> 上传截图识别</>}
              </button>
              <input ref={ssRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={handleScreenshot} />
              {ssPreview && (
                <img src={ssPreview} alt="截图预览"
                  style={{ marginTop: 12, maxWidth: 240, maxHeight: 160, borderRadius: 6, border: '1px solid #E5E7EB', objectFit: 'contain' }} />
              )}
              {ssMsg && <MsgBanner msg={ssMsg} style={{ marginTop: 12 }} />}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** 负债导入/导出 折叠面板 */
function LiabImportSection({ open, onToggle, onRefresh }: { open: boolean; onToggle: () => void; onRefresh: () => void }) {
  const [loading, setLoading] = useState(false)
  const [msg,     setMsg]     = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  async function handleImport(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]; if (!file) return
    setLoading(true); setMsg(null)
    try {
      const r = await portfolioApi.importLiabilitiesCsv(file)
      setMsg(`✅ 导入成功：${r.imported} 条`)
      onRefresh()
    } catch (err) {
      setMsg(`❌ ${err instanceof Error ? err.message : '导入失败'}`)
    } finally {
      setLoading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <div style={{ marginBottom: 16 }}>
      <button onClick={onToggle} style={{
        width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        background: '#fff', border: '1px solid #E5E7EB', borderRadius: open ? '12px 12px 0 0' : 12,
        padding: '12px 20px', fontSize: 13, fontWeight: 500, color: '#374151', cursor: 'pointer',
        boxShadow: 'var(--shadow-sm)',
      }}>
        <span>📥  导入 / 导出数据（负债）</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {!open && (
            <a href="/api/portfolio/export/liabilities.csv" download onClick={e => e.stopPropagation()}
               style={{ ...btnSecondary, fontSize: 11, padding: '3px 10px' }}>
              <Download size={11} /> 导出 CSV
            </a>
          )}
          {open ? <ChevronUp size={16} color="#9CA3AF" /> : <ChevronDown size={16} color="#9CA3AF" />}
        </div>
      </button>

      {open && (
        <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderTop: 'none', borderRadius: '0 0 12px 12px', padding: '20px', boxShadow: 'var(--shadow-sm)' }}>
          <p style={{ fontSize: 12, color: '#6B7280', marginTop: 0, marginBottom: 12, lineHeight: 1.6 }}>
            上传后将<strong>全量覆盖</strong>全部负债数据。
          </p>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button onClick={() => fileRef.current?.click()} disabled={loading}
              style={{ ...btnPrimary, display: 'flex', alignItems: 'center', gap: 6 }}>
              {loading ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
              选择文件导入
            </button>
            <a href="/api/portfolio/export/liabilities.csv" download style={{ ...btnSecondary, marginLeft: 'auto', marginRight: 8 }}>
              <Download size={13} /> 导出负债 CSV
            </a>
          </div>
          <input ref={fileRef} type="file" accept=".csv" style={{ display: 'none' }} onChange={handleImport} />
          {msg && <MsgBanner msg={msg} style={{ marginTop: 12 }} />}
        </div>
      )}
    </div>
  )
}

/** 通用消息条 */
function MsgBanner({ msg, style }: { msg: string; style?: React.CSSProperties }) {
  const ok = msg.startsWith('✅')
  return (
    <div style={{
      padding: '8px 12px', borderRadius: 8, fontSize: 12, fontWeight: 500,
      background: ok ? '#F0FDF4' : '#FEF2F2',
      color:      ok ? '#166534' : '#991B1B',
      border:     `1px solid ${ok ? '#BBF7D0' : '#FECACA'}`,
      ...style,
    }}>
      {msg}
    </div>
  )
}

/** AI 综合分析报告区块 */
function AIReportSection({ sessionId }: { sessionId: string }) {
  const [report, setReport]     = useState<string | null>(null)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  async function generate() {
    if (loading) return
    setLoading(true); setReport(null); setError(null)
    abortRef.current = new AbortController()

    const QUERY = '请综合分析我当前的投资组合，从持仓集中度、资产配置、风险敞口三个维度给出评估，并给出具体的调仓建议。'
    let text = ''

    try {
      for await (const evt of streamDecisionChat(QUERY, sessionId, abortRef.current.signal)) {
        if (evt.type === 'text') {
          text += (evt.data.delta as string) ?? ''
          setReport(text)
        } else if (evt.type === 'error') {
          setError((evt.data.message as string) ?? 'AI 分析失败，请检查 API Key 配置')
          break
        } else if (evt.type === 'done') {
          break
        }
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        setError(e instanceof Error ? e.message : 'AI 分析失败')
      }
    } finally {
      setLoading(false)
      // 清理 session
      decisionApi.clearSession(sessionId).catch(() => {})
    }
  }

  return (
    <div style={{ background: 'linear-gradient(135deg, #EFF6FF 0%, #F0FDF4 100%)', border: '1px solid #BFDBFE', borderRadius: 12, padding: '18px 24px 16px', marginBottom: 16 }}>
      {/* 头部 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <Sparkles size={16} color="#3B82F6" />
        <span style={{ fontSize: 14, fontWeight: 700, color: '#1B2A4A' }}>AI 综合分析报告</span>
        <span style={{ background: '#FEE2E2', color: '#DC2626', borderRadius: 5, padding: '2px 8px', fontSize: 11, fontWeight: 600 }}>Beta</span>
      </div>
      <div style={{ fontSize: 12, color: '#6B7280', lineHeight: 1.6, marginBottom: 14 }}>
        报告将融合：账户总览 · 投资纪律检查 · 偏离度分析 · 风险告警，生成个性化投资建议。
      </div>

      {/* 生成按钮 */}
      <button
        onClick={generate}
        disabled={loading}
        style={{ ...btnPrimary, display: 'inline-flex', alignItems: 'center', gap: 6 }}
      >
        {loading ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
        {loading ? 'AI 分析中…' : '✨ 生成报告'}
      </button>

      {/* 错误 */}
      {error && (
        <div style={{ marginTop: 12, padding: '10px 14px', background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: 8, fontSize: 12, color: '#991B1B' }}>
          ❌ {error}
        </div>
      )}

      {/* 报告内容（流式追加） */}
      {report && (
        <div style={{ marginTop: 12, background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '16px 20px', fontSize: 13, lineHeight: 1.8, color: '#374151', whiteSpace: 'pre-wrap' }}>
          {report}
          {loading && <span style={{ display: 'inline-block', width: 6, height: 14, background: '#3B82F6', borderRadius: 1, marginLeft: 2, verticalAlign: 'text-bottom', animation: 'pulse 1s infinite' }} />}
        </div>
      )}
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// 样式常量
// ══════════════════════════════════════════════════════════════

const td: React.CSSProperties = {
  padding: '9px 10px',
  borderBottom: '1px solid #F3F4F6',
  verticalAlign: 'middle',
  whiteSpace: 'nowrap',
  color: '#374151',
}

function tag(bg: string, color: string): React.CSSProperties {
  return { display: 'inline-block', padding: '1px 7px', borderRadius: 4, fontSize: 11, fontWeight: 500, background: bg, color }
}

const btnPrimary: React.CSSProperties = {
  background: 'linear-gradient(135deg, #3B82F6, #1D4ED8)',
  color: '#fff', border: 'none', borderRadius: 8, padding: '8px 16px',
  fontSize: 12, fontWeight: 600, cursor: 'pointer',
  boxShadow: '0 2px 8px rgba(59,130,246,0.3)',
  textDecoration: 'none',
}

const btnSecondary: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 5,
  background: '#fff', color: '#374151', border: '1px solid #E5E7EB',
  borderRadius: 8, padding: '7px 14px',
  fontSize: 12, fontWeight: 500, cursor: 'pointer',
  textDecoration: 'none', boxShadow: 'var(--shadow-sm)',
}

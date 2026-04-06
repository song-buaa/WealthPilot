/**
 * AssetAllocationCard — 大类资产配置卡片（共享组件）
 *
 * 从 Dashboard.tsx 抽取，供 Dashboard 和 Allocation 复用。
 * 货币类与其他四类使用完全相同的条形图样式，仅 tooltip 显示绝对金额区间。
 */

import { fmtCny, fmtPct } from '@/lib/fmt'
import type { PortfolioSummary } from '@/lib/api'

// ── 类别定义 ────────────────────────────────────────────────

const ALLOC_CATS = [
  { key: 'monetary',     label: '货币', color: '#3B82F6', minPct: 0.8,  maxPct: 8.2  },
  { key: 'fixed_income', label: '固收', color: '#10B981', minPct: 20,   maxPct: 60   },
  { key: 'equity',       label: '权益', color: '#F59E0B', minPct: 40,   maxPct: 80   },
  { key: 'alternative',  label: '另类', color: '#8B5CF6', minPct: 0,    maxPct: 10   },
  { key: 'derivative',   label: '衍生', color: '#EF4444', minPct: 0,    maxPct: 10   },
] as const

const ALLOC_EXAMPLES: Record<string, string> = {
  monetary:     '余额宝、货币基金、活期存款等',
  fixed_income: '债券基金、银行理财、信托等',
  equity:       '股票、股票基金、指数ETF等',
  alternative:  '黄金、大宗商品、REITs 等',
  derivative:   '期权、期货等',
}

// ── 组件 ────────────────────────────────────────────────────

interface Props {
  allocation: PortfolioSummary['allocation']
  /** 货币类绝对金额区间（元），传入后货币行的 tooltip 显示金额区间 */
  cashRange?: { min: number; max: number }
}

export default function AssetAllocationCard({ allocation, cashRange }: Props) {
  return (
    <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12, padding: '16px 20px 8px', boxShadow: 'var(--shadow-sm)' }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 }}>
        📊 大类资产配置
        <span style={{ fontSize: 11, fontWeight: 400, color: '#9CA3AF', marginLeft: 2 }}>当前 vs 目标区间</span>
      </div>
      {/* 图例 */}
      <div style={{ display: 'flex', gap: 14, marginBottom: 8, fontSize: 11, color: '#9CA3AF', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <div style={{ width: 18, height: 7, background: 'rgba(59,130,246,0.14)', borderRadius: 3, border: '1px solid rgba(59,130,246,0.28)' }} />目标区间
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <div style={{ width: 2, height: 11, background: 'rgba(59,130,246,0.45)', borderRadius: 1 }} />目标中值
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <div style={{ width: 11, height: 11, borderRadius: '50%', background: '#3B82F6', border: '2px solid white', boxShadow: '0 0 0 1px rgba(0,0,0,0.12)' }} />当前配置
        </div>
      </div>
      {/* 偏差列表：所有五类使用统一的条形图样式 */}
      <div>
        {ALLOC_CATS.map(cat => {
          const cur    = allocation[cat.key]?.pct   ?? 0
          const amount = allocation[cat.key]?.value ?? 0
          const { minPct: min, maxPct: max } = cat
          const mid    = (min + max) / 2
          const bw     = Math.max(max - min, 0.5)
          const dotPos = Math.min(Math.max(cur, 0), 100)

          let badge: { text: string; bg: string; color: string }
          if (cur > max)
            badge = { text: `↑ 超配 +${(cur - max).toFixed(1)}%`, bg: '#FEE2E2', color: '#DC2626' }
          else if (min > 0 && cur < min)
            badge = { text: `↓ 低配 −${(min - cur).toFixed(1)}%`, bg: '#DBEAFE', color: '#1D4ED8' }
          else
            badge = { text: '✓ 区间内', bg: '#DCFCE7', color: '#16A34A' }

          // 货币类 tooltip 显示绝对金额区间，其他类显示百分比区间
          const rangeTip = (cat.key === 'monetary' && cashRange)
            ? `目标：${cashRange.min / 10000}万 ~ ${cashRange.max / 10000}万元`
            : `目标区间：${min}% ~ ${max}%`
          const amountTip  = fmtCny(amount)
          const exampleTip = ALLOC_EXAMPLES[cat.key] ?? ''

          return (
            <div key={cat.key} style={{
              display: 'grid', gridTemplateColumns: '52px 1fr 64px 104px',
              alignItems: 'center', gap: 12, height: 44,
              borderBottom: '1px solid #F3F4F6',
            }}>
              <span data-tip={exampleTip} style={{ fontSize: 13, fontWeight: 500, color: '#374151', cursor: 'default' }}>
                {cat.label}
              </span>
              <div style={{ position: 'relative', height: 7, background: '#F3F4F6', borderRadius: 4 }}>
                <div data-tip={rangeTip} style={{ position: 'absolute', top: 0, bottom: 0, left: `${min}%`, width: `${bw}%`, background: 'rgba(59,130,246,0.12)', border: '1px solid rgba(59,130,246,0.25)', borderRadius: 4 }} />
                <div style={{ position: 'absolute', top: -2, bottom: -2, left: `${mid}%`, width: 2, background: 'rgba(59,130,246,0.35)', borderRadius: 1 }} />
                <div data-tip={amountTip} style={{ position: 'absolute', top: '50%', left: `${dotPos}%`, transform: 'translate(-50%,-50%)', width: 11, height: 11, borderRadius: '50%', background: cat.color, border: '2px solid white', boxShadow: '0 0 0 1px rgba(0,0,0,0.12)', zIndex: 2 }} />
              </div>
              <span style={{ fontSize: 13, fontWeight: 600, color: '#1B2A4A', fontVariantNumeric: 'tabular-nums', textAlign: 'right' }}>
                {fmtPct(cur)}
              </span>
              <span style={{
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                padding: '4px 10px', borderRadius: 5, fontSize: 11, fontWeight: 600,
                whiteSpace: 'nowrap', background: badge.bg, color: badge.color,
              }}>
                {badge.text}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export { ALLOC_CATS, ALLOC_EXAMPLES }

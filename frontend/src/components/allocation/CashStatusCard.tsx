/**
 * CashStatusCard — 货币类状态独立展示卡片
 * 置于看板顶部，独立于五大类条形图之外。
 * 不显示占比，只显示金额和状态标签。
 */

import type { CashDeviation } from '@/lib/allocation-api'

const STATUS_CONFIG = {
  sufficient: { label: '充足', color: '#059669', bg: '#D1FAE5' },
  low:        { label: '略低', color: '#D97706', bg: '#FEF3C7' },
  insufficient: { label: '不足', color: '#DC2626', bg: '#FEE2E2' },
} as const

export default function CashStatusCard({ cash }: { cash: CashDeviation }) {
  const cfg = STATUS_CONFIG[cash.status]

  return (
    <div style={{
      background: '#fff',
      borderRadius: 12,
      padding: '14px 18px',
      border: '1px solid #E5E7EB',
      boxShadow: '0 1px 3px rgba(15,30,53,0.07), 0 1px 2px rgba(15,30,53,0.04)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 16,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{
          width: 36, height: 36, borderRadius: 10,
          background: '#EFF6FF',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 16,
        }}>
          💰
        </div>
        <div>
          <div style={{ fontSize: 11, color: '#9CA3AF', marginBottom: 2 }}>货币类（流动性底仓）</div>
          <div style={{ fontSize: 16, fontWeight: 700, color: '#1B2A4A' }}>
            {(cash.current_amount / 10000).toFixed(1)} 万元
          </div>
        </div>
      </div>

      <div style={{ textAlign: 'right' }}>
        <span style={{
          display: 'inline-flex', alignItems: 'center', gap: 3,
          padding: '2px 6px',
          borderRadius: 8,
          fontSize: 10,
          fontWeight: 500,
          color: cfg.color,
          background: cfg.bg,
        }}>
          {cfg.label}
        </span>
        <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 4 }}>
          建议区间: {(cash.min_amount / 10000).toFixed(0)}~{(cash.max_amount / 10000).toFixed(0)} 万元
        </div>
      </div>
    </div>
  )
}

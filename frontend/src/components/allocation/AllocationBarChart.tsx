/**
 * AllocationBarChart — 五大类横向分段条形图（不含货币类）
 *
 * 每类显示当前占比、目标中值、是否在区间内。
 * 超配橙色、低配蓝色、在区间内绿色。
 * 样式对齐 Dashboard 卡片规范。
 */

import type { DeviationSnapshot, AssetTarget } from '@/lib/allocation-api'
import { ALLOC_LABEL, BAR_STATUS_COLOR } from '@/lib/allocation-api'

interface Props {
  deviation: DeviationSnapshot
  targets: AssetTarget[]
}

const NON_CASH_KEYS = ['fixed', 'equity', 'alt', 'deriv'] as const

function getBarColor(dev: { is_in_range: boolean; deviation: number; deviation_level: string }) {
  if (dev.deviation_level === 'alert') return BAR_STATUS_COLOR.alert
  if (!dev.is_in_range && dev.deviation > 0) return BAR_STATUS_COLOR.above_ceiling
  if (!dev.is_in_range && dev.deviation < 0) return BAR_STATUS_COLOR.below_floor
  return BAR_STATUS_COLOR.in_range
}

export default function AllocationBarChart({ deviation, targets }: Props) {
  const targetMap = Object.fromEntries(targets.map(t => [t.asset_class, t]))

  return (
    <div style={{
      background: '#fff',
      borderRadius: 12,
      padding: '16px 20px',
      border: '1px solid #E5E7EB',
      boxShadow: '0 1px 3px rgba(15,30,53,0.07), 0 1px 2px rgba(15,30,53,0.04)',
    }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: '#1B2A4A', marginBottom: 14 }}>
        资产配置分布
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {NON_CASH_KEYS.map(key => {
          const dev = deviation.by_class[key]
          if (!dev) return null

          const target = targetMap[key]
          const color = getBarColor(dev)
          const barWidth = Math.max(2, dev.current_ratio * 100)

          return (
            <div key={key}>
              {/* 标签行 */}
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: '#374151' }}>
                  {ALLOC_LABEL[key] || key}
                </span>
                <span style={{ fontSize: 11, color: '#6B7280' }}>
                  {(dev.current_ratio * 100).toFixed(1)}%
                  <span style={{ color: '#9CA3AF', marginLeft: 4 }}>
                    (目标 {(dev.target_mid * 100).toFixed(0)}%)
                  </span>
                </span>
              </div>

              {/* 条形图 */}
              <div style={{ position: 'relative', height: 20, background: '#F3F4F6', borderRadius: 4 }}>
                {/* 当前占比条 */}
                <div style={{
                  position: 'absolute', top: 0, left: 0, bottom: 0,
                  width: `${Math.min(100, barWidth)}%`,
                  background: color,
                  borderRadius: 4,
                  transition: 'width 0.3s ease',
                }} />

                {/* 目标中值线 */}
                {dev.target_mid > 0 && (
                  <div style={{
                    position: 'absolute',
                    top: -2, bottom: -2,
                    left: `${dev.target_mid * 100}%`,
                    width: 2,
                    background: '#374151',
                    borderRadius: 1,
                  }} />
                )}

                {/* 目标区间范围 */}
                {target && target.floor_ratio != null && (
                  <div style={{
                    position: 'absolute',
                    top: 0, bottom: 0,
                    left: `${(target.floor_ratio ?? 0) * 100}%`,
                    width: `${(target.ceiling_ratio - (target.floor_ratio ?? 0)) * 100}%`,
                    background: 'rgba(55, 65, 81, 0.06)',
                    borderRadius: 3,
                    border: '1px dashed rgba(55, 65, 81, 0.12)',
                    pointerEvents: 'none',
                  }} />
                )}
              </div>

              {/* 偏离标注 */}
              {!dev.is_in_range && (
                <div style={{
                  fontSize: 10, marginTop: 1,
                  color: dev.deviation > 0 ? '#EA580C' : '#3B82F6',
                  fontWeight: 500,
                }}>
                  {dev.deviation > 0 ? '超配' : '低配'} {Math.abs(dev.deviation * 100).toFixed(1)}pp
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* 图例 */}
      <div style={{
        display: 'flex', gap: 14, marginTop: 14,
        fontSize: 10, color: '#9CA3AF',
      }}>
        <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 2, background: BAR_STATUS_COLOR.in_range, marginRight: 3, verticalAlign: 'middle' }} />在区间内</span>
        <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 2, background: BAR_STATUS_COLOR.above_ceiling, marginRight: 3, verticalAlign: 'middle' }} />超配</span>
        <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 2, background: BAR_STATUS_COLOR.below_floor, marginRight: 3, verticalAlign: 'middle' }} />低配</span>
        <span><span style={{ display: 'inline-block', width: 2, height: 10, background: '#374151', marginRight: 3, verticalAlign: 'middle' }} />目标中值</span>
      </div>
    </div>
  )
}

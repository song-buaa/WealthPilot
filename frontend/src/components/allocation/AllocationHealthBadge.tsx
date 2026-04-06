/**
 * AllocationHealthBadge — 配置健康状态标签 + 动作建议
 * 样式对齐 Dashboard 的 alert/status badge 规范
 */

import {
  STATUS_TEXT, ACTION_TEXT,
  type OverallStatusType, type PriorityActionType,
} from '@/lib/allocation-api'

interface Props {
  overallStatus: OverallStatusType
  priorityAction: PriorityActionType
}

const STATUS_STYLE: Record<OverallStatusType, { color: string; bg: string }> = {
  on_target:              { color: '#059669', bg: '#D1FAE5' },
  mild_deviation:         { color: '#D97706', bg: '#FEF3C7' },
  significant_deviation:  { color: '#EA580C', bg: '#FFF7ED' },
  alert:                  { color: '#DC2626', bg: '#FEE2E2' },
}

export default function AllocationHealthBadge({ overallStatus, priorityAction }: Props) {
  const s = STATUS_STYLE[overallStatus]

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      padding: '10px 14px',
      background: s.bg,
      borderRadius: 10,
      border: `1px solid ${s.color}30`,
    }}>
      <span style={{
        display: 'inline-flex', alignItems: 'center', gap: 3,
        padding: '2px 6px', borderRadius: 8,
        fontSize: 10, fontWeight: 500,
        color: s.color, background: '#fff',
      }}>
        {STATUS_TEXT[overallStatus]}
      </span>
      <span style={{ fontSize: 12, color: '#6B7280' }}>{ACTION_TEXT[priorityAction]}</span>
    </div>
  )
}

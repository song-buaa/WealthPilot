/**
 * EmptyState — 通用空状态组件（规范 8.1）
 */
import { Upload, type LucideIcon } from 'lucide-react'

interface EmptyStateProps {
  icon?: LucideIcon
  title?: string
  desc?: string
}

export default function EmptyState({
  icon: Icon = Upload,
  title = '暂无数据',
  desc = '请先导入数据',
}: EmptyStateProps) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '48px 24px',
        color: '#9CA3AF',
        textAlign: 'center',
      }}
    >
      <Icon size={40} strokeWidth={1.5} style={{ marginBottom: 12, opacity: 0.5 }} />
      <div style={{ fontSize: 14, fontWeight: 500, color: '#6B7280', marginBottom: 4 }}>
        {title}
      </div>
      <div style={{ fontSize: 13, color: '#9CA3AF', lineHeight: 1.6 }}>
        {desc}
      </div>
    </div>
  )
}

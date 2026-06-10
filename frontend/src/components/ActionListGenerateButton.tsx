/**
 * ActionListGenerateButton — "生成行动清单" 按钮（三态）
 *
 * | 状态 | 视觉 | 文案 |
 * |------|------|------|
 * | default | 灰色浅色 | "生成行动清单" |
 * | highlighted | 主色高亮+角标 | "AI 检测到可执行行动，点击查看" |
 * | completed | 完成态 | "已加入投资行动 →" |
 *
 * 三态由 props 驱动，按钮始终可点击（不 disabled）。
 */
import { Loader2, Zap, CheckCircle } from 'lucide-react'

export type ActionButtonState = 'default' | 'highlighted' | 'loading' | 'completed'

interface Props {
  state: ActionButtonState
  actionable_hint?: string | null
  onClick: () => void
}

export default function ActionListGenerateButton({ state, actionable_hint, onClick }: Props) {
  if (state === 'completed') {
    return (
      <button
        onClick={onClick}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          padding: '6px 14px', fontSize: 12, borderRadius: 8,
          border: '1px solid #D1FAE5', background: '#ECFDF5', color: '#059669',
          cursor: 'pointer', fontWeight: 500,
        }}
      >
        <CheckCircle size={14} />
        已加入投资行动 →
      </button>
    )
  }

  if (state === 'loading') {
    return (
      <button
        disabled
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          padding: '6px 14px', fontSize: 12, borderRadius: 8,
          border: '1px solid #E5E7EB', background: '#F9FAFB', color: '#9CA3AF',
          cursor: 'wait', fontWeight: 500,
        }}
      >
        <Loader2 size={14} className="animate-spin" />
        生成中...
      </button>
    )
  }

  if (state === 'highlighted') {
    return (
      <button
        onClick={onClick}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          padding: '6px 14px', fontSize: 12, borderRadius: 8,
          border: '1px solid #93C5FD', background: '#EFF6FF', color: '#1D4ED8',
          cursor: 'pointer', fontWeight: 600,
          boxShadow: '0 0 0 2px rgba(59, 130, 246, 0.15)',
        }}
      >
        <Zap size={14} />
        {actionable_hint || 'AI 检测到可执行行动，点击查看'}
      </button>
    )
  }

  // default
  return (
    <button
      onClick={onClick}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        padding: '6px 14px', fontSize: 12, borderRadius: 8,
        border: '1px solid #E5E7EB', background: '#F9FAFB', color: '#6B7280',
        cursor: 'pointer', fontWeight: 500,
        transition: 'background 0.15s',
      }}
      onMouseEnter={e => (e.currentTarget.style.background = '#F3F4F6')}
      onMouseLeave={e => (e.currentTarget.style.background = '#F9FAFB')}
    >
      <Zap size={14} />
      生成行动清单
    </button>
  )
}

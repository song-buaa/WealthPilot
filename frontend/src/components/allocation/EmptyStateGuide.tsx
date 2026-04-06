/**
 * EmptyStateGuide — 首次用户 / 无持仓引导态
 * 样式对齐 Placeholder 页面和 EmptyState 组件规范
 */

import { useNavigate } from 'react-router-dom'
import { Target } from 'lucide-react'
import AllocationPrinciplesPanel from './AllocationPrinciplesPanel'

export default function EmptyStateGuide() {
  const nav = useNavigate()

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '48px 24px',
      textAlign: 'center',
    }}>
      {/* 图标 — 对齐 EmptyState 的 icon 规范 */}
      <Target size={40} strokeWidth={1.5} style={{ marginBottom: 12, opacity: 0.5, color: '#9CA3AF' }} />

      {/* 标题 — 对齐 Placeholder 规范 */}
      <div style={{ fontSize: 16, fontWeight: 600, color: '#6B7280', marginBottom: 4 }}>
        还没有配置记录
      </div>

      {/* 描述 */}
      <div style={{ fontSize: 13, color: '#9CA3AF', lineHeight: 1.6, maxWidth: 420, marginBottom: 16 }}>
        让我们从头规划你的资产结构。WealthPilot 会基于你的风险偏好和投资目标，
        为你制定五大类资产的配置方案。
      </div>

      {/* 主按钮 — 对齐 primary button 规范 */}
      <button
        onClick={() => nav('/allocation/chat')}
        style={{
          padding: '8px 16px',
          background: 'linear-gradient(135deg, #3B82F6, #1D4ED8)',
          color: '#fff',
          border: 'none',
          borderRadius: 8,
          fontSize: 12,
          fontWeight: 500,
          cursor: 'pointer',
          display: 'inline-flex',
          alignItems: 'center',
          gap: 5,
          marginBottom: 24,
        }}
      >
        开始规划
      </button>

      {/* 配置原则说明（默认展开，帮助首次用户理解） */}
      <div style={{ width: '100%', maxWidth: 520 }}>
        <AllocationPrinciplesPanel defaultOpen />
      </div>

      {/* 底部提示 — 对齐 Placeholder 规范 */}
      <div style={{ marginTop: 24, fontSize: 12, color: '#D1D5DB', display: 'flex', alignItems: 'center', gap: 6 }}>
        📐 导入持仓数据后，看板将自动展示配置状态
      </div>
    </div>
  )
}

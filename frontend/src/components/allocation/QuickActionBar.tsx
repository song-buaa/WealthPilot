/**
 * QuickActionBar — 快速行动入口
 * 按钮样式对齐 Dashboard 的 primary/secondary button 规范
 */

import { useNavigate } from 'react-router-dom'

export default function QuickActionBar() {
  const nav = useNavigate()

  return (
    <div style={{ display: 'flex', gap: 10 }}>
      <button
        onClick={() => nav('/allocation/chat')}
        style={{
          flex: 1,
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
          justifyContent: 'center',
          gap: 5,
        }}
      >
        规划一笔新资金
      </button>
      <button
        onClick={() => nav('/allocation/chat')}
        style={{
          flex: 1,
          padding: '7px 14px',
          background: '#fff',
          color: '#374151',
          border: '1px solid #E5E7EB',
          borderRadius: 8,
          fontSize: 12,
          fontWeight: 500,
          cursor: 'pointer',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 5,
        }}
      >
        AI 配置咨询
      </button>
    </div>
  )
}

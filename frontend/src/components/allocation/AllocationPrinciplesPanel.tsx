/**
 * AllocationPrinciplesPanel — 配置原则说明（空状态引导页使用）
 */

import { useState } from 'react'
import { ChevronDown, Scale } from 'lucide-react'

export default function AllocationPrinciplesPanel({ defaultOpen = false }: { defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div style={{
      background: '#fff',
      borderRadius: 12,
      border: '1px solid #E5E7EB',
      boxShadow: '0 1px 3px rgba(15,30,53,0.07), 0 1px 2px rgba(15,30,53,0.04)',
      overflow: 'hidden',
    }}>
      <button
        onClick={() => setOpen(!open)}
        style={{
          width: '100%',
          padding: '12px 18px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          fontSize: 13,
          fontWeight: 700,
          color: '#1B2A4A',
        }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Scale size={14} style={{ color: '#3B82F6' }} />
          配置原则说明
        </span>
        <ChevronDown
          size={14}
          style={{
            color: '#9CA3AF',
            transform: open ? 'rotate(180deg)' : 'none',
            transition: 'transform 0.15s',
          }}
        />
      </button>

      {open && (
        <div style={{
          padding: '0 18px 14px',
          fontSize: 12,
          color: '#6B7280',
          lineHeight: 1.7,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}>
          <div style={{ background: '#EFF6FF', borderRadius: 6, padding: '8px 12px' }}>
            <div style={{ fontWeight: 600, color: '#1B2A4A', marginBottom: 2 }}>多元资产配置</div>
            货币保流动性，固收稳底盘，权益求增长，另类分散风险，衍生作战术工具，不同资产解决不同问题。
          </div>
          <div style={{ background: '#F0FDF4', borderRadius: 6, padding: '8px 12px' }}>
            <div style={{ fontWeight: 600, color: '#1B2A4A', marginBottom: 2 }}>目标区间管理</div>
            每类资产都有自己的目标区间，资产配置的重点不是判断短期涨跌，而是让整体结构长期保持在合理范围内。
          </div>
          <div style={{ background: '#FFF7ED', borderRadius: 6, padding: '8px 12px' }}>
            <div style={{ fontWeight: 600, color: '#1B2A4A', marginBottom: 2 }}>动态再平衡</div>
            当配置出现偏离时，优先通过新增资金自然修正，减少不必要的卖出操作，只有偏离明显时才考虑主动调整。
          </div>

          <div style={{ fontSize: 11, color: '#C4C9D4', marginTop: 4 }}>
            所有资产配置建议均会经过投资纪律校验。
          </div>
        </div>
      )}
    </div>
  )
}

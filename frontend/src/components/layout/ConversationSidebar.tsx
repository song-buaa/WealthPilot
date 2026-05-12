/**
 * ConversationSidebar — 会话历史列表（投资决策页内嵌）
 *
 * 结构：[+ 新对话] 按钮 + 会话列表（按 updated_at 倒序）
 * 交互：点击切换、双击重命名、hover 删除
 */
import React, { useState, useRef } from 'react'
import { Plus, Trash2, MessageSquare } from 'lucide-react'
import { useDecisionStore } from '@/store/decisionStore'

function relativeTime(iso: string): string {
  const d = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return '刚刚'
  if (diffMin < 60) return `${diffMin}分钟前`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) {
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
    const thatDay = new Date(d.getFullYear(), d.getMonth(), d.getDate())
    if (today.getTime() === thatDay.getTime()) {
      return d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0')
    }
    return '昨天'
  }
  const diffDay = Math.floor(diffHr / 24)
  if (diffDay === 1) return '昨天'
  if (diffDay < 30) return `${diffDay}天前`
  return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

interface Props {
  onSwitch: (id: string) => void
  onNew: () => void
}

export default function ConversationSidebar({ onSwitch, onNew }: Props) {
  const { conversations, activeConversationId, deleteConversation, renameConversation } = useDecisionStore()
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const [hoverId, setHoverId] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  function handleStartRename(id: string, currentTitle: string) {
    setEditingId(id)
    setEditValue(currentTitle || '')
    setTimeout(() => inputRef.current?.select(), 0)
  }

  function handleFinishRename(id: string) {
    const trimmed = editValue.trim()
    if (trimmed && trimmed !== conversations.find(c => c.id === id)?.title) {
      renameConversation(id, trimmed)
    }
    setEditingId(null)
  }

  async function handleDelete(e: React.MouseEvent, id: string) {
    e.stopPropagation()
    if (!confirm('确认删除这个对话？')) return
    await deleteConversation(id)
  }

  return (
    <div style={{
      width: 220, flexShrink: 0,
      background: '#F9FAFB', borderRight: '1px solid #E5E7EB',
      display: 'flex', flexDirection: 'column', overflow: 'hidden',
    }}>
      {/* 新对话按钮 */}
      <div style={{ padding: '12px 10px 8px' }}>
        <button
          onClick={onNew}
          style={{
            width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
            padding: '8px 0', fontSize: 13, fontWeight: 500,
            color: '#3B82F6', background: '#EFF6FF',
            border: '1px solid #BFDBFE', borderRadius: 8,
            cursor: 'pointer', transition: 'background 0.15s',
          }}
          onMouseEnter={e => (e.currentTarget.style.background = '#DBEAFE')}
          onMouseLeave={e => (e.currentTarget.style.background = '#EFF6FF')}
        >
          <Plus size={14} /> 新对话
        </button>
      </div>

      {/* 会话列表 */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '0 6px 12px' }}>
        {conversations.length === 0 && (
          <div style={{ padding: '20px 10px', textAlign: 'center', fontSize: 12, color: '#9CA3AF' }}>
            暂无对话记录
          </div>
        )}
        {conversations.map(conv => {
          const isActive = conv.id === activeConversationId
          const isHover = conv.id === hoverId
          const isEditing = conv.id === editingId

          return (
            <div
              key={conv.id}
              onClick={() => { if (!isEditing) onSwitch(conv.id) }}
              onMouseEnter={() => setHoverId(conv.id)}
              onMouseLeave={() => setHoverId(null)}
              onDoubleClick={() => handleStartRename(conv.id, conv.title ?? '')}
              style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '8px 10px', marginBottom: 2, borderRadius: 8,
                cursor: isEditing ? 'text' : 'pointer',
                background: isActive ? '#EFF6FF' : isHover ? '#F3F4F6' : 'transparent',
                border: isActive ? '1px solid #BFDBFE' : '1px solid transparent',
                transition: 'background 0.12s',
              }}
            >
              <MessageSquare size={14} style={{ flexShrink: 0, color: isActive ? '#3B82F6' : '#9CA3AF' }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                {isEditing ? (
                  <input
                    ref={inputRef}
                    value={editValue}
                    onChange={e => setEditValue(e.target.value)}
                    onBlur={() => handleFinishRename(conv.id)}
                    onKeyDown={e => {
                      if (e.key === 'Enter') handleFinishRename(conv.id)
                      if (e.key === 'Escape') setEditingId(null)
                    }}
                    style={{
                      width: '100%', fontSize: 12, padding: '2px 4px',
                      border: '1px solid #93C5FD', borderRadius: 4, outline: 'none',
                      background: '#fff',
                    }}
                    autoFocus
                  />
                ) : (
                  <>
                    <div style={{
                      fontSize: 12, fontWeight: isActive ? 600 : 400,
                      color: isActive ? '#1B2A4A' : '#374151',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>
                      {conv.title || '新对话'}
                    </div>
                    <div style={{ fontSize: 10, color: '#9CA3AF', marginTop: 1 }}>
                      {relativeTime(conv.updated_at)}
                    </div>
                  </>
                )}
              </div>

              {/* 删除按钮 — hover 时显示 */}
              {(isHover || isActive) && !isEditing && (
                <button
                  onClick={(e) => handleDelete(e, conv.id)}
                  style={{
                    flexShrink: 0, background: 'none', border: 'none',
                    cursor: 'pointer', padding: 2, borderRadius: 4,
                    color: '#9CA3AF', display: 'flex',
                  }}
                  onMouseEnter={e => (e.currentTarget.style.color = '#EF4444')}
                  onMouseLeave={e => (e.currentTarget.style.color = '#9CA3AF')}
                  title="删除对话"
                >
                  <Trash2 size={13} />
                </button>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

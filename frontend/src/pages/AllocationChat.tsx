/**
 * AllocationChat — 配置方案对话页
 * 路由: /allocation/chat
 *
 * 通过 SSE 调用投资决策后端（/api/decision/chat），共用意图识别。
 * AssetAllocation 意图走资产配置完整处理逻辑。
 * 右侧面板使用 ExplainData 格式展示分析依据。
 */

import { useRef, useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2, Send, SquarePen, Sparkles, User, ChevronDown, ChevronLeft } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useAllocationStore, type AllocMessage } from '@/store/allocationStore'
import { ExplainPanel as DecisionExplainPanel, ExplainEmpty } from '@/pages/Decision'
import type { ExplainData } from '@/lib/api'

// ── Markdown 渲染组件映射（对齐 Decision.tsx）──────────────

const MD_COMPONENTS = {
  p:      ({ children }: { children?: React.ReactNode }) => <p style={{ margin: '0 0 10px' }}>{children}</p>,
  strong: ({ children }: { children?: React.ReactNode }) => <strong style={{ fontWeight: 700 }}>{children}</strong>,
  ul:     ({ children }: { children?: React.ReactNode }) => <ul style={{ listStyleType: 'disc', paddingLeft: '1.5rem', margin: '0 0 10px' }}>{children}</ul>,
  ol:     ({ children }: { children?: React.ReactNode }) => <ol style={{ listStyleType: 'decimal', paddingLeft: '1.5rem', margin: '0 0 10px' }}>{children}</ol>,
  li:     ({ children }: { children?: React.ReactNode }) => <li style={{ display: 'list-item', marginBottom: 4 }}>{children}</li>,
  h1:     ({ children }: { children?: React.ReactNode }) => <h1 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 8px' }}>{children}</h1>,
  h2:     ({ children }: { children?: React.ReactNode }) => <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 8px' }}>{children}</h2>,
  h3:     ({ children }: { children?: React.ReactNode }) => <h3 style={{ fontSize: 14, fontWeight: 700, margin: '0 0 6px' }}>{children}</h3>,
  hr:     () => <hr style={{ border: 'none', borderTop: '1px solid #E5E7EB', margin: '10px 0' }} />,
  code:   ({ children }: { children?: React.ReactNode }) => <code style={{ background: '#F3F4F6', borderRadius: 4, padding: '1px 5px', fontSize: 13, fontFamily: 'monospace' }}>{children}</code>,
}

export default function AllocationChat() {
  const nav = useNavigate()
  const {
    messages, isStreaming, explainContent, isExplainLoading,
    sendMessage, clearChat, abortStream,
  } = useAllocationStore()

  const [input, setInput] = useState('')
  const messagesEnd = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // 预填问题（从"了解更多"跳转）
  useEffect(() => {
    const prefill = sessionStorage.getItem('allocation_prefill')
    if (prefill) {
      sessionStorage.removeItem('allocation_prefill')
      setTimeout(() => sendMessage(prefill), 100)
    }
  }, [])

  useEffect(() => {
    messagesEnd.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = () => {
    const text = input.trim()
    if (!text || isStreaming) return
    setInput('')
    if (textareaRef.current) textareaRef.current.style.height = '44px'
    sendMessage(text)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    e.target.style.height = '44px'
    e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
  }

  return (
    <div style={{ height: '100%', display: 'flex', overflow: 'hidden' }}>
      {/* ── 左栏：对话区（70%）── */}
      <div style={{ flex: '0 0 70%', minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* 顶部标题 */}
        <div style={{ flexShrink: 0, padding: '18px 24px 14px', borderBottom: '1px solid #E5E7EB', display: 'flex', alignItems: 'center', gap: 12 }}>
          <div
            onClick={() => nav('/allocation')}
            style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', borderRadius: 8, padding: '4px 8px 4px 2px', margin: '-4px -8px -4px -2px', transition: 'background 0.15s', flex: 1 }}
            onMouseEnter={e => (e.currentTarget.style.background = '#F3F4F6')}
            onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
          >
            <ChevronLeft size={16} style={{ color: '#9CA3AF', flexShrink: 0 }} />
            <div style={{ width: 36, height: 36, borderRadius: 12, background: '#1e3a5f', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
              <span style={{ fontSize: 20, lineHeight: 1 }}>🎯</span>
            </div>
            <div>
              <div style={{ fontSize: 16, fontWeight: 700, color: '#1B2A4A', letterSpacing: -0.3 }}>资产配置</div>
              <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 1 }}>AI 顾问 · 五大类配置 · 纪律校验</div>
            </div>
          </div>
          {messages.length > 0 && (
            <button
              style={{ background: 'none', border: '1px solid #E5E7EB', borderRadius: 8, padding: '5px 10px', fontSize: 11, color: '#9CA3AF', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}
              onClick={clearChat}
            >
              <SquarePen size={11} /> 新会话
            </button>
          )}
        </div>

        {/* 消息列表 */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 14 }}>
          {messages.length === 0 && <WelcomeArea onSelect={sendMessage} />}

          {messages.map(msg => {
            if (msg.role === 'user') return <UserBubble key={msg.id} content={msg.content} />
            if (msg.role === 'ai') return <AIBubble key={msg.id} msg={msg} />
            if (msg.role === 'error') return <ErrorBubble key={msg.id} content={msg.content} />
            return null
          })}

          <div ref={messagesEnd} />
        </div>

        {/* 输入框 */}
        <div style={{ flexShrink: 0, padding: '14px 24px', borderTop: '1px solid #E5E7EB', background: '#fff' }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end' }}>
            <textarea
              ref={textareaRef}
              value={input}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              placeholder='描述你的配置需求，例如"我有100万，帮我规划资产配置"'
              rows={1}
              style={{
                flex: 1, border: '1px solid #E5E7EB', borderRadius: 12,
                padding: '10px 14px', fontSize: 14, color: '#374151',
                resize: 'none', minHeight: 44, maxHeight: 120,
                outline: 'none', fontFamily: 'inherit', lineHeight: 1.5, overflowY: 'auto',
              }}
              disabled={isStreaming}
            />
            <button
              onClick={isStreaming ? abortStream : handleSend}
              disabled={!isStreaming && !input.trim()}
              style={{
                width: 40, height: 40, flexShrink: 0,
                background: isStreaming ? '#EF4444' : (input.trim() ? '#1B2A4A' : '#E5E7EB'),
                borderRadius: '50%', border: 'none',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                cursor: isStreaming || input.trim() ? 'pointer' : 'not-allowed',
                color: '#fff', transition: 'background 0.15s',
              }}
              title={isStreaming ? '停止' : '发送'}
            >
              {isStreaming
                ? <span style={{ fontSize: 14, fontWeight: 700 }}>■</span>
                : <Send size={16} />}
            </button>
          </div>
        </div>
      </div>

      {/* ── 右栏：分析过程面板（30%）── */}
      <div style={{ flex: '0 0 30%', minWidth: 0, background: '#FAFAFA', display: 'flex', flexDirection: 'column', overflow: 'hidden', borderLeft: '1px solid #E5E7EB' }}>
        <div style={{ flexShrink: 0, padding: '18px 20px 14px', borderBottom: '1px solid #E5E7EB', display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ width: 36, height: 36, borderRadius: '50%', background: '#EFF6FF', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            <Sparkles size={18} color="#3B82F6" />
          </div>
          <div>
            <div style={{ fontSize: 16, fontWeight: 700, color: '#1B2A4A', letterSpacing: -0.3 }}>分析过程</div>
            <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 1 }}>本次分析的关键数据与推理依据</div>
          </div>
        </div>
        {/* 内容区：explainContent 优先；其次用最后一条完成的 AI 消息的 stages/intent 作 fallback（对齐 Decision.tsx） */}
        <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
          {isExplainLoading ? (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 40, color: '#9CA3AF' }}>
              <Loader2 size={18} className="animate-spin" />
            </div>
          ) : explainContent ? (
            <DecisionExplainPanel data={explainContent} />
          ) : (() => {
            const lastDone = messages.filter(m => m.role === 'ai' && !m.streaming && m.content).at(-1)
            if (!lastDone) return <ExplainEmpty />
            const fallback: ExplainData = {
              decision_id: String(lastDone.id),
              intent: lastDone.intent as ExplainData['intent'],
              stages: (lastDone.stages ?? []).map(s => ({ name: s.name, status: s.status, summary: s.summary ?? '' })),
              conclusion: lastDone.conclusion,
            }
            return <DecisionExplainPanel data={fallback} />
          })()}
        </div>
      </div>
    </div>
  )
}

// ── 欢迎区 ──────────────────────────────────────────────────

function WelcomeArea({ onSelect }: { onSelect: (q: string) => void }) {
  const [openCat, setOpenCat] = useState<string | null>(null)

  return (
    <div style={{ display: 'flex', justifyContent: 'center', width: '100%' }}>
      <div style={{ width: '100%', maxWidth: 600, padding: '16px 0 12px' }}>
        <div style={{ textAlign: 'center', marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, marginBottom: 4 }}>
            <div style={{ width: 44, height: 44, borderRadius: '50%', background: '#3B82F6', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
              <Sparkles size={22} color="white" />
            </div>
            <div style={{ fontSize: 22, fontWeight: 700, color: '#1B2A4A', letterSpacing: -0.3 }}>AI资产配置</div>
          </div>
          <div style={{ fontSize: 13, color: '#6B7280', lineHeight: 1.7 }}>
            告诉我你的配置需求，我会基于偏离度和投资纪律，<br />为你生成结构化的配置方案。
          </div>
        </div>

        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: '#9CA3AF', letterSpacing: '0.4px', textTransform: 'uppercase', marginBottom: 8 }}>你可以这样问我</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {['我有100万，帮我规划资产配置', '下个月发30万年终奖，怎么加', '我现在的配置合理吗'].map(q => (
              <div key={q} onClick={() => onSelect(q)}
                style={{ background: '#F8FAFC', border: '1px solid #E5E7EB', borderRadius: 8, padding: '10px 14px', fontSize: 13, color: '#374151', cursor: 'pointer', lineHeight: 1.5 }}
                onMouseEnter={e => (e.currentTarget.style.background = '#EFF6FF')}
                onMouseLeave={e => (e.currentTarget.style.background = '#F8FAFC')}
              >{q}</div>
            ))}
          </div>
        </div>

        <div>
          <div style={{ fontSize: 11, fontWeight: 600, color: '#9CA3AF', letterSpacing: '0.4px', textTransform: 'uppercase', marginBottom: 8 }}>按问题类型</div>
          <div style={{ border: '1px solid #E5E7EB', borderRadius: 12, overflow: 'hidden' }}>
            {[
              { icon: '📊', label: '配置方案', key: 'plan', questions: ['我有50万，帮我规划资产配置', '年终奖20万怎么分配'] },
              { icon: '🔍', label: '配置诊断', key: 'diag', questions: ['我的配置合理吗', '哪些资产需要调整'] },
              { icon: '💡', label: '概念解答', key: 'edu', questions: ['什么是另类资产', '为什么建议这个比例'] },
            ].map((cat, idx) => (
              <div key={cat.key}>
                <div onClick={() => setOpenCat(openCat === cat.key ? null : cat.key)}
                  style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px', cursor: 'pointer', background: openCat === cat.key ? '#F0F7FF' : '#fff', borderTop: idx > 0 ? '1px solid #F3F4F6' : undefined, userSelect: 'none' }}>
                  <span style={{ fontSize: 18, lineHeight: 1 }}>{cat.icon}</span>
                  <span style={{ flex: 1, fontSize: 13, fontWeight: 500, color: '#374151' }}>{cat.label}</span>
                  <span style={{ fontSize: 11, background: '#F3F4F6', color: '#6B7280', borderRadius: 10, padding: '2px 7px', fontWeight: 500 }}>{cat.questions.length}</span>
                  <ChevronDown size={14} style={{ color: '#9CA3AF', transform: openCat === cat.key ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s', flexShrink: 0 }} />
                </div>
                {openCat === cat.key && (
                  <div style={{ background: '#F8FAFC', borderTop: '1px solid #EFF6FF' }}>
                    {cat.questions.map(q => (
                      <div key={q} onClick={() => onSelect(q)}
                        style={{ padding: '8px 16px 8px 48px', fontSize: 13, color: '#4B5563', cursor: 'pointer', lineHeight: 1.5, borderBottom: '1px solid #F0F0F0' }}
                        onMouseEnter={e => (e.currentTarget.style.background = '#EFF6FF')}
                        onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                      >{q}</div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        <div style={{ textAlign: 'center', fontSize: 11, color: '#C4C9D4', marginTop: 14 }}>
          配置建议仅供参考，不构成投资建议。最终决策由你自行做出。
        </div>
      </div>
    </div>
  )
}

// ── 消息气泡组件 ────────────────────────────────────────────

function UserBubble({ content }: { content: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'flex-start', gap: 8 }}>
      <div style={{
        maxWidth: '78%',
        background: 'linear-gradient(135deg, #1B2A4A, #2D4A7A)',
        color: '#fff', borderRadius: '14px 14px 4px 14px',
        padding: '10px 14px', fontSize: 14, lineHeight: 1.6,
        whiteSpace: 'pre-wrap', wordBreak: 'break-word',
      }}>
        {content}
      </div>
      <div style={{ width: 32, height: 32, borderRadius: '50%', background: '#4B5563', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
        <User size={14} color="white" />
      </div>
    </div>
  )
}

function AIBubble({ msg }: { msg: AllocMessage }) {
  // loading 态：无内容且正在流式输出
  if (msg.streaming && !msg.content) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ width: 32, height: 32, borderRadius: '50%', background: '#3B82F6', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
          <Sparkles size={14} color="white" />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#9CA3AF', fontSize: 14 }}>
          <Loader2 size={16} className="animate-spin" />
          正在分析中...
        </div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', justifyContent: 'flex-start', alignItems: 'flex-start', gap: 8 }}>
      <div style={{ width: 32, height: 32, borderRadius: '50%', background: '#3B82F6', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
        <Sparkles size={14} color="white" />
      </div>
      <div style={{ maxWidth: 'calc(90% - 40px)', display: 'flex', flexDirection: 'column', gap: 6 }}>
        {msg.content && (
          <div style={{
            background: '#fff', border: '1px solid #E5E7EB', borderRadius: '4px 14px 14px 14px',
            padding: '10px 14px', fontSize: 14, lineHeight: 1.7,
            color: msg.error ? '#DC2626' : '#1F2937',
            wordBreak: 'break-word',
            boxShadow: 'var(--shadow-sm)',
          }}>
            {msg.error ? (
              <span>⚠️ {msg.content}</span>
            ) : (
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
                {msg.streaming ? msg.content + '▊' : msg.content}
              </ReactMarkdown>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function ErrorBubble({ content }: { content: string }) {
  return (
    <div style={{ background: '#FEE2E2', border: '1px solid #FECACA', borderRadius: 10, padding: '10px 14px', fontSize: 13, color: '#7F1D1D' }}>
      ⚠️ {content}
    </div>
  )
}

// 右侧面板已统一使用 Decision.tsx 的 ExplainPanel 和 ExplainEmpty 组件

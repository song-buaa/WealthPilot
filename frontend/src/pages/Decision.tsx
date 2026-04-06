/**
 * Decision — 投资决策
 * 左栏：SSE 多轮对话（意图识别 + 阶段进度 + AI 流式文字）
 * 右栏：决策链路面板（intent / stages / conclusion）
 *
 * 注意：AppLayout 对此路由特殊处理 — height:100% overflow:hidden
 */
import React, { useState, useEffect, useRef, useCallback } from 'react'
import { Loader2, Send, AlertTriangle, AlertCircle, CheckCircle, XCircle, MinusCircle, ChevronDown, Sparkles, SquarePen, User, Lightbulb } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { streamDecisionChat, decisionApi, portfolioApi, type ExplainData, type Position } from '@/lib/api'

// ── 消息类型 ─────────────────────────────────────────────────
interface Message {
  id: number
  role: 'user' | 'ai'
  content: string
  streaming?: boolean
  error?: boolean
  decisionId?: string
  // 进度状态（AI 消息附带）
  intent?: Record<string, unknown>
  stages?: StageInfo[]
  conclusion?: { verdict: string; summary: string }
}

interface StageInfo {
  name: string
  status: string
  summary?: string
}

// ── 阶段 badge ────────────────────────────────────────────────
const STAGE_STATUS: Record<string, { icon: React.ReactNode; color: string; bg: string; label: string }> = {
  pass:    { icon: <CheckCircle size={12} />,  color: '#059669', bg: '#D1FAE5', label: '通过' },
  fail:    { icon: <XCircle size={12} />,      color: '#DC2626', bg: '#FEE2E2', label: '阻断' },
  warn:    { icon: <AlertTriangle size={12} />, color: '#D97706', bg: '#FEF3C7', label: '警告' },
  skip:    { icon: <MinusCircle size={12} />,  color: '#9CA3AF', bg: '#F3F4F6', label: '跳过' },
  running: { icon: <Loader2 size={12} className="animate-spin" />, color: '#3B82F6', bg: '#EFF6FF', label: '进行中' },
}

function stageBadge(status: string) {
  const s = STAGE_STATUS[status.toLowerCase()] ?? STAGE_STATUS.skip
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 10, fontWeight: 500, padding: '2px 6px', borderRadius: 8, background: s.bg, color: s.color }}>
      {s.icon}{s.label}
    </span>
  )
}

// ── 阶段名称中文化 ────────────────────────────────────────────
const STAGE_NAMES: Record<string, string> = {
  discipline:     '纪律检查',
  leverage:       '杠杆评估',
  concentration:  '集中度检查',
  psychology:     '心理过滤',
  viewpoints:     '观点支撑',
  pre_check:      '前置检查',
  rules:          '规则审核',
  signals:        '信号分析',
  llm:            'AI综合判断',
  data:           '数据准备',
  intent:         '意图识别',
}

function stageName(raw: string): string {
  return STAGE_NAMES[raw.toLowerCase()] ?? raw
}

// ── B区：通用推荐问题（兜底）────────────────────────────────────
const GENERIC_SUGGESTIONS = [
  '如果我准备开始配置权益资产，第一步应该怎么做？',
  '稳健型投资者应该怎么理解股债的仓位比例？',
  '同样是买基金，主动型和指数型怎么选？',
]

// ── B-1：根据持仓生成个性化推荐问题 ────────────────────────────
const GENERIC_NAME_WORDS = ['投资', '组合', '策略', '配置', '理财']
function isGenericName(name: string): boolean {
  return name.length < 3 || GENERIC_NAME_WORDS.some(w => name.includes(w))
}

function buildPersonalizedQuestions(positions: Position[], totalAssets: number): string[] {
  const holdings = positions
    .filter(p => p.market_value_cny > 0)
    .map(p => ({
      name: p.name,
      ratio: p.market_value_cny / totalAssets,
      pnl: p.profit_loss_value ?? 0,
    }))
    .sort((a, b) => b.ratio - a.ratio)

  if (!holdings.length) return []

  const q1h = holdings[0]
  // Q3：positionRatio 第二高，不与 Q1 重复，且名称非通用词
  const q3h = holdings.slice(1).find(h => h.name !== q1h.name && !isGenericName(h.name)) ?? null
  const forbidden = new Set([q1h.name, q3h?.name].filter(Boolean) as string[])

  // Q2：浮亏最大（绝对值）且不与 Q1/Q3 重叠；若全盈利则取波动最大
  const negatives = holdings
    .filter(h => h.pnl < 0 && !forbidden.has(h.name))
    .sort((a, b) => a.pnl - b.pnl)

  let q2h = negatives[0] ?? null
  let q2IsNegative = true

  if (!q2h) {
    const fallback = holdings
      .filter(h => !forbidden.has(h.name))
      .sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl))
    q2h = fallback[0] ?? null
    q2IsNegative = false
  }

  const questions: string[] = []
  questions.push(`${q1h.name} 目前仓位偏重，我需要重新评估一下吗？`)
  if (q2h) {
    questions.push(
      q2IsNegative
        ? `${q2h.name} 目前处于浮亏状态，这个持仓还值得继续拿吗？`
        : `${q2h.name} 近期波动比较大，我这部分仓位需要做什么调整吗？`
    )
  }
  if (q3h) {
    questions.push(`如果新增一笔资金，${q3h.name} 在现有组合里还值得优先考虑吗？`)
  }
  return questions
}

// ── 意图分类数据 ──────────────────────────────────────────────
const INTENT_CATEGORIES = [
  {
    key: 'single',
    label: '单标的决策',
    icon: '🎯',
    questions: [
      '我有一只股票最近涨了不少，该不该趁现在落袋为安？',
      '我有一只基金持续亏损，现在止损出来还是继续持有？',
      '我看好一个标的想加仓，但它在我组合里已经不轻了，怎么判断能不能加？',
    ],
  },
  {
    key: 'portfolio',
    label: '组合评估',
    icon: '📊',
    questions: [
      '我的持仓里有几只股票集中在同一个行业，这样风险大吗？',
      '我最近调仓比较频繁，整体组合现在是什么状态？',
      '我感觉我的组合在震荡市里跌得比较多，问题出在哪？',
    ],
  },
  {
    key: 'allocation',
    label: '资产配置',
    icon: '🗂️',
    questions: [
      '我现在大部分钱都在股票上，固收和现金留得很少，这样合理吗？',
      '我准备把一笔到期的理财重新配置，不知道怎么分？',
      '我的港股和A股持仓比例有点失衡，需要调整吗？',
    ],
  },
  {
    key: 'performance',
    label: '收益分析',
    icon: '📈',
    questions: [
      '这段时间大盘还行，但我的组合收益明显跑输了，为什么？',
      '我有几笔投资一直是正收益，但整体算下来并不好看，哪里出了问题？',
      '我想知道过去三个月里，是哪些持仓在拖累我的整体表现？',
    ],
  },
  {
    key: 'education',
    label: '通用问题',
    icon: '📚',
    questions: [
      '我总是在股票涨了之后才后悔没多买，跌了又舍不得止损，怎么破？',
      '我听说要定期做再平衡，但不知道什么情况下该做、怎么做？',
      '分散投资和集中持仓我一直没想清楚，对我来说哪种更适合？',
    ],
  },
]

// ── 主组件 ────────────────────────────────────────────────────
export default function Decision() {
  const sessionId   = useRef<string>(crypto.randomUUID())
  const messagesEnd = useRef<HTMLDivElement>(null)
  const abortRef    = useRef<AbortController | null>(null)
  const msgIdRef    = useRef<number>(0)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const [messages, setMessages]   = useState<Message[]>([])
  const [input, setInput]         = useState('')
  const [streaming, setStreaming] = useState(false)
  const [explainData, setExplainData] = useState<ExplainData | null>(null)
  const [openCategory, setOpenCategory] = useState<string | null>(null)
  const [recSuggestions, setRecSuggestions] = useState<string[]>([])
  const [recMode, setRecMode] = useState<'personalized' | 'generic'>('generic')

  // B区：拉取持仓数据，生成个性化推荐
  useEffect(() => {
    Promise.all([portfolioApi.getPositions(), portfolioApi.getSummary()])
      .then(([posResult, summary]) => {
        const positions = posResult.items
        const totalAssets = summary.total_assets
        if (positions.length >= 1 && totalAssets > 0) {
          const qs = buildPersonalizedQuestions(positions, totalAssets)
          if (qs.length >= 1) {
            setRecSuggestions(qs)
            setRecMode('personalized')
            return
          }
        }
        setRecSuggestions(GENERIC_SUGGESTIONS)
        setRecMode('generic')
      })
      .catch(() => {
        setRecSuggestions(GENERIC_SUGGESTIONS)
        setRecMode('generic')
      })
  }, [])

  function handleSelectQuestion(q: string) {
    setInput(q)
    setTimeout(() => {
      const el = textareaRef.current
      if (el) { el.focus(); el.setSelectionRange(q.length, q.length) }
    }, 0)
  }

  // 自动滚动到底部
  useEffect(() => {
    messagesEnd.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = useCallback(async () => {
    const text = input.trim()
    if (!text || streaming) return
    setInput('')

    const userId = ++msgIdRef.current
    const aiId   = ++msgIdRef.current

    // 添加用户消息 + AI 占位消息
    setMessages(prev => [
      ...prev,
      { id: userId, role: 'user', content: text },
      { id: aiId, role: 'ai', content: '', streaming: true, stages: [] },
    ])

    const controller = new AbortController()
    abortRef.current = controller
    setStreaming(true)

    const updateAi = (updater: (m: Message) => Message) => {
      setMessages(prev => prev.map(m => m.id === aiId ? updater(m) : m))
    }

    try {
      let lastDecisionId: string | null = null

      for await (const ev of streamDecisionChat(text, sessionId.current, controller.signal)) {
        if (ev.type === 'text') {
          const delta = (ev.data.delta as string) ?? ''
          updateAi(m => ({ ...m, content: m.content + delta }))

        } else if (ev.type === 'intent') {
          updateAi(m => ({ ...m, intent: ev.data }))

        } else if (ev.type === 'stage') {
          const stage: StageInfo = {
            name:    (ev.data.stage as string) ?? '',
            status:  (ev.data.status as string) ?? 'running',
            summary: ev.data.label as string | undefined,
          }
          updateAi(m => ({
            ...m,
            stages: [
              ...(m.stages ?? []).filter(s => s.name !== stage.name),
              stage,
            ],
          }))

        } else if (ev.type === 'done') {
          const did            = ev.data.decision_id   as string | undefined
          const conclusionLevel = ev.data.conclusion_level as string | undefined
          const conclusionLabel = ev.data.conclusion_label as string | undefined
          if (did) lastDecisionId = did
          updateAi(m => ({
            ...m,
            streaming: false,
            decisionId: did,
            // running → pass（后端不发完成事件，done 时统一标记）
            stages: (m.stages ?? []).map(s =>
              s.status.toLowerCase() === 'running' ? { ...s, status: 'pass' } : s
            ),
            // 从 done 事件提取结论
            ...(conclusionLevel ? {
              conclusion: { verdict: conclusionLabel ?? conclusionLevel, summary: '' },
            } : {}),
          }))

        } else if (ev.type === 'error') {
          updateAi(m => ({ ...m, streaming: false, error: true, content: m.content || '发生错误，请重试' }))
        }
      }

      // 拉取完整 explain 数据供右侧面板展示
      if (lastDecisionId) {
        try {
          console.log('[getExplain] calling with decisionId=', lastDecisionId, 'sessionId=', sessionId.current)
          const explain = await decisionApi.getExplain(lastDecisionId, sessionId.current)
          console.log('[getExplain] success:', explain)
          setExplainData(explain)
        } catch (err) {
          console.error('[getExplain] failed:', err)
        }
      } else {
        console.warn('[getExplain] skipped: lastDecisionId is null')
      }

    } catch (e: unknown) {
      if ((e as Error)?.name !== 'AbortError') {
        updateAi(m => ({ ...m, streaming: false, error: true, content: m.content || '连接失败，请检查网络后重试' }))
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }, [input, streaming])

  function handleClear() {
    abortRef.current?.abort()
    decisionApi.clearSession(sessionId.current).catch(() => {})
    sessionId.current = crypto.randomUUID()
    setMessages([])
    setExplainData(null)
    setInput('')
    setStreaming(false)
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  // 动态调整 textarea 高度
  function handleInputChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setInput(e.target.value)
    e.target.style.height = 'auto'
    e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
  }

  return (
    <div style={{ height: '100%', display: 'flex', overflow: 'hidden' }}>
      {/* ── 左栏：对话区 ── */}
      <div style={{ flex: '0 0 70%', minWidth: 0, display: 'flex', flexDirection: 'column', borderRight: '1px solid #E5E7EB', overflow: 'hidden' }}>
        {/* 顶部标题 */}
        <div style={{ flexShrink: 0, padding: '18px 24px 14px', borderBottom: '1px solid #E5E7EB', display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ width: 36, height: 36, borderRadius: 12, background: '#1e3a5f', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span style={{ fontSize: 20, lineHeight: 1 }}>💡</span>
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 16, fontWeight: 700, color: '#1B2A4A', letterSpacing: -0.3 }}>投资决策</div>
            <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 1 }}>AI 辅助 · 纪律守护 · 多轮对话</div>
          </div>
          {messages.length > 0 && (
            <button
              style={{ background: 'none', border: '1px solid #E5E7EB', borderRadius: 8, padding: '5px 10px', fontSize: 11, color: '#9CA3AF', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}
              onClick={handleClear}
            >
              <SquarePen size={11} /> 新会话
            </button>
          )}
        </div>

        {/* 消息列表 */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 14 }}>
          {messages.length === 0 && (
            <div style={{ display: 'flex', justifyContent: 'center', width: '100%' }}>
              <div style={{ width: '100%', maxWidth: 600, padding: '16px 0 12px' }}>

                {/* A区：图标 + 主标题 + 副标题 */}
                <div style={{ textAlign: 'center', marginBottom: 16 }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, marginBottom: 4 }}>
                    <div style={{ width: 44, height: 44, borderRadius: '50%', background: '#3B82F6', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                      <Sparkles size={22} color="white" />
                    </div>
                    <div style={{ fontSize: 22, fontWeight: 700, color: '#1B2A4A', letterSpacing: -0.3 }}>AI投资决策</div>
                  </div>
                  <div style={{ fontSize: 13, color: '#6B7280', lineHeight: 1.7 }}>
                    告诉我你的投资想法，我会结合持仓、风险、纪律和已有观点，<br />为你做一次结构化评估。
                  </div>
                </div>

                {/* B区：个性化推荐 */}
                {recSuggestions.length > 0 && (
                  <div style={{ marginBottom: 16 }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: '#9CA3AF', letterSpacing: '0.4px', textTransform: 'uppercase', marginBottom: 8 }}>
                      {recMode === 'personalized' ? '为你推荐' : '你可以这样问我'}
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                      {recSuggestions.map(q => (
                        <div
                          key={q}
                          onClick={() => handleSelectQuestion(q)}
                          style={{ background: '#F8FAFC', border: '1px solid #E5E7EB', borderRadius: 8, padding: '10px 14px', fontSize: 13, color: '#374151', cursor: 'pointer', lineHeight: 1.5 }}
                          onMouseEnter={e => (e.currentTarget.style.background = '#EFF6FF')}
                          onMouseLeave={e => (e.currentTarget.style.background = '#F8FAFC')}
                        >
                          {q}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* C区：意图分类列表 */}
                <div>
                  <div style={{ fontSize: 11, fontWeight: 600, color: '#9CA3AF', letterSpacing: '0.4px', textTransform: 'uppercase', marginBottom: 8 }}>
                    按问题类型开始
                  </div>
                  <div style={{ border: '1px solid #E5E7EB', borderRadius: 12, overflow: 'hidden' }}>
                    {INTENT_CATEGORIES.map((cat, idx) => {
                      const isOpen = openCategory === cat.key
                      return (
                        <div key={cat.key}>
                          {/* 分类行 */}
                          <div
                            onClick={() => setOpenCategory(isOpen ? null : cat.key)}
                            style={{
                              display: 'flex', alignItems: 'center', gap: 10,
                              padding: '10px 16px', cursor: 'pointer',
                              background: isOpen ? '#F0F7FF' : '#fff',
                              borderTop: idx > 0 ? '1px solid #F3F4F6' : undefined,
                              userSelect: 'none',
                            }}
                          >
                            <span style={{ fontSize: 18, lineHeight: 1 }}>{cat.icon}</span>
                            <span style={{ flex: 1, fontSize: 13, fontWeight: 500, color: '#374151' }}>{cat.label}</span>
                            <span style={{ fontSize: 11, background: '#F3F4F6', color: '#6B7280', borderRadius: 10, padding: '2px 7px', fontWeight: 500 }}>{cat.questions.length}</span>
                            <ChevronDown size={14} style={{ color: '#9CA3AF', transform: isOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s', flexShrink: 0 }} />
                          </div>
                          {/* 展开的问题列表 */}
                          {isOpen && (
                            <div style={{ background: '#F8FAFC', borderTop: '1px solid #EFF6FF' }}>
                              {cat.questions.map(q => (
                                <div
                                  key={q}
                                  onClick={() => handleSelectQuestion(q)}
                                  style={{ padding: '8px 16px 8px 48px', fontSize: 13, color: '#4B5563', cursor: 'pointer', lineHeight: 1.5, borderBottom: '1px solid #F0F0F0' }}
                                  onMouseEnter={e => (e.currentTarget.style.background = '#EFF6FF')}
                                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                                >
                                  {q}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
                <div style={{ textAlign: 'center', fontSize: 11, color: '#C4C9D4', marginTop: 14 }}>
                  市场有风险，投资需谨慎。本功能仅供辅助参考，不构成任何投资建议。
                </div>
              </div>
            </div>
          )}

          {messages.map(msg => (
            msg.role === 'user' ? (
              <UserMessage key={msg.id} msg={msg} />
            ) : (
              <AiMessage key={msg.id} msg={msg} />
            )
          ))}
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
              placeholder='输入你的投资想法，例如"腾讯仓位有点重，想评估一下是否需要调整"'
              rows={1}
              style={{
                flex: 1,
                border: '1px solid #E5E7EB',
                borderRadius: 12,
                padding: '10px 14px',
                fontSize: 14,
                color: '#374151',
                resize: 'none',
                minHeight: 44,
                maxHeight: 120,
                outline: 'none',
                fontFamily: 'inherit',
                lineHeight: 1.5,
                overflowY: 'auto',
              }}
              disabled={streaming}
            />
            <button
              onClick={streaming ? () => abortRef.current?.abort() : handleSend}
              disabled={!streaming && !input.trim()}
              style={{
                width: 40, height: 40, flexShrink: 0,
                background: streaming ? '#EF4444' : (input.trim() ? '#1B2A4A' : '#E5E7EB'),
                borderRadius: '50%', border: 'none',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                cursor: streaming || input.trim() ? 'pointer' : 'not-allowed',
                color: '#fff', transition: 'background 0.15s',
              }}
              title={streaming ? '停止' : '发送'}
            >
              {streaming
                ? <span style={{ fontSize: 14, fontWeight: 700 }}>■</span>
                : <Send size={16} />}
            </button>
          </div>
        </div>
      </div>

      {/* ── 右栏：决策依据面板 ── */}
      <div style={{ flex: '0 0 30%', minWidth: 0, background: '#FAFAFA', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* 头部标题 */}
        <div style={{ flexShrink: 0, padding: '18px 20px 14px', borderBottom: '1px solid #E5E7EB', display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ width: 36, height: 36, borderRadius: '50%', background: '#EFF6FF', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            <Sparkles size={18} color="#3B82F6" />
          </div>
          <div>
            <div style={{ fontSize: 16, fontWeight: 700, color: '#1B2A4A', letterSpacing: -0.3 }}>分析过程</div>
            <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 1 }}>本次分析的关键数据与推理依据</div>
          </div>
        </div>

        {/* 生成中提示条 */}
        {streaming && (
          <div style={{ flexShrink: 0, padding: '8px 20px', borderBottom: '1px solid #DBEAFE', display: 'flex', alignItems: 'center', gap: 8, background: '#EFF6FF' }}>
            <Loader2 size={13} className="animate-spin" style={{ color: '#3B82F6' }} />
            <span style={{ fontSize: 12, color: '#3B82F6' }}>正在生成本次分析…</span>
          </div>
        )}

        {/* 内容区：explainData 优先；其次用最后一条完成的 AI 消息的 stages/intent 作 fallback */}
        <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
          {(() => {
            if (explainData) return <ExplainPanel data={explainData} />
            const lastDone = messages.filter(m => m.role === 'ai' && !m.streaming && m.content).at(-1)
            if (!lastDone) return <ExplainEmpty />
            const fallback: ExplainData = {
              decision_id: String(lastDone.id),
              intent: lastDone.intent as ExplainData['intent'],
              stages: (lastDone.stages ?? []).map(s => ({ name: s.name, status: s.status, summary: s.summary ?? '' })),
              conclusion: lastDone.conclusion,
            }
            return <ExplainPanel data={fallback} />
          })()}
        </div>
      </div>
    </div>
  )
}

// ── AI 头像（复用）────────────────────────────────────────────
function AiAvatar() {
  return (
    <div style={{ width: 32, height: 32, borderRadius: '50%', background: '#3B82F6', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
      <Sparkles size={14} color="white" />
    </div>
  )
}

// ── 用户消息气泡 ──────────────────────────────────────────────
function UserMessage({ msg }: { msg: Message }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'flex-start', gap: 8 }}>
      <div style={{
        maxWidth: '78%',
        background: 'linear-gradient(135deg, #1B2A4A, #2D4A7A)',
        color: '#fff', borderRadius: '14px 14px 4px 14px',
        padding: '10px 14px', fontSize: 14, lineHeight: 1.6,
        whiteSpace: 'pre-wrap', wordBreak: 'break-word',
      }}>
        {msg.content}
      </div>
      <div style={{ width: 32, height: 32, borderRadius: '50%', background: '#4B5563', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
        <User size={14} color="white" />
      </div>
    </div>
  )
}

// ── AI 消息 ───────────────────────────────────────────────────
function AiMessage({ msg }: { msg: Message }) {
  // loading 态：无内容且正在流式输出
  if (msg.streaming && !msg.content) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <AiAvatar />
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#9CA3AF', fontSize: 14 }}>
          <Loader2 size={16} className="animate-spin" />
          正在分析中...
        </div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', justifyContent: 'flex-start', alignItems: 'flex-start', gap: 8 }}>
      <AiAvatar />
      <div style={{ maxWidth: 'calc(90% - 40px)', display: 'flex', flexDirection: 'column', gap: 6 }}>
        {/* 文字内容 */}
        {msg.content && (
          <div style={{
            background: '#fff', border: '1px solid #E5E7EB', borderRadius: '4px 14px 14px 14px',
            padding: '10px 14px', fontSize: 14, lineHeight: 1.7,
            color: msg.error ? '#DC2626' : '#1F2937',
            wordBreak: 'break-word',
            boxShadow: 'var(--shadow-sm)',
          }}>
            {msg.error ? (
              <span>
                <AlertTriangle size={14} style={{ marginRight: 6, verticalAlign: 'text-top', color: '#DC2626' }} />
                {msg.content}
              </span>
            ) : (
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  p:      ({ children }) => <p style={{ margin: '0 0 10px' }}>{children}</p>,
                  strong: ({ children }) => <strong style={{ fontWeight: 700 }}>{children}</strong>,
                  ul:     ({ children }) => <ul style={{ listStyleType: 'disc', paddingLeft: '1.5rem', margin: '0 0 10px' }}>{children}</ul>,
                  ol:     ({ children }) => <ol style={{ listStyleType: 'decimal', paddingLeft: '1.5rem', margin: '0 0 10px' }}>{children}</ol>,
                  li:     ({ children }) => <li style={{ display: 'list-item', marginBottom: 4 }}>{children}</li>,
                  h1:     ({ children }) => <h1 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 8px' }}>{children}</h1>,
                  h2:     ({ children }) => <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 8px' }}>{children}</h2>,
                  h3:     ({ children }) => <h3 style={{ fontSize: 14, fontWeight: 700, margin: '0 0 6px' }}>{children}</h3>,
                  hr:     () => <hr style={{ border: 'none', borderTop: '1px solid #E5E7EB', margin: '10px 0' }} />,
                  code:   ({ children }) => <code style={{ background: '#F3F4F6', borderRadius: 4, padding: '1px 5px', fontSize: 13, fontFamily: 'monospace' }}>{children}</code>,
                }}
              >
                {msg.streaming ? msg.content + '▊' : msg.content}
              </ReactMarkdown>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── 意图 badge ────────────────────────────────────────────────
function IntentBadge({ intent }: { intent: Record<string, unknown> }) {
  const asset  = intent.asset as string | undefined
  const action = intent.action as string | undefined
  const conf   = intent.confidence as number | undefined
  if (!asset && !action) return null

  return (
    <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
      {asset  && <Chip label="标的" value={asset} />}
      {action && <Chip label="操作" value={displayAction(action)} />}
      {conf != null && <Chip label="置信" value={`${Math.round(conf * 100)}%`} />}
    </div>
  )
}

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'inline-flex', gap: 4, background: '#EFF6FF', borderRadius: 6, padding: '2px 7px', fontSize: 11 }}>
      <span style={{ color: '#93C5FD' }}>{label}</span>
      <span style={{ fontWeight: 600, color: '#1B2A4A' }}>{value}</span>
    </div>
  )
}

// ── 右侧空状态：分析步骤说明 ────────────────────────────────────
const ANALYSIS_STEPS = [
  { n: 1, title: '识别问题类型', desc: '判断这是单标的、组合、配置、收益或者其他类问题' },
  { n: 2, title: '读取账户数据', desc: '调取相关持仓信息、仓位占比与盈亏状态' },
  { n: 3, title: '检查纪律约束', desc: '核对是否触发投资纪律中的风险规则' },
  { n: 4, title: '分析市场信号', desc: '结合投研观点与风险信号进行综合评估' },
  { n: 5, title: '生成结论',     desc: '输出判断依据与建议方向' },
]

// ── AI 推理过程折叠面板 ──────────────────────────────────────
function ReasoningPanel({ reasoning }: { reasoning: string[] | string }) {
  // 防御性处理：reasoning 可能是字符串（AssetAllocation 意图）或数组
  const items = Array.isArray(reasoning) ? reasoning : reasoning ? [reasoning] : []
  if (items.length === 0) return null
  const [open, setOpen] = React.useState(false)
  return (
    <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '10px 14px' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
      >
        <span style={{ fontSize: 14, fontWeight: 600, color: '#111827' }}>AI 推理过程</span>
        <ChevronDown size={14} style={{ color: '#9CA3AF', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s', flexShrink: 0 }} />
      </button>
      {open && (
        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {items.map((item, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
              <span style={{ flexShrink: 0, width: 5, height: 5, borderRadius: '50%', background: '#9CA3AF', marginTop: 6 }} />
              <span style={{ fontSize: 12, color: '#374151', lineHeight: 1.5 }}>{item}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function ExplainEmpty() {
  return (
    <div style={{ padding: '24px 16px 16px' }}>
      <div style={{ fontSize: 12, color: '#6B7280', lineHeight: 1.7, marginBottom: 16 }}>
        发起一次投资问题后，我会按以下步骤为你分析：
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {ANALYSIS_STEPS.map((step, idx) => (
          <div key={step.n} style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
            {/* 序号 + 连接线 */}
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flexShrink: 0 }}>
              <div style={{ width: 22, height: 22, borderRadius: '50%', background: '#3B82F6', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <span style={{ fontSize: 11, fontWeight: 700, color: '#fff', lineHeight: 1 }}>{step.n}</span>
              </div>
              {idx < ANALYSIS_STEPS.length - 1 && (
                <div style={{ width: 1, height: 16, background: '#E5E7EB', marginTop: 4 }} />
              )}
            </div>
            {/* 文字 */}
            <div>
              <div style={{ fontSize: 13, fontWeight: 500, color: '#374151', marginBottom: 4, lineHeight: '22px' }}>{step.title}</div>
              <div style={{ fontSize: 11, color: '#9CA3AF', lineHeight: 1.6 }}>{step.desc}</div>
            </div>
          </div>
        ))}
      </div>
      <div style={{ fontSize: 11, color: '#C4C9D4', lineHeight: 1.7, marginTop: 20 }}>
        分析完成后，本次判断的关键依据将展示在这里，让结论更透明。
      </div>
    </div>
  )
}

// ── 风险等级辅助 ───────────────────────────────────────────────
const RISK_LABELS = ['低', '较低', '中等', '中等偏高', '高']
function verdictToRiskLevel(verdict: string): number {
  const u = verdict.toUpperCase()
  if (u.includes('STOP_LOSS') || u.includes('止损') || u.includes('BLOCK') || u.includes('拒绝')) return 5
  if (u.includes('SELL') || u.includes('清仓') || u.includes('REDUCE') || u.includes('减仓')) return 4
  if (u.includes('TAKE_PROFIT') || u.includes('止盈') || u.includes('HOLD') || u.includes('观望')) return 3
  if (u.includes('WARN') || u.includes('警告')) return 3
  if (u.includes('ADD') || u.includes('加仓')) return 2
  if (u.includes('BUY') || u.includes('买入') || u.includes('ALLOW') || u.includes('通过')) return 2
  return 3
}
function riskBarColor(level: number): string {
  if (level <= 2) return '#10B981'
  if (level === 3) return '#F59E0B'
  return '#EF4444'
}

// ── 关键依据 chip 颜色 ─────────────────────────────────────────
function stageChipStyle(name: string, status: string): { bg: string; color: string } {
  const s = status.toLowerCase()
  if (s === 'blocked' || s === 'fail') return { bg: '#FEE2E2', color: '#DC2626' }
  const n = name.toLowerCase()
  if (n === 'rules' || n === 'pre_check' || n === 'concentration') return { bg: '#FEF3C7', color: '#D97706' }
  if (n === 'viewpoints') return { bg: '#D1FAE5', color: '#059669' }
  return { bg: '#F3F4F6', color: '#6B7280' }
}

// ── intent 字段辅助映射 ───────────────────────────────────────
// 兼容两路数据：SSE fallback 传英文枚举值，getExplain 传中文值（直接透传）
const ACTION_LABELS: Record<string, string> = {
  BUY:        '买入判断',
  ADD:        '加仓判断',
  SELL:       '卖出判断',
  REDUCE:     '减仓判断',
  HOLD:       '持有观察',
  ANALYZE:    '综合评估',
  TAKE_PROFIT:'止盈',
  STOP_LOSS:  '止损',
}
function displayAction(action: string): string {
  return ACTION_LABELS[action.toUpperCase()] ?? action  // 已是中文则直接透传
}

const PRIMARY_INTENT_LABELS: Record<string, string> = {
  PositionDecision:    '单标的决策',
  PortfolioEvaluation: '组合评估',
  PortfolioReview:     '组合评估',
  AssetAllocation:     '资产配置',
  ReturnAnalysis:      '收益分析',
  GeneralQuestion:     '通用问题',
}

// ── 信号颜色辅助 ──────────────────────────────────────────────
function signalColor(value: string): string {
  if (['正面', '合理', '利好', '低'].some(k => value.includes(k))) return '#10B981'
  if (['负面', '利空', '偏高'].some(k => value.includes(k)))       return '#EF4444'
  if (['偏低', '高', '中'].some(k => value.includes(k)))            return '#F59E0B'
  return '#9CA3AF'
}

// ── 投研观点条目解析 ─────────────────────────────────────────
function parseResearchItem(raw: string): { type: 'user' | 'web' | 'other'; text: string; url: string | null; domain: string | null } {
  let type: 'user' | 'web' | 'other' = 'other'
  let text = raw
  if (text.startsWith('[用户资料]')) { type = 'user'; text = text.slice(6).trim() }
  else if (text.startsWith('[联网参考]')) { type = 'web';  text = text.slice(6).trim() }

  // 匹配末尾括号（中文全角或半角）内的完整 URL
  const urlMatch = text.match(/\s*[（(](https?:\/\/[^）)]+)[）)]\s*$/)
  if (urlMatch) {
    const fullUrl = urlMatch[1]
    const domainMatch = fullUrl.match(/^https?:\/\/([^/?#]+)/)
    const domain = domainMatch ? domainMatch[1].replace(/^www\./, '') : fullUrl
    text = text.slice(0, text.lastIndexOf(urlMatch[0])).trim()
    return { type, text, url: fullUrl, domain }
  }

  // 兼容旧格式：末尾只有裸域名
  const domainMatch = text.match(/\s*\(([a-zA-Z0-9.-]+\.[a-zA-Z]{2,}[^)]*)\)\s*$/)
  const domain = domainMatch ? domainMatch[1] : null
  if (domainMatch) text = text.slice(0, text.lastIndexOf(domainMatch[0])).trim()
  return { type, text, url: domain ? `https://${domain}` : null, domain }
}

// ── 公共区块标题（14px 600 #111827）───────────────────────────
function SectionLabel({ label }: { label: string }) {
  return <div style={{ fontSize: 14, fontWeight: 600, color: '#111827', marginBottom: 10 }}>{label}</div>
}

// ── 可折叠区块标题（用于投研观点）────────────────────────────
function CollapsibleHeader({ label, open, onToggle }: { label: string; open: boolean; onToggle: () => void }) {
  return (
    <button
      onClick={onToggle}
      style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', background: 'none', border: 'none', cursor: 'pointer', padding: 0, marginBottom: open ? 10 : 0 }}
    >
      <span style={{ fontSize: 14, fontWeight: 600, color: '#111827' }}>{label}</span>
      <ChevronDown size={14} style={{ color: '#9CA3AF', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s', flexShrink: 0 }} />
    </button>
  )
}

// ── 右侧结果面板 ─────────────────────────────────────────────────
export function ExplainPanel({ data }: { data: ExplainData }) {
  // ── AssetAllocation 意图专用视图 ──
  if (data.intent?.intent_type === 'asset_allocation') {
    return <AllocationExplainView data={data} />
  }

  const { stages, conclusion, rules, signals } = data
  const research  = data.data?.research
  const position  = data.data?.target_position
  const [chainOpen,    setChainOpen]    = React.useState(false)
  const [researchOpen, setResearchOpen] = React.useState(false)

  const intent    = data.intent
  const riskLevel = conclusion ? verdictToRiskLevel(conclusion.verdict) : 0
  const barColor  = riskBarColor(riskLevel)

  return (
    <div style={{ padding: '16px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>

      {/* ── 1. 识别意图 ── */}
      {intent && (
        <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 14px' }}>
          <SectionLabel label="识别意图" />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {intent.primary_intent && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 12, color: '#6B7280' }}>意图</span>
                <span style={{ fontSize: 12, fontWeight: 600, color: '#111827' }}>
                  {PRIMARY_INTENT_LABELS[intent.primary_intent] ?? intent.primary_intent}
                </span>
              </div>
            )}
            {intent.asset && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 12, color: '#6B7280' }}>标的</span>
                <span style={{ fontSize: 12, fontWeight: 600, color: '#111827' }}>{intent.asset}</span>
              </div>
            )}
            {intent.action && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 12, color: '#6B7280' }}>操作</span>
                <span style={{ fontSize: 12, fontWeight: 600, color: '#111827' }}>
                  {displayAction(intent.action)}
                </span>
              </div>
            )}
            {intent.time_context && intent.time_context !== '未知' && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 12, color: '#6B7280' }}>时间</span>
                <span style={{ fontSize: 12, fontWeight: 600, color: '#111827' }}>{intent.time_context}</span>
              </div>
            )}
            {intent.confidence != null && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 12, color: '#6B7280' }}>置信度</span>
                <span style={{ fontSize: 12, fontWeight: 600, color: '#3B82F6' }}>
                  {Math.round((intent.confidence as number) * 100)}%
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── 1b. 持仓数据 ── */}
      {(position || data.data?.total_assets) && (
        <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 14px' }}>
          <SectionLabel label="持仓数据" />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {data.data?.total_assets != null && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 12, color: '#6B7280' }}>组合总市值</span>
                <span style={{ fontSize: 12, fontWeight: 600, color: '#111827' }}>
                  ¥{(data.data.total_assets / 10000).toFixed(2)}万
                </span>
              </div>
            )}
            {position && (
              <>
                <div style={{ height: 1, background: '#F3F4F6', margin: '2px 0' }} />
                <div style={{ fontSize: 12, fontWeight: 600, color: '#111827', marginBottom: 2 }}>{position.name}</div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: 12, color: '#6B7280' }}>仓位占比</span>
                  <span style={{ fontSize: 12, fontWeight: 600, color: '#111827' }}>
                    {(position.weight * 100).toFixed(1)}%
                  </span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: 12, color: '#6B7280' }}>市值</span>
                  <span style={{ fontSize: 12, fontWeight: 600, color: '#111827' }}>
                    ¥{(position.market_value_cny / 10000).toFixed(2)}万
                  </span>
                </div>
                {position.profit_loss_rate != null && (
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontSize: 12, color: '#6B7280' }}>收益率</span>
                    <span style={{ fontSize: 12, fontWeight: 600, color: position.profit_loss_rate >= 0 ? '#10B981' : '#EF4444' }}>
                      {position.profit_loss_rate >= 0 ? '+' : ''}{(position.profit_loss_rate * 100).toFixed(2)}%
                    </span>
                  </div>
                )}
                {position.platforms && position.platforms.length > 0 && (
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontSize: 12, color: '#6B7280' }}>平台</span>
                    <span style={{ fontSize: 12, fontWeight: 600, color: '#111827' }}>
                      {position.platforms.join(' / ')}
                    </span>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* ── 2. 规则校验 ── */}
      {rules && (
        <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 14px' }}>
          <SectionLabel label="纪律校验" />
          {/* 整体结论 */}
          <div style={{ fontSize: 12, fontWeight: 500, color: rules.violation ? '#EF4444' : rules.warning ? '#D97706' : '#059669', marginBottom: rules.rule_details?.length ? 6 : 0 }}>
            {rules.violation ? '❌ 校验未通过，已拦截' : rules.warning ? `⚠️ ${rules.warning}` : '✅ 纪律校验通过'}
          </div>
          {/* 规则明细 */}
          {rules.rule_details && rules.rule_details.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              {rules.rule_details.map((detail, i) => (
                <div key={i} style={{ fontSize: 12, color: '#374151', lineHeight: 1.5 }}>{detail}</div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── 3. 投研观点（默认折叠）── */}
      {research && research.length > 0 && (
        <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 14px' }}>
          <CollapsibleHeader label="投研观点" open={researchOpen} onToggle={() => setResearchOpen(o => !o)} />
          {researchOpen && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
              {research.map((raw, i) => {
                const { type, text, url, domain } = parseResearchItem(raw)
                const isUser = type === 'user'
                return (
                  <div key={i} style={{
                    display: 'flex', gap: 8, alignItems: 'flex-start',
                    paddingLeft: 8,
                    borderLeft: `2px solid ${isUser ? '#3B82F6' : '#D1D5DB'}`,
                  }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{
                        fontSize: 12, color: '#374151', lineHeight: 1.5,
                        display: '-webkit-box', WebkitLineClamp: 3,
                        WebkitBoxOrient: 'vertical', overflow: 'hidden',
                      }}>
                        {text}
                      </div>
                      {url && domain && (
                        <a href={url} target="_blank" rel="noopener noreferrer"
                          style={{ display: 'inline-block', marginTop: 2, fontSize: 11, color: '#9CA3AF', textDecoration: 'underline' }}>
                          {domain}
                        </a>
                      )}
                    </div>
                    <span style={{
                      flexShrink: 0, fontSize: 11, fontWeight: 500, padding: '1px 6px',
                      borderRadius: 4,
                      background: isUser ? '#EFF6FF' : '#F3F4F6',
                      color: isUser ? '#3B82F6' : '#9CA3AF',
                    }}>
                      {isUser ? '观点库' : '联网'}
                    </span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* ── 4. 四维信号 ── */}
      {signals && (
        <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 14px' }}>
          <SectionLabel label="市场信号" />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {[
              { label: '仓位',   value: signals.position },
              { label: '基本面', value: signals.fundamental },
              { label: '事件',   value: `不确定性${signals.event.uncertainty} · ${signals.event.direction}` },
              { label: '情绪',   value: signals.sentiment },
            ].map(({ label, value }) => (
              <div key={label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 12, color: '#6B7280' }}>{label}</span>
                <span style={{
                  fontSize: 11, fontWeight: 500, padding: '2px 7px', borderRadius: 5,
                  color: signalColor(value),
                  background: signalColor(value) === '#9CA3AF' ? '#F3F4F6' : `${signalColor(value)}18`,
                }}>
                  {value}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── 5. 分析过程（折叠） ── */}
      {(stages ?? []).length > 0 && (
        <div>
          <button
            onClick={() => setChainOpen(o => !o)}
            style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: '#3B82F6', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
          >
            <ChevronDown size={13} style={{ transform: chainOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }} />
            {chainOpen ? '收起分析过程' : '查看完整分析过程'}
          </button>
          {chainOpen && (
            <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
              {(stages ?? []).map(s => {
                const info = STAGE_STATUS[s.status?.toLowerCase()] ?? STAGE_STATUS.skip
                return (
                  <div key={s.name} style={{ background: '#fff', borderLeft: `3px solid ${info.bg}`, borderRadius: '0 6px 6px 0', padding: '7px 10px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: s.summary ? 3 : 0 }}>
                      <span style={{ fontSize: 12, fontWeight: 500, color: '#374151' }}>{stageName(s.name)}</span>
                      {stageBadge(s.status)}
                    </div>
                    {s.summary && <div style={{ fontSize: 11, color: '#6B7280', lineHeight: 1.5 }}>{s.summary}</div>}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* ── 5b. AI 推理过程（折叠，默认收起） ── */}
      {data.llm?.reasoning && data.llm.reasoning.length > 0 && (
        <ReasoningPanel reasoning={data.llm.reasoning} />
      )}

      {/* ── 6. 最终结论完整版 ── */}
      {(conclusion || data.llm) && (
        <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 14px' }}>
          <SectionLabel label="最终结论" />

          {/* 决策标签 + 风险条 */}
          {(() => {
            const verdict = conclusion?.verdict ?? data.llm?.decision_cn ?? ''
            const level   = verdict ? verdictToRiskLevel(verdict) : 0
            const color   = riskBarColor(level)
            return verdict ? (
              <>
                <div style={{ fontSize: 14, fontWeight: 700, color: '#1B2A4A', marginBottom: 8, lineHeight: 1.5 }}>
                  {data.llm?.decision_emoji ? `${data.llm.decision_emoji} ` : ''}{verdict}
                </div>
                {level > 0 && (
                  <>
                    <div style={{ fontSize: 11, color: '#9CA3AF', marginBottom: 5 }}>
                      风险等级：<span style={{ color, fontWeight: 600 }}>{RISK_LABELS[level - 1]}</span>
                    </div>
                    <div style={{ display: 'flex', gap: 3, marginBottom: 12 }}>
                      {RISK_LABELS.map((_, i) => (
                        <div key={i} style={{ flex: 1, height: 5, borderRadius: 3, background: i < level ? color : '#E5E7EB' }} />
                      ))}
                    </div>
                  </>
                )}
              </>
            ) : null
          })()}

          {/* 操作建议 */}
          {data.llm?.strategy && data.llm.strategy.length > 0 && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#111827', marginBottom: 6 }}>操作建议</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {data.llm.strategy.map((s, i) => (
                  <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
                    <span style={{ flexShrink: 0, width: 5, height: 5, borderRadius: '50%', background: '#3B82F6', marginTop: 6 }} />
                    <span style={{ fontSize: 12, color: '#374151', lineHeight: 1.5 }}>{s}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 风险提示 */}
          {data.llm?.risk && data.llm.risk.length > 0 && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#111827', marginBottom: 6 }}>风险提示</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {data.llm.risk.map((r, i) => (
                  <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
                    <span style={{ flexShrink: 0, width: 5, height: 5, borderRadius: '50%', background: '#F59E0B', marginTop: 6 }} />
                    <span style={{ fontSize: 12, color: '#374151', lineHeight: 1.5 }}>{r}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 免责声明 */}
          <div style={{ fontSize: 11, color: '#9CA3AF', lineHeight: 1.6, borderTop: '1px solid #F3F4F6', paddingTop: 8, marginTop: 2 }}>
            本系统输出仅供参考，不构成投资建议。
          </div>
        </div>
      )}

      {/* stages 为空且无完整数据时的简单说明 */}
      {!conclusion && !data.llm && !rules && !signals && !research?.length && (stages ?? []).length === 0 && (
        <div style={{ fontSize: 11, color: '#C4C9D4', lineHeight: 1.7, paddingTop: 4 }}>
          分析链路详情待后端接入 stage 事件后展示。
        </div>
      )}
    </div>
  )
}


// ── AssetAllocation 意图专用面板视图 ───────────────────────────

const ALLOC_SUB_INTENT_LABELS: Record<string, string> = {
  INITIAL_ALLOCATION:   '初始配置',
  INCREMENT_ALLOCATION: '增量补配',
  DIAGNOSIS:            '配置诊断',
  EXPLAIN:              '配置解释',
  CONCEPT:              '概念问答',
}

function AllocationExplainView({ data }: { data: ExplainData }) {
  const intent = data.intent
  const d = data.data as Record<string, unknown> | undefined
  const rules = data.rules as Record<string, unknown> | undefined
  const llm = data.llm as Record<string, unknown> | undefined

  const subIntent = intent?.action ? (ALLOC_SUB_INTENT_LABELS[intent.action] ?? intent.action) : '资产配置'
  const totalAssets = d?.totalAssets as number | undefined
  const overallStatus = d?.overallStatus as string | undefined
  const allocationPlan = d?.allocationPlan as Array<Record<string, unknown>> | undefined
  const reasoning = llm?.reasoning as string[] | undefined

  return (
    <div style={{ padding: '16px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>

      {/* ── 意图识别 ── */}
      <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 14px' }}>
        <SectionLabel label="识别意图" />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: 12, color: '#6B7280' }}>意图</span>
            <span style={{ fontSize: 12, fontWeight: 600, color: '#111827' }}>资产配置</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: 12, color: '#6B7280' }}>子类型</span>
            <span style={{ fontSize: 12, fontWeight: 600, color: '#111827' }}>{subIntent}</span>
          </div>
        </div>
      </div>

      {/* ── 配置数据 ── */}
      {(totalAssets != null || overallStatus) && (
        <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 14px' }}>
          <SectionLabel label="配置数据" />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {totalAssets != null && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 12, color: '#6B7280' }}>
                  {intent?.action === 'INITIAL_ALLOCATION' ? '规划金额' : intent?.action === 'INCREMENT_ALLOCATION' ? '新增金额' : '总资产'}
                </span>
                <span style={{ fontSize: 12, fontWeight: 600, color: '#111827' }}>
                  {(totalAssets / 10000).toFixed(1)}万元
                </span>
              </div>
            )}
            {overallStatus && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 12, color: '#6B7280' }}>配置状态</span>
                <span style={{ fontSize: 12, fontWeight: 600, color: overallStatus === '接近目标' ? '#059669' : '#D97706' }}>
                  {overallStatus}
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── 分配方案 ── */}
      {allocationPlan && allocationPlan.length > 0 && (
        <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 14px' }}>
          <SectionLabel label="分配方案" />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {allocationPlan.map((item, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 12, color: '#6B7280' }}>{String(item.label || item.asset_class)}</span>
                <span style={{ fontSize: 12, fontWeight: 600, color: '#111827' }}>
                  {((item.suggested_amount as number) / 10000).toFixed(1)}万元
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── 纪律校验 ── */}
      {rules && (
        <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 14px' }}>
          <SectionLabel label="纪律校验" />
          <div style={{ fontSize: 12, fontWeight: 500, color: rules.passed ? '#059669' : '#DC2626' }}>
            {rules.passed
              ? '✅ 纪律校验通过'
              : `❌ 触发 ${(rules.violations as unknown[])?.length ?? 0} 条，已自动修正`}
          </div>
          {!rules.passed && (rules.violations as Array<{ message: string; severity: string }> | undefined)?.map((v, i) => (
            <div key={i} style={{ fontSize: 11, color: '#D97706', marginTop: 4 }}>{v.message}</div>
          ))}
        </div>
      )}

      {/* ── 核心判断 ── */}
      {reasoning && reasoning.length > 0 && (
        <ReasoningPanel reasoning={reasoning} />
      )}

      {/* 免责声明 */}
      <div style={{ fontSize: 11, color: '#9CA3AF', lineHeight: 1.6, borderTop: '1px solid #F3F4F6', paddingTop: 8, marginTop: 2 }}>
        本系统输出仅供参考，不构成投资建议。
      </div>
    </div>
  )
}

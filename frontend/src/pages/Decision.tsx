/**
 * Decision — 投资决策
 * 左栏：SSE 多轮对话（意图识别 + 阶段进度 + AI 流式文字）
 * 右栏：决策链路面板（intent / stages / conclusion）
 *
 * 注意：AppLayout 对此路由特殊处理 — height:100% overflow:hidden
 */
import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Loader2, Send, AlertTriangle, AlertCircle, CheckCircle, XCircle, MinusCircle, ChevronDown, ChevronLeft, ChevronRight, Sparkles, SquarePen, User, Lightbulb, BarChart3, Search, BookOpen, Zap } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { streamDecisionChat, decisionApi, portfolioApi, actionApi, conversationsApi, knowledgeApi, type ExplainData, type Position, type ActionDraftResponse, type SymbolStrategyDraft } from '@/lib/api'
import ActionListGenerateButton, { type ActionButtonState } from '@/components/ActionListGenerateButton'
import ActionDraftCard from '@/components/ActionDraftCard'
import ExecutionPlanPanel from '@/components/ExecutionPlanPanel'
import ConversationSidebar from '@/components/layout/ConversationSidebar'
import { useDecisionStore } from '@/store/decisionStore'

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
  candidates?: Array<{ name: string; symbol: string; metric_label: string; metric_type: string }>
  // v3.2 actionable
  actionable?: boolean
  actionable_hint?: string | null
  actionDraftStatus?: 'idle' | 'loading' | 'completed'
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
    .filter(p => p.market_value_cny > 0 && p.ticker !== 'HKCONNECT')
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
  questions.push(`${q1h.name} 仓位偏重，是不是该考虑减仓或再平衡了？`)
  if (q2h) {
    questions.push(
      q2IsNegative
        ? `${q2h.name} 目前处于浮亏状态，这个持仓还值得继续拿吗？`
        : `${q2h.name} 近期波动比较大，我这部分仓位需要做什么调整吗？`
    )
  }
  if (q3h) {
    questions.push(`如果新增一笔资金，现在加仓 ${q3h.name} 合适吗？`)
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
      '我的组合调整过几次了，现在整体是什么状态？',
      '我感觉我的组合在震荡市里跌得比较多，问题出在哪？',
    ],
  },
  {
    key: 'allocation',
    label: '资产配置',
    icon: '🗂️',
    questions: [
      '我有100万准备开始投资，应该怎么分配？',
      '我准备把一笔即将到期的30万理财重新配置，不知道怎么分？',
      '我想把组合调整到更稳健的结构，固收应该加多少？',
    ],
  },
  {
    key: 'performance',
    label: '收益分析',
    icon: '📈',
    questions: [
      '这段时间大盘还行，但我的组合收益明显跑输了，为什么？',
      '我有几笔投资一直是正收益，但整体算下来并不好看，哪里出了问题？',
      '从我现在的持仓来看，哪些标的在拖累整体表现？',
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
  const {
    conversations, activeConversationId, fetchConversations,
    createConversation, switchConversation, updateConversationTitle,
  } = useDecisionStore()

  const [searchParams, setSearchParams] = useSearchParams()
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

  // 分析面板折叠状态（localStorage 持久化，默认折叠）
  const [panelOpen, setPanelOpen] = useState(() => {
    try { return localStorage.getItem('wp_analysis_panel_open') === 'true' } catch { return false }
  })
  function togglePanel() {
    setPanelOpen(prev => {
      const next = !prev
      try { localStorage.setItem('wp_analysis_panel_open', String(next)) } catch {}
      return next
    })
  }

  // v3.2 行动清单
  const [draftCardOpen, setDraftCardOpen] = useState(false)
  const [currentDraft, setCurrentDraft] = useState<ActionDraftResponse | null>(null)
  const [planMetaForModal, setPlanMetaForModal] = useState<{
    plan_id?: string; factor_snapshot?: Record<string,unknown>;
    constraints_applied?: Record<string,unknown>; rationale?: string;
  } | null>(null)
  const [actionMsgId, setActionMsgId] = useState<number | null>(null)

  // ── 初始化：加载会话列表 + URL 参数处理 ──
  const initDone = useRef(false)
  useEffect(() => {
    if (initDone.current) return
    initDone.current = true
    const urlConvId = searchParams.get('conversation_id')
    ;(async () => {
      await fetchConversations()

      if (urlConvId) {
        // 从 Action 页面跳转过来，激活指定会话
        switchConversation(urlConvId)
        setSearchParams({}, { replace: true })
        try {
          const history = await conversationsApi.getMessages(urlConvId)
          const loaded: Message[] = history.map((m, i) => ({
            id: i + 1,
            role: m.role === 'assistant' ? 'ai' as const : 'user' as const,
            content: m.content,
            intent: m.intent ? { primary_intent: m.intent, asset: m.asset } : undefined,
          }))
          msgIdRef.current = loaded.length
          setMessages(loaded)
        } catch (e) {
          console.error('[init] load messages from URL param failed:', e)
        }
      }
      // 无 URL 参数时：不自动选中任何会话，保持 activeConversationId = null
      // 用户点击会话或 "+ 新对话" 后才激活
    })()
  }, [])

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

    // 无激活会话时自动创建（用户在欢迎页直接输入发送）
    let convId = activeConversationId
    if (!convId) {
      convId = await createConversation()
    }

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

      for await (const ev of streamDecisionChat(text, convId, controller.signal)) {
        if (ev.type === 'text') {
          const delta = (ev.data.delta as string) ?? ''
          updateAi(m => ({ ...m, content: m.content + delta }))

        } else if (ev.type === 'candidates') {
          const items = (ev.data.items ?? []) as Message['candidates']
          updateAi(m => ({ ...m, candidates: items }))

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
          // 从 decisionResult 中提取 decisionType，覆盖意图层的 action
          const decisionType = (ev.data.decisionResult as Record<string, unknown>)?.decisionType as string | undefined
          updateAi(m => ({
            ...m,
            streaming: false,
            decisionId: did,
            // running → pass（后端不发完成事件，done 时统一标记）
            stages: (m.stages ?? []).map(s =>
              s.status.toLowerCase() === 'running' ? { ...s, status: 'pass' } : s
            ),
            // 用 decisionType 覆盖 intent.action，保持操作字段与结论一致
            ...(decisionType && m.intent ? {
              intent: { ...(m.intent as Record<string, unknown>), action: decisionType },
            } : {}),
            // 从 done 事件提取结论
            ...(conclusionLevel ? {
              conclusion: { verdict: conclusionLabel ?? conclusionLevel, summary: '' },
            } : {}),
            // v3.2 actionable
            actionable: (ev.data.actionable as boolean) ?? false,
            actionable_hint: (ev.data.actionable_hint as string) ?? null,
          }))

        } else if (ev.type === 'error') {
          updateAi(m => ({ ...m, streaming: false, error: true, content: m.content || '发生错误，请重试' }))
        }
      }

      // 拉取完整 explain 数据供右侧面板展示
      if (lastDecisionId) {
        try {
          console.log('[getExplain] calling with decisionId=', lastDecisionId, 'conversationId=', convId)
          const explain = await decisionApi.getExplain(lastDecisionId, convId)
          console.log('[getExplain] success:', explain)
          setExplainData(explain)
        } catch (err) {
          console.error('[getExplain] failed:', err)
        }
      } else {
        console.warn('[getExplain] skipped: lastDecisionId is null')
      }

      // 首条消息：先用截断标题立即显示，2 秒后刷新（等 LLM 生成完成）
      if (messages.length === 0 && convId) {
        updateConversationTitle(convId, text.slice(0, 20))
        setTimeout(() => fetchConversations(), 2000)
      }

    } catch (e: unknown) {
      if ((e as Error)?.name !== 'AbortError') {
        updateAi(m => ({ ...m, streaming: false, error: true, content: m.content || '连接失败，请检查网络后重试' }))
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }, [input, streaming, activeConversationId, messages.length])

  // ── 会话切换处理 ──
  async function handleSwitchConversation(id: string) {
    if (id === activeConversationId) return
    abortRef.current?.abort()
    setStreaming(false)
    switchConversation(id)
    setMessages([])
    setExplainData(null)
    setInput('')
    // 从 API 加载历史消息
    try {
      const history = await conversationsApi.getMessages(id)
      const loaded: Message[] = history.map((m, i) => ({
        id: i + 1,
        role: m.role === 'assistant' ? 'ai' as const : 'user' as const,
        content: m.content,
        intent: m.intent ? { primary_intent: m.intent, asset: m.asset } : undefined,
      }))
      msgIdRef.current = loaded.length
      setMessages(loaded)
    } catch (e) {
      console.error('[switchConversation] load messages failed:', e)
    }
  }

  async function handleNewConversation() {
    abortRef.current?.abort()
    setStreaming(false)
    const newId = await createConversation()
    setMessages([])
    setExplainData(null)
    setInput('')
  }

  function handleClear() {
    handleNewConversation()
  }

  // v3.2 行动清单生成
  async function handleGenerateAction(msgId: number) {
    // 已完成的直接跳转
    const msg = messages.find(m => m.id === msgId)
    if (msg?.actionDraftStatus === 'completed') {
      // TODO: 跳转到投资行动页面
      return
    }

    // 设置 loading 状态
    setActionMsgId(msgId)
    setMessages(prev => prev.map(m =>
      m.id === msgId ? { ...m, actionDraftStatus: 'loading' as const } : m
    ))

    try {
      // 构建对话上下文
      const context = messages
        .filter(m => m.content)
        .map(m => ({ role: m.role === 'ai' ? 'assistant' : 'user', content: m.content }))

      // 获取 expressing_output（intent + explainData 的持仓信息）
      const aiMsg = messages.find(m => m.id === msgId)
      const expressingOutput: Record<string, unknown> = {}
      if (aiMsg?.intent) {
        expressingOutput.decisionType = (aiMsg.intent as Record<string, unknown>).action
        expressingOutput.confidence = (aiMsg.intent as Record<string, unknown>).confidence
        expressingOutput.asset = (aiMsg.intent as Record<string, unknown>).asset
      }
      // 注入持仓数据供 ActionPlanner 推算（含 estimated_shares / current_price）
      if (explainData?.data) {
        const d = explainData.data as Record<string, unknown>
        if (d.target_position) expressingOutput.target_position = d.target_position
        if (d.total_assets) expressingOutput.total_assets = d.total_assets
      }

      const draft = await actionApi.generateDraft({
        conversation_id: activeConversationId!,
        conversation_context: context,
        expressing_output: expressingOutput,
      })

      setCurrentDraft(draft)
      setDraftCardOpen(true)
    } catch (e: unknown) {
      console.error('[ActionDraft] generate failed:', e)
      // 恢复按钮状态
      setMessages(prev => prev.map(m =>
        m.id === msgId ? { ...m, actionDraftStatus: undefined } : m
      ))
      alert(`行动清单生成失败: ${(e as Error).message}`)
    }
  }

  function handleDraftConfirmed() {
    setDraftCardOpen(false)
    setCurrentDraft(null)
    // 更新按钮为完成态
    if (actionMsgId !== null) {
      setMessages(prev => prev.map(m =>
        m.id === actionMsgId ? { ...m, actionDraftStatus: 'completed' as const } : m
      ))
    }
  }

  function handleDraftClose() {
    setDraftCardOpen(false)
    // 恢复按钮状态（未确认 → 恢复为 idle）
    if (actionMsgId !== null) {
      setMessages(prev => prev.map(m =>
        m.id === actionMsgId && m.actionDraftStatus === 'loading'
          ? { ...m, actionDraftStatus: undefined }
          : m
      ))
    }
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
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden', background: '#F7F8FA' }}>
      {/* ── 标题区 ── */}
      <div style={{ flexShrink: 0, padding: '22px 28px 0', display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <div style={{ width: 38, height: 38, borderRadius: 10, background: 'linear-gradient(135deg, #1B2A4A, #2D4A7A)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 17 }}>💡</div>
        <div>
          <div style={{ fontSize: 20, fontWeight: 700, color: '#1B2A4A', letterSpacing: '-0.3px' }}>投资决策</div>
          <div style={{ fontSize: 12, color: '#9CA3AF', marginTop: 1 }}>AI 辅助 · 纪律守护 · 多轮对话</div>
        </div>
      </div>

      {/* ── 三栏区 ── */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
      {/* ── 会话列表 ── */}
      <ConversationSidebar onSwitch={handleSwitchConversation} onNew={handleNewConversation} />

      {/* ── 聊天主区 ── */}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: '#F7F8FA' }}>
        {/* 消息列表 */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '20px 0', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
          <div style={{ width: '100%', maxWidth: 880, padding: '0 32px', display: 'flex', flexDirection: 'column', gap: 24 }}>
          {messages.length === 0 && (
            <div style={{ display: 'flex', justifyContent: 'center', width: '100%' }}>
              <div style={{ width: '100%', maxWidth: 600, padding: '16px 0 12px' }}>

                {/* A区：欢迎 + 快捷入口 */}
                <div style={{ textAlign: 'center', marginBottom: 16 }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, marginBottom: 4 }}>
                    <div style={{ width: 44, height: 44, borderRadius: '50%', background: '#3B82F6', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                      <Sparkles size={22} color="white" />
                    </div>
                    <div style={{ fontSize: 22, fontWeight: 700, color: '#1B2A4A', letterSpacing: -0.3 }}>WealthPilot 投资决策</div>
                  </div>
                  <div style={{ fontSize: 13, color: '#6B7280', lineHeight: 1.7 }}>
                    让投资决策从凭感觉变成体系化
                  </div>
                  {/* 快捷入口 */}
                  <div style={{ display: 'flex', justifyContent: 'center', gap: 10, marginTop: 14 }}>
                    {[
                      { icon: <BarChart3 size={14} />, label: '分析持仓', prompt: '帮我分析一下当前持仓情况' },
                      { icon: <Search size={14} />,    label: '评估新标的', prompt: '我想评估一个新标的' },
                      { icon: <BookOpen size={14} />,  label: '投资教育', prompt: '给我讲讲投资相关知识' },
                    ].map(item => (
                      <button
                        key={item.label}
                        onClick={() => handleSelectQuestion(item.prompt)}
                        style={{
                          display: 'flex', alignItems: 'center', gap: 6,
                          padding: '8px 16px', fontSize: 12, fontWeight: 500,
                          color: '#374151', background: '#fff',
                          border: '1px solid #E5E7EB', borderRadius: 20,
                          cursor: 'pointer', transition: 'all 0.15s',
                        }}
                        onMouseEnter={e => { e.currentTarget.style.background = '#EFF6FF'; e.currentTarget.style.borderColor = '#93C5FD' }}
                        onMouseLeave={e => { e.currentTarget.style.background = '#fff'; e.currentTarget.style.borderColor = '#E5E7EB' }}
                      >
                        {item.icon} {item.label}
                      </button>
                    ))}
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
              <AiMessage key={msg.id} msg={msg} onSelectCandidate={handleSelectQuestion} onGenerateAction={handleGenerateAction} explainData={explainData} />
            )
          ))}
          <div ref={messagesEnd} />
          </div>{/* 居中容器结束 */}
        </div>

        {/* 输入框 */}
        <div style={{ flexShrink: 0, padding: '14px 0', display: 'flex', justifyContent: 'center' }}>
          <div style={{ width: '100%', maxWidth: 880, padding: '0 32px' }}>
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
                background: streaming ? '#EF4444' : (input.trim() ? '#1F2937' : '#E5E7EB'),
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
          </div>{/* 输入框居中容器结束 */}
        </div>
      </div>

      {/* ── 分析面板（可折叠）── */}
      <div style={{ display: 'flex', flexShrink: 0 }}>
        {/* 左侧边缘按钮（始终同一位置）*/}
        <div
          onClick={togglePanel}
          style={{
            width: 24, flexShrink: 0, background: 'transparent',
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
            cursor: 'pointer',
          }}
          title={panelOpen ? '折叠面板' : '展开面板'}
        >
          {panelOpen
            ? <ChevronRight size={14} style={{ color: '#9CA3AF' }} />
            : <ChevronLeft size={14} style={{ color: '#9CA3AF' }} />
          }
        </div>

        {/* 面板内容（展开时显示）*/}
        {panelOpen && (
          <div style={{ width: 320, flexShrink: 0, background: '#fff', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            {/* 生成中提示条 */}
            {streaming && (
              <div style={{ flexShrink: 0, padding: '8px 16px', display: 'flex', alignItems: 'center', gap: 8, background: '#EFF6FF', borderRadius: 8, margin: '8px 12px' }}>
                <Loader2 size={13} className="animate-spin" style={{ color: '#3B82F6' }} />
                <span style={{ fontSize: 12, color: '#3B82F6' }}>正在生成本次分析…</span>
              </div>
            )}

            {/* 内容区 */}
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
        )}
      </div>

      </div>{/* 三栏区结束 */}

      {/* v3.2 行动清单弹层 */}
      <ActionDraftCard
        open={draftCardOpen}
        onClose={handleDraftClose}
        draft={currentDraft}
        onConfirmed={handleDraftConfirmed}
        planMeta={planMetaForModal}
      />
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
function AiMessage({ msg, onSelectCandidate, onGenerateAction, explainData }: {
  msg: Message
  onSelectCandidate?: (name: string) => void
  onGenerateAction?: (msgId: number) => void
  explainData?: ExplainData | null
}) {
  const [showExecPlan, setShowExecPlan] = useState(false)
  const [userInitiated, setUserInitiated] = useState(false)  // Step E: 用户主动发起
  const [userSide, setUserSide] = useState<string>('ADD')
  const [userTargetPct, setUserTargetPct] = useState('8')
  const plan_generated_ref = useRef(false)
  // loading 态：无内容且正在流式输出 — 根据 stage 事件动态显示进度
  if (msg.streaming && !msg.content) {
    const lastStage = (msg.stages ?? []).at(-1)
    const stageName = lastStage?.name ?? ''
    const isComplete = stageName === 'rules' || stageName === 'signals'
    const label = lastStage?.summary ?? '意图识别中...'
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <AiAvatar />
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#9CA3AF', fontSize: 14 }}>
          {isComplete
            ? <CheckCircle size={16} style={{ color: '#10B981' }} />
            : <Loader2 size={16} className="animate-spin" />}
          {label}
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
              <div className="decision-md">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    a: ({ href, children }) => (
                      <a href={href} target="_blank" rel="noopener noreferrer">
                        {children}
                      </a>
                    ),
                    // 兜底：LLM 没加 ### 前缀时，把 "一、X" 等段落识别为 H3
                    p: ({ children, node }: any) => {
                      const firstChild = node?.children?.[0]
                      const textContent = firstChild?.type === 'text' ? firstChild.value : null
                      if (textContent && /^[一二三四五六七八九十]、/.test(textContent)) {
                        return <h3>{children}</h3>
                      }
                      return <p>{children}</p>
                    },
                  }}
                >
                  {msg.streaming ? msg.content + '▊' : msg.content}
                </ReactMarkdown>
              </div>
            )}
          </div>
        )}

        {/* 候选标的点选按钮 */}
        {msg.candidates && msg.candidates.length > 0 && !msg.streaming && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
            {msg.candidates.map(c => (
              <button key={c.symbol || c.name} onClick={() => onSelectCandidate?.(c.name)}
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                  padding: '6px 14px', fontSize: 13, borderRadius: 20,
                  border: '1px solid #93C5FD', background: '#EFF6FF', color: '#1D4ED8',
                  cursor: 'pointer', transition: 'background 0.15s',
                }}
                onMouseEnter={e => (e.currentTarget.style.background = '#DBEAFE')}
                onMouseLeave={e => (e.currentTarget.style.background = '#EFF6FF')}>
                {c.name}
                <span style={{ fontSize: 11, color: '#60A5FA' }}>{c.metric_label}</span>
              </button>
            ))}
          </div>
        )}

        {/* v3.11 执行计划入口(AI 建议 — 主样式) */}
        {!msg.streaming && msg.content && msg.actionable && !showExecPlan && (
          <button
            onClick={() => { setUserInitiated(false); setShowExecPlan(true) }}
            style={{
              marginTop: 6, display: 'inline-flex', alignItems: 'center', gap: 6, alignSelf: 'flex-start',
              padding: '6px 14px', fontSize: 12, fontWeight: 600, borderRadius: 8,
              border: '1px solid #93C5FD', background: '#EFF6FF', color: '#1D4ED8',
              cursor: 'pointer', boxShadow: '0 0 0 2px rgba(59, 130, 246, 0.15)',
            }}
          >
            <Zap size={14} />
            {msg.actionable_hint || '生成执行计划'}
          </button>
        )}
        {/* Step E: 用户主动发起(观望/持有结论时 — 次要样式) */}
        {!msg.streaming && msg.content && !msg.actionable && msg.intent && !showExecPlan && (
          <button
            onClick={() => { setUserInitiated(true); setShowExecPlan(true) }}
            style={{
              marginTop: 6, display: 'inline-flex', alignItems: 'center', gap: 6, alignSelf: 'flex-start',
              padding: '5px 12px', fontSize: 11, fontWeight: 500, borderRadius: 6,
              border: '1px solid #E5E7EB', background: '#F9FAFB', color: '#6B7280',
              cursor: 'pointer',
            }}
            onMouseEnter={e => (e.currentTarget.style.background = '#F3F4F6')}
            onMouseLeave={e => (e.currentTarget.style.background = '#F9FAFB')}
          >
            主动制定执行计划
          </button>
        )}
        {showExecPlan && (() => {
          const intent = msg.intent as Record<string, unknown> | undefined
          const action = (intent?.action as string) || 'BUY'
          // 从 explainData.target_position 读标准 symbol (后端 _serialize_target_position 产出)
          const tp = (explainData?.data as Record<string, unknown>)?.target_position as Record<string, unknown> | undefined
          const symbol = (tp?.symbol as string) || ''
          const market = symbol.includes(':') ? symbol.split(':')[1] : 'US'
          const sideMap: Record<string, string> = {
            buy_init: 'BUY', buy_more: 'ADD', trim: 'REDUCE', exit: 'SELL',
            BUY: 'BUY', ADD: 'ADD', REDUCE: 'REDUCE', SELL: 'SELL',
          }
          if (!symbol) {
            return (
              <div style={{ background: '#FFF7ED', border: '1px solid #FED7AA', borderRadius: 10,
                padding: '10px 14px', marginTop: 8, fontSize: 12, color: '#9A3412' }}>
                无法获取标的代码(target_position.symbol 为空)。请先完成一次持仓决策分析。
                <button onClick={() => setShowExecPlan(false)} style={{
                  marginLeft: 8, background: 'none', border: 'none', color: '#9CA3AF', cursor: 'pointer', fontSize: 11,
                }}>关闭</button>
              </div>
            )
          }
          // Step E: 用户主动发起时显示方向+目标选择
          if (userInitiated && !plan_generated_ref.current) {
            return (
              <div style={{ background: '#F9FAFB', border: '1px solid #E5E7EB', borderRadius: 10,
                padding: '12px 16px', marginTop: 8 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: '#374151' }}>主动制定执行计划</span>
                  <button onClick={() => setShowExecPlan(false)} style={{ background: 'none', border: 'none', color: '#9CA3AF', cursor: 'pointer', fontSize: 11 }}>关闭</button>
                </div>
                <div style={{ fontSize: 11, color: '#D97706', background: '#FFF7ED', borderRadius: 4, padding: '4px 8px', marginBottom: 8 }}>
                  注：当前 AI 决策结论为观望/持有，本计划由你主动发起
                </div>
                <div style={{ display: 'flex', gap: 10, marginBottom: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                  <label style={{ fontSize: 12 }}>方向
                    <select value={userSide} onChange={e => setUserSide(e.target.value)} style={{ marginLeft: 4, padding: '3px 6px', fontSize: 12, borderRadius: 4, border: '1px solid #D1D5DB' }}>
                      <option value="ADD">加仓</option>
                      <option value="REDUCE">减仓</option>
                      <option value="BUY">买入</option>
                      <option value="SELL">卖出</option>
                    </select>
                  </label>
                  <label style={{ fontSize: 12 }}>目标仓位
                    <input type="text" value={userTargetPct} onChange={e => setUserTargetPct(e.target.value)}
                      style={{ marginLeft: 4, width: 40, padding: '3px 6px', fontSize: 12, borderRadius: 4, border: '1px solid #D1D5DB' }} /> %
                  </label>
                </div>
                <button onClick={() => { plan_generated_ref.current = true; setShowExecPlan(true) }}
                  style={{ padding: '5px 14px', fontSize: 12, fontWeight: 500, borderRadius: 6, border: '1px solid #E5E7EB', background: '#fff', color: '#374151', cursor: 'pointer' }}>
                  生成计划
                </button>
              </div>
            )
          }

          const effectiveSide = userInitiated ? userSide : (sideMap[action] || 'BUY')
          const effectiveTargetPct = userInitiated ? (parseFloat(userTargetPct) / 100 || 0.08) : undefined

          return (
            <ExecutionPlanPanel
              symbol={symbol}
              market={market}
              side={effectiveSide}
              userInitiated={userInitiated}
              defaultTargetPct={effectiveTargetPct}
              onClose={() => { setShowExecPlan(false); plan_generated_ref.current = false }}
              onConfirmPlan={(planResult) => {
                // 组装 ActionDraftResponse 格式，打开 modal
                const tranches = planResult.plan_summary_block?.tranches || []
                const strategies = tranches.map((t, i) => ({
                  symbol: planResult.plan_summary_block?.symbol || symbol,
                  side: sideMap[action] || 'BUY',
                  quantity: t.quantity,
                  quantity_pct: null,
                  order_type: 'LIMIT' as const,
                  trigger_price: t.trigger_price,
                  limit_price: t.limit_price,
                  parent_intent_index: null,
                  value_sources: null,
                  _trigger_type: t.trigger_type,
                }))
                const fakeDraft: ActionDraftResponse = {
                  id: planResult.plan_id || '',
                  conversation_id: '',
                  decision_summary: planResult.rationale || '',
                  payload: {
                    symbol_strategies: strategies as SymbolStrategyDraft[],
                    allocation_intents: [],
                    risk_notes: planResult.risk_notes ? [planResult.risk_notes] : [],
                    missing_fields: [],
                  },
                  status: 'draft',
                  created_at: null, updated_at: null, confirmed_at: null, discarded_at: null,
                }
                setPlanMetaForModal({
                  plan_id: planResult.plan_id,
                  factor_snapshot: planResult.factor_snapshot as Record<string,unknown>,
                  constraints_applied: planResult.constraints_applied as Record<string,unknown>,
                  rationale: planResult.rationale,
                })
                setCurrentDraft(fakeDraft)
                setDraftCardOpen(true)
                setShowExecPlan(false)
              }}
            />
          )
        })()}
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
  // decisionType 值（优先级更高，done 事件后覆盖）
  buy_init:   '建仓',
  buy_more:   '加仓',
  hold:       '持有',
  trim:       '减仓',
  exit:       '清仓',
  wait:       '观望',
  need_info:  '待确认',
  // intent actions（兜底，done 事件前显示）
  BUY:        '买入判断',
  ADD:        '加仓判断',
  SELL:       '卖出判断',
  REDUCE:     '减仓判断',
  HOLD:       '持有观察',
  ANALYZE:    '综合评估',
  TAKE_PROFIT:'止盈',
  STOP_LOSS:  '止损',
  // PortfolioReview conclusion_type
  rebalance_needed: '建议再平衡',
  healthy:          '维持现状',
  high_risk:        '建议降仓',
  low_defense:      '补充防御',
  portfolio_review: '组合评估',
  performance_analysis: '收益分析',
  // AssetAllocation allocation_type
  new_cash:         '新增配置',
  rebalance:        '再平衡',
  asset_allocation: '资产配置',
}
function displayAction(action: string): string {
  return ACTION_LABELS[action] ?? ACTION_LABELS[action.toUpperCase()] ?? action
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

// ── 来源标签颜色 ─────────────────────────────────────────────
type SourceTag = { label: string; isUser: boolean }

const SOURCE_PREFIX_MAP: Record<string, SourceTag> = {
  '投研观点':       { label: '投研观点',       isUser: true },
  'Alpha Vantage': { label: 'Alpha Vantage', isUser: false },
  'AKShare':       { label: 'AKShare',       isUser: false },
  'Perplexity':    { label: 'Perplexity',    isUser: false },
  'gpt-4o':        { label: 'gpt-4o',        isUser: false },
  '数据源':         { label: '数据源',         isUser: false },
  // 兼容旧格式
  '用户资料':       { label: '投研观点',       isUser: true },
  '第三方数据':     { label: '数据源',         isUser: false },
  '联网参考':       { label: '联网搜索',       isUser: false },
}

// ── 联网搜索条目解析 ─────────────────────────────────────────
function parseResearchItem(raw: string): { type: 'user' | 'web' | 'thirdparty' | 'other'; text: string; url: string | null; domain: string | null; sourceLabel: string; isUser: boolean } {
  let type: 'user' | 'web' | 'thirdparty' | 'other' = 'other'
  let text = raw
  let refUrl: string | null = null
  let sourceLabel = '数据源'
  let isUser = false

  // 解析 [XXX] 前缀 — 支持新旧格式
  const prefixMatch = text.match(/^\[([^\]]+)\]\s*/)
  if (prefixMatch) {
    const tag = prefixMatch[1]
    const mapped = SOURCE_PREFIX_MAP[tag]
    if (mapped) {
      sourceLabel = mapped.label
      isUser = mapped.isUser
      type = isUser ? 'user' : (tag === '联网参考' || tag === 'Perplexity' || tag === 'gpt-4o' ? 'web' : 'thirdparty')
    } else {
      sourceLabel = tag
      type = 'thirdparty'
    }
    text = text.slice(prefixMatch[0].length)
  }

  // 提取并过滤 [ref:url] 标记
  const refMatch = text.match(/^\[ref:(https?:\/\/[^\]]+)\]\s*/)
  if (refMatch) {
    refUrl = refMatch[1]
    text = text.slice(refMatch[0].length).trim()
  }

  // 过滤残留的日期标注 [2026-03] 等
  text = text.replace(/^\[\d{4}-\d{2}\]\s*/, '').trim()

  // 匹配末尾括号（中文全角或半角）内的完整 URL
  const urlMatch = text.match(/\s*[（(](https?:\/\/[^）)]+)[）)]\s*$/)
  if (urlMatch) {
    const fullUrl = urlMatch[1]
    const domainMatch = fullUrl.match(/^https?:\/\/([^/?#]+)/)
    const domain = domainMatch ? domainMatch[1].replace(/^www\./, '') : fullUrl
    text = text.slice(0, text.lastIndexOf(urlMatch[0])).trim()
    return { type, text, url: fullUrl, domain, sourceLabel, isUser }
  }

  // 使用 ref 标记中的 URL
  if (refUrl) {
    const domainMatch = refUrl.match(/^https?:\/\/([^/?#]+)/)
    const domain = domainMatch ? domainMatch[1].replace(/^www\./, '') : null
    return { type, text, url: refUrl, domain, sourceLabel, isUser }
  }

  // 兼容旧格式：末尾只有裸域名
  const domainMatch = text.match(/\s*\(([a-zA-Z0-9.-]+\.[a-zA-Z]{2,}[^)]*)\)\s*$/)
  const domain = domainMatch ? domainMatch[1] : null
  if (domainMatch) text = text.slice(0, text.lastIndexOf(domainMatch[0])).trim()
  return { type, text, url: domain ? `https://${domain}` : null, domain, sourceLabel, isUser }
}

// ── 公共区块标题（14px 600 #111827）───────────────────────────
function SectionLabel({ label }: { label: string }) {
  return <div style={{ fontSize: 14, fontWeight: 600, color: '#111827', marginBottom: 10 }}>{label}</div>
}

// ── 知识库引用区块（v3.6.1 新增）────────────────────────────────

const _CITE_SOURCE_LABELS: Record<string, string> = {
  investment_principles: '投资纪律',
  investment_style:      '投资理念',
  allocation_principles: '资产配置',
  research_views:        '投研观点',
}

const _CITE_FILE_NAMES: Record<string, string> = {
  handbook_official:       '投资纪律手册',
  handbook_custom:         '自定义纪律',
  investment_philosophy:   '投资理念',
  dynamic_rebalancing:     '动态再平衡原则',
  multi_asset_allocation:  '多元资产配置',
  target_range_management: '目标区间管理',
}

function _getFileDisplayName(path: string): string {
  for (const [key, name] of Object.entries(_CITE_FILE_NAMES)) {
    if (path.includes(key)) return name
  }
  if (path.includes('research_views/')) {
    const parts = path.split('/')
    const file = parts[parts.length - 1]?.replace('.md', '') || ''
    const asset = parts[parts.length - 2] || ''
    return `${asset} ${file}`
  }
  const base = path.split('/').pop()?.replace('.md', '') || path
  return base
}

type CitationItem = {
  sourceType: string
  label: string
  fileName: string
  date: string | null
  parentDocPath: string
}

function KnowledgeCitations({ data, viewpointCards = [], onFileClick }: {
  data: ExplainData
  viewpointCards?: string[]
  onFileClick: (path: string) => void
}) {
  const principles = data.data?.retrieved_principles || []
  const researchViews = data.data?.retrieved_research_views || []
  const allChunks = [...principles, ...researchViews]

  if (allChunks.length === 0 && viewpointCards.length === 0) return null

  // RAG chunks：按 parent_doc_path 去重
  const seen = new Set<string>()
  const items: CitationItem[] = []
  for (const c of allChunks) {
    if (seen.has(c.parent_doc_path)) continue
    seen.add(c.parent_doc_path)
    items.push({
      sourceType: c.source_type,
      label: _CITE_SOURCE_LABELS[c.source_type] || c.source_type,
      fileName: _getFileDisplayName(c.parent_doc_path),
      date: c.date,
      parentDocPath: c.parent_doc_path,
    })
  }

  // 用户投研观点卡片（从 ld.research 分流过来的 [投研观点] 条目）
  const vpItems = viewpointCards.map(raw => {
    const { text } = parseResearchItem(raw)
    // 截断到 80 字符
    const truncated = text.length > 80 ? text.slice(0, 79) + '…' : text
    return truncated
  })

  const [open, setOpen] = React.useState(false)

  return (
    <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 14px' }}>
      <CollapsibleHeader label="知识库引用" open={open} onToggle={() => setOpen(o => !o)} />
      {open && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {/* RAG 召回的知识文件（可点击预览） */}
          {items.map((item, i) => (
            <div
              key={`rag-${i}`}
              onClick={() => onFileClick(item.parentDocPath)}
              style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '6px 8px', borderRadius: 6,
                cursor: 'pointer', transition: 'background 0.15s',
              }}
              onMouseEnter={e => (e.currentTarget.style.background = '#F9FAFB')}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
            >
              <span style={{
                flexShrink: 0, fontSize: 11, fontWeight: 500,
                padding: '1px 6px', borderRadius: 4,
                background: '#F0F9FF', color: '#0369A1',
              }}>
                {item.label}
              </span>
              <span style={{ flex: 1, fontSize: 12, color: '#374151', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {item.fileName}
              </span>
              {item.date && (
                <span style={{ flexShrink: 0, fontSize: 11, color: '#9CA3AF' }}>
                  {item.date}
                </span>
              )}
            </div>
          ))}
          {/* 用户投研观点卡片（结构化数据，不可点击预览） */}
          {vpItems.map((text, i) => (
            <div
              key={`vp-${i}`}
              style={{
                display: 'flex', alignItems: 'flex-start', gap: 8,
                padding: '6px 8px', borderRadius: 6,
              }}
            >
              <span style={{
                flexShrink: 0, fontSize: 11, fontWeight: 500,
                padding: '1px 6px', borderRadius: 4,
                background: '#EFF6FF', color: '#3B82F6',
              }}>
                投研观点
              </span>
              <span style={{
                flex: 1, fontSize: 12, color: '#374151', lineHeight: 1.5,
                display: '-webkit-box', WebkitLineClamp: 2,
                WebkitBoxOrient: 'vertical', overflow: 'hidden',
              }}>
                {text}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── 知识库文件预览弹窗（v3.6.1 新增）────────────────────────

const _SENSITIVITY_LABELS: Record<string, string> = {
  permanent: '长期有效', slow_decay: '年度有效',
  medium_decay: '季度有效', fast_decay: '月度有效',
}

function KnowledgeFilePreview({ path, onClose }: { path: string; onClose: () => void }) {
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)
  const [fileData, setFileData] = React.useState<{ frontmatter: Record<string, unknown>; content: string } | null>(null)

  React.useEffect(() => {
    setLoading(true)
    setError(null)
    knowledgeApi.getFile(path)
      .then(data => { setFileData(data); setLoading(false) })
      .catch(e => { setError(String(e)); setLoading(false) })
  }, [path])

  const title = _getFileDisplayName(path)
  const fm = fileData?.frontmatter || {}
  const sensitivity = _SENSITIVITY_LABELS[fm.time_sensitivity as string] || ''

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.3)' }} onClick={onClose} />
      <div style={{
        position: 'relative', background: '#fff', borderRadius: 12,
        width: 'min(640px, 90vw)', maxHeight: '80vh', display: 'flex', flexDirection: 'column',
        boxShadow: '0 20px 60px rgba(0,0,0,0.15)',
      }}>
        {/* 标题栏 */}
        <div style={{
          padding: '14px 18px', borderBottom: '1px solid #E5E7EB',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <div>
            <div style={{ fontSize: 15, fontWeight: 600, color: '#111827' }}>{title}</div>
            <div style={{ fontSize: 12, color: '#9CA3AF', marginTop: 2, display: 'flex', gap: 8 }}>
              {fm.source && <span>来源: {String(fm.source)}</span>}
              {fm.date && <span>{String(fm.date)}</span>}
              {sensitivity && <span>{sensitivity}</span>}
            </div>
          </div>
          <button onClick={onClose} style={{
            background: 'none', border: 'none', cursor: 'pointer',
            fontSize: 18, color: '#9CA3AF', padding: 4,
          }}>✕</button>
        </div>
        {/* 正文 */}
        <div style={{ flex: 1, overflow: 'auto', padding: '16px 18px' }}>
          {loading && <div style={{ color: '#9CA3AF', fontSize: 13 }}>加载中...</div>}
          {error && <div style={{ color: '#EF4444', fontSize: 13 }}>加载失败: {error}</div>}
          {fileData && (
            <div className="decision-md" style={{ fontSize: 13, lineHeight: 1.7, color: '#374151' }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{fileData.content}</ReactMarkdown>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── 可折叠区块标题 ────────────────────────────────────────────
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
  const researchRaw  = data.data?.research || []
  const position  = data.data?.target_position

  // v3.6.1: 分流——[投研观点] 前缀归知识库引用，其余归联网搜索
  const webSearchItems = researchRaw.filter(r => !r.startsWith('[投研观点]') && !r.startsWith('[用户资料]'))
  const viewpointCards = researchRaw.filter(r => r.startsWith('[投研观点]') || r.startsWith('[用户资料]'))

  const [chainOpen,    setChainOpen]    = React.useState(false)
  const [researchOpen, setResearchOpen] = React.useState(false)
  const [previewPath,  setPreviewPath]  = React.useState<string | null>(null)

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

      {/* ── 1b. 持仓数据 / 资产分布（Education 不显示）── */}
      {intent?.primary_intent !== 'Education' && (() => {
        const isPortfolioReview = intent?.primary_intent === 'PortfolioReview' || intent?.intent_type === 'portfolio_review'
        const breakdown = data.data?.asset_breakdown as Record<string, unknown> | undefined
        const prResult = data.portfolioResult as Record<string, unknown> | undefined

        // PortfolioReview：显示资产分布
        if (isPortfolioReview && breakdown) {
          const cats = breakdown.categories as Record<string, { market_value: number; pnl: number; pct: number; count: number }>
          const top3 = (breakdown.top3_by_weight as { name: string; weight: number; pnl_pct: number }[]) || []
          return (
            <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 14px' }}>
              <SectionLabel label="资产分布" />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {data.data?.total_assets != null && (
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                    <span style={{ fontSize: 12, color: '#6B7280' }}>组合总市值</span>
                    <span style={{ fontSize: 13, fontWeight: 700, color: '#111827' }}>
                      ¥{(data.data.total_assets / 10000).toFixed(2)}万
                    </span>
                  </div>
                )}
                {cats && Object.entries(cats).map(([cat, info]) => (
                  <div key={cat} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontSize: 12, color: '#6B7280', minWidth: 36 }}>{cat}</span>
                    <span style={{ fontSize: 12, fontWeight: 600, color: '#111827', flex: 1, textAlign: 'right', marginRight: 8 }}>
                      {info.pct.toFixed(1)}%
                    </span>
                    <span style={{ fontSize: 11, color: info.pnl >= 0 ? '#EF4444' : '#10B981', minWidth: 60, textAlign: 'right' }}>
                      {info.pnl >= 0 ? '+' : ''}{(info.pnl / 10000).toFixed(2)}万
                    </span>
                  </div>
                ))}
                {top3.length > 0 && (
                  <>
                    <div style={{ height: 1, background: '#F3F4F6', margin: '4px 0' }} />
                    <div style={{ fontSize: 11, color: '#9CA3AF', marginBottom: 2 }}>持仓前三</div>
                    {top3.map((p, i) => (
                      <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span style={{ fontSize: 12, color: '#374151' }}>{p.name}</span>
                        <span style={{ fontSize: 12, fontWeight: 600, color: '#111827' }}>{p.weight}%</span>
                      </div>
                    ))}
                  </>
                )}
              </div>
            </div>
          )
        }

        // 非 PortfolioReview：原有持仓数据模块
        if (position || data.data?.total_assets) {
          return (
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
                        <span style={{ fontSize: 12, fontWeight: 600, color: position.profit_loss_rate >= 0 ? '#EF4444' : '#10B981' }}>
                          {position.profit_loss_rate >= 0 ? '+' : ''}{(position.profit_loss_rate * 100).toFixed(2)}%
                        </span>
                      </div>
                    )}
                    {position.platforms && position.platforms.length > 0 && (
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span style={{ fontSize: 12, color: '#6B7280' }}>平台</span>
                        <span style={{ fontSize: 12, fontWeight: 600, color: '#111827' }}>
                          {position.platforms.map((p: string) => p === '雪盈证券' ? '盈透证券' : p).join(' / ')}
                        </span>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          )
        }
        return null
      })()}

      {/* ── 2. 规则校验（Education 不显示）── */}
      {intent?.primary_intent !== 'Education' && rules && (
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

      {/* ── 3. 知识库引用 ── */}
      <KnowledgeCitations data={data} viewpointCards={viewpointCards} onFileClick={setPreviewPath} />

      {/* ── 3b. 联网搜索（默认折叠，PerformanceAnalysis 不显示）── */}
      {intent?.primary_intent !== 'PerformanceAnalysis' && webSearchItems.length > 0 && (
        <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 14px' }}>
          <CollapsibleHeader label="联网搜索" open={researchOpen} onToggle={() => setResearchOpen(o => !o)} />
          {researchOpen && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
              {webSearchItems.map((raw, i) => {
                const { text, url, domain, sourceLabel, isUser } = parseResearchItem(raw)
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
                      {sourceLabel}
                    </span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* ── 4. 四维信号（Education 不显示）── */}
      {intent?.primary_intent !== 'Education' && signals && (
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

      {/* ── 5. 分析过程（折叠，Education 不显示）── */}
      {intent?.primary_intent !== 'Education' && (stages ?? []).length > 0 && (
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

      {/* ── 5b. 组合评估结论（PortfolioReview 专用）── */}
      {data.portfolioResult && (() => {
        const pr = data.portfolioResult as Record<string, unknown>
        const PCLS: Record<string, string> = { healthy: '✅ 结构健康', rebalance_needed: '⚖️ 建议再平衡', high_risk: '⚠️ 风险偏高', low_defense: '🛡️ 防御不足' }
        const ct = pr.conclusion_type as string || ''
        const rl = pr.risk_level as string || ''
        const findings = (pr.key_findings as string[]) || []
        const concIssues = (pr.concentration_issues as string[]) || []
        const rebalSugs = (pr.rebalance_suggestions as string[]) || []
        return (
          <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 14px' }}>
            <SectionLabel label="组合评估结论" />
            <div style={{ fontSize: 14, fontWeight: 700, color: '#1B2A4A', marginBottom: 6 }}>
              {PCLS[ct] || ct}
            </div>
            {rl && <div style={{ fontSize: 12, color: '#6B7280', marginBottom: 8 }}>风险等级：<span style={{ fontWeight: 600, color: rl === '高' ? '#EF4444' : rl === '低' ? '#10B981' : '#F59E0B' }}>{rl}</span></div>}
            {findings.length > 0 && (
              <div style={{ marginBottom: 8 }}>
                <div style={{ fontSize: 11, color: '#9CA3AF', marginBottom: 4 }}>核心发现</div>
                {findings.map((f, i) => <div key={i} style={{ fontSize: 12, color: '#374151', lineHeight: 1.6, paddingLeft: 8, borderLeft: '2px solid #3B82F6', marginBottom: 3 }}>{f}</div>)}
              </div>
            )}
            {concIssues.length > 0 && (
              <div style={{ marginBottom: 8 }}>
                <div style={{ fontSize: 11, color: '#9CA3AF', marginBottom: 4 }}>集中度问题</div>
                {concIssues.map((c, i) => <div key={i} style={{ fontSize: 12, color: '#D97706', lineHeight: 1.6, paddingLeft: 8, borderLeft: '2px solid #F59E0B', marginBottom: 3 }}>{c}</div>)}
              </div>
            )}
            {rebalSugs.length > 0 && (
              <div>
                <div style={{ fontSize: 11, color: '#9CA3AF', marginBottom: 4 }}>调仓建议</div>
                {rebalSugs.map((s, i) => <div key={i} style={{ fontSize: 12, color: '#059669', lineHeight: 1.6, paddingLeft: 8, borderLeft: '2px solid #10B981', marginBottom: 3 }}>{s}</div>)}
              </div>
            )}
          </div>
        )
      })()}

      {/* ── 5b. 收益诊断（PerformanceAnalysis 专用）── */}
      {(() => {
        const pr = (data as Record<string, unknown>).performanceResult as Record<string, unknown> | undefined
        if (!pr) return null
        const DIAG: Record<string, string> = {
          concentration: '📊 集中度过高', asset_mix: '⚖️ 资产配比问题',
          stock_selection: '🎯 个股分化明显', healthy: '✅ 收益结构健康',
          low_defense: '🛡️ 防御资产不足',
        }
        return (
          <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 14px' }}>
            <SectionLabel label="收益诊断" />
            <div style={{ fontSize: 14, fontWeight: 700, color: '#1B2A4A', marginBottom: 6 }}>
              {DIAG[pr.diagnosis_type as string] ?? '综合分析'}
            </div>
            {pr.structural_issue && (
              <div style={{ fontSize: 12, color: '#374151', lineHeight: 1.6 }}>{pr.structural_issue as string}</div>
            )}
          </div>
        )
      })()}

      {/* ── 5c. 分配方案（AssetAllocation 专用）── */}
      {(() => {
        const ar = (data as Record<string, unknown>).allocationResult as Record<string, unknown> | undefined
        if (!ar) return null
        const plan = ar.allocation_plan as Array<Record<string, string>> | undefined
        if (!plan || plan.length === 0) return null
        const DIR_COLOR: Record<string, string> = { '增加': '#059669', '减少': '#EF4444', '维持': '#6B7280' }
        return (
          <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 14px' }}>
            <SectionLabel label="分配方案" />
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {plan.map((item, i) => (
                <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: 12, color: '#374151' }}>{item.asset_class}</span>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <span style={{ fontSize: 11, fontWeight: 500, color: DIR_COLOR[item.direction] ?? '#6B7280', padding: '1px 6px', borderRadius: 4, background: `${DIR_COLOR[item.direction] ?? '#6B7280'}15` }}>{item.direction}</span>
                    <span style={{ fontSize: 12, fontWeight: 600, color: '#111827', minWidth: 50, textAlign: 'right' }}>{item.suggested_pct}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )
      })()}

      {/* ── 6. 最终结论完整版（Education 不显示）── */}
      {intent?.primary_intent !== 'Education' && (conclusion || data.llm) && !data.portfolioResult && (
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
      {!conclusion && !data.llm && !rules && !signals && !researchRaw?.length && (stages ?? []).length === 0 && (
        <div style={{ fontSize: 11, color: '#C4C9D4', lineHeight: 1.7, paddingTop: 4 }}>
          分析链路详情待后端接入 stage 事件后展示。
        </div>
      )}

      {/* ── 知识库文件预览弹窗 ── */}
      {previewPath && (
        <KnowledgeFilePreview path={previewPath} onClose={() => setPreviewPath(null)} />
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

      {/* 免责声明 */}
      <div style={{ fontSize: 11, color: '#9CA3AF', lineHeight: 1.6, borderTop: '1px solid #F3F4F6', paddingTop: 8, marginTop: 2 }}>
        本系统输出仅供参考，不构成投资建议。
      </div>
    </div>
  )
}

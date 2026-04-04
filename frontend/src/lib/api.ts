/**
 * WealthPilot API 调用封装
 * 所有 fetch 请求统一走 /api 前缀，由 Vite proxy 转发到 http://localhost:8000
 */

const BASE = '/api'

// ── 通用 fetch 包装 ──────────────────────────────────────

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

// ── Portfolio ────────────────────────────────────────────

export const portfolioApi = {
  getSummary: () => request<PortfolioSummary>('/portfolio/summary'),
  getPositions: (segment?: string) =>
    request<PagedResult<Position>>(
      `/portfolio/positions${segment ? `?segment=${encodeURIComponent(segment)}` : ''}`
    ),
  getLiabilities: () => request<{ items: Liability[]; total: number }>('/portfolio/liabilities'),
  getAlerts: () => request<{ items: Alert[]; count: number }>('/portfolio/alerts'),
  importCsv: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<ImportResult>('/portfolio/import/csv', {
      method: 'POST',
      headers: {},  // let browser set Content-Type with boundary
      body: fd,
    })
  },
  importBrokerCsv: (file: File, broker: string) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<{ imported: number; rate: number; errors: string[] }>(
      `/portfolio/import/broker-csv?broker=${encodeURIComponent(broker)}`,
      { method: 'POST', headers: {}, body: fd }
    )
  },
  importScreenshot: (file: File, platform: string) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<ImportResult>(
      `/portfolio/import/screenshot?platform=${encodeURIComponent(platform)}`,
      { method: 'POST', headers: {}, body: fd }
    )
  },
  importLiabilitiesCsv: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<ImportResult>('/portfolio/liabilities/import/csv', {
      method: 'POST',
      headers: {},
      body: fd,
    })
  },
  deletePositions: () => request<{ message: string }>('/portfolio/positions', { method: 'DELETE' }),
}

// ── Discipline ───────────────────────────────────────────

export const disciplineApi = {
  getRules: () => request<Record<string, unknown>>('/discipline/rules'),
  updateRules: (rules: Record<string, unknown>) =>
    request<Record<string, unknown>>('/discipline/rules', { method: 'PUT', body: JSON.stringify({ rules }) }),
  resetRules: () => request<Record<string, unknown>>('/discipline/rules', { method: 'DELETE' }),
  getHandbook: () => request<{ source: string; content: string }>('/discipline/handbook'),
  uploadHandbook: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<{ source: string; content: string }>('/discipline/handbook', {
      method: 'POST', headers: {}, body: fd,
    })
  },
  resetHandbook: () =>
    request<{ source: string; content: string }>('/discipline/handbook', { method: 'DELETE' }),
  evaluate: (text: string) =>
    request<EvaluateResult>('/discipline/evaluate', {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
}

// ── Research ─────────────────────────────────────────────

export const researchApi = {
  getViewpoints: (q?: string) =>
    request<PagedResult<Viewpoint>>(
      `/research/viewpoints${q ? `?q=${encodeURIComponent(q)}` : ''}`
    ),
  createViewpoint: (data: ViewpointCreate) =>
    request<Viewpoint>('/research/viewpoints', { method: 'POST', body: JSON.stringify(data) }),
  updateViewpoint: (id: number, data: Partial<ViewpointCreate>) =>
    request<Viewpoint>(`/research/viewpoints/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteViewpoint: (id: number) =>
    request<void>(`/research/viewpoints/${id}`, { method: 'DELETE' }),
  getCards: () => request<PagedResult<ResearchCard>>('/research/cards'),
  getDocuments: () => request<PagedResult<ResearchDocument>>('/research/documents'),
  deleteDocument: (id: number) => request<void>(`/research/documents/${id}`, { method: 'DELETE' }),
  reparseDocument: (id: number) =>
    request<ParseResult>(`/research/documents/${id}/reparse`, { method: 'POST' }),
  parseText: (content: string, title?: string, source_url?: string) =>
    request<ParseResult>('/research/parse/text', {
      method: 'POST',
      body: JSON.stringify({ content, title: title ?? '', source_url }),
    }),
  parseUrl: (url: string) =>
    request<ParseResult>('/research/parse/url', {
      method: 'POST',
      body: JSON.stringify({ url }),
    }),
  parsePdf: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<ParseResult>('/research/parse/pdf', {
      method: 'POST',
      headers: {},
      body: fd,
    })
  },
  approveCard: (id: number, overrides?: Record<string, unknown>) =>
    request<Viewpoint>(`/research/cards/${id}/approve`, {
      method: 'POST',
      body: JSON.stringify({ overrides: overrides ?? {} }),
    }),
}

// ── Decision SSE ─────────────────────────────────────────

/**
 * 消费投资决策 SSE 流
 * 使用 fetch + ReadableStream，不依赖 EventSource
 */
export async function* streamDecisionChat(
  message: string,
  sessionId: string,
  signal?: AbortSignal
): AsyncGenerator<SSEEvent> {
  const res = await fetch(`${BASE}/decision/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ message, session_id: sessionId }),
    signal,
  })
  if (!res.ok || !res.body) {
    throw new Error(`HTTP ${res.status}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // SSE 事件以 \n\n 分隔
    const events = buffer.split('\n\n')
    buffer = events.pop() ?? ''  // 最后一段可能不完整，留待下次

    for (const raw of events) {
      if (!raw.trim()) continue
      const event = parseSSEEvent(raw)
      if (event) yield event
    }
  }
}

function parseSSEEvent(raw: string): SSEEvent | null {
  let eventType = 'message'
  let dataStr = ''
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) eventType = line.slice(6).trim()
    else if (line.startsWith('data:')) dataStr = line.slice(5).trim()
  }
  if (!dataStr) return null
  try {
    return { type: eventType, data: JSON.parse(dataStr) } as SSEEvent
  } catch {
    return null
  }
}

export const decisionApi = {
  getExplain: (decisionId: string, sessionId: string) =>
    request<ExplainData>(`/decision/explain/${decisionId}?session_id=${sessionId}`),
  clearSession: (sessionId: string) =>
    request<{ message: string }>(`/decision/session/${sessionId}`, { method: 'DELETE' }),
}

// ── 类型定义 ─────────────────────────────────────────────

export interface PortfolioSummary {
  total_assets: number
  total_liabilities: number
  net_worth: number
  leverage_ratio: number
  total_profit_loss: number
  allocation: Record<string, { value: number; pct: number }>
  platform_distribution: Record<string, number>
  concentration: Record<string, number>
}

export interface Position {
  id: number
  name: string
  ticker?: string
  platform: string
  asset_class: string
  currency?: string
  quantity?: number
  cost_price?: number
  current_price?: number
  market_value_cny: number
  original_currency?: string   // USD / HKD / CNY
  original_value?: number      // 原始货币金额
  fx_rate_to_cny?: number
  profit_loss_value?: number   // 盈亏金额（人民币）
  profit_loss_rate?: number    // 盈亏百分比
  segment?: string
}

export interface Liability {
  id: number
  name: string
  category?: string        // 融资 / 房贷 / 信用贷 等
  purpose?: string         // 投资杠杆 / 生活 等
  amount: number           // 金额（元）
  interest_rate?: number   // 年利率（小数，如 0.05 = 5%）
}

export interface Alert {
  alert_type: string
  severity: string       // 'warning' | 'danger' | 'info'
  title: string
  description: string
  current_value?: number
  target_value?: number
  deviation?: number
}

export interface ImportResult {
  imported: number
  errors: string[]
}

export interface EvaluateResult {
  parsed_intent: {
    asset?: string
    action?: string
    amount_cny?: number
    amount_pct?: number
    confidence?: number
    unresolved?: string[]
  }
  evaluation: {
    blocked: boolean
    block_reason?: string
    block_reasons?: string[]
    final_verdict: string
    risk_status: string
    risk_warnings?: string[]
    risk_messages: string[]
    psychology_status: string
    psychology_warnings?: string[]
    decision_recommendation?: string
    decision_reasons?: string[]
    decision_warnings?: string[]
  }
}

export interface PagedResult<T> {
  items: T[]
  total: number
}

export interface Viewpoint {
  id: number
  title: string
  object_type?: string
  object_name?: string
  stance?: string
  thesis?: string
  horizon?: string
  user_approval_level?: string
  validity_status?: string
  created_at?: string
}

export interface ViewpointCreate {
  title: string
  object_type?: string
  object_name?: string
  market_name?: string
  topic_tags?: string[]
  thesis?: string
  supporting_points?: string[]
  opposing_points?: string[]
  key_metrics?: string[]
  risks?: string[]
  action_suggestion?: string
  invalidation_conditions?: string
  horizon?: string
  stance?: string
  user_approval_level?: string
  validity_status?: string
}

export interface ResearchDocument {
  id: number
  title: string
  source_type?: string
  source_url?: string
  object_name?: string
  market_name?: string
  author?: string
  publish_time?: string
  tags?: string[]
  parse_status?: string
  notes?: string
  uploaded_at?: string
}

export interface ResearchCard {
  id: number
  document_id?: number
  summary?: string
  thesis?: string
  bull_case?: string
  bear_case?: string
  key_drivers?: string[]
  risks?: string[]
  key_metrics?: string[]
  horizon?: string
  stance?: string
  action_suggestion?: string
  invalidation_conditions?: string
  suggested_tags?: string[]
  is_approved?: boolean
  viewpoint_id?: number | null
  created_at?: string
  // populated when include_doc=True (from list_cards)
  document_title?: string
  document_object_name?: string
}

export interface ParseResult {
  document_id: number
  document_title: string
  card: ResearchCard
}

export interface SSEEvent {
  type: 'intent' | 'stage' | 'text' | 'done' | 'error'
  data: Record<string, unknown>
}

// ── Profile ──────────────────────────────────────────────

export interface UserProfile {
  id?:                    number
  version?:               number
  created_at?:            string
  updated_at?:            string
  // 风险画像
  risk_source?:           string   // "external" | "ai"
  risk_provider?:         string
  risk_original_level?:   string
  risk_normalized_level?: number   // 1-5
  risk_type?:             string   // "保守型"|"稳健型"|"平衡型"|"成长型"|"进取型"
  risk_assessed_at?:      string   // ISO datetime
  // 基础信息
  income_level?:          string
  income_stability?:      string
  total_assets?:          string
  investable_ratio?:      string
  liability_level?:       string
  family_status?:         string
  asset_structure?:       string
  investment_motivation?: string
  fund_usage_timeline?:   string
  // 投资目标
  goal_type?:             string[]
  target_return?:         string
  max_drawdown?:          string
  investment_horizon?:    string
  // AI 结果
  ai_summary?:            string
  ai_style?:              string   // "稳健"|"平衡"|"进取"
  ai_confidence?:         string   // "high"|"medium"|"low"
}

export interface ConflictItem {
  type:    string
  message: string
  options: string[]
}

export interface ExtractResult {
  extracted:      Partial<UserProfile>
  missing_fields: string[]
  next_question:  string | null
  error?:         string
}

export const profileApi = {
  get: () =>
    request<UserProfile>('/profile'),

  save: (data: Partial<UserProfile>) =>
    request<UserProfile>('/profile', { method: 'PUT', body: JSON.stringify(data) }),

  extract: (text: string, existing_fields: Partial<UserProfile> = {}) =>
    request<ExtractResult>('/profile/extract', {
      method: 'POST',
      body: JSON.stringify({ text, existing_fields }),
    }),

  generate: () =>
    request<{ summary: string; style: string; confidence: string }>('/profile/generate', {
      method: 'POST',
    }),

  checkConflicts: (max_drawdown: string, target_return: string, fund_usage_timeline: string) =>
    request<{ conflicts: ConflictItem[] }>('/profile/conflicts', {
      method: 'POST',
      body: JSON.stringify({ max_drawdown, target_return, fund_usage_timeline }),
    }),

  isRiskExpired: () =>
    request<{ expired: boolean }>('/profile/risk-expired'),
}

export interface ExplainData {
  decision_id: string
  intent?: {
    asset?: string
    action?: string          // getExplain: 中文值如"加仓判断"；SSE fallback: 英文如"ADD"
    primary_intent?: string  // 仅 SSE fallback 有，如 "PositionDecision"
    time_context?: string    // getExplain: time_horizon 重命名后的字段
    confidence?: number
    intent_type?: string
    needs_clarification?: boolean
  }
  stages?: Array<{ name: string; status: string; summary: string; detail?: string }>
  conclusion?: { verdict: string; summary: string }
  // getExplain 完整返回字段
  data?: {
    asset_name?: string
    has_data_errors?: boolean
    research?: string[]
    total_assets?: number
    target_position?: {
      name: string
      weight: number
      market_value_cny: number
      profit_loss_rate?: number
      platforms?: string[]
    }
  }
  rules?: {
    passed: boolean
    current_weight: number
    max_position: number
    violation: boolean
    warning?: string
    rule_details: string[]
  }
  signals?: {
    position: string
    event: { uncertainty: string; direction: string }
    fundamental: string
    sentiment: string
  }
  llm?: {
    decision: string
    decision_cn: string
    decision_emoji: string
    reasoning: string[]
    risk: string[]
    strategy: string[]
    is_fallback: boolean
  }
}

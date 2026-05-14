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
  // 204 No Content：返回 null
  if (res.status === 204) {
    return null as unknown as T
  }
  // 非 204 但 body 可能为空
  const text = await res.text()
  if (!text) {
    return null as unknown as T
  }
  try {
    return JSON.parse(text) as T
  } catch (err) {
    console.error('Failed to parse JSON response:', text.slice(0, 200))
    throw new Error(`Invalid JSON response: ${err}`)
  }
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
  updateDocument: (id: number, updates: Record<string, unknown>) =>
    request<ResearchDocument>(`/research/documents/${id}`, { method: 'PATCH', body: JSON.stringify(updates) }),
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

// ── Research v2 Types ────────────────────────────────────

export interface SourceRefV2 {
  ref_type: string
  ref_value: string
  title?: string | null
}

export interface ExtractedKPIV2 {
  current_price?: number | null
  target_price?: number | null
  revenue_yoy?: number | null
  earnings_yoy?: number | null
  gross_margin?: number | null
  net_margin?: number | null
  eps_surprise_pct?: number | null
  deliveries_latest?: number | null
  deliveries_yoy?: number | null
  analyst_target_upside?: number | null
  market_cap?: number | null
  pe_ttm?: number | null
  forward_pe?: number | null
  notes?: string | null
}

export interface FactsLayerV2 {
  affected_symbols: string[]
  primary_symbol: string | null
  primary_entity_id: string | null
  source_type: string
  source_refs: SourceRefV2[]
  as_of: string
  ingested_at: string
  raw_facts: Record<string, unknown>
  sentiment_raw?: Record<string, unknown> | null
}

export interface NarrativeLayerV2 {
  thesis: string | null
  bull_case: string | null
  bear_case: string | null
  narrative_summary: string | null
  event_type: string
  topics: string[]
  extracted_kpi: ExtractedKPIV2 | null
}

export interface DecisionSignalV2 {
  direction: number
  strength: number
  confidence_score: number
}

export interface JudgmentLayerV2 {
  is_ai_prefilled: boolean
  user_endorsement: string
  stance: string
  horizon: string
  confidence: string
  decision_signal: DecisionSignalV2
  action_type: string
  trigger_conditions: string | null
  invalidation_conditions: string | null
  key_metrics_to_watch: string[]
  validity_status: string
  expires_at: string | null
}

export interface ViewpointCardV2 {
  card_id: string
  facts: FactsLayerV2
  narrative: NarrativeLayerV2
  judgment: JudgmentLayerV2
  relations: unknown[]
  time_sensitivity?: string | null  // v3.6.3
  status: string
  created_at: string
  updated_at: string
}

// ── Research v2 API ──────────────────────────────────────

export const researchV2Api = {
  ingestUpload: (title: string, content: string, source_url?: string) =>
    request<{ card_id: string; card: ViewpointCardV2 }>('/research/v2/ingest/upload', {
      method: 'POST',
      body: JSON.stringify({ title, content, source_url }),
    }),

  ingestAlphaVantage: (symbol: string) =>
    request<{ cards: ViewpointCardV2[]; errors?: unknown[] }>('/research/v2/ingest/alpha_vantage', {
      method: 'POST',
      body: JSON.stringify({ symbol }),
    }),

  ingestAkshare: (symbol: string) =>
    request<{ cards: ViewpointCardV2[]; errors?: unknown[] }>('/research/v2/ingest/akshare', {
      method: 'POST',
      body: JSON.stringify({ symbol }),
    }),

  getHoldingsUS: () =>
    request<{ symbol: string | null; asset_name: string; market: string | null; supported: boolean; weight: number; entity_id?: string | null; sibling_symbols?: string[] }[]>('/research/v2/holdings_us'),

  updateJudgment: (cardId: string, judgment: Record<string, unknown>, confirm: boolean, action?: string) =>
    request<{ card: ViewpointCardV2 }>(`/research/v2/cards/${cardId}/judgment`, {
      method: 'POST',
      body: JSON.stringify({ judgment, confirm, action }),
    }),

  queryCards: (params?: { symbol?: string; status?: string; event_type?: string; render?: boolean; top_k?: number }) => {
    const qs = new URLSearchParams()
    if (params?.symbol) qs.set('symbol', params.symbol)
    if (params?.status) qs.set('status', params.status)
    if (params?.event_type) qs.set('event_type', params.event_type)
    if (params?.render) qs.set('render', 'true')
    if (params?.top_k) qs.set('top_k', String(params.top_k))
    const q = qs.toString()
    return request<{ cards?: ViewpointCardV2[]; rendered?: string[]; count: number }>(
      `/research/v2/cards${q ? `?${q}` : ''}`
    )
  },
}

// ── Decision SSE ─────────────────────────────────────────

/**
 * 消费投资决策 SSE 流
 * 使用 fetch + ReadableStream，不依赖 EventSource
 */
export async function* streamDecisionChat(
  message: string,
  conversationId: string,
  signal?: AbortSignal
): AsyncGenerator<SSEEvent> {
  const res = await fetch(`${BASE}/decision/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ message, conversation_id: conversationId }),
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
  getExplain: (decisionId: string, conversationId: string) =>
    request<ExplainData>(`/decision/explain/${decisionId}?conversation_id=${conversationId}`),
  clearSession: (conversationId: string) =>
    request<{ message: string }>(`/decision/conversation/${conversationId}`, { method: 'DELETE' }),
}

// v3.6.1: 知识库 API
export const knowledgeApi = {
  getFile: (path: string) =>
    request<{ path: string; frontmatter: Record<string, unknown>; content: string }>(
      `/knowledge/file?path=${encodeURIComponent(path)}`,
    ),
}

// ── Conversations API ────────────────────────────────────

export interface Conversation {
  id: string
  title: string | null
  portfolio_id: number | null
  status: string
  created_at: string
  updated_at: string
}

export interface ConversationMessageDTO {
  id: number
  role: string
  content: string
  intent: string | null
  asset: string | null
  created_at: string
}

export const conversationsApi = {
  list: () =>
    request<Conversation[]>('/conversations'),
  create: (portfolioId?: number) =>
    request<Conversation>('/conversations', {
      method: 'POST',
      body: JSON.stringify({ portfolio_id: portfolioId ?? null }),
    }),
  rename: (id: string, title: string) =>
    request<Conversation>(`/conversations/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    }),
  remove: (id: string) =>
    request<{ message: string }>(`/conversations/${id}`, { method: 'DELETE' }),
  getMessages: (id: string) =>
    request<ConversationMessageDTO[]>(`/conversations/${id}/messages`),
}

// ── 投资行动 API ──────────────────────────────────────────

export interface SymbolStrategyDraft {
  symbol: string
  side: string
  quantity: number | null
  quantity_pct: number | null
  order_type: string
  trigger_price: number | null
  limit_price: number | null
  parent_intent_index: number | null
  value_sources: Record<string, string> | null  // v0.6: 推算依据
}

export interface MissingField {
  target_type: string     // "symbol_strategy" / "allocation_intent"
  target_index: number
  field: string           // 字段名
  description: string     // 给用户看的文案
}

export interface AllocationIntentDraft {
  title: string
  target_allocation: Record<string, number>
}

export interface ActionDraftResponse {
  id: string
  conversation_id: string
  decision_summary: string
  payload: {
    symbol_strategies: SymbolStrategyDraft[]
    allocation_intents: AllocationIntentDraft[]
    risk_notes: string[]
    missing_fields: MissingField[]
  } | null
  status: string
  created_at: string | null
  updated_at: string | null
  confirmed_at: string | null
  discarded_at: string | null
  risk_notes?: string[]
  missing_fields?: MissingField[]
}

export interface RiskCheckResponse {
  passed: boolean
  requires_confirmation: boolean
  warnings: Array<{
    rule: string
    severity: string
    message: string
    detail: Record<string, unknown>
  }>
  portfolio_total_value: number
  confirmation_text_required: string | null
}

export const actionApi = {
  generateDraft: (params: {
    conversation_id: string
    conversation_context: Array<{ role: string; content: string }>
    expressing_output: Record<string, unknown>
  }) =>
    request<ActionDraftResponse>('/action/drafts/generate', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  getDraft: (draftId: string) =>
    request<ActionDraftResponse>(`/action/drafts/${draftId}`),

  updateDraft: (draftId: string, payload: Record<string, unknown>) =>
    request<ActionDraftResponse>(`/action/drafts/${draftId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  confirmDraft: (draftId: string) =>
    request<{ draft_id: string; status: string; created_entities: number }>(
      `/action/drafts/${draftId}/confirm`,
      { method: 'POST' },
    ),

  discardDraft: (draftId: string) =>
    request<ActionDraftResponse>(`/action/drafts/${draftId}`, {
      method: 'DELETE',
    }),

  listDrafts: (status?: string) =>
    request<{ items: ActionDraftResponse[] }>(
      `/action/drafts${status ? `?status=${status}` : ''}`,
    ),

  listIntents: (status?: string) =>
    request<{ items: AllocationIntentResponse[] }>(
      `/action/intents${status ? `?status=${status}` : ''}`,
    ),

  discardIntent: (intentId: string) =>
    request<AllocationIntentResponse>(`/action/intents/${intentId}/discard`, {
      method: 'POST',
    }),

  updateIntent: (intentId: string, updates: Record<string, unknown>) =>
    request<AllocationIntentResponse>(`/action/intents/${intentId}`, {
      method: 'PATCH',
      body: JSON.stringify(updates),
    }),

  listStrategies: (params?: { status?: string; parent_intent_id?: string }) => {
    const qs = new URLSearchParams()
    if (params?.status) qs.set('status', params.status)
    if (params?.parent_intent_id) qs.set('parent_intent_id', params.parent_intent_id)
    const q = qs.toString()
    return request<{ items: SymbolStrategyResponse[] }>(`/action/strategies${q ? `?${q}` : ''}`)
  },

  listOrders: (params?: { strategy_id?: string; status?: string }) => {
    const qs = new URLSearchParams()
    if (params?.strategy_id) qs.set('strategy_id', params.strategy_id)
    if (params?.status) qs.set('status', params.status)
    const q = qs.toString()
    return request<{ items: OrderResponse[] }>(`/action/orders${q ? `?${q}` : ''}`)
  },

  cancelOrder: (orderId: string) =>
    request<OrderResponse>(`/action/orders/${orderId}/cancel`, { method: 'POST' }),

  pauseStrategy: (strategyId: string) =>
    request<SymbolStrategyResponse>(`/action/strategies/${strategyId}/pause`, { method: 'POST' }),

  resumeStrategy: (strategyId: string) =>
    request<SymbolStrategyResponse>(`/action/strategies/${strategyId}/resume`, { method: 'POST' }),

  discardStrategy: (strategyId: string) =>
    request<SymbolStrategyResponse>(`/action/strategies/${strategyId}/discard`, { method: 'POST' }),

  getTimeline: (limit: number = 50) =>
    request<{ items: TimelineEvent[]; total: number }>(`/action/timeline?limit=${limit}`),

  checkRisk: (strategyId: string, params: { quantity: number; limit_price: number }) =>
    request<RiskCheckResponse>(`/action/strategies/${strategyId}/check_risk`, {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  placeOrder: (strategyId: string, params: { quantity: number; limit_price: number; confirmation_text: string }) =>
    request<OrderResponse>(`/action/strategies/${strategyId}/place_order`, {
      method: 'POST',
      body: JSON.stringify(params),
    }),
}

export interface TimelineEvent {
  id: number
  event_type: string
  timestamp: string | null
  payload: Record<string, unknown>
  trace: {
    order?: { id: string; symbol: string; side: string; quantity: number; status: string; limit_price: number | null }
    strategy?: { id: string; symbol: string; side: string; target_quantity: number | null; limit_price: number | null; related_conversation_id: string | null }
    draft?: { id: string; decision_summary: string; conversation_id: string | null }
  }
}

export interface AllocationIntentResponse {
  id: string
  source_draft_id: string | null
  title: string
  target_allocation: Record<string, number> | null
  status: string
  related_conversation_id: string | null
  decision_basis: string | null
  related_strategies_count: number
  created_at: string | null
  updated_at: string | null
  completed_at: string | null
}

export interface SymbolStrategyResponse {
  id: string
  source_draft_id: string | null
  parent_intent_id: string | null
  symbol: string
  side: string
  target_quantity: number | null
  target_quantity_pct: number | null
  cumulative_filled_quantity: number
  order_type: string
  trigger_price: number | null
  limit_price: number | null
  status: string
  decision_basis: string | null
  is_held: boolean
  created_at: string | null
  updated_at: string | null
}

export interface OrderResponse {
  id: string
  strategy_id: string
  broker_name: string
  broker_order_id: string | null
  symbol: string
  side: string
  quantity: number
  filled_quantity: number
  order_type: string
  limit_price: number | null
  avg_filled_price: number | null
  status: string
  created_at: string | null
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
  extracted:      Record<string, unknown>
  missing_fields: string[]
  next_question:  string | null
  error?:         string
}

export const profileApi = {
  get: () =>
    request<UserProfile>('/profile'),

  save: (data: Partial<UserProfile>) =>
    request<UserProfile>('/profile', { method: 'PUT', body: JSON.stringify(data) }),

  extract: (payload: { type: 'text'; text: string; existing_fields?: Record<string, unknown> } | { type: 'images'; images: string[]; existing_fields?: Record<string, unknown> }) =>
    request<ExtractResult>('/profile/extract', {
      method: 'POST',
      body: JSON.stringify(payload),
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
    // v3.6.1: 知识库引用
    retrieved_principles?: Array<{
      content: string
      source_type: string
      source_channel: string
      parent_doc_path: string
      date: string | null
      semantic_score: number
    }>
    retrieved_research_views?: Array<{
      content: string
      source_type: string
      source_channel: string
      parent_doc_path: string
      date: string | null
      semantic_score: number
    }>
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
  generic_llm?: {
    chat_answer: string
    is_fallback: boolean
    error?: string
  }
  portfolioResult?: {
    risk_level?: string
    key_findings?: string[]
    concentration_issues?: string[]
    rebalance_needed?: boolean
    rebalance_suggestions?: string[]
    conclusion_type?: string
  }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  [key: string]: any
}

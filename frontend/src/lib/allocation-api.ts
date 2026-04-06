/**
 * 资产配置模块 — API 客户端 + TypeScript 类型定义
 */

const BASE = '/api'

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

// ── 枚举 & 类型 ─────────────────────────────────────────

export type AllocAssetClass = 'cash' | 'fixed' | 'equity' | 'alt' | 'deriv' | 'unclassified'

export type DeviationLevel = 'normal' | 'mild' | 'significant' | 'alert'
export type CashStatusType = 'sufficient' | 'low' | 'insufficient'
export type OverallStatusType = 'on_target' | 'mild_deviation' | 'significant_deviation' | 'alert'
export type PriorityActionType = 'no_action' | 'correct_with_inflow' | 'urgent_attention'

export const ALLOC_LABEL: Record<string, string> = {
  cash: '货币',
  fixed: '固收',
  equity: '权益',
  alt: '另类',
  deriv: '衍生',
  unclassified: '未分类',
}

export const STATUS_TEXT: Record<OverallStatusType, string> = {
  on_target: '接近目标',
  mild_deviation: '轻微偏离',
  significant_deviation: '明显偏离',
  alert: '需要关注',
}

export const ACTION_TEXT: Record<PriorityActionType, string> = {
  no_action: '暂不处理',
  correct_with_inflow: '后续用新增资金自然修正',
  urgent_attention: '需要尽快关注',
}

export const STATUS_COLOR: Record<OverallStatusType, string> = {
  on_target: '#22C55E',
  mild_deviation: '#F59E0B',
  significant_deviation: '#F97316',
  alert: '#EF4444',
}

export const BAR_STATUS_COLOR = {
  above_ceiling: '#F97316',   // 橙色：超配
  below_floor: '#3B82F6',     // 蓝色：低配
  in_range: '#22C55E',        // 绿色：在区间内
  alert: '#EF4444',           // 红色：预警
}

// ── 数据模型 ─────────────────────────────────────────────

export interface ClassAllocation {
  amount: number
  ratio: number
}

export interface AllocationSnapshot {
  snapshot_at: string
  total_investable_assets: number
  by_class: Record<string, ClassAllocation>
  unclassified_amount: number
  has_unclassified: boolean
}

export interface AssetTarget {
  asset_class: AllocAssetClass
  cash_min_amount?: number
  cash_max_amount?: number
  floor_ratio?: number
  ceiling_ratio: number
  mid_ratio?: number
}

export interface ClassDeviation {
  current_ratio: number
  target_mid: number
  deviation: number
  is_above_floor: boolean
  is_below_ceiling: boolean
  is_in_range: boolean
  deviation_level: DeviationLevel
}

export interface CashDeviation {
  current_amount: number
  min_amount: number
  max_amount: number
  status: CashStatusType
}

export interface DeviationSnapshot {
  by_class: Record<string, ClassDeviation>
  cash: CashDeviation
  overall_status: OverallStatusType
  priority_action: PriorityActionType
}

export interface AllocationPlanItem {
  asset_class: string
  label: string
  current_ratio: number
  target_mid: number
  deviation: number
  suggested_amount: number
  suggested_ratio: number
  candidates: string[]
}

export interface DisciplineViolation {
  type: string
  message: string
  severity: 'warning' | 'block'
}

export interface DisciplineCheckResult {
  passed: boolean
  violations: DisciplineViolation[]
}

export interface AllocationResult {
  total_amount: number
  allocations: Record<string, number>
  plan_items: AllocationPlanItem[]
  discipline_check?: DisciplineCheckResult
}

export interface ExplainPanelData {
  tools_called: string[]
  key_data: Record<string, unknown>
  reasoning: string
}

export interface AllocationAIResponse {
  diagnosis?: string
  logic?: string
  plan?: {
    type: string
    table: AllocationPlanItem[]
    totalAmount: number
    discipline?: DisciplineCheckResult
  }
  risk_note?: string
  status_conclusion?: string
  deviation_detail?: string
  action_direction?: {
    level: string
    description: string
  }
  explain_panel?: ExplainPanelData
}

export interface SessionContext {
  confirmed_increment_amount?: number
  confirmed_replanning?: boolean
  user_requested_deriv?: boolean
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface AllocationChatResponse {
  intent_type: string
  response: AllocationAIResponse
  updated_session_context?: SessionContext
}

export interface UnclassifiedHolding {
  id: number
  name: string
  ticker?: string
  platform: string
  asset_class: string
  market_value_cny: number
}

// ── API 调用 ─────────────────────────────────────────────

export const allocationApi = {
  getSnapshot: () =>
    request<AllocationSnapshot>('/allocation/snapshot'),

  getDeviation: () =>
    request<DeviationSnapshot>('/allocation/deviation'),

  getTargets: () =>
    request<AssetTarget[]>('/allocation/targets'),

  postIncrementPlan: (incrementAmount: number, userRequestedDeriv = false) =>
    request<AllocationResult>('/allocation/increment-plan', {
      method: 'POST',
      body: JSON.stringify({
        increment_amount: incrementAmount,
        user_requested_deriv: userRequestedDeriv,
      }),
    }),

  postInitialPlan: (totalAmount: number) =>
    request<AllocationResult>('/allocation/initial-plan', {
      method: 'POST',
      body: JSON.stringify({ total_amount: totalAmount }),
    }),

  postDisciplineCheck: (proposedAllocation: Record<string, number>) =>
    request<DisciplineCheckResult>('/allocation/discipline-check', {
      method: 'POST',
      body: JSON.stringify({ proposed_allocation: proposedAllocation }),
    }),

  classifyAsset: (holdingId: number, assetClass: string) =>
    request<{ success: boolean }>('/allocation/classify-asset', {
      method: 'POST',
      body: JSON.stringify({ holding_id: holdingId, asset_class: assetClass }),
    }),

  getUnclassified: () =>
    request<UnclassifiedHolding[]>('/allocation/unclassified-holdings'),

  chat: (
    message: string,
    conversationHistory: ChatMessage[] = [],
    context?: Record<string, unknown>,
    sessionContext?: SessionContext,
  ) =>
    request<AllocationChatResponse>('/allocation/chat', {
      method: 'POST',
      body: JSON.stringify({
        message,
        conversation_history: conversationHistory,
        context,
        session_context: sessionContext,
      }),
    }),
}

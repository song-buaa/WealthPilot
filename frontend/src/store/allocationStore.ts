/**
 * 资产配置模块 — Zustand 状态管理
 *
 * 看板数据：从 allocation API 获取
 * 对话功能：通过 SSE 调用 decision/chat 端点（与投资决策共用后端）
 * 消息结构对齐 Decision.tsx 的 Message 类型
 */

import { create } from 'zustand'
import {
  allocationApi,
  type AllocationSnapshot,
  type DeviationSnapshot,
  type AssetTarget,
} from '@/lib/allocation-api'
import { streamDecisionChat, decisionApi, type ExplainData } from '@/lib/api'

// ── 消息类型（对齐 Decision.tsx 的 Message）──────────────────

export interface StageInfo {
  name: string
  status: string
  summary?: string
}

export interface AllocMessage {
  id: string
  role: 'user' | 'ai' | 'error'
  content: string
  streaming?: boolean
  decisionId?: string
  intent?: Record<string, unknown>
  stages?: StageInfo[]
  conclusion?: { verdict: string; summary: string }
  error?: boolean
  // AssetAllocation 意图特有
  allocationPlan?: unknown
}

interface AllocationStore {
  // 看板
  snapshot: AllocationSnapshot | null
  deviation: DeviationSnapshot | null
  targets: AssetTarget[]
  isLoading: boolean
  error: string | null

  // 对话（SSE 流）
  messages: AllocMessage[]
  isStreaming: boolean
  sessionId: string
  abortController: AbortController | null
  explainContent: ExplainData | null
  isExplainLoading: boolean

  // 动作
  fetchDashboard: () => Promise<void>
  sendMessage: (text: string) => Promise<void>
  abortStream: () => void
  clearChat: () => void
  openExplain: (decisionId: string) => Promise<void>
}

function uid() { return crypto.randomUUID() }

export const useAllocationStore = create<AllocationStore>((set, get) => ({
  snapshot: null,
  deviation: null,
  targets: [],
  isLoading: false,
  error: null,

  messages: [],
  isStreaming: false,
  sessionId: uid(),
  abortController: null,
  explainContent: null,
  isExplainLoading: false,

  fetchDashboard: async () => {
    set({ isLoading: true, error: null })
    try {
      const [snapshot, deviation, targets] = await Promise.all([
        allocationApi.getSnapshot(),
        allocationApi.getDeviation(),
        allocationApi.getTargets(),
      ])
      set({ snapshot, deviation, targets, isLoading: false })
    } catch (e) {
      set({ error: (e as Error).message, isLoading: false })
    }
  },

  sendMessage: async (text: string) => {
    const state = get()
    const userMsgId = uid()
    const aiMsgId = uid()
    const controller = new AbortController()

    set({
      messages: [
        ...state.messages,
        { id: userMsgId, role: 'user', content: text },
        { id: aiMsgId, role: 'ai', content: '', streaming: true, stages: [] },
      ],
      isStreaming: true,
      abortController: controller,
    })

    const updateAi = (updater: (m: AllocMessage) => AllocMessage) => {
      set({ messages: get().messages.map(m => m.id === aiMsgId ? updater(m) : m) })
    }

    try {
      let lastDecisionId: string | null = null

      for await (const event of streamDecisionChat(text, state.sessionId, controller.signal)) {
        if (event.type === 'intent') {
          updateAi(m => ({ ...m, intent: event.data as Record<string, unknown> }))
        }

        else if (event.type === 'stage') {
          const stage: StageInfo = {
            name:    (event.data as { stage?: string }).stage ?? '',
            status:  (event.data as { status?: string }).status ?? 'running',
            summary: (event.data as { label?: string }).label,
          }
          updateAi(m => ({
            ...m,
            stages: [
              ...(m.stages ?? []).filter(s => s.name !== stage.name),
              stage,
            ],
          }))
        }

        else if (event.type === 'text') {
          const delta = (event.data as { delta?: string }).delta || ''
          updateAi(m => ({ ...m, content: m.content + delta }))
        }

        else if (event.type === 'done') {
          const data = event.data as Record<string, unknown>
          const did = data.decision_id as string | undefined
          const conclusionLevel = data.conclusion_level as string | undefined
          const conclusionLabel = data.conclusion_label as string | undefined
          if (did) lastDecisionId = did

          updateAi(m => ({
            ...m,
            streaming: false,
            decisionId: did,
            allocationPlan: data.allocationPlan,
            stages: (m.stages ?? []).map(s =>
              s.status.toLowerCase() === 'running' ? { ...s, status: 'pass' } : s
            ),
            ...(conclusionLevel ? {
              conclusion: { verdict: conclusionLabel ?? conclusionLevel, summary: '' },
            } : {}),
          }))
        }

        else if (event.type === 'error') {
          updateAi(m => ({ ...m, streaming: false, error: true, content: m.content || '发生错误，请重试' }))
        }
      }

      // SSE 流结束后拉取 explain 数据（与 Decision.tsx 完全一致的时序）
      set({ isStreaming: false, abortController: null })
      if (lastDecisionId) {
        try {
          const data = await decisionApi.getExplain(lastDecisionId, get().sessionId)
          set({ explainContent: data, isExplainLoading: false })
        } catch {
          // explain 获取失败不影响主流程
        }
      }
    } catch (e) {
      if ((e as Error)?.name !== 'AbortError') {
        updateAi(m => ({ ...m, streaming: false, error: true, content: m.content || '连接失败，请检查网络后重试' }))
      }
      set({ isStreaming: false, abortController: null })
    }
  },

  abortStream: () => {
    const ctrl = get().abortController
    if (ctrl) ctrl.abort()
    set({ isStreaming: false, abortController: null })
  },

  clearChat: () => {
    const sid = get().sessionId
    decisionApi.clearSession(sid).catch(() => {})
    set({
      messages: [],
      isStreaming: false,
      sessionId: uid(),
      abortController: null,
      explainContent: null,
    })
  },

  openExplain: async (decisionId: string) => {
    set({ isExplainLoading: true })
    try {
      const data = await decisionApi.getExplain(decisionId, get().sessionId)
      set({ explainContent: data, isExplainLoading: false })
    } catch {
      set({ isExplainLoading: false })
    }
  },
}))

/**
 * decisionStore — 投资决策页全局状态（Zustand）
 */
import { create } from 'zustand'
import type { ExplainData } from '@/lib/api'

// ── 类型 ──────────────────────────────────────────────────

export type MessageRole = 'user' | 'ai' | 'stage' | 'error'
export type Verdict = 'BUY' | 'SELL' | 'HOLD' | 'CAUTIOUS' | 'BLOCKED' | null

export interface Message {
  id: string
  role: MessageRole
  content: string        // ai 消息在流式过程中持续追加
  timestamp: number
  decisionId?: string    // done 事件后设置，用于触发 Explain 查询
  verdict?: Verdict      // done 事件后设置，控制结论 Badge 渲染
}

// ── Store 接口 ─────────────────────────────────────────────

interface DecisionStore {
  // State
  messages: Message[]
  isStreaming: boolean
  sessionId: string
  isPanelOpen: boolean
  activeDecisionId: string | null
  explainContent: ExplainData | null
  isExplainLoading: boolean

  // Chat Actions
  addUserMessage: (text: string) => string           // 返回新消息 id
  addAIMessage: () => string                         // 添加空 ai 消息，返回 id
  appendAIChunk: (id: string, delta: string) => void // SSE text 事件
  upsertStageMsg: (label: string) => void            // SSE stage 事件（覆盖更新）
  finalizeAIMsg: (id: string, decisionId: string | null, verdict: Verdict) => void
  setError: (msg: string) => void
  setStreaming: (v: boolean) => void
  removeStageMsg: () => void

  // Explain Panel Actions
  openExplain: (decisionId: string) => void
  setExplainContent: (data: ExplainData | null) => void
  setExplainLoading: (v: boolean) => void
  closePanel: () => void

  // Session
  clearSession: () => void
}

// ── 工具 ───────────────────────────────────────────────────

function uid(): string {
  return crypto.randomUUID()
}

// ── Store 实现 ─────────────────────────────────────────────

export const useDecisionStore = create<DecisionStore>((set) => ({
  messages: [],
  isStreaming: false,
  sessionId: uid(),
  isPanelOpen: false,
  activeDecisionId: null,
  explainContent: null,
  isExplainLoading: false,

  // ── Chat ──

  addUserMessage: (text) => {
    const id = uid()
    set((s) => ({
      messages: [
        ...s.messages,
        { id, role: 'user', content: text, timestamp: Date.now() },
      ],
    }))
    return id
  },

  addAIMessage: () => {
    const id = uid()
    set((s) => ({
      messages: [
        ...s.messages,
        { id, role: 'ai', content: '', timestamp: Date.now() },
      ],
    }))
    return id
  },

  appendAIChunk: (id, delta) => {
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === id ? { ...m, content: m.content + delta } : m
      ),
    }))
  },

  // stage 消息：同一轮只保留最新一条（覆盖而非追加）
  upsertStageMsg: (label) => {
    set((s) => {
      const hasStage = s.messages.some((m) => m.role === 'stage')
      if (hasStage) {
        return {
          messages: s.messages.map((m) =>
            m.role === 'stage' ? { ...m, content: label } : m
          ),
        }
      }
      return {
        messages: [
          ...s.messages,
          { id: uid(), role: 'stage', content: label, timestamp: Date.now() },
        ],
      }
    })
  },

  removeStageMsg: () => {
    set((s) => ({ messages: s.messages.filter((m) => m.role !== 'stage') }))
  },

  finalizeAIMsg: (id, decisionId, verdict) => {
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === id ? { ...m, decisionId: decisionId ?? undefined, verdict } : m
      ),
    }))
  },

  setError: (msg) => {
    set((s) => ({
      messages: [
        ...s.messages.filter((m) => m.role !== 'stage'),
        { id: uid(), role: 'error', content: msg, timestamp: Date.now() },
      ],
    }))
  },

  setStreaming: (v) => set({ isStreaming: v }),

  // ── Explain Panel ──

  openExplain: (decisionId) =>
    set({ activeDecisionId: decisionId, isPanelOpen: true, explainContent: null }),

  setExplainContent: (data) => set({ explainContent: data }),
  setExplainLoading: (v) => set({ isExplainLoading: v }),
  closePanel: () => set({ isPanelOpen: false, activeDecisionId: null, explainContent: null }),

  // ── Session ──

  clearSession: () =>
    set({
      messages: [],
      isStreaming: false,
      sessionId: uid(),
      isPanelOpen: false,
      activeDecisionId: null,
      explainContent: null,
    }),
}))

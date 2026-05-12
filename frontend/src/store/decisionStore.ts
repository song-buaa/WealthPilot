/**
 * decisionStore — 投资决策页全局状态（Zustand）
 *
 * v3.5 M3: 新增 conversations 列表 + activeConversationId 管理。
 * 消息层状态（messages/streaming 等）仍由 Decision.tsx 本地管理。
 */
import { create } from 'zustand'
import { conversationsApi, type Conversation } from '@/lib/api'

// ── Store 接口 ─────────────────────────────────────────────

interface DecisionStore {
  // Conversations
  conversations: Conversation[]
  activeConversationId: string | null
  isLoadingConversations: boolean

  // Actions
  fetchConversations: () => Promise<void>
  createConversation: () => Promise<string>           // 返回新 id
  switchConversation: (id: string) => void
  deleteConversation: (id: string) => Promise<void>
  renameConversation: (id: string, title: string) => Promise<void>
  updateConversationTitle: (id: string, title: string) => void  // 本地更新（不调 API）
}

// ── Store 实现 ─────────────────────────────────────────────

export const useDecisionStore = create<DecisionStore>((set, get) => ({
  conversations: [],
  activeConversationId: null,
  isLoadingConversations: false,

  fetchConversations: async () => {
    set({ isLoadingConversations: true })
    try {
      const list = await conversationsApi.list()
      set({ conversations: list, isLoadingConversations: false })
    } catch {
      set({ isLoadingConversations: false })
    }
  },

  createConversation: async () => {
    const conv = await conversationsApi.create()
    set((s) => ({
      conversations: [conv, ...s.conversations],
      activeConversationId: conv.id,
    }))
    return conv.id
  },

  switchConversation: (id) => {
    set({ activeConversationId: id })
  },

  deleteConversation: async (id) => {
    await conversationsApi.remove(id)
    const { conversations, activeConversationId } = get()
    const remaining = conversations.filter((c) => c.id !== id)
    const needSwitch = activeConversationId === id
    set({
      conversations: remaining,
      ...(needSwitch
        ? { activeConversationId: remaining[0]?.id ?? null }
        : {}),
    })
  },

  renameConversation: async (id, title) => {
    await conversationsApi.rename(id, title)
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, title } : c
      ),
    }))
  },

  updateConversationTitle: (id, title) => {
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, title } : c
      ),
    }))
  },
}))

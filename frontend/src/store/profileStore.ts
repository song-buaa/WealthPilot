/**
 * profileStore — 用户画像页全局状态（Zustand）
 * 重构后去掉分步骤状态，只保留 profile 数据
 */
import { create } from 'zustand'
import type { UserProfile } from '@/lib/api'

interface ProfileStore {
  profile:    UserProfile | null
  isLoading:  boolean
  setProfile: (p: UserProfile | null) => void
  patchProfile: (patch: Partial<UserProfile>) => void
  setLoading: (v: boolean) => void
  reset:      () => void
}

export const useProfileStore = create<ProfileStore>((set) => ({
  profile:   null,
  isLoading: false,

  setProfile: (p) => set({ profile: p }),

  patchProfile: (patch) =>
    set((s) => ({
      profile: s.profile ? { ...s.profile, ...patch } : (patch as UserProfile),
    })),

  setLoading: (v) => set({ isLoading: v }),
  reset:      ()  => set({ profile: null, isLoading: false }),
}))

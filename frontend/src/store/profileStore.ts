/**
 * profileStore — 用户画像页全局状态（Zustand）
 */
import { create } from 'zustand'
import type { UserProfile, ConflictItem } from '@/lib/api'

// ── Store 接口 ─────────────────────────────────────────────

interface ProfileStore {
  // State
  profile:   UserProfile | null
  step:      number           // 1-7
  conflicts: ConflictItem[]
  isLoading: boolean

  // Actions
  setProfile:   (p: UserProfile | null) => void
  patchProfile: (patch: Partial<UserProfile>) => void
  setStep:      (s: number) => void
  nextStep:     () => void
  prevStep:     () => void
  setConflicts: (c: ConflictItem[]) => void
  setLoading:   (v: boolean) => void
  reset:        () => void
}

// ── Store 实现 ─────────────────────────────────────────────

export const useProfileStore = create<ProfileStore>((set) => ({
  profile:   null,
  step:      1,
  conflicts: [],
  isLoading: false,

  setProfile: (p) => set({ profile: p }),

  patchProfile: (patch) =>
    set((s) => ({
      profile: s.profile ? { ...s.profile, ...patch } : (patch as UserProfile),
    })),

  setStep:  (s) => set({ step: s }),
  nextStep: ()  => set((s) => ({ step: Math.min(s.step + 1, 7) })),
  prevStep: ()  => set((s) => ({ step: Math.max(s.step - 1, 1) })),

  setConflicts: (c) => set({ conflicts: c }),
  setLoading:   (v) => set({ isLoading: v }),

  reset: () => set({ profile: null, step: 1, conflicts: [], isLoading: false }),
}))

/**
 * UserProfile — 用户画像与投资目标（分步引导，7步）
 *
 * Step 1: 风险评估
 * Step 2: 基础信息
 * Step 3: 投资目标
 * Step 4: AI 对话补全（有缺失字段时）
 * Step 5: 冲突检测（自动触发）
 * Step 6: 确认与修改
 * Step 7: 画像结果
 */
import React, { useEffect, useState } from 'react'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { profileApi, type UserProfile as TUserProfile } from '@/lib/api'
import { useProfileStore } from '@/store/profileStore'

import StepRisk      from '@/components/profile/StepRisk'
import StepBasicInfo from '@/components/profile/StepBasicInfo'
import StepGoals     from '@/components/profile/StepGoals'
import StepAIChat    from '@/components/profile/StepAIChat'
import StepConflicts from '@/components/profile/StepConflicts'
import StepConfirm   from '@/components/profile/StepConfirm'
import StepResult    from '@/components/profile/StepResult'

// ── 样式常量 ──────────────────────────────────────────────────────────────────

const STEP_LABELS = ['风险评估', '基础信息', '投资目标', 'AI 补全', '冲突检测', '确认修改', '画像结果']

const S = {
  page: {
    minHeight: '100%', background: '#F8FAFC',
    display: 'flex', flexDirection: 'column' as const, alignItems: 'center',
    padding: '32px 16px 48px',
  },
  container: {
    width: '100%', maxWidth: 680,
    display: 'flex', flexDirection: 'column' as const, gap: 24,
  },
  card: {
    background: '#fff', border: '1px solid #E5E7EB',
    borderRadius: 14, padding: 28, boxShadow: '0 1px 4px rgba(0,0,0,0.06)',
  } as React.CSSProperties,
  pageTitle: { fontSize: 20, fontWeight: 700, color: '#1B2A4A' } as React.CSSProperties,
  pageSubtitle: { fontSize: 13, color: '#6B7280', marginTop: 4 } as React.CSSProperties,
  stepTitle: { fontSize: 15, fontWeight: 700, color: '#1B2A4A', marginBottom: 20 } as React.CSSProperties,
}

// ── 进度条 ────────────────────────────────────────────────────────────────────

function StepBar({ current }: { current: number }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' as const }}>
      {STEP_LABELS.map((label, i) => {
        const idx   = i + 1
        const done  = idx < current
        const active = idx === current
        return (
          <React.Fragment key={idx}>
            <div style={{ display: 'flex', flexDirection: 'column' as const, alignItems: 'center', gap: 3 }}>
              <div style={{
                width: 28, height: 28, borderRadius: '50%',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 11, fontWeight: 700,
                background: done ? '#3B82F6' : active ? 'linear-gradient(135deg,#3B82F6,#1D4ED8)' : '#E5E7EB',
                color:      done || active ? '#fff' : '#9CA3AF',
                boxShadow:  active ? '0 2px 8px rgba(59,130,246,0.4)' : 'none',
              }}>
                {done ? '✓' : idx}
              </div>
              <span style={{ fontSize: 10, color: active ? '#3B82F6' : '#9CA3AF', whiteSpace: 'nowrap' as const }}>
                {label}
              </span>
            </div>
            {i < STEP_LABELS.length - 1 && (
              <div style={{
                flex: 1, height: 2, borderRadius: 2, minWidth: 8, marginBottom: 16,
                background: done ? '#3B82F6' : '#E5E7EB',
              }} />
            )}
          </React.Fragment>
        )
      })}
    </div>
  )
}

// ── 主页面 ────────────────────────────────────────────────────────────────────

export default function UserProfile() {
  const {
    profile: storeProfile, step, conflicts,
    patchProfile, setProfile, setStep, nextStep, prevStep, setConflicts,
  } = useProfileStore()

  const profile = (storeProfile ?? {}) as Partial<TUserProfile>

  const [initLoading, setInitLoading] = useState(true)
  const [riskExpired, setRiskExpired] = useState(false)

  // 初始化：拉取已有画像 + 检查过期
  useEffect(() => {
    Promise.all([profileApi.get(), profileApi.isRiskExpired()])
      .then(([existing, expiredRes]) => {
        if (existing && Object.keys(existing).length > 0) {
          setProfile(existing as TUserProfile)
          // 已有画像时直接进入确认页
          setStep(6)
        }
        setRiskExpired(expiredRes.expired)
      })
      .catch(console.error)
      .finally(() => setInitLoading(false))
  }, [])  // eslint-disable-line

  function goTo(s: number) { setStep(s) }
  function next()          { nextStep() }
  function prev()          { prevStep() }

  if (initLoading) {
    return (
      <div style={{ ...S.page, justifyContent: 'center' }}>
        <Loader2 size={24} style={{ animation: 'spin 1s linear infinite', color: '#3B82F6' }} />
      </div>
    )
  }

  return (
    <div style={S.page}>
      <div style={S.container}>
        {/* 标题 */}
        <div>
          <div style={S.pageTitle}>用户画像与投资目标</div>
          <div style={S.pageSubtitle}>帮助系统更好地了解您，为您提供个性化的投资建议</div>
        </div>

        {/* 风险过期提示 */}
        {riskExpired && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10,
            padding: '12px 16px', background: '#FFFBEB',
            border: '1px solid #FDE68A', borderRadius: 10,
            fontSize: 13, color: '#92400E',
          }}>
            <AlertTriangle size={15} />
            当前风险评估可能已过期（超过12个月），建议重新评估
          </div>
        )}

        {/* 进度条 */}
        <div style={S.card}>
          <StepBar current={step} />
        </div>

        {/* 步骤内容 */}
        <div style={S.card}>
          <div style={S.stepTitle}>
            {step}/{STEP_LABELS.length} — {STEP_LABELS[step - 1]}
          </div>

          {step === 1 && (
            <StepRisk data={profile} onChange={patchProfile} onNext={next} />
          )}
          {step === 2 && (
            <StepBasicInfo data={profile} onChange={patchProfile} onNext={next} onPrev={prev} />
          )}
          {step === 3 && (
            <StepGoals data={profile} onChange={patchProfile} onNext={next} onPrev={prev} />
          )}
          {step === 4 && (
            <StepAIChat data={profile} onChange={patchProfile} onNext={next} onPrev={prev} />
          )}
          {step === 5 && (
            <StepConflicts
              data={profile}
              conflicts={conflicts}
              onChange={patchProfile}
              onConflictsChecked={c => setConflicts(c)}
              onNext={() => goTo(6)}
              onPrev={prev}
            />
          )}
          {step === 6 && (
            <StepConfirm data={profile} onChange={patchProfile} onNext={next} onPrev={prev} />
          )}
          {step === 7 && (
            <StepResult
              data={profile}
              onPrev={prev}
              onSaved={() => setProfile(null)}
            />
          )}
        </div>
      </div>
    </div>
  )
}

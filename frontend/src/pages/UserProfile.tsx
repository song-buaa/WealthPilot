/**
 * UserProfile — 用户画像与投资目标
 * 两个状态：无画像 → 填写页；已有画像 → 结果页
 */
import React, { useEffect, useState } from 'react'
import { Loader2, User } from 'lucide-react'
import { profileApi, type UserProfile as TUserProfile } from '@/lib/api'
import ProfileForm from '@/components/profile/ProfileForm'
import ProfileResult from '@/components/profile/ProfileResult'

export default function UserProfile() {
  const [profile, setProfile] = useState<TUserProfile | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    profileApi.get()
      .then(data => {
        if (data && Object.keys(data).length > 0) setProfile(data as TUserProfile)
      })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 300, gap: 8, color: '#9CA3AF' }}>
        <Loader2 size={18} style={{ animation: 'spin 1s linear infinite' }} />
        <span style={{ fontSize: 13 }}>加载中…</span>
      </div>
    )
  }

  return (
    <div>
      {/* 页面标题 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <div style={{
          width: 38, height: 38, borderRadius: 10,
          background: 'linear-gradient(135deg, #1B2A4A, #2D4A7A)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <User size={18} color="#fff" />
        </div>
        <div>
          <div style={{ fontSize: 20, fontWeight: 700, color: '#1B2A4A', letterSpacing: -0.3 }}>用户画像与投资目标</div>
          <div style={{ fontSize: 12, color: '#9CA3AF', marginTop: 1 }}>风险评估 · 投资目标 · 画像生成</div>
        </div>
      </div>

      {profile
        ? <ProfileResult profile={profile} onUpdate={setProfile} />
        : <ProfileForm onProfileCreated={setProfile} />
      }
    </div>
  )
}

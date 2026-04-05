/**
 * ProfileResult — 已有画像时的结果展示页
 * 顶部：AI总结 + 3个badge
 * 下方：两个可展开/编辑的卡片
 */
import React from 'react'
import type { UserProfile } from '@/lib/api'
import ResultCardA from './ResultCardA'
import ResultCardB from './ResultCardB'

const CONFIDENCE_STYLE: Record<string, { bg: string; color: string; label: string }> = {
  high:   { bg: '#D1FAE5', color: '#065F46', label: '高置信度' },
  medium: { bg: '#FEF3C7', color: '#92400E', label: '中置信度' },
  low:    { bg: '#FEE2E2', color: '#991B1B', label: '低置信度' },
}

const RISK_TYPE: Record<number, string> = { 1: '保守型', 2: '稳健型', 3: '平衡型', 4: '成长型', 5: '进取型' }

interface Props {
  profile:  UserProfile
  onUpdate: (profile: UserProfile) => void
}

export default function ProfileResult({ profile, onUpdate }: Props) {
  const conf = CONFIDENCE_STYLE[profile.ai_confidence ?? 'low'] ?? CONFIDENCE_STYLE.low
  const riskLabel = profile.risk_normalized_level
    ? `R${profile.risk_normalized_level} ${RISK_TYPE[profile.risk_normalized_level]}`
    : '未设置'

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
      {/* AI 总结区 */}
      <div style={{ background:'linear-gradient(135deg, #EFF6FF, #E0E7FF)', borderRadius:14, padding:'24px 24px 20px', border:'1px solid #BFDBFE' }}>
        <div style={{ fontSize:15, lineHeight:1.8, color:'#1B2A4A', fontWeight:500, marginBottom:16 }}>
          {profile.ai_summary ?? '暂无 AI 总结'}
        </div>
        <div style={{ display:'flex', flexWrap:'wrap', gap:8 }}>
          {profile.risk_normalized_level && (
            <span style={{ padding:'4px 12px', borderRadius:20, fontSize:12, fontWeight:600, background:'#DBEAFE', color:'#1D4ED8' }}>
              {riskLabel}
            </span>
          )}
          {profile.ai_style && (
            <span style={{ padding:'4px 12px', borderRadius:20, fontSize:12, fontWeight:600, background:'#EDE9FE', color:'#5B21B6' }}>
              {profile.ai_style}风格
            </span>
          )}
          <span style={{ padding:'4px 12px', borderRadius:20, fontSize:12, fontWeight:600, background: conf.bg, color: conf.color }}>
            {conf.label}
          </span>
        </div>
      </div>

      {/* 卡片1：风险 + 基础信息 */}
      <ResultCardA profile={profile} onSaved={onUpdate} />

      {/* 卡片2：投资目标 */}
      <ResultCardB profile={profile} onSaved={onUpdate} />
    </div>
  )
}

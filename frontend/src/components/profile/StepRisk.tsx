/**
 * Step 1 — 风险评估
 * 有外部风评 → 录入来源+等级（银行A1-A5 / 券商C1-C6 / 自定义低中高）
 * 无外部风评 → 3 个下拉 → AI 评估
 */
import React, { useState } from 'react'
import { profileApi, type UserProfile } from '@/lib/api'
import { Loader2 } from 'lucide-react'

// ── 前端本地标准化（镜像 profile_service.py 逻辑）────────────
function normalizeLevel(sourceType: string, original: string): number {
  const lv = original.trim().toUpperCase()
  if (sourceType === 'bank') {
    const m: Record<string, number> = { A1: 1, A2: 2, A3: 3, A4: 4, A5: 5 }
    return m[lv] ?? 3
  } else if (sourceType === 'broker') {
    const m: Record<string, number> = { C1: 1, C2: 1, C3: 2, C4: 3, C5: 4, C6: 5 }
    return m[lv] ?? 3
  } else {
    const m: Record<string, number> = { 低: 2, 中: 3, 高: 4 }
    return m[original.trim()] ?? 3
  }
}
const RISK_TYPE: Record<number, string> = { 1: '保守型', 2: '稳健型', 3: '平衡型', 4: '成长型', 5: '进取型' }

// ── 样式常量（与 Discipline 页一致）────────────────────────
const S = {
  label:  { fontSize: 12, fontWeight: 500, color: '#374151', marginBottom: 4 } as React.CSSProperties,
  select: {
    width: '100%', padding: '8px 10px', border: '1px solid #E5E7EB',
    borderRadius: 8, fontSize: 13, color: '#1B2A4A', background: '#fff',
    cursor: 'pointer',
  } as React.CSSProperties,
  badge: (level: number): React.CSSProperties => ({
    display: 'inline-block', padding: '4px 12px',
    borderRadius: 20, fontSize: 12, fontWeight: 600,
    background: ['', '#E0F2FE', '#D1FAE5', '#FEF9C3', '#FEE2E2', '#FECDD3'][level] || '#F3F4F6',
    color:      ['', '#0369A1', '#065F46', '#92400E', '#B91C1C', '#9F1239'][level] || '#374151',
  }),
}

interface Props {
  data: Partial<UserProfile>
  onChange: (patch: Partial<UserProfile>) => void
  onNext: () => void
}

export default function StepRisk({ data, onChange, onNext }: Props) {
  const [hasExternal, setHasExternal] = useState<boolean | null>(
    data.risk_source === 'external' ? true : data.risk_source === 'ai' ? false : null
  )
  const [sourceType, setSourceType] = useState<string>('bank')
  const [originalLevel, setOriginalLevel] = useState<string>(data.risk_original_level ?? '')
  const [aiLoading, setAiLoading] = useState(false)

  // 无外部风评时的 3 个下拉
  const [aiMaxDrawdown, setAiMaxDrawdown] = useState(data.max_drawdown ?? '')
  const [aiHorizon, setAiHorizon] = useState(data.investment_horizon ?? '')
  const [aiReturn, setAiReturn] = useState(data.target_return ?? '')

  const normalized = originalLevel ? normalizeLevel(sourceType, originalLevel) : null

  async function handleAIAssess() {
    if (!aiMaxDrawdown || !aiHorizon || !aiReturn) return
    setAiLoading(true)
    try {
      const res = await profileApi.extract(
        `最大回撤容忍${aiMaxDrawdown}，投资期限${aiHorizon}，目标收益${aiReturn}`,
        {}
      )
      const aiLevel = res.extracted?.risk_normalized_level as number | undefined
      const level = aiLevel ?? (aiMaxDrawdown === '<5%' ? 1 : aiMaxDrawdown === '5-15%' ? 2 : aiMaxDrawdown === '15-30%' ? 4 : 5)
      onChange({
        risk_source:           'ai',
        risk_provider:         'ai_generated',
        risk_normalized_level: level,
        risk_type:             RISK_TYPE[level],
        max_drawdown:          aiMaxDrawdown,
        investment_horizon:    aiHorizon,
        target_return:         aiReturn,
        risk_assessed_at:      new Date().toISOString(),
      })
      onNext()
    } finally {
      setAiLoading(false)
    }
  }

  function handleExternalConfirm() {
    if (!normalized) return
    const riskType = RISK_TYPE[normalized]
    const levelOptions: Record<string, string[]> = {
      bank:   ['A1','A2','A3','A4','A5'],
      broker: ['C1','C2','C3','C4','C5','C6'],
      custom: ['低','中','高'],
    }
    const providerLabel: Record<string, string> = { bank: '银行评估', broker: '券商评估', custom: '自定义' }
    onChange({
      risk_source:           'external',
      risk_provider:         providerLabel[sourceType],
      risk_original_level:   originalLevel,
      risk_normalized_level: normalized,
      risk_type:             riskType,
      risk_assessed_at:      new Date().toISOString(),
    })
    onNext()
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* 是否有外部风评 */}
      <div>
        <div style={S.label}>是否有银行/券商的正式风险评估结果？</div>
        <div style={{ display: 'flex', gap: 10, marginTop: 6 }}>
          {[{ v: true, label: '有' }, { v: false, label: '没有，帮我评估' }].map(({ v, label }) => (
            <button
              key={String(v)}
              onClick={() => setHasExternal(v)}
              style={{
                padding: '8px 20px', borderRadius: 8, fontSize: 13, fontWeight: 500,
                cursor: 'pointer', border: '1px solid',
                background: hasExternal === v ? 'linear-gradient(135deg,#3B82F6,#1D4ED8)' : '#fff',
                color:      hasExternal === v ? '#fff' : '#374151',
                borderColor: hasExternal === v ? '#3B82F6' : '#E5E7EB',
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* 有外部风评 */}
      {hasExternal === true && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div>
            <div style={S.label}>评估来源</div>
            <select value={sourceType} onChange={(e) => { setSourceType(e.target.value); setOriginalLevel('') }} style={S.select}>
              <option value="bank">银行（A1-A5）</option>
              <option value="broker">券商（C1-C6）</option>
              <option value="custom">自定义（低/中/高）</option>
            </select>
          </div>
          <div>
            <div style={S.label}>风险等级</div>
            <select value={originalLevel} onChange={(e) => setOriginalLevel(e.target.value)} style={S.select}>
              <option value="">请选择</option>
              {sourceType === 'bank'   && ['A1','A2','A3','A4','A5'].map(v => <option key={v} value={v}>{v}</option>)}
              {sourceType === 'broker' && ['C1','C2','C3','C4','C5','C6'].map(v => <option key={v} value={v}>{v}</option>)}
              {sourceType === 'custom' && ['低','中','高'].map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          </div>
          {normalized && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ fontSize: 12, color: '#6B7280' }}>标准化结果：</span>
              <span style={S.badge(normalized)}>R{normalized} {RISK_TYPE[normalized]}</span>
            </div>
          )}
          <button
            onClick={handleExternalConfirm}
            disabled={!normalized}
            style={{
              padding: '9px 24px', borderRadius: 8, fontSize: 13, fontWeight: 500,
              background: normalized ? 'linear-gradient(135deg,#3B82F6,#1D4ED8)' : '#E5E7EB',
              color: normalized ? '#fff' : '#9CA3AF', border: 'none', cursor: normalized ? 'pointer' : 'not-allowed',
            }}
          >
            确认，下一步
          </button>
        </div>
      )}

      {/* 无外部风评 — AI 评估 */}
      {hasExternal === false && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div>
            <div style={S.label}>您能接受的最大亏损幅度是多少？</div>
            <select value={aiMaxDrawdown} onChange={(e) => setAiMaxDrawdown(e.target.value)} style={S.select}>
              <option value="">请选择</option>
              {['<5%','5-15%','15-30%','>30%'].map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          </div>
          <div>
            <div style={S.label}>这笔投资计划持有多长时间？</div>
            <select value={aiHorizon} onChange={(e) => setAiHorizon(e.target.value)} style={S.select}>
              <option value="">请选择</option>
              {['<1年','1-3年','3-5年','>5年'].map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          </div>
          <div>
            <div style={S.label}>您期望的年化收益目标是？</div>
            <select value={aiReturn} onChange={(e) => setAiReturn(e.target.value)} style={S.select}>
              <option value="">请选择</option>
              {['<5%','5-10%','10-20%','>20%'].map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          </div>
          <button
            onClick={handleAIAssess}
            disabled={!aiMaxDrawdown || !aiHorizon || !aiReturn || aiLoading}
            style={{
              padding: '9px 24px', borderRadius: 8, fontSize: 13, fontWeight: 500,
              background: aiMaxDrawdown && aiHorizon && aiReturn ? 'linear-gradient(135deg,#3B82F6,#1D4ED8)' : '#E5E7EB',
              color: aiMaxDrawdown && aiHorizon && aiReturn ? '#fff' : '#9CA3AF',
              border: 'none', cursor: 'pointer',
              display: 'inline-flex', alignItems: 'center', gap: 6,
            }}
          >
            {aiLoading && <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} />}
            AI 评估我的风险等级
          </button>
        </div>
      )}
    </div>
  )
}

/**
 * Step 3 — 投资目标
 * goal_type 多选 checkbox，其余 3 个字段下拉
 */
import React from 'react'
import type { UserProfile } from '@/lib/api'

const S = {
  label:    { fontSize: 12, fontWeight: 500, color: '#374151', marginBottom: 4 } as React.CSSProperties,
  select:   {
    width: '100%', padding: '8px 10px', border: '1px solid #E5E7EB',
    borderRadius: 8, fontSize: 13, color: '#1B2A4A', background: '#fff', cursor: 'pointer',
  } as React.CSSProperties,
  checkbox: { display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' } as React.CSSProperties,
}

const GOAL_OPTIONS = ['资本增值', '稳健增长', '保值', '现金流']

interface Props {
  data: Partial<UserProfile>
  onChange: (patch: Partial<UserProfile>) => void
  onNext: () => void
  onPrev: () => void
}

export default function StepGoals({ data, onChange, onNext, onPrev }: Props) {
  const goals = data.goal_type ?? []

  function toggleGoal(g: string) {
    const next = goals.includes(g) ? goals.filter(x => x !== g) : [...goals, g]
    onChange({ goal_type: next.length > 0 ? next : undefined })
  }

  const canNext = goals.length > 0 && !!data.target_return && !!data.max_drawdown && !!data.investment_horizon

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      {/* 目标类型多选 */}
      <div>
        <div style={S.label}>投资目标 <span style={{ color: '#EF4444' }}>*</span>（可多选）</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 6 }}>
          {GOAL_OPTIONS.map(g => (
            <label key={g} style={S.checkbox}>
              <input
                type="checkbox"
                checked={goals.includes(g)}
                onChange={() => toggleGoal(g)}
                style={{ width: 15, height: 15, accentColor: '#3B82F6' }}
              />
              {g}
            </label>
          ))}
        </div>
      </div>

      {/* 目标收益 */}
      <div>
        <div style={S.label}>目标年化收益率 <span style={{ color: '#EF4444' }}>*</span></div>
        <select value={data.target_return ?? ''} onChange={e => onChange({ target_return: e.target.value || undefined })} style={S.select}>
          <option value="">请选择</option>
          {['<5%','5-10%','10-20%','>20%'].map(v => <option key={v} value={v}>{v}</option>)}
        </select>
      </div>

      {/* 最大回撤 */}
      <div>
        <div style={S.label}>可承受最大回撤 <span style={{ color: '#EF4444' }}>*</span></div>
        <select value={data.max_drawdown ?? ''} onChange={e => onChange({ max_drawdown: e.target.value || undefined })} style={S.select}>
          <option value="">请选择</option>
          {['<5%','5-15%','15-30%','>30%'].map(v => <option key={v} value={v}>{v}</option>)}
        </select>
      </div>

      {/* 投资期限 */}
      <div>
        <div style={S.label}>整体投资期限 <span style={{ color: '#EF4444' }}>*</span></div>
        <select value={data.investment_horizon ?? ''} onChange={e => onChange({ investment_horizon: e.target.value || undefined })} style={S.select}>
          <option value="">请选择</option>
          {['<1年','1-3年','3-5年','>5年'].map(v => <option key={v} value={v}>{v}</option>)}
        </select>
      </div>

      <div style={{ display: 'flex', gap: 10, marginTop: 4 }}>
        <button onClick={onPrev} style={{
          padding: '8px 20px', borderRadius: 8, fontSize: 13, fontWeight: 500,
          background: '#fff', color: '#374151', border: '1px solid #E5E7EB', cursor: 'pointer',
        }}>上一步</button>
        <button
          onClick={onNext}
          disabled={!canNext}
          style={{
            padding: '8px 24px', borderRadius: 8, fontSize: 13, fontWeight: 500,
            background: canNext ? 'linear-gradient(135deg,#3B82F6,#1D4ED8)' : '#E5E7EB',
            color: canNext ? '#fff' : '#9CA3AF', border: 'none', cursor: canNext ? 'pointer' : 'not-allowed',
          }}
        >下一步</button>
      </div>
    </div>
  )
}

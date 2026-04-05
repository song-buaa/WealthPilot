/**
 * ModuleB — 投资目标
 * 模块A确认前锁定；解锁后填写4个目标字段
 * 确认时做本地冲突校验，通过后显示"生成画像"按钮
 */
import React, { useState } from 'react'
import { Lock } from 'lucide-react'
import type { UserProfile } from '@/lib/api'

const S = {
  label:    { fontSize: 12, fontWeight: 500, color: '#374151', marginBottom: 4, display: 'block' } as React.CSSProperties,
  select:   { width: '100%', padding: '8px 10px', border: '1px solid #E5E7EB', borderRadius: 8, fontSize: 13, color: '#1B2A4A', background: '#fff', cursor: 'pointer' } as React.CSSProperties,
  selectErr:{ width: '100%', padding: '8px 10px', border: '1px solid #EF4444', borderRadius: 8, fontSize: 13, color: '#1B2A4A', background: '#fff', cursor: 'pointer' } as React.CSSProperties,
  section:  { marginBottom: 20 } as React.CSSProperties,
  errMsg:   { fontSize: 11, color: '#EF4444', marginTop: 2 } as React.CSSProperties,
  btn:      { padding: '9px 24px', borderRadius: 8, fontSize: 13, fontWeight: 600, background: 'linear-gradient(135deg,#3B82F6,#1D4ED8)', color: '#fff', border: 'none', cursor: 'pointer' } as React.CSSProperties,
  btnGreen: { padding: '9px 24px', borderRadius: 8, fontSize: 13, fontWeight: 600, background: 'linear-gradient(135deg,#10B981,#059669)', color: '#fff', border: 'none', cursor: 'pointer' } as React.CSSProperties,
  btnDisabled: { padding: '9px 24px', borderRadius: 8, fontSize: 13, fontWeight: 500, background: '#E5E7EB', color: '#9CA3AF', border: 'none', cursor: 'not-allowed' } as React.CSSProperties,
  checkbox: { width: 15, height: 15, marginRight: 6, cursor: 'pointer' } as React.CSSProperties,
}

const GOAL_TYPE_OPTIONS = ['资本增值', '稳健增长', '保值', '现金流']
const TARGET_RETURN_OPTIONS = ['<5%', '5-10%', '10-20%', '>20%']
const MAX_DRAWDOWN_OPTIONS  = ['<5%', '5-15%', '15-30%', '>30%']
const HORIZON_OPTIONS       = ['<1年', '1-3年', '3-5年', '>5年']

interface Conflict { fields: string[]; message: string }

function checkConflicts(data: Partial<UserProfile>): Conflict[] {
  const conflicts: Conflict[] = []
  if (data.fund_usage_timeline === '1年内' && ['15-30%', '>30%'].includes(data.max_drawdown ?? '')) {
    conflicts.push({ fields: ['max_drawdown'], message: '资金1年内可能使用，不建议选择高回撤策略' })
  }
  if (data.max_drawdown === '<5%' && ['10-20%', '>20%'].includes(data.target_return ?? '')) {
    conflicts.push({ fields: ['max_drawdown', 'target_return'], message: '低回撤目标与高收益预期存在矛盾，请调整其中一项' })
  }
  return conflicts
}

interface Props {
  locked:       boolean
  data:         Partial<UserProfile>
  onChange:     (patch: Partial<UserProfile>) => void
  onGenerate:   () => void
  isGenerating: boolean
}

export default function ModuleB({ locked, data, onChange, onGenerate, isGenerating }: Props) {
  const [confirmed, setConfirmed]   = useState(false)
  const [conflicts, setConflicts]   = useState<Conflict[]>([])
  const [errFields, setErrFields]   = useState<Set<string>>(new Set())

  if (locked) {
    return (
      <div style={{ padding: 24, textAlign: 'center', color: '#9CA3AF', background: '#F9FAFB', borderRadius: 12, border: '2px dashed #E5E7EB' }}>
        <Lock size={20} style={{ marginBottom: 8, display: 'block', margin: '0 auto 8px' }} />
        <div style={{ fontSize: 13 }}>请先完成上方风险评估</div>
      </div>
    )
  }

  function handleFieldChange(key: string, value: string | string[]) {
    onChange({ [key]: value } as Partial<UserProfile>)
    setErrFields(s => { const n = new Set(s); n.delete(key); return n })
    // 修改后清除确认状态，重新校验
    setConfirmed(false)
    setConflicts([])
  }

  function handleGoalTypeToggle(val: string) {
    const cur = (data.goal_type as string[]) ?? []
    const next = cur.includes(val) ? cur.filter(v => v !== val) : [...cur, val]
    handleFieldChange('goal_type', next)
  }

  function handleConfirm() {
    const c = checkConflicts(data)
    const errSet = new Set(c.flatMap(x => x.fields))
    setConflicts(c)
    setErrFields(errSet)
    if (c.length === 0) {
      setConfirmed(true)
    }
  }

  const goalType = (data.goal_type as string[]) ?? []

  return (
    <div>
      {/* goal_type */}
      <div style={S.section}>
        <label style={S.label}>投资目标（可多选）</label>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
          {GOAL_TYPE_OPTIONS.map(opt => (
            <label key={opt} style={{ display: 'flex', alignItems: 'center', fontSize: 13, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={goalType.includes(opt)}
                onChange={() => handleGoalTypeToggle(opt)}
                style={S.checkbox}
              />
              {opt}
            </label>
          ))}
        </div>
      </div>

      {/* target_return */}
      <div style={S.section}>
        <label style={S.label}>目标收益率</label>
        <select
          value={data.target_return ?? ''}
          onChange={e => handleFieldChange('target_return', e.target.value)}
          style={errFields.has('target_return') ? S.selectErr : S.select}
        >
          <option value="">请选择</option>
          {TARGET_RETURN_OPTIONS.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
        {errFields.has('target_return') && <div style={S.errMsg}>请调整目标收益率</div>}
      </div>

      {/* max_drawdown */}
      <div style={S.section}>
        <label style={S.label}>最大可接受回撤</label>
        <select
          value={data.max_drawdown ?? ''}
          onChange={e => handleFieldChange('max_drawdown', e.target.value)}
          style={errFields.has('max_drawdown') ? S.selectErr : S.select}
        >
          <option value="">请选择</option>
          {MAX_DRAWDOWN_OPTIONS.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
        {errFields.has('max_drawdown') && <div style={S.errMsg}>请调整最大回撤</div>}
      </div>

      {/* investment_horizon */}
      <div style={S.section}>
        <label style={S.label}>投资期限</label>
        <select
          value={data.investment_horizon ?? ''}
          onChange={e => handleFieldChange('investment_horizon', e.target.value)}
          style={S.select}
        >
          <option value="">请选择</option>
          {HORIZON_OPTIONS.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
      </div>

      {/* 冲突提示 */}
      {conflicts.map((c, i) => (
        <div key={i} style={{ padding: '10px 14px', background: '#FFFBEB', border: '1px solid #FDE68A', borderRadius: 8, fontSize: 13, color: '#92400E', marginBottom: 12 }}>
          ⚠️ {c.message}
        </div>
      ))}

      {/* 按钮区 */}
      <div style={{ display: 'flex', gap: 10, marginTop: 8 }}>
        {!confirmed ? (
          <button onClick={handleConfirm} style={S.btn}>确认投资目标</button>
        ) : (
          <button
            onClick={onGenerate}
            disabled={isGenerating}
            style={isGenerating ? S.btnDisabled : S.btnGreen}
          >
            {isGenerating ? '生成中...' : '生成画像'}
          </button>
        )}
      </div>
    </div>
  )
}

/**
 * ResultCardB — 投资目标卡片（可展开/编辑）
 */
import React, { useState } from 'react'
import { ChevronDown, ChevronUp, Pencil, Check, X } from 'lucide-react'
import { profileApi, type UserProfile } from '@/lib/api'

const S = {
  select:    { width:'100%', padding:'6px 8px', border:'1px solid #E5E7EB', borderRadius:6, fontSize:12 } as React.CSSProperties,
  selectErr: { width:'100%', padding:'6px 8px', border:'1px solid #EF4444', borderRadius:6, fontSize:12 } as React.CSSProperties,
  errMsg:    { fontSize:11, color:'#EF4444', marginTop:2 } as React.CSSProperties,
  checkbox:  { width:14, height:14, marginRight:5, cursor:'pointer' } as React.CSSProperties,
}

const TARGET_RETURN_OPTIONS = ['<5%', '5-10%', '10-20%', '>20%']
const MAX_DRAWDOWN_OPTIONS  = ['<5%', '5-15%', '15-30%', '>30%']
const HORIZON_OPTIONS       = ['<1年', '1-3年', '3-5年', '>5年']
const GOAL_TYPES            = ['资本增值', '稳健增长', '保值', '现金流']

interface Conflict { fields: string[]; message: string }

function checkConflicts(data: Partial<UserProfile>): Conflict[] {
  const c: Conflict[] = []
  if (data.fund_usage_timeline === '1年内' && ['15-30%','>30%'].includes(data.max_drawdown ?? ''))
    c.push({ fields: ['max_drawdown'], message: '资金1年内可能使用，不建议选择高回撤策略' })
  if (data.max_drawdown === '<5%' && ['10-20%','>20%'].includes(data.target_return ?? ''))
    c.push({ fields: ['max_drawdown','target_return'], message: '低回撤目标与高收益预期存在矛盾，请调整其中一项' })
  return c
}

interface Props {
  profile: UserProfile
  onSaved: (updated: UserProfile) => void
}

export default function ResultCardB({ profile, onSaved }: Props) {
  const [open, setOpen]       = useState(false)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft]     = useState<Partial<UserProfile>>({})
  const [saving, setSaving]   = useState(false)
  const [conflicts, setConflicts] = useState<Conflict[]>([])
  const [errFields, setErrFields] = useState<Set<string>>(new Set())
  const [saveErr, setSaveErr] = useState('')

  function startEdit() {
    setDraft({ ...profile })
    setEditing(true)
    setConflicts([])
    setErrFields(new Set())
    setSaveErr('')
  }

  function cancelEdit() { setEditing(false); setDraft({}); setConflicts([]); setErrFields(new Set()) }

  function toggleGoal(val: string) {
    const cur = (draft.goal_type as string[]) ?? []
    setDraft(d => ({ ...d, goal_type: cur.includes(val) ? cur.filter(v => v !== val) : [...cur, val] }))
    setConflicts([]); setErrFields(new Set())
  }

  async function handleSave() {
    const c = checkConflicts(draft)
    if (c.length > 0) {
      setConflicts(c)
      setErrFields(new Set(c.flatMap(x => x.fields)))
      return
    }
    setSaving(true)
    setSaveErr('')
    try {
      await profileApi.save(draft)
      const genResult = await profileApi.generate()
      const updated = await profileApi.get()
      onSaved({ ...updated, ...genResult } as UserProfile)
      setEditing(false)
    } catch (e) {
      setSaveErr(`保存失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSaving(false)
    }
  }

  const goalType = (profile.goal_type as string[]) ?? []
  const summary = `目标收益 ${profile.target_return ?? '—'} | 最大回撤 ${profile.max_drawdown ?? '—'}`

  return (
    <div style={{ background:'#fff', borderRadius:12, border:'1px solid #E5E7EB', overflow:'hidden' }}>
      <div
        style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'14px 18px', cursor:'pointer' }}
        onClick={() => !editing && setOpen(o => !o)}
      >
        <div>
          <span style={{ fontSize:14, fontWeight:700, color:'#1B2A4A' }}>投资目标</span>
          {!open && <span style={{ fontSize:12, color:'#6B7280', marginLeft:10 }}>{summary}</span>}
        </div>
        <div style={{ display:'flex', alignItems:'center', gap:8 }}>
          {open && !editing && (
            <button
              onClick={e => { e.stopPropagation(); startEdit() }}
              style={{ display:'flex', alignItems:'center', gap:4, padding:'5px 12px', borderRadius:6, fontSize:12, fontWeight:500, background:'#F3F4F6', color:'#374151', border:'none', cursor:'pointer' }}
            >
              <Pencil size={12} />修改
            </button>
          )}
          {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </div>
      </div>

      {open && (
        <div style={{ padding:'0 18px 18px', borderTop:'1px solid #F3F4F6' }}>
          {editing ? (
            <div style={{ paddingTop:14 }}>
              {/* goal_type */}
              <div style={{ marginBottom:14 }}>
                <label style={{ fontSize:12, fontWeight:500, color:'#374151', display:'block', marginBottom:6 }}>投资目标（可多选）</label>
                <div style={{ display:'flex', flexWrap:'wrap', gap:10 }}>
                  {GOAL_TYPES.map(opt => (
                    <label key={opt} style={{ display:'flex', alignItems:'center', fontSize:12, cursor:'pointer' }}>
                      <input type="checkbox" checked={((draft.goal_type as string[]) ?? []).includes(opt)} onChange={() => toggleGoal(opt)} style={S.checkbox} />
                      {opt}
                    </label>
                  ))}
                </div>
              </div>
              {/* target_return */}
              <div style={{ marginBottom:14 }}>
                <label style={{ fontSize:12, fontWeight:500, color:'#374151', display:'block', marginBottom:4 }}>目标收益率</label>
                <select value={draft.target_return ?? ''} onChange={e => { setDraft(d => ({ ...d, target_return: e.target.value })); setConflicts([]); setErrFields(new Set()) }} style={errFields.has('target_return') ? S.selectErr : S.select}>
                  <option value="">请选择</option>
                  {TARGET_RETURN_OPTIONS.map(o => <option key={o} value={o}>{o}</option>)}
                </select>
                {errFields.has('target_return') && <div style={S.errMsg}>请调整目标收益率</div>}
              </div>
              {/* max_drawdown */}
              <div style={{ marginBottom:14 }}>
                <label style={{ fontSize:12, fontWeight:500, color:'#374151', display:'block', marginBottom:4 }}>最大可接受回撤</label>
                <select value={draft.max_drawdown ?? ''} onChange={e => { setDraft(d => ({ ...d, max_drawdown: e.target.value })); setConflicts([]); setErrFields(new Set()) }} style={errFields.has('max_drawdown') ? S.selectErr : S.select}>
                  <option value="">请选择</option>
                  {MAX_DRAWDOWN_OPTIONS.map(o => <option key={o} value={o}>{o}</option>)}
                </select>
                {errFields.has('max_drawdown') && <div style={S.errMsg}>请调整最大回撤</div>}
              </div>
              {/* investment_horizon */}
              <div style={{ marginBottom:14 }}>
                <label style={{ fontSize:12, fontWeight:500, color:'#374151', display:'block', marginBottom:4 }}>投资期限</label>
                <select value={draft.investment_horizon ?? ''} onChange={e => setDraft(d => ({ ...d, investment_horizon: e.target.value }))} style={S.select}>
                  <option value="">请选择</option>
                  {HORIZON_OPTIONS.map(o => <option key={o} value={o}>{o}</option>)}
                </select>
              </div>
              {/* 冲突提示 */}
              {conflicts.map((c, i) => (
                <div key={i} style={{ padding:'10px 12px', background:'#FFFBEB', border:'1px solid #FDE68A', borderRadius:8, fontSize:12, color:'#92400E', marginBottom:10 }}>
                  ⚠️ {c.message}
                </div>
              ))}
              {saveErr && <div style={{ ...S.errMsg, marginBottom:10, fontSize:13 }}>{saveErr}</div>}
              <div style={{ display:'flex', gap:8 }}>
                <button onClick={handleSave} disabled={saving} style={{ padding:'8px 20px', borderRadius:8, fontSize:13, fontWeight:600, background:'linear-gradient(135deg,#3B82F6,#1D4ED8)', color:'#fff', border:'none', cursor:'pointer', display:'flex', alignItems:'center', gap:4 }}>
                  <Check size={13} />{saving ? '保存中...' : '保存'}
                </button>
                <button onClick={cancelEdit} style={{ padding:'8px 16px', borderRadius:8, fontSize:13, background:'#F3F4F6', color:'#374151', border:'none', cursor:'pointer', display:'flex', alignItems:'center', gap:4 }}>
                  <X size={13} />取消
                </button>
              </div>
            </div>
          ) : (
            <div style={{ paddingTop:14 }}>
              <Row label="投资目标" value={goalType.length ? goalType.join('、') : '—'} />
              <Row label="目标收益率" value={profile.target_return} />
              <Row label="最大回撤" value={profile.max_drawdown} />
              <Row label="投资期限" value={profile.investment_horizon} />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function Row({ label, value }: { label: string; value?: string | null }) {
  return (
    <div style={{ display:'flex', justifyContent:'space-between', padding:'6px 0', borderBottom:'1px solid #F9FAFB', fontSize:13 }}>
      <span style={{ color:'#6B7280' }}>{label}</span>
      <span style={{ fontWeight:500, color:'#1B2A4A' }}>{value ?? '—'}</span>
    </div>
  )
}

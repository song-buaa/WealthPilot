/**
 * ResultCardA — 风险评估 + 基础信息卡片（可展开/编辑）
 */
import React, { useState } from 'react'
import { ChevronDown, ChevronUp, Pencil, Check, X } from 'lucide-react'
import { profileApi, type UserProfile } from '@/lib/api'

const RISK_TYPE: Record<number, string> = { 1: '保守型', 2: '稳健型', 3: '平衡型', 4: '成长型', 5: '进取型' }

const RISK_SOURCE_OPTIONS = [
  { value: 'bank',   label: '银行（A1-A5）' },
  { value: 'broker', label: '券商（C1-C5）' },
  { value: 'custom', label: '自定义（低/中/高）' },
]

function getRiskLevelOpts(source?: string) {
  if (source === 'bank')   return ['A1','A2','A3','A4','A5']
  if (source === 'broker') return ['C1','C2','C3','C4','C5']
  if (source === 'custom') return ['低','中','高']
  return []
}

function normalizeRisk(source: string, original: string): number {
  if (source === 'bank')   { const m: Record<string, number> = {A1:1,A2:2,A3:3,A4:4,A5:5}; return m[original] ?? 3 }
  if (source === 'broker') { const m: Record<string, number> = {C1:1,C2:1,C3:2,C4:3,C5:4}; return m[original] ?? 3 }
  if (source === 'custom') { const m: Record<string, number> = {'低':2,'中':3,'高':4}; return m[original] ?? 3 }
  return 3
}

const FIELD_OPTS: Record<string, string[]> = {
  income_level:          ['<10万','10-30万','30-100万','>100万'],
  income_stability:      ['稳定','较稳定','波动'],
  total_assets:          ['<50万','50-200万','200-500万','>500万'],
  investable_ratio:      ['<20%','20-50%','50-80%','>80%'],
  liability_level:       ['无','低','中','高'],
  family_status:         ['单身','已婚无子','已婚有子','退休'],
  asset_structure:       ['现金为主','固收为主','股票基金为主','多元配置'],
  investment_motivation: ['新增资金','调整配置','市场波动调整','长期规划'],
  fund_usage_timeline:   ['1年内','1-3年','3年以上','不确定'],
}

const FIELD_LABELS: Record<string, string> = {
  income_level:'年收入', income_stability:'收入稳定性', total_assets:'总资产',
  investable_ratio:'可投资占比', liability_level:'负债水平', family_status:'家庭状态',
  asset_structure:'资产结构', investment_motivation:'投资动机', fund_usage_timeline:'资金使用',
}

const REQUIRED = ['risk_normalized_level','income_stability','asset_structure','fund_usage_timeline']

const S = {
  select: { width:'100%', padding:'6px 8px', border:'1px solid #E5E7EB', borderRadius:6, fontSize:12 } as React.CSSProperties,
  selectErr: { width:'100%', padding:'6px 8px', border:'1px solid #EF4444', borderRadius:6, fontSize:12 } as React.CSSProperties,
  errMsg: { fontSize:11, color:'#EF4444', marginTop:2 } as React.CSSProperties,
}

interface Props {
  profile: UserProfile
  onSaved: (updated: UserProfile) => void
}

export default function ResultCardA({ profile, onSaved }: Props) {
  const [open, setOpen]       = useState(true)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft]     = useState<Partial<UserProfile>>({})
  const [saving, setSaving]   = useState(false)
  const [errors, setErrors]   = useState<Record<string, string>>({})

  function startEdit() {
    setDraft({ ...profile })
    setEditing(true)
    setErrors({})
  }

  function cancelEdit() {
    setEditing(false)
    setDraft({})
    setErrors({})
  }

  function handleRisk(key: 'risk_source' | 'risk_original_level', value: string) {
    const src  = key === 'risk_source'         ? value : (draft.risk_source ?? '')
    const orig = key === 'risk_original_level' ? value : (draft.risk_original_level ?? '')
    const patch: Partial<UserProfile> = { [key]: value }
    if (src && orig) {
      const lvl = normalizeRisk(src, orig)
      patch.risk_normalized_level = lvl
      patch.risk_type = RISK_TYPE[lvl]
    }
    setDraft(d => ({ ...d, ...patch }))
  }

  async function handleSave() {
    const errs: Record<string, string> = {}
    for (const f of REQUIRED) {
      if (!(draft as Record<string, unknown>)[f]) errs[f] = '请填写该字段'
    }
    if (Object.keys(errs).length > 0) { setErrors(errs); return }

    setSaving(true)
    try {
      await profileApi.save(draft)
      const genResult = await profileApi.generate()
      const updated = await profileApi.get()
      onSaved({ ...updated, ...genResult } as UserProfile)
      setEditing(false)
    } catch (e) {
      setErrors({ _global: `保存失败：${e instanceof Error ? e.message : String(e)}` })
    } finally {
      setSaving(false)
    }
  }

  const riskLabel = profile.risk_normalized_level
    ? `R${profile.risk_normalized_level} ${RISK_TYPE[profile.risk_normalized_level]}`
    : '未设置'

  return (
    <div style={{ background:'#fff', borderRadius:12, border:'1px solid #E5E7EB', overflow:'hidden' }}>
      {/* 标题栏 */}
      <div
        style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'14px 18px', cursor:'pointer' }}
        onClick={() => !editing && setOpen(o => !o)}
      >
        <div>
          <span style={{ fontSize:14, fontWeight:700, color:'#1B2A4A' }}>风险评估 + 基础信息</span>
          {!open && <span style={{ fontSize:12, color:'#6B7280', marginLeft:10 }}>{riskLabel}</span>}
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

      {/* 展开内容 */}
      {open && (
        <div style={{ padding:'0 18px 18px', borderTop:'1px solid #F3F4F6' }}>
          {editing ? (
            <div style={{ paddingTop:14 }}>
              {/* 风险评估来源 */}
              <div style={{ marginBottom:14 }}>
                <label style={{ fontSize:12, fontWeight:500, color:'#374151', display:'block', marginBottom:4 }}>风险评估来源</label>
                <select value={draft.risk_source ?? ''} onChange={e => handleRisk('risk_source', e.target.value)} style={S.select}>
                  <option value="">请选择</option>
                  {RISK_SOURCE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
              {draft.risk_source && (
                <div style={{ marginBottom:14 }}>
                  <label style={{ fontSize:12, fontWeight:500, color:'#374151', display:'block', marginBottom:4 }}>原始风险等级</label>
                  <select value={draft.risk_original_level ?? ''} onChange={e => handleRisk('risk_original_level', e.target.value)} style={S.select}>
                    <option value="">请选择</option>
                    {getRiskLevelOpts(draft.risk_source).map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                </div>
              )}
              {draft.risk_normalized_level && (
                <div style={{ marginBottom:14, padding:'8px 12px', background:'#EFF6FF', borderRadius:8, fontSize:13 }}>
                  标准化风险等级：<strong>R{draft.risk_normalized_level} {RISK_TYPE[draft.risk_normalized_level]}</strong>
                  {errors.risk_normalized_level && <div style={S.errMsg}>{errors.risk_normalized_level}</div>}
                </div>
              )}
              {/* 其他字段 */}
              {Object.entries(FIELD_OPTS).map(([key, opts]) => (
                <div key={key} style={{ marginBottom:14 }}>
                  <label style={{ fontSize:12, fontWeight:500, color:'#374151', display:'block', marginBottom:4 }}>
                    {FIELD_LABELS[key]}{REQUIRED.includes(key) && <span style={{ color:'#EF4444', marginLeft:2 }}>*</span>}
                  </label>
                  <select
                    value={(draft as Record<string, unknown>)[key] as string ?? ''}
                    onChange={e => setDraft(d => ({ ...d, [key]: e.target.value }))}
                    style={errors[key] ? S.selectErr : S.select}
                  >
                    <option value="">请选择</option>
                    {opts.map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                  {errors[key] && <div style={S.errMsg}>{errors[key]}</div>}
                </div>
              ))}
              {errors._global && <div style={{ ...S.errMsg, marginBottom:12, fontSize:13 }}>{errors._global}</div>}
              <div style={{ display:'flex', gap:8, marginTop:4 }}>
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
              <Row label="风险评估来源" value={profile.risk_source === 'bank' ? '银行' : profile.risk_source === 'broker' ? '券商' : '自定义'} />
              <Row label="原始等级" value={profile.risk_original_level} />
              <Row label="标准化等级" value={riskLabel} highlight />
              {Object.keys(FIELD_OPTS).map(key => (
                <Row key={key} label={FIELD_LABELS[key]} value={(profile as Record<string, unknown>)[key] as string} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function Row({ label, value, highlight }: { label: string; value?: string | null; highlight?: boolean }) {
  return (
    <div style={{ display:'flex', justifyContent:'space-between', padding:'6px 0', borderBottom:'1px solid #F9FAFB', fontSize:13 }}>
      <span style={{ color:'#6B7280' }}>{label}</span>
      <span style={{ fontWeight: highlight ? 700 : 500, color: highlight ? '#1D4ED8' : '#1B2A4A' }}>{value ?? '—'}</span>
    </div>
  )
}

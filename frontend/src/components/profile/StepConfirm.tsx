/**
 * Step 6 — 确认与修改
 * 三组卡片展示：风险画像 / 基础信息 / 投资目标
 * 每个字段右侧铅笔图标可 inline 编辑
 * 任意字段修改后自动调 POST /api/profile/generate
 */
import React, { useState } from 'react'
import { Pencil, Check, X } from 'lucide-react'
import { profileApi, type UserProfile } from '@/lib/api'

const S = {
  card: {
    background: '#fff', border: '1px solid #E5E7EB',
    borderRadius: 12, padding: 16, marginBottom: 12,
  } as React.CSSProperties,
  sectionTitle: {
    fontSize: 12, fontWeight: 700, color: '#374151', marginBottom: 12,
    display: 'flex', alignItems: 'center', gap: 6,
  } as React.CSSProperties,
  row: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '6px 0', borderBottom: '1px solid #F3F4F6',
  } as React.CSSProperties,
  fieldLabel: { fontSize: 12, color: '#6B7280', minWidth: 90 } as React.CSSProperties,
  fieldValue: { fontSize: 13, fontWeight: 500, color: '#1B2A4A', flex: 1, textAlign: 'right' as const } as React.CSSProperties,
  select: {
    padding: '4px 8px', border: '1px solid #3B82F6', borderRadius: 6,
    fontSize: 12, color: '#1B2A4A', background: '#fff',
  } as React.CSSProperties,
}

// ── 字段配置 ─────────────────────────────────────────────────────────────────

interface FieldDef {
  key: keyof UserProfile
  label: string
  options: string[]
}

const BASIC_FIELDS: FieldDef[] = [
  { key: 'total_assets',          label: '总资产规模',    options: ['<50万','50-200万','200-500万','>500万'] },
  { key: 'income_level',          label: '年收入',        options: ['<10万','10-30万','30-100万','>100万'] },
  { key: 'income_stability',      label: '收入稳定性',    options: ['稳定','较稳定','波动'] },
  { key: 'investable_ratio',      label: '可投资比例',    options: ['<20%','20-50%','50-80%','>80%'] },
  { key: 'liability_level',       label: '负债水平',      options: ['无','低','中','高'] },
  { key: 'family_status',         label: '家庭状态',      options: ['单身','已婚无子','已婚有子','退休'] },
  { key: 'asset_structure',       label: '资产结构',      options: ['现金为主','固收为主','股票基金为主','多元配置'] },
  { key: 'investment_motivation', label: '投资动机',      options: ['新增资金','调整配置','市场波动调整','长期规划'] },
  { key: 'fund_usage_timeline',   label: '资金使用时间',  options: ['1年内','1-3年','3年以上','不确定'] },
]

const GOAL_FIELDS: FieldDef[] = [
  { key: 'goal_type',          label: '投资目标',    options: ['资本增值','稳健增长','保值','现金流'] },
  { key: 'target_return',      label: '目标收益率',  options: ['<5%','5-10%','10-20%','>20%'] },
  { key: 'max_drawdown',       label: '最大回撤',    options: ['<5%','5-15%','15-30%','>30%'] },
  { key: 'investment_horizon', label: '投资期限',    options: ['<1年','1-3年','3-5年','>5年'] },
]

const RISK_LEVELS: Record<number, string> = { 1:'保守型', 2:'稳健型', 3:'平衡型', 4:'成长型', 5:'进取型' }

interface Props {
  data: Partial<UserProfile>
  onChange: (patch: Partial<UserProfile>) => void
  onNext: () => void
  onPrev: () => void
}

export default function StepConfirm({ data, onChange, onNext, onPrev }: Props) {
  const [editingKey, setEditingKey] = useState<keyof UserProfile | null>(null)
  const [editValue, setEditValue]   = useState<string>('')

  async function saveEdit(key: keyof UserProfile) {
    const patch: Partial<UserProfile> = {}
    if (key === 'goal_type') {
      // goal_type 存为数组（单选模式，inline 只允许修改单个值）
      ;(patch as Record<string, unknown>)[key] = [editValue]
    } else {
      ;(patch as Record<string, unknown>)[key] = editValue
    }
    onChange(patch)
    setEditingKey(null)
    // 自动重新生成 AI 画像
    try {
      await profileApi.save({ ...data, ...patch })
      await profileApi.generate()
    } catch {
      // 生成失败不阻断流程
    }
  }

  function startEdit(key: keyof UserProfile, current: string) {
    setEditingKey(key)
    setEditValue(current)
  }

  function renderValue(key: keyof UserProfile): string {
    const v = data[key]
    if (!v) return '—'
    if (Array.isArray(v)) return v.join('、')
    if (typeof v === 'number') return String(v)
    return String(v)
  }

  function FieldRow({ fieldDef }: { fieldDef: FieldDef }) {
    const { key, label, options } = fieldDef
    const isEditing = editingKey === key
    const displayVal = renderValue(key)

    return (
      <div style={S.row}>
        <span style={S.fieldLabel}>{label}</span>
        {isEditing ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <select value={editValue} onChange={e => setEditValue(e.target.value)} style={S.select}>
              {options.map(o => <option key={o} value={o}>{o}</option>)}
            </select>
            <button onClick={() => saveEdit(key)} style={{ padding: '4px 6px', border: 'none', borderRadius: 5, background: '#3B82F6', color: '#fff', cursor: 'pointer' }}>
              <Check size={12} />
            </button>
            <button onClick={() => setEditingKey(null)} style={{ padding: '4px 6px', border: 'none', borderRadius: 5, background: '#E5E7EB', color: '#374151', cursor: 'pointer' }}>
              <X size={12} />
            </button>
          </div>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={S.fieldValue}>{displayVal}</span>
            <button onClick={() => startEdit(key, Array.isArray(data[key]) ? (data[key] as string[])[0] ?? '' : String(data[key] ?? ''))} style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#9CA3AF', padding: 2 }}>
              <Pencil size={12} />
            </button>
          </div>
        )}
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {/* 风险画像 */}
      <div style={S.card}>
        <div style={S.sectionTitle}>📊 风险画像</div>
        <div style={S.row}>
          <span style={S.fieldLabel}>来源</span>
          <span style={S.fieldValue}>{data.risk_source === 'external' ? `外部（${data.risk_provider ?? ''}）` : 'AI 评估'}</span>
        </div>
        <div style={S.row}>
          <span style={S.fieldLabel}>风险等级</span>
          <span style={{ ...S.fieldValue, color: '#3B82F6', fontWeight: 700 }}>
            R{data.risk_normalized_level ?? '—'} {data.risk_normalized_level ? RISK_LEVELS[data.risk_normalized_level] : ''}
          </span>
        </div>
        {data.risk_original_level && (
          <div style={S.row}>
            <span style={S.fieldLabel}>原始等级</span>
            <span style={S.fieldValue}>{data.risk_original_level}</span>
          </div>
        )}
      </div>

      {/* 基础信息 */}
      <div style={S.card}>
        <div style={S.sectionTitle}>👤 基础信息</div>
        {BASIC_FIELDS.map(f => <FieldRow key={f.key} fieldDef={f} />)}
      </div>

      {/* 投资目标 */}
      <div style={S.card}>
        <div style={S.sectionTitle}>🎯 投资目标</div>
        {GOAL_FIELDS.map(f => <FieldRow key={f.key} fieldDef={f} />)}
      </div>

      <div style={{ display: 'flex', gap: 10, marginTop: 8 }}>
        <button onClick={onPrev} style={{ padding: '8px 20px', borderRadius: 8, fontSize: 13, fontWeight: 500, background: '#fff', color: '#374151', border: '1px solid #E5E7EB', cursor: 'pointer' }}>上一步</button>
        <button onClick={onNext} style={{
          padding: '8px 24px', borderRadius: 8, fontSize: 13, fontWeight: 500,
          background: 'linear-gradient(135deg,#3B82F6,#1D4ED8)', color: '#fff', border: 'none', cursor: 'pointer',
        }}>生成画像</button>
      </div>
    </div>
  )
}

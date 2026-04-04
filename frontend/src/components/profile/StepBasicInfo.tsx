/**
 * Step 2 — 基础信息
 * 9 个字段全部用 <select>，必填：income_stability
 */
import React from 'react'
import type { UserProfile } from '@/lib/api'

const S = {
  row: { display: 'flex', flexDirection: 'column', gap: 4 } as React.CSSProperties,
  label: { fontSize: 12, fontWeight: 500, color: '#374151' } as React.CSSProperties,
  required: { color: '#EF4444', marginLeft: 2 } as React.CSSProperties,
  optional: { fontSize: 11, color: '#9CA3AF', marginLeft: 4 } as React.CSSProperties,
  select: {
    width: '100%', padding: '8px 10px', border: '1px solid #E5E7EB',
    borderRadius: 8, fontSize: 13, color: '#1B2A4A', background: '#fff', cursor: 'pointer',
  } as React.CSSProperties,
}

interface Field {
  key: keyof UserProfile
  label: string
  required?: boolean
  options: string[]
}

const FIELDS: Field[] = [
  { key: 'total_assets',          label: '总资产规模',                     options: ['<50万','50-200万','200-500万','>500万'] },
  { key: 'income_level',          label: '年收入水平',                     options: ['<10万','10-30万','30-100万','>100万'] },
  { key: 'income_stability',      label: '收入稳定性',    required: true,  options: ['稳定','较稳定','波动'] },
  { key: 'investable_ratio',      label: '可投资资产占比',                 options: ['<20%','20-50%','50-80%','>80%'] },
  { key: 'liability_level',       label: '负债水平',                       options: ['无','低','中','高'] },
  { key: 'family_status',         label: '家庭状态',                       options: ['单身','已婚无子','已婚有子','退休'] },
  { key: 'asset_structure',       label: '现有资产结构',                   options: ['现金为主','固收为主','股票基金为主','多元配置'] },
  { key: 'investment_motivation', label: '本次投资动机',                   options: ['新增资金','调整配置','市场波动调整','长期规划'] },
  { key: 'fund_usage_timeline',   label: '资金使用时间',                   options: ['1年内','1-3年','3年以上','不确定'] },
]

interface Props {
  data: Partial<UserProfile>
  onChange: (patch: Partial<UserProfile>) => void
  onNext: () => void
  onPrev: () => void
}

export default function StepBasicInfo({ data, onChange, onNext, onPrev }: Props) {
  const requiredFilled = ['income_stability'].every(
    k => !!(data as Record<string, unknown>)[k]
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {FIELDS.map(({ key, label, required, options }) => (
        <div key={key} style={S.row}>
          <div style={S.label}>
            {label}
            {required ? <span style={S.required}>*</span> : <span style={S.optional}>（可跳过）</span>}
          </div>
          <select
            value={(data[key] as string) ?? ''}
            onChange={(e) => onChange({ [key]: e.target.value || undefined })}
            style={S.select}
          >
            <option value="">请选择</option>
            {options.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
        </div>
      ))}

      <div style={{ display: 'flex', gap: 10, marginTop: 6 }}>
        <button onClick={onPrev} style={{
          padding: '8px 20px', borderRadius: 8, fontSize: 13, fontWeight: 500,
          background: '#fff', color: '#374151', border: '1px solid #E5E7EB', cursor: 'pointer',
        }}>上一步</button>
        <button
          onClick={onNext}
          disabled={!requiredFilled}
          style={{
            padding: '8px 24px', borderRadius: 8, fontSize: 13, fontWeight: 500,
            background: requiredFilled ? 'linear-gradient(135deg,#3B82F6,#1D4ED8)' : '#E5E7EB',
            color: requiredFilled ? '#fff' : '#9CA3AF', border: 'none', cursor: requiredFilled ? 'pointer' : 'not-allowed',
          }}
        >下一步</button>
      </div>
    </div>
  )
}

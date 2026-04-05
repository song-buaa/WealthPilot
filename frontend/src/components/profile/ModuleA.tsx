/**
 * ModuleA — 风险评估 + 基础信息
 * 支持截图上传（多张）或文本输入两种方式调 AI 提取
 * 提取完成后显示可编辑字段列表
 * 必填：risk_normalized_level, income_stability, asset_structure, fund_usage_timeline
 */
import React, { useRef, useState } from 'react'
import { Loader2, Upload, MessageSquare } from 'lucide-react'
import { profileApi, type UserProfile } from '@/lib/api'

// ── 风险等级标准化（与后端保持一致）───────────────────────────────────────────

const RISK_TYPE: Record<number, string> = { 1: '保守型', 2: '稳健型', 3: '平衡型', 4: '成长型', 5: '进取型' }

function normalizeRisk(source: string, original: string): number {
  if (source === 'bank') {
    const m: Record<string, number> = { A1: 1, A2: 2, A3: 3, A4: 4, A5: 5 }
    return m[original] ?? 3
  }
  if (source === 'broker') {
    const m: Record<string, number> = { C1: 1, C2: 1, C3: 2, C4: 3, C5: 4 }
    return m[original] ?? 3
  }
  if (source === 'custom') {
    const m: Record<string, number> = { '低': 2, '中': 3, '高': 4 }
    return m[original] ?? 3
  }
  return 3
}

// ── 字段枚举值 ────────────────────────────────────────────────────────────────

const RISK_SOURCE_OPTIONS = [
  { value: 'bank',   label: '银行（A1-A5）' },
  { value: 'broker', label: '券商（C1-C5）' },
  { value: 'custom', label: '自定义（低/中/高）' },
]

function getRiskLevelOptions(source: string): string[] {
  if (source === 'bank')   return ['A1', 'A2', 'A3', 'A4', 'A5']
  if (source === 'broker') return ['C1', 'C2', 'C3', 'C4', 'C5']
  if (source === 'custom') return ['低', '中', '高']
  return []
}

const FIELD_OPTIONS: Record<string, string[]> = {
  income_level:          ['<10万', '10-30万', '30-100万', '>100万'],
  income_stability:      ['稳定', '较稳定', '波动'],
  total_assets:          ['<50万', '50-200万', '200-500万', '>500万'],
  investable_ratio:      ['<20%', '20-50%', '50-80%', '>80%'],
  liability_level:       ['无', '低', '中', '高'],
  family_status:         ['单身', '已婚无子', '已婚有子', '退休'],
  asset_structure:       ['现金为主', '固收为主', '股票基金为主', '多元配置'],
  investment_motivation: ['新增资金', '调整配置', '市场波动调整', '长期规划'],
  fund_usage_timeline:   ['1年内', '1-3年', '3年以上', '不确定'],
}

const FIELD_LABELS: Record<string, string> = {
  risk_source:           '风险来源',
  risk_original_level:   '原始风险等级',
  risk_normalized_level: '标准化风险等级',
  income_level:          '年收入水平',
  income_stability:      '收入稳定性',
  total_assets:          '总资产规模',
  investable_ratio:      '可投资资产占比',
  liability_level:       '负债水平',
  family_status:         '家庭状态',
  asset_structure:       '现有资产结构',
  investment_motivation: '本次投资动机',
  fund_usage_timeline:   '资金使用时间',
}

const REQUIRED_FIELDS = ['risk_normalized_level', 'income_stability', 'asset_structure', 'fund_usage_timeline']

// ── 样式常量 ──────────────────────────────────────────────────────────────────

const S = {
  section: { marginBottom: 20 } as React.CSSProperties,
  label:   { fontSize: 12, fontWeight: 500, color: '#374151', marginBottom: 4, display: 'block' } as React.CSSProperties,
  select:  { width: '100%', padding: '8px 10px', border: '1px solid #E5E7EB', borderRadius: 8, fontSize: 13, color: '#1B2A4A', background: '#fff', cursor: 'pointer' } as React.CSSProperties,
  selectErr: { width: '100%', padding: '8px 10px', border: '1px solid #EF4444', borderRadius: 8, fontSize: 13, color: '#1B2A4A', background: '#fff', cursor: 'pointer' } as React.CSSProperties,
  badge:   { display: 'inline-block', padding: '2px 10px', borderRadius: 20, fontSize: 12, background: '#EFF6FF', color: '#1D4ED8', fontWeight: 600, marginLeft: 6 } as React.CSSProperties,
  errMsg:  { fontSize: 11, color: '#EF4444', marginTop: 2 } as React.CSSProperties,
  tabBtn:  (active: boolean) => ({
    padding: '7px 18px', borderRadius: 8, fontSize: 13, fontWeight: 500, cursor: 'pointer',
    background: active ? '#3B82F6' : '#F3F4F6',
    color: active ? '#fff' : '#374151',
    border: 'none',
  } as React.CSSProperties),
  btn: { padding: '9px 24px', borderRadius: 8, fontSize: 13, fontWeight: 600, background: 'linear-gradient(135deg,#3B82F6,#1D4ED8)', color: '#fff', border: 'none', cursor: 'pointer' } as React.CSSProperties,
  btnGray: { padding: '9px 24px', borderRadius: 8, fontSize: 13, fontWeight: 500, background: '#E5E7EB', color: '#9CA3AF', border: 'none', cursor: 'not-allowed' } as React.CSSProperties,
}

// ── 组件 ──────────────────────────────────────────────────────────────────────

interface Props {
  data:      Partial<UserProfile>
  onChange:  (patch: Partial<UserProfile>) => void
  onConfirm: () => void
}

export default function ModuleA({ data, onChange, onConfirm }: Props) {
  const [mode, setMode]         = useState<'image' | 'text'>('image')
  const [text, setText]         = useState('')
  const [files, setFiles]       = useState<File[]>([])
  const [loading, setLoading]   = useState(false)
  const [extracted, setExtracted] = useState(false)
  const [errors, setErrors]     = useState<Record<string, string>>({})
  const fileRef = useRef<HTMLInputElement>(null)

  // ── 提取 ──────────────────────────────────────────────────────────────────

  async function handleExtract() {
    setLoading(true)
    setErrors({})
    try {
      let result
      if (mode === 'image') {
        const images = await Promise.all(files.map(fileToBase64))
        result = await profileApi.extract({ type: 'images', images, existing_fields: {} })
      } else {
        result = await profileApi.extract({ type: 'text', text, existing_fields: {} })
      }
      const patch: Partial<UserProfile> = {}
      for (const [k, v] of Object.entries(result.extracted ?? {})) {
        if (v !== null && v !== undefined) {
          (patch as Record<string, unknown>)[k] = v
        }
      }
      // 如果提取到 risk_source 和 risk_original_level，本地计算标准化等级
      const src = (patch.risk_source ?? data.risk_source) as string
      const orig = (patch.risk_original_level ?? data.risk_original_level) as string
      if (src && orig) {
        patch.risk_normalized_level = normalizeRisk(src, orig)
        patch.risk_type = RISK_TYPE[patch.risk_normalized_level]
      }
      onChange(patch)
      setExtracted(true)
    } catch (e) {
      setErrors({ _global: String(e) })
    } finally {
      setLoading(false)
    }
  }

  // ── 确认校验 ──────────────────────────────────────────────────────────────

  function handleConfirm() {
    const errs: Record<string, string> = {}
    for (const f of REQUIRED_FIELDS) {
      if (!(data as Record<string, unknown>)[f]) {
        errs[f] = '请填写该字段'
      }
    }
    if (Object.keys(errs).length > 0) {
      setErrors(errs)
      return
    }
    setErrors({})
    onConfirm()
  }

  // ── risk_original_level / risk_normalized_level 联动 ─────────────────────

  function handleRiskChange(key: 'risk_source' | 'risk_original_level', value: string) {
    const patch: Partial<UserProfile> = { [key]: value }
    const src  = key === 'risk_source'           ? value : (data.risk_source ?? '')
    const orig = key === 'risk_original_level'   ? value : (data.risk_original_level ?? '')
    if (src && orig) {
      const lvl = normalizeRisk(src, orig)
      patch.risk_normalized_level = lvl
      patch.risk_type = RISK_TYPE[lvl]
    }
    onChange(patch)
    setErrors(e => { const n = { ...e }; delete n[key]; delete n.risk_normalized_level; return n })
  }

  function handleFieldChange(key: string, value: string) {
    onChange({ [key]: value } as Partial<UserProfile>)
    setErrors(e => { const n = { ...e }; delete n[key]; return n })
  }

  const canExtract = mode === 'image' ? files.length > 0 : text.trim().length > 0

  return (
    <div>
      {/* 输入方式切换 */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
        <button style={S.tabBtn(mode === 'image')} onClick={() => setMode('image')}>
          <Upload size={13} style={{ marginRight: 4, verticalAlign: 'middle' }} />截图上传
        </button>
        <button style={S.tabBtn(mode === 'text')} onClick={() => setMode('text')}>
          <MessageSquare size={13} style={{ marginRight: 4, verticalAlign: 'middle' }} />文字描述
        </button>
      </div>

      {/* 输入区 */}
      {mode === 'image' ? (
        <div style={{ marginBottom: 16 }}>
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            multiple
            style={{ display: 'none' }}
            onChange={e => setFiles(Array.from(e.target.files ?? []))}
          />
          <div
            onClick={() => fileRef.current?.click()}
            style={{
              border: '2px dashed #D1D5DB', borderRadius: 10, padding: '24px 16px',
              textAlign: 'center', cursor: 'pointer', background: '#F9FAFB',
              color: '#6B7280', fontSize: 13,
            }}
          >
            {files.length > 0
              ? `已选择 ${files.length} 张图片：${files.map(f => f.name).join('、')}`
              : '点击选择截图（可多选），支持银行/券商风险评估报告'
            }
          </div>
        </div>
      ) : (
        <textarea
          value={text}
          onChange={e => setText(e.target.value)}
          placeholder="请描述您的风险评估情况、收入资产状况等，例如：我在工商银行做了风险测评，结果是A3稳健型，年收入30万左右..."
          style={{
            width: '100%', minHeight: 100, padding: '10px 12px',
            border: '1px solid #E5E7EB', borderRadius: 8, fontSize: 13,
            resize: 'vertical', fontFamily: 'inherit', boxSizing: 'border-box',
          }}
        />
      )}

      {/* 提取按钮 */}
      <div style={{ marginBottom: 24 }}>
        <button
          onClick={handleExtract}
          disabled={!canExtract || loading}
          style={canExtract && !loading ? S.btn : S.btnGray}
        >
          {loading
            ? <><Loader2 size={14} style={{ marginRight: 6, verticalAlign: 'middle', animation: 'spin 1s linear infinite' }} />AI 解析中...</>
            : 'AI 解析'}
        </button>
        {errors._global && <div style={{ ...S.errMsg, marginTop: 6 }}>{errors._global}</div>}
      </div>

      {/* 字段列表（始终显示，AI解析后填入值） */}
      <div style={{ borderTop: '1px solid #F3F4F6', paddingTop: 16 }}>
        <div style={{ fontSize: 12, color: '#9CA3AF', marginBottom: 12 }}>
          {extracted ? 'AI 解析完成，请确认并补充未识别字段' : '也可直接手动填写以下字段'}
        </div>

        {/* 风险来源 */}
        <div style={S.section}>
          <label style={S.label}>风险来源</label>
          <select
            value={data.risk_source ?? ''}
            onChange={e => handleRiskChange('risk_source', e.target.value)}
            style={S.select}
          >
            <option value="">请选择</option>
            {RISK_SOURCE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>

        {/* 原始风险等级 */}
        {data.risk_source && (
          <div style={S.section}>
            <label style={S.label}>原始风险等级</label>
            <select
              value={data.risk_original_level ?? ''}
              onChange={e => handleRiskChange('risk_original_level', e.target.value)}
              style={S.select}
            >
              <option value="">请选择</option>
              {getRiskLevelOptions(data.risk_source).map(o => <option key={o} value={o}>{o}</option>)}
            </select>
          </div>
        )}

        {/* 标准化风险等级（只读） */}
        {data.risk_normalized_level ? (
          <div style={{ ...S.section, background: '#EFF6FF', padding: '10px 14px', borderRadius: 8 }}>
            <span style={{ fontSize: 12, color: '#374151', fontWeight: 500 }}>标准化风险等级：</span>
            <span style={S.badge}>R{data.risk_normalized_level} {RISK_TYPE[data.risk_normalized_level]}</span>
            {errors.risk_normalized_level && <div style={S.errMsg}>{errors.risk_normalized_level}</div>}
          </div>
        ) : (
          <div style={{ ...S.section }}>
            <label style={S.label}>
              标准化风险等级
              {REQUIRED_FIELDS.includes('risk_normalized_level') && <span style={{ color: '#EF4444', marginLeft: 2 }}>*</span>}
            </label>
            <div style={{ fontSize: 13, color: '#9CA3AF', padding: '8px 0' }}>选择风险来源和等级后自动计算</div>
            {errors.risk_normalized_level && <div style={S.errMsg}>{errors.risk_normalized_level}</div>}
          </div>
        )}

        {/* 其他字段 */}
        {Object.entries(FIELD_OPTIONS).map(([key, opts]) => {
          const isRequired = REQUIRED_FIELDS.includes(key)
          const hasErr = !!errors[key]
          return (
            <div key={key} style={S.section}>
              <label style={S.label}>
                {FIELD_LABELS[key]}
                {isRequired && <span style={{ color: '#EF4444', marginLeft: 2 }}>*</span>}
              </label>
              <select
                value={(data as Record<string, unknown>)[key] as string ?? ''}
                onChange={e => handleFieldChange(key, e.target.value)}
                style={hasErr ? S.selectErr : S.select}
              >
                <option value="">{ extracted && !(data as Record<string, unknown>)[key] ? '未识别，请选择' : '请选择' }</option>
                {opts.map(o => <option key={o} value={o}>{o}</option>)}
              </select>
              {hasErr && <div style={S.errMsg}>{errors[key]}</div>}
            </div>
          )
        })}
      </div>

      {/* 确认按钮 */}
      <div style={{ marginTop: 8, paddingTop: 16, borderTop: '1px solid #F3F4F6' }}>
        <button onClick={handleConfirm} style={S.btn}>确认模块A，填写投资目标</button>
      </div>
    </div>
  )
}

// ── 工具函数 ──────────────────────────────────────────────────────────────────

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

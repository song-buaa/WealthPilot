/**
 * Step 4 — AI 对话补全（仅当有字段为 null 时显示）
 * 最多 3 轮追问，用户可随时跳过
 */
import React, { useState } from 'react'
import { Loader2, Send } from 'lucide-react'
import { profileApi, type UserProfile } from '@/lib/api'

const NULL_FIELDS: Array<keyof UserProfile> = [
  'total_assets','income_level','income_stability','investable_ratio',
  'liability_level','family_status','asset_structure','investment_motivation',
  'fund_usage_timeline','goal_type','target_return','max_drawdown','investment_horizon',
]

const FIELD_LABEL: Record<string, string> = {
  total_assets:          '总资产规模',
  income_level:          '年收入水平',
  income_stability:      '收入稳定性',
  investable_ratio:      '可投资比例',
  liability_level:       '负债水平',
  family_status:         '家庭状态',
  asset_structure:       '资产结构',
  investment_motivation: '投资动机',
  fund_usage_timeline:   '资金使用时间',
  goal_type:             '投资目标',
  target_return:         '目标收益率',
  max_drawdown:          '最大回撤容忍',
  investment_horizon:    '投资期限',
}

interface Props {
  data: Partial<UserProfile>
  onChange: (patch: Partial<UserProfile>) => void
  onNext: () => void
  onPrev: () => void
}

export default function StepAIChat({ data, onChange, onNext, onPrev }: Props) {
  const [input, setInput]           = useState('')
  const [rounds, setRounds]         = useState(0)
  const [loading, setLoading]       = useState(false)
  const [nextQ, setNextQ]           = useState<string | null>(null)
  const [extracted, setExtracted]   = useState<Partial<UserProfile>>({})

  const missingFields = NULL_FIELDS.filter(k => {
    const v = data[k]
    return v === null || v === undefined || (Array.isArray(v) && v.length === 0)
  })

  if (missingFields.length === 0) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div style={{ padding: 16, background: '#F0FDF4', borderRadius: 10, border: '1px solid #BBF7D0', fontSize: 13, color: '#166534' }}>
          所有字段已填写完整，无需 AI 补全。
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button onClick={onPrev} style={{ padding: '8px 20px', borderRadius: 8, fontSize: 13, fontWeight: 500, background: '#fff', color: '#374151', border: '1px solid #E5E7EB', cursor: 'pointer' }}>上一步</button>
          <button onClick={onNext} style={{ padding: '8px 24px', borderRadius: 8, fontSize: 13, fontWeight: 500, background: 'linear-gradient(135deg,#3B82F6,#1D4ED8)', color: '#fff', border: 'none', cursor: 'pointer' }}>下一步</button>
        </div>
      </div>
    )
  }

  async function handleSend() {
    if (!input.trim() || loading || rounds >= 3) return
    setLoading(true)
    try {
      const res = await profileApi.extract(input.trim(), data)
      const newPatch: Partial<UserProfile> = {}
      for (const [k, v] of Object.entries(res.extracted ?? {})) {
        if (v !== null && v !== undefined) {
          (newPatch as Record<string, unknown>)[k] = v
        }
      }
      setExtracted(prev => ({ ...prev, ...newPatch }))
      onChange(newPatch)
      setNextQ(res.next_question ?? null)
      setRounds(r => r + 1)
      setInput('')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* 当前已提取 */}
      {Object.keys(extracted).length > 0 && (
        <div style={{ padding: 14, background: '#F0FDF4', borderRadius: 10, border: '1px solid #BBF7D0' }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: '#166534', marginBottom: 8 }}>已提取字段</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {Object.entries(extracted).map(([k, v]) => (
              <span key={k} style={{ padding: '3px 10px', background: '#D1FAE5', borderRadius: 12, fontSize: 11, color: '#065F46' }}>
                {FIELD_LABEL[k] ?? k}：{Array.isArray(v) ? v.join('、') : String(v)}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* 还有字段缺失 */}
      {missingFields.length > 0 && rounds < 3 && (
        <div style={{ padding: 14, background: '#EFF6FF', borderRadius: 10, border: '1px solid #BFDBFE', fontSize: 13, color: '#1E40AF' }}>
          {nextQ ?? `请告诉我您的 ${missingFields.slice(0,3).map(k => FIELD_LABEL[k] ?? k).join('、')} 等信息。`}
        </div>
      )}

      {/* 输入框 */}
      {rounds < 3 && missingFields.length > 0 && (
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleSend()}
            placeholder="用自然语言描述即可，如：总资产200万，主要是股票和基金..."
            style={{
              flex: 1, padding: '9px 12px', border: '1px solid #E5E7EB',
              borderRadius: 8, fontSize: 13, color: '#1B2A4A', outline: 'none',
            }}
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || loading}
            style={{
              padding: '9px 14px', borderRadius: 8, border: 'none',
              background: 'linear-gradient(135deg,#3B82F6,#1D4ED8)', color: '#fff',
              cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4,
            }}
          >
            {loading ? <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> : <Send size={14} />}
          </button>
        </div>
      )}

      {/* 轮次提示 + 跳过 */}
      {rounds > 0 && (
        <div style={{ fontSize: 11, color: '#9CA3AF' }}>已对话 {rounds}/3 轮</div>
      )}

      <div style={{ display: 'flex', gap: 10, marginTop: 4 }}>
        <button onClick={onPrev} style={{ padding: '8px 20px', borderRadius: 8, fontSize: 13, fontWeight: 500, background: '#fff', color: '#374151', border: '1px solid #E5E7EB', cursor: 'pointer' }}>上一步</button>
        <button onClick={onNext} style={{
          padding: '8px 24px', borderRadius: 8, fontSize: 13, fontWeight: 500,
          background: 'linear-gradient(135deg,#3B82F6,#1D4ED8)', color: '#fff', border: 'none', cursor: 'pointer',
        }}>
          {missingFields.length > 0 ? '跳过，继续' : '下一步'}
        </button>
      </div>
    </div>
  )
}

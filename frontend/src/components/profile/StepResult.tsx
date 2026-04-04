/**
 * Step 7 — 画像结果
 * 展示 AI 总结、置信度 badge、风格标签
 * "保存画像"按钮 → PUT /api/profile → 跳转 /dashboard
 */
import React, { useEffect, useState } from 'react'
import { Loader2, CheckCircle } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { profileApi, type UserProfile } from '@/lib/api'

const CONFIDENCE_STYLE: Record<string, React.CSSProperties> = {
  high:   { background: '#D1FAE5', color: '#065F46' },
  medium: { background: '#FEF9C3', color: '#92400E' },
  low:    { background: '#FEE2E2', color: '#B91C1C' },
}
const CONFIDENCE_LABEL: Record<string, string> = {
  high: '高置信度', medium: '中置信度', low: '低置信度',
}
const STYLE_STYLE: Record<string, React.CSSProperties> = {
  稳健: { background: '#DBEAFE', color: '#1D4ED8' },
  平衡: { background: '#EDE9FE', color: '#6D28D9' },
  进取: { background: '#FCE7F3', color: '#9D174D' },
}

interface Props {
  data: Partial<UserProfile>
  onPrev: () => void
  onSaved: () => void
}

export default function StepResult({ data, onPrev, onSaved }: Props) {
  const navigate  = useNavigate()
  const [loading, setLoading]  = useState(false)
  const [genLoading, setGenLoading] = useState(false)
  const [summary, setSummary]  = useState(data.ai_summary ?? '')
  const [style, setStyle]      = useState(data.ai_style ?? '')
  const [confidence, setConf]  = useState(data.ai_confidence ?? '')

  // 进入此步骤时，若尚无 AI 总结则自动生成
  useEffect(() => {
    if (summary) return
    setGenLoading(true)
    profileApi.save(data)
      .then(() => profileApi.generate())
      .then(res => {
        setSummary(res.summary)
        setStyle(res.style)
        setConf(res.confidence)
      })
      .catch(console.error)
      .finally(() => setGenLoading(false))
  }, [])  // eslint-disable-line

  async function handleSave() {
    setLoading(true)
    try {
      await profileApi.save({
        ...data,
        ai_summary:    summary,
        ai_style:      style,
        ai_confidence: confidence,
      })
      onSaved()
      navigate('/dashboard')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {genLoading ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: 20, color: '#6B7280', fontSize: 13 }}>
          <Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} />
          AI 正在生成画像总结...
        </div>
      ) : (
        <>
          {/* AI 总结 */}
          <div style={{
            background: 'linear-gradient(135deg, #EFF6FF, #F5F3FF)',
            border: '1px solid #BFDBFE', borderRadius: 14, padding: 20,
          }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: '#6B7280', marginBottom: 10, letterSpacing: 0.5 }}>AI 画像总结</div>
            <div style={{ fontSize: 16, fontWeight: 600, color: '#1E3A5F', lineHeight: 1.7 }}>
              {summary || '—'}
            </div>
          </div>

          {/* 标签行 */}
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            {confidence && (
              <span style={{
                padding: '5px 14px', borderRadius: 20, fontSize: 12, fontWeight: 600,
                ...(CONFIDENCE_STYLE[confidence] ?? { background: '#F3F4F6', color: '#374151' }),
              }}>
                {CONFIDENCE_LABEL[confidence] ?? confidence}
              </span>
            )}
            {style && (
              <span style={{
                padding: '5px 14px', borderRadius: 20, fontSize: 12, fontWeight: 600,
                ...(STYLE_STYLE[style] ?? { background: '#F3F4F6', color: '#374151' }),
              }}>
                {style}投资者
              </span>
            )}
            {data.risk_normalized_level && (
              <span style={{ padding: '5px 14px', borderRadius: 20, fontSize: 12, fontWeight: 600, background: '#E0F2FE', color: '#0369A1' }}>
                R{data.risk_normalized_level} 风险等级
              </span>
            )}
          </div>
        </>
      )}

      <div style={{ display: 'flex', gap: 10, marginTop: 4 }}>
        <button onClick={onPrev} style={{ padding: '8px 20px', borderRadius: 8, fontSize: 13, fontWeight: 500, background: '#fff', color: '#374151', border: '1px solid #E5E7EB', cursor: 'pointer' }}>上一步</button>
        <button
          onClick={handleSave}
          disabled={loading || genLoading}
          style={{
            padding: '9px 28px', borderRadius: 8, fontSize: 13, fontWeight: 600,
            background: 'linear-gradient(135deg,#3B82F6,#1D4ED8)',
            color: '#fff', border: 'none', cursor: 'pointer',
            display: 'inline-flex', alignItems: 'center', gap: 6,
          }}
        >
          {loading
            ? <><Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} />保存中...</>
            : <><CheckCircle size={14} />保存画像</>
          }
        </button>
      </div>
    </div>
  )
}

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, CheckCircle, Loader2, ShieldCheck } from 'lucide-react'

import { decisionApi, executionBatchApi, type StructuredTradeIntent, type TradeIntentField } from '@/lib/api'

interface Props {
  intent: StructuredTradeIntent
  conversationId: string | null
  messageId?: number
  onConfirmed?: (intent: StructuredTradeIntent) => void
}

const resolutionLabel: Record<string, string> = {
  MISSING: '缺失',
  AMBIGUOUS: '有歧义',
  CONFLICTING: '有冲突',
  UNSUPPORTED_FOR_V3_15_V1: '当前不支持',
}

function formatField(field: TradeIntentField, fallback = '待补充'): string {
  if (field.value === null || field.value === undefined || field.value === '') return fallback
  if (typeof field.value === 'object') {
    const amount = field.value.amount
    const currency = field.value.currency
    if (typeof amount === 'number') {
      return `${currency === 'USD' ? '$' : `${String(currency ?? '')} `}${amount.toLocaleString('en-US')}`
    }
    return '已提供'
  }
  return String(field.value)
}

function SourceBadge({ field }: { field: TradeIntentField }) {
  const unresolved = field.status !== 'RESOLVED'
  const label = unresolved
    ? resolutionLabel[field.status]
    : field.provenance === 'AI_INFERRED'
      ? 'AI 归一化'
      : null
  if (!label) return null
  const color = unresolved ? '#B45309' : field.provenance === 'AI_INFERRED' ? '#6D28D9' : '#047857'
  const background = unresolved ? '#FFFBEB' : field.provenance === 'AI_INFERRED' ? '#F5F3FF' : '#ECFDF5'
  return (
    <span style={{ fontSize: 10, fontWeight: 600, color, background, padding: '2px 6px', borderRadius: 10 }}>
      {label}
    </span>
  )
}

function Fact({ label, field, value }: { label: string; field: TradeIntentField; value?: string }) {
  return (
    <div style={{ minWidth: 130 }}>
      <div style={{ color: '#9CA3AF', fontSize: 11, marginBottom: 3 }}>{label}</div>
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 5, color: '#1F2937', fontSize: 13, fontWeight: 600 }}>
        <span>{value ?? formatField(field)}</span>
        <SourceBadge field={field} />
      </div>
    </div>
  )
}

export default function TradeIntentPreview({ intent, conversationId, messageId, onConfirmed }: Props) {
  const navigate = useNavigate()
  const [submitting, setSubmitting] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const confirmable = intent.readiness === 'READY_FOR_CONFIRMATION'
    && intent.confirmation_status === 'PENDING'
    && Boolean(conversationId)
    && messageId !== undefined
  const blockingIssues = intent.issues.filter(issue => issue.blocking)

  async function handleConfirm() {
    if (!confirmable || !conversationId || messageId === undefined) return
    setSubmitting(true)
    setError(null)
    try {
      const result = await decisionApi.confirmTradeIntent(intent.intent_id, conversationId, messageId)
      onConfirmed?.(result.trade_intent)
    } catch (err) {
      setError(err instanceof Error ? err.message : '确认失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleGenerateBatch() {
    if (!conversationId || messageId === undefined) return
    setGenerating(true)
    setError(null)
    try {
      const batch = await executionBatchApi.create(conversationId, messageId)
      navigate(`/action?batch=${encodeURIComponent(batch.id)}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : '生成交易执行计划失败')
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div style={{ marginTop: 8, border: '1px solid #BFDBFE', borderRadius: 12, overflow: 'hidden', background: '#fff', boxShadow: 'var(--shadow-sm)' }}>
      <div style={{ padding: '11px 14px', background: '#EFF6FF', borderBottom: '1px solid #DBEAFE', display: 'flex', alignItems: 'center', gap: 8 }}>
        <ShieldCheck size={16} style={{ color: '#2563EB' }} />
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: '#1E3A8A' }}>交易意图预览</div>
          <div style={{ fontSize: 10, color: '#64748B', marginTop: 2 }}>仅确认表达含义 · 尚未进入合约、报价或下单阶段</div>
        </div>
        {intent.confirmation_status === 'CONFIRMED' && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: '#047857', fontWeight: 600 }}>
            <CheckCircle size={13} /> 意图已确认
          </span>
        )}
      </div>

      <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(135px, 1fr))', gap: '12px 18px' }}>
          <Fact label="券商" field={intent.broker} />
          <Fact label="账户" field={intent.account} value={intent.account.value ? '用户已指定（待验证）' : '待系统确认'} />
          <Fact label="资金来源" field={intent.funding_source} value={`${formatField(intent.funding_currency)} Cash`} />
          <Fact label="用户陈述现金" field={intent.stated_cash} />
          <Fact label="预算方式" field={intent.budget_mode} />
          <Fact label="交易约束" field={intent.venue} value={`${formatField(intent.venue)} · ${formatField(intent.trading_currency)} · ${formatField(intent.share_class)}`} />
          <Fact label="方向 / 委托偏好" field={intent.side} value={`${formatField(intent.side)} · ${formatField(intent.order_type)}`} />
        </div>

        <div>
          <div style={{ fontSize: 11, color: '#9CA3AF', marginBottom: 6 }}>资金分配意图</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {intent.legs.map(leg => (
              <div key={`${leg.sequence}-${String(leg.alias.value)}`} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', background: '#F8FAFC', borderRadius: 8, fontSize: 12 }}>
                <span style={{ width: 18, color: '#94A3B8' }}>{leg.sequence}.</span>
                <span style={{ minWidth: 56, fontWeight: 700, color: '#1F2937' }}>{formatField(leg.alias)}</span>
                <span style={{ color: '#475569' }}>
                  {leg.allocation_mode.value === 'REMAINDER' ? '使用剩余资金' : `约 ${formatField(leg.target_amount)}`}
                </span>
                <SourceBadge field={leg.allocation_mode} />
              </div>
            ))}
          </div>
        </div>

        {intent.issues.length > 0 && (
          <div style={{ padding: '9px 10px', background: blockingIssues.length ? '#FFF7ED' : '#F8FAFC', borderRadius: 8 }}>
            {intent.issues.map(issue => (
              <div key={`${issue.code}-${issue.field_path}`} style={{ display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: 11, color: issue.blocking ? '#9A3412' : '#64748B', lineHeight: 1.5 }}>
                {issue.blocking && <AlertTriangle size={12} style={{ marginTop: 2, flexShrink: 0 }} />}
                <span>{issue.message}</span>
              </div>
            ))}
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {intent.confirmation_status === 'CONFIRMED' ? (
            <>
              <div style={{ fontSize: 12, color: '#047857', fontWeight: 600 }}>已确认解析含义；尚未创建或提交订单。</div>
              <button
                onClick={handleGenerateBatch}
                disabled={!conversationId || messageId === undefined || generating}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '7px 13px', border: 'none', borderRadius: 8, background: '#2563EB', color: '#fff', fontSize: 12, fontWeight: 600, cursor: generating ? 'wait' : 'pointer' }}
              >
                {generating && <Loader2 size={13} className="animate-spin" />}
                生成交易执行计划
              </button>
            </>
          ) : (
            <button
              onClick={handleConfirm}
              disabled={!confirmable || submitting}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '7px 13px', border: 'none', borderRadius: 8, background: confirmable ? '#2563EB' : '#D1D5DB', color: '#fff', fontSize: 12, fontWeight: 600, cursor: confirmable ? 'pointer' : 'not-allowed' }}
            >
              {submitting && <Loader2 size={13} className="animate-spin" />}
              {blockingIssues.length ? '请先澄清意图' : '确认意图理解'}
            </button>
          )}
          <span style={{ fontSize: 10, color: '#94A3B8' }}>生成计划仅执行只读解析、报价与资金校验；不会提交订单</span>
        </div>
        {error && <div style={{ fontSize: 11, color: '#DC2626' }}>{error}</div>}
      </div>
    </div>
  )
}

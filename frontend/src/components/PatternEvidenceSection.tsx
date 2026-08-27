import { useId, useState } from 'react'
import { ChevronDown } from 'lucide-react'

import type { DecisionPatternEvidenceSnapshotDTO } from '../lib/api'
import {
  buildPatternEvidencePresentation,
  type PresentedPatternEvidence,
} from '../lib/patternEvidencePresentation'

const LIFECYCLE_LABELS = {
  CONFIRMED: '当前确认',
  INVALIDATED: '已失效（历史）',
  EXPIRED: '已过期（历史）',
} as const

const CONFIRMATION_LABELS: Record<string, string> = {
  confirmed: '已确认',
  pending: '待确认',
  rejected: '未确认',
  not_required: '无需确认',
}

const DIRECTION_LABELS = {
  bullish: '偏多结构',
  bearish: '偏空结构',
  neutral: '中性结构',
} as const

function formatFactValue(value: boolean | number | string): string {
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') {
    return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 4 }).format(value)
  }
  return value
}

function PatternEvidenceCard({ item }: { item: PresentedPatternEvidence }) {
  const historical = item.lifecycle !== 'CONFIRMED'
  const canRenderSnapshot = Boolean(
    item.snapshotUri
    && (item.snapshotMediaType === 'image/svg+xml' || item.snapshotMediaType === 'image/png'),
  )
  return (
    <article
      data-testid={`pattern-card-${item.candidateId}`}
      style={{ border: '1px solid #E5E7EB', borderRadius: 8, padding: '10px 11px', background: '#F9FAFB' }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 8 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: '#1F2937', overflowWrap: 'anywhere' }}>
            {item.patternName}
          </div>
          <div style={{ fontSize: 11, color: '#6B7280', marginTop: 2 }}>
            {item.requestedSymbol} · {DIRECTION_LABELS[item.direction]}
          </div>
        </div>
        <span style={{
          flexShrink: 0, fontSize: 10, fontWeight: 600, padding: '2px 6px', borderRadius: 8,
          color: historical ? '#92400E' : '#047857', background: historical ? '#FEF3C7' : '#D1FAE5',
        }}>
          {LIFECYCLE_LABELS[item.lifecycle]}
        </span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: item.facts.length ? 8 : 0 }}>
        <div style={{ padding: '6px 7px', background: '#fff', borderRadius: 6 }}>
          <div style={{ fontSize: 10, color: '#9CA3AF' }}>结构确认</div>
          <div style={{ fontSize: 11, fontWeight: 600, color: '#374151', marginTop: 2 }}>
            {CONFIRMATION_LABELS[item.structureState] ?? item.structureState}
            {item.structureObservedOn ? ` · ${item.structureObservedOn}` : ''}
          </div>
        </div>
        <div style={{ padding: '6px 7px', background: '#fff', borderRadius: 6 }}>
          <div style={{ fontSize: 10, color: '#9CA3AF' }}>方向确认</div>
          <div style={{ fontSize: 11, fontWeight: 600, color: '#374151', marginTop: 2 }}>
            {CONFIRMATION_LABELS[item.directionState] ?? item.directionState}
            {item.directionObservedOn ? ` · ${item.directionObservedOn}` : ''}
          </div>
        </div>
      </div>

      {item.facts.length > 0 && (
        <dl style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(112px, 1fr))', gap: '5px 8px', margin: 0 }}>
          {item.facts.map(fact => (
            <div key={fact.code} style={{ minWidth: 0 }}>
              <dt style={{ fontSize: 10, color: '#9CA3AF' }}>{fact.label}</dt>
              <dd style={{ margin: '1px 0 0', fontSize: 11, fontWeight: 600, color: '#374151', overflowWrap: 'anywhere' }}>
                {formatFactValue(fact.value)}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {item.invalidated && (
        <div style={{ marginTop: 8, fontSize: 11, color: '#92400E' }}>
          后续技术事实已使该结构失效{item.invalidatedOn ? ` · ${item.invalidatedOn}` : ''}
        </div>
      )}

      {canRenderSnapshot && (
        <img
          src={item.snapshotUri ?? undefined}
          alt={`${item.requestedSymbol} ${item.patternName} 的静态技术证据图`}
          loading="lazy"
          style={{ display: 'block', width: '100%', height: 'auto', marginTop: 9, borderRadius: 6, border: '1px solid #E5E7EB' }}
        />
      )}

      <div style={{ marginTop: 8, paddingTop: 7, borderTop: '1px solid #E5E7EB', fontSize: 10, lineHeight: 1.5, color: '#6B7280' }}>
        {item.riskNote}
      </div>
    </article>
  )
}

export default function PatternEvidenceSection({
  snapshot,
  defaultOpen = false,
  defaultMoreOpen = false,
}: {
  snapshot?: DecisionPatternEvidenceSnapshotDTO
  defaultOpen?: boolean
  defaultMoreOpen?: boolean
}) {
  const presentation = buildPatternEvidencePresentation(snapshot)
  const [open, setOpen] = useState(defaultOpen)
  const [moreOpen, setMoreOpen] = useState(defaultMoreOpen)
  const contentId = useId()
  const moreId = useId()
  if (!presentation) return null

  const remainingCount = presentation.groups.reduce((total, group) => total + group.remaining.length, 0)
  return (
    <section
      data-testid="pattern-evidence-section"
      style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 14px' }}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls={contentId}
        onClick={() => setOpen(value => !value)}
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
      >
        <span style={{ textAlign: 'left' }}>
          <span style={{ display: 'block', fontSize: 14, fontWeight: 600, color: '#111827' }}>
            技术形态证据 · Technical Pattern Evidence ({presentation.count})
          </span>
          <span style={{ display: 'block', marginTop: 2, fontSize: 10, color: '#9CA3AF' }}>
            仅作技术证据展示，不构成交易建议
          </span>
        </span>
        <ChevronDown size={14} style={{ color: '#9CA3AF', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s', flexShrink: 0 }} />
      </button>

      {open && (
        <div id={contentId} style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 10 }}>
          {presentation.groups.map(group => (
            <div key={group.requestedSymbol} data-testid={`pattern-group-${group.requestedSymbol}`}>
              {presentation.invocationScope === 'COMPARE' && (
                <div style={{ fontSize: 11, fontWeight: 700, color: '#475569', marginBottom: 6, overflowWrap: 'anywhere' }}>
                  {group.requestedSymbol}
                </div>
              )}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
                {group.top.map(item => <PatternEvidenceCard key={item.candidateId} item={item} />)}
              </div>
              {group.remaining.length > 0 && (
                <div style={{ marginTop: group.top.length ? 8 : 0 }}>
                  <button
                    type="button"
                    aria-expanded={moreOpen}
                    aria-controls={`${moreId}-${group.requestedSymbol.replace(/[^a-zA-Z0-9_-]/g, '-')}`}
                    onClick={() => setMoreOpen(value => !value)}
                    style={{ display: 'flex', alignItems: 'center', gap: 4, background: 'none', border: 'none', cursor: 'pointer', padding: 0, fontSize: 11, color: '#64748B' }}
                  >
                    <ChevronDown size={12} style={{ transform: moreOpen ? 'rotate(180deg)' : 'none' }} />
                    更多证据 ({group.remaining.length})
                  </button>
                  {moreOpen && (
                    <div id={`${moreId}-${group.requestedSymbol.replace(/[^a-zA-Z0-9_-]/g, '-')}`} style={{ display: 'flex', flexDirection: 'column', gap: 7, marginTop: 7 }}>
                      {group.remaining.map(item => <PatternEvidenceCard key={item.candidateId} item={item} />)}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
          {remainingCount === 0 && (
            <div style={{ fontSize: 10, color: '#9CA3AF' }}>按后台既定展示顺序呈现</div>
          )}
        </div>
      )}
    </section>
  )
}

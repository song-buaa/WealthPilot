import type {
  DecisionPatternEvidenceSnapshotDTO,
  PatternEvidenceBundleDTO,
  PatternEvidenceFactDTO,
  PatternEvidenceResultState,
  PatternType,
} from './api'

const RESULT_STATES = new Set<PatternEvidenceResultState>([
  'PATTERN_FOUND',
  'NO_PATTERN',
  'INSUFFICIENT_HISTORY',
  'DATA_UNAVAILABLE',
  'DATA_QUALITY_BLOCKED',
  'ENGINE_ERROR',
])

const PATTERN_TYPES = new Set<PatternType>([
  'breakout',
  'breakdown',
  'rectangle',
  'ascending_triangle',
  'double_top',
  'double_bottom',
])

const CONFIRMATION_STATES = new Set(['pending', 'confirmed', 'rejected', 'not_required'])
const LIFECYCLE_STATES = new Set(['CONFIRMED', 'INVALIDATED', 'EXPIRED'])
const SNAPSHOT_MEDIA_TYPES = new Set(['image/svg+xml', 'image/png'])

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(item => typeof item === 'string')
}

function isFact(value: unknown): value is PatternEvidenceFactDTO {
  if (!isRecord(value)) return false
  return typeof value.code === 'string'
    && ['boolean', 'number', 'string'].includes(typeof value.value)
    && typeof value.available_from === 'string'
    && typeof value.available_from_session_ordinal === 'number'
    && isStringArray(value.source_ids)
}

function isConfirmation(value: unknown): boolean {
  if (!isRecord(value)) return false
  return typeof value.state === 'string'
    && CONFIRMATION_STATES.has(value.state)
    && typeof value.reason === 'string'
    && (value.observed_on === null || typeof value.observed_on === 'string')
    && Array.isArray(value.facts)
    && value.facts.every(isFact)
}

function isFoundEvidence(value: unknown): boolean {
  if (!isRecord(value) || !isRecord(value.pattern)) return false
  const pattern = value.pattern
  if (typeof pattern.candidate_id !== 'string'
    || typeof pattern.pattern_type !== 'string'
    || !PATTERN_TYPES.has(pattern.pattern_type as PatternType)
    || typeof pattern.lifecycle_status !== 'string'
    || !LIFECYCLE_STATES.has(pattern.lifecycle_status)) return false
  if (!isConfirmation(value.structure_confirmation)
    || !isConfirmation(value.direction_confirmation)
    || !isRecord(value.geometry)
    || !Array.isArray(value.geometry.facts)
    || !value.geometry.facts.every(isFact)
    || !isRecord(value.invalidation)
    || typeof value.invalidation.invalidated !== 'boolean'
    || !Array.isArray(value.invalidation.facts)
    || !value.invalidation.facts.every(isFact)) return false
  return true
}

function isBundle(value: unknown): value is PatternEvidenceBundleDTO {
  if (!isRecord(value)
    || value.schema_version !== 'wp-pattern-evidence-bundle-v1'
    || typeof value.result_state !== 'string'
    || !RESULT_STATES.has(value.result_state as PatternEvidenceResultState)
    || !isRecord(value.instrument)
    || typeof value.instrument.symbol !== 'string'
    || typeof value.instrument.market !== 'string'
    || !isRecord(value.evidence_snapshot)) return false

  const mediaType = value.evidence_snapshot.media_type
  const uri = value.evidence_snapshot.uri
  if (mediaType !== null && (typeof mediaType !== 'string' || !SNAPSHOT_MEDIA_TYPES.has(mediaType))) return false
  if (uri !== null && typeof uri !== 'string') return false
  if ((mediaType === null) !== (uri === null)) return false

  return value.result_state === 'PATTERN_FOUND'
    ? isFoundEvidence(value.evidence)
    : value.evidence === null || value.evidence === undefined
}

export function readPatternEvidenceSnapshot(
  value: unknown,
): DecisionPatternEvidenceSnapshotDTO | undefined {
  if (!isRecord(value)
    || value.snapshot_schema_version !== 'wp-decision-pattern-evidence-snapshot-v1'
    || !['NONE', 'SINGLE', 'COMPARE'].includes(String(value.invocation_scope))
    || !isStringArray(value.requested_symbols)
    || !Array.isArray(value.bundles)
    || !value.bundles.every(isBundle)
    || !isStringArray(value.bundle_hashes)
    || !isStringArray(value.top_evidence_candidate_ids)
    || !isStringArray(value.remaining_evidence_candidate_ids)) return undefined
  return value as unknown as DecisionPatternEvidenceSnapshotDTO
}

export function patternEvidenceFromSSEData(
  data: Record<string, unknown>,
): DecisionPatternEvidenceSnapshotDTO | undefined {
  return readPatternEvidenceSnapshot(data.pattern_evidence)
}

export function patternEvidenceFromMessageMetadata(
  metadata: Record<string, unknown> | null | undefined,
): DecisionPatternEvidenceSnapshotDTO | undefined {
  return readPatternEvidenceSnapshot(metadata?.pattern_evidence)
}

export interface PresentedPatternFact {
  code: string
  label: string
  value: boolean | number | string
}

export interface PresentedPatternEvidence {
  candidateId: string
  patternType: PatternType
  patternName: string
  symbol: string
  requestedSymbol: string
  lifecycle: 'CONFIRMED' | 'INVALIDATED' | 'EXPIRED'
  direction: 'bullish' | 'bearish' | 'neutral'
  structureState: string
  structureObservedOn: string | null
  directionState: string
  directionObservedOn: string | null
  invalidated: boolean
  invalidatedOn: string | null
  facts: PresentedPatternFact[]
  snapshotUri: string | null
  snapshotMediaType: 'image/svg+xml' | 'image/png' | null
  riskNote: string
}

export interface PatternEvidenceSymbolGroup {
  requestedSymbol: string
  top: PresentedPatternEvidence[]
  remaining: PresentedPatternEvidence[]
}

export interface PatternEvidencePresentation {
  invocationScope: 'NONE' | 'SINGLE' | 'COMPARE'
  count: number
  groups: PatternEvidenceSymbolGroup[]
}

export const PATTERN_NAMES: Record<PatternType, string> = {
  breakout: '向上突破',
  breakdown: '向下破位',
  rectangle: '矩形整理',
  ascending_triangle: '上升三角形',
  double_top: '双顶结构',
  double_bottom: '双底结构',
}

const RISK_NOTES: Record<PatternType, string> = {
  breakout: '仅为价格突破技术证据，不构成交易建议。',
  breakdown: '仅为价格破位技术证据，不代表做空建议。',
  rectangle: '仅描述中性区间结构，不预测后续方向。',
  ascending_triangle: '仅描述结构事实，方向确认必须单独判断。',
  double_top: '反转结构仅作描述，不代表确定性预测或交易建议。',
  double_bottom: '反转结构仅作描述，方向确认仍受既有成交量条件约束。',
}

const FACT_LABELS: Partial<Record<PatternType, Record<string, string>>> = {
  breakout: {
    boundary_axis: '关键边界', boundary_zone_high: '边界上沿', boundary_zone_low: '边界下沿',
    boundary_touch_count: '边界触及', break_close: '突破收盘', break_threshold: '确认阈值',
    volume_confirmed: '成交量确认', volume_ratio: '成交量比率', ema_direction_aligned: 'EMA 方向一致',
    invalidation_boundary: '失效边界', price_break_confirmed: '价格突破确认',
  },
  breakdown: {
    boundary_axis: '关键边界', boundary_zone_high: '边界上沿', boundary_zone_low: '边界下沿',
    boundary_touch_count: '边界触及', break_close: '破位收盘', break_threshold: '确认阈值',
    volume_confirmed: '成交量确认', volume_ratio: '成交量比率', ema_direction_aligned: 'EMA 方向一致',
    invalidation_boundary: '失效边界', price_break_confirmed: '价格破位确认',
  },
  rectangle: {
    range_high: '区间上沿', range_low: '区间下沿', range_width: '区间宽度', range_width_pct: '区间宽度比例',
    support_touch_count: '支撑触及', resistance_touch_count: '阻力触及', structure_span_sessions: '结构交易日数',
    invalidation_lower_boundary: '下方失效边界', invalidation_upper_boundary: '上方失效边界',
  },
  ascending_triangle: {
    resistance_at_confirmation: '水平阻力', support_at_confirmation: '上升支撑',
    resistance_touch_count: '阻力触及', support_touch_count: '支撑触及', contraction_pct: '收敛比例',
    apex_progress_at_confirmation: '顶点进度', apex_session_ordinal: '顶点交易日序号',
    ascending_triangle_upside_close_confirmed: '向上收盘确认',
  },
  double_top: {
    first_extreme_price: '第一高点', second_extreme_price: '第二高点', intervening_reaction_ratio: '中间回撤比例',
    neckline_price: '颈线', extreme_similarity_ratio: '高点相似度', structure_duration_sessions: '结构交易日数',
    double_top_downside_neckline_close_confirmed: '颈线下破确认', volume_confirmation_role: '成交量角色',
  },
  double_bottom: {
    first_extreme_price: '第一低点', second_extreme_price: '第二低点', intervening_reaction_ratio: '中间反弹比例',
    neckline_price: '颈线', extreme_similarity_ratio: '低点相似度', structure_duration_sessions: '结构交易日数',
    double_bottom_upside_neckline_close_confirmed: '颈线上破确认',
    direction_confirmation_volume_ratio: '方向确认成交量比率', volume_confirmation_role: '成交量角色',
  },
}

function requestedSymbolOf(bundle: PatternEvidenceBundleDTO): string {
  return `${bundle.instrument.symbol}:${bundle.instrument.market}`
}

function presentBundle(bundle: PatternEvidenceBundleDTO): PresentedPatternEvidence | undefined {
  if (bundle.result_state !== 'PATTERN_FOUND' || !bundle.evidence) return undefined
  const evidence = bundle.evidence
  const patternType = evidence.pattern.pattern_type
  const labels = FACT_LABELS[patternType] ?? {}
  const sourceFacts = [
    ...evidence.geometry.facts,
    ...evidence.structure_confirmation.facts,
    ...evidence.direction_confirmation.facts,
    ...evidence.invalidation.facts,
  ]
  const seen = new Set<string>()
  const facts: PresentedPatternFact[] = []
  for (const fact of sourceFacts) {
    const label = labels[fact.code]
    if (!label || seen.has(fact.code)) continue
    seen.add(fact.code)
    facts.push({ code: fact.code, label, value: fact.value })
  }
  return {
    candidateId: evidence.pattern.candidate_id,
    patternType,
    patternName: PATTERN_NAMES[patternType],
    symbol: bundle.instrument.symbol,
    requestedSymbol: requestedSymbolOf(bundle),
    lifecycle: evidence.pattern.lifecycle_status,
    direction: evidence.pattern.direction,
    structureState: evidence.structure_confirmation.state,
    structureObservedOn: evidence.structure_confirmation.observed_on,
    directionState: evidence.direction_confirmation.state,
    directionObservedOn: evidence.direction_confirmation.observed_on,
    invalidated: evidence.invalidation.invalidated,
    invalidatedOn: evidence.invalidation.observed_on,
    facts,
    snapshotUri: bundle.evidence_snapshot.uri,
    snapshotMediaType: bundle.evidence_snapshot.media_type,
    riskNote: RISK_NOTES[patternType],
  }
}

export function buildPatternEvidencePresentation(
  snapshot: DecisionPatternEvidenceSnapshotDTO | undefined,
): PatternEvidencePresentation | null {
  if (!snapshot) return null
  const foundById = new Map<string, PresentedPatternEvidence>()
  for (const bundle of snapshot.bundles) {
    const presented = presentBundle(bundle)
    if (presented) foundById.set(presented.candidateId, presented)
  }

  const groups = new Map<string, PatternEvidenceSymbolGroup>()
  for (const requestedSymbol of snapshot.requested_symbols) {
    groups.set(requestedSymbol, { requestedSymbol, top: [], remaining: [] })
  }
  const append = (candidateId: string, destination: 'top' | 'remaining') => {
    const item = foundById.get(candidateId)
    const group = item ? groups.get(item.requestedSymbol) : undefined
    if (item && group) group[destination].push(item)
  }
  snapshot.top_evidence_candidate_ids.forEach(id => append(id, 'top'))
  snapshot.remaining_evidence_candidate_ids.forEach(id => append(id, 'remaining'))

  const visibleGroups = [...groups.values()].filter(group => group.top.length || group.remaining.length)
  const count = visibleGroups.reduce((total, group) => total + group.top.length + group.remaining.length, 0)
  return count ? { invocationScope: snapshot.invocation_scope, count, groups: visibleGroups } : null
}

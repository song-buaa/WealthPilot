import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { expect, test } from '@playwright/test'
import { createElement, type ComponentType } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer, type ViteDevServer } from 'vite'

import type {
  DecisionPatternEvidenceSnapshotDTO,
  PatternConfirmationState,
  PatternEvidenceBundleDTO,
  PatternEvidenceResultState,
  PatternLifecycleStatus,
  PatternType,
} from '../../src/lib/api'
import {
  buildPatternEvidencePresentation,
  patternEvidenceFromMessageMetadata,
  patternEvidenceFromSSEData,
  readPatternEvidenceSnapshot,
} from '../../src/lib/patternEvidencePresentation'

const componentSourcePath = fileURLToPath(
  new URL('../../src/components/PatternEvidenceSection.tsx', import.meta.url),
)
const componentSource = readFileSync(componentSourcePath, 'utf8')
let viteServer: ViteDevServer
let PatternEvidenceSection: ComponentType<{
  snapshot?: DecisionPatternEvidenceSnapshotDTO
  defaultOpen?: boolean
  defaultMoreOpen?: boolean
}>

test.beforeAll(async () => {
  viteServer = await createServer({
    root: fileURLToPath(new URL('../..', import.meta.url)),
    appType: 'custom',
    logLevel: 'silent',
    server: { middlewareMode: true },
  })
  const module = await viteServer.ssrLoadModule('/src/components/PatternEvidenceSection.tsx')
  PatternEvidenceSection = module.default as typeof PatternEvidenceSection
})

test.afterAll(async () => {
  await viteServer.close()
})

function renderPatternEvidence(
  canonical: DecisionPatternEvidenceSnapshotDTO,
  defaultMoreOpen = false,
): string {
  return renderToStaticMarkup(createElement(PatternEvidenceSection, {
    snapshot: canonical,
    defaultOpen: true,
    defaultMoreOpen,
  }))
}

const patternTypes: PatternType[] = [
  'breakout',
  'breakdown',
  'rectangle',
  'ascending_triangle',
  'double_top',
  'double_bottom',
]

const factCodes: Record<PatternType, string> = {
  breakout: 'boundary_axis',
  breakdown: 'break_close',
  rectangle: 'range_high',
  ascending_triangle: 'resistance_at_confirmation',
  double_top: 'neckline_price',
  double_bottom: 'direction_confirmation_volume_ratio',
}

function confirmation(state: PatternConfirmationState, observedOn: string | null) {
  return {
    state,
    reason: 'canonical-test-reason',
    observed_on: observedOn,
    observed_session_ordinal: observedOn ? 10 : null,
    facts: [],
  }
}

function foundBundle({
  patternType,
  candidateId,
  symbol = 'AAPL',
  lifecycle = 'CONFIRMED',
  directionState = 'confirmed',
  snapshot = false,
}: {
  patternType: PatternType
  candidateId: string
  symbol?: string
  lifecycle?: PatternLifecycleStatus
  directionState?: PatternConfirmationState
  snapshot?: boolean
}): PatternEvidenceBundleDTO {
  const invalidated = lifecycle === 'INVALIDATED'
  const direction = patternType === 'rectangle'
    ? 'neutral'
    : ['breakdown', 'double_top'].includes(patternType) ? 'bearish' : 'bullish'
  return {
    schema_version: 'wp-pattern-evidence-bundle-v1',
    instrument: {
      instrument_id: `IBKR:${symbol}`,
      symbol,
      market: 'US',
      economic_asset_class: 'EQUITY',
      con_id: 1,
      isin: null,
      currency: 'USD',
    },
    timeframe: '1d',
    result_state: 'PATTERN_FOUND',
    evidence: {
      pattern: {
        candidate_id: candidateId,
        pattern_type: patternType,
        pattern_family: 'test-family',
        direction,
        lifecycle_status: lifecycle,
        formed_on: '2026-01-02',
        available_from: '2026-01-05',
        evaluated_on: '2026-01-06',
      },
      structure_confirmation: confirmation('confirmed', '2026-01-05'),
      direction_confirmation: confirmation(directionState, directionState === 'confirmed' ? '2026-01-06' : null),
      geometry: {
        pivots: [],
        boundaries: [],
        facts: [{
          code: factCodes[patternType],
          value: patternType === 'double_bottom' ? 1.4 : 101.25,
          available_from: '2026-01-05',
          available_from_session_ordinal: 9,
          source_ids: ['source-1'],
        }],
      },
      invalidation: {
        invalidated,
        condition: 'canonical-condition',
        reason: invalidated ? 'later-fact' : null,
        observed_on: invalidated ? '2026-01-07' : null,
        observed_session_ordinal: invalidated ? 11 : null,
        facts: [],
      },
      provenance: {
        provider: 'IBKR',
        source_bar_hash: 'a'.repeat(64),
        candidate_source_bar_hash: 'b'.repeat(64),
        detector_version: 'detector-v1',
        indicator_layer_version: 'indicator-v1',
        calibration_version: 'calibration-v1',
        parameter_set_id: 'parameters-v1',
        parameter_hash: 'c'.repeat(64),
        detector_result_hash: 'd'.repeat(64),
      },
    },
    evidence_snapshot: snapshot ? {
      uri: 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg"/%3E',
      media_type: 'image/svg+xml',
    } : { uri: null, media_type: null },
    reason: '',
  }
}

function emptyBundle(state: Exclude<PatternEvidenceResultState, 'PATTERN_FOUND'>): PatternEvidenceBundleDTO {
  return {
    schema_version: 'wp-pattern-evidence-bundle-v1',
    instrument: {
      instrument_id: 'IBKR:AAPL', symbol: 'AAPL', market: 'US', economic_asset_class: 'EQUITY',
      con_id: 1, isin: null, currency: 'USD',
    },
    timeframe: '1d',
    result_state: state,
    evidence: null,
    evidence_snapshot: { uri: null, media_type: null },
    reason: `test-${state}`,
  }
}

function snapshot(
  bundles: PatternEvidenceBundleDTO[],
  top: string[],
  remaining: string[],
  requestedSymbols = ['AAPL:US'],
  scope: 'SINGLE' | 'COMPARE' = 'SINGLE',
): DecisionPatternEvidenceSnapshotDTO {
  return {
    snapshot_schema_version: 'wp-decision-pattern-evidence-snapshot-v1',
    invocation_scope: scope,
    requested_symbols: requestedSymbols,
    bundles,
    bundle_hashes: bundles.map((_, index) => `hash-${index}`),
    top_evidence_candidate_ids: top,
    remaining_evidence_candidate_ids: remaining,
  }
}

test('parses current-turn and restored-history snapshots through one contract', () => {
  const canonical = snapshot(
    [foundBundle({ patternType: 'breakout', candidateId: 'breakout-1', snapshot: true })],
    ['breakout-1'],
    [],
  )
  const current = patternEvidenceFromSSEData({ pattern_evidence: canonical })
  const restored = patternEvidenceFromMessageMetadata({ pattern_evidence: canonical })

  expect(current).toEqual(canonical)
  expect(restored).toEqual(canonical)
  expect(buildPatternEvidencePresentation(current)).toEqual(buildPatternEvidencePresentation(restored))
  expect(renderPatternEvidence(current!)).toBe(renderPatternEvidence(restored!))
  expect(patternEvidenceFromSSEData({})).toBeUndefined()
  expect(patternEvidenceFromMessageMetadata(null)).toBeUndefined()
})

test('preserves all result states and silently excludes non-found states', () => {
  const states: Array<Exclude<PatternEvidenceResultState, 'PATTERN_FOUND'>> = [
    'NO_PATTERN', 'INSUFFICIENT_HISTORY', 'DATA_UNAVAILABLE', 'DATA_QUALITY_BLOCKED', 'ENGINE_ERROR',
  ]
  const found = foundBundle({ patternType: 'breakout', candidateId: 'found' })
  const canonical = snapshot([found, ...states.map(emptyBundle)], ['found'], [])
  const parsed = readPatternEvidenceSnapshot(canonical)

  expect(parsed?.bundles.map(bundle => bundle.result_state)).toEqual(['PATTERN_FOUND', ...states])
  const silentSnapshot = snapshot(states.map(emptyBundle), [], [])
  expect(buildPatternEvidencePresentation(silentSnapshot)).toBeNull()
  expect(renderPatternEvidence(silentSnapshot)).toBe('')
  expect(componentSource).toContain('if (!presentation) return null')
})

test('renders all six pattern families with separate lifecycle and confirmation states', () => {
  const bundles = patternTypes.map((patternType, index) => foundBundle({
    patternType,
    candidateId: `candidate-${index}`,
    lifecycle: index === 4 ? 'INVALIDATED' : index === 5 ? 'EXPIRED' : 'CONFIRMED',
    directionState: patternType === 'rectangle' ? 'not_required' : index >= 3 ? 'pending' : 'confirmed',
    snapshot: index === 0,
  }))
  const canonical = snapshot(
    bundles,
    ['candidate-0', 'candidate-1', 'candidate-2'],
    ['candidate-3', 'candidate-4', 'candidate-5'],
  )
  const presentation = buildPatternEvidencePresentation(canonical)
  const items = presentation?.groups.flatMap(group => [...group.top, ...group.remaining]) ?? []

  expect(items.map(item => item.patternName)).toEqual([
    '向上突破', '向下破位', '矩形整理', '上升三角形', '双顶结构', '双底结构',
  ])
  expect(items.map(item => item.lifecycle)).toContain('CONFIRMED')
  expect(items.map(item => item.lifecycle)).toContain('INVALIDATED')
  expect(items.map(item => item.lifecycle)).toContain('EXPIRED')
  expect(items.map(item => item.directionState)).toContain('not_required')
  expect(items.map(item => item.directionState)).toContain('pending')
  expect(items[0]?.snapshotMediaType).toBe('image/svg+xml')
  for (const label of ['结构确认', '方向确认', '无需确认', '待确认', '静态技术证据图']) {
    expect(componentSource).toContain(label)
  }
  const html = renderPatternEvidence(canonical, true)
  for (const label of ['向上突破', '向下破位', '矩形整理', '上升三角形', '双顶结构', '双底结构']) {
    expect(html).toContain(label)
  }
  expect(html).toContain('当前确认')
  expect(html).toContain('已失效（历史）')
  expect(html).toContain('已过期（历史）')
  expect(html).toContain('静态技术证据图')
})

test('uses backend top and remaining IDs exactly regardless of bundle order', () => {
  const first = foundBundle({ patternType: 'breakout', candidateId: 'first' })
  const second = foundBundle({ patternType: 'rectangle', candidateId: 'second' })
  const third = foundBundle({ patternType: 'double_top', candidateId: 'third' })
  const top = ['third', 'first']
  const remaining = ['second']
  const ordered = buildPatternEvidencePresentation(snapshot([first, second, third], top, remaining))
  const shuffled = buildPatternEvidencePresentation(snapshot([second, third, first], top, remaining))

  expect(ordered).toEqual(shuffled)
  expect(ordered?.groups[0].top.map(item => item.candidateId)).toEqual(top)
  expect(ordered?.groups[0].remaining.map(item => item.candidateId)).toEqual(remaining)
})

test('preserves two- and three-symbol compare attribution without ranking or merging', () => {
  for (const symbols of [['AAPL', 'SPY'], ['AAPL', 'SPY', 'TLT']]) {
    const bundles = symbols.map((symbol, index) => foundBundle({
      patternType: patternTypes[index], candidateId: `candidate-${symbol}`, symbol,
    }))
    const requested = symbols.map(symbol => `${symbol}:US`)
    const canonical = snapshot(
      bundles,
      bundles.map(bundle => bundle.evidence?.pattern.candidate_id ?? ''),
      [],
      requested,
      'COMPARE',
    )
    const presentation = buildPatternEvidencePresentation(canonical)

    expect(presentation?.groups.map(group => group.requestedSymbol)).toEqual(requested)
    expect(presentation?.groups.every(group => group.top.every(item => item.requestedSymbol === group.requestedSymbol))).toBe(true)
    const serialized = JSON.stringify(presentation)
    expect(serialized).not.toContain('胜出')
    expect(serialized).not.toContain('推荐排序')
    expect(serialized).not.toContain('吸引力评分')
  }
})

test('component has only presentation controls and no action or broker API authority', () => {
  expect(componentSource).toContain('defaultOpen = false')
  expect(componentSource).toContain('aria-expanded={open}')
  expect(componentSource).toContain('仅作技术证据展示，不构成交易建议')
  expect(componentSource).not.toContain('生成交易执行计划')
  expect(componentSource).not.toContain('立即下单')
  expect(componentSource).not.toContain('创建行动清单')
  for (const forbiddenImport of ['actionDraftApi', 'executionPlanApi', 'executionBatchApi', 'brokerApi']) {
    expect(componentSource).not.toContain(forbiddenImport)
  }
})

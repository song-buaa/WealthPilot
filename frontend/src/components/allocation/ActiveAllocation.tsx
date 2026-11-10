import { useEffect, useState } from 'react'
import { fmtCny } from '@/lib/fmt'

interface Gap {
  amount_cny: string | null
  current_ratio: string | null
  target_ratio: string
  target_kind?: 'NEUTRAL_POLICY'
  gap_pp: string | null
  amount_gap_cny: string | null
}
interface Hierarchy {
  status: 'QUALIFIED' | 'PARTIAL' | 'UNKNOWN'
  strategic: Record<string, Gap>
  secondary: Record<string, Gap>
  direct_equity?: { status: string; issuers: Record<string, Gap>
    unallocated: { target_ratio: string; status: string } | null }
  etf: { status: string; products: Record<string, Gap & {
    current_ratio_of_d: string | null; target_ratio_of_d: string }> }
  strategic_review: { status: string; threshold_pp: string; candidate_hit_classes?: string[] | null }
  unclassified_asset_refs: string[]
  unclassified_amount_cny: string
}

interface Concentration {
  status: string
  expires_at?: string | null
  unknown_coverage?: boolean
  issues: string[]
  issuers?: Record<string, {
    issuer_name: string
    direct_pct: string | null
    indirect_known_pct: string | null
    known_lower_bound_pct: string | null
    proven_upper_bound_pct: string | null
    risk_status: string
  }>
  btc?: { known_lower_bound_pct: string | null; risk_status: string }
}

interface Diagnostic {
  status: string
  snapshot_id: string
  snapshot_sha256: string
  as_of: string
  expires_at: string
  denominator_cny: string
  policy_reference: { bundle_sha256: string }
  classes: Record<string, { amount_cny: string; current_ratio: string; target_ratio: string;
    gap_pp: string; amount_gap_cny: string; direction: string; target_kind?: Gap['target_kind'] }>
  focus: string[]
  hierarchy?: Hierarchy
  concentration?: Concentration
  execution_authorized: false
}
const labels: Record<string, string> = { cash: '货币', fixed: '固收', equity: '权益', alt: '另类', deriv: '衍生' }

const policyLabels: Record<string, string> = {
  growth_and_alternatives: '成长权益及另类资产', stable_pool: '稳健池',
  us_etf: '美国股票 ETF', us_direct: '美国直接个股', china: '中国主动精选',
  gold: '黄金', btc: 'BTC 现货', ib01: 'IB01', cbu3: 'CBU3', cash: '投资账户临时现金',
}
const percent = (ratio: string | null) => ratio === null ? '—' : (Number(ratio) * 100).toFixed(2) + '%'
const currency = (amount: string | null) => amount === null ? '—' : fmtCny(Number(amount))

const targetLabel = (rows: Record<string, { target_kind?: Gap['target_kind'] }>, fallback: string) =>
  Object.values(rows).length > 0 && Object.values(rows).every(row => row.target_kind === 'NEUTRAL_POLICY')
    ? '中性政策目标' : fallback

function GapTable({ title, rows }: { title: string; rows: Record<string, Gap> }) {
  return <table>
    <caption>{title}</caption>
    <thead><tr><th>类别</th><th>当前金额</th><th>当前权重</th><th>{targetLabel(rows, '目标权重')}</th><th>差距（百分点）</th><th>金额差距</th></tr></thead>
    <tbody>{Object.entries(rows).map(([key, row]) => <tr key={key}>
      <th scope="row">{policyLabels[key] ?? key}</th><td>{currency(row.amount_cny)}</td>
      <td>{percent(row.current_ratio)}</td><td>{percent(row.target_ratio)}</td>
      <td>{row.gap_pp === null ? '—' : Number(row.gap_pp).toFixed(2)}</td><td>{currency(row.amount_gap_cny)}</td>
    </tr>)}</tbody>
  </table>
}

function PolicyHierarchy({ value }: { value: Hierarchy }) {
  const review = value.strategic_review
  return <section aria-label="ACTIVE 分层配置">
    <h4>ACTIVE 分层配置</h4>
    {value.status !== 'QUALIFIED' && <p role="status">
      分层资格 {value.status}。未归类金额 {currency(value.unclassified_amount_cny)}，
      来源 {value.unclassified_asset_refs.join('、')}。
    </p>}
    <GapTable title="一级战略配置（占 D）" rows={value.strategic} />
    <p role="status">RULE_SCOPE_UNVERIFIED · 政策复核适用节点待确认。
      {review.status === 'RULE_SCOPE_UNVERIFIED' && !!review.candidate_hit_classes?.length &&
        <>候选偏差超过 {Number(review.threshold_pp)} 个百分点（{review.candidate_hit_classes.map(key => policyLabels[key]).join('、')}）。</>}
    </p>
    <GapTable title="二级政策预算（占 D）" rows={value.secondary} />
    {value.direct_equity && <>
      {value.direct_equity.status !== 'QUALIFIED' && <p role="status">核心直接股的实际差距待身份及覆盖核验。</p>}
      <GapTable title="美国核心直接股（占 D）" rows={value.direct_equity.issuers} />
      {value.direct_equity.unallocated && <p>未指定自主选股预算 {percent(value.direct_equity.unallocated.target_ratio)} · 待研究。</p>}
    </>}
    {value.etf.status === 'NO_ETF_HOLDINGS' && <p>当前美国股票 ETF 子组合为空，内部权重未定义。</p>}
    <GapTable title="ETF 产品配置（占 ETF 子组合）" rows={value.etf.products} />
    <table><caption>ETF 产品占 D</caption><thead><tr><th>产品</th><th>当前</th><th>{targetLabel(value.etf.products, '派生目标')}</th></tr></thead>
      <tbody>{Object.entries(value.etf.products).map(([key, row]) => <tr key={key}>
        <th scope="row">{key}</th><td>{percent(row.current_ratio_of_d)}</td><td>{percent(row.target_ratio_of_d)}</td>
      </tr>)}</tbody>
    </table>
  </section>
}

function ConcentrationRisk({ value }: { value: Concentration }) {
  const display = (ratio: string | null) => ratio === null ? '—' : Number(ratio).toFixed(2) + '%'
  return <section aria-label="发行人集中度">
    <h4>发行人集中度</h4>
    {(value.status === 'UNKNOWN' || value.unknown_coverage) && <p role="status">
      {value.issues.includes('CONCENTRATION_EVIDENCE_EXPIRED')
        ? '发行人集中度证据已到期。'
        : '穿透覆盖尚未证实完整。已知下限可以触发复核，缺少上界时无法判定安全。'}
    </p>}
    <table>
      <thead><tr><th>发行人</th><th>直接</th><th>间接已知</th><th>已知下限</th><th>证实上界</th><th>风险</th></tr></thead>
      <tbody>{Object.entries(value.issuers ?? {}).map(([issuer, row]) => <tr key={issuer}>
        <th scope="row">{row.issuer_name}</th><td>{display(row.direct_pct)}</td>
        <td>{display(row.indirect_known_pct)}</td><td>{display(row.known_lower_bound_pct)}</td>
        <td>{display(row.proven_upper_bound_pct)}</td><td>{row.risk_status}</td>
      </tr>)}</tbody>
    </table>
    {value.btc && <p>BTC 现货已知下限 {display(value.btc.known_lower_bound_pct)} · {value.btc.risk_status}</p>}
  </section>
}

/** Reads an already-frozen view. It never prepares, refreshes or places orders. */
export default function ActiveAllocation({ policySha }: { policySha?: string }) {
  const [value, setValue] = useState<Diagnostic | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let concentrationTimer: ReturnType<typeof setTimeout> | undefined
    fetch('/api/allocation/snapshot').then(async response => {
      const body = await response.json()
      if (!response.ok) throw new Error(typeof body.detail === 'string'
        ? body.detail + (Array.isArray(body.qualification?.issues) ? ' · ' + body.qualification.issues.join(' / ') : '')
        : 'ALLOCATION_UNAVAILABLE')
      const diagnostic = body.policy_allocation as Diagnostic | undefined
      if (!diagnostic || diagnostic.status !== 'QUALIFIED_MAJOR_CLASSES' || !policySha ||
          diagnostic.policy_reference.bundle_sha256 !== policySha) throw new Error('POLICY_VERSION_CONFLICT')
      const remaining = Date.parse(diagnostic.expires_at) - Date.now()
      if (!Number.isFinite(remaining) || remaining <= 0) throw new Error('ASSET_VIEW_EXPIRED')
      if (!cancelled) {
        const expires = diagnostic.concentration?.expires_at
        const expiredRisk = { status: 'UNKNOWN', issues: ['CONCENTRATION_EVIDENCE_EXPIRED'] }
        if (expires) {
          const riskRemaining = Date.parse(expires) - Date.now()
          if (!Number.isFinite(riskRemaining) || riskRemaining <= 0) diagnostic.concentration = expiredRisk
          else if (riskRemaining < remaining) concentrationTimer = setTimeout(() => {
            setValue(current => current && { ...current, concentration: expiredRisk })
          }, riskRemaining)
        }
        setValue(diagnostic); setError('')
        timer = setTimeout(() => { setValue(null); setError('ASSET_VIEW_EXPIRED') }, remaining)
      }
    }).catch((reason: unknown) => {
      if (!cancelled) { setValue(null); setError(reason instanceof Error ? reason.message : 'ALLOCATION_UNAVAILABLE') }
    })
    return () => { cancelled = true; clearTimeout(timer); clearTimeout(concentrationTimer) }
  }, [policySha])
  if (error) return <div role="alert">当前配置资格 UNKNOWN：{error}。需要已有合格资产快照及 Owner 用途、覆盖、养老金和 FX 证据；本读取不会触发同步，不提供调仓金额。</div>
  if (!value) return <p>正在读取已冻结的组合配置…</p>
  return <section aria-label="当前与目标配置">
    <h3>可投资资产 {fmtCny(Number(value.denominator_cny))}</h3>
    <p>快照 {value.snapshot_id} · 来源时点 {value.as_of}</p>
    <table><thead><tr><th>类别</th><th>当前金额</th><th>当前</th><th>{targetLabel(value.classes, '目标')}</th><th>差距（百分点）</th><th>金额差距</th></tr></thead>
      <tbody>{Object.entries(value.classes).map(([key, row]) => <tr key={key}>
        <th>{labels[key]}</th><td>{fmtCny(Number(row.amount_cny))}</td>
        <td>{(Number(row.current_ratio) * 100).toFixed(2)}%</td><td>{(Number(row.target_ratio) * 100).toFixed(2)}%</td>
        <td>{Number(row.gap_pp).toFixed(2)}</td><td>{fmtCny(Number(row.amount_gap_cny))}</td>
      </tr>)}</tbody></table>
    <p>主要偏差：{value.focus.map(key => `${labels[key]}${value.classes[key].direction === 'OVER' ? '超配' : '低配'}`).join('、') || '与目标一致'}。</p>
    {value.hierarchy && <PolicyHierarchy value={value.hierarchy} />}
    {value.concentration && <ConcentrationRisk value={value.concentration} />}
    <p>正差距表示超配，负差距表示低配。配置与风险诊断不构成交易或资金操作建议。</p>
  </section>
}

import { useEffect, useState } from 'react'
import { fmtCny } from '@/lib/fmt'

interface Diagnostic {
  status: string
  snapshot_id: string
  snapshot_sha256: string
  as_of: string
  expires_at: string
  denominator_cny: string
  policy_reference: { bundle_sha256: string }
  classes: Record<string, { amount_cny: string; current_ratio: string; target_ratio: string;
    gap_pp: string; amount_gap_cny: string; direction: string }>
  focus: string[]
  execution_authorized: false
}
const labels: Record<string, string> = { cash: '货币', fixed: '固收', equity: '权益', alt: '另类', deriv: '衍生' }

/** Reads an already-frozen view. It never prepares, refreshes or places orders. */
export default function ActiveAllocation({ policySha }: { policySha?: string }) {
  const [value, setValue] = useState<Diagnostic | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
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
        setValue(diagnostic); setError('')
        timer = setTimeout(() => { setValue(null); setError('ASSET_VIEW_EXPIRED') }, remaining)
      }
    }).catch((reason: unknown) => {
      if (!cancelled) { setValue(null); setError(reason instanceof Error ? reason.message : 'ALLOCATION_UNAVAILABLE') }
    })
    return () => { cancelled = true; clearTimeout(timer) }
  }, [policySha])
  if (error) return <div role="alert">当前配置资格 UNKNOWN：{error}。需要已有合格资产快照及 Owner 用途、覆盖、养老金和 FX 证据；本读取不会触发同步，不提供调仓金额。</div>
  if (!value) return <p>正在读取已冻结的组合配置…</p>
  return <section aria-label="当前与目标配置">
    <h3>可投资资产 {fmtCny(Number(value.denominator_cny))}</h3>
    <p>快照 {value.snapshot_id} · 来源时点 {value.as_of}</p>
    <table><thead><tr><th>类别</th><th>当前金额</th><th>当前</th><th>目标</th><th>差距（百分点）</th><th>金额差距</th></tr></thead>
      <tbody>{Object.entries(value.classes).map(([key, row]) => <tr key={key}>
        <th>{labels[key]}</th><td>{fmtCny(Number(row.amount_cny))}</td>
        <td>{(Number(row.current_ratio) * 100).toFixed(2)}%</td><td>{(Number(row.target_ratio) * 100).toFixed(2)}%</td>
        <td>{Number(row.gap_pp).toFixed(2)}</td><td>{fmtCny(Number(row.amount_gap_cny))}</td>
      </tr>)}</tbody></table>
    <p>主要偏差：{value.focus.map(key => `${labels[key]}${value.classes[key].direction === 'OVER' ? '超配' : '低配'}`).join('、') || '与目标一致'}。</p>
    <p>正差距表示超配，负差距表示低配。仅为配置差距，未完成穿透风险复核，也不是交易或资金操作建议。</p>
  </section>
}

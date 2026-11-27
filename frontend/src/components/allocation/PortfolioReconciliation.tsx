import { useState } from 'react'

type Review = {
  decision_revision_ref: string
  decision_state: string
  policy_changed: boolean
  current_investable_denominator_cny?: string
  next_review: string
  review_due_at: string
  checkpoints: {
    item_id: string
    stage_label: string
    observation_status: string
    actions: {
      action_id: string
      kind: string
      symbol?: string
      current?: { current_quantity?: string; current_amount_cny?: string; current?: { principal_cny: string } }
      target_amount_cny?: string
      target_principal_cny?: string
      remaining_cny?: string
      observation_status: string
      comparability_issue?: string
      execution: { status: string; issue?: string; sync_status_at_readback?: string }
    }[]
  }[]
  risk_reassessment?: Record<string, { status: string; current_fact?: { subject: string } }>
}

export default function PortfolioReconciliation({ snapshotId, snapshotSha }:
  { snapshotId: string; snapshotSha: string }) {
  const [bundle, setBundle] = useState('')
  const [links, setLinks] = useState('')
  const [value, setValue] = useState<Review | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function run() {
    setBusy(true); setValue(null); setError('')
    try {
      const orderLinks = links.trim() ? JSON.parse(links) : {}
      const response = await fetch('/api/allocation/governance/reconcile', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ snapshot_id: snapshotId, snapshot_sha256: snapshotSha,
          bundle_id: bundle.trim() || null, order_links: orderLinks }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '复核资料无效')
      setValue(result as Review)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '复核暂不可用')
    } finally { setBusy(false) }
  }

  return <section className="mt-4 border-t pt-3" aria-label="实际执行复核">
    <h4>实际执行复核</h4>
    <p>使用此页已有快照。留空时读取最近一次带检查点的确认方案。</p>
    <label className="block">方案 ID
      <input className="ml-2 rounded border px-2" value={bundle} disabled={busy}
        onChange={event => { setBundle(event.target.value); setValue(null) }} />
    </label>
    <details>
      <summary>关联已有订单证据</summary>
      <label className="block">动作 ID 到已有订单 ID 的映射（JSON）
        <textarea className="block w-full rounded border px-2" value={links} disabled={busy}
          onChange={event => { setLinks(event.target.value); setValue(null) }} />
      </label>
    </details>
    <button type="button" disabled={busy} onClick={() => void run()}>{busy ? '复核中…' : '复核上次方案'}</button>
    {error && <p role="alert">{error}</p>}
    {value && <>
      <p>{value.decision_revision_ref} · {value.decision_state} · 复核 {value.review_due_at}</p>
      {value.policy_changed && <p role="status">ACTIVE 政策已变化。请回到预览重新审议原计划。</p>}
      <p>当前合格可投资分母 {value.current_investable_denominator_cny ?? 'UNKNOWN'}</p>
      {value.checkpoints.map(stage => <section key={stage.item_id}>
        <h5>{stage.stage_label} · {stage.observation_status}</h5>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead><tr><th>动作</th><th>计划值</th><th>当前事实</th><th>剩余</th><th>状态观察</th><th>执行证明</th></tr></thead>
            <tbody>{stage.actions.map(action => <tr key={action.action_id}>
              <td>{action.action_id} {action.symbol}</td>
              <td>{action.target_amount_cny ?? action.target_principal_cny ?? '—'}</td>
              <td>{action.current?.current_amount_cny ?? action.current?.current?.principal_cny ?? 'UNKNOWN'}</td>
              <td>{action.remaining_cny ?? 'UNKNOWN'}</td>
              <td>{action.observation_status} {action.comparability_issue}</td>
              <td>{action.execution.status} {action.execution.issue} {action.execution.sync_status_at_readback}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </section>)}
      {Object.entries(value.risk_reassessment ?? {}).map(([key, risk]) =>
        <p key={key}>{risk.current_fact?.subject ?? '原跟踪风险'} · {risk.status}</p>)}
      <p>下次复核 {value.next_review}</p>
      <details><summary>当前配置、穿透和来源明细</summary>
        <pre className="max-h-64 overflow-auto text-xs">{JSON.stringify(value, null, 2)}</pre>
      </details>
    </>}
  </section>
}

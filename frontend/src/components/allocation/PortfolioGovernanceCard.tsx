import { useEffect, useState } from 'react'
import PortfolioReconciliation from './PortfolioReconciliation'

type Item = {
  item_id: string; kind: string; reason: string; review_due_at: string; expires_at: string; revoke_when: string
}
type Entry = { proposal: Item; disposition_state: string; event_states: string[]; checkpoint: unknown; actual_risk: unknown }
type Preview = {
  request_id: string; preview_token: string; preview_fingerprint: string; expires_at: string
  payload: {
    items: Entry[]; qualification: { status: string; issues: string[] }
    request: { selected_path?: string; bundle_id: string }
    policy_reference: { bundle_version: number }
    simulation: { paths: Record<string, { label: string; investment_assets_cny: string | null; liabilities_cny: string | null }[]> } | null
  }
}
type RecordSummary = { bundle_id: string; revision: number; review_due_at: string; bundle_state: string }
type Records = { capabilities?: string[]; records: RecordSummary[]; followups: { decision_revision_ref: string; review_due_at: string }[]
  risk_events: { risk_key: string; state: string }[] }
type Risk = { subject: string; rule: { rule_id: string; action: string }; triggered_classes?: string[] }

const route = '/api/allocation/governance'

export default function PortfolioGovernanceCard({ snapshotId, snapshotSha, policySha, scenarioInput }:
  { snapshotId?: string; snapshotSha?: string; policySha?: string; scenarioInput?: object }) {
  const [records, setRecords] = useState<Records | null>(null)
  const [input, setInput] = useState('')
  const [risks, setRisks] = useState<Record<string, Risk> | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [checked, setChecked] = useState<string[]>([])
  const [history, setHistory] = useState<RecordSummary[]>([])
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const source = snapshotId && snapshotSha && policySha ? { snapshot_id: snapshotId, snapshot_sha256: snapshotSha, policy_bundle_sha256: policySha } : null

  useEffect(() => {
    const abort = new AbortController()
    fetch(route, { signal: abort.signal }).then(async response => {
      if (response.ok) {
        const data = await response.json() as Records
        if (Array.isArray(data.records) && Array.isArray(data.followups) && Array.isArray(data.risk_events)) setRecords(data)
      }
    }).catch(() => {})
    return () => abort.abort()
  }, [])

  async function request(path: string, body?: object) {
    const response = await fetch(route + path, body ? {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    } : undefined)
    const data = await response.json()
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请检查输入与基准证据')
    return data
  }

  async function perform(operation: () => Promise<void>) {
    setError(''); setMessage(''); setBusy(true)
    try { await operation() } catch (reason) {
      setError(reason instanceof Error ? reason.message : '组合治理暂不可用')
    } finally { setBusy(false) }
  }

  if (!records) return null
  const canPreview = records.capabilities?.includes('preview') ?? true
  const canConfirm = records.capabilities?.includes('confirm') ?? true
  const stageRows = preview?.payload.request.selected_path && preview.payload.simulation
    ? preview.payload.simulation.paths[preview.payload.request.selected_path] : []

  function loadExample() {
    const bundle = 'portfolio-' + crypto.randomUUID()
    const next = new Date(Date.now() + 7 * 86400000).toISOString()
    const expiry = new Date(Date.now() + 14 * 86400000).toISOString()
    if (scenarioInput) setInput(JSON.stringify({ bundle_id: bundle, request_id: bundle,
      expected_revision: 0, scenario: scenarioInput, selected_path: 'A', items: [{
        item_id: 'stage-hold', kind: 'PLANNED_TRANSITION_HOLD', reason: '填写决定理由', stage_index: 1,
        review_due_at: next, expires_at: expiry, revoke_when: '填写撤销或重新审议条件',
      }] }, null, 2))
    else {
      const firstRisk = Object.keys(risks ?? {})[0]
      if (!firstRisk) { setMessage('请先运行 D 模拟，或读取正式风险候选。'); return }
      setInput(JSON.stringify({ bundle_id: bundle, request_id: bundle, expected_revision: 0, items: [{
        item_id: 'risk-open', kind: 'RISK_EVENT', event_action: 'OPEN', risk_key: firstRisk,
        reason: '填写人工复核理由', review_due_at: next, expires_at: expiry, revoke_when: '填写撤销或重新审议条件',
      }] }, null, 2))
    }
    setPreview(null); setChecked([])
  }

  function confirm(cancel: boolean) {
    if (!preview || !canConfirm) return
    void perform(async () => {
      const saved = await request('/commit', { request_id: preview.request_id, preview_token: preview.preview_token,
        preview_fingerprint: preview.preview_fingerprint, selected_item_ids: cancel ? [] : checked,
        confirmed: !cancel, confirmation: cancel ? '取消' : '保存这次组合决定' })
      setPreview(null); setChecked([])
      if (saved.saved) {
        setMessage('已保存并读回确认版本。')
        setRecords(await request('') as Records)
      } else setMessage('预览已取消。')
    })
  }

  return <details className="my-4">
    <summary>组合人工决策与复核</summary>
    {canConfirm
      ? <p>按选中事项保存人工判断。场景阶段和检查点用于后续复核。</p>
      : <p>{canPreview ? '可预览组合决定，保存权限未开启。' : '当前仅可读取组合决定和复核结果。'}</p>}
    {!source && <p>暂无可用于当前配置复核的快照。历史决定仍可查看。</p>}
    {source && <>
    <button type="button" disabled={busy} onClick={() => void perform(async () => {
      const result = await request('/risk-preview', source)
      setRisks(result.actual_risk_candidates)
      if (result.qualification.status !== 'QUALIFIED') setMessage('资产资格 UNKNOWN，正式风险候选不可用。')
    })}>读取正式风险候选</button>
    {risks && Object.entries(risks).map(([key, risk]) => <p key={key}>
      {risk.subject} · {risk.rule.rule_id} · {risk.rule.action} {risk.triggered_classes?.join('、')}
    </p>)}
    </>}
    {canPreview && source && <>
    <button type="button" disabled={busy} onClick={loadExample}>载入可编辑草案</button>
    <label className="block">决策草案 JSON
      <textarea className="block w-full rounded border p-2 font-mono text-sm" rows={8} value={input} disabled={busy}
        onChange={event => { setInput(event.target.value); setPreview(null); setChecked([]) }} />
    </label>
    <button type="button" disabled={busy || !input.trim()} onClick={() => void perform(async () => {
      setPreview(null); setChecked([])
      const draft = JSON.parse(input)
      setPreview(await request('/preview', { ...draft, ...source }) as Preview)
    })}>预览组合决定</button>
    </>}
    {preview && canPreview && source && <section aria-label="组合决定预览">
      <h4>选择本次保存事项</h4>
      <p>政策 v{preview.payload.policy_reference.bundle_version} · 数据 {preview.payload.qualification.status}
        {' · '}预览到期 {preview.expires_at}</p>
      {preview.payload.qualification.issues.length > 0 && <p>{preview.payload.qualification.issues.join(' / ')}</p>}
      {stageRows && stageRows.map(row => <p key={row.label}>
        场景 {row.label} · 分母 {row.investment_assets_cny ?? 'UNKNOWN'} · 债务 {row.liabilities_cny ?? 'UNKNOWN'}
      </p>)}
      {preview.payload.items.map(entry => <div className="my-2 border p-2" key={entry.proposal.item_id}>
        <label><input type="checkbox" disabled={busy || !canConfirm} checked={checked.includes(entry.proposal.item_id)}
          onChange={event => setChecked(old => event.target.checked ? [...old, entry.proposal.item_id]
            : old.filter(id => id !== entry.proposal.item_id))} />
        {' '}{entry.proposal.item_id} · {entry.proposal.kind} · {entry.disposition_state}</label>
        <p>{entry.proposal.reason}</p>
        <p>复核 {entry.proposal.review_due_at} · 期限 {entry.proposal.expires_at}</p>
        <p>重新审议或撤销条件 {entry.proposal.revoke_when}</p>
        <details><summary>风险与检查点</summary>
          <pre className="max-h-48 overflow-auto text-xs">{JSON.stringify(
            { actual: entry.actual_risk, states: entry.event_states, checkpoint: entry.checkpoint }, null, 2)}</pre>
        </details>
      </div>)}
      {canConfirm && <>
      <button type="button" disabled={!checked.length || busy} onClick={() => confirm(false)}>
        保存这次组合决定（{checked.length} 项）
      </button>
      <button type="button" disabled={busy} onClick={() => confirm(true)}>取消预览</button>
      </>}
    </section>}
    {message && <p role="status">{message}</p>}
    {error && <p role="alert">{error}</p>}
    <h4>历史决策</h4>
    {records.records.map(row => <p key={row.bundle_id}>
      {row.bundle_id} · v{row.revision} · {row.bundle_state} · 复核 {row.review_due_at}{' '}
      <button type="button" disabled={busy} onClick={() => void perform(async () => {
        const value = await request('?bundle_id=' + encodeURIComponent(row.bundle_id))
        setHistory(value.records)
      })}>查看历史</button>
    </p>)}
    {history.map(row => <p key={row.bundle_id + row.revision}>{row.bundle_id} · 历史 v{row.revision} · {row.bundle_state}</p>)}
    {snapshotId && snapshotSha && <PortfolioReconciliation key={snapshotSha} snapshotId={snapshotId} snapshotSha={snapshotSha} />}
    <h4>到期复核</h4>
    {records.followups.map(row => <p key={row.decision_revision_ref}>{row.decision_revision_ref} · {row.review_due_at}</p>)}
  </details>
}

import { useState } from 'react'
import PortfolioGovernanceCard from './PortfolioGovernanceCard'
import { fmtCny } from '@/lib/fmt'

type Stage = {
  label: string
  investment_assets_cny: string | null
  liabilities_cny: string | null
  newly_breached_issuers: string[]
  cash: Record<string, { account: string; currency: string; amount: string | null; state: string; availability_basis?: string }>
  allocation: { secondary?: Record<string, { amount_cny: string | null; gap_pp: string | null }> }
  concentration: { issuers?: Record<string, {
    issuer_name: string; known_lower_bound_pct: string | null; scenario_risk_status: string
  }> }
  actions: { action_id: string; blocked_reason: string | null }[]
}

type Result = { basis: string; paths: { A: Stage[]; B?: Stage[] } }

const money = (value: string | null | undefined) => value == null ? 'UNKNOWN' : fmtCny(Number(value))

export default function PortfolioTransition({ snapshotId, snapshotSha, policySha }:
  { snapshotId: string; snapshotSha: string; policySha: string }) {
  const [input, setInput] = useState('')
  const [scenario, setScenario] = useState<object>()
  const [result, setResult] = useState<Result | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const example = {
    paths: {
      A: [{ label: 'S1', actions: [
        { action_id: 'sale', kind: 'SELL_TO_CASH', position_ref: 'position:123', amount: '100', fee: '0',
          outcome: 'APPLIED', assumption: '研究示例，假设款项已结算，金额为持仓原币' },
        { action_id: 'buy', kind: 'BUY_WITH_CASH', cash_ref: 'action:sale', symbol: 'GOOG:US', amount: '100',
          fee: '0', outcome: 'APPLIED', assumption: '假设用相同账户的已结算美元成功买入' },
      ] }],
      B: [{ label: 'S1', actions: [
        { action_id: 'sale', kind: 'SELL_TO_CASH', position_ref: 'position:123', amount: '100', fee: '0',
          outcome: 'UNSETTLED', assumption: '假设卖出款仍未结算' },
      ] }],
    },
  }

  async function run() {
    setBusy(true)
    setError('')
    setResult(null)
    setScenario(undefined)
    try {
      const plan = JSON.parse(input)
      if (!plan || typeof plan !== 'object' || Array.isArray(plan)) throw new Error('请填写方案对象')
      const normalized = { ...plan, snapshot_id: snapshotId, snapshot_sha256: snapshotSha,
        policy_bundle_sha256: policySha }
      const response = await fetch('/api/allocation/simulate-transition', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(normalized),
      })
      const body = await response.json()
      if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '请检查方案字段与金额')
      setResult(body as Result)
      setScenario(normalized)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '模拟不可用')
    } finally {
      setBusy(false)
    }
  }

  return <>
  <details className="my-4">
    <summary>多阶段资产修复模拟</summary>
    <p>使用同一冻结快照比较路径 A/B。所有动作、费用和到账结果均为情景假设。</p>
    <button type="button" onClick={() => setInput(JSON.stringify(example, null, 2))}>载入可编辑的 A/B 示例</button>
    <label className="block">方案 JSON
      <textarea className="block w-full rounded border p-2 font-mono text-sm" rows={9} value={input}
        onChange={event => { setInput(event.target.value); setResult(null); setScenario(undefined) }} disabled={busy} />
    </label>
    <button type="button" disabled={busy || !input.trim()} onClick={() => void run()}>
      {busy ? '计算中…' : '运行模拟'}
    </button>
    {error && <p role="alert">{error}</p>}
    {result && <>
      <p>{result.basis === 'QUALIFIED_BASELINE_SCENARIO' ? '合格基准 · 情景结果' : '假设基准 · 研究结果'}</p>
      <div className="grid gap-4 lg:grid-cols-2">
        {Object.entries(result.paths).map(([name, stages]) => <section key={name}>
          <h4>路径 {name}</h4>
          {stages.map((stage, index) => <div className="my-3 rounded border p-3" key={index}>
            <h5>{stage.label}</h5>
            <p>可投资分母 {money(stage.investment_assets_cny)} · 债务 {money(stage.liabilities_cny)}</p>
            <p>IB01 {money(stage.allocation.secondary?.ib01?.amount_cny)} · CBU3 {money(stage.allocation.secondary?.cbu3?.amount_cny)}
              {' · '}投资现金 {money(stage.allocation.secondary?.cash?.amount_cny)}</p>
            <p>新增场景越线 {stage.newly_breached_issuers.join('、') || '—'}</p>
            {Object.entries(stage.cash).map(([ref, lot]) => <p key={ref}>
              {ref} · {lot.account} · {lot.amount ?? 'UNKNOWN'} {lot.currency} · {lot.state}
              {lot.availability_basis === 'ASSUMED_FROM_FROZEN_BALANCE' && ' · 冻结余额可用性为假设'}
            </p>)}
            {stage.actions.filter(action => action.blocked_reason).map(action =>
              <p key={action.action_id}>{action.action_id} · {action.blocked_reason}</p>)}
            <details>
              <summary>阶段配置、集中度与证据</summary>
              <ul>{Object.entries(stage.concentration.issuers ?? {}).map(([issuer, entry]) => <li key={issuer}>
                {entry.issuer_name} · {entry.known_lower_bound_pct == null ? '比例 UNKNOWN'
                  : `下限 ${Number(entry.known_lower_bound_pct).toFixed(2)}%`} · {entry.scenario_risk_status}
              </li>)}</ul>
              <pre className="max-h-80 overflow-auto text-xs">{JSON.stringify(stage, null, 2)}</pre>
            </details>
          </div>)}
        </section>)}
      </div>
    </>}
  </details>
  <PortfolioGovernanceCard snapshotId={snapshotId} snapshotSha={snapshotSha} policySha={policySha} scenarioInput={scenario} />
  </>
}

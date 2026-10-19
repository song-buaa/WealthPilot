import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'

interface View {
  snapshot_id: string
  captured_at: string
  context_current: boolean
  data_quality: { status: string; issues: string[]; can_propose_rebalance: boolean;
    accounts: { account_ref: string; broker: string; account_synced_at: string | null; price_as_of?: string | null }[] }
  views: {
    wealth_summary: { total_assets: number; total_liabilities: number; net_worth: number }
    get_investment_details: { items: { id: number; name: string; symbol: string; account_ref: string; quantity: number;
      original_currency: string; cost_price: number; market_value_cny: number }[] }
  }
  issuer_exposure: { companies: { issuer_id: string; name: string; market_value_cny: number; members: { id: number; symbol: string; quantity: number; cost_price: number; currency: string }[] }[] }
}

async function call<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch('/api/asset-analysis/views/' + path, options)
  if (!response.ok) throw new Error('固定资产视图不可用，请返回 ChatGPT 查看准备状态或发起新的分析任务。')
  return response.json()
}

export default function AssetView() {
  const { snapshotId = '' } = useParams()
  const [view, setView] = useState<View | null>(null)
  const [error, setError] = useState('')
  const [conclusion, setConclusion] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let active = true
    call<View>(encodeURIComponent(snapshotId)).then(v => { if (active) setView(v) })
      .catch((e: Error) => { if (active) setError(e.message) })
    return () => { active = false }
  }, [snapshotId])
  async function review() {
    setBusy(true); setError('')
    try {
      const result = await call<{ conclusion: string; snapshot_id: string }>(encodeURIComponent(snapshotId) + '/review', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: '评估本次固定资产视图的投资组合风险与配置情况', snapshot_id: snapshotId }),
      })
      if (result.snapshot_id !== snapshotId) throw new Error('分析输入版本不一致，请重新准备。')
      setConclusion(result.conclusion)
    } catch (e) { setError(e instanceof Error ? e.message : '分析暂不可用') }
    finally { setBusy(false) }
  }
  const money = (v: number) => v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return <main className="p-6 space-y-5">
    <h1 className="text-xl font-semibold">本次分析的固定资产视图</h1>
    <p className="text-sm break-all" data-testid="asset-view-id">{snapshotId}</p>
    {error && <p role="alert">{error}</p>}
    {view && <>
      <p>捕获时间：{view.captured_at}。该时间不是证券估值或行情时间。</p>
      {(!view.data_quality.can_propose_rebalance || !view.context_current) && <div role="status" className="rounded-lg bg-amber-50 p-4 text-amber-900">
        数据部分过期或证据不足，仅作一般分析，不能直接用于精确调仓。
        <p className="text-sm">{view.data_quality.issues.join('、')}</p>
      </div>}
      <div className="grid gap-4 sm:grid-cols-3">
        <p>总资产 ¥{money(view.views.wealth_summary.total_assets)}</p>
        <p>负债 ¥{money(view.views.wealth_summary.total_liabilities)}</p>
        <p>净资产 ¥{money(view.views.wealth_summary.net_worth)}</p>
      </div>
      <h2 className="font-semibold">账户来源与时间</h2>
      {view.data_quality.accounts.map(a => <p key={a.account_ref}>{a.broker}：账户同步 {a.account_synced_at ?? '未知'}；证券估值 {a.price_as_of ?? '未知'}</p>)}
      <h2 className="font-semibold">证券明细</h2>
      <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr>
        <th>证券</th><th>账户</th><th>数量</th><th>原币成本单价</th><th>CNY 市值</th>
      </tr></thead><tbody>{view.views.get_investment_details.items.map(p => <tr key={p.id}>
        <td>{p.name} {p.symbol}</td><td>{p.account_ref}</td><td>{p.quantity}</td>
        <td>{p.cost_price} {p.original_currency}</td><td>{money(p.market_value_cny)}</td>
      </tr>)}</tbody></table></div>
      <h2 className="font-semibold">有证据的公司经济敞口</h2>
      {view.issuer_exposure.companies.map(c => <div key={c.issuer_id}>
        <p>{c.name}：¥{money(c.market_value_cny)}</p>
        <p className="text-sm">{c.members.map(p => `${p.symbol} ${p.quantity}（成本 ${p.cost_price} ${p.currency}）`).join('；')}</p>
      </div>)}
      <p className="text-sm">未知发行人不猜测；不换算 ADR 数量、不穿透基金。持仓与手工财富口径沿用原服务。</p>
      <button className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50" onClick={review} disabled={busy || !view.context_current}>
        {busy ? '分析中…' : '使用此版本进行组合评估'}
      </button>
      {conclusion && <article className="whitespace-pre-wrap" data-testid="asset-view-conclusion">{conclusion}</article>}
    </>}
  </main>
}

import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'

interface View {
  snapshot_id: string
  captured_at: string
  context_current: boolean
  data_quality: { status: string; issues: string[]; can_propose_rebalance: boolean;
    analysis_eligibility?: Record<'A' | 'B' | 'C', { allowed: boolean; blockers: { code: string; symbol?: string; id?: number; required_currency?: string }[] }>;
    qualification_scope?: { symbol: string | null; analysis_scope: 'investments' | 'net_worth' };
    manual_review_required?: { id: number; name: string; current_value: number; value_as_of: string; reason_codes: string[]; update_path: string }[];
    accounts: { account_ref: string; broker: string; account_synced_at: string | null; price_as_of?: string | null;
      fetched_at?: string | null; queried_at?: string | Record<string, string> | null;
      positions_as_of?: string | null; cash_as_of?: string | null;
      last_attempt_time?: string | null; last_attempt_status?: string; last_attempt_reason_code?: string | null;
      last_good_quality?: string | null }[] }
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
  return <FixedAssetView key={snapshotId} snapshotId={snapshotId} />
}

function FixedAssetView({ snapshotId }: { snapshotId: string }) {
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
      const result = await call<{ conclusion: string; snapshot_id: string;
        data_quality: { asset_view: View['data_quality']; context_current: boolean } }>(encodeURIComponent(snapshotId) + '/review', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: '评估本次固定资产视图的投资组合风险与配置情况', snapshot_id: snapshotId }),
      })
      if (result.snapshot_id !== snapshotId) throw new Error('分析输入版本不一致，请重新准备。')
      if (!result.data_quality?.asset_view || typeof result.data_quality.context_current !== 'boolean') {
        throw new Error('分析结果缺少最终数据质量，请重新准备。')
      }
      setView(current => current && ({ ...current, data_quality: result.data_quality.asset_view,
        context_current: result.data_quality.context_current }))
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
      {view.data_quality.analysis_eligibility && <section aria-label="分析资格" className="space-y-2">
        <h2 className="font-semibold">分析资格</h2>
        <p>分析范围：{view.data_quality.qualification_scope?.symbol ?? '全组合'}；用途：{view.data_quality.qualification_scope?.analysis_scope === 'net_worth' ? '净资产及偿债规划' : '投资资产分析'}。</p>
        {(['A', 'B', 'C'] as const).map(level => {
          const q = view.data_quality.analysis_eligibility![level]
          return <div key={level}>
            <p>{level} · {{ A: '战略配置分析', B: '调仓金额规划', C: '精确交易参数' }[level]}：{q.allowed ? '可进行' : '条件不足'}</p>
            {!q.allowed && <p className="text-sm">{q.blockers.map(b => `${b.symbol ?? (b.id ? `项目 ${b.id}` : '')} ${b.required_currency ?? ''} ${b.code}`).join('；')}</p>}
          </div>
        })}
        <p className="text-sm">A/B/C 是不同用途，非逐级授权；单标 C 不代表全组合 B 获准。资格仅适用于本次分析范围；不能替代实盘行情、WhatIf、资金检查和 Web 最终人工确认。</p>
      </section>}
      <div className="grid gap-4 sm:grid-cols-3">
        <p>总资产 ¥{money(view.views.wealth_summary.total_assets)}</p>
        <p>负债 ¥{money(view.views.wealth_summary.total_liabilities)}</p>
        <p>净资产 ¥{money(view.views.wealth_summary.net_worth)}</p>
      </div>
      <h2 className="font-semibold">账户来源与时间</h2>
      {view.data_quality.accounts.map(a => <p key={a.account_ref}>
        {a.broker}：最近尝试 {a.last_attempt_time ?? '未知'}（{a.last_attempt_status ?? '未知'}）
        {a.last_attempt_reason_code && <>；失败原因 {a.last_attempt_reason_code}</>}；
        最近成功同步 {a.account_synced_at ?? '未知'}；成功数据质量 {a.last_good_quality ?? '未知'}；证券估值 {a.price_as_of ?? '未知'}；
        抓取时间 {a.fetched_at ?? '未知'}；来源查询 {typeof a.queried_at === 'object' ? JSON.stringify(a.queried_at) : a.queried_at ?? '未知'}；
        持仓有效时间 {a.positions_as_of ?? '未知'}；现金有效时间 {a.cash_as_of ?? '未知'}
      </p>)}
      {!!view.data_quality.manual_review_required?.length && <section aria-label="人工核对清单">
        <h2 className="font-semibold">待用户复核的手工资产与负债</h2>
        {view.data_quality.manual_review_required.map(item => <p key={item.id}>
          {item.name}：¥{money(item.current_value)}；有效日期 {item.value_as_of ?? '未知'}；{item.reason_codes.join('、')}。
          <a className="underline" href={item.update_path}>使用原财富页面更新</a>
        </p>)}
      </section>}
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

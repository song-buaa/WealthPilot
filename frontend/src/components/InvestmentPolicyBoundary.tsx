import { useEffect, useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'

interface PolicyContext {
  mode: 'LEGACY' | 'POLICY_ACTIVE' | 'UNAVAILABLE'
  status: string
  reason?: string
  bundle_id?: string
  bundle_version?: number
  bundle_sha?: string
  precision?: string
  synthetic?: boolean
  source_hashes?: Record<string,string>
  risk_diagnostic?: { status: string; asset_snapshot_id?: string; asset_issues?: string[]; unknown?: { reason: string }[] }
  targets?: { neutral: Record<string, string>; etf_internal: Record<string, string>; direct_equity: Record<string, string>; growth_total: string; stable_total: string }
  rules?: { rule_id: string; operator: string; threshold: string | null; action: string; source_clause: { section: string; source_sha256: string } }[]
  philosophy?: { text: string }
}

/** Read-only boundary: never mount a legacy editor while policy state is unknown. */
export default function InvestmentPolicyBoundary({ children, view = 'portfolio', activeChildren }: {
  children: ReactNode; view?: string; activeChildren?: ReactNode
}) {
  const [context, setContext] = useState<PolicyContext | null>(null)
  useEffect(() => {
    let cancelled = false
    fetch('/api/discipline/policy-context?view=' + encodeURIComponent(view))
      .then(async response => {
        if (!response.ok) throw new Error('POLICY_CONTEXT_UNAVAILABLE')
        return response.json() as Promise<PolicyContext>
      })
      .then(value => { if (!cancelled) setContext(value) })
      .catch(() => { if (!cancelled) setContext({ mode: 'UNAVAILABLE', status: 'UNAVAILABLE' }) })
    return () => { cancelled = true }
  }, [view])
  if (!context) return <p role="status">核对政策版本…</p>
  if (context.mode === 'LEGACY') return <>{children}</>
  if (context.mode !== 'POLICY_ACTIVE') return <section role="alert">政策状态不可核验，不能回退旧规则或给出精确操作结论。</section>
  if (activeChildren) return <>{activeChildren}</>
  return <section aria-label="统一投资政策" style={{ padding: 20, background: '#fff', border: '1px solid #ddd', borderRadius: 12 }}>
    <h2>统一投资政策 · {context.status}{context.synthetic ? ' · TEST ONLY 隔离模拟' : ''}</h2>
    <p>版本 {context.bundle_version} · {context.bundle_id}</p>
    <small style={{ overflowWrap: 'anywhere' }}>SHA256 {context.bundle_sha}</small>
    <p>只读受控版本。政策不是交易授权；完整可投资资产、养老金排除、跨账户去重及外部黄金/BTC覆盖未核验时为 UNKNOWN。</p>
    {context.source_hashes && <details><summary>批准原文来源指纹</summary>{Object.entries(context.source_hashes).map(([key,value]) => <p key={key} style={{ overflowWrap:'anywhere' }}>{key}: {value}</p>)}</details>}
    {context.risk_diagnostic && <p role="status">当前偏差及风险资格：{context.risk_diagnostic.status}；{context.risk_diagnostic.asset_snapshot_id || '没有合格全组合资产快照'}。{context.risk_diagnostic.asset_issues?.join(' / ')}。UNKNOWN 不表示无风险。</p>}
    {context.targets && <>
      <h3>长期中性预算（合格可投资资产分母）</h3>
      <p>增长及另类 {(Number(context.targets.growth_total)*100).toFixed(2)}% / 稳健池 {(Number(context.targets.stable_total)*100).toFixed(2)}%</p>
      <table><tbody>{Object.entries(context.targets.neutral).map(([key, value]) => <tr key={key}><th>{key}</th><td>{(Number(value) * 100).toFixed(2)}%</td></tr>)}</tbody></table>
      <h3>ETF 内部结构</h3><p>{Object.entries(context.targets.etf_internal).map(([key,value]) => key + ' ' + (Number(value)*100).toFixed(2) + '%').join(' / ')}</p>
      <h3>直接个股预算</h3><p>{Object.entries(context.targets.direct_equity).map(([key,value]) => key + ' ' + (Number(value)*100).toFixed(2) + '%').join(' / ')}</p>
    </>}
    {context.rules && <><h3>正式规则与来源</h3><ul>{context.rules.map(rule => <li key={rule.rule_id}>
      {rule.rule_id} · {rule.operator} {rule.threshold === null ? '' : (Number(rule.threshold)*100).toFixed(2)+'%'} · {rule.action} · §{rule.source_clause.section}
    </li>)}</ul></>}
    {context.philosophy && <ReactMarkdown>{context.philosophy.text}</ReactMarkdown>}
  </section>
}

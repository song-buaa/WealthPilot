import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle, Loader2, RefreshCw, ShieldAlert, X } from 'lucide-react'

import { executionBatchApi, type ExecutionBatchResponse } from '@/lib/api'


const LIVE_CONFIRMATION_TEXT = '确认并提交 4 笔 IBKR 实盘限价单'

function usd(value: number | null | undefined) {
  return value == null ? '—' : `$${value.toLocaleString('en-US', { maximumFractionDigits: 2 })}`
}

function price(value: number | null | undefined) {
  return value == null ? '—' : `$${value.toLocaleString('en-US', { maximumFractionDigits: 6 })}`
}

export default function ExecutionBatchCard({
  batch,
  onChanged,
}: {
  batch: ExecutionBatchResponse
  onChanged: (batch: ExecutionBatchResponse) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [ack, setAck] = useState(false)
  const [manualLimits, setManualLimits] = useState<Record<string, string>>(() =>
    Object.fromEntries(batch.legs.map(leg => [leg.user_alias, leg.final_limit?.toString() || ''])),
  )
  const [safety, setSafety] = useState<{
    ibkr_read_only_mode: boolean
    live_trading_enabled: boolean
  } | null>(null)

  useEffect(() => {
    executionBatchApi.safety().then(setSafety).catch(() => setSafety(null))
  }, [])

  function update(next: ExecutionBatchResponse) {
    onChanged(next)
  }

  async function refresh() {
    setBusy(true); setError(null)
    try { update(await executionBatchApi.refresh(batch.id)) }
    catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  async function confirm() {
    setBusy(true); setError(null)
    try { update(await executionBatchApi.confirm(batch.id)) }
    catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  async function applyManualLimits() {
    const values = Object.fromEntries(
      Object.entries(manualLimits).map(([alias, value]) => [alias, Number(value)]),
    )
    if (Object.values(values).some(value => !Number.isFinite(value) || value <= 0)) {
      setError('请为 4 个标的输入有效的手工 Limit')
      return
    }
    setBusy(true); setError(null)
    try { update(await executionBatchApi.applyManualLimits(batch.id, values)) }
    catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  async function submitAll() {
    if (!ack) return
    setBusy(true); setError(null)
    try {
      for (const leg of batch.legs) {
        if (leg.linked_order_id || ['FILLED', 'CANCELLED'].includes(leg.status)) continue
        const order = await executionBatchApi.submitLeg(
          batch.id, leg.id, batch.confirmation_version, LIVE_CONFIRMATION_TEXT,
        )
        const refreshed = await executionBatchApi.get(batch.id)
        update(refreshed)
        if (['unknown', 'rejected'].includes(order.status)) {
          throw new Error(`${leg.user_alias}=${order.status}，已停止后续订单`)
        }
      }
      update(await executionBatchApi.get(batch.id))
      setModalOpen(false)
    } catch (err) {
      setError((err as Error).message)
      update(await executionBatchApi.get(batch.id).catch(() => batch))
      setModalOpen(false)
    } finally { setBusy(false) }
  }

  const liveReady = safety?.live_trading_enabled === true
    && safety.ibkr_read_only_mode === false
    && batch.legs.every(leg => leg.market_open)
  const attention = Array.isArray(batch.attention_reason)
    ? batch.attention_reason
    : batch.attention_reason ? [batch.attention_reason] : []

  return (
    <div style={{ border: '1px solid #93C5FD', borderRadius: 12, background: '#fff', overflow: 'hidden', marginBottom: 14 }}>
      <div style={{ padding: '13px 16px', background: '#EFF6FF', display: 'flex', alignItems: 'center', gap: 10 }}>
        <ShieldAlert size={17} color="#1D4ED8" />
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: '#1E3A8A' }}>IBKR Case 1 · 4 标的交易执行批次</div>
          <div style={{ fontSize: 11, color: '#64748B', marginTop: 2 }}>
            {batch.account_masked} · USD Cash · {batch.status} · Confirmation v{batch.confirmation_version}
          </div>
        </div>
        <span style={{ fontSize: 11, fontWeight: 700, color: batch.status === 'READY' ? '#047857' : '#475569' }}>{batch.status}</span>
      </div>

      <div style={{ padding: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(135px, 1fr))', gap: 10, marginBottom: 14 }}>
          {[
            ['权威 USD Cash', usd(batch.usable_cash)],
            ['预计订单金额', usd(batch.estimated_total)],
            ['WhatIf 费用', usd(batch.estimated_fees)],
            ['安全垫', usd(batch.safety_cushion)],
            ['预计剩余', usd(batch.estimated_residual)],
          ].map(([label, value]) => (
            <div key={label} style={{ background: '#F8FAFC', borderRadius: 8, padding: '9px 10px' }}>
              <div style={{ fontSize: 10, color: '#94A3B8' }}>{label}</div>
              <div style={{ fontSize: 13, fontWeight: 700, color: '#1F2937', marginTop: 3 }}>{value}</div>
            </div>
          ))}
        </div>

        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead><tr style={{ color: '#64748B', textAlign: 'left', borderBottom: '1px solid #E5E7EB' }}>
              {['Leg', 'Contract / Identity', 'Target', 'Quote / Limit', 'Qty / Notional', 'WhatIf', '状态'].map(item => (
                <th key={item} style={{ padding: '7px 6px', fontWeight: 600 }}>{item}</th>
              ))}
            </tr></thead>
            <tbody>
              {batch.legs.map(leg => (
                <tr key={leg.id} style={{ borderBottom: '1px solid #F1F5F9', verticalAlign: 'top' }}>
                  <td style={{ padding: '9px 6px', fontWeight: 700 }}>{leg.sequence}. {leg.user_alias}</td>
                  <td style={{ padding: '9px 6px', lineHeight: 1.5 }}>
                    <div>conId {leg.resolved_con_id} · {leg.symbol}/{leg.local_symbol}</div>
                    <div>{leg.exchange} · {leg.currency} · {leg.stock_type}</div>
                    <div>{leg.isin}</div>
                    <div style={{ color: '#047857', fontWeight: 600 }}>Acc {leg.share_class_verification}</div>
                  </td>
                  <td style={{ padding: '9px 6px' }}>{leg.allocation_mode === 'REMAINDER' ? '动态剩余资金' : usd(leg.target_amount)}</td>
                  <td style={{ padding: '9px 6px', lineHeight: 1.5 }}>
                    <div>Ask {price(leg.quote_ask)} · {leg.quote_quality || 'MISSING'}</div>
                    <div>Limit {price(leg.final_limit)}</div>
                    {!leg.linked_order_id && (
                      <input
                        aria-label={`${leg.user_alias} 手工 Limit`}
                        value={manualLimits[leg.user_alias] || ''}
                        onChange={event => setManualLimits(current => ({ ...current, [leg.user_alias]: event.target.value }))}
                        placeholder="手工 Limit"
                        inputMode="decimal"
                        style={{ width: 88, marginTop: 4, padding: '3px 5px', border: '1px solid #CBD5E1', borderRadius: 5, fontSize: 11 }}
                      />
                    )}
                    <div>Rule {leg.market_rule_id} · {leg.quote_as_of ? new Date(leg.quote_as_of).toLocaleString('zh-CN') : '—'}</div>
                    <div style={{ color: leg.market_open ? '#047857' : '#B45309' }}>{leg.market_open ? 'MARKET OPEN' : 'MARKET CLOSED'}</div>
                  </td>
                  <td style={{ padding: '9px 6px' }}>{leg.final_quantity ?? '—'} 股<br />{usd(leg.estimated_notional)}</td>
                  <td style={{ padding: '9px 6px' }}>
                    {leg.what_if?.status === 'PASS'
                      ? <span style={{ color: '#047857' }}><CheckCircle size={12} /> {usd(Number(leg.what_if.commission || 0))}</span>
                      : <span style={{ color: '#B45309' }}>待校验</span>}
                  </td>
                  <td style={{ padding: '9px 6px', fontWeight: 600 }}>{leg.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {attention.length > 0 && (
          <div style={{ marginTop: 12, padding: '9px 10px', background: '#FFF7ED', borderRadius: 8, color: '#9A3412', fontSize: 11 }}>
            {attention.map((item, index) => <div key={index}>• {item}</div>)}
          </div>
        )}
        <div style={{ marginTop: 12, padding: '9px 10px', background: '#FEF2F2', borderRadius: 8, color: '#991B1B', fontSize: 11 }}>
          本交易计划由多笔独立券商订单组成，并非原子交易。部分订单可能成功而部分失败。
        </div>

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 13 }}>
          <button onClick={refresh} disabled={busy || Boolean(batch.legs.some(leg => leg.linked_order_id))} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '7px 11px', borderRadius: 7, border: '1px solid #CBD5E1', background: '#fff', cursor: 'pointer' }}>
            <RefreshCw size={13} />刷新只读事实
          </button>
          {batch.status === 'DRAFT' && (
            <button onClick={applyManualLimits} disabled={busy} style={{ padding: '7px 12px', borderRadius: 7, border: '1px solid #2563EB', background: '#EFF6FF', color: '#1D4ED8', fontWeight: 600, cursor: 'pointer' }}>校验手工 Limit</button>
          )}
          {batch.status === 'READY' && (
            <button onClick={confirm} disabled={busy} style={{ padding: '7px 12px', borderRadius: 7, border: 'none', background: '#2563EB', color: '#fff', fontWeight: 600, cursor: 'pointer' }}>确认交易计划</button>
          )}
          {batch.status === 'CONFIRMED' && (
            <button onClick={() => setModalOpen(true)} disabled={busy || !liveReady} style={{ padding: '7px 12px', borderRadius: 7, border: 'none', background: liveReady ? '#B91C1C' : '#D1D5DB', color: '#fff', fontWeight: 700, cursor: liveReady ? 'pointer' : 'not-allowed' }}>进入 IBKR 实盘提交确认</button>
          )}
        </div>
        {!liveReady && batch.status === 'CONFIRMED' && (
          <div style={{ textAlign: 'right', fontSize: 10, color: '#B45309', marginTop: 5 }}>当前仍为本地只读、Live Trading OFF 或 Market Closed，提交入口已硬禁用。</div>
        )}
        {error && <div style={{ marginTop: 8, color: '#DC2626', fontSize: 11 }}>{error}</div>}
      </div>

      {modalOpen && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(15,23,42,.58)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
          <div style={{ width: 'min(620px, 100%)', background: '#fff', borderRadius: 14, boxShadow: '0 20px 60px rgba(0,0,0,.25)' }}>
            <div style={{ padding: '14px 18px', borderBottom: '1px solid #E5E7EB', display: 'flex', alignItems: 'center' }}>
              <div style={{ flex: 1, fontSize: 16, fontWeight: 800, color: '#991B1B' }}>这是 IBKR 实盘订单</div>
              <button onClick={() => setModalOpen(false)} style={{ border: 0, background: 'none' }}><X size={18} /></button>
            </div>
            <div style={{ padding: 18 }}>
              <div style={{ display: 'flex', gap: 8, color: '#991B1B', fontSize: 12, lineHeight: 1.6 }}>
                <AlertTriangle size={18} style={{ flexShrink: 0 }} />
                <span>将按 IBTA → VDCA → CBU0 → IB01 顺序提交 4 笔真实 BUY LIMIT 订单。每一腿都会重新检查资金、报价、合约和 WhatIf；UNKNOWN/TIMEOUT/REJECTED 会立即停止。</span>
              </div>
              <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginTop: 16, fontSize: 12, color: '#334155' }}>
                <input type="checkbox" checked={ack} onChange={event => setAck(event.target.checked)} />
                <span>我已确认 IB Gateway 已关闭 Read-Only，并理解这些订单将进入真实账户。</span>
              </label>
              <button onClick={submitAll} disabled={!ack || busy} style={{ width: '100%', marginTop: 16, padding: '10px 14px', border: 0, borderRadius: 8, background: ack ? '#B91C1C' : '#D1D5DB', color: '#fff', fontWeight: 800, cursor: ack ? 'pointer' : 'not-allowed' }}>
                {busy && <Loader2 size={14} className="animate-spin" />} {LIVE_CONFIRMATION_TEXT}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

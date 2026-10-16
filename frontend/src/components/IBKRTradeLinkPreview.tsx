import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { executionBatchApi, type IBKRLinkPreview } from '@/lib/api'

/** Intent-only handoff. The existing ExecutionBatchCard remains the final authorization UI. */
export default function IBKRTradeLinkPreview({ preview, onClose, onCreated }: {
  preview: IBKRLinkPreview
  onClose: () => void
  onCreated: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const instrument = preview.instrument
  async function confirmIntent() {
    setBusy(true); setError('')
    try { await executionBatchApi.confirmTradeLinkIntent(preview); onCreated() }
    catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }
  return <Dialog.Root open onOpenChange={open => { if (!open && !busy) onClose() }}>
    <Dialog.Portal>
      <Dialog.Overlay style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.4)', zIndex: 1000 }} />
      <Dialog.Content style={{ position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
        width: 'min(640px, 94vw)', maxHeight: '90vh', overflow: 'auto', padding: 24,
        background: '#fff', borderRadius: 16, zIndex: 1001 }}>
        <Dialog.Title>IBKR 交易意图预览</Dialog.Title>
        <Dialog.Description>先核对意图，再进入原 IBKR 批次交易预览；此处不会提交订单。</Dialog.Description>
        <dl style={{ lineHeight: 1.9, margin: '16px 0' }}>
          <dt>标的 / 方向</dt><dd>{preview.order.symbol} / BUY</dd>
          <dt>目标买入金额（佣金另计）</dt><dd data-testid="ibkr-link-target">{preview.order.target_amount} USD</dd>
          <dt>合约标识</dt><dd>conId {instrument.con_id} / ISIN {instrument.isin}</dd>
          <dt>上市交易所 / 执行路由</dt><dd>{instrument.listing_exchange} / {instrument.execution_exchange}</dd>
          <dt>交易币种 / 份额 / 订单</dt><dd>{instrument.currency} / Acc 累积型 / LIMIT / GTC</dd>
        </dl>
        <p>使用现有 IBKR 配置账户的美元现金；请在批次预览再次核对账户。</p>
        <p>股数与限价由原交易服务读取 IBKR 合约、行情和现金后计算，也可在原页面核对限价。合约登记不代表当前 Gateway 已连接。</p>
        {(preview.safety.ibkr_read_only_mode || !preview.safety.live_trading_enabled) &&
          <p role="status">IBKR 当前保留只读保护，尚不可提交真实订单。</p>}
        {error && <div role="alert">{error}</div>}
        <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end', marginTop: 20, flexWrap: 'wrap' }}>
          <button disabled={busy} onClick={onClose}>取消</button>
          <button disabled={busy} onClick={confirmIntent}>{busy ? '生成预览中…' : '确认意图并生成 IBKR 交易预览'}</button>
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>
}

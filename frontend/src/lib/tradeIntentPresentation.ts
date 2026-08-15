import type { StructuredTradeIntent } from '@/lib/api'

export interface TradeIntentPanelSummary {
  type: string
  legCount: number
  action: string
}

export function summarizeTradeIntentForPanel(
  tradeIntent?: StructuredTradeIntent,
): TradeIntentPanelSummary | null {
  if (!tradeIntent) return null
  if (
    tradeIntent.readiness !== 'READY_FOR_CONFIRMATION'
    && tradeIntent.confirmation_status !== 'CONFIRMED'
  ) return null
  return {
    type: '多标的交易意图',
    legCount: tradeIntent.legs.length,
    action: tradeIntent.side.value === 'BUY'
      ? '买入'
      : String(tradeIntent.side.value ?? '待确认'),
  }
}

// Presentation only: authority remains OrderManager / ExecutionBatch / U2.
export const tradeLabels: Record<string, string> = {
  created: '待提交', pending: '待提交', submitted_to_broker: '已提交，等待成交',
  broker_pending: '已提交，等待券商确认或成交',
  partially_filled: '部分成交', filled: '全部成交', cancelled: '已撤销', rejected: '已拒绝',
  expired: '已过期', unknown: 'UNKNOWN / 待券商对账，请勿重复提交',
  SYNC_PENDING: '持仓待同步', SYNC_RUNNING: '持仓同步中', SYNCED: '持仓及现金已同步且覆盖成交',
  SYNC_DELAYED: '持仓同步延迟', SYNC_FAILED: '持仓同步失败，成交事实不变',
  SYNC_UNVERIFIED: 'SYNC_UNVERIFIED：覆盖尚未证实，不代表成交失败', NOT_REQUIRED: '暂无成交需要同步',
}
export function tradeError(message: string) {
  const rules: [RegExp, string][] = [
    [/STALE|expired|TTL|过期/i, '报价已过期，需要重新核对。'],
    [/INSUFFICIENT.*CASH|现金不足/i, '原币现金不足，请核对账户及预算。'],
    [/PREVIEW_CHANGED|confirmation.*(version|hash)|确认.*版本/i, '交易参数已变化，请重新核对并确认。'],
    [/market.*closed|交易时段/i, '当前交易时段不可用。'],
    [/UNKNOWN|TIMEOUT|超时|Failed to fetch|NetworkError/i, '订单或连接状态尚未确认，请查询原请求，勿重复提交。'],
    [/connect|连接|Gateway/i, '券商暂时无法连接，请稍后刷新核对。'],
    [/permission|资格/i, '当前账户的交易资格尚未确认。'],
  ]
  return `${rules.find(([pattern]) => pattern.test(message))?.[1] ?? '操作未完成，请核对原因。'}（诊断：${message}）`
}

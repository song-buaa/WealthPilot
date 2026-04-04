/**
 * 数字格式化工具 — 规范 6.0
 * 所有数字展示统一走这里，保证 tabular-nums 一致性
 */

/** 金额：千分位，0 位小数，前缀 ¥ */
export function fmtCny(v: number | null | undefined): string {
  if (v == null) return '—'
  return '¥' + Math.abs(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

/** 金额带符号（盈亏专用）：正 +¥xxx 绿，负 -¥xxx 红 */
export function fmtCnySigned(v: number): string {
  const abs = Math.abs(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
  return v >= 0 ? `+¥${abs}` : `-¥${abs}`
}

/** 百分比：1 位小数，如 12.5% */
export function fmtPct(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${v.toFixed(1)}%`
}

/** 涨跌幅：2 位小数，正数加 +，如 +3.45% / -2.10% */
export function fmtDelta(v: number | null | undefined): string {
  if (v == null) return '—'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

/** 数量/份额：无小数整数 */
export function fmtQty(v: number | null | undefined): string {
  if (v == null || v === 0) return '—'
  return Math.abs(v) >= 1
    ? Math.round(v).toLocaleString('zh-CN')
    : v.toFixed(2)
}

/** 外币金额（美元/港币） */
export function fmtFx(v: number | null | undefined, currency: string): string {
  if (v == null || v === 0) return '—'
  const prefix = currency === 'USD' ? '$' : currency === 'HKD' ? 'HK$' : '¥'
  return prefix + Math.abs(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

/** 杠杆倍数：2 位小数 + x，如 1.05x */
export function fmtLeverage(v: number): string {
  return `${v.toFixed(2)}x`
}

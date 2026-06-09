/**
 * 执行计划"说人话"展示工具。
 *
 * 基于 factor_snapshot 和 constraints_applied 的实际值动态生成可读描述。
 * 描述里出现的数字必须来自原始数据,不新编。
 */

const SOURCE_LABEL: Record<string, string> = {
  tiger: '老虎证券', futu: '富途', alphavantage: 'Alpha Vantage', none: '不可用',
}

const TREND_LABEL: Record<string, string> = {
  bullish: '偏强', bearish: '偏弱', neutral: '中性',
}

const DEGRADED_LABEL: Record<string, string> = {
  kline: 'K线走势数据', '52w_high_low': '52周高低点', price_percentile: '价格分位',
}

// ── 数据来源 ──

export function formatDataSource(dsm: Record<string, unknown>): string {
  const kSrc = SOURCE_LABEL[String(dsm.kline_source || '')] || String(dsm.kline_source || '未知')
  const pSrc = SOURCE_LABEL[String(dsm.price_source || '')] || String(dsm.price_source || '未知')
  const pts = Number(dsm.kline_points || 0)
  const rt = dsm.is_realtime ? '实时' : '延迟'

  if (dsm.kline_source === 'none' && dsm.price_source === 'none') {
    return '数据来源不可用'
  }

  const parts: string[] = []
  if (dsm.price_source && dsm.price_source !== 'none') {
    parts.push(`${rt}行情(${pSrc})`)
  }
  if (dsm.kline_source && dsm.kline_source !== 'none') {
    parts.push(`近${pts}日走势(${kSrc})`)
  }

  return `数据来源：${parts.join(' + ')}，数据完整`
}

export function formatDegraded(fields: string[]): string {
  if (!fields.length) return ''
  const names = fields.map(f => DEGRADED_LABEL[f] || f)
  return `部分数据缺失：${names.join('、')}，本计划未使用该项`
}

// ── 因子指标人话 ──

export interface FactorLine {
  label: string
  value: string
  desc: string
}

export function formatFactors(fs: Record<string, unknown>): FactorLine[] {
  const lines: FactorLine[] = []

  const vol = fs.volatility_annual as number | null
  if (vol != null) {
    const pct = (vol * 100).toFixed(1)
    const level = vol > 0.4 ? '波动较大' : vol > 0.25 ? '波动适中' : '波动较小'
    lines.push({
      label: '年化波动率',
      value: `${pct}%`,
      desc: `${level}，${vol > 0.35 ? '因此分批执行以降低择时风险' : '适合稳步建仓'}`,
    })
  }

  const pctile = fs.price_percentile as number | null
  if (pctile != null) {
    const pctVal = (pctile * 100).toFixed(1)
    const pos = pctile < 0.2 ? '接近52周低点' : pctile > 0.8 ? '接近52周高点' : '处于52周中间位置'
    lines.push({
      label: '价格分位',
      value: `${pctVal}%`,
      desc: `当前${pos}`,
    })
  }

  const dd = fs.drawdown_from_high as number | null
  if (dd != null) {
    const pct = Math.abs(dd * 100).toFixed(0)
    lines.push({
      label: '从高点回撤',
      value: `-${pct}%`,
      desc: `已从近期高点回落约${pct}%`,
    })
  }

  const trend = fs.trend_signal as string | null
  if (trend) {
    lines.push({
      label: '技术面趋势',
      value: TREND_LABEL[trend] || trend,
      desc: trend === 'bearish' ? '短期走势偏弱，注意控制节奏'
           : trend === 'bullish' ? '短期走势偏强'
           : '走势无明显方向',
    })
  }

  const rsi = fs.rsi14 as number | null
  if (rsi != null) {
    const level = rsi < 30 ? '超卖区间' : rsi > 70 ? '超买区间' : '正常区间'
    lines.push({
      label: 'RSI(14)',
      value: rsi.toFixed(1),
      desc: `处于${level}`,
    })
  }

  return lines
}

// ── 纪律约束人话 ──

export interface ConstraintLine {
  checked: boolean
  text: string
}

export function formatConstraints(
  ca: Record<string, unknown>,
  numTranches: number,
  targetPct: number,
): ConstraintLine[] {
  const lines: ConstraintLine[] = []
  const minBatch = Number(ca.min_batches_required || 2)
  const maxPos = Number(ca.max_position_pct || 0.4)
  const maxSingle = Number(ca.max_single_add_pct || 0.1)
  const minInterval = Number(ca.min_interval_between_adds_days || 1)

  if (ca.n_one_exempt) {
    lines.push({ checked: true, text: `快速单笔执行（符合单笔豁免条件）` })
  } else {
    lines.push({ checked: true, text: `分${numTranches}批执行（纪律要求至少${minBatch}批）` })
  }

  lines.push({
    checked: true,
    text: `目标仓位${(targetPct * 100).toFixed(0)}%，不超过单票上限${(maxPos * 100).toFixed(0)}%`,
  })

  lines.push({
    checked: true,
    text: `每批不超过单次加仓上限${(maxSingle * 100).toFixed(0)}%`,
  })

  if (numTranches > 1) {
    lines.push({
      checked: true,
      text: `相邻批次间隔至少${minInterval}天`,
    })
  }

  if (ca.requires_review) {
    lines.push({
      checked: false,
      text: `当前回撤已达复盘线，标记为需人工复盘`,
    })
  }

  return lines
}

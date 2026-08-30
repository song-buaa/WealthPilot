import { useEffect, useMemo, useState } from 'react'
import { Bar, BarChart, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { AlertTriangle, CircleHelp, ReceiptText, RefreshCw } from 'lucide-react'
import EmptyState from '@/components/shared/EmptyState'
import PageHeader from '@/components/shared/PageHeader'
import {
  consumptionApi,
  type ConsumptionAnalyticsSummary,
  type ConsumptionCoverageStatus,
  type ConsumptionMonthlyPoint,
} from '@/lib/api'
import { fmtCny, fmtPct } from '@/lib/fmt'

const CATEGORY_META = [
  { key: 'daily_cny', label: '日常消费', color: '#3B82F6' },
  { key: 'travel_cny', label: '旅行消费', color: '#8B5CF6' },
  { key: 'housing_cny', label: '住房消费', color: '#10B981' },
  { key: 'unclassified_eligible_cny', label: '待分类', color: '#F59E0B' },
] as const

const COVERAGE_COPY: Record<ConsumptionCoverageStatus, { label: string; detail: string; color: string }> = {
  COMPLETE: { label: '数据完整', detail: '已接入账户的本月数据完整。', color: '#047857' },
  PARTIAL: { label: '数据未完整', detail: '部分数据尚未完整覆盖，金额会随导入更新。', color: '#B45309' },
  SOURCE_LIMITED: { label: '来源无法确认完整性', detail: '招行信用卡账单未提供明确账单周期，当前基于已解析交易范围分析。', color: '#B45309' },
  UNKNOWN: { label: '数据覆盖未知', detail: '部分预期账户尚无可验证的导入覆盖范围。', color: '#B91C1C' },
}

const SECONDARY_LABELS: Record<string, string> = {
  FOOD_DINING: '餐饮', TRANSPORT_AUTO: '交通用车', SHOPPING: '购物', HOME_LIVING: '居家生活',
  DIGITAL_COMMUNICATION: '数字与通讯', HEALTH_INSURANCE: '健康保障', SPORTS_HOBBY: '运动兴趣', PET: '宠物',
  LONG_DISTANCE_TRANSPORT: '大交通', ACCOMMODATION: '住宿', LOCAL_TRANSPORT: '当地交通',
  ACTIVITIES_EXPERIENCES: '活动体验', TRAVEL_SHOPPING: '旅行购物', RENT: '房租', PROPERTY_FEE: '物业费', OTHER: '其他',
}

type CategoryKey = (typeof CATEGORY_META)[number]['key']

function toNumber(value: string | null | undefined): number {
  return value == null ? 0 : Number(value)
}

function monthLabel(month: string): string {
  const [year, value] = month.split('-')
  return `${year}年${Number(value)}月`
}

function shortMonth(month: string): string {
  return `${Number(month.slice(5, 7))}月`
}

function coverageRate(point: ConsumptionMonthlyPoint): number | null {
  return point.classification_coverage_rate == null ? null : toNumber(point.classification_coverage_rate) * 100
}

function categoryShare(point: ConsumptionMonthlyPoint, key: CategoryKey): number | null {
  const total = toNumber(point.total_spending_cny)
  return total > 0 ? toNumber(point[key]) / total * 100 : null
}

function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return <section style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12, boxShadow: 'var(--shadow-sm)', ...style }}>{children}</section>
}

function CoverageBadge({ status }: { status: ConsumptionCoverageStatus }) {
  const item = COVERAGE_COPY[status]
  return <span style={{ color: item.color, background: `${item.color}12`, borderRadius: 99, padding: '4px 8px', fontSize: 11, fontWeight: 600 }}>{item.label}</span>
}

function Skeleton() {
  return <div style={{ display: 'grid', gap: 16 }} aria-label="正在加载消费数据">
    {[120, 260, 180].map((height, index) => <div key={index} style={{ height, borderRadius: 12, background: '#E5E7EB', opacity: 0.7 }} />)}
  </div>
}

export default function Consumption() {
  const [summary, setSummary] = useState<ConsumptionAnalyticsSummary | null>(null)
  const [selectedMonth, setSelectedMonth] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    setError(null)
    consumptionApi.getAnalytics({ months: 12 })
      .then(value => {
        setSummary(value)
        setSelectedMonth(current => current && value.months.some(item => item.month === current) ? current : (value.months.at(-1)?.month ?? null))
      })
      .catch(() => setError('消费数据加载失败'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    let active = true
    consumptionApi.getAnalytics({ months: 12 })
      .then(value => {
        if (!active) return
        setSummary(value)
        setSelectedMonth(value.months.at(-1)?.month ?? null)
      })
      .catch(() => { if (active) setError('消费数据加载失败') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  const selected = useMemo(
    () => summary?.months.find(item => item.month === selectedMonth) ?? summary?.months.at(-1) ?? null,
    [selectedMonth, summary],
  )
  const chartData = useMemo(() => (summary?.months ?? []).map(item => ({
    ...item,
    label: shortMonth(item.month),
    daily_cny: toNumber(item.daily_cny), travel_cny: toNumber(item.travel_cny),
    housing_cny: toNumber(item.housing_cny), unclassified_eligible_cny: toNumber(item.unclassified_eligible_cny),
    total_spending_cny: toNumber(item.total_spending_cny),
  })), [summary])

  if (loading) return <Skeleton />
  if (error) return (
    <Card style={{ padding: 20, color: '#991B1B' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 600 }}><AlertTriangle size={18} /> {error}</div>
      <button onClick={load} style={secondaryButtonStyle}><RefreshCw size={14} /> 重试</button>
    </Card>
  )
  if (!summary || !selected || summary.months.every(item => toNumber(item.total_spending_cny) === 0 && item.eligibility_review_count === 0)) return (
    <div>
      <PageHeader icon="◇" title="消费分析" subtitle="了解每个月花了多少钱、花在哪里，以及数据覆盖情况" />
      <Card><EmptyState icon={ReceiptText} title="暂无消费分析数据" desc="完成消费账户数据导入后，可在这里查看月度消费趋势。" /></Card>
    </div>
  )

  const selectedCoverage = COVERAGE_COPY[selected.data_coverage_status]
  const selectedRate = coverageRate(selected)

  return (
    <div style={{ maxWidth: 1320, margin: '0 auto', paddingBottom: 24 }}>
      <PageHeader icon="◇" title="消费分析" subtitle="基于已接入账户，查看月度消费趋势、结构与数据覆盖情况" />

      <Card style={{ padding: '20px 22px', marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start' }}>
          <div>
            <div style={eyebrowStyle}>月度消费概览 · {monthLabel(selected.month)}</div>
            <div className="tabular-nums" style={{ marginTop: 7, fontSize: 32, lineHeight: 1, fontWeight: 750, letterSpacing: '-1px', color: '#1B2A4A' }}>{fmtCny(toNumber(selected.total_spending_cny))}</div>
            <div style={{ fontSize: 12, color: '#6B7280', marginTop: 9 }}>
              {selected.as_of_date ? `分析日期：${selected.as_of_date}（不代表数据完整覆盖）` : '已接入账户的已确认消费'}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            <div style={metricStyle}><span>分类覆盖率</span><strong>{selectedRate == null ? '—' : fmtPct(selectedRate)}</strong></div>
            <div style={metricStyle}><span>数据覆盖</span><CoverageBadge status={selected.data_coverage_status} /></div>
            {summary.twelve_month_average.amount_cny != null && <div style={metricStyle}><span>可比月均</span><strong>{fmtCny(toNumber(summary.twelve_month_average.amount_cny))}</strong><small>{summary.twelve_month_average.months_used} 个完整月</small></div>}
          </div>
        </div>
        {selected.comparison_available ? <div style={{ marginTop: 14, fontSize: 12, color: '#047857' }}>本月与上一个完整月可比较。</div> : <div style={{ marginTop: 14, fontSize: 12, color: '#92400E' }}>当前月份尚不适合直接与上月比较。</div>}
      </Card>

      <Card style={{ padding: '18px 18px 12px', marginBottom: 16 }}>
        <SectionTitle title="近 12 个月消费趋势" detail="按自然月展示，柱高为后端已确认的月度总消费。" />
        <div style={{ width: '100%', height: 310, minWidth: 0 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 12, right: 8, left: -8, bottom: 0 }}>
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#6B7280' }} axisLine={false} tickLine={false} />
              <YAxis tickFormatter={value => `¥${Math.round(value / 1000)}k`} tick={{ fontSize: 11, fill: '#9CA3AF' }} axisLine={false} tickLine={false} width={42} />
              <Tooltip content={({ active, payload, label }) => {
                const point = payload?.[0]?.payload as ConsumptionMonthlyPoint | undefined
                if (!active || !point) return null
                return <TrendTooltip point={point} label={label as string} />
              }} />
              <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12, paddingTop: 8 }} />
              {CATEGORY_META.map(item => <Bar key={item.key} dataKey={item.key} name={item.label} stackId="spending" fill={item.color} maxBarSize={42} cursor="pointer" />)}
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', padding: '8px 4px 0' }} aria-label="选择月份查看详情">
          {summary.months.map(item => <button key={item.month} onClick={() => setSelectedMonth(item.month)} aria-pressed={selected.month === item.month} style={{ ...monthButtonStyle, ...(selected.month === item.month ? selectedMonthButtonStyle : {}) }}>{shortMonth(item.month)}</button>)}
        </div>
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.6fr) minmax(280px, 1fr)', gap: 16, alignItems: 'start' }}>
        <div style={{ display: 'grid', gap: 16 }}>
          <Card style={{ padding: 18 }}>
            <SectionTitle title={`${monthLabel(selected.month)}消费结构`} detail="待分类是已确认但尚未归类的消费状态，并非第四个业务分类。" />
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(145px, 1fr))', gap: 10 }}>
              {CATEGORY_META.map(item => <CategoryCard key={item.key} label={item.label} color={item.color} amount={toNumber(selected[item.key])} share={categoryShare(selected, item.key)} />)}
            </div>
          </Card>
          <Card style={{ padding: 18 }}>
            <SectionTitle title={`${monthLabel(selected.month)}二级分类`} detail="仅展示该月已完成分类的消费；金额与占比由 Analytics API 返回。" />
            {selected.secondary_breakdowns.length === 0 ? <LightEmpty text="该月暂无已完成分类的二级消费数据。" /> : <SecondaryBreakdowns breakdowns={selected.secondary_breakdowns} />}
          </Card>
        </div>
        <div style={{ display: 'grid', gap: 16 }}>
          <Card style={{ padding: 18 }}>
            <SectionTitle title="数据覆盖与金额状态" />
            <div style={{ display: 'grid', gap: 12 }}>
              <div><div style={{ fontSize: 11, color: '#6B7280', marginBottom: 6 }}>数据覆盖</div><CoverageBadge status={selected.data_coverage_status} /><p style={detailTextStyle}>{selectedCoverage.detail}</p></div>
              {!selected.amount_complete && <div style={noticeStyle}><CircleHelp size={15} /> 部分外币消费尚未完成人民币金额换算，当前为已知金额。</div>}
            </div>
          </Card>
          <Card style={{ padding: 18 }}>
            <SectionTitle title="待确认状态" />
            <div style={{ display: 'grid', gap: 14 }}>
              <ReviewLine label="消费归属待确认" value={`${selected.eligibility_review_count} 条记录待确认是否属于消费`} detail="这些记录不计入消费金额。" />
              <ReviewLine label="分类待确认" value={`${selected.classification_review_count} 笔已确认消费尚未分类`} detail={`${fmtCny(toNumber(selected.unclassified_eligible_cny))} 已计入本月总消费。`} />
            </div>
          </Card>
          <Card style={{ padding: 18 }}>
            <div style={{ fontSize: 13, color: '#374151', fontWeight: 700 }}>分类覆盖率</div>
            <div className="tabular-nums" style={{ fontSize: 28, color: '#1B2A4A', fontWeight: 750, marginTop: 6 }}>{selectedRate == null ? '—' : fmtPct(selectedRate)}</div>
            <p style={detailTextStyle}>已完成分类的消费金额 / 当前已确认消费总额</p>
            <div style={{ fontSize: 12, color: '#B45309', marginTop: 8 }}>{fmtCny(toNumber(selected.unclassified_eligible_cny))} 待分类</div>
          </Card>
        </div>
      </div>
    </div>
  )
}

function TrendTooltip({ point, label }: { point: ConsumptionMonthlyPoint; label: string }) {
  return <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 8, padding: '9px 11px', boxShadow: '0 4px 12px rgba(15,30,53,0.12)', fontSize: 12, lineHeight: 1.8, color: '#374151' }}>
    <div style={{ fontWeight: 700, color: '#1B2A4A', marginBottom: 3 }}>{label}</div>
    <div>总消费：<b>{fmtCny(toNumber(point.total_spending_cny))}</b></div>
    {CATEGORY_META.map(item => <div key={item.key}>{item.label}：{fmtCny(toNumber(point[item.key]))}</div>)}
    <div>分类覆盖率：{coverageRate(point) == null ? '—' : fmtPct(coverageRate(point))}</div>
    {!point.amount_complete && <div style={{ color: '#B45309', marginTop: 3 }}>部分外币消费尚未完成人民币金额换算，当前为已知金额。</div>}
  </div>
}

function CategoryCard({ label, color, amount, share }: { label: string; color: string; amount: number; share: number | null }) {
  return <div style={{ border: '1px solid #EEF2F7', borderRadius: 10, padding: 13, borderTop: `3px solid ${color}` }}>
    <div style={{ fontSize: 12, color: '#6B7280' }}>{label}</div>
    <div className="tabular-nums" style={{ fontSize: 18, fontWeight: 700, color: '#1B2A4A', marginTop: 5 }}>{fmtCny(amount)}</div>
    <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 4 }}>占总消费 {share == null ? '—' : fmtPct(share)}</div>
  </div>
}

function SecondaryBreakdowns({ breakdowns }: { breakdowns: ConsumptionAnalyticsSummary['secondary_breakdowns'] }) {
  const groups = ['DAILY', 'TRAVEL', 'HOUSING'] as const
  const labels = { DAILY: '日常消费', TRAVEL: '旅行消费', HOUSING: '住房消费' }
  return <div style={{ display: 'grid', gap: 14 }}>
    {groups.map(primary => {
      const items = breakdowns.filter(item => item.primary_category === primary)
      if (items.length === 0) return null
      return <div key={primary}>
        <div style={{ fontSize: 12, fontWeight: 700, color: '#374151', marginBottom: 7 }}>{labels[primary]}</div>
        <div style={{ display: 'grid', gap: 7 }}>
          {items.map(item => <div key={`${primary}-${item.secondary_category}`}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 12, color: '#4B5563' }}><span>{SECONDARY_LABELS[item.secondary_category] ?? item.secondary_category}</span><span className="tabular-nums">{fmtCny(toNumber(item.amount_cny))}</span></div>
            <div style={{ height: 5, background: '#EEF2F7', borderRadius: 99, marginTop: 4 }}><div style={{ width: `${Math.min(100, toNumber(item.share_within_primary) * 100)}%`, height: '100%', background: '#60A5FA', borderRadius: 99 }} /></div>
          </div>)}
        </div>
      </div>
    })}
  </div>
}

function SectionTitle({ title, detail }: { title: string; detail?: string }) {
  return <div style={{ marginBottom: 14 }}><div style={{ fontSize: 14, color: '#1B2A4A', fontWeight: 700 }}>{title}</div>{detail && <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 3 }}>{detail}</div>}</div>
}

function LightEmpty({ text }: { text: string }) { return <div style={{ padding: '18px 0', color: '#9CA3AF', fontSize: 12 }}>{text}</div> }

function ReviewLine({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <div><div style={{ fontSize: 12, fontWeight: 700, color: '#374151' }}>{label}</div><div style={{ fontSize: 13, color: '#1B2A4A', marginTop: 4 }}>{value}</div><div style={detailTextStyle}>{detail}</div></div>
}

const eyebrowStyle: React.CSSProperties = { fontSize: 12, color: '#6B7280', fontWeight: 600 }
const detailTextStyle: React.CSSProperties = { fontSize: 11, lineHeight: 1.6, color: '#9CA3AF', marginTop: 5 }
const secondaryButtonStyle: React.CSSProperties = { marginTop: 14, display: 'inline-flex', alignItems: 'center', gap: 6, background: '#fff', color: '#991B1B', border: '1px solid #FCA5A5', padding: '7px 10px', borderRadius: 7, cursor: 'pointer', fontSize: 12 }
const metricStyle: React.CSSProperties = { display: 'grid', gap: 4, minWidth: 108, fontSize: 11, color: '#6B7280' }
const monthButtonStyle: React.CSSProperties = { border: '1px solid #E5E7EB', background: '#fff', color: '#6B7280', padding: '5px 8px', borderRadius: 6, cursor: 'pointer', fontSize: 11 }
const selectedMonthButtonStyle: React.CSSProperties = { background: '#EFF6FF', border: '1px solid #93C5FD', color: '#1D4ED8', fontWeight: 700 }
const noticeStyle: React.CSSProperties = { display: 'flex', gap: 7, alignItems: 'flex-start', fontSize: 12, lineHeight: 1.55, color: '#92400E', background: '#FFFBEB', borderRadius: 8, padding: '8px 9px' }

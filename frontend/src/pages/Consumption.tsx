import { useEffect, useMemo, useState } from 'react'
import { Bar, BarChart, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { AlertTriangle, CircleHelp, ReceiptText, RefreshCw } from 'lucide-react'
import EmptyState from '@/components/shared/EmptyState'
import PageHeader from '@/components/shared/PageHeader'
import {
  consumptionApi,
  type ConsumptionAnalyticsSummary,
  type ConsumptionCoverageStatus,
  type ConsumptionEventDetail,
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

function toNumber(value: string | null | undefined): number { return value == null ? 0 : Number(value) }
function monthLabel(month: string): string { const [year, value] = month.split('-'); return `${year}年${Number(value)}月` }
function shortMonth(month: string): string { return `${Number(month.slice(5, 7))}月` }
function coverageRate(point: ConsumptionMonthlyPoint): number | null { return point.classification_coverage_rate == null ? null : toNumber(point.classification_coverage_rate) * 100 }
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
    {[100, 300, 220, 260].map((height, index) => <div key={index} style={{ height, borderRadius: 12, background: '#E5E7EB', opacity: 0.7 }} />)}
  </div>
}

export default function Consumption() {
  const [summary, setSummary] = useState<ConsumptionAnalyticsSummary | null>(null)
  const [selectedMonth, setSelectedMonth] = useState<string | null>(null)
  const [details, setDetails] = useState<ConsumptionEventDetail[]>([])
  const [detailTotal, setDetailTotal] = useState(0)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
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
    const task = window.setTimeout(load, 0)
    return () => window.clearTimeout(task)
  }, [])

  useEffect(() => {
    if (!selectedMonth) return
    let active = true
    void Promise.resolve().then(() => {
      if (!active) return
      setDetailLoading(true)
      setDetailError(null)
      return consumptionApi.getEvents({ month: selectedMonth.slice(0, 7), limit: 200 })
        .then(value => { if (active) { setDetails(value.items); setDetailTotal(value.total) } })
        .catch(() => { if (active) { setDetails([]); setDetailTotal(0); setDetailError('月度明细加载失败') } })
        .finally(() => { if (active) setDetailLoading(false) })
    })
    return () => { active = false }
  }, [selectedMonth])

  const selected = useMemo(
    () => summary?.months.find(item => item.month === selectedMonth) ?? summary?.months.at(-1) ?? null,
    [selectedMonth, summary],
  )
  const chartData = useMemo(() => (summary?.months ?? []).map(item => ({
    ...item,
    label: shortMonth(item.month),
    daily_cny: toNumber(item.daily_cny), travel_cny: toNumber(item.travel_cny),
    housing_cny: toNumber(item.housing_cny), unclassified_eligible_cny: toNumber(item.unclassified_eligible_cny),
  })), [summary])

  if (loading) return <Skeleton />
  if (error) return <Card style={{ padding: 20, color: '#991B1B' }}><div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 600 }}><AlertTriangle size={18} /> {error}</div><button onClick={load} style={secondaryButtonStyle}><RefreshCw size={14} /> 重试</button></Card>
  if (!summary || !selected || summary.months.every(item => toNumber(item.total_spending_cny) === 0 && item.eligibility_review_count === 0)) return <div><PageHeader icon="◇" title="消费分析" subtitle="了解每个月花了多少钱、花在哪里，以及数据覆盖情况" /><Card><EmptyState icon={ReceiptText} title="暂无消费分析数据" desc="完成消费账户数据导入后，可在这里查看月度消费趋势。" /></Card></div>

  const selectedCoverage = COVERAGE_COPY[selected.data_coverage_status]
  const selectedRate = coverageRate(selected)

  return <div style={{ paddingBottom: 24 }}>
    <PageHeader icon="◇" title="消费分析" subtitle="基于已接入账户，查看月度消费趋势、结构与数据覆盖情况" />

    <div style={kpiGridStyle}>
      <section style={heroStyle}>
        <div style={{ fontSize: 12, color: '#C7D2FE', fontWeight: 600 }}>月度消费概览 · {monthLabel(selected.month)}</div>
        <div className="tabular-nums" style={{ marginTop: 8, fontSize: 32, lineHeight: 1, fontWeight: 750, letterSpacing: '-1px' }}>{fmtCny(toNumber(selected.total_spending_cny))}</div>
        <div style={{ fontSize: 12, color: '#CBD5E1', marginTop: 10 }}>{selected.as_of_date ? `分析日期：${selected.as_of_date}（不代表数据完整覆盖）` : '已接入账户的已确认消费'}</div>
      </section>
      <Card style={kpiCardStyle}><div style={kpiLabelStyle}>分类覆盖率</div><div className="tabular-nums" style={kpiValueStyle}>{selectedRate == null ? '—' : fmtPct(selectedRate)}</div><div style={detailTextStyle}>{fmtCny(toNumber(selected.unclassified_eligible_cny))} 待分类</div></Card>
      <Card style={kpiCardStyle}><div style={kpiLabelStyle}>数据覆盖</div><div style={{ marginTop: 10 }}><CoverageBadge status={selected.data_coverage_status} /></div><div style={detailTextStyle}>{selectedCoverage.detail}</div></Card>
    </div>

    <Card style={{ padding: '18px 18px 12px', marginTop: 16 }}>
      <SectionTitle title="近 12 个月消费趋势" detail="按自然月展示，柱高为后端已确认的月度总消费。" />
      <div style={{ width: '100%', height: 310, minWidth: 0 }}>
        <ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} margin={{ top: 12, right: 8, left: -8, bottom: 0 }}>
          <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#6B7280' }} axisLine={false} tickLine={false} />
          <YAxis tickFormatter={value => `¥${Math.round(value / 1000)}k`} tick={{ fontSize: 11, fill: '#9CA3AF' }} axisLine={false} tickLine={false} width={42} />
          <Tooltip content={({ active, payload, label }) => { const point = payload?.[0]?.payload as ConsumptionMonthlyPoint | undefined; return active && point ? <TrendTooltip point={point} label={label as string} /> : null }} />
          <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12, paddingTop: 8 }} />
          {CATEGORY_META.map(item => <Bar key={item.key} dataKey={item.key} name={item.label} stackId="spending" fill={item.color} maxBarSize={42} cursor="pointer" />)}
        </BarChart></ResponsiveContainer>
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', padding: '8px 4px 0' }} aria-label="选择月份查看详情">
        {summary.months.map(item => <button key={item.month} onClick={() => setSelectedMonth(item.month)} aria-pressed={selected.month === item.month} style={{ ...monthButtonStyle, ...(selected.month === item.month ? selectedMonthButtonStyle : {}) }}>{shortMonth(item.month)}</button>)}
      </div>
    </Card>

    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 3fr) minmax(280px, 2fr)', gap: 16, alignItems: 'start', marginTop: 16 }}>
      <Card style={{ padding: 20 }}>
        <SectionTitle title={`${monthLabel(selected.month)}消费结构`} detail="待分类是已确认但尚未归类的消费状态，并非第四个业务分类。" />
        <div style={{ display: 'grid', gap: 12 }}>{CATEGORY_META.map(item => <CategoryRow key={item.key} label={item.label} color={item.color} amount={toNumber(selected[item.key])} share={categoryShare(selected, item.key)} />)}</div>
        <div style={{ borderTop: '1px solid #F3F4F6', margin: '18px 0 14px' }} />
        <div style={{ fontSize: 13, color: '#1B2A4A', fontWeight: 700 }}>{monthLabel(selected.month)}二级分类</div>
        <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 3 }}>仅展示该月已完成分类的消费。</div>
        <div style={{ marginTop: 12 }}>{selected.secondary_breakdowns.length === 0 ? <LightEmpty text="该月暂无已完成分类的二级消费数据。" /> : <SecondaryBreakdowns breakdowns={selected.secondary_breakdowns} />}</div>
      </Card>
      <div style={{ display: 'grid', gap: 16 }}>
        <Card style={{ padding: 18 }}><SectionTitle title="数据覆盖与金额状态" /><div style={{ fontSize: 11, color: '#6B7280', marginBottom: 6 }}>数据覆盖</div><CoverageBadge status={selected.data_coverage_status} /><p style={detailTextStyle}>{selectedCoverage.detail}</p>{!selected.amount_complete && <div style={noticeStyle}><CircleHelp size={15} /> 部分外币消费尚未完成人民币金额换算，当前为已知金额。</div>}</Card>
        <Card style={{ padding: 18 }}><SectionTitle title="待确认状态" /><div style={{ display: 'grid', gap: 14 }}><ReviewLine label="消费归属待确认" value={`${selected.eligibility_review_count} 条记录待确认是否属于消费`} detail="这些记录不计入消费金额。" /><ReviewLine label="分类待确认" value={`${selected.classification_review_count} 笔已确认消费尚未分类`} detail={`${fmtCny(toNumber(selected.unclassified_eligible_cny))} 已计入本月总消费。`} /></div></Card>
      </div>
    </div>

    <Card style={{ padding: '20px 20px 16px', marginTop: 16 }}>
      <SectionTitle title={`${monthLabel(selected.month)}消费明细`} detail="仅显示已确认纳入消费分析的记录；名称为安全语义标签，不展示账单原始文本。" />
      <MonthlyDetailTable items={details} total={detailTotal} loading={detailLoading} error={detailError} />
    </Card>
  </div>
}

function TrendTooltip({ point, label }: { point: ConsumptionMonthlyPoint; label: string }) {
  return <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 8, padding: '9px 11px', boxShadow: '0 4px 12px rgba(15,30,53,0.12)', fontSize: 12, lineHeight: 1.8, color: '#374151' }}><div style={{ fontWeight: 700, color: '#1B2A4A', marginBottom: 3 }}>{label}</div><div>总消费：<b>{fmtCny(toNumber(point.total_spending_cny))}</b></div>{CATEGORY_META.map(item => <div key={item.key}>{item.label}：{fmtCny(toNumber(point[item.key]))}</div>)}<div>分类覆盖率：{coverageRate(point) == null ? '—' : fmtPct(coverageRate(point))}</div>{!point.amount_complete && <div style={{ color: '#B45309', marginTop: 3 }}>部分外币消费尚未完成人民币金额换算，当前为已知金额。</div>}</div>
}

function CategoryRow({ label, color, amount, share }: { label: string; color: string; amount: number; share: number | null }) {
  return <div><div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}><div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: '#4B5563' }}><span style={{ width: 8, height: 8, borderRadius: 99, background: color }} />{label}</div><div className="tabular-nums" style={{ fontSize: 14, fontWeight: 700, color: '#1B2A4A' }}>{fmtCny(amount)}</div></div><div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}><div style={{ height: 6, background: '#EEF2F7', borderRadius: 99, flex: 1 }}><div style={{ width: `${Math.min(100, share ?? 0)}%`, height: '100%', background: color, borderRadius: 99 }} /></div><span style={{ minWidth: 38, textAlign: 'right', fontSize: 11, color: '#9CA3AF' }}>{share == null ? '—' : fmtPct(share)}</span></div></div>
}

function SecondaryBreakdowns({ breakdowns }: { breakdowns: ConsumptionAnalyticsSummary['secondary_breakdowns'] }) {
  const groups = ['DAILY', 'TRAVEL', 'HOUSING'] as const
  const labels = { DAILY: '日常消费', TRAVEL: '旅行消费', HOUSING: '住房消费' }
  return <div style={{ display: 'grid', gap: 14 }}>{groups.map(primary => { const items = breakdowns.filter(item => item.primary_category === primary); if (items.length === 0) return null; return <div key={primary}><div style={{ fontSize: 12, fontWeight: 700, color: '#374151', marginBottom: 7 }}>{labels[primary]}</div><div style={{ display: 'grid', gap: 7 }}>{items.map(item => <div key={`${primary}-${item.secondary_category}`}><div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 12, color: '#4B5563' }}><span>{SECONDARY_LABELS[item.secondary_category] ?? item.secondary_category}</span><span className="tabular-nums">{fmtCny(toNumber(item.amount_cny))}</span></div><div style={{ height: 5, background: '#EEF2F7', borderRadius: 99, marginTop: 4 }}><div style={{ width: `${Math.min(100, toNumber(item.share_within_primary) * 100)}%`, height: '100%', background: '#60A5FA', borderRadius: 99 }} /></div></div>)}</div></div> })}</div>
}

function MonthlyDetailTable({ items, total, loading, error }: { items: ConsumptionEventDetail[]; total: number; loading: boolean; error: string | null }) {
  if (loading) return <div aria-label="正在加载月度明细" style={{ height: 170, borderRadius: 8, background: '#F9FAFB' }} />
  if (error) return <div style={{ padding: '16px 0', fontSize: 12, color: '#B91C1C' }}>{error}</div>
  if (items.length === 0) return <LightEmpty text="该月暂无已确认纳入分析的消费记录。" />
  return <><div style={{ fontSize: 11, color: '#9CA3AF', marginBottom: 9 }}>共 {total} 条，按金额从高到低排列</div><div style={{ overflow: 'auto', maxHeight: 494, border: '1px solid #F3F4F6', borderRadius: 6 }}><table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}><thead><tr>{['日期', '消费名称', '一级分类', '二级分类', '账户', '金额', '分类状态'].map((label, index) => <th key={label} style={{ ...tableHeaderStyle, textAlign: index === 5 ? 'right' : 'left' }}>{label}</th>)}</tr></thead><tbody>{items.map((item, index) => <tr key={`${item.analytics_effective_date}-${index}`}><td style={tableCellStyle}>{item.analytics_effective_date}</td><td style={{ ...tableCellStyle, fontWeight: 600, color: '#374151' }}>{item.display_description}</td><td style={tableCellStyle}>{item.primary_category ? { DAILY: '日常消费', TRAVEL: '旅行消费', HOUSING: '住房消费' }[item.primary_category] : '待分类'}</td><td style={tableCellStyle}>{item.secondary_category ? (SECONDARY_LABELS[item.secondary_category] ?? item.secondary_category) : '—'}</td><td style={tableCellStyle}>{item.account_display_name}</td><td className="tabular-nums" style={{ ...tableCellStyle, textAlign: 'right', fontWeight: 700, color: '#1B2A4A' }}>{fmtCny(toNumber(item.amount_cny))}</td><td style={tableCellStyle}><span style={item.classification_status === 'CLASSIFIED' ? classifiedPillStyle : reviewPillStyle}>{item.classification_status === 'CLASSIFIED' ? '已分类' : '待分类'}</span></td></tr>)}</tbody></table></div></>
}

function SectionTitle({ title, detail }: { title: string; detail?: string }) { return <div style={{ marginBottom: 14 }}><div style={{ fontSize: 14, color: '#1B2A4A', fontWeight: 700 }}>{title}</div>{detail && <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 3 }}>{detail}</div>}</div> }
function LightEmpty({ text }: { text: string }) { return <div style={{ padding: '18px 0', color: '#9CA3AF', fontSize: 12 }}>{text}</div> }
function ReviewLine({ label, value, detail }: { label: string; value: string; detail: string }) { return <div><div style={{ fontSize: 12, fontWeight: 700, color: '#374151' }}>{label}</div><div style={{ fontSize: 13, color: '#1B2A4A', marginTop: 4 }}>{value}</div><div style={detailTextStyle}>{detail}</div></div> }

const kpiGridStyle: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'minmax(0, 2fr) minmax(190px, 1fr) minmax(190px, 1fr)', gap: 12 }
const heroStyle: React.CSSProperties = { minHeight: 100, borderRadius: 12, padding: '20px 24px', color: '#fff', background: 'linear-gradient(135deg, #1F2937, #111827)', boxShadow: 'var(--shadow-dark)' }
const kpiCardStyle: React.CSSProperties = { minHeight: 100, padding: '16px 18px' }
const kpiLabelStyle: React.CSSProperties = { fontSize: 11, color: '#6B7280', fontWeight: 600 }
const kpiValueStyle: React.CSSProperties = { fontSize: 25, lineHeight: 1, color: '#1B2A4A', fontWeight: 750, marginTop: 8 }
const detailTextStyle: React.CSSProperties = { fontSize: 11, lineHeight: 1.6, color: '#9CA3AF', marginTop: 5 }
const secondaryButtonStyle: React.CSSProperties = { marginTop: 14, display: 'inline-flex', alignItems: 'center', gap: 6, background: '#fff', color: '#991B1B', border: '1px solid #FCA5A5', padding: '7px 10px', borderRadius: 7, cursor: 'pointer', fontSize: 12 }
const monthButtonStyle: React.CSSProperties = { border: '1px solid #E5E7EB', background: '#fff', color: '#6B7280', padding: '5px 8px', borderRadius: 6, cursor: 'pointer', fontSize: 11 }
const selectedMonthButtonStyle: React.CSSProperties = { background: '#EFF6FF', border: '1px solid #93C5FD', color: '#1D4ED8', fontWeight: 700 }
const noticeStyle: React.CSSProperties = { display: 'flex', gap: 7, alignItems: 'flex-start', fontSize: 12, lineHeight: 1.55, color: '#92400E', background: '#FFFBEB', borderRadius: 8, padding: '8px 9px', marginTop: 10 }
const tableHeaderStyle: React.CSSProperties = { position: 'sticky', top: 0, zIndex: 1, whiteSpace: 'nowrap', background: '#fff', borderBottom: '1px solid #E5E7EB', padding: '8px 10px', fontSize: 11, color: '#6B7280', fontWeight: 600 }
const tableCellStyle: React.CSSProperties = { whiteSpace: 'nowrap', borderBottom: '1px solid #F3F4F6', padding: '9px 10px', fontSize: 12, color: '#4B5563' }
const classifiedPillStyle: React.CSSProperties = { display: 'inline-block', borderRadius: 99, padding: '3px 7px', fontSize: 11, color: '#047857', background: '#ECFDF5' }
const reviewPillStyle: React.CSSProperties = { display: 'inline-block', borderRadius: 99, padding: '3px 7px', fontSize: 11, color: '#B45309', background: '#FFFBEB' }

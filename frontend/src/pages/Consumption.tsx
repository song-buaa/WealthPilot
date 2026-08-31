import { useEffect, useMemo, useRef, useState } from 'react'
import { Bar, BarChart, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { AlertTriangle, Check, Download, Loader2, ReceiptText, RefreshCw } from 'lucide-react'
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

const EDITABLE_TAXONOMY = {
  DAILY: ["FOOD_DINING", "TRANSPORT_AUTO", "SHOPPING", "HOME_LIVING", "DIGITAL_COMMUNICATION", "HEALTH_INSURANCE", "SPORTS_HOBBY", "PET", "OTHER"],
  TRAVEL: ["LONG_DISTANCE_TRANSPORT", "ACCOMMODATION", "LOCAL_TRANSPORT", "FOOD_DINING", "ACTIVITIES_EXPERIENCES", "TRAVEL_SHOPPING", "OTHER"],
  HOUSING: ["RENT", "PROPERTY_FEE", "OTHER"],
} as const

type CategoryKey = (typeof CATEGORY_META)[number]['key']
type EditablePrimary = keyof typeof EDITABLE_TAXONOMY
type ClassificationDraft = { primary: EditablePrimary; secondary: string }
type AutosaveState = { state: 'saving' | 'saved' | 'error'; draft: ClassificationDraft }
type DetailClassificationFilter = 'ALL' | 'CLASSIFIED' | 'NEEDS_REVIEW'

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
  const [detailClassificationFilter, setDetailClassificationFilter] = useState<DetailClassificationFilter>('ALL')
  const [detailPrimaryFilter, setDetailPrimaryFilter] = useState<EditablePrimary | ''>('')
  const [detailSecondaryFilter, setDetailSecondaryFilter] = useState('')
  const [editing, setEditing] = useState<Record<string, ClassificationDraft>>({})
  const [autosaveStates, setAutosaveStates] = useState<Record<string, AutosaveState>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const autosaveTimers = useRef<Record<string, number>>({})
  const requestVersions = useRef<Record<string, number>>({})

  const detailFilters = useMemo(() => ({
    classificationStatus: detailClassificationFilter === 'ALL' ? undefined : detailClassificationFilter,
    primaryCategory: detailClassificationFilter === 'NEEDS_REVIEW' ? undefined : detailPrimaryFilter || undefined,
    secondaryCategory: detailClassificationFilter === 'NEEDS_REVIEW' ? undefined : detailSecondaryFilter || undefined,
  }), [detailClassificationFilter, detailPrimaryFilter, detailSecondaryFilter])

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

  const refreshSummary = () => {
    void consumptionApi.getAnalytics({ months: 12 })
      .then(value => setSummary(value))
      .catch(() => undefined)
  }

  useEffect(() => {
    const task = window.setTimeout(load, 0)
    return () => window.clearTimeout(task)
  }, [])

  useEffect(() => () => {
    Object.values(autosaveTimers.current).forEach(timer => window.clearTimeout(timer))
  }, [])

  useEffect(() => {
    if (!selectedMonth) return
    let active = true
    void Promise.resolve().then(() => {
      if (!active) return
      setDetailLoading(true)
      setDetailError(null)
      return consumptionApi.getEvents({ month: selectedMonth.slice(0, 7), limit: 200, offset: 0, ...detailFilters })
        .then(value => { if (active) { setDetails(value.items); setDetailTotal(value.total) } })
        .catch(() => { if (active) { setDetails([]); setDetailTotal(0); setDetailError('月度明细加载失败') } })
        .finally(() => { if (active) setDetailLoading(false) })
    })
    return () => { active = false }
  }, [selectedMonth, detailFilters])

  const saveClassification = async (item: ConsumptionEventDetail, draft: ClassificationDraft, version: number) => {
    try {
      const result = await consumptionApi.updateEventClassification(item.event_id, draft.primary, draft.secondary)
      if (requestVersions.current[item.event_id] !== version) return
      setDetails(current => detailClassificationFilter === 'NEEDS_REVIEW'
        ? current.filter(row => row.event_id !== item.event_id)
        : current.map(row => row.event_id === item.event_id ? {
        ...row,
        primary_category: result.primary_category as ConsumptionEventDetail['primary_category'],
        secondary_category: result.secondary_category,
        classification_status: result.classification_status as ConsumptionEventDetail['classification_status'],
      } : row))
      if (detailClassificationFilter === 'NEEDS_REVIEW') setDetailTotal(current => Math.max(0, current - 1))
      setEditing(current => { const next = { ...current }; delete next[item.event_id]; return next })
      setAutosaveStates(current => ({ ...current, [item.event_id]: { state: 'saved', draft } }))
      refreshSummary()
    } catch {
      if (requestVersions.current[item.event_id] !== version) return
      setAutosaveStates(current => ({ ...current, [item.event_id]: { state: 'error', draft } }))
    }
  }

  const scheduleClassificationSave = (item: ConsumptionEventDetail, draft: ClassificationDraft) => {
    setEditing(current => ({ ...current, [item.event_id]: draft }))
    window.clearTimeout(autosaveTimers.current[item.event_id])
    const version = (requestVersions.current[item.event_id] ?? 0) + 1
    requestVersions.current[item.event_id] = version
    if (!draft.secondary) {
      setAutosaveStates(current => { const next = { ...current }; delete next[item.event_id]; return next })
      return
    }
    setAutosaveStates(current => ({ ...current, [item.event_id]: { state: 'saving', draft } }))
    autosaveTimers.current[item.event_id] = window.setTimeout(() => {
      void saveClassification(item, draft, version)
    }, 300)
  }

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
  if (!summary || !selected || summary.months.every(item => toNumber(item.total_spending_cny) === 0 && item.eligibility_review_count === 0)) return <div><PageHeader icon="¥" title="消费分析" subtitle="了解每个月花了多少钱、花在哪里，以及数据覆盖情况" /><Card><EmptyState icon={ReceiptText} title="暂无消费分析数据" desc="完成消费账户数据导入后，可在这里查看月度消费趋势。" /></Card></div>

  const selectedCoverage = COVERAGE_COPY[selected.data_coverage_status]
  const selectedRate = coverageRate(selected)

  return <div style={{ paddingBottom: 24 }}>
    <PageHeader icon="¥" title="消费分析" subtitle="基于已接入账户，查看月度消费趋势、结构与数据覆盖情况" />

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

    <div style={{ marginTop: 16 }}>
      <Card style={{ padding: 20 }}>
        <SectionTitle title={`${monthLabel(selected.month)}消费结构`} detail="待分类是已确认但尚未归类的消费状态，并非第四个业务分类。" />
        <div style={{ display: 'grid', gap: 12 }}>{CATEGORY_META.map(item => <CategoryRow key={item.key} label={item.label} color={item.color} amount={toNumber(selected[item.key])} share={categoryShare(selected, item.key)} />)}</div>
        <div style={{ borderTop: '1px solid #F3F4F6', margin: '18px 0 14px' }} />
        <div style={{ fontSize: 13, color: '#1B2A4A', fontWeight: 700 }}>{monthLabel(selected.month)}二级分类</div>
        <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 3 }}>仅展示该月已完成分类的消费。</div>
        <div style={{ marginTop: 12 }}>{selected.secondary_breakdowns.length === 0 ? <LightEmpty text="该月暂无已完成分类的二级消费数据。" /> : <SecondaryBreakdowns breakdowns={selected.secondary_breakdowns} />}</div>
      </Card>
    </div>

    <Card style={{ padding: '20px 20px 16px', marginTop: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, marginBottom: 14 }}>
        <div><div style={{ fontSize: 14, color: '#1B2A4A', fontWeight: 700 }}>{monthLabel(selected.month)}消费明细</div><div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 3 }}>仅显示已确认纳入消费分析的记录；消费名称直接来自原始账单描述。</div></div>
        <a href={consumptionApi.getEventsExportUrl(selected.month.slice(0, 7), detailFilters)} download style={exportButtonStyle}><Download size={13} /> 导出 CSV</a>
      </div>
      <div style={detailFilterBarStyle} aria-label="消费明细分类筛选">
        <label style={detailFilterLabelStyle}>分类状态<select aria-label="分类状态筛选" value={detailClassificationFilter} onChange={event => {
          const next = event.target.value as DetailClassificationFilter
          setDetailClassificationFilter(next)
          if (next === 'NEEDS_REVIEW') { setDetailPrimaryFilter(''); setDetailSecondaryFilter('') }
        }} style={detailFilterSelectStyle}><option value="ALL">全部</option><option value="CLASSIFIED">已分类</option><option value="NEEDS_REVIEW">待分类</option></select></label>
        <label style={detailFilterLabelStyle}>一级分类<select aria-label="一级分类筛选" value={detailPrimaryFilter} disabled={detailClassificationFilter === 'NEEDS_REVIEW'} onChange={event => { setDetailPrimaryFilter(event.target.value as EditablePrimary | ''); setDetailSecondaryFilter('') }} style={detailFilterSelectStyle}><option value="">全部</option><option value="DAILY">日常消费</option><option value="TRAVEL">旅行</option><option value="HOUSING">住房</option></select></label>
        <label style={detailFilterLabelStyle}>二级分类<select aria-label="二级分类筛选" value={detailSecondaryFilter} disabled={detailClassificationFilter === 'NEEDS_REVIEW' || !detailPrimaryFilter} onChange={event => setDetailSecondaryFilter(event.target.value)} style={detailFilterSelectStyle}><option value="">全部</option>{detailPrimaryFilter && EDITABLE_TAXONOMY[detailPrimaryFilter].map(value => <option key={value} value={value}>{SECONDARY_LABELS[value]}</option>)}</select></label>
      </div>
      <MonthlyDetailTable items={details} total={detailTotal} loading={detailLoading} error={detailError} editing={editing} autosaveStates={autosaveStates} onChange={scheduleClassificationSave} />
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

function MonthlyDetailTable({ items, total, loading, error, editing, autosaveStates, onChange }: { items: ConsumptionEventDetail[]; total: number; loading: boolean; error: string | null; editing: Record<string, ClassificationDraft>; autosaveStates: Record<string, AutosaveState>; onChange: (item: ConsumptionEventDetail, draft: ClassificationDraft) => void }) {
  if (loading) return <div aria-label="正在加载月度明细" style={{ height: 170, borderRadius: 8, background: '#F9FAFB' }} />
  if (error) return <div style={{ padding: '16px 0', fontSize: 12, color: '#B91C1C' }}>{error}</div>
  if (items.length === 0) return <LightEmpty text="当前筛选条件下暂无消费明细" />
  return <><div style={{ fontSize: 11, color: '#9CA3AF', marginBottom: 9 }}>共 {total} 条，按金额从高到低排列</div><div style={{ overflow: 'auto', maxHeight: 494, border: '1px solid #F3F4F6', borderRadius: 6 }}><table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 980 }}><thead><tr>{['日期', '消费明细', '一级分类', '二级分类', '账户', '金额', '分类状态', '保存状态'].map((label, index) => <th key={label} style={{ ...tableHeaderStyle, textAlign: index === 5 ? 'right' : 'left' }}>{label}</th>)}</tr></thead><tbody>{items.map((item, index) => {
    const draft = editing[item.event_id]
    const primary = draft?.primary ?? item.primary_category ?? ''
    const secondary = draft?.secondary ?? item.secondary_category ?? ''
    const options = primary ? EDITABLE_TAXONOMY[primary as EditablePrimary] : []
    const state = autosaveStates[item.event_id]
    return <tr key={`${item.event_id}-${index}`}><td style={tableCellStyle}>{item.analytics_effective_date}</td><td style={{ ...tableCellStyle, maxWidth: 250 }}><div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: 600, color: '#374151' }}>{item.raw_description}</div></td><td style={tableCellStyle}><select aria-label={`一级分类 ${item.event_id}`} value={primary} onChange={event => { const nextPrimary = event.target.value as EditablePrimary; const currentSecondary = secondary; const nextSecondary = EDITABLE_TAXONOMY[nextPrimary].includes(currentSecondary as never) ? currentSecondary : ''; onChange(item, { primary: nextPrimary, secondary: nextSecondary }) }} style={selectStyle}><option value="" disabled>待分类</option>{Object.entries({ DAILY: '日常消费', TRAVEL: '旅行消费', HOUSING: '住房消费' }).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></td><td style={tableCellStyle}><select aria-label={`二级分类 ${item.event_id}`} value={secondary} disabled={!primary} onChange={event => onChange(item, { primary: primary as EditablePrimary, secondary: event.target.value })} style={selectStyle}><option value="" disabled>选择分类</option>{options.map(value => <option key={value} value={value}>{SECONDARY_LABELS[value] ?? value}</option>)}</select></td><td style={tableCellStyle}>{item.account_display_name}</td><td className="tabular-nums" style={{ ...tableCellStyle, textAlign: 'right', fontWeight: 700, color: '#1B2A4A' }}>{fmtCny(toNumber(item.amount_cny))}</td><td style={tableCellStyle}><span style={item.classification_status === 'CLASSIFIED' ? classifiedPillStyle : reviewPillStyle}>{item.classification_status === 'CLASSIFIED' ? '已分类' : '待分类'}</span></td><td style={tableCellStyle}>{state?.state === 'saving' && <span style={autosaveSavingStyle}><Loader2 size={12} className="animate-spin" /> 保存中…</span>}{state?.state === 'saved' && <span style={autosaveSavedStyle}><Check size={12} /> 已保存</span>}{state?.state === 'error' && <button aria-label={`重试分类 ${item.event_id}`} onClick={() => onChange(item, state.draft)} style={retryButtonStyle}>保存失败 / 重试</button>}</td></tr>
  })}</tbody></table></div></>
}

function SectionTitle({ title, detail }: { title: string; detail?: string }) { return <div style={{ marginBottom: 14 }}><div style={{ fontSize: 14, color: '#1B2A4A', fontWeight: 700 }}>{title}</div>{detail && <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 3 }}>{detail}</div>}</div> }
function LightEmpty({ text }: { text: string }) { return <div style={{ padding: '18px 0', color: '#9CA3AF', fontSize: 12 }}>{text}</div> }

const kpiGridStyle: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'minmax(0, 2fr) minmax(190px, 1fr) minmax(190px, 1fr)', gap: 12 }
const heroStyle: React.CSSProperties = { minHeight: 100, borderRadius: 12, padding: '20px 24px', color: '#fff', background: 'linear-gradient(135deg, #1F2937, #111827)', boxShadow: 'var(--shadow-dark)' }
const kpiCardStyle: React.CSSProperties = { minHeight: 100, padding: '16px 18px' }
const kpiLabelStyle: React.CSSProperties = { fontSize: 11, color: '#6B7280', fontWeight: 600 }
const kpiValueStyle: React.CSSProperties = { fontSize: 25, lineHeight: 1, color: '#1B2A4A', fontWeight: 750, marginTop: 8 }
const detailTextStyle: React.CSSProperties = { fontSize: 11, lineHeight: 1.6, color: '#9CA3AF', marginTop: 5 }
const secondaryButtonStyle: React.CSSProperties = { marginTop: 14, display: 'inline-flex', alignItems: 'center', gap: 6, background: '#fff', color: '#991B1B', border: '1px solid #FCA5A5', padding: '7px 10px', borderRadius: 7, cursor: 'pointer', fontSize: 12 }
const monthButtonStyle: React.CSSProperties = { border: '1px solid #E5E7EB', background: '#fff', color: '#6B7280', padding: '5px 8px', borderRadius: 6, cursor: 'pointer', fontSize: 11 }
const selectedMonthButtonStyle: React.CSSProperties = { background: '#EFF6FF', border: '1px solid #93C5FD', color: '#1D4ED8', fontWeight: 700 }
const tableHeaderStyle: React.CSSProperties = { position: 'sticky', top: 0, zIndex: 1, whiteSpace: 'nowrap', background: '#fff', borderBottom: '1px solid #E5E7EB', padding: '8px 10px', fontSize: 11, color: '#6B7280', fontWeight: 600 }
const tableCellStyle: React.CSSProperties = { whiteSpace: 'nowrap', borderBottom: '1px solid #F3F4F6', padding: '9px 10px', fontSize: 12, color: '#4B5563' }
const classifiedPillStyle: React.CSSProperties = { display: 'inline-block', borderRadius: 99, padding: '3px 7px', fontSize: 11, color: '#047857', background: '#ECFDF5' }
const reviewPillStyle: React.CSSProperties = { display: 'inline-block', borderRadius: 99, padding: '3px 7px', fontSize: 11, color: '#B45309', background: '#FFFBEB' }
const exportButtonStyle: React.CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: 5, flexShrink: 0, border: '1px solid #E5E7EB', borderRadius: 6, padding: '5px 9px', color: '#4B5563', background: '#fff', fontSize: 11, textDecoration: 'none' }
const detailFilterBarStyle: React.CSSProperties = { display: 'flex', alignItems: 'end', gap: 10, flexWrap: 'wrap', marginBottom: 14 }
const detailFilterLabelStyle: React.CSSProperties = { display: 'grid', gap: 4, color: '#6B7280', fontSize: 11, fontWeight: 600 }
const detailFilterSelectStyle: React.CSSProperties = { minWidth: 128, border: '1px solid #E5E7EB', borderRadius: 6, background: '#fff', color: '#374151', padding: '6px 8px', fontSize: 12 }
const selectStyle: React.CSSProperties = { maxWidth: 126, border: '1px solid #E5E7EB', borderRadius: 5, background: '#fff', color: '#374151', padding: '4px 6px', fontSize: 11 }
const autosaveSavingStyle: React.CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: 4, color: '#6B7280', fontSize: 11 }
const autosaveSavedStyle: React.CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: 4, color: '#047857', fontSize: 11 }
const retryButtonStyle: React.CSSProperties = { border: 'none', padding: 0, background: 'transparent', color: '#B91C1C', cursor: 'pointer', fontSize: 11 }

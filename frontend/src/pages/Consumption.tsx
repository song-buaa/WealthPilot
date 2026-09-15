import { useEffect, useMemo, useRef, useState } from 'react'
import { Bar, BarChart, Cell, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { AlertTriangle, Check, Download, Loader2, ReceiptText, RefreshCw, WalletCards } from 'lucide-react'
import EmptyState from '@/components/shared/EmptyState'
import PageHeader from '@/components/shared/PageHeader'
import {
  consumptionApi,
  type ConsumptionAnalyticsSummary,
  type ConsumptionCandidate,
  type ConsumptionEventDetail,
  type ConsumptionMonthlyPoint,
  type ConsumptionSecondaryBreakdown,
} from '@/lib/api'
import { fmtCny, fmtPct } from '@/lib/fmt'

const CATEGORY_META = [
  { key: 'daily_cny', label: '日常消费', color: '#3B82F6' },
  { key: 'housing_cny', label: '住房消费', color: '#10B981' },
  { key: 'travel_cny', label: '旅行消费', color: '#8B5CF6' },
  { key: 'unclassified_eligible_cny', label: '待分类', color: '#F59E0B' },
] as const
const STACK_RENDER_META = [...CATEGORY_META].reverse()

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
type NoteSaveState = { state: 'saving' | 'saved' | 'error'; value: string }
type CandidateActionState = { state: 'confirming' | 'rejecting' | 'error'; draft?: ClassificationDraft }
type DetailClassificationFilter = 'ALL' | 'CLASSIFIED' | 'NEEDS_REVIEW'
type DetailViewFilters = { classificationStatus?: 'CLASSIFIED' | 'NEEDS_REVIEW'; primaryCategory?: EditablePrimary; secondaryCategory?: string }
type KpiWindow = { endingMonth: string; summary: ConsumptionAnalyticsSummary }
type ConsumptionStructureData = {
  id: string
  title: string
  detail: string
  total: number
  primaryAmounts: Record<CategoryKey, number>
  secondaryBreakdowns: ConsumptionSecondaryBreakdown[]
}

const SECONDARY_TAB_META: Array<{ key: EditablePrimary; label: string }> = [
  { key: 'DAILY', label: '日常消费' },
  { key: 'HOUSING', label: '住房消费' },
  { key: 'TRAVEL', label: '旅行消费' },
]
const PRIMARY_CATEGORY_KEYS: Record<EditablePrimary, CategoryKey> = {
  DAILY: 'daily_cny',
  HOUSING: 'housing_cny',
  TRAVEL: 'travel_cny',
}

function toNumber(value: string | null | undefined): number { return value == null ? 0 : Number(value) }
function monthLabel(month: string): string { const [year, value] = month.split('-'); return `${year}年${Number(value)}月` }
function shortMonth(month: string): string { return `${Number(month.slice(5, 7))}月` }
function monthEnd(month: string): string {
  const [year, value] = month.split('-').map(Number)
  return `${year}-${String(value).padStart(2, '0')}-${String(new Date(year, value, 0).getDate()).padStart(2, '0')}`
}
function isOpenCalendarMonth(point: ConsumptionMonthlyPoint): boolean { return Boolean(point.as_of_date && point.as_of_date < monthEnd(point.month)) }
function fmtChange(value: number): string { return `${value >= 0 ? '+' : ''}${value.toFixed(1)}%` }
function coverageRate(point: ConsumptionMonthlyPoint): number | null { return point.classification_coverage_rate == null ? null : toNumber(point.classification_coverage_rate) * 100 }

function updateVisibleDetail(
  items: ConsumptionEventDetail[], eventId: string,
  result: { primary_category: string; secondary_category: string; classification_status: string },
  filters: DetailViewFilters,
): ConsumptionEventDetail[] {
  if (filters.classificationStatus === 'NEEDS_REVIEW') return items.filter(item => item.event_id !== eventId)
  return items.map(item => item.event_id === eventId ? {
    ...item,
    primary_category: result.primary_category as ConsumptionEventDetail['primary_category'],
    secondary_category: result.secondary_category,
    classification_status: result.classification_status as ConsumptionEventDetail['classification_status'],
  } : item)
}

async function loadAllDetailPages(params: Parameters<typeof consumptionApi.getEvents>[0]) {
  const first = await consumptionApi.getEvents({ ...params, limit: 200, offset: 0 })
  if (first.total <= first.items.length) return first
  const offsets = Array.from({ length: Math.ceil((first.total - first.items.length) / 200) }, (_, index) => first.items.length + index * 200)
  const pages = await Promise.all(offsets.map(offset => consumptionApi.getEvents({ ...params, limit: 200, offset })))
  return { ...first, items: [...first.items, ...pages.flatMap(page => page.items)] }
}

function primaryAmounts(point: ConsumptionMonthlyPoint): Record<CategoryKey, number> {
  return {
    daily_cny: toNumber(point.daily_cny),
    housing_cny: toNumber(point.housing_cny),
    travel_cny: toNumber(point.travel_cny),
    unclassified_eligible_cny: toNumber(point.unclassified_eligible_cny),
  }
}

function monthlyStructure(point: ConsumptionMonthlyPoint): ConsumptionStructureData {
  return {
    id: `monthly-${point.month}`,
    title: `${monthLabel(point.month)}消费结构`,
    detail: '待分类是已确认但尚未归类的消费状态，并非第四个业务分类。',
    total: toNumber(point.total_spending_cny),
    primaryAmounts: primaryAmounts(point),
    secondaryBreakdowns: point.secondary_breakdowns,
  }
}

function rollingStructure(points: ConsumptionMonthlyPoint[]): ConsumptionStructureData | null {
  if (points.length === 0) return null
  const primary = points.reduce<Record<CategoryKey, number>>((totals, point) => {
    const amounts = primaryAmounts(point)
    for (const key of CATEGORY_META) totals[key.key] += amounts[key.key]
    return totals
  }, { daily_cny: 0, housing_cny: 0, travel_cny: 0, unclassified_eligible_cny: 0 })
  const total = points.reduce((sum, point) => sum + toNumber(point.total_spending_cny), 0)
  const secondary = new Map<string, { primary: ConsumptionSecondaryBreakdown['primary_category']; category: string; amount: number; count: number }>()
  for (const point of points) for (const item of point.secondary_breakdowns) {
    const key = `${item.primary_category}:${item.secondary_category}`
    const current = secondary.get(key) ?? { primary: item.primary_category, category: item.secondary_category, amount: 0, count: 0 }
    current.amount += toNumber(item.amount_cny)
    current.count += item.event_count
    secondary.set(key, current)
  }
  const primaryAmountByCategory = {
    DAILY: primary.daily_cny,
    HOUSING: primary.housing_cny,
    TRAVEL: primary.travel_cny,
  }
  const secondaryBreakdowns = [...secondary.values()]
    .map(item => ({
      primary_category: item.primary,
      secondary_category: item.category,
      amount_cny: String(item.amount),
      event_count: item.count,
      share_of_total: total > 0 ? String(item.amount / total) : null,
      share_within_primary: primaryAmountByCategory[item.primary] > 0 ? String(item.amount / primaryAmountByCategory[item.primary]) : null,
    }))
    .sort((left, right) => toNumber(right.amount_cny) - toNumber(left.amount_cny) || left.secondary_category.localeCompare(right.secondary_category))
  return {
    id: `rolling-${points.at(-1)?.month}`,
    title: '近12个月消费结构',
    detail: `统计区间：${monthLabel(points[0].month)} – ${monthLabel(points.at(-1)!.month)}`,
    total,
    primaryAmounts: primary,
    secondaryBreakdowns,
  }
}

function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return <section style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12, boxShadow: 'var(--shadow-sm)', ...style }}>{children}</section>
}

function Skeleton() {
  return <div style={{ display: 'grid', gap: 16 }} aria-label="正在加载消费数据">
    {[100, 300, 220, 260].map((height, index) => <div key={index} style={{ height, borderRadius: 12, background: '#E5E7EB', opacity: 0.7 }} />)}
  </div>
}

export default function Consumption() {
  const [summary, setSummary] = useState<ConsumptionAnalyticsSummary | null>(null)
  const [selectedMonth, setSelectedMonth] = useState<string | null>(null)
  const [kpiWindow, setKpiWindow] = useState<KpiWindow | null>(null)
  const [details, setDetails] = useState<ConsumptionEventDetail[]>([])
  const [rollingDetails, setRollingDetails] = useState<ConsumptionEventDetail[]>([])
  const [candidates, setCandidates] = useState<ConsumptionCandidate[]>([])
  const [candidateTotal, setCandidateTotal] = useState(0)
  const [candidateMonth, setCandidateMonth] = useState<string | null>(null)
  const [candidateActions, setCandidateActions] = useState<Record<string, CandidateActionState>>({})
  const [candidateDrafts, setCandidateDrafts] = useState<Record<string, ClassificationDraft>>({})
  const [detailReloadVersion, setDetailReloadVersion] = useState(0)
  const [rollingDetailReloadVersion, setRollingDetailReloadVersion] = useState(0)
  const [detailTotal, setDetailTotal] = useState(0)
  const [rollingDetailTotal, setRollingDetailTotal] = useState(0)
  const [detailLoading, setDetailLoading] = useState(false)
  const [rollingDetailLoading, setRollingDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [rollingDetailError, setRollingDetailError] = useState<string | null>(null)
  const [detailClassificationFilter, setDetailClassificationFilter] = useState<DetailClassificationFilter>('ALL')
  const [detailPrimaryFilter, setDetailPrimaryFilter] = useState<EditablePrimary | ''>('')
  const [detailSecondaryFilter, setDetailSecondaryFilter] = useState('')
  const [rollingDetailClassificationFilter, setRollingDetailClassificationFilter] = useState<DetailClassificationFilter>('ALL')
  const [rollingDetailPrimaryFilter, setRollingDetailPrimaryFilter] = useState<EditablePrimary | ''>('')
  const [rollingDetailSecondaryFilter, setRollingDetailSecondaryFilter] = useState('')
  const [editing, setEditing] = useState<Record<string, ClassificationDraft>>({})
  const [autosaveStates, setAutosaveStates] = useState<Record<string, AutosaveState>>({})
  const [noteDrafts, setNoteDrafts] = useState<Record<string, string>>({})
  const [noteEditors, setNoteEditors] = useState<Record<string, string>>({})
  const [noteSaveStates, setNoteSaveStates] = useState<Record<string, NoteSaveState>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const autosaveTimers = useRef<Record<string, number>>({})
  const requestVersions = useRef<Record<string, number>>({})
  const detailRequestVersion = useRef(0)
  const rollingDetailRequestVersion = useRef(0)

  const detailFilters = useMemo(() => ({
    classificationStatus: detailClassificationFilter === 'ALL' ? undefined : detailClassificationFilter,
    primaryCategory: detailClassificationFilter === 'NEEDS_REVIEW' ? undefined : detailPrimaryFilter || undefined,
    secondaryCategory: detailClassificationFilter === 'NEEDS_REVIEW' ? undefined : detailSecondaryFilter || undefined,
  }), [detailClassificationFilter, detailPrimaryFilter, detailSecondaryFilter])
  const rollingDetailFilters = useMemo(() => ({
    classificationStatus: rollingDetailClassificationFilter === 'ALL' ? undefined : rollingDetailClassificationFilter,
    primaryCategory: rollingDetailClassificationFilter === 'NEEDS_REVIEW' ? undefined : rollingDetailPrimaryFilter || undefined,
    secondaryCategory: rollingDetailClassificationFilter === 'NEEDS_REVIEW' ? undefined : rollingDetailSecondaryFilter || undefined,
  }), [rollingDetailClassificationFilter, rollingDetailPrimaryFilter, rollingDetailSecondaryFilter])

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

  const selected = useMemo(
    () => summary?.months.find(item => item.month === selectedMonth) ?? summary?.months.at(-1) ?? null,
    [selectedMonth, summary],
  )
  const rollingPoints = useMemo(() => summary?.months.slice(-12) ?? [], [summary])
  const rollingDetailStartMonth = rollingPoints[0]?.month
  const rollingDetailEndMonth = rollingPoints.at(-1)?.month
  const twelveMonthStructure = useMemo(() => rollingStructure(rollingPoints), [rollingPoints])

  useEffect(() => {
    const task = window.setTimeout(load, 0)
    return () => window.clearTimeout(task)
  }, [])

  useEffect(() => () => {
    Object.values(autosaveTimers.current).forEach(timer => window.clearTimeout(timer))
  }, [])

  useEffect(() => {
    if (!summary || !selected || summary.months.at(-1)?.month === selected.month) return
    let active = true
    void consumptionApi.getAnalytics({ asOf: monthEnd(selected.month), months: 12 })
      .then(value => { if (active) setKpiWindow({ endingMonth: selected.month, summary: value }) })
      .catch(() => undefined)
    return () => { active = false }
  }, [selected, summary])

  useEffect(() => {
    if (!selectedMonth) return
    let active = true
    const requestVersion = ++detailRequestVersion.current
    void Promise.resolve().then(() => {
      if (!active) return
      setDetailLoading(true)
      setDetailError(null)
      return loadAllDetailPages({ month: selectedMonth.slice(0, 7), ...detailFilters })
        .then(value => { if (active && detailRequestVersion.current === requestVersion) { setDetails(value.items); setDetailTotal(value.total) } })
        .catch(() => { if (active && detailRequestVersion.current === requestVersion) { setDetails([]); setDetailTotal(0); setDetailError('月度明细加载失败') } })
        .finally(() => { if (active && detailRequestVersion.current === requestVersion) setDetailLoading(false) })
    })
    return () => { active = false }
  }, [selectedMonth, detailFilters, detailReloadVersion])

  useEffect(() => {
    if (!rollingDetailStartMonth || !rollingDetailEndMonth) return
    let active = true
    const requestVersion = ++rollingDetailRequestVersion.current
    void Promise.resolve().then(() => {
      if (!active) return
      setRollingDetailLoading(true)
      setRollingDetailError(null)
      return loadAllDetailPages({
        startMonth: rollingDetailStartMonth.slice(0, 7), endMonth: rollingDetailEndMonth.slice(0, 7),
        ...rollingDetailFilters,
      })
        .then(value => { if (active && rollingDetailRequestVersion.current === requestVersion) { setRollingDetails(value.items); setRollingDetailTotal(value.total) } })
        .catch(() => { if (active && rollingDetailRequestVersion.current === requestVersion) { setRollingDetails([]); setRollingDetailTotal(0); setRollingDetailError('近12个月明细加载失败') } })
        .finally(() => { if (active && rollingDetailRequestVersion.current === requestVersion) setRollingDetailLoading(false) })
    })
    return () => { active = false }
  }, [rollingDetailStartMonth, rollingDetailEndMonth, rollingDetailFilters, rollingDetailReloadVersion])

  useEffect(() => {
    if (!selectedMonth) return
    let active = true
    void Promise.resolve().then(() => {
      if (!active) return
      return consumptionApi.getCandidates({ month: selectedMonth.slice(0, 7), limit: 100, offset: 0 })
        .then(value => { if (active) { setCandidates(value.items); setCandidateTotal(value.total); setCandidateMonth(selectedMonth) } })
        .catch(() => { if (active) { setCandidates([]); setCandidateTotal(0); setCandidateMonth(selectedMonth) } })
    })
    return () => { active = false }
  }, [selectedMonth])

  const saveClassification = async (item: ConsumptionEventDetail, draft: ClassificationDraft, version: number) => {
    try {
      const result = await consumptionApi.updateEventClassification(item.event_id, draft.primary, draft.secondary)
      if (requestVersions.current[item.event_id] !== version) return
      // 分类保存的本地结果比尚未完成的旧列表请求更新，避免旧响应把已移除的
      // NEEDS_REVIEW 行重新写回页面。
      detailRequestVersion.current++
      rollingDetailRequestVersion.current++
      setDetails(current => updateVisibleDetail(current, item.event_id, result, detailFilters))
      setRollingDetails(current => updateVisibleDetail(current, item.event_id, result, rollingDetailFilters))
      setDetailLoading(false)
      setRollingDetailLoading(false)
      if (detailClassificationFilter === 'NEEDS_REVIEW' && details.some(row => row.event_id === item.event_id)) setDetailTotal(current => Math.max(0, current - 1))
      if (rollingDetailClassificationFilter === 'NEEDS_REVIEW' && rollingDetails.some(row => row.event_id === item.event_id)) setRollingDetailTotal(current => Math.max(0, current - 1))
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

  const beginNoteEdit = (item: ConsumptionEventDetail, viewId: string) => {
    setNoteDrafts(current => current[item.event_id] == null ? { ...current, [item.event_id]: item.user_note ?? '' } : current)
    setNoteEditors(current => ({ ...current, [item.event_id]: viewId }))
  }

  const updateNoteDraft = (eventId: string, value: string) => {
    setNoteDrafts(current => ({ ...current, [eventId]: value }))
    setNoteSaveStates(current => { const next = { ...current }; delete next[eventId]; return next })
  }

  const saveNote = async (item: ConsumptionEventDetail) => {
    const draft = noteDrafts[item.event_id]
    if (draft == null) return
    const value = draft.trim()
    if (value === (item.user_note ?? '')) {
      setNoteDrafts(current => { const next = { ...current }; delete next[item.event_id]; return next })
      setNoteEditors(current => { const next = { ...current }; delete next[item.event_id]; return next })
      return
    }
    setNoteSaveStates(current => ({ ...current, [item.event_id]: { state: 'saving', value: draft } }))
    try {
      const result = await consumptionApi.updateEventNote(item.event_id, draft)
      const apply = (items: ConsumptionEventDetail[]) => items.map(row => row.event_id === item.event_id ? { ...row, user_note: result.user_note } : row)
      setDetails(apply)
      setRollingDetails(apply)
      setNoteDrafts(current => { const next = { ...current }; delete next[item.event_id]; return next })
      setNoteEditors(current => { const next = { ...current }; delete next[item.event_id]; return next })
      setNoteSaveStates(current => ({ ...current, [item.event_id]: { state: 'saved', value: result.user_note ?? '' } }))
    } catch {
      setNoteSaveStates(current => ({ ...current, [item.event_id]: { state: 'error', value: draft } }))
    }
  }

  const updateCandidateDraft = (eventId: string, draft: ClassificationDraft) => {
    setCandidateDrafts(current => ({ ...current, [eventId]: draft }))
    setCandidateActions(current => { const next = { ...current }; delete next[eventId]; return next })
  }

  const confirmCandidate = async (candidate: ConsumptionCandidate) => {
    const draft = candidateDrafts[candidate.event_id]
    if (!draft?.secondary) {
      setCandidateActions(current => ({ ...current, [candidate.event_id]: { state: 'error', draft } }))
      return
    }
    setCandidateActions(current => ({ ...current, [candidate.event_id]: { state: 'confirming', draft } }))
    try {
      await consumptionApi.confirmCandidate(candidate.event_id, draft.primary, draft.secondary)
      setCandidates(current => current.filter(item => item.event_id !== candidate.event_id))
      setCandidateTotal(current => Math.max(0, current - 1))
      setCandidateDrafts(current => { const next = { ...current }; delete next[candidate.event_id]; return next })
      setCandidateActions(current => { const next = { ...current }; delete next[candidate.event_id]; return next })
      setDetailReloadVersion(current => current + 1)
      setRollingDetailReloadVersion(current => current + 1)
      refreshSummary()
    } catch {
      setCandidateActions(current => ({ ...current, [candidate.event_id]: { state: 'error', draft } }))
    }
  }

  const rejectCandidate = async (candidate: ConsumptionCandidate) => {
    setCandidateActions(current => ({ ...current, [candidate.event_id]: { state: 'rejecting' } }))
    try {
      await consumptionApi.rejectCandidate(candidate.event_id)
      setCandidates(current => current.filter(item => item.event_id !== candidate.event_id))
      setCandidateTotal(current => Math.max(0, current - 1))
      setCandidateDrafts(current => { const next = { ...current }; delete next[candidate.event_id]; return next })
      setCandidateActions(current => { const next = { ...current }; delete next[candidate.event_id]; return next })
      setRollingDetailReloadVersion(current => current + 1)
      refreshSummary()
    } catch {
      setCandidateActions(current => ({ ...current, [candidate.event_id]: { state: 'error' } }))
    }
  }

  const chartData = useMemo(() => (summary?.months ?? []).map(item => ({
    ...item,
    label: shortMonth(item.month),
    daily_cny: toNumber(item.daily_cny), travel_cny: toNumber(item.travel_cny),
    housing_cny: toNumber(item.housing_cny), unclassified_eligible_cny: toNumber(item.unclassified_eligible_cny),
  })), [summary])

  if (loading) return <Skeleton />
  if (error) return <Card style={{ padding: 20, color: '#991B1B' }}><div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 600 }}><AlertTriangle size={18} /> {error}</div><button onClick={load} style={secondaryButtonStyle}><RefreshCw size={14} /> 重试</button></Card>
  if (!summary || !selected || summary.months.every(item => toNumber(item.total_spending_cny) === 0 && item.eligibility_review_count === 0)) {
    return (
      <div>
        <PageHeader icon={<WalletCards size={17} strokeWidth={2.25} color="#fff" />} title="消费分析" subtitle="消费趋势 · 分类结构" />
        <Card><EmptyState icon={ReceiptText} title="暂无消费分析数据" desc="完成消费账户数据导入后，可在这里查看月度消费趋势。" /></Card>
      </div>
    )
  }

  const selectedKpiSummary = summary.months.at(-1)?.month === selected.month ? summary : kpiWindow?.endingMonth === selected.month ? kpiWindow.summary : null
  const selectedKpiPoints = selectedKpiSummary?.months ?? null
  const rollingTotal = rollingPoints.reduce((total, point) => total + toNumber(point.total_spending_cny), 0)
  const rollingAverage = rollingTotal / 12
  const previousMonth = selectedKpiPoints?.at(-2)
  const previousAmount = previousMonth ? toNumber(previousMonth.total_spending_cny) : null
  const selectedIsOpen = isOpenCalendarMonth(selected)
  const monthOverMonth = !selectedIsOpen && previousAmount != null && previousAmount !== 0
    ? (toNumber(selected.total_spending_cny) - previousAmount) / previousAmount * 100
    : null

  return <div style={{ paddingBottom: 24 }}>
    <PageHeader
      icon={<WalletCards size={17} strokeWidth={2.25} color="#fff" />}
      title="消费分析"
      subtitle="消费趋势 · 分类结构"
    />

    <div style={kpiGridStyle}>
      <section style={heroStyle}>
        <div style={{ fontSize: 12, color: '#C7D2FE', fontWeight: 600 }}>本月消费 · {monthLabel(selected.month)}</div>
        <div className="tabular-nums" style={{ marginTop: 8, fontSize: 32, lineHeight: 1, fontWeight: 750, letterSpacing: '-1px' }}>{fmtCny(toNumber(selected.total_spending_cny))}</div>
        {selectedIsOpen && <div style={{ fontSize: 12, color: '#CBD5E1', marginTop: 10 }}>截至 {selected.as_of_date}</div>}
      </section>
      <Card style={kpiCardStyle}><div style={kpiLabelStyle}>近12个月消费</div><div className="tabular-nums" style={kpiValueStyle}>{rollingTotal == null ? '—' : fmtCny(rollingTotal)}</div><div style={detailTextStyle}>{rollingAverage == null ? '统计更新中' : `月均 ${fmtCny(rollingAverage)}`}</div></Card>
      <Card style={kpiCardStyle}><div style={kpiLabelStyle}>本月消费环比</div><div className="tabular-nums" style={kpiValueStyle}>{monthOverMonth == null ? '—' : fmtChange(monthOverMonth)}</div><div style={detailTextStyle}>{selectedIsOpen ? '本月尚未结束' : previousAmount == null || previousAmount === 0 ? '暂无可比上月数据' : `较${shortMonth(previousMonth!.month)} · ${fmtCny(previousAmount)}`}</div></Card>
    </div>

    <Card style={{ padding: '18px 18px 12px', marginTop: 16 }}>
      <SectionTitle title="近 12 个月消费趋势" detail="按自然月展示，柱高为后端已确认的月度总消费。" />
      <div style={{ width: '100%', height: 310, minWidth: 0 }}>
        <ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} margin={{ top: 12, right: 8, left: -8, bottom: 0 }}>
          <XAxis dataKey="label" tick={({ x, y, payload }: { x: number; y: number; payload: { value: string } }) => {
            const isSelected = payload.value === shortMonth(selected.month)
            return <text data-testid={`trend-month-${payload.value}`} x={x} y={y + 14} textAnchor="middle" fill={isSelected ? '#1D4ED8' : '#6B7280'} fontSize={11} fontWeight={isSelected ? 700 : 400}>{payload.value}</text>
          }} axisLine={false} tickLine={false} />
          <YAxis tickFormatter={value => `¥${Math.round(value / 1000)}k`} tick={{ fontSize: 11, fill: '#9CA3AF' }} axisLine={false} tickLine={false} width={42} />
          <Tooltip content={({ active, payload, label }) => { const point = payload?.[0]?.payload as ConsumptionMonthlyPoint | undefined; return active && point ? <TrendTooltip point={point} label={label as string} /> : null }} />
          <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12, paddingTop: 8 }} payload={CATEGORY_META.map(item => ({ id: item.key, value: item.label, type: 'circle', color: item.color }))} />
          {STACK_RENDER_META.map(item => <Bar key={item.key} dataKey={item.key} name={item.label} stackId="spending" fill={item.color} maxBarSize={42} cursor="pointer" onClick={(_, index) => {
            const month = chartData[index]?.month
            if (month) setSelectedMonth(month)
          }}>{chartData.map(point => <Cell key={`${item.key}-${point.month}`} data-testid={`trend-bar-${item.key}-${point.month}`} fillOpacity={1} />)}</Bar>)}
        </BarChart></ResponsiveContainer>
      </div>
    </Card>

    {candidateMonth === selectedMonth && candidateTotal > 0 && candidates.length > 0 && <ConsumptionCandidateCard
      items={candidates}
      total={candidateTotal}
      drafts={candidateDrafts}
      actions={candidateActions}
      onDraftChange={updateCandidateDraft}
      onCancel={eventId => setCandidateDrafts(current => { const next = { ...current }; delete next[eventId]; return next })}
      onConfirm={confirmCandidate}
      onReject={rejectCandidate}
    />}

    <Card style={{ padding: '20px 20px 16px', marginTop: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, marginBottom: 14 }}>
        <div><div style={{ fontSize: 14, color: '#1B2A4A', fontWeight: 700 }}>{monthLabel(selected.month)}消费明细</div><div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 3 }}>仅显示已确认纳入消费分析的记录；消费名称直接来自原始账单描述。</div></div>
        <a href={consumptionApi.getEventsExportUrl({ month: selected.month.slice(0, 7) }, detailFilters)} download style={exportButtonStyle}><Download size={13} /> 导出 CSV</a>
      </div>
      <DetailFilterBar classificationFilter={detailClassificationFilter} primaryFilter={detailPrimaryFilter} secondaryFilter={detailSecondaryFilter} onClassificationChange={setDetailClassificationFilter} onPrimaryChange={setDetailPrimaryFilter} onSecondaryChange={setDetailSecondaryFilter} />
      <MonthlyDetailTable viewId="monthly" items={details} total={detailTotal} loading={detailLoading} error={detailError} editing={editing} autosaveStates={autosaveStates} noteDrafts={noteDrafts} noteEditors={noteEditors} noteSaveStates={noteSaveStates} onChange={scheduleClassificationSave} onNoteStart={beginNoteEdit} onNoteChange={updateNoteDraft} onNoteSave={saveNote} />
    </Card>

    <ConsumptionStructureCard data={monthlyStructure(selected)} />
    {rollingDetailStartMonth && rollingDetailEndMonth && <Card style={{ padding: '20px 20px 16px', marginTop: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, marginBottom: 14 }}>
        <div><div style={{ fontSize: 14, color: '#1B2A4A', fontWeight: 700 }}>近12个月消费明细</div><div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 3 }}>统计区间：{monthLabel(rollingDetailStartMonth)} – {monthLabel(rollingDetailEndMonth)}</div></div>
        <a href={consumptionApi.getEventsExportUrl({ startMonth: rollingDetailStartMonth.slice(0, 7), endMonth: rollingDetailEndMonth.slice(0, 7) }, rollingDetailFilters)} download style={exportButtonStyle}><Download size={13} /> 导出 CSV</a>
      </div>
      <DetailFilterBar prefix="近12个月" classificationFilter={rollingDetailClassificationFilter} primaryFilter={rollingDetailPrimaryFilter} secondaryFilter={rollingDetailSecondaryFilter} onClassificationChange={setRollingDetailClassificationFilter} onPrimaryChange={setRollingDetailPrimaryFilter} onSecondaryChange={setRollingDetailSecondaryFilter} />
      <MonthlyDetailTable viewId="rolling" items={rollingDetails} total={rollingDetailTotal} loading={rollingDetailLoading} error={rollingDetailError} editing={editing} autosaveStates={autosaveStates} noteDrafts={noteDrafts} noteEditors={noteEditors} noteSaveStates={noteSaveStates} onChange={scheduleClassificationSave} onNoteStart={beginNoteEdit} onNoteChange={updateNoteDraft} onNoteSave={saveNote} />
    </Card>}
    {twelveMonthStructure && <ConsumptionStructureCard data={twelveMonthStructure} testIdPrefix="rolling-" />}
  </div>
}

function TrendTooltip({ point, label }: { point: ConsumptionMonthlyPoint; label: string }) {
  return <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 8, padding: '9px 11px', boxShadow: '0 4px 12px rgba(15,30,53,0.12)', fontSize: 12, lineHeight: 1.8, color: '#374151' }}><div style={{ fontWeight: 700, color: '#1B2A4A', marginBottom: 3 }}>{label}</div><div>总消费：<b>{fmtCny(toNumber(point.total_spending_cny))}</b></div>{CATEGORY_META.map(item => <div key={item.key}>{item.label}：{fmtCny(toNumber(point[item.key]))}</div>)}<div>分类覆盖率：{coverageRate(point) == null ? '—' : fmtPct(coverageRate(point))}</div>{!point.amount_complete && <div style={{ color: '#B45309', marginTop: 3 }}>部分外币消费尚未完成人民币金额换算，当前为已知金额。</div>}</div>
}

function ConsumptionStructureCard({ data, testIdPrefix = '' }: { data: ConsumptionStructureData; testIdPrefix?: string }) {
  const [hoveredPrimary, setHoveredPrimary] = useState<CategoryKey | null>(null)
  const [tabSelection, setTabSelection] = useState<{ structureId: string; primary: EditablePrimary } | null>(null)
  const primaryRows = CATEGORY_META.map(item => ({
    ...item,
    amount: data.primaryAmounts[item.key],
    share: data.total > 0 ? data.primaryAmounts[item.key] / data.total * 100 : null,
  }))
  const defaultPrimary = SECONDARY_TAB_META
    .filter(item => data.primaryAmounts[PRIMARY_CATEGORY_KEYS[item.key]] > 0)
    .sort((left, right) => data.primaryAmounts[PRIMARY_CATEGORY_KEYS[right.key]] - data.primaryAmounts[PRIMARY_CATEGORY_KEYS[left.key]])[0]?.key ?? null
  const activePrimary = tabSelection?.structureId === data.id ? tabSelection.primary : defaultPrimary
  const secondaryItems = activePrimary == null
    ? []
    : data.secondaryBreakdowns.filter(item => item.primary_category === activePrimary)
  const hoveredIndex = primaryRows.findIndex(item => item.key === hoveredPrimary)
  const hoveredRow = hoveredIndex < 0 ? null : primaryRows[hoveredIndex]
  const tooltipAnchor = hoveredRow == null
    ? 50
    : primaryRows.slice(0, hoveredIndex).reduce((sum, item) => sum + (item.share ?? 0), 0) + (hoveredRow.share ?? 0) / 2

  return <Card style={{ padding: 20, marginTop: 16 }}>
    <SectionTitle title={data.title} detail={data.detail} />
    <div style={structureGridStyle}>
      <div style={primaryStructureStyle}>
        <div style={structureColumnTitleStyle}>一级分类总览</div>
        <div aria-label="一级分类占比" style={stackedBarStyle}>
          {data.total > 0 && primaryRows.filter(item => item.amount > 0).map((item, index, items) => <div key={item.key} style={{ position: 'relative', width: `${item.share ?? 0}%`, height: '100%', flexShrink: 0 }}>
            <div style={{ width: '100%', height: '100%', background: item.color, borderTopLeftRadius: index === 0 ? 99 : 0, borderBottomLeftRadius: index === 0 ? 99 : 0, borderTopRightRadius: index === items.length - 1 ? 99 : 0, borderBottomRightRadius: index === items.length - 1 ? 99 : 0, opacity: hoveredPrimary && hoveredPrimary !== item.key ? 0.82 : 1, transition: 'opacity 120ms ease' }} />
            <div data-testid={`${testIdPrefix}structure-segment-${item.key}`} aria-label={`${item.label}占比`} onMouseEnter={() => setHoveredPrimary(item.key)} onMouseLeave={() => setHoveredPrimary(null)} style={{ ...segmentHitAreaStyle, zIndex: 100 - Math.round(item.share ?? 0) }} />
          </div>)}
          {hoveredRow && <div role="tooltip" data-testid={`${testIdPrefix}structure-segment-tooltip`} style={{ ...structureTooltipStyle, left: `${Math.min(88, Math.max(12, tooltipAnchor))}%` }}><div style={{ color: '#1B2A4A', fontWeight: 700 }}>{hoveredRow.label}</div><div className="tabular-nums" style={structureTooltipValueStyle}><span>金额</span><b>{fmtCny(hoveredRow.amount)}</b></div><div className="tabular-nums" style={structureTooltipValueStyle}><span>占比</span><b>{hoveredRow.share == null ? '—' : fmtPct(hoveredRow.share)}</b></div></div>}
        </div>
        <div style={{ display: 'grid', gap: 10, marginTop: 14 }}>
          {primaryRows.map(item => <div key={item.key} style={primaryLegendRowStyle}>
            <div style={{ display: 'flex', alignItems: 'center', minWidth: 0, gap: 7 }}><span style={{ width: 8, height: 8, flexShrink: 0, borderRadius: 99, background: item.color }} /><span style={{ fontSize: 12, color: '#4B5563' }}>{item.label}</span></div>
            <div className="tabular-nums" style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'end', gap: 9 }}><span style={{ fontSize: 13, fontWeight: 700, color: '#1B2A4A' }}>{fmtCny(item.amount)}</span><span style={{ width: 40, textAlign: 'right', fontSize: 11, color: '#9CA3AF' }}>{item.share == null ? '—' : fmtPct(item.share)}</span></div>
          </div>)}
        </div>
      </div>
      <div>
        <div style={structureColumnTitleStyle}>二级分类明细</div>
        <div role="tablist" aria-label={`${data.title}二级分类`} style={secondaryTabListStyle}>
          {SECONDARY_TAB_META.map(item => {
            const active = activePrimary === item.key
            return <button key={item.key} type="button" role="tab" aria-selected={active} data-testid={`${testIdPrefix}secondary-category-tab-${item.key}`} onClick={() => setTabSelection({ structureId: data.id, primary: item.key })} style={{ ...secondaryTabStyle, ...(active ? secondaryTabActiveStyle : {}) }}>{item.label}</button>
          })}
        </div>
        {activePrimary == null || secondaryItems.length === 0
          ? <LightEmpty text="当前分类暂无消费明细" />
          : <div style={{ display: 'grid', gap: 10, marginTop: 14 }}>{secondaryItems.map(item => <div key={`${item.primary_category}-${item.secondary_category}`}><div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, color: '#4B5563', fontSize: 12 }}><span>{SECONDARY_LABELS[item.secondary_category] ?? item.secondary_category}</span><span className="tabular-nums" style={{ display: 'flex', gap: 9 }}><b style={{ color: '#1B2A4A' }}>{fmtCny(toNumber(item.amount_cny))}</b><span style={{ width: 40, textAlign: 'right', color: '#9CA3AF' }}>{fmtPct(toNumber(item.share_within_primary) * 100)}</span></span></div><div style={{ height: 5, overflow: 'hidden', borderRadius: 99, background: '#EEF2F7', marginTop: 5 }}><div style={{ width: `${Math.min(100, toNumber(item.share_within_primary) * 100)}%`, height: '100%', borderRadius: 99, background: '#60A5FA' }} /></div></div>)}</div>}
      </div>
    </div>
  </Card>
}

function ConsumptionCandidateCard({ items, total, drafts, actions, onDraftChange, onCancel, onConfirm, onReject }: {
  items: ConsumptionCandidate[]
  total: number
  drafts: Record<string, ClassificationDraft>
  actions: Record<string, CandidateActionState>
  onDraftChange: (eventId: string, draft: ClassificationDraft) => void
  onCancel: (eventId: string) => void
  onConfirm: (candidate: ConsumptionCandidate) => void
  onReject: (candidate: ConsumptionCandidate) => void
}) {
  return <Card style={{ padding: '18px 20px 16px', marginTop: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 }}>
      <div><div style={{ fontSize: 14, color: '#1B2A4A', fontWeight: 700 }}>消费候选待确认</div><div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 3 }}>这些是用途尚不明确的资金流出；确认后才会进入消费分析。</div></div>
      <span style={candidateCountStyle}>{total} 笔</span>
    </div>
    <div style={candidateTableScrollStyle}><table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 900 }}><thead><tr>{['日期', '原始交易描述', '账户 / 来源', '金额', '当前状态', '操作'].map((label, index) => <th key={label} style={{ ...tableHeaderStyle, textAlign: index === 3 ? 'right' : 'left' }}>{label}</th>)}</tr></thead><tbody>{items.map(candidate => {
        const draft = drafts[candidate.event_id]
        const action = actions[candidate.event_id]
        const secondaryOptions = draft ? EDITABLE_TAXONOMY[draft.primary] : []
        const busy = action?.state === 'confirming' || action?.state === 'rejecting'
        return <tr key={candidate.event_id} data-testid={`consumption-candidate-${candidate.event_id}`}>
          <td style={tableCellStyle}>{candidate.analytics_effective_date}</td>
          <td style={{ ...tableCellStyle, maxWidth: 280 }}><div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: 600, color: '#374151' }}>{candidate.raw_description}</div></td>
          <td style={tableCellStyle}><div>{candidate.account_display_name}</div><div style={{ fontSize: 10, color: '#9CA3AF', marginTop: 2 }}>{candidate.source_label}</div></td>
          <td className="tabular-nums" style={{ ...tableCellStyle, textAlign: 'right', fontWeight: 700, color: '#1B2A4A' }}>{candidate.amount_cny == null ? '金额待换算' : fmtCny(toNumber(candidate.amount_cny))}</td>
          <td style={tableCellStyle}><span style={candidatePillStyle}>待确认</span></td>
          <td style={tableCellStyle}>{draft ? <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 6 }}>
            <select aria-label={`候选一级分类 ${candidate.event_id}`} value={draft.primary} disabled={busy} onChange={event => {
              const primary = event.target.value as EditablePrimary
              const secondary = EDITABLE_TAXONOMY[primary].includes(draft.secondary as never) ? draft.secondary : ''
              onDraftChange(candidate.event_id, { primary, secondary })
            }} style={selectStyle}>{Object.entries({ DAILY: '日常消费', TRAVEL: '旅行消费', HOUSING: '住房消费' }).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
            <select aria-label={`候选二级分类 ${candidate.event_id}`} value={draft.secondary} disabled={busy} onChange={event => onDraftChange(candidate.event_id, { ...draft, secondary: event.target.value })} style={selectStyle}><option value="">选择分类</option>{secondaryOptions.map(value => <option key={value} value={value}>{SECONDARY_LABELS[value] ?? value}</option>)}</select>
            <button type="button" disabled={busy || !draft.secondary} onClick={() => onConfirm(candidate)} style={candidateConfirmButtonStyle}>{action?.state === 'confirming' ? <><Loader2 size={12} className="animate-spin" /> 保存中…</> : '确认保存'}</button>
            <button type="button" disabled={busy} onClick={() => onCancel(candidate.event_id)} style={candidateCancelButtonStyle}>取消</button>
          </div> : <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <button type="button" disabled={busy} onClick={() => onDraftChange(candidate.event_id, { primary: 'DAILY', secondary: '' })} style={candidateConfirmButtonStyle}>确认为消费</button>
            <button type="button" disabled={busy} onClick={() => onReject(candidate)} style={candidateRejectButtonStyle}>{action?.state === 'rejecting' ? <><Loader2 size={12} className="animate-spin" /> 保存中…</> : '非消费'}</button>
          </div>}{action?.state === 'error' && <div style={candidateErrorStyle}>保存失败，请重试</div>}</td>
        </tr>
      })}</tbody></table></div>
  </Card>
}

function DetailFilterBar({ prefix = '', classificationFilter, primaryFilter, secondaryFilter, onClassificationChange, onPrimaryChange, onSecondaryChange }: {
  prefix?: string
  classificationFilter: DetailClassificationFilter
  primaryFilter: EditablePrimary | ''
  secondaryFilter: string
  onClassificationChange: (value: DetailClassificationFilter) => void
  onPrimaryChange: (value: EditablePrimary | '') => void
  onSecondaryChange: (value: string) => void
}) {
  const label = (value: string) => prefix ? `${prefix}明细${value.replace('筛选', '')}` : value
  return <div style={detailFilterBarStyle} aria-label={label('消费明细分类筛选')}>
    <label style={detailFilterLabelStyle}>分类状态<select aria-label={label('分类状态筛选')} value={classificationFilter} onChange={event => {
      const next = event.target.value as DetailClassificationFilter
      onClassificationChange(next)
      if (next === 'NEEDS_REVIEW') { onPrimaryChange(''); onSecondaryChange('') }
    }} style={detailFilterSelectStyle}><option value="ALL">全部</option><option value="CLASSIFIED">已分类</option><option value="NEEDS_REVIEW">待分类</option></select></label>
    <label style={detailFilterLabelStyle}>一级分类<select aria-label={label('一级分类筛选')} value={primaryFilter} disabled={classificationFilter === 'NEEDS_REVIEW'} onChange={event => { onPrimaryChange(event.target.value as EditablePrimary | ''); onSecondaryChange('') }} style={detailFilterSelectStyle}><option value="">全部</option><option value="DAILY">日常消费</option><option value="TRAVEL">旅行</option><option value="HOUSING">住房</option></select></label>
    <label style={detailFilterLabelStyle}>二级分类<select aria-label={label('二级分类筛选')} value={secondaryFilter} disabled={classificationFilter === 'NEEDS_REVIEW' || !primaryFilter} onChange={event => onSecondaryChange(event.target.value)} style={detailFilterSelectStyle}><option value="">全部</option>{primaryFilter && EDITABLE_TAXONOMY[primaryFilter].map(value => <option key={value} value={value}>{SECONDARY_LABELS[value]}</option>)}</select></label>
  </div>
}

function MonthlyDetailTable({ viewId, items, total, loading, error, editing, autosaveStates, noteDrafts, noteEditors, noteSaveStates, onChange, onNoteStart, onNoteChange, onNoteSave }: { viewId: string; items: ConsumptionEventDetail[]; total: number; loading: boolean; error: string | null; editing: Record<string, ClassificationDraft>; autosaveStates: Record<string, AutosaveState>; noteDrafts: Record<string, string>; noteEditors: Record<string, string>; noteSaveStates: Record<string, NoteSaveState>; onChange: (item: ConsumptionEventDetail, draft: ClassificationDraft) => void; onNoteStart: (item: ConsumptionEventDetail, viewId: string) => void; onNoteChange: (eventId: string, value: string) => void; onNoteSave: (item: ConsumptionEventDetail) => void }) {
  if (loading) return <div aria-label="正在加载月度明细" style={{ height: 170, borderRadius: 8, background: '#F9FAFB' }} />
  if (error) return <div style={{ padding: '16px 0', fontSize: 12, color: '#B91C1C' }}>{error}</div>
  if (items.length === 0) return <LightEmpty text="当前筛选条件下暂无消费明细" />
  return <><div style={{ fontSize: 11, color: '#9CA3AF', marginBottom: 9 }}>共 {total} 条，按金额从高到低排列</div><div style={{ overflow: 'auto', maxHeight: 494, border: '1px solid #F3F4F6', borderRadius: 6 }}><table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1100 }}><thead><tr>{['日期', '消费明细', '一级分类', '二级分类', '账户', '金额', '备注', '分类状态', '保存状态'].map((label, index) => <th key={label} style={{ ...tableHeaderStyle, textAlign: index === 5 ? 'right' : 'left' }}>{label}</th>)}</tr></thead><tbody>{items.map((item, index) => {
    const draft = editing[item.event_id]
    const primary = draft?.primary ?? item.primary_category ?? ''
    const secondary = draft?.secondary ?? item.secondary_category ?? ''
    const options = primary ? EDITABLE_TAXONOMY[primary as EditablePrimary] : []
    const state = autosaveStates[item.event_id]
    const noteDraft = noteDrafts[item.event_id]
    const noteState = noteSaveStates[item.event_id]
    return <tr key={`${item.event_id}-${index}`}><td style={tableCellStyle}>{item.analytics_effective_date}</td><td style={{ ...tableCellStyle, maxWidth: 250 }}><div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: 600, color: '#374151' }}>{item.raw_description}</div></td><td style={tableCellStyle}><select aria-label={`一级分类 ${item.event_id}`} value={primary} onChange={event => { const nextPrimary = event.target.value as EditablePrimary; const currentSecondary = secondary; const nextSecondary = EDITABLE_TAXONOMY[nextPrimary].includes(currentSecondary as never) ? currentSecondary : ''; onChange(item, { primary: nextPrimary, secondary: nextSecondary }) }} style={selectStyle}><option value="" disabled>待分类</option>{Object.entries({ DAILY: '日常消费', TRAVEL: '旅行消费', HOUSING: '住房消费' }).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></td><td style={tableCellStyle}><select aria-label={`二级分类 ${item.event_id}`} value={secondary} disabled={!primary} onChange={event => onChange(item, { primary: primary as EditablePrimary, secondary: event.target.value })} style={selectStyle}><option value="" disabled>选择分类</option>{options.map(value => <option key={value} value={value}>{SECONDARY_LABELS[value] ?? value}</option>)}</select></td><td style={tableCellStyle}>{item.account_display_name}</td><td className="tabular-nums" style={{ ...tableCellStyle, textAlign: 'right', fontWeight: 700, color: '#1B2A4A' }}>{fmtCny(toNumber(item.amount_cny))}</td><td style={{ ...tableCellStyle, minWidth: 164 }}>{noteEditors[item.event_id] === viewId ? <input aria-label={`备注 ${item.event_id}`} autoFocus maxLength={200} value={noteDraft ?? item.user_note ?? ''} onChange={event => onNoteChange(item.event_id, event.target.value)} onBlur={() => void onNoteSave(item)} onKeyDown={event => { if (event.key === 'Enter') { event.currentTarget.blur() } }} placeholder="添加备注" style={noteInputStyle} /> : <button type="button" aria-label={`备注 ${item.event_id}`} onClick={() => onNoteStart(item, viewId)} style={item.user_note ? noteTextButtonStyle : notePlaceholderButtonStyle}>{item.user_note || '添加备注'}</button>}{noteState?.state === 'saving' && <span style={{ ...autosaveSavingStyle, marginTop: 3 }}><Loader2 size={11} className="animate-spin" /> 保存中…</span>}{noteState?.state === 'saved' && <span style={{ ...autosaveSavedStyle, marginTop: 3 }}><Check size={11} /> 已保存</span>}{noteState?.state === 'error' && <button aria-label={`重试备注 ${item.event_id}`} type="button" onClick={() => void onNoteSave(item)} style={{ ...retryButtonStyle, marginTop: 3 }}>保存失败 / 重试</button>}</td><td style={tableCellStyle}><span style={item.classification_status === 'CLASSIFIED' ? classifiedPillStyle : reviewPillStyle}>{item.classification_status === 'CLASSIFIED' ? '已分类' : '待分类'}</span></td><td style={tableCellStyle}>{state?.state === 'saving' && <span style={autosaveSavingStyle}><Loader2 size={12} className="animate-spin" /> 保存中…</span>}{state?.state === 'saved' && <span style={autosaveSavedStyle}><Check size={12} /> 已保存</span>}{state?.state === 'error' && <button aria-label={`重试分类 ${item.event_id}`} onClick={() => onChange(item, state.draft)} style={retryButtonStyle}>保存失败 / 重试</button>}</td></tr>
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
const tableHeaderStyle: React.CSSProperties = { position: 'sticky', top: 0, zIndex: 1, whiteSpace: 'nowrap', background: '#fff', borderBottom: '1px solid #E5E7EB', padding: '8px 10px', fontSize: 11, color: '#6B7280', fontWeight: 600 }
const tableCellStyle: React.CSSProperties = { whiteSpace: 'nowrap', borderBottom: '1px solid #F3F4F6', padding: '9px 10px', fontSize: 12, color: '#4B5563' }
const classifiedPillStyle: React.CSSProperties = { display: 'inline-block', borderRadius: 99, padding: '3px 7px', fontSize: 11, color: '#047857', background: '#ECFDF5' }
const reviewPillStyle: React.CSSProperties = { display: 'inline-block', borderRadius: 99, padding: '3px 7px', fontSize: 11, color: '#B45309', background: '#FFFBEB' }
const candidatePillStyle: React.CSSProperties = { display: 'inline-block', borderRadius: 99, padding: '3px 7px', fontSize: 11, color: '#92400E', background: '#FEF3C7' }
const candidateCountStyle: React.CSSProperties = { flexShrink: 0, borderRadius: 99, padding: '3px 8px', color: '#92400E', background: '#FFFBEB', fontSize: 11, fontWeight: 600 }
const candidateConfirmButtonStyle: React.CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: 4, border: '1px solid #BFDBFE', borderRadius: 5, padding: '5px 7px', background: '#EFF6FF', color: '#1D4ED8', cursor: 'pointer', fontSize: 11, fontWeight: 600 }
const candidateRejectButtonStyle: React.CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: 4, border: '1px solid #E5E7EB', borderRadius: 5, padding: '5px 7px', background: '#fff', color: '#6B7280', cursor: 'pointer', fontSize: 11 }
const candidateCancelButtonStyle: React.CSSProperties = { border: 'none', padding: '4px 2px', background: 'transparent', color: '#6B7280', cursor: 'pointer', fontSize: 11 }
const candidateErrorStyle: React.CSSProperties = { marginTop: 5, color: '#B91C1C', fontSize: 11 }
const candidateTableScrollStyle: React.CSSProperties = { overflowX: 'auto', overflowY: 'auto', maxHeight: 'calc(34px + 10 * 44px)', marginTop: 14, border: '1px solid #F3F4F6', borderRadius: 6 }
const exportButtonStyle: React.CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: 5, flexShrink: 0, border: '1px solid #E5E7EB', borderRadius: 6, padding: '5px 9px', color: '#4B5563', background: '#fff', fontSize: 11, textDecoration: 'none' }
const detailFilterBarStyle: React.CSSProperties = { display: 'flex', alignItems: 'end', gap: 10, flexWrap: 'wrap', marginBottom: 14 }
const detailFilterLabelStyle: React.CSSProperties = { display: 'grid', gap: 4, color: '#6B7280', fontSize: 11, fontWeight: 600 }
const detailFilterSelectStyle: React.CSSProperties = { minWidth: 128, border: '1px solid #E5E7EB', borderRadius: 6, background: '#fff', color: '#374151', padding: '6px 8px', fontSize: 12 }
const selectStyle: React.CSSProperties = { maxWidth: 126, border: '1px solid #E5E7EB', borderRadius: 5, background: '#fff', color: '#374151', padding: '4px 6px', fontSize: 11 }
const noteTextButtonStyle: React.CSSProperties = { display: 'block', width: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', border: 'none', padding: 0, background: 'transparent', color: '#4B5563', cursor: 'text', fontSize: 12, textAlign: 'left' }
const notePlaceholderButtonStyle: React.CSSProperties = { ...noteTextButtonStyle, color: '#9CA3AF' }
const noteInputStyle: React.CSSProperties = { width: '100%', boxSizing: 'border-box', border: '1px solid #BFDBFE', borderRadius: 5, outline: 'none', padding: '4px 6px', color: '#374151', fontSize: 12 }
const autosaveSavingStyle: React.CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: 4, color: '#6B7280', fontSize: 11 }
const autosaveSavedStyle: React.CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: 4, color: '#047857', fontSize: 11 }
const retryButtonStyle: React.CSSProperties = { border: 'none', padding: 0, background: 'transparent', color: '#B91C1C', cursor: 'pointer', fontSize: 11 }
const structureGridStyle: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'minmax(0, 0.92fr) minmax(0, 1.08fr)', gap: 24 }
const primaryStructureStyle: React.CSSProperties = { paddingRight: 24, borderRight: '1px solid #F3F4F6' }
const structureColumnTitleStyle: React.CSSProperties = { color: '#374151', fontSize: 12, fontWeight: 700 }
const stackedBarStyle: React.CSSProperties = { position: 'relative', display: 'flex', overflow: 'visible', height: 10, marginTop: 13, borderRadius: 99, background: '#EEF2F7' }
const segmentHitAreaStyle: React.CSSProperties = { position: 'absolute', zIndex: 2, top: -5, bottom: -5, left: '50%', width: 'max(100%, 12px)', transform: 'translateX(-50%)' }
const structureTooltipStyle: React.CSSProperties = { position: 'absolute', zIndex: 3, top: 19, minWidth: 132, transform: 'translateX(-50%)', border: '1px solid #E5E7EB', borderRadius: 8, padding: '8px 10px', background: '#fff', boxShadow: '0 4px 12px rgba(15,30,53,0.12)', color: '#4B5563', fontSize: 11, lineHeight: 1.7, pointerEvents: 'none' }
const structureTooltipValueStyle: React.CSSProperties = { display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 15 }
const primaryLegendRowStyle: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', alignItems: 'center', columnGap: 12 }
const secondaryTabListStyle: React.CSSProperties = { display: 'flex', gap: 5, marginTop: 9, borderBottom: '1px solid #F3F4F6' }
const secondaryTabStyle: React.CSSProperties = { border: 'none', borderBottom: '2px solid transparent', padding: '6px 7px 8px', marginBottom: -1, background: 'transparent', color: '#6B7280', cursor: 'pointer', fontSize: 12, fontWeight: 600 }
const secondaryTabActiveStyle: React.CSSProperties = { borderBottomColor: '#3B82F6', color: '#1D4ED8' }

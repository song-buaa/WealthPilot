import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { Cell, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ChevronDown, Download, Edit3, Ellipsis, Loader2, RefreshCw, Trash2, WalletCards } from 'lucide-react'
import PageHeader from '@/components/shared/PageHeader'
import { dataManagementBarStyle, dataManagementExportButtonStyle } from '@/components/shared/dataManagementStyles'
import DonutDistributionCard from '@/components/shared/DonutDistributionCard'
import { fmtCny, fmtCnySigned, fmtPct } from '@/lib/fmt'
import { wealthApi, type WealthItem, type WealthItemWrite, type WealthSummary } from '@/lib/api'
import { wealthAssetColorMap, wealthLiabilityPalette } from './wealthChartPalette'

const ASSET_TYPES = [['bank_cash', '银行现金 / 活期'], ['time_deposit', '定期存款 / 大额存单'], ['housing_fund', '住房公积金'], ['enterprise_annuity', '企业年金'], ['personal_pension', '个人养老金'], ['pension_insurance', '养老保险'], ['basic_pension', '基本养老保险权益'], ['other_asset', '其他资产']] as const
const LIABILITY_TYPES = [['credit_card', '信用卡'], ['consumer_loan', '信用贷'], ['mortgage', '房贷'], ['other_liability', '其他负债']] as const

type FormState = WealthItemWrite & { id?: number }
type DetailFilter = 'asset' | 'liability'
type StructureItem = { key: string; label: string; coreValue: number }

const emptyForm = (kind: 'asset' | 'liability' = 'asset'): FormState => ({
  kind, name: '', item_type: kind === 'asset' ? 'bank_cash' : 'credit_card', current_value: 0,
  value_as_of: new Date().toISOString().slice(0, 10), included_in_net_worth: true,
  already_investment_accounted: false, source_type: 'MANUAL', notes: '',
})

const card = (primary = false) => ({
  background: primary ? 'linear-gradient(135deg, #1F2937, #111827)' : '#fff',
  border: primary ? 'none' : '1px solid #E5E7EB', borderRadius: 14,
  padding: primary ? '24px 26px' : '20px 22px', boxShadow: primary ? 'var(--shadow-dark)' : 'var(--shadow-sm)',
})

function csvCell(value: string | number | boolean | null | undefined) {
  const text = String(value ?? '')
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text
}

function wealthCsvFilename() {
  const now = new Date()
  const date = [now.getFullYear(), String(now.getMonth() + 1).padStart(2, '0'), String(now.getDate()).padStart(2, '0')].join('-')
  return `wealth_details_${date}.csv`
}

export default function WealthOverview() {
  const [summary, setSummary] = useState<WealthSummary | null>(null)
  const [assets, setAssets] = useState<WealthItem[]>([])
  const [liabilities, setLiabilities] = useState<WealthItem[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [range, setRange] = useState(365)
  const [form, setForm] = useState<FormState | null>(null)
  const [detailFilter, setDetailFilter] = useState<DetailFilter>('asset')
  const requestVersion = useRef(0)

  const refresh = useCallback((days = range) => {
    const version = ++requestVersion.current
    setLoading(true); setError(null)
    Promise.all([wealthApi.getSummary(days), wealthApi.getItems('asset'), wealthApi.getItems('liability')])
      .then(([s, a, l]) => { if (version === requestVersion.current) { setSummary(s); setAssets(a.items); setLiabilities(l.items) } })
      .catch((e: unknown) => { if (version === requestVersion.current) setError(e instanceof Error ? e.message : '财富数据加载失败') })
      .finally(() => { if (version === requestVersion.current) setLoading(false) })
  }, [range])

  useEffect(() => {
    const versionRef = requestVersion
    const timer = window.setTimeout(() => refresh(), 0)
    const refetch = () => { if (document.visibilityState === 'visible') refresh() }
    window.addEventListener('focus', refetch)
    document.addEventListener('visibilitychange', refetch)
    window.addEventListener('portfolio-updated', refetch)
    return () => {
      window.clearTimeout(timer)
      ++versionRef.current
      window.removeEventListener('focus', refetch)
      document.removeEventListener('visibilitychange', refetch)
      window.removeEventListener('portfolio-updated', refetch)
    }
  }, [refresh])

  const categoryValues = useMemo(() => new Map(summary?.asset_breakdown.map(item => [item.category, item.value]) ?? []), [summary])
  const assetStructure = useMemo<StructureItem[]>(() => {
    const investment = summary?.investment.total_assets ?? 0
    const cash = categoryValues.get('cash_deposits') ?? 0
    const retirementCore = categoryValues.get('retirement_long_term') ?? 0
    return [
      { key: 'investment', label: '投资资产', coreValue: investment },
      { key: 'cash_deposits', label: '现金及存款', coreValue: cash },
      { key: 'retirement_long_term', label: '住房公积金', coreValue: retirementCore },
    ]
  }, [categoryValues, summary])
  const pieData = useMemo(() => assetStructure.filter(item => item.coreValue > 0).map(item => ({ key: item.key, name: item.label, value: item.coreValue })), [assetStructure])
  const sortedLiabilities = useMemo(() => [...liabilities].sort((a, b) => b.current_value - a.current_value), [liabilities])
  const allDetails = useMemo(() => [...assets, ...liabilities].sort((a, b) => b.current_value - a.current_value), [assets, liabilities])
  const filteredDetails = useMemo(() => allDetails.filter(item => item.kind === detailFilter), [allDetails, detailFilter])
  const baseline = summary?.monthly_net_worth_change == null ? null : summary.net_worth - summary.monthly_net_worth_change
  const monthlyPct = baseline && baseline !== 0 && summary?.monthly_net_worth_change != null
    ? summary.monthly_net_worth_change / baseline * 100 : null

  const changeRange = (days: number) => setRange(days)
  const switchDetailFilter = (filter: DetailFilter) => setDetailFilter(filter)
  const save = async (event: FormEvent) => {
    event.preventDefault(); if (!form) return
    setSaving(true); setError(null)
    const { id, ...payload } = form
    try {
      if (id) await wealthApi.updateItem(id, payload)
      else await wealthApi.createItem(payload)
      setForm(null); refresh()
    } catch (e) { setError(e instanceof Error ? e.message : '保存失败') } finally { setSaving(false) }
  }
  const edit = (item: WealthItem) => setForm({
    id: item.id, kind: item.kind, name: item.name, item_type: item.item_type, current_value: item.current_value,
    value_as_of: item.value_as_of, included_in_net_worth: item.included_in_net_worth,
    already_investment_accounted: item.already_investment_accounted, source_type: item.source_type, notes: item.notes ?? '',
  })
  const remove = async (item: WealthItem) => {
    if (!window.confirm(`删除“${item.name}”？历史快照也会一并删除。`)) return
    try { await wealthApi.deleteItem(item.id); refresh() } catch (e) { setError(e instanceof Error ? e.message : '删除失败') }
  }
  const exportDetails = () => {
    const headers = ['类型', '名称', '分类', '原币金额', '币种', '折合人民币', '是否计入核心资产', '数据日期', '最后更新时间', '数据来源', '备注']
    const rows = filteredDetails.map(item => [
      item.kind === 'asset' ? '资产' : '负债', item.name, itemTypeLabel(item.item_type),
      item.original_value ?? item.current_value, item.currency, item.current_value,
      item.effective_included_in_net_worth ? '计入' : '仅展示', item.value_as_of, item.updated_at, item.source_type, item.notes,
    ])
    const csv = '\uFEFF' + [headers, ...rows].map(row => row.map(csvCell).join(',')).join('\r\n')
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = wealthCsvFilename()
    link.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div>
      <PageHeader icon={<WalletCards size={19} strokeWidth={2.2} color="#DBEAFE" />} title="财富总览" subtitle="个人资产负债与财富变化" />
      {error && <div style={errorStyle}>{error}</div>}
      {loading || !summary ? <Loading /> : <>
        <section aria-label="财富核心概览" style={{ display: 'grid', gridTemplateColumns: 'minmax(280px, 1.75fr) repeat(3, minmax(160px, 1fr))', gap: 12, marginBottom: 20 }}>
          <Kpi label="净资产" value={fmtCny(summary.net_worth)} primary detail={`总资产 ${fmtCny(summary.total_assets)} · 总负债 ${fmtCny(summary.total_liabilities)}`} />
          <Kpi label="总资产" value={fmtCny(summary.total_assets)} />
          <Kpi label="总负债" value={fmtCny(summary.total_liabilities)} />
          <Kpi label="本月净资产变化" value={summary.monthly_net_worth_change === null ? '—' : fmtCnySigned(summary.monthly_net_worth_change)} tone={changeTone(summary.monthly_net_worth_change)} detail={summary.monthly_net_worth_change === null ? '尚未形成月初基线' : `${signedPct(monthlyPct)} 较月初`} />
        </section>

        <section aria-label="资产与负债结构" style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.15fr) minmax(0, .85fr)', gap: 16, marginBottom: 20 }}>
          <AssetStructure summary={summary} items={assetStructure} pieData={pieData} />
          <LiabilityOverview total={summary.total_liabilities} liabilities={sortedLiabilities} />
        </section>

        <section id="wealth-details" aria-label="资产与负债明细" style={detailCard}>
          <div style={detailTitle}><span>📋 资产与负债明细</span><span style={detailCount}>{filteredDetails.length} 条</span></div>
          <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
            {([['asset', '资产'], ['liability', '负债']] as const).map(([filter, label]) => <button key={filter} type="button" onClick={() => switchDetailFilter(filter)} style={detailTabStyle(detailFilter === filter)}>{label}</button>)}
          </div>
          {filteredDetails.length ? <DetailTable items={filteredDetails} onEdit={edit} onDelete={remove} /> : <CompactEmptyState text="暂无符合条件的资产或负债。" />}
        </section>
        <section aria-label="财富数据管理" style={{ marginBottom: 16 }}>
          <div style={dataManagementBarStyle()}>
            <button type="button" onClick={() => setForm(emptyForm())} style={dataManagementEntryButton}><RefreshCw size={14} /> 更新 / 导出财富数据</button>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <button type="button" onClick={exportDetails} style={dataManagementExportButtonStyle}><Download size={11} /> 导出 CSV</button>
              <ChevronDown size={16} color="#9CA3AF" aria-hidden="true" />
            </div>
          </div>
        </section>

        <section aria-label="财富趋势" style={{ marginBottom: 20 }}>
          <div style={card()}>
            <div style={sectionHeader}><span>净资产历史趋势</span><RangeTabs range={range} onChange={changeRange} /></div>
            {summary.trend.length > 1 ? <div style={{ height: 292, marginTop: 12 }}><ResponsiveContainer width="100%" height="100%"><LineChart data={summary.trend}><XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={value => value.slice(5)} /><YAxis tick={{ fontSize: 11 }} width={76} tickFormatter={value => `${Math.round(value / 10000)}万`} /><Tooltip formatter={(value: number) => fmtCny(value)} /><Line type="monotone" dataKey="net_worth" stroke="#2563EB" strokeWidth={2.5} dot={{ r: 3 }} /></LineChart></ResponsiveContainer></div> : <EmptyTrend />}
          </div>
          <AttributionStrip summary={summary} />
        </section>
      </>}
      {form && <ItemDialog form={form} setForm={setForm} saving={saving} onSubmit={save} onClose={() => setForm(null)} />}
    </div>
  )
}

function Kpi({ label, value, primary, tone, detail }: { label: string; value: string; primary?: boolean; tone?: 'positive' | 'negative'; detail?: string }) {
  return <div style={{ ...card(primary), minHeight: primary ? 122 : 104, display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
    <div style={{ ...mutedLabel, color: primary ? 'rgba(255,255,255,.55)' : '#9CA3AF' }}>{label}</div>
    <div style={{ fontSize: primary ? 34 : 22, lineHeight: 1.15, fontWeight: 700, letterSpacing: '-.8px', color: primary ? '#fff' : tone === 'positive' ? '#059669' : tone === 'negative' ? '#DC2626' : '#1F2937', fontVariantNumeric: 'tabular-nums' }}>{value}</div>
    {detail && <div style={{ fontSize: 12, color: primary ? 'rgba(255,255,255,.68)' : tone ? tone === 'positive' ? '#059669' : '#DC2626' : '#9CA3AF' }}>{detail}</div>}
  </div>
}

function RangeTabs({ range, onChange }: { range: number; onChange: (days: number) => void }) {
  return <div style={{ display: 'flex', gap: 4 }}>{[[30, '1M'], [90, '3M'], [365, '1Y'], [0, 'ALL']].map(([days, label]) => <button key={label} onClick={() => onChange(days as number)} style={{ ...filterButton, padding: '4px 8px', background: range === days ? '#EFF6FF' : 'transparent', color: range === days ? '#2563EB' : '#6B7280', borderColor: range === days ? '#BFDBFE' : 'transparent' }}>{label}</button>)}</div>
}

function AttributionStrip({ summary }: { summary: WealthSummary }) {
  const a = summary.attribution
  if (!a.baseline_available) return <div style={attributionStyle}><strong>本月财富变化归因</strong><span>尚未形成完整月度基线，下一次资产或负债更新后开始生成变化归因。</span></div>
  const values = [[a.cash_surplus, '收支结余'], [a.investment_return, '投资收益'], [a.other_adjustment, '其他调整']]
  return <div style={{ ...attributionStyle, alignItems: 'center' }}><strong>本月财富变化归因</strong><div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>{values.map(([value, label]) => <span key={label as string} style={{ color: '#64748B' }}>{label as string} <b style={{ color: moneyTone(value as number | null), marginLeft: 4 }}>{value === null ? '—' : fmtCnySigned(Number(value))}</b></span>)}</div></div>
}

function AssetStructure({ summary, items, pieData }: { summary: WealthSummary; items: StructureItem[]; pieData: Array<{ key: string; name: string; value: number }> }) {
  const precise = (value: number) => value.toLocaleString('zh-CN', { style: 'currency', currency: 'CNY' })
  return <div style={card()}>
    <div style={sectionHeader}><span>资产结构</span></div>
    {!!summary.investment.adjustments?.length && <div style={{ marginTop: 8, fontSize: 11, color: '#64748B', lineHeight: 1.6 }}>
      投资账户总额 {precise(summary.investment.portfolio_total_assets ?? 0)}
      {summary.investment.adjustments.map(item => <span key={item.wealth_item_id}> − {item.name} {precise(item.value)}</span>)}
      {' = '}{precise(summary.investment.total_assets)}
      <div>按已登记关联金额扣除；底层持仓关联待核对。</div>
    </div>}
    <div style={{ display: 'grid', gridTemplateColumns: '150px minmax(0, 1fr)', gap: 10, alignItems: 'center', marginTop: 6 }}>
      <div style={{ height: 164 }}>{pieData.length ? <ResponsiveContainer width="100%" height="100%"><PieChart><Pie data={pieData} dataKey="value" nameKey="name" innerRadius={43} outerRadius={66} paddingAngle={2}>{pieData.map(item => <Cell key={item.name} fill={wealthAssetColorMap[item.key] ?? wealthAssetColorMap.other_assets} />)}</Pie><Tooltip formatter={(value: number) => fmtCny(value)} /></PieChart></ResponsiveContainer> : <CompactEmptyState text="暂无资产结构" />}</div>
      <div>{items.map((item, index) => <div key={item.key} style={{ padding: '7px 0', borderBottom: index < items.length - 1 ? '1px solid #F1F5F9' : 'none' }}><div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, color: '#374151', fontSize: 13 }}><span>{item.label}</span><strong style={{ fontVariantNumeric: 'tabular-nums' }}>{fmtCny(item.coreValue)}</strong></div><div style={{ marginTop: 2, color: '#9CA3AF', fontSize: 11 }}>计入核心资产 · 核心总资产占比 {summary.total_assets ? fmtPct(item.coreValue / summary.total_assets * 100) : '—'}</div></div>)}</div>
    </div>
    {summary.pension_benefit > 0 && <div style={{ marginTop: 5, padding: '8px 9px', borderRadius: 7, background: '#F8FAFC', color: '#64748B', fontSize: 11, lineHeight: 1.55 }}><div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, color: '#475569' }}><span>养老保障权益</span><strong style={{ fontVariantNumeric: 'tabular-nums' }}>{fmtCny(summary.pension_benefit)}</strong></div><div style={{ marginTop: 2 }}>企业年金、个人养老金、养老险及基本养老保险个人账户 · 不计入核心总资产</div></div>}
  </div>
}

function LiabilityOverview({ total, liabilities }: { total: number; liabilities: WealthItem[] }) {
  return <DonutDistributionCard
    title="负债结构"
    titleRight={<span style={{ color: '#9CA3AF', fontSize: 11, fontWeight: 500 }}>总负债 {fmtCny(total)}</span>}
    entries={liabilities.map(item => ({ name: item.name, value: item.current_value }))}
    emptyText="暂无负债"
    valueLabel="余额"
    palette={wealthLiabilityPalette}
    style={card()}
    titleStyle={sectionHeader}
  />
}

function DetailTable({ items, onEdit, onDelete }: { items: WealthItem[]; onEdit: (item: WealthItem) => void; onDelete: (item: WealthItem) => void }) {
  return <div style={detailTableScroll}>
    <table style={detailTable}>
      <colgroup>
        <col style={{ width: 60 }} /><col /><col style={{ width: 104 }} /><col style={{ width: 112 }} />
        <col style={{ width: 122 }} /><col style={{ width: 96 }} /><col style={{ width: 104 }} /><col style={{ width: 34 }} />
      </colgroup>
      <thead><tr>{['类型', '名称', '分类', '原币金额', '折合人民币', '核心资产状态', '更新时间', ''].map((heading, index) => <th key={`${heading}-${index}`} style={{ ...detailTh, textAlign: index >= 3 ? 'right' : 'left' }}>{heading}</th>)}</tr></thead>
      <tbody>{items.map(item => <tr key={`${item.kind}-${item.id}`} onMouseEnter={event => { event.currentTarget.style.background = '#F9FAFB' }} onMouseLeave={event => { event.currentTarget.style.background = '' }}>
        <td style={{ ...detailTd, color: '#6B7280', fontSize: 12 }}>{item.kind === 'asset' ? '资产' : '负债'}</td>
        <td style={{ ...detailTd, color: '#1B2A4A', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={item.name}>{item.name}</td>
        <td style={detailTd}><span style={tag('#F3F4F6', '#4B5563')}>{itemTypeLabel(item.item_type)}</span></td>
        <td style={{ ...detailTd, textAlign: 'right', color: item.currency === 'CNY' ? '#4B5563' : '#1F2937', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>{fmtOriginalAmount(item)}</td>
        <td style={{ ...detailTd, textAlign: 'right', fontWeight: 600, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>{fmtCny(item.current_value)}</td>
        <td style={{ ...detailTd, textAlign: 'right', fontSize: 12 }}>{item.effective_included_in_net_worth ? <span style={{ color: '#16A34A', fontWeight: 500 }}>● 计入</span> : <span style={{ color: '#9CA3AF' }}>仅展示</span>}</td>
        <td style={{ ...detailTd, textAlign: 'right', color: item.freshness === 'latest' ? '#9CA3AF' : '#D97706', fontSize: 11, whiteSpace: 'nowrap' }}>{freshnessText(item)}</td>
        <td style={{ ...detailTd, textAlign: 'right' }}><details style={{ position: 'relative', display: 'inline-block' }}><summary aria-label={`${item.name}更多操作`} style={moreButton}><Ellipsis size={17} /></summary><div style={moreMenu}><button type="button" onClick={() => onEdit(item)} style={menuButton}><Edit3 size={14} /> 编辑</button><button type="button" onClick={() => onDelete(item)} style={{ ...menuButton, color: '#DC2626' }}><Trash2 size={14} /> 删除</button></div></details></td>
      </tr>)}</tbody>
    </table>
  </div>
}

function ItemDialog({ form, setForm, saving, onSubmit, onClose }: { form: FormState; setForm: (form: FormState) => void; saving: boolean; onSubmit: (event: FormEvent) => void; onClose: () => void }) {
  const types = form.kind === 'asset' ? ASSET_TYPES : LIABILITY_TYPES
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm({ ...form, [key]: value })
  return <div style={dialogBackdrop}><form onSubmit={onSubmit} style={dialogStyle}><div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 18 }}><h2 style={{ fontSize: 18, margin: 0, color: '#1F2937' }}>{form.id ? '更新财富项目' : '更新财富数据'}</h2><button type="button" onClick={onClose} style={textButton}>关闭</button></div><div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}><Field label="类型"><select value={form.kind} onChange={event => setForm(emptyForm(event.target.value as 'asset' | 'liability'))} style={inputStyle}><option value="asset">资产</option><option value="liability">负债</option></select></Field><Field label="项目分类"><select value={form.item_type} onChange={event => set('item_type', event.target.value)} style={inputStyle}>{types.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field></div><Field label="名称"><input required value={form.name} onChange={event => set('name', event.target.value)} placeholder="例如：招商银行活期" style={inputStyle} /></Field><div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}><Field label="当前金额（元）"><input required min="0" step="0.01" type="number" value={form.current_value} onChange={event => set('current_value', Number(event.target.value))} style={inputStyle} /></Field><Field label="数据日期"><input required type="date" value={form.value_as_of ?? ''} onChange={event => set('value_as_of', event.target.value)} style={inputStyle} /></Field></div><Field label="备注（可选）"><textarea value={form.notes ?? ''} onChange={event => set('notes', event.target.value)} rows={2} style={{ ...inputStyle, resize: 'vertical' }} /></Field>{form.kind === 'asset' && <label style={checkStyle}><input type="checkbox" checked={form.already_investment_accounted ?? false} onChange={event => set('already_investment_accounted', event.target.checked)} /> 该资产已在投资账户总览中统计（仅展示，不重复计入净资产）</label>}<label style={checkStyle}><input type="checkbox" checked={form.included_in_net_worth ?? true} onChange={event => set('included_in_net_worth', event.target.checked)} disabled={form.already_investment_accounted} /> 计入净资产</label><div style={{ display: 'flex', justifyContent: 'end', gap: 8, marginTop: 20 }}><button type="button" onClick={onClose} style={secondaryButton}>取消</button><button disabled={saving} type="submit" style={primaryButton}>{saving && <Loader2 size={14} className="animate-spin" />}保存更新</button></div></form></div>
}

function Field({ label, children }: { label: string; children: ReactNode }) { return <label style={{ display: 'block', marginBottom: 13, color: '#4B5563', fontSize: 12, fontWeight: 600 }}>{label}<div style={{ marginTop: 6 }}>{children}</div></label> }
function Loading() { return <div style={{ height: 280, display: 'flex', gap: 8, alignItems: 'center', justifyContent: 'center', color: '#6B7280', fontSize: 13 }}><Loader2 size={17} className="animate-spin" />加载财富数据…</div> }
function EmptyTrend() { return <div style={{ height: 220, display: 'grid', placeItems: 'center', textAlign: 'center', color: '#6B7280', fontSize: 13, lineHeight: 1.7 }}>确认一次资产或负债更新后，即可开始积累净资产趋势。<br />未更新的项目会持续沿用最近确认值。</div> }
function CompactEmptyState({ text }: { text: string }) { return <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '18px 0 4px', color: '#9CA3AF', fontSize: 12, lineHeight: 1.6 }}><span aria-hidden="true" style={{ width: 5, height: 5, borderRadius: 999, background: '#CBD5E1', flexShrink: 0 }} />{text}</div> }

function itemTypeLabel(itemType: string) { return ({ bank_cash: '活期', time_deposit: '定期存款', housing_fund: '住房公积金', enterprise_annuity: '企业年金', personal_pension: '个人养老金', pension_insurance: '养老保险', basic_pension: '基本养老保险权益', other_asset: '其他资产', credit_card: '信用卡', consumer_loan: '信用贷', mortgage: '房贷', other_liability: '其他负债' } as Record<string, string>)[itemType] ?? itemType }
function freshnessText(item: WealthItem) { return item.age_days === 0 ? '今天更新' : `${item.age_days} 天前更新${item.freshness === 'suggested_update' ? ' · 建议更新' : item.freshness === 'long_unupdated' ? ' · 长期未更新' : ''}` }
function fmtOriginalAmount(item: WealthItem) {
  const value = item.original_value ?? item.current_value
  if (item.currency === 'CNY') return '—'
  return `${item.currency} ${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}
function changeTone(value: number | null): 'positive' | 'negative' | undefined { return value === null || value === 0 ? undefined : value > 0 ? 'positive' : 'negative' }
function moneyTone(value: number | null): string { return value === null || value === 0 ? '#6B7280' : value > 0 ? '#059669' : '#DC2626' }
function signedPct(value: number | null) { return value === null ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(1)}%` }

const sectionHeader = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, fontSize: 15, fontWeight: 700, color: '#1F2937' } as const
const mutedLabel = { fontSize: 11, fontWeight: 600, letterSpacing: '.35px', textTransform: 'uppercase' as const } as const
const errorStyle = { marginBottom: 16, padding: '10px 13px', background: '#FEF2F2', border: '1px solid #FECACA', color: '#B91C1C', borderRadius: 8, fontSize: 13 } as const
const attributionStyle = { display: 'flex', gap: 14, marginTop: 8, padding: '10px 14px', border: '1px solid #E2E8F0', borderRadius: 9, background: '#F8FAFC', color: '#64748B', fontSize: 12, lineHeight: 1.6, flexWrap: 'wrap' } as const
const textButton = { border: 'none', background: 'transparent', padding: 0, color: '#2563EB', cursor: 'pointer', fontSize: 12, fontWeight: 600 } as const
const filterButton = { border: '1px solid #E5E7EB', borderRadius: 7, padding: '5px 10px', cursor: 'pointer', fontSize: 12, fontWeight: 600 } as const
const detailCard = { background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12, padding: '20px 20px 16px', marginBottom: 16, boxShadow: 'var(--shadow-sm)' } as const
const detailTitle = { fontSize: 13, fontWeight: 600, color: '#374151', display: 'flex', alignItems: 'center', gap: 6, marginBottom: 16 } as const
const detailCount = { marginLeft: 'auto', fontSize: 11, fontWeight: 400, color: '#9CA3AF' } as const
const detailTableScroll = { maxHeight: 494, overflowY: 'auto' as const, overflowX: 'auto' as const, borderRadius: 6 } as const
const detailTable = { width: '100%', minWidth: 860, borderCollapse: 'collapse' as const, fontSize: 13, tableLayout: 'fixed' as const } as const
const detailTh = { padding: '8px 10px', fontSize: 11, fontWeight: 600, color: '#9CA3AF', textTransform: 'uppercase' as const, letterSpacing: '.4px', borderBottom: '1px solid #E5E7EB', whiteSpace: 'nowrap' as const, background: '#fff', position: 'sticky' as const, top: 0, zIndex: 1 } as const
const detailTd = { padding: '9px 10px', color: '#374151', borderBottom: '1px solid #F3F4F6', verticalAlign: 'middle' as const, whiteSpace: 'nowrap' as const } as const
const dataManagementEntryButton = { display: 'inline-flex', alignItems: 'center', gap: 6, border: 'none', padding: 0, background: 'transparent', color: 'inherit', cursor: 'pointer', font: 'inherit' } as const
const primaryButton = { border: '1px solid #1D4ED8', background: '#2563EB', color: '#fff', borderRadius: 8, padding: '8px 11px', cursor: 'pointer', fontSize: 12, fontWeight: 600, display: 'inline-flex', alignItems: 'center', gap: 6 } as const
const secondaryButton = { border: '1px solid #D1D5DB', background: '#fff', color: '#374151', borderRadius: 8, padding: '8px 11px', cursor: 'pointer', fontSize: 12, fontWeight: 600 } as const
const inputStyle = { width: '100%', boxSizing: 'border-box' as const, padding: '9px 10px', border: '1px solid #D1D5DB', borderRadius: 7, fontSize: 13, color: '#1F2937', background: '#fff' } as const
const checkStyle = { display: 'block', color: '#4B5563', fontSize: 13, marginTop: 10 } as const
const dialogBackdrop = { position: 'fixed' as const, inset: 0, zIndex: 30, background: 'rgba(17,24,39,.45)', display: 'grid', placeItems: 'center', padding: 18 } as const
const dialogStyle = { width: 'min(550px, 100%)', maxHeight: 'calc(100vh - 36px)', overflow: 'auto', background: '#fff', borderRadius: 14, padding: 22, boxShadow: '0 24px 70px rgba(0,0,0,.25)' } as const
const moreButton = { listStyle: 'none', cursor: 'pointer', color: '#64748B', padding: 4, display: 'flex', alignItems: 'center' } as const
const moreMenu = { position: 'absolute' as const, zIndex: 2, top: 28, right: 0, width: 86, padding: 4, border: '1px solid #E5E7EB', borderRadius: 8, background: '#fff', boxShadow: '0 8px 18px rgba(15,23,42,.12)' } as const
const menuButton = { width: '100%', border: 'none', background: 'transparent', padding: '7px 8px', display: 'flex', gap: 6, alignItems: 'center', color: '#374151', cursor: 'pointer', fontSize: 12, textAlign: 'left' as const } as const
function detailTabStyle(active: boolean) {
  return {
    padding: '4px 12px', borderRadius: 6, fontSize: 12, fontWeight: 500, border: 'none', cursor: 'pointer', transition: 'all 0.15s',
    background: active ? '#3B82F6' : '#F3F4F6', color: active ? '#fff' : '#6B7280',
  } as const
}
function tag(background: string, color: string) { return { display: 'inline-block', padding: '1px 7px', borderRadius: 4, background, color, fontSize: 11, fontWeight: 500, whiteSpace: 'nowrap' as const } }

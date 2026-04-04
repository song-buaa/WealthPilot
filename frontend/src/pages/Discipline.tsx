/**
 * Discipline — 投资纪律
 * 1. 状态摘要（当前杠杆 / 最大单仓 / 流动性）
 * 2. 核心规则配置（分组卡片）
 * 3. 投资纪律手册（Markdown 查阅）
 */
import React, { useState, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Loader2, AlertTriangle,
  ChevronDown, ChevronUp, Save, RefreshCw,
  Upload, Download, BookOpen, Pencil,
} from 'lucide-react'
import { disciplineApi, portfolioApi, type PortfolioSummary, type Alert } from '@/lib/api'

/** 去掉手册中的 RULES_CONFIG HTML 注释块（前端无需显示） */
function stripRulesConfig(md: string): string {
  return md.replace(/<!--\s*RULES_CONFIG[\s\S]*?-->/g, '').trim()
}

// ── 样式常量 ──────────────────────────────────────────────────
const S = {
  card: {
    background: '#fff', border: '1px solid #E5E7EB',
    borderRadius: 12, boxShadow: 'var(--shadow-sm)',
  } as React.CSSProperties,
  sectionTitle: {
    fontSize: 13, fontWeight: 600, color: '#374151',
    display: 'flex', alignItems: 'center', gap: 6,
    marginBottom: 14,
  } as React.CSSProperties,
  btnPrimary: {
    background: 'linear-gradient(135deg, #3B82F6, #1D4ED8)',
    color: '#fff', border: 'none', borderRadius: 8,
    padding: '8px 16px', fontSize: 12, fontWeight: 500,
    cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 5,
  } as React.CSSProperties,
  btnSecondary: {
    background: '#fff', color: '#374151',
    border: '1px solid #E5E7EB', borderRadius: 8,
    padding: '7px 14px', fontSize: 12, fontWeight: 500,
    cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 5,
  } as React.CSSProperties,
  label: { fontSize: 11, color: '#9CA3AF', marginBottom: 2 } as React.CSSProperties,
  value: { fontSize: 18, fontWeight: 700, color: '#1B2A4A', fontVariantNumeric: 'tabular-nums' } as React.CSSProperties,
  desc: { fontSize: 11, color: '#9CA3AF', marginTop: 2, lineHeight: 1.4 } as React.CSSProperties,
}

// ── 规则字段 & 分组定义 ────────────────────────────────────────
interface RuleField {
  group: string; key: string; label: string
  unit: '%' | 'x'; scale: number; desc: string
}
interface RuleGroup {
  id: string; title: string; emoji: string; fields: RuleField[]
}

const RULE_GROUPS: RuleGroup[] = [
  {
    id: 'leverage', title: '杠杆管理', emoji: '⚡',
    fields: [
      { group: 'leverage_limits', key: 'leverage_ratio_normal_max',     label: '正常上限', unit: 'x', scale: 1,   desc: '低于此倍数视为安全水平（绿）' },
      { group: 'leverage_limits', key: 'leverage_ratio_acceptable_max', label: '警戒线',   unit: 'x', scale: 1,   desc: '超过此倍数进入橙色警示区' },
      { group: 'leverage_limits', key: 'leverage_ratio_warning_max',    label: '危险线',   unit: 'x', scale: 1,   desc: '超过此倍数触发高风险告警（红）' },
    ],
  },
  {
    id: 'position', title: '仓位约束', emoji: '🎯',
    fields: [
      { group: 'single_asset_limits', key: 'max_position_pct',     label: '单一持仓上限', unit: '%', scale: 100, desc: '任何单一持仓不得超过此比例' },
      { group: 'single_asset_limits', key: 'warning_position_pct', label: '警戒线',       unit: '%', scale: 100, desc: '超过此比例时发出警戒提示' },
      { group: 'position_sizing',     key: 'max_single_add_pct',   label: '单次加仓上限', unit: '%', scale: 100, desc: '每次加仓占总资产上限' },
    ],
  },
  {
    id: 'liquidity', title: '流动性管理', emoji: '💧',
    fields: [
      { group: 'liquidity_limits', key: 'min_cash_pct', label: '最低流动性资产（货币+固收）比例', unit: '%', scale: 100, desc: '' },
    ],
  },
  {
    id: 'rebalancing', title: '配置偏离', emoji: '⚖️',
    fields: [
      { group: 'rebalancing_rules', key: 'deviation_warning_pct', label: '实际配置偏离目标区间阈值', unit: '%', scale: 100, desc: '' },
    ],
  },
]

function getNested(rules: Record<string, unknown>, group: string, key: string): number {
  const g = rules[group] as Record<string, unknown> | undefined
  return ((g?.[key] ?? 0) as number)
}
function setNested(rules: Record<string, unknown>, group: string, key: string, val: number): Record<string, unknown> {
  return { ...rules, [group]: { ...(rules[group] as object ?? {}), [key]: val } }
}

// ── 状态摘要计算 ──────────────────────────────────────────────

type StatusLevel = 'green' | 'yellow' | 'red' | 'gray'

const STATUS_COLORS: Record<StatusLevel, { dot: string; bg: string; text: string }> = {
  green:  { dot: '#16A34A', bg: '#F0FDF4', text: '#15803D' },
  yellow: { dot: '#D97706', bg: '#FFFBEB', text: '#B45309' },
  red:    { dot: '#DC2626', bg: '#FEF2F2', text: '#B91C1C' },
  gray:   { dot: '#9CA3AF', bg: '#F9FAFB', text: '#6B7280' },
}

/** 流动性：货币 + 固收类资产占比之和 */
const LIQUID_KEYWORDS = ['货币', '固收', '债', '现金', 'cash', 'money', 'fixed', 'bond']

function getLiquidityPct(allocation: Record<string, { value: number; pct: number }>): number {
  let total = 0
  for (const [key, val] of Object.entries(allocation)) {
    if (LIQUID_KEYWORDS.some(k => key.toLowerCase().includes(k))) {
      total += val.pct
    }
  }
  return total
}

/** 最大单仓：concentration map 中值最大的条目，去掉前缀 "N:" */
function getMaxPosition(concentration: Record<string, number>): { name: string; pct: number } | null {
  const entries = Object.entries(concentration)
  if (!entries.length) return null
  const [rawName, pct] = entries.reduce((a, b) => b[1] > a[1] ? b : a)
  const name = rawName.replace(/^\d+:/, '').trim()
  return { name, pct }
}

interface KPIStatus {
  label: string
  current: string
  threshold: string
  status: StatusLevel
  detail?: string
}

function computeKPIs(
  rules: Record<string, unknown>,
  summary: PortfolioSummary | null,
  alerts: Alert[],
): KPIStatus[] {
  const r = rules

  // ── 杠杆 ──
  const leverNormal   = getNested(r, 'leverage_limits', 'leverage_ratio_normal_max')
  const leverWarning  = getNested(r, 'leverage_limits', 'leverage_ratio_warning_max')
  // 与投资账户总览一致：total_assets / net_worth
  const currentLever  = summary ? summary.total_assets / Math.max(summary.net_worth, 1) : null
  let leverStatus: StatusLevel = 'gray'
  if (currentLever !== null) {
    if (currentLever <= leverNormal)  leverStatus = 'green'
    else if (currentLever <= leverWarning) leverStatus = 'yellow'
    else leverStatus = 'red'
  }

  // ── 最大单仓 ──
  // concentration 值为百分比整数（如 26.5 表示 26.5%），规则值为小数（0.4 = 40%）
  const maxPosPct   = getNested(r, 'single_asset_limits', 'max_position_pct')   // 0-1
  const warnPosPct  = getNested(r, 'single_asset_limits', 'warning_position_pct') // 0-1
  const maxPos      = summary ? getMaxPosition(summary.concentration) : null
  let posStatus: StatusLevel = 'gray'
  if (maxPos !== null) {
    const maxPosFrac = maxPos.pct / 100
    if (maxPosFrac <= warnPosPct)     posStatus = 'green'
    else if (maxPosFrac <= maxPosPct) posStatus = 'yellow'
    else posStatus = 'red'
  }

  // ── 流动性 ──
  // allocation.pct 值为百分比整数（如 36.3 表示 36.3%），规则值为小数（0.2 = 20%）
  const minCash     = getNested(r, 'liquidity_limits', 'min_cash_pct')  // 0-1
  const liquidPct   = summary ? getLiquidityPct(summary.allocation) : null  // 百分比整数
  let liqStatus: StatusLevel = 'gray'
  if (liquidPct !== null) {
    const liquidFrac = liquidPct / 100
    if (liquidFrac >= minCash * 1.2) liqStatus = 'green'
    else if (liquidFrac >= minCash)  liqStatus = 'yellow'
    else liqStatus = 'red'
  }

  // ── 最大配置偏离 ──
  // 从 alerts 中找配置偏离告警，取最大 deviation 值（fraction，如 0.12 = 12%）
  const deviationWarn = getNested(r, 'rebalancing_rules', 'deviation_warning_pct')  // 0-1
  const devAlert = alerts
    .filter(a => a.deviation !== undefined && (
      a.alert_type.toLowerCase().includes('deviation') ||
      a.alert_type.toLowerCase().includes('allocation') ||
      a.alert_type.toLowerCase().includes('rebalanc')
    ))
    .sort((a, b) => (b.deviation ?? 0) - (a.deviation ?? 0))[0]
  // 有组合数据时：无偏离告警 → 0% 绿色；有告警 → 取最大值；无组合数据 → 灰色 —
  const maxDev: number | null = summary !== null ? (devAlert?.deviation ?? 0) : null
  let devStatus: StatusLevel = 'gray'
  if (maxDev !== null) {
    if (maxDev <= deviationWarn * 0.6)   devStatus = 'green'
    else if (maxDev <= deviationWarn)    devStatus = 'yellow'
    else devStatus = 'red'
  }

  return [
    {
      label: '当前杠杆',
      current:   currentLever !== null ? `${currentLever.toFixed(2)}x` : '—',
      threshold: `上限 ${leverNormal.toFixed(2)}x`,
      status:    leverStatus,
    },
    {
      label: '最大单仓',
      current:   maxPos ? `${maxPos.pct.toFixed(1)}%` : '—',
      threshold: `上限 ${(maxPosPct * 100).toFixed(0)}%`,
      status:    posStatus,
      detail:    maxPos?.name,
    },
    {
      label: '流动性资产占比',
      current:   liquidPct !== null ? `${liquidPct.toFixed(1)}%` : '—',
      threshold: `最低 ${(minCash * 100).toFixed(0)}%`,
      status:    liqStatus,
    },
    {
      label: '最大配置偏离',
      current:   maxDev !== null ? `${(maxDev * 100).toFixed(1)}%` : '—',
      threshold: `预警 ${(deviationWarn * 100).toFixed(0)}%`,
      status:    devStatus,
    },
  ]
}

// ── 主组件 ────────────────────────────────────────────────────
export default function Discipline() {
  const [rules,    setRules]    = useState<Record<string, unknown> | null>(null)
  const [handbook, setHandbook] = useState<{ source: string; content: string } | null>(null)
  const [summary,  setSummary]  = useState<PortfolioSummary | null>(null)
  const [alerts,   setAlerts]   = useState<Alert[]>([])
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState<string | null>(null)

  // 规则编辑
  const [editMode, setEditMode] = useState(false)
  const [draft,    setDraft]    = useState<Record<string, unknown>>({})
  const [saving,   setSaving]   = useState(false)
  const [saveMsg,  setSaveMsg]  = useState<string | null>(null)

  // 手册
  const [handbookOpen, setHandbookOpen] = useState(true)
  const [uploading,    setUploading]    = useState(false)
  const handbookFileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    setLoading(true)
    Promise.all([
      disciplineApi.getRules(),
      disciplineApi.getHandbook(),
      portfolioApi.getSummary().catch(() => null),
      portfolioApi.getAlerts().catch(() => ({ items: [], count: 0 })),
    ])
      .then(([r, h, s, al]) => {
        setRules(r)
        setDraft(r as Record<string, unknown>)
        setHandbook(h)
        setSummary(s)
        setAlerts(al?.items ?? [])
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : '加载失败'))
      .finally(() => setLoading(false))
  }, [])

  async function handleSaveRules() {
    setSaving(true); setSaveMsg(null)
    try {
      const updated = await disciplineApi.updateRules(draft)
      setRules(updated as Record<string, unknown>)
      setDraft(updated as Record<string, unknown>)
      setEditMode(false)
      setSaveMsg('已保存')
      setTimeout(() => setSaveMsg(null), 2000)
    } catch (e: unknown) {
      setSaveMsg(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  async function handleResetRules() {
    if (!confirm('确定恢复默认规则？当前修改将丢失。')) return
    setSaving(true)
    try {
      const updated = await disciplineApi.resetRules()
      setRules(updated as Record<string, unknown>)
      setDraft(updated as Record<string, unknown>)
      setEditMode(false)
      setSaveMsg('已重置')
      setTimeout(() => setSaveMsg(null), 2000)
    } catch (e: unknown) {
      setSaveMsg(e instanceof Error ? e.message : '重置失败')
    } finally {
      setSaving(false)
    }
  }

  async function handleHandbookUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      const result = await disciplineApi.uploadHandbook(file)
      setHandbook(result)
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : '上传失败')
    } finally {
      setUploading(false)
      if (handbookFileRef.current) handbookFileRef.current.value = ''
    }
  }

  async function handleResetHandbook() {
    if (!confirm('确定恢复官方手册？自定义内容将丢失。')) return
    try {
      const result = await disciplineApi.resetHandbook()
      setHandbook(result)
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : '重置失败')
    }
  }

  // ── 加载 / 错误状态 ──
  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 300, gap: 8, color: '#9CA3AF' }}>
        <Loader2 size={18} className="animate-spin" /><span style={{ fontSize: 13 }}>加载中…</span>
      </div>
    )
  }
  if (error) {
    return (
      <div style={{ background: '#FEE2E2', border: '1px solid #FECACA', borderRadius: 10, padding: '12px 16px', color: '#7F1D1D', fontSize: 13, display: 'flex', gap: 8 }}>
        <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: 1 }} />{error}
      </div>
    )
  }

  const currentRules = editMode ? draft : (rules ?? {})
  const kpis = computeKPIs(currentRules, summary, alerts)

  return (
    <div>
      {/* ── 页面标题 ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <div style={{
          width: 38, height: 38, borderRadius: 10,
          background: 'linear-gradient(135deg, #1B2A4A, #2D4A7A)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 17,
        }}>📋</div>
        <div>
          <div style={{ fontSize: 20, fontWeight: 700, color: '#1B2A4A', letterSpacing: -0.3 }}>投资纪律</div>
          <div style={{ fontSize: 12, color: '#9CA3AF', marginTop: 1 }}>规则管理 · 纪律手册</div>
        </div>
      </div>

      {/* ── 状态摘要 ── */}
      <StatusSummary kpis={kpis} />

      {/* ── Section 1: 核心规则 ── */}
      <div style={{ ...S.card, padding: '20px 24px', marginBottom: 16 }}>
        {/* 标题行 */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <div style={{ ...S.sectionTitle, marginBottom: 0 }}>
            🛡️ 核心规则配置
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {saveMsg && (
              <span style={{ fontSize: 11, color: saveMsg.includes('失败') ? '#DC2626' : '#059669', fontWeight: 500 }}>
                {saveMsg}
              </span>
            )}
            {editMode ? (
              <>
                <button style={S.btnSecondary} onClick={() => { setEditMode(false); setDraft(rules ?? {}) }}>
                  取消
                </button>
                <button style={S.btnSecondary} disabled={saving} onClick={handleResetRules}>
                  <RefreshCw size={12} /> 重置默认
                </button>
                <button style={S.btnPrimary} disabled={saving} onClick={handleSaveRules}>
                  {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
                  保存
                </button>
              </>
            ) : (
              <button style={S.btnSecondary} onClick={() => setEditMode(true)}>
                <Pencil size={12} /> 编辑
              </button>
            )}
          </div>
        </div>

        {/* 规则分组网格 */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          {RULE_GROUPS.map(group => (
            <RuleGroupCard
              key={group.id}
              group={group}
              rules={currentRules}
              editMode={editMode}
              onFieldChange={(g, k, v) => setDraft(prev => setNested(prev, g, k, v))}
            />
          ))}
        </div>
      </div>

      {/* ── Section 2: 投资纪律手册 ── */}
      <div style={{ ...S.card, overflow: 'hidden' }}>
        {/* 折叠标题行 */}
        <div
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '16px 24px', cursor: 'pointer',
            borderBottom: handbookOpen ? '1px solid #E5E7EB' : undefined,
          }}
          onClick={() => setHandbookOpen(v => !v)}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <BookOpen size={15} style={{ color: '#3B82F6' }} />
            <span style={{ fontSize: 13, fontWeight: 600, color: '#374151' }}>投资纪律手册</span>
            {handbook && (
              <span style={{
                fontSize: 10, fontWeight: 500, padding: '2px 7px', borderRadius: 10,
                background: handbook.source === 'custom' ? '#EFF6FF' : '#F0FDF4',
                color: handbook.source === 'custom' ? '#3B82F6' : '#16A34A',
              }}>
                {handbook.source === 'custom' ? '自定义版' : '官方版'}
              </span>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }} onClick={e => e.stopPropagation()}>
            <input ref={handbookFileRef} type="file" accept=".md,.txt" style={{ display: 'none' }} onChange={handleHandbookUpload} />
            {/* 下载当前手册 */}
            {handbook && (
              <button
                style={{ ...S.btnSecondary, fontSize: 11, padding: '5px 10px', whiteSpace: 'nowrap' }}
                onClick={() => {
                  const firstLine = handbook.content.trim().split('\n')[0] ?? ''
                  const versionTag = firstLine.replace(/^#+\s*/, '').trim().replace(/\s+/g, '_') || 'investment_discipline_handbook'
                  const blob = new Blob([handbook.content], { type: 'text/markdown' })
                  const url = URL.createObjectURL(blob)
                  const a = document.createElement('a')
                  a.href = url
                  a.download = `${versionTag}.md`
                  a.click()
                  URL.revokeObjectURL(url)
                }}
              >
                <Download size={11} /> 下载
              </button>
            )}
            {/* 上传自定义手册 */}
            <button
              style={{ ...S.btnSecondary, fontSize: 11, padding: '5px 10px', whiteSpace: 'nowrap' }}
              disabled={uploading}
              onClick={() => handbookFileRef.current?.click()}
            >
              {uploading ? <Loader2 size={11} className="animate-spin" /> : <Upload size={11} />}
              上传
            </button>
            {/* 恢复官方版（仅自定义版时显示） */}
            {handbook?.source === 'custom' && (
              <button style={{ ...S.btnSecondary, fontSize: 11, padding: '5px 10px', whiteSpace: 'nowrap' }} onClick={handleResetHandbook}>
                <RefreshCw size={11} /> 恢复官方
              </button>
            )}
            {handbookOpen ? <ChevronUp size={15} color="#9CA3AF" /> : <ChevronDown size={15} color="#9CA3AF" />}
          </div>
        </div>

        {/* 手册内容 */}
        {handbookOpen && handbook && (
          <div style={{ padding: '16px 24px 20px' }}>
            <HandbookContent content={stripRulesConfig(handbook.content)} />
          </div>
        )}
      </div>
    </div>
  )
}

// ── 状态摘要组件 ──────────────────────────────────────────────

function StatusSummary({ kpis }: { kpis: KPIStatus[] }) {
  return (
    <div style={{ ...S.card, padding: '16px 20px', marginBottom: 16 }}>
      {/* 区块标题 */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ ...S.sectionTitle, marginBottom: 0 }}>
          🎯 账户风险仪表盘
        </div>
      </div>

      {/* 4-KPI 横排 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
        {kpis.map(kpi => {
          const sc = STATUS_COLORS[kpi.status]
          return (
            <div key={kpi.label} style={{
              background: sc.bg, borderRadius: 9,
              padding: '10px 12px', border: `1px solid ${sc.dot}22`,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 5 }}>
                <span style={{
                  width: 7, height: 7, borderRadius: '50%',
                  background: sc.dot, flexShrink: 0,
                  boxShadow: kpi.status !== 'gray' ? `0 0 0 2px ${sc.dot}22` : undefined,
                }} />
                <span style={{ fontSize: 11, color: '#6B7280', fontWeight: 500 }}>{kpi.label}</span>
              </div>
              <div style={{ fontSize: 20, fontWeight: 700, color: sc.text, fontVariantNumeric: 'tabular-nums', lineHeight: 1.2 }}>
                {kpi.current}
              </div>
              <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {kpi.detail ? `${kpi.detail} | ${kpi.threshold}` : kpi.threshold}
              </div>
            </div>
          )
        })}
      </div>

    </div>
  )
}

// ── 规则分组卡片 ─────────────────────────────────────────────

function RuleGroupCard({
  group, rules, editMode, onFieldChange,
}: {
  group: RuleGroup
  rules: Record<string, unknown>
  editMode: boolean
  onFieldChange: (group: string, key: string, val: number) => void
}) {
  const horizontal = group.fields.length >= 3

  return (
    <div style={{ background: '#F8FAFC', border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 14px' }}>
      {/* 组标题 */}
      <div style={{ fontSize: 12, fontWeight: 600, color: '#374151', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 5 }}>
        <span>{group.emoji}</span> {group.title}
      </div>
      {/* 字段区域：3个字段横向3列，其余竖排 */}
      <div style={horizontal
        ? { display: 'grid', gridTemplateColumns: `repeat(${group.fields.length}, 1fr)`, gap: 0 }
        : { display: 'flex', flexDirection: 'column', gap: 8 }
      }>
        {group.fields.map((f, i) => {
          const raw = getNested(rules, f.group, f.key)
          const displayed = raw * f.scale
          return (
            <div key={`${f.group}.${f.key}`} style={horizontal ? {
              paddingLeft: i > 0 ? 12 : 0,
              borderLeft: i > 0 ? '1px solid #E5E7EB' : undefined,
            } : {}}>
              <div style={S.label}>{f.label}</div>
              {editMode ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: 3, marginTop: 2 }}>
                  <input
                    type="number"
                    step={f.unit === 'x' ? '0.01' : '1'}
                    value={parseFloat(displayed.toFixed(f.unit === 'x' ? 2 : 0))}
                    onChange={e => {
                      const v = parseFloat(e.target.value)
                      if (!isNaN(v)) onFieldChange(f.group, f.key, v / f.scale)
                    }}
                    style={{
                      width: 56, border: '1px solid #3B82F6', borderRadius: 6,
                      padding: '3px 5px', fontSize: 13, fontWeight: 600,
                      color: '#1B2A4A', outline: 'none', background: '#fff',
                    }}
                  />
                  <span style={{ fontSize: 11, color: '#6B7280' }}>{f.unit}</span>
                </div>
              ) : (
                <div style={{ ...S.value, fontSize: 15, marginTop: 2 }}>
                  {f.unit === 'x' ? `${displayed.toFixed(2)}x` : `${displayed.toFixed(0)}%`}
                </div>
              )}
              {f.desc && <div style={S.desc}>{f.desc}</div>}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── 手册：解析 + 折叠面板 ─────────────────────────────────────

interface HandbookSection {
  title: string
  body: string
}

function parseHandbook(md: string): { preamble: string; sections: HandbookSection[] } {
  const lines = md.split('\n')
  const sections: HandbookSection[] = []
  let preambleLines: string[] = []
  let current: HandbookSection | null = null

  for (const line of lines) {
    if (line.startsWith('### ')) {
      if (current) sections.push(current)
      current = { title: line.replace(/^###\s*/, '').trim(), body: '' }
    } else if (current) {
      current.body += line + '\n'
    } else {
      preambleLines.push(line)
    }
  }
  if (current) sections.push(current)
  return { preamble: preambleLines.join('\n').trim(), sections }
}

function extractType(title: string): { clean: string; badge: React.ReactNode } {
  if (title.includes('🔴 HARD') || title.includes('HARD')) {
    const clean = title.replace(/🔴\s*HARD/g, '').trim()
    return { clean, badge: <span style={{ fontSize: 9, fontWeight: 700, padding: '2px 6px', borderRadius: 8, background: '#FEE2E2', color: '#DC2626', marginLeft: 6, verticalAlign: 'middle' }}>HARD</span> }
  }
  if (title.includes('🔵 SOFT') || title.includes('SOFT')) {
    const clean = title.replace(/🔵\s*SOFT/g, '').trim()
    return { clean, badge: <span style={{ fontSize: 9, fontWeight: 700, padding: '2px 6px', borderRadius: 8, background: '#EFF6FF', color: '#3B82F6', marginLeft: 6, verticalAlign: 'middle' }}>SOFT</span> }
  }
  return { clean: title, badge: null }
}

function HandbookContent({ content }: { content: string }) {
  const { preamble: rawPreamble, sections } = parseHandbook(content)
  // 去掉前言中的 h1 标题行（版本号不对客展示）
  const preamble = rawPreamble.replace(/^#\s[^\n]*\n?/, '').trim()
  const [openIdx, setOpenIdx] = useState<number | null>(null)

  return (
    <div>
      {preamble && (
        <div className="handbook-md" style={{ marginBottom: 16, paddingBottom: 14, borderBottom: '1px solid #F3F4F6' }}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{preamble}</ReactMarkdown>
        </div>
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {sections.map((sec, i) => {
          const isOpen = openIdx === i
          const { clean, badge } = extractType(sec.title)
          return (
            <div key={i} style={{ border: '1px solid #E5E7EB', borderRadius: 8, overflow: 'hidden' }}>
              <div
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '10px 14px', cursor: 'pointer',
                  background: isOpen ? '#F0F7FF' : '#FAFAFA',
                  transition: 'background 0.1s',
                  borderBottom: isOpen ? '1px solid #E5E7EB' : undefined,
                }}
                onClick={() => setOpenIdx(isOpen ? null : i)}
              >
                <div style={{ fontSize: 13, fontWeight: 600, color: '#1B2A4A', lineHeight: 1.4 }}>
                  {clean}{badge}
                </div>
                {isOpen
                  ? <ChevronUp size={14} color="#9CA3AF" style={{ flexShrink: 0 }} />
                  : <ChevronDown size={14} color="#9CA3AF" style={{ flexShrink: 0 }} />}
              </div>
              {isOpen && (
                <div style={{ padding: '14px 16px', background: '#fff' }}>
                  <div className="handbook-md">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{sec.body.trim()}</ReactMarkdown>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

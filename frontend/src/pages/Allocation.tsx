/**
 * Allocation — 资产配置首页
 * 路由: /allocation
 *
 * 三个区块：
 * 1. 配置健康状态标签（一行）
 * 2. 左右双列：大类资产配置卡片 + 配置原则说明
 * 3. 两个并排按钮
 *
 * 数据统一来源：portfolioApi.getSummary()，与 Dashboard 同一口径。
 */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2, AlertTriangle, Scale } from 'lucide-react'
import { portfolioApi, type PortfolioSummary } from '@/lib/api'
import { allocationApi } from '@/lib/allocation-api'
import AssetAllocationCard, { ALLOC_CATS } from '@/components/allocation/AssetAllocationCard'
import DataTip from '@/components/shared/DataTip'
import EmptyStateGuide from '@/components/allocation/EmptyStateGuide'

// ── 健康状态样式 ────────────────────────────────────────────────

type HealthLevel = 'on_target' | 'mild' | 'significant' | 'alert'

const HEALTH_STYLE: Record<HealthLevel, { label: string; color: string; bg: string }> = {
  on_target:    { label: '接近目标', color: '#16A34A', bg: '#DCFCE7' },
  mild:         { label: '轻微偏离', color: '#D97706', bg: '#FEF3C7' },
  significant:  { label: '明显偏离', color: '#EA580C', bg: '#FFF7ED' },
  alert:        { label: '需要关注', color: '#DC2626', bg: '#FEE2E2' },
}

// ── 主组件 ──────────────────────────────────────────────────────

export default function Allocation() {
  const [summary, setSummary] = useState<PortfolioSummary | null>(null)
  const [cashRange, setCashRange] = useState<{ min: number; max: number } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchData = () => {
    setLoading(true)
    setError(null)
    Promise.all([
      portfolioApi.getSummary(),
      allocationApi.getTargets(),
    ])
      .then(([s, targets]) => {
        setSummary(s)
        const cashTarget = targets.find(t => t.asset_class === 'cash')
        if (cashTarget?.cash_min_amount != null && cashTarget?.cash_max_amount != null) {
          setCashRange({ min: cashTarget.cash_min_amount, max: cashTarget.cash_max_amount })
        }
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(fetchData, [])

  // 加载中
  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 300, gap: 8, color: '#9CA3AF' }}>
        <Loader2 size={18} className="animate-spin" />
        <span style={{ fontSize: 13 }}>加载中…</span>
      </div>
    )
  }

  // 错误
  if (error) {
    return (
      <div style={{ background: '#FEE2E2', border: '1px solid #FECACA', borderRadius: 10, padding: '12px 16px', color: '#7F1D1D', fontSize: 13, display: 'flex', gap: 8 }}>
        <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: 1 }} />
        <div>
          <div style={{ fontWeight: 600 }}>加载失败</div>
          <div style={{ fontSize: 12, marginTop: 2 }}>{error}</div>
          <button onClick={fetchData} style={{ marginTop: 8, padding: '5px 12px', background: '#fff', border: '1px solid #FECACA', borderRadius: 6, fontSize: 11, cursor: 'pointer', color: '#7F1D1D' }}>重试</button>
        </div>
      </div>
    )
  }

  // 空状态
  if (!summary || summary.total_assets === 0) {
    return (
      <>
        <PageHeader />
        <EmptyStateGuide />
      </>
    )
  }

  // 基于 summary.allocation + ALLOC_CATS 计算健康状态（与卡片同一数据源）
  const health = calcHealthFromAllocation(summary.allocation)

  return (
    <>
      <DataTip />
      <PageHeader />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {/* ── 区块一：配置健康状态 ── */}
        <HealthStatusBar health={health} />

        {/* ── 区块二：左右双列 ── */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <AssetAllocationCard allocation={summary.allocation} cashRange={cashRange ?? undefined} />
          <PrinciplesCard />
        </div>

        {/* ── 区块三：快速行动入口 ── */}
        <QuickActions />
      </div>
    </>
  )
}

// ── 页面标题 ────────────────────────────────────────────────────

function PageHeader() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
      <div style={{
        width: 38, height: 38, borderRadius: 10,
        background: 'linear-gradient(135deg, #1B2A4A, #2D4A7A)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 17,
      }}>⚖️</div>
      <div>
        <div style={{ fontSize: 20, fontWeight: 700, color: '#1B2A4A', letterSpacing: -0.3 }}>资产配置</div>
        <div style={{ fontSize: 12, color: '#9CA3AF', marginTop: 1 }}>配置管理 · 动态再平衡</div>
      </div>
    </div>
  )
}

// ── 区块一：配置诊断结论 ────────────────────────────────────────

interface HealthInfo {
  level: HealthLevel
  maxDevLabel: string | null
  maxDevText: string | null
  actionHint: string
}

function HealthStatusBar({ health }: { health: HealthInfo }) {
  const s = HEALTH_STYLE[health.level]

  let message: string
  if (health.level === 'on_target') {
    message = '✅ 当前大类资产配置均在目标区间内，无需主动调整。如有新增资金，可点击下方开始规划。'
  } else if (health.level === 'mild') {
    message = `📊 配置整体正常，${health.maxDevLabel}略有偏离，建议后续用新增资金自然修正。`
  } else {
    message = `⚠️ ${health.maxDevLabel}偏离目标区间较大，建议尽快关注，可点击下方开始规划。`
  }

  return (
    <div style={{
      padding: '12px 16px',
      background: s.bg,
      borderRadius: 10,
      border: `1px solid ${s.color}30`,
      fontSize: 13, color: '#374151', lineHeight: 1.6,
    }}>
      {message}
    </div>
  )
}

// ── 基于 allocation + ALLOC_CATS 计算健康状态 ───────────────────

function calcHealthFromAllocation(
  allocation: PortfolioSummary['allocation']
): HealthInfo {
  let worstLevel: HealthLevel = 'on_target'
  let maxAbsDev = 0
  let maxDevLabel: string | null = null
  let maxDevText: string | null = null

  for (const cat of ALLOC_CATS) {
    const cur = allocation[cat.key]?.pct ?? 0
    const { minPct: min, maxPct: max, label } = cat

    let devText: string | null = null
    let level: HealthLevel = 'on_target'
    let absDev = 0

    if (cur > max) {
      absDev = cur - max
      devText = `超配 +${absDev.toFixed(1)}%`
      level = absDev > 5 ? 'alert' : absDev > 2 ? 'significant' : 'mild'
    } else if (min > 0 && cur < min) {
      absDev = min - cur
      devText = `低配 −${absDev.toFixed(1)}%`
      level = absDev > 5 ? 'alert' : absDev > 2 ? 'significant' : 'mild'
    }

    // 更新最严重级别
    const severity = { on_target: 0, mild: 1, significant: 2, alert: 3 }
    if (severity[level] > severity[worstLevel]) worstLevel = level

    // 更新最大偏离项
    if (absDev > maxAbsDev) {
      maxAbsDev = absDev
      maxDevLabel = label
      maxDevText = devText
    }
  }

  const actionHints: Record<HealthLevel, string> = {
    on_target: '暂不处理',
    mild: '建议后续用新增资金修正',
    significant: '建议后续用新增资金修正',
    alert: '需要尽快关注',
  }

  return {
    level: worstLevel,
    maxDevLabel,
    maxDevText,
    actionHint: actionHints[worstLevel],
  }
}

// ── 右列：配置原则说明卡片 ──────────────────────────────────────

function PrinciplesCard() {
  const nav = useNavigate()

  const handleLearnMore = () => {
    sessionStorage.setItem('allocation_prefill', '请解释 WealthPilot 的资产配置逻辑：多元资产配置、目标区间管理和动态再平衡分别是什么意思？')
    nav('/allocation/chat')
  }

  return (
    <div style={{
      background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12,
      padding: '16px 20px', boxShadow: 'var(--shadow-sm)',
      display: 'flex', flexDirection: 'column',
      height: '100%',
    }}>
      {/* 标题行 + 了解更多 */}
      <div style={{ fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
        <Scale size={14} style={{ color: '#3B82F6' }} />
        配置原则说明
        <span
          onClick={handleLearnMore}
          style={{ marginLeft: 'auto', fontSize: 11, fontWeight: 400, color: '#9CA3AF', cursor: 'pointer' }}
          onMouseEnter={e => (e.currentTarget.style.color = '#3B82F6')}
          onMouseLeave={e => (e.currentTarget.style.color = '#9CA3AF')}
        >
          了解更多 &gt;
        </span>
      </div>

      <div style={{ fontSize: 12, color: '#6B7280', lineHeight: 1.7, flex: 1, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ background: '#EFF6FF', borderRadius: 6, padding: '8px 12px' }}>
          <div style={{ fontWeight: 600, color: '#1B2A4A', marginBottom: 2 }}>多元资产配置</div>
          货币保流动性，固收稳底盘，权益求增长，另类分散风险，衍生作战术工具，不同资产解决不同问题。
        </div>
        <div style={{ background: '#F0FDF4', borderRadius: 6, padding: '8px 12px' }}>
          <div style={{ fontWeight: 600, color: '#1B2A4A', marginBottom: 2 }}>目标区间管理</div>
          每类资产都有自己的目标区间，资产配置的重点不是判断短期涨跌，而是让整体结构长期保持在合理范围内。
        </div>
        <div style={{ background: '#FFF7ED', borderRadius: 6, padding: '8px 12px' }}>
          <div style={{ fontWeight: 600, color: '#1B2A4A', marginBottom: 2 }}>动态再平衡</div>
          当配置出现偏离时，优先通过新增资金自然修正，减少不必要的卖出操作，只有偏离明显时才考虑主动调整。
        </div>
      </div>

      {/* 底部小字 — 右对齐 */}
      <div style={{ fontSize: 11, color: '#C4C9D4', marginTop: 8, textAlign: 'right' }}>
        所有资产配置建议均会经过投资纪律校验。
      </div>
    </div>
  )
}

// ── 区块三：快速行动入口 ────────────────────────────────────────

function QuickActions() {
  const nav = useNavigate()
  return (
    <div style={{ display: 'flex', justifyContent: 'center' }}>
      <button
        onClick={() => nav('/allocation/chat')}
        style={{
          padding: '9px 28px',
          background: 'linear-gradient(135deg, #3B82F6, #1D4ED8)',
          color: '#fff', border: 'none', borderRadius: 8,
          fontSize: 13, fontWeight: 500, cursor: 'pointer',
          display: 'inline-flex', alignItems: 'center', gap: 5,
        }}
      >
        开始配置规划 →
      </button>
    </div>
  )
}

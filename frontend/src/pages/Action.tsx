/**
 * Action — 投资行动页面（M7.2.5 信息架构重构）
 * 2 Tab：行动清单 / 行动记录
 * 行动清单 = 待确认草稿 + 按意图分组的策略 + 散户策略 + 已挂单
 */
import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2, AlertTriangle, CheckCircle, Trash2, ExternalLink, FileText, Edit3 } from 'lucide-react'
import * as Dialog from '@radix-ui/react-dialog'
import { actionApi, type ActionDraftResponse, type AllocationIntentResponse, type SymbolStrategyResponse, type OrderResponse, type TimelineEvent } from '@/lib/api'
import { allocationApi, type DeviationSnapshot, type ClassDeviation, ALLOC_LABEL } from '@/lib/allocation-api'
import ActionDraftCard from '@/components/ActionDraftCard'
import ConfirmOrderDialog from '@/components/ConfirmOrderDialog'
import { useToast } from '@/components/Toast'
import PageHeader from '@/components/shared/PageHeader'

type TabKey = 'action' | 'history'

export default function Action() {
  const searchParams = new URLSearchParams(window.location.hash.split('?')[1] || '')
  const urlTab = searchParams.get('tab') as string | null
  // 兼容旧 URL: tab=strategies/allocation → action
  const resolvedTab: TabKey = (urlTab === 'history') ? 'history' : 'action'
  const urlParentIntentId = searchParams.get('parent_intent_id')

  const [activeTab, setActiveTab] = useState<TabKey>(resolvedTab)

  // 草稿
  const [drafts, setDrafts] = useState<ActionDraftResponse[]>([])
  const [draftCardOpen, setDraftCardOpen] = useState(false)
  const [currentDraft, setCurrentDraft] = useState<ActionDraftResponse | null>(null)

  const fetchDrafts = useCallback(async () => {
    try {
      const res = await actionApi.listDrafts('draft')
      setDrafts(res.items)
    } catch { /* ignore */ }
  }, [])

  useEffect(() => { fetchDrafts() }, [fetchDrafts])

  function handleEditDraft(draft: ActionDraftResponse) {
    setCurrentDraft(draft)
    setDraftCardOpen(true)
  }
  async function handleDiscardDraft(id: string) {
    try {
      await actionApi.discardDraft(id)
      setDrafts(prev => prev.filter(d => d.id !== id))
    } catch (e: unknown) { alert(`丢弃失败: ${(e as Error).message}`) }
  }
  function handleDraftConfirmed() {
    setDraftCardOpen(false)
    setCurrentDraft(null)
    fetchDrafts()
  }

  return (
    <div>
      {/* 页面头部 — 与 Discipline / Dashboard 完全一致的结构 */}
      <PageHeader icon="🎯" title="投资行动" subtitle="决策落地 · 全流程追溯" />

      {/* Tab 切换器 */}
      <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid #E5E7EB', marginBottom: 20 }}>
        {([
          { key: 'action' as TabKey, label: '行动清单', badge: drafts.length > 0 ? drafts.length : undefined, badgeRed: true },
          { key: 'history' as TabKey, label: '行动记录' },
        ]).map(tab => {
          const isActive = activeTab === tab.key
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              style={{
                padding: '8px 20px', fontSize: 13, fontWeight: isActive ? 600 : 500,
                border: 'none', cursor: 'pointer', transition: 'all 0.15s',
                borderBottom: isActive ? '2px solid #3B82F6' : '2px solid transparent',
                background: 'transparent', marginBottom: -1,
                color: isActive ? '#3B82F6' : '#6B7280',
                display: 'flex', alignItems: 'center', gap: 6,
              }}
            >
              {tab.label}
              {'badge' in tab && tab.badge !== undefined && tab.badge > 0 && (
                <span style={{
                  fontSize: 11, fontWeight: 600, minWidth: 16, textAlign: 'center',
                  padding: '0 5px', borderRadius: 8, lineHeight: '16px',
                  background: tab.badgeRed ? '#DC2626' : '#E5E7EB',
                  color: tab.badgeRed ? '#fff' : '#6B7280',
                }}>{tab.badge}</span>
              )}
            </button>
          )
        })}
      </div>

      {/* Tab 内容 — 无额外 padding，继承 AppLayout 的 28px 64px */}
      <div>
        {activeTab === 'action' && (
          <ActionListTab
            drafts={drafts}
            onEditDraft={handleEditDraft}
            onDiscardDraft={handleDiscardDraft}
            initialParentIntentId={urlParentIntentId}
          />
        )}
        {activeTab === 'history' && <TimelineTab />}
      </div>

      {/* ActionDraftCard 弹层 */}
      <ActionDraftCard
        open={draftCardOpen}
        onClose={() => { setDraftCardOpen(false); setCurrentDraft(null) }}
        draft={currentDraft}
        onConfirmed={handleDraftConfirmed}
      />
    </div>
  )
}


// ═══════════════════════════════════════════════════════════════════
// 行动清单 Tab（核心重构）
// ═══════════════════════════════════════════════════════════════════

function ActionListTab({
  drafts, onEditDraft, onDiscardDraft, initialParentIntentId,
}: {
  drafts: ActionDraftResponse[]
  onEditDraft: (d: ActionDraftResponse) => void
  onDiscardDraft: (id: string) => void
  initialParentIntentId: string | null
}) {
  const navigate = useNavigate()
  const [intents, setIntents] = useState<AllocationIntentResponse[]>([])
  const [strategies, setStrategies] = useState<SymbolStrategyResponse[]>([])
  const [orders, setOrders] = useState<OrderResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [placeOrderStrategy, setPlaceOrderStrategy] = useState<SymbolStrategyResponse | null>(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const [intentRes, stratRes, orderRes] = await Promise.all([
        actionApi.listIntents('active'),
        actionApi.listStrategies({ status: 'active' }),
        actionApi.listOrders({}),  // v3.4 M5: 显示所有订单,不仅 broker_pending
      ])
      setIntents(intentRes.items)
      setStrategies(stratRes.items)
      setOrders(orderRes.items)
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { fetchData() }, [fetchData])

  // Group strategies by parent_intent_id
  const grouped = new Map<string, SymbolStrategyResponse[]>()
  const orphanStrategies: SymbolStrategyResponse[] = []
  // v3.11: Group by plan_id (execution plan)
  const planGrouped = new Map<string, SymbolStrategyResponse[]>()
  for (const s of strategies) {
    if (s.plan_id) {
      const arr = planGrouped.get(s.plan_id) || []
      arr.push(s)
      planGrouped.set(s.plan_id, arr)
    } else if (s.parent_intent_id) {
      const arr = grouped.get(s.parent_intent_id) || []
      arr.push(s)
      grouped.set(s.parent_intent_id, arr)
    } else {
      orphanStrategies.push(s)
    }
  }
  // Sort plan groups by tranche_sequence
  for (const [, arr] of planGrouped) {
    arr.sort((a, b) => (a.tranche_sequence || 0) - (b.tranche_sequence || 0))
  }

  async function handlePause(id: string) {
    try { await actionApi.pauseStrategy(id); fetchData() }
    catch (e: unknown) { alert((e as Error).message) }
  }
  async function handleResume(id: string) {
    try { await actionApi.resumeStrategy(id); fetchData() }
    catch (e: unknown) { alert((e as Error).message) }
  }
  async function handleDiscard(id: string) {
    try { await actionApi.discardStrategy(id); fetchData() }
    catch (e: unknown) { alert((e as Error).message) }
  }
  async function handleCancelOrder(id: string) {
    try { await actionApi.cancelOrder(id); fetchData() }
    catch (e: unknown) { alert((e as Error).message) }
  }
  async function handleDiscardIntent(id: string) {
    try { await actionApi.discardIntent(id); fetchData() }
    catch (e: unknown) { alert((e as Error).message) }
  }

  if (loading) return <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#9CA3AF', padding: 20 }}><Loader2 size={16} className="animate-spin" />加载中...</div>

  const hasAny = drafts.length > 0 || strategies.length > 0 || orders.length > 0

  if (!hasAny) {
    return (
      <div style={{ textAlign: 'center', padding: '60px 0', color: '#9CA3AF' }}>
        <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 8 }}>暂无行动清单</div>
        <button onClick={() => navigate('/decision')} style={{
          fontSize: 12, color: '#3B82F6', background: 'none', border: 'none', cursor: 'pointer',
        }}>在投资决策页面生成第一份行动清单 →</button>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>

      {/* ── 待确认 ── */}
      {drafts.length > 0 && (
        <>
          <SectionTitle>待确认</SectionTitle>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 8 }}>
            {drafts.map(d => (
              <div key={d.id} style={{
                background: '#fff', border: '1px solid #3B82F6', borderRadius: 12,
                padding: '14px 18px',
                boxShadow: '0 1px 3px rgba(15,30,53,0.07), 0 1px 2px rgba(15,30,53,0.04)',
              }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 6 }}>
                  <FileText size={14} style={{ color: '#3B82F6', flexShrink: 0, marginTop: 2 }} />
                  <span style={{
                    fontSize: 13, fontWeight: 600, color: '#1B2A4A', flex: 1,
                    display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' as const,
                    overflow: 'hidden', lineHeight: 1.5,
                  }}>
                    {d.decision_summary || '未命名草稿'}
                  </span>
                  <span style={{ fontSize: 11, color: '#9CA3AF' }}>
                    {d.created_at ? new Date(d.created_at).toLocaleString('zh-CN') : ''}
                  </span>
                </div>
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                  <button onClick={() => onEditDraft(d)} style={{
                    fontSize: 11, padding: '4px 12px', borderRadius: 6,
                    border: '1px solid #3B82F6', background: '#EFF6FF', color: '#1D4ED8', cursor: 'pointer',
                  }}>查看并确认</button>
                  <button onClick={() => onDiscardDraft(d.id)} style={{
                    fontSize: 11, padding: '4px 8px', borderRadius: 6,
                    border: '1px solid #E5E7EB', background: '#fff', color: '#9CA3AF', cursor: 'pointer',
                  }}><Trash2 size={12} /></button>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {/* ── 已执行中（按意图分组） ── */}
      {(() => {
        // 只展示有关联策略的意图
        const activeIntents = intents.filter(i => (grouped.get(i.id) || []).length > 0)
        if (activeIntents.length === 0 && orphanStrategies.length === 0) return null
        return (
        <>
          <SectionTitle>已执行中</SectionTitle>

          {/* 有意图分组的策略 */}
          {activeIntents.map(intent => {
            const intentStrategies = grouped.get(intent.id) || []
            return (
              <IntentGroup
                key={intent.id}
                intent={intent}
                strategies={intentStrategies}
                onPause={handlePause}
                onResume={handleResume}
                onDiscard={handleDiscard}
                onDiscardIntent={() => handleDiscardIntent(intent.id)}
                onPlaceOrder={setPlaceOrderStrategy}
              />
            )
          })}

          {/* v3.11: 执行计划归组 */}
          {Array.from(planGrouped.entries()).map(([planId, planStrategies]) => {
            const first = planStrategies[0]
            const sideLabel = first.side === 'BUY' ? '买入' : '卖出'
            const totalQty = planStrategies.reduce((s, st) => s + (st.target_quantity || 0), 0)
            return (
              <div key={planId} style={{
                border: '1px solid #93C5FD', borderRadius: 12, padding: '16px 18px',
                marginBottom: 12, background: '#EFF6FF',
              }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
                  <span style={{ fontSize: 14, fontWeight: 700, color: '#1B2A4A' }}>{first.symbol}</span>
                  <span style={{
                    fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 4,
                    background: first.side === 'BUY' ? '#DCFCE7' : '#FEE2E2',
                    color: first.side === 'BUY' ? '#16A34A' : '#DC2626',
                  }}>{sideLabel} 计划</span>
                  <span style={{ fontSize: 11, color: '#6B7280' }}>
                    {planStrategies.length} 批 · 共 {totalQty.toLocaleString()} 股
                  </span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {planStrategies.map(s => (
                    <StrategyCard key={s.id} strategy={s}
                      onPause={() => handlePause(s.id)}
                      onResume={() => handleResume(s.id)}
                      onDiscard={() => handleDiscard(s.id)}
                      onPlaceOrder={() => setPlaceOrderStrategy(s)}
                      compact
                      trancheLabel={`第 ${s.tranche_sequence || '?'} 批`}
                    />
                  ))}
                </div>
              </div>
            )
          })}

          {/* 无关联意图的策略 */}
          {orphanStrategies.length > 0 && (
            <div style={{ marginTop: activeIntents.length > 0 ? 24 : 0 }}>
              {activeIntents.length > 0 && (
                <div style={{ fontSize: 12, fontWeight: 500, color: '#9CA3AF', marginBottom: 12 }}>
                  无关联意图的策略
                </div>
              )}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {orphanStrategies.map(s => (
                  <StrategyCard key={s.id} strategy={s}
                    onPause={() => handlePause(s.id)}
                    onResume={() => handleResume(s.id)}
                    onDiscard={() => handleDiscard(s.id)}
                    onPlaceOrder={() => setPlaceOrderStrategy(s)}
                  />
                ))}
              </div>
            </div>
          )}
        </>
        )
      })()}

      {/* ── 已挂单 ── */}
      {orders.length > 0 && (
        <>
          <SectionTitle>已挂单</SectionTitle>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {orders.map(o => (
              <OrderCard key={o.id} order={o} onCancel={handleCancelOrder} />
            ))}
          </div>
        </>
      )}

      {/* ConfirmOrderDialog */}
      {placeOrderStrategy && (
        <ConfirmOrderDialog
          open={!!placeOrderStrategy}
          onClose={() => setPlaceOrderStrategy(null)}
          strategy={placeOrderStrategy}
          onOrderPlaced={fetchData}
        />
      )}
    </div>
  )
}


// ── 意图分组 ────────────────────────────────────────────────

function IntentGroup({
  intent, strategies, onPause, onResume, onDiscard, onDiscardIntent, onPlaceOrder,
}: {
  intent: AllocationIntentResponse
  strategies: SymbolStrategyResponse[]
  onPause: (id: string) => void
  onResume: (id: string) => void
  onDiscard: (id: string) => void
  onDiscardIntent: () => void
  onPlaceOrder: (s: SymbolStrategyResponse) => void
}) {
  const navigate = useNavigate()
  const alloc = intent.target_allocation
  const allocSummary = alloc
    ? Object.entries(alloc).map(([k, v]) =>
      `${ALLOC_LABEL[k] || k} ${typeof v === 'number' ? `${(v * 100).toFixed(0)}%` : v}`
    ).join(' · ')
    : ''

  return (
    <div style={{ marginBottom: 24 }}>
      {/* 意图标题 */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <span style={{ fontSize: 15, fontWeight: 700, color: '#1B2A4A' }}>
            {intent.title || '未命名意图'}
          </span>
        </div>
        <div style={{ fontSize: 12, color: '#6B7280', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {allocSummary && <span>{allocSummary}</span>}
          <span>关联 {strategies.length} 条策略</span>
          {intent.related_conversation_id && (
            <button
              onClick={() => navigate(`/decision?conversation_id=${intent.related_conversation_id}`)}
              style={{
                fontSize: 11, color: '#3B82F6', background: 'none', border: 'none',
                cursor: 'pointer', padding: 0,
              }}
              onMouseEnter={e => (e.currentTarget.style.color = '#1D4ED8')}
              onMouseLeave={e => (e.currentTarget.style.color = '#3B82F6')}
            >查看原始对话 →</button>
          )}
        </div>
      </div>

      {/* 意图下的策略卡片 */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {strategies.length === 0 ? (
          <div style={{ fontSize: 12, color: '#9CA3AF', padding: '8px 0' }}>暂无关联策略</div>
        ) : (
          strategies.map(s => (
            <StrategyCard key={s.id} strategy={s}
              onPause={() => onPause(s.id)}
              onResume={() => onResume(s.id)}
              onDiscard={() => onDiscard(s.id)}
              onPlaceOrder={() => onPlaceOrder(s)}
              compact
            />
          ))
        )}
      </div>
    </div>
  )
}


// ── 策略卡片 ────────────────────────────────────────────────

function StrategyCard({ strategy, onPause, onResume, onDiscard, onPlaceOrder, compact, trancheLabel }: {
  strategy: SymbolStrategyResponse
  onPause: () => void
  onResume: () => void
  onDiscard: () => void
  onPlaceOrder: () => void
  compact?: boolean
  trancheLabel?: string  // v3.11: "第 N 批"
}) {
  const [confirmDiscard, setConfirmDiscard] = useState(false)

  function handleDiscard() {
    if (!confirmDiscard) { setConfirmDiscard(true); setTimeout(() => setConfirmDiscard(false), 3000); return }
    onDiscard()
  }

  const progress = strategy.target_quantity
    ? Math.round((strategy.cumulative_filled_quantity / strategy.target_quantity) * 100)
    : 0
  const sideLabel = strategy.side === 'BUY' ? '买入' : '卖出'
  const sideBadgeBg = strategy.side === 'BUY' ? '#DCFCE7' : '#FEE2E2'
  const sideBadgeColor = strategy.side === 'BUY' ? '#16A34A' : '#DC2626'
  const isPaused = strategy.status === 'paused'
  const progressColor = isPaused ? '#9CA3AF' : strategy.side === 'BUY' ? '#3B82F6' : '#F59E0B'

  return (
    <div style={{
      background: '#fff',
      border: `1px solid ${isPaused ? '#FDE68A' : compact ? '#F3F4F6' : '#E5E7EB'}`,
      borderRadius: compact ? 10 : 12, padding: compact ? '12px 16px' : '16px 18px',
      boxShadow: compact ? 'none' : '0 1px 3px rgba(15,30,53,0.07), 0 1px 2px rgba(15,30,53,0.04)',
      opacity: isPaused ? 0.75 : 1,
    }}>
      {/* Row 1: 标的名 + Badges */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        {trancheLabel && (
          <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 4,
            background: '#DBEAFE', color: '#1E40AF' }}>{trancheLabel}</span>
        )}
        {!trancheLabel && <span style={{ fontSize: 14, fontWeight: 700, color: '#1B2A4A' }}>{strategy.symbol}</span>}
        {!trancheLabel && <span style={{
          fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 4,
          background: sideBadgeBg, color: sideBadgeColor,
        }}>{sideLabel}</span>}
        <span style={{ fontSize: 11, color: '#9CA3AF', background: '#F3F4F6', padding: '2px 6px', borderRadius: 4 }}>限价单</span>
        {isPaused && <span style={{
          fontSize: 10, fontWeight: 500, padding: '2px 6px', borderRadius: 4, background: '#FEF3C7', color: '#D97706',
        }}>已暂停</span>}
        {/* v3.11 M6: 到价/暂缓标记 */}
        {strategy.armed_at && !strategy.interval_blocked && (
          <span style={{
            fontSize: 10, fontWeight: 600, padding: '2px 8px', borderRadius: 4,
            background: '#EFF6FF', color: '#1D4ED8',
            boxShadow: '0 0 0 1px #93C5FD',
          }}>已到价 · 待确认下单</span>
        )}
        {strategy.interval_blocked && (
          <span style={{
            fontSize: 10, fontWeight: 500, padding: '2px 6px', borderRadius: 4,
            background: '#FEF3C7', color: '#D97706',
          }}>{strategy.interval_blocked}</span>
        )}
      </div>
      {/* Row 2: 触发价/限价 + 目标 + 进度 */}
      <div style={{ fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 6, fontVariantNumeric: 'tabular-nums' }}>
        {strategy.trigger_price ? `触发 $${strategy.trigger_price} → ` : ''}
        {strategy.limit_price ? `限价 $${strategy.limit_price}` : '限价未设'}
        {strategy.target_quantity ? <span style={{ fontWeight: 400, color: '#6B7280' }}> · 目标 {strategy.target_quantity.toLocaleString()} 股</span> : ''}
        <span style={{ fontWeight: 400, color: '#9CA3AF', marginLeft: 8, fontSize: 11 }}>
          已成交 {strategy.cumulative_filled_quantity}/{strategy.target_quantity || '—'} ({progress}%)
        </span>
      </div>
      {/* Row 3: 进度条 */}
      <div style={{ marginBottom: 10 }}>
        <div style={{
          height: 6, borderRadius: 3, maxWidth: 200,
          background: `${progressColor}18`, border: `1px solid ${progressColor}30`,
        }}>
          <div style={{
            height: '100%', borderRadius: 3, transition: 'width 0.3s',
            width: `${Math.max(progress, 0)}%`, background: progressColor,
            minWidth: progress > 0 ? 4 : 0,
          }} />
        </div>
      </div>
      {/* Row 4: 操作按钮 */}
      <div style={{ display: 'flex', gap: 8 }}>
        <button onClick={isPaused ? onResume : onPause} style={{
          fontSize: 11, padding: '4px 10px', borderRadius: 6,
          border: '1px solid #E5E7EB', background: '#fff', color: '#6B7280', cursor: 'pointer',
        }}>{isPaused ? '恢复' : '暂停'}</button>
        <button onClick={handleDiscard} style={{
          fontSize: 11, padding: '4px 10px', borderRadius: 6,
          border: `1px solid ${confirmDiscard ? '#EF4444' : '#E5E7EB'}`,
          background: confirmDiscard ? '#FEF2F2' : '#fff',
          color: confirmDiscard ? '#DC2626' : '#9CA3AF', cursor: 'pointer',
        }}>{confirmDiscard ? '确认作废？' : '作废'}</button>
        <button
          data-testid={`place-order-btn-${strategy.id}`}
          onClick={onPlaceOrder}
          disabled={isPaused || strategy.status !== 'active'}
          style={{
            fontSize: 11, padding: '4px 10px', borderRadius: 6,
            border: `1px solid ${isPaused ? '#D1D5DB' : '#DC2626'}`,
            background: isPaused ? '#F9FAFB' : '#FEF2F2',
            color: isPaused ? '#D1D5DB' : '#DC2626',
            cursor: isPaused ? 'not-allowed' : 'pointer',
            fontWeight: 600,
          }}
        >立即下单</button>
      </div>
    </div>
  )
}


// ═══════════════════════════════════════════════════════════════════
// 行动记录 Tab（时间轴，保持不变）
// ═══════════════════════════════════════════════════════════════════

const HIGH_VALUE_EVENTS = new Set([
  'order_placed_manual_confirm', 'order_submitted', 'order_synced', 'order_rejected', 'order_cancelled',
  'order_expired', 'order_network_error', 'strategy_auto_completed',
  'draft_confirmed', 'intent_discarded',
])

const EVENT_TYPE_DISPLAY: Record<string, { icon: string; title: string; color: string }> = {
  draft_created:         { icon: '📝', title: '草稿生成', color: '#9CA3AF' },
  draft_updated:         { icon: '✏️', title: '草稿编辑', color: '#9CA3AF' },
  draft_confirmed:       { icon: '📋', title: '行动清单已确认', color: '#3B82F6' },
  draft_discarded:       { icon: '🗑️', title: '草稿已丢弃', color: '#9CA3AF' },
  strategy_paused:       { icon: '⏸', title: '策略已暂停', color: '#F59E0B' },
  strategy_resumed:      { icon: '▶️', title: '策略已恢复', color: '#10B981' },
  strategy_discarded:    { icon: '🗑️', title: '策略已作废', color: '#9CA3AF' },
  strategy_auto_completed: { icon: '🎯', title: '策略完成（累计成交达标）', color: '#10B981' },
  intent_updated:        { icon: '✏️', title: '配置意图已编辑', color: '#9CA3AF' },
  intent_discarded:      { icon: '🗑️', title: '配置意图已作废', color: '#9CA3AF' },
  order_created:         { icon: '📤', title: '订单已创建', color: '#6B7280' },
  order_placed_manual_confirm: { icon: '📤', title: '订单已提交（人工确认）', color: '#3B82F6' },
  order_submitted:       { icon: '📤', title: '订单已提交', color: '#3B82F6' },
  order_rejected:        { icon: '❌', title: '订单被拒', color: '#DC2626' },
  order_cancelled:       { icon: '🚫', title: '订单已取消', color: '#F59E0B' },
  order_synced:          { icon: '🟢', title: '订单状态更新', color: '#10B981' },
  order_network_error:   { icon: '⚠️', title: '网络异常', color: '#DC2626' },
  sync_network_error:    { icon: '⚠️', title: '同步异常', color: '#DC2626' },
  order_expired:         { icon: '⏰', title: '订单已过期', color: '#9CA3AF' },
}

function deduplicateOrderEvents(items: TimelineEvent[]): TimelineEvent[] {
  const manualConfirms = new Map<string, number>()
  for (const ev of items) {
    if (ev.event_type === 'order_placed_manual_confirm' && ev.timestamp) {
      const oid = (ev.payload?.order_id || ev.trace?.order?.id) as string
      if (oid) manualConfirms.set(oid, new Date(ev.timestamp).getTime())
    }
  }
  return items.filter(ev => {
    if (ev.event_type !== 'order_submitted' && ev.event_type !== 'order_created') return true
    const oid = (ev.payload?.order_id || ev.trace?.order?.id) as string
    if (!oid || !manualConfirms.has(oid)) return true
    const confirmTs = manualConfirms.get(oid)!
    const evTs = ev.timestamp ? new Date(ev.timestamp).getTime() : 0
    return Math.abs(confirmTs - evTs) >= 10000
  })
}

function TimelineTab() {
  const navigate = useNavigate()
  const [events, setEvents] = useState<TimelineEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [showAll, setShowAll] = useState(false)

  useEffect(() => {
    actionApi.getTimeline(100).then(res => {
      setEvents(res.items)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  if (loading) return <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#9CA3AF', padding: 20 }}><Loader2 size={16} className="animate-spin" />加载中...</div>

  const filtered = deduplicateOrderEvents(
    showAll ? events : events.filter(e => HIGH_VALUE_EVENTS.has(e.event_type)),
  )
  const hiddenCount = events.length - events.filter(e => HIGH_VALUE_EVENTS.has(e.event_type)).length

  if (events.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '60px 0', color: '#9CA3AF' }}>
        <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 8 }}>暂无行动记录</div>
        <button onClick={() => navigate('/decision')} style={{
          fontSize: 12, color: '#3B82F6', background: 'none', border: 'none', cursor: 'pointer',
        }}>在投资决策页面生成第一份行动清单 →</button>
      </div>
    )
  }

  return (
    <div>
      {hiddenCount > 0 && (
        <button onClick={() => setShowAll(!showAll)} style={{
          fontSize: 12, color: '#3B82F6', background: '#EFF6FF', border: '1px solid #DBEAFE',
          borderRadius: 6, padding: '4px 12px', cursor: 'pointer', marginBottom: 16,
        }}>
          {showAll ? '只看高价值事件' : `显示全部事件 (${events.length} 条，含 ${hiddenCount} 条低价值)`}
        </button>
      )}

      <div style={{ position: 'relative', paddingLeft: 24 }}>
        <div style={{ position: 'absolute', left: 7, top: 0, bottom: 0, width: 2, background: '#E5E7EB' }} />

        {filtered.map(ev => {
          const cfg = EVENT_TYPE_DISPLAY[ev.event_type] || { icon: '❓', title: ev.event_type, color: '#9CA3AF' }
          const t = ev.trace
          const p = ev.payload
          const isHigh = HIGH_VALUE_EVENTS.has(ev.event_type)
          const dotSize = isHigh ? 12 : 8

          return (
            <div key={ev.id} style={{
              position: 'relative', marginBottom: isHigh ? 18 : 12, paddingLeft: 16,
              opacity: isHigh ? 1 : 0.85,
            }}>
              <div style={{
                position: 'absolute', left: isHigh ? -20 : -18, top: isHigh ? 4 : 6,
                width: dotSize, height: dotSize,
                borderRadius: '50%', background: cfg.color, border: '2px solid #fff',
                boxShadow: `0 0 0 2px ${cfg.color}30`,
              }} />

              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 4 }}>
                <div style={{
                  fontSize: isHigh ? 14 : 13, fontWeight: isHigh ? 600 : 400,
                  color: isHigh ? '#1B2A4A' : '#374151',
                }}>
                  {cfg.icon} {cfg.title}
                </div>
                <span style={{ fontSize: 11, color: '#9CA3AF', marginLeft: 'auto', flexShrink: 0 }}>
                  {ev.timestamp ? new Date(ev.timestamp).toLocaleString('zh-CN') : ''}
                </span>
              </div>

              {t.order && (
                <div style={{ fontSize: 12, color: '#374151', marginBottom: 4 }}>
                  {t.order.symbol} {t.order.side === 'BUY' ? '买入' : '卖出'} {t.order.quantity}股
                  {t.order.limit_price ? ` @$${t.order.limit_price}` : ''}
                  {p.status ? ` → ${p.status}` : ''}
                  {p.filled_quantity ? ` (成交${p.filled_quantity}股)` : ''}
                </div>
              )}
              {!t.order && t.strategy && (
                <div style={{ fontSize: 12, color: '#374151', marginBottom: 4 }}>
                  {t.strategy.symbol} {t.strategy.side === 'BUY' ? '买入' : '卖出'}
                  {t.strategy.target_quantity ? ` ${t.strategy.target_quantity}股` : ''}
                  {t.strategy.limit_price ? ` @$${t.strategy.limit_price}` : ''}
                </div>
              )}

              <div style={{ fontSize: 11, color: '#6B7280', display: 'flex', flexDirection: 'column', gap: 2, marginTop: 4 }}>
                {t.strategy && (
                  <span>← 策略：{t.strategy.symbol} {t.strategy.side === 'BUY' ? '买入' : '卖出'}
                    {t.strategy.limit_price ? ` @$${t.strategy.limit_price}` : ''}</span>
                )}
                {t.draft && (
                  <span>← 行动清单：{t.draft.decision_summary || '（无摘要）'}</span>
                )}
                {(t.strategy?.related_conversation_id || t.draft?.conversation_id) && (
                  <button
                    onClick={() => navigate(`/decision?conversation_id=${t.strategy?.related_conversation_id || t.draft?.conversation_id}`)}
                    style={{ fontSize: 11, color: '#3B82F6', background: 'none', border: 'none', cursor: 'pointer', padding: 0, textAlign: 'left' }}
                    onMouseEnter={e => (e.currentTarget.style.color = '#1D4ED8')}
                    onMouseLeave={e => (e.currentTarget.style.color = '#3B82F6')}
                  >← 查看原始对话 →</button>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}


// ── 订单卡片(v3.4 M5) ────────────────────────────────────────

const STATUS_DISPLAY: Record<string, { label: string; color: string; dotColor: string }> = {
  submitted_to_broker: { label: '提交中', color: '#6B7280', dotColor: '#3B82F6' },
  broker_pending:      { label: '已挂单', color: '#1B2A4A', dotColor: '#F59E0B' },
  partially_filled:    { label: '部分成交', color: '#1B2A4A', dotColor: '#10B981' },
  filled:              { label: '已成交', color: '#16A34A', dotColor: '#10B981' },
  cancelled:           { label: '已撤单', color: '#9CA3AF', dotColor: '#9CA3AF' },
  rejected:            { label: '被拒绝', color: '#DC2626', dotColor: '#DC2626' },
  expired:             { label: '已过期', color: '#9CA3AF', dotColor: '#9CA3AF' },
  unknown:             { label: '状态未知', color: '#D97706', dotColor: '#D97706' },
}

const TERMINAL_STATUSES = new Set(['filled', 'cancelled', 'rejected', 'expired'])

function OrderCard({ order: o, onCancel }: {
  order: OrderResponse
  onCancel: (id: string) => void
}) {
  const [confirmCancel, setConfirmCancel] = useState(false)
  const [cancelling, setCancelling] = useState(false)

  const cfg = STATUS_DISPLAY[o.status] || STATUS_DISPLAY.unknown
  const isTerminal = TERMINAL_STATUSES.has(o.status)
  const isPartial = o.status === 'partially_filled'
  const canCancel = !isTerminal && o.status !== 'unknown'

  async function handleCancel() {
    if (!confirmCancel) {
      setConfirmCancel(true)
      setTimeout(() => setConfirmCancel(false), 3000)
      return
    }
    setCancelling(true)
    try {
      await onCancel(o.id)
    } finally {
      setCancelling(false)
      setConfirmCancel(false)
    }
  }

  return (
    <div style={{
      background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12,
      padding: '12px 18px',
      boxShadow: '0 1px 3px rgba(15,30,53,0.07), 0 1px 2px rgba(15,30,53,0.04)',
      opacity: isTerminal ? 0.7 : 1,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{
          width: 8, height: 8, borderRadius: '50%',
          background: cfg.dotColor, flexShrink: 0,
        }} />
        <div style={{ flex: 1 }}>
          <div>
            <span style={{ fontSize: 13, fontWeight: 600, color: '#1B2A4A' }}>
              {o.symbol} {o.side === 'BUY' ? '买入' : '卖出'} {o.quantity}股
              {o.limit_price ? ` @$${o.limit_price}` : ''}
            </span>
            <span style={{
              fontSize: 11, fontWeight: 500, marginLeft: 8,
              padding: '1px 6px', borderRadius: 4,
              background: `${cfg.dotColor}18`, color: cfg.color,
            }}>{cfg.label}</span>
          </div>
          <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 2 }}>
            ({o.broker_name === 'ibkr' || o.broker_name === 'snowball' ? '盈透证券' : o.broker_name === 'tiger' ? '老虎证券' : o.broker_name}) · {o.created_at ? new Date(o.created_at).toLocaleTimeString('zh-CN') : ''}
          </div>
        </div>
        {canCancel && (
          <button
            onClick={handleCancel}
            disabled={cancelling}
            style={{
              fontSize: 11, padding: '4px 10px', borderRadius: 6,
              border: `1px solid ${confirmCancel ? '#EF4444' : '#E5E7EB'}`,
              background: confirmCancel ? '#FEF2F2' : '#fff',
              color: confirmCancel ? '#DC2626' : '#9CA3AF',
              cursor: cancelling ? 'not-allowed' : 'pointer',
            }}
          >
            {cancelling ? '撤单中...' : confirmCancel ? '确认撤单?' : '撤单'}
          </button>
        )}
      </div>

      {/* 部分成交详情 */}
      {isPartial && (
        <div style={{
          marginTop: 8, padding: '8px 12px', background: '#F0FDF4',
          borderRadius: 6, fontSize: 12, color: '#16A34A',
        }}>
          已成交: {o.filled_quantity}股
          {o.avg_filled_price ? ` @ $${o.avg_filled_price} 均价` : ''}
          <span style={{ color: '#6B7280', marginLeft: 8 }}>
            待成交: {(o.quantity || 0) - (o.filled_quantity || 0)}股
          </span>
        </div>
      )}
    </div>
  )
}


// ── 辅助组件 ────────────────────────────────────────────────

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize: 13, fontWeight: 600, color: '#6B7280',
      paddingTop: 24, paddingBottom: 12,
    }}>
      {children}
    </div>
  )
}

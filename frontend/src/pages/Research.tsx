/**
 * Research — 投研观点
 * Tab 1: 资料导入（4种方式 · AI解析 · 审核 · 待审核列表 · 已导入文档）
 * Tab 2: 观点库（多维筛选 · 可折叠列表 · CRUD）
 * Tab 3: 决策检索（自然语言查询）
 */
import React, { useState, useEffect, useRef } from 'react'
import {
  Loader2, AlertTriangle, Plus, Search, Trash2,
  Pencil, ChevronDown, ChevronUp, Check, X, Sparkles, FileText, Link, RefreshCw,
} from 'lucide-react'
import {
  researchApi, researchV2Api,
  type Viewpoint, type ViewpointCreate, type ResearchCard, type ResearchDocument,
  type ParseResult, type ViewpointCardV2,
} from '@/lib/api'

// ── 样式常量 ──────────────────────────────────────────────────
const S = {
  card: { background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12, boxShadow: 'var(--shadow-sm)' } as React.CSSProperties,
  btnPrimary: { background: 'linear-gradient(135deg, #3B82F6, #1D4ED8)', color: '#fff', border: 'none', borderRadius: 8, padding: '8px 16px', fontSize: 12, fontWeight: 500, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 5 } as React.CSSProperties,
  btnSecondary: { background: '#fff', color: '#374151', border: '1px solid #E5E7EB', borderRadius: 8, padding: '7px 14px', fontSize: 12, fontWeight: 500, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 5 } as React.CSSProperties,
  btnDanger: { background: '#FEF2F2', color: '#DC2626', border: '1px solid #FECACA', borderRadius: 8, padding: '7px 14px', fontSize: 12, fontWeight: 500, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 5 } as React.CSSProperties,
  label: { fontSize: 12, fontWeight: 500, color: '#6B7280', marginBottom: 4, display: 'block' } as React.CSSProperties,
  input: { width: '100%', border: '1px solid #E5E7EB', borderRadius: 8, padding: '8px 10px', fontSize: 13, color: '#374151', outline: 'none', boxSizing: 'border-box' as const, fontFamily: 'inherit' },
  select: { border: '1px solid #E5E7EB', borderRadius: 8, padding: '7px 10px', fontSize: 12, color: '#374151', outline: 'none', background: '#fff', fontFamily: 'inherit' },
  textarea: { width: '100%', border: '1px solid #E5E7EB', borderRadius: 8, padding: '8px 10px', fontSize: 13, color: '#374151', outline: 'none', boxSizing: 'border-box' as const, resize: 'vertical' as const, fontFamily: 'inherit', lineHeight: 1.6 },
}

// ── 导入方式 ─────────────────────────────────────────────────
type SourceType = 'text' | 'markdown' | 'link' | 'pdf'
const SOURCE_TYPES: { key: SourceType; label: string; icon: React.ReactNode }[] = [
  { key: 'text',     label: '纯文本粘贴', icon: <FileText size={12} /> },
  { key: 'markdown', label: 'Markdown文件', icon: <FileText size={12} /> },
  { key: 'link',     label: '链接URL',    icon: <Link size={12} /> },
  { key: 'pdf',      label: 'PDF上传',    icon: <FileText size={12} /> },
]

// ── 文档状态 ─────────────────────────────────────────────────
const DOC_STATUS: Record<string, { label: string; bg: string; color: string }> = {
  parsed:     { label: '已解析', bg: '#D1FAE5', color: '#059669' },
  pending:    { label: '待解析', bg: '#FEF3C7', color: '#D97706' },
  saved_only: { label: '仅存档', bg: '#F3F4F6', color: '#6B7280' },
  discarded:  { label: '已丢弃', bg: '#F3F4F6', color: '#9CA3AF' },
}

// ── 有效性 / 立场 badge ───────────────────────────────────────
const VALIDITY: Record<string, { label: string; bg: string; color: string }> = {
  active:  { label: '有效', bg: '#D1FAE5', color: '#059669' },
  suspect: { label: '存疑', bg: '#FEF3C7', color: '#D97706' },
  invalid: { label: '已作废', bg: '#F3F4F6', color: '#9CA3AF' },
}
// 来源类型中文映射（去品牌化）
const SOURCE_TYPE_LABEL: Record<string, string> = {
  user_upload: '用户上传',
  alpha_vantage_news: '公开新闻',
  alpha_vantage_fundamental: '基本面快照',
  alpha_vantage_earnings: '财报数据',
  perplexity_search: '联网搜索',
  hybrid: '混合来源',
}

const STANCE: Record<string, { label: string; bg: string; color: string }> = {
  bullish: { label: '做多', bg: '#FEE2E2', color: '#DC2626' },
  bearish: { label: '做空', bg: '#D1FAE5', color: '#059669' },
  neutral: { label: '中性', bg: '#EFF6FF', color: '#3B82F6' },
  watch:   { label: '观察', bg: '#FEF3C7', color: '#D97706' },
}

function validityBadge(status: string | undefined) {
  const v = VALIDITY[status ?? 'active'] ?? VALIDITY.active
  return <span style={{ fontSize: 10, fontWeight: 500, padding: '2px 7px', borderRadius: 10, background: v.bg, color: v.color }}>{v.label}</span>
}
function stanceBadge(stance: string | undefined) {
  if (!stance) return null
  const v = STANCE[stance]
  if (!v) return null
  return <span style={{ fontSize: 10, fontWeight: 500, padding: '2px 7px', borderRadius: 10, background: v.bg, color: v.color }}>{v.label}</span>
}

// ── 空表单 ────────────────────────────────────────────────────
const EMPTY_FORM: ViewpointCreate = {
  title: '', object_type: 'asset', object_name: '',
  stance: 'neutral', thesis: '', supporting_points: [], opposing_points: [],
  risks: [], action_suggestion: '', invalidation_conditions: '',
  horizon: '中期', validity_status: 'active', user_approval_level: 'reference',
}

// ── StringList 输入控件 ───────────────────────────────────────
function StringListInput({ label, value, onChange }: {
  label: string; value: string[]; onChange: (v: string[]) => void
}) {
  const [input, setInput] = useState('')
  function add() {
    const t = input.trim(); if (!t) return
    onChange([...value, t]); setInput('')
  }
  return (
    <div>
      <label style={S.label}>{label}</label>
      {value.map((item, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 6, marginBottom: 4 }}>
          <div style={{ flex: 1, background: '#F8FAFC', border: '1px solid #E5E7EB', borderRadius: 6, padding: '6px 8px', fontSize: 12, color: '#374151', lineHeight: 1.5 }}>{item}</div>
          <button onClick={() => onChange(value.filter((_, j) => j !== i))}
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#9CA3AF', padding: '4px', flexShrink: 0, marginTop: 2 }}>
            <X size={12} />
          </button>
        </div>
      ))}
      <div style={{ display: 'flex', gap: 6 }}>
        <input value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && add()}
          placeholder="输入后按 Enter 或点击添加" style={{ ...S.input, flex: 1, fontSize: 12 }} />
        <button onClick={add} style={{ ...S.btnSecondary, padding: '7px 10px' }}><Plus size={12} /></button>
      </div>
    </div>
  )
}

// ── 观点表单 ──────────────────────────────────────────────────
function ViewpointForm({ initial, onSave, onCancel, saving }: {
  initial: ViewpointCreate; onSave: (data: ViewpointCreate) => Promise<void>
  onCancel: () => void; saving: boolean
}) {
  const [form, setForm] = useState<ViewpointCreate>(initial)
  const set = (k: keyof ViewpointCreate, v: unknown) => setForm(prev => ({ ...prev, [k]: v }))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div>
        <label style={S.label}>标题 *</label>
        <input style={S.input} value={form.title} onChange={e => set('title', e.target.value)} placeholder="简短描述这条观点" />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 10 }}>
        <div>
          <label style={S.label}>标的类型</label>
          <select style={{ ...S.select, width: '100%' }} value={form.object_type ?? 'asset'} onChange={e => set('object_type', e.target.value)}>
            <option value="asset">个股/ETF</option><option value="market">市场</option>
            <option value="sector">行业</option><option value="theme">主题</option><option value="macro">宏观</option>
          </select>
        </div>
        <div>
          <label style={S.label}>标的名称</label>
          <input style={S.input} value={form.object_name ?? ''} onChange={e => set('object_name', e.target.value)} placeholder="如：腾讯控股" />
        </div>
        <div>
          <label style={S.label}>立场</label>
          <select style={{ ...S.select, width: '100%' }} value={form.stance ?? 'neutral'} onChange={e => set('stance', e.target.value)}>
            <option value="bullish">做多</option><option value="bearish">做空</option><option value="neutral">中性</option>
          </select>
        </div>
        <div>
          <label style={S.label}>时间维度</label>
          <select style={{ ...S.select, width: '100%' }} value={form.horizon ?? '中期'} onChange={e => set('horizon', e.target.value)}>
            <option value="短期">短期（&lt;3月）</option><option value="中期">中期（3-12月）</option>
            <option value="长期">长期（1年+）</option><option value="不限">不限</option>
          </select>
        </div>
      </div>
      <div>
        <label style={S.label}>核心论点</label>
        <textarea rows={3} style={S.textarea} value={form.thesis ?? ''} onChange={e => set('thesis', e.target.value)} placeholder="用 1-3 句话阐述这条观点的核心逻辑" />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <StringListInput label="支持论点" value={form.supporting_points ?? []} onChange={v => set('supporting_points', v)} />
        <StringListInput label="反对论点 / 风险因素" value={form.opposing_points ?? []} onChange={v => set('opposing_points', v)} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <div>
          <label style={S.label}>操作建议</label>
          <textarea rows={2} style={S.textarea} value={form.action_suggestion ?? ''} onChange={e => set('action_suggestion', e.target.value)} placeholder="具体操作方向，如：逢低分批建仓" />
        </div>
        <div>
          <label style={S.label}>作废条件</label>
          <textarea rows={2} style={S.textarea} value={form.invalidation_conditions ?? ''} onChange={e => set('invalidation_conditions', e.target.value)} placeholder="何时该作废此观点" />
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <div>
          <label style={S.label}>有效性</label>
          <select style={{ ...S.select, width: '100%' }} value={form.validity_status ?? 'active'} onChange={e => set('validity_status', e.target.value)}>
            <option value="active">有效</option><option value="suspect">存疑</option><option value="invalid">已作废</option>
          </select>
        </div>
        <div>
          <label style={S.label}>认同程度</label>
          <select style={{ ...S.select, width: '100%' }} value={form.user_approval_level ?? 'reference'} onChange={e => set('user_approval_level', e.target.value)}>
            <option value="reference">参考</option><option value="partial">部分认可</option><option value="strong">强认可</option>
          </select>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', paddingTop: 4 }}>
        <button style={S.btnSecondary} onClick={onCancel}>取消</button>
        <button style={{ ...S.btnPrimary, opacity: saving || !form.title.trim() ? 0.6 : 1 }}
          disabled={saving || !form.title.trim()} onClick={() => onSave(form)}>
          {saving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />} 保存观点
        </button>
      </div>
    </div>
  )
}

// ── 解析结果预览卡 ────────────────────────────────────────────
function ParseCardPreview({ card }: { card: ResearchCard }) {
  const sLabel: React.CSSProperties = { fontSize: 10, fontWeight: 600, color: '#9CA3AF', textTransform: 'uppercase', marginBottom: 3 }
  const sText: React.CSSProperties = { fontSize: 12, color: '#374151', lineHeight: 1.65 }
  const hasLists = (card.key_drivers?.length ?? 0) + (card.key_metrics?.length ?? 0) + (card.risks?.length ?? 0) > 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {card.summary && (
        <div style={{ background: '#F8FAFC', borderRadius: 6, padding: '8px 10px' }}>
          <div style={sLabel}>摘要</div>
          <div style={sText}>{card.summary}</div>
        </div>
      )}
      {card.thesis && (
        <div>
          <div style={sLabel}>核心结论</div>
          <div style={{ ...sText, fontWeight: 500, color: '#1B2A4A' }}>{card.thesis}</div>
        </div>
      )}
      {(card.bull_case || card.bear_case) && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          {card.bull_case && (
            <div style={{ background: '#F0FDF4', borderRadius: 6, padding: '8px 10px' }}>
              <div style={{ ...sLabel, color: '#059669' }}>看多逻辑</div>
              <div style={sText}>{card.bull_case}</div>
            </div>
          )}
          {card.bear_case && (
            <div style={{ background: '#FFF5F5', borderRadius: 6, padding: '8px 10px' }}>
              <div style={{ ...sLabel, color: '#DC2626' }}>看空逻辑</div>
              <div style={sText}>{card.bear_case}</div>
            </div>
          )}
        </div>
      )}
      {hasLists && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
          {(card.key_drivers?.length ?? 0) > 0 && (
            <div>
              <div style={sLabel}>关键驱动</div>
              {card.key_drivers!.map((d, i) => <div key={i} style={{ ...sText, fontSize: 11 }}>• {d}</div>)}
            </div>
          )}
          {(card.key_metrics?.length ?? 0) > 0 && (
            <div>
              <div style={sLabel}>观察指标</div>
              {card.key_metrics!.map((d, i) => <div key={i} style={{ ...sText, fontSize: 11 }}>• {d}</div>)}
            </div>
          )}
          {(card.risks?.length ?? 0) > 0 && (
            <div>
              <div style={sLabel}>风险提示</div>
              {card.risks!.map((d, i) => <div key={i} style={{ ...sText, fontSize: 11 }}>• {d}</div>)}
            </div>
          )}
        </div>
      )}
      {card.action_suggestion && (
        <div>
          <div style={sLabel}>操作建议</div>
          <div style={sText}>{card.action_suggestion}</div>
        </div>
      )}
      {card.invalidation_conditions && (
        <div>
          <div style={sLabel}>失效条件</div>
          <div style={{ ...sText, color: '#9CA3AF' }}>{card.invalidation_conditions}</div>
        </div>
      )}
    </div>
  )
}

// ── 主组件 ────────────────────────────────────────────────────
export default function Research() {
  const [viewpoints, setViewpoints] = useState<Viewpoint[]>([])
  const [cards,      setCards]      = useState<ResearchCard[]>([])
  const [documents,  setDocuments]  = useState<ResearchDocument[]>([])
  const [loading,    setLoading]    = useState(true)
  const [error,      setError]      = useState<string | null>(null)
  const [activeTab,  setActiveTab]  = useState<'import' | 'library' | 'search'>('import')

  // ── Tab 1 — 资料导入 ──
  const [sourceType,  setSourceType]  = useState<SourceType>('text')
  const [inputText,   setInputText]   = useState('')
  const [inputTitle,  setInputTitle]  = useState('')
  const [inputUrl,    setInputUrl]    = useState('')
  const [inputFile,   setInputFile]   = useState<File | null>(null)
  const mdFileRef  = useRef<HTMLInputElement>(null)
  const pdfFileRef = useRef<HTMLInputElement>(null)

  const [parsing,     setParsing]     = useState(false)
  const [parseResult, setParseResult] = useState<ParseResult | null>(null)
  const [parseError,  setParseError]  = useState<string | null>(null)

  // 元数据（解析后可编辑）
  const [metaTitle,  setMetaTitle]  = useState('')
  const [metaObject, setMetaObject] = useState('')
  const [metaMarket, setMetaMarket] = useState('')
  const [metaAuthor, setMetaAuthor] = useState('')
  const [metaTime,   setMetaTime]   = useState('')
  const [metaTags,   setMetaTags]   = useState('')
  const [metaStance, setMetaStance] = useState('')
  const [metaHorizon,setMetaHorizon]= useState('')

  // 修改后录入展开区
  const [editOpen,        setEditOpen]        = useState(false)
  const [editThesis,      setEditThesis]      = useState('')
  const [editAction,      setEditAction]      = useState('')
  const [editInvalidation,setEditInvalidation]= useState('')

  const [approvingId,    setApprovingId]    = useState<number | null>(null)
  const [reparsingDocId, setReparsingDocId] = useState<number | null>(null)

  // ── Tab 2 — 观点库 ──
  const [query,          setQuery]          = useState('')
  const [filterValidity, setFilterValidity] = useState('all')
  const [filterStance,   setFilterStance]   = useState('all')
  const [filterHorizon,  setFilterHorizon]  = useState('all')
  const [filterObject,   setFilterObject]   = useState('')
  const [modalOpen,      setModalOpen]      = useState(false)
  const [editTarget,     setEditTarget]     = useState<Viewpoint | null>(null)
  const [formSaving,     setFormSaving]     = useState(false)
  const [formError,      setFormError]      = useState<string | null>(null)

  // ── Tab 3 — 决策检索 ──
  const [searchQuery,   setSearchQuery]   = useState('')
  const [searchObject,  setSearchObject]  = useState('')
  const [searchLimit,   setSearchLimit]   = useState(5)
  const [searchExpired, setSearchExpired] = useState(false)
  const [searching,     setSearching]     = useState(false)
  const [searchResults, setSearchResults] = useState<Viewpoint[]>([])
  const [searchDone,    setSearchDone]    = useState(false)

  // ── v2 状态 ──
  const [v2Cards,       setV2Cards]       = useState<ViewpointCardV2[]>([])
  const [v2Loading,     setV2Loading]     = useState(false)
  const [v2Holdings,    setV2Holdings]    = useState<{symbol: string | null; asset_name: string; market: string | null; supported: boolean; weight: number}[]>([])
  const [v2Selected,    setV2Selected]    = useState<Set<string>>(new Set())
  const [v2ManualSym,   setV2ManualSym]   = useState('')
  const [v2Fetching,    setV2Fetching]    = useState(false)
  const [v2FetchMsg,    setV2FetchMsg]    = useState('')
  const [v2FetchProgress, setV2FetchProgress] = useState('')
  const [v2ReviewCard,  setV2ReviewCard]  = useState<ViewpointCardV2 | null>(null)
  const [v2JudgEdit,    setV2JudgEdit]    = useState<Record<string, string>>({})
  const [v2Confirming,  setV2Confirming]  = useState(false)
  const [v2DiscardedId, setV2DiscardedId] = useState<string | null>(null)
  // 批量操作
  const [v2SelectedIds, setV2SelectedIds] = useState<Set<string>>(new Set())
  const [v2BatchBusy,   setV2BatchBusy]   = useState(false)
  const [v2BatchMsg,    setV2BatchMsg]    = useState('')
  // 观点库批量
  const [v2LibSelectedIds, setV2LibSelectedIds] = useState<Set<string>>(new Set())
  const [v2LibBatchBusy,   setV2LibBatchBusy]   = useState(false)
  const [v2LibBatchMsg,    setV2LibBatchMsg]    = useState('')
  // 文档详情弹窗
  const [docDetail,        setDocDetail]        = useState<ResearchDocument | null>(null)
  const [docEditFields,    setDocEditFields]    = useState<Record<string, string>>({})
  const [docSaving,        setDocSaving]        = useState(false)
  // v2 Tab 3
  const [v2SearchSymbol, setV2SearchSymbol] = useState('')
  const [v2SearchLines,  setV2SearchLines]  = useState<string[]>([])
  const [v2SearchCardCount, setV2SearchCardCount] = useState(0)
  const [v2Searching,    setV2Searching]    = useState(false)

  function loadV2Cards() {
    setV2Loading(true)
    researchV2Api.queryCards({ top_k: 50 })
      .then(res => setV2Cards(res.cards ?? []))
      .catch(() => {})
      .finally(() => setV2Loading(false))
  }

  function loadAll() {
    setLoading(true)
    Promise.all([
      researchApi.getViewpoints(),
      researchApi.getCards(),
      researchApi.getDocuments().catch(() => ({ items: [], total: 0 })),
    ])
      .then(([vp, c, d]) => {
        setViewpoints(vp.items)
        setCards(c.items)
        setDocuments(d.items)
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : '加载失败'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadAll(); loadV2Cards()
    researchV2Api.getHoldingsUS().then(h => setV2Holdings(h)).catch(() => {})
  }, [])

  // 初始化元数据编辑区
  function initMeta(result: ParseResult) {
    const c = result.card
    setMetaTitle(result.document_title)
    setMetaObject(c.document_object_name ?? '')
    setMetaMarket('')
    setMetaAuthor('')
    setMetaTime('')
    setMetaTags((c.suggested_tags ?? []).join(', '))
    setMetaStance(c.stance ?? '')
    setMetaHorizon(c.horizon ?? '')
    setEditThesis(c.thesis ?? '')
    setEditAction(c.action_suggestion ?? '')
    setEditInvalidation(c.invalidation_conditions ?? '')
    setEditOpen(false)
  }

  // ── 解析入口 ──
  async function handleParse() {
    setParsing(true); setParseResult(null); setParseError(null)
    try {
      let result: ParseResult
      if (sourceType === 'text') {
        if (!inputText.trim()) throw new Error('请输入研报内容')
        result = await researchApi.parseText(inputText, inputTitle)
      } else if (sourceType === 'markdown') {
        let content = inputText
        if (inputFile) content = await inputFile.text()
        if (!content.trim()) throw new Error('请上传 Markdown 文件或粘贴内容')
        result = await researchApi.parseText(content, inputTitle || inputFile?.name)
      } else if (sourceType === 'link') {
        if (!inputUrl.trim()) throw new Error('请输入链接地址')
        result = await researchApi.parseUrl(inputUrl)
      } else {
        if (!inputFile) throw new Error('请上传 PDF 文件')
        result = await researchApi.parsePdf(inputFile)
      }
      initMeta(result)
      setParseResult(result)
      setCards(prev => prev.find(c => c.id === result.card.id) ? prev : [result.card, ...prev])
      researchApi.getDocuments().then(d => setDocuments(d.items)).catch(() => {})
    } catch (e: unknown) {
      setParseError(e instanceof Error ? e.message : 'AI 解析失败，请稍后重试')
    } finally {
      setParsing(false)
    }
  }

  // ── 解析后操作 ──
  function buildApproveOverrides(approvalLevel: string): Record<string, unknown> {
    const ov: Record<string, unknown> = { user_approval_level: approvalLevel }
    if (metaTitle)  ov.title = metaTitle
    if (metaObject) ov.object_name = metaObject
    if (metaMarket) ov.market_name = metaMarket
    if (metaStance) ov.stance = metaStance
    if (metaHorizon) ov.horizon = metaHorizon
    return ov
  }

  async function handleApproveFromParse(approvalLevel: string) {
    if (!parseResult) return
    const card = parseResult.card
    setApprovingId(card.id)
    try {
      const ov = buildApproveOverrides(approvalLevel)
      if (approvalLevel !== 'strong') {
        if (editThesis)       ov.thesis = editThesis
        if (editAction)       ov.action_suggestion = editAction
        if (editInvalidation) ov.invalidation_conditions = editInvalidation
      }
      const vp = await researchApi.approveCard(card.id, ov)
      setViewpoints(prev => [vp, ...prev])
      setCards(prev => prev.filter(c => c.id !== card.id))
      setParseResult(null)
      researchApi.getDocuments().then(d => setDocuments(d.items)).catch(() => {})
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '录入失败')
    } finally {
      setApprovingId(null)
    }
  }

  function handleSaveOnly() {
    if (!parseResult) return
    // doc + card already saved; just close preview, card stays in pending list
    setParseResult(null)
  }

  async function handleDiscardParsed() {
    if (!parseResult) return
    if (!confirm('确定丢弃？文档和解析结果将一并删除。')) return
    try {
      await researchApi.deleteDocument(parseResult.document_id)
      setCards(prev => prev.filter(c => c.id !== parseResult.card.id))
      setDocuments(prev => prev.filter(d => d.id !== parseResult.document_id))
      setParseResult(null)
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '删除失败')
    }
  }

  // ── 待审核卡片操作 ──
  async function handleApproveCard(card: ResearchCard) {
    setApprovingId(card.id)
    try {
      const vp = await researchApi.approveCard(card.id, { user_approval_level: 'reference' })
      setViewpoints(prev => [vp, ...prev])
      setCards(prev => prev.filter(c => c.id !== card.id))
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '操作失败')
    } finally {
      setApprovingId(null)
    }
  }

  async function handleDiscardCard(card: ResearchCard) {
    if (!confirm('确定丢弃这张候选卡？')) return
    try {
      if (card.document_id) {
        await researchApi.deleteDocument(card.document_id)
        setDocuments(prev => prev.filter(d => d.id !== card.document_id))
      }
      setCards(prev => prev.filter(c => c.id !== card.id))
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '删除失败')
    }
  }

  async function handleDeleteDocument(id: number) {
    if (!confirm('确定删除此文档？关联候选卡也会一并删除。')) return
    try {
      await researchApi.deleteDocument(id)
      setDocuments(prev => prev.filter(d => d.id !== id))
      researchApi.getCards().then(c => setCards(c.items)).catch(() => {})
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '删除失败')
    }
  }

  async function handleReparse(doc: ResearchDocument) {
    setReparsingDocId(doc.id)
    try {
      const result = await researchApi.reparseDocument(doc.id)
      initMeta(result)
      setParseResult(result)
      setCards(prev => prev.find(c => c.id === result.card.id) ? prev : [result.card, ...prev])
      researchApi.getDocuments().then(d => setDocuments(d.items)).catch(() => {})
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '重新解析失败')
    } finally {
      setReparsingDocId(null)
    }
  }

  // ── 观点库 CRUD ──
  async function handleSave(data: ViewpointCreate) {
    setFormSaving(true); setFormError(null)
    try {
      if (editTarget) {
        const updated = await researchApi.updateViewpoint(editTarget.id, data)
        setViewpoints(prev => prev.map(v => v.id === updated.id ? updated : v))
      } else {
        const created = await researchApi.createViewpoint(data)
        setViewpoints(prev => [created, ...prev])
      }
      setModalOpen(false); setEditTarget(null)
    } catch (e: unknown) {
      setFormError(e instanceof Error ? e.message : '保存失败')
    } finally {
      setFormSaving(false)
    }
  }

  async function handleDelete(id: number) {
    if (!confirm('确定删除此观点？此操作不可撤销。')) return
    try {
      await researchApi.deleteViewpoint(id)
      setViewpoints(prev => prev.filter(v => v.id !== id))
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '删除失败')
    }
  }

  async function handleStatusChange(vp: Viewpoint, status: string) {
    try {
      const updated = await researchApi.updateViewpoint(vp.id, { validity_status: status })
      setViewpoints(prev => prev.map(v => v.id === updated.id ? updated : v))
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '更新失败')
    }
  }

  // ── 决策检索 ──
  async function handleSearch() {
    if (!searchQuery.trim()) return
    setSearching(true); setSearchDone(false)
    try {
      const q = [searchQuery, searchObject ? `标的:${searchObject}` : ''].filter(Boolean).join(' ')
      const res = await researchApi.getViewpoints(q)
      let items = res.items
      if (!searchExpired) items = items.filter(v => v.validity_status !== 'invalid')
      setSearchResults(items.slice(0, searchLimit))
      setSearchDone(true)
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '检索失败')
    } finally {
      setSearching(false)
    }
  }

  // ── 观点库过滤（v2 卡）──
  const filteredV2 = v2Cards.filter(c => {
    const q = query.toLowerCase()
    const thesis = c.narrative.thesis ?? ''
    const symbol = c.facts.primary_symbol ?? ''
    const matchQ = !q || thesis.toLowerCase().includes(q) || symbol.toLowerCase().includes(q)
    const matchV = filterValidity === 'all' || c.judgment.validity_status === filterValidity
    const matchS = filterStance === 'all' || c.judgment.stance === filterStance
    const matchH = filterHorizon === 'all' || c.judgment.horizon === filterHorizon
    const matchO = !filterObject || symbol.toLowerCase().includes(filterObject.toLowerCase())
    return matchQ && matchV && matchS && matchH && matchO
  })

  // ── v2 handlers ──
  async function handleV2Fetch() {
    const symbols = [...v2Selected]
    if (v2ManualSym.trim()) symbols.push(v2ManualSym.trim())
    if (symbols.length === 0) return

    setV2Fetching(true); setV2FetchMsg(''); setV2FetchProgress('')
    let totalCards = 0; let totalErrors = 0
    for (let i = 0; i < symbols.length; i++) {
      const sym = symbols[i]
      setV2FetchProgress(`处理中 (${i + 1}/${symbols.length}) — 正在拉取 ${sym}...`)
      try {
        const res = await researchV2Api.ingestAlphaVantage(sym)
        totalCards += res.cards?.length ?? 0
        totalErrors += res.errors?.length ?? 0
      } catch (err: unknown) {
        totalErrors++
        setV2FetchMsg(prev => prev + `\n${sym}: ${err instanceof Error ? err.message : '失败'}`)
      }
    }
    setV2FetchProgress('')
    setV2FetchMsg(`完成：${symbols.length} 个标的，生成 ${totalCards} 张待审核卡${totalErrors > 0 ? `，${totalErrors} 条处理失败` : ''}`)
    setV2ManualSym('')
    loadV2Cards()
    setV2Fetching(false)
  }

  function openV2Review(card: ViewpointCardV2) {
    setV2ReviewCard(card)
    setV2JudgEdit({
      stance: card.judgment.stance,
      confidence: card.judgment.confidence,
      user_endorsement: card.judgment.user_endorsement,
      action_type: card.judgment.action_type,
      horizon: card.judgment.horizon,
      trigger_conditions: card.judgment.trigger_conditions ?? '',
      invalidation_conditions: card.judgment.invalidation_conditions ?? '',
    })
  }

  async function handleV2Action(action: 'confirm' | 'unconfirm' | 'modify' | 'discard', endorsement?: string) {
    if (!v2ReviewCard) return
    setV2Confirming(true)
    try {
      const judgment: Record<string, unknown> = { ...v2JudgEdit }
      if (endorsement) judgment.user_endorsement = endorsement
      await researchV2Api.updateJudgment(v2ReviewCard.card_id, judgment, action === 'confirm', action)
      if (action === 'discard') {
        setV2DiscardedId(v2ReviewCard.card_id)
        setTimeout(() => setV2DiscardedId(null), 5000)
      }
      setV2ReviewCard(null)
      loadV2Cards()
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : '操作失败')
    } finally {
      setV2Confirming(false)
    }
  }

  async function handleV2Restore() {
    if (!v2DiscardedId) return
    try {
      await researchV2Api.updateJudgment(v2DiscardedId, {}, false, 'restore')
      setV2DiscardedId(null)
      loadV2Cards()
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : '恢复失败')
    }
  }

  // 切换持仓选项时自动触发检索
  const v2SearchSymbolRef = useRef(v2SearchSymbol)
  useEffect(() => {
    if (v2SearchSymbol && v2SearchSymbol !== v2SearchSymbolRef.current && activeTab === 'search') {
      v2SearchSymbolRef.current = v2SearchSymbol
      handleV2Search()
    }
  }, [v2SearchSymbol, activeTab])

  // 首次进入 Tab 3 时默认选中第一个有 symbol 的持仓
  useEffect(() => {
    if (activeTab === 'search' && !v2SearchSymbol && v2Holdings.length > 0) {
      const first = v2Holdings.find(h => h.symbol)
      if (first?.symbol) setV2SearchSymbol(first.symbol)
    }
  }, [activeTab, v2Holdings])

  async function handleV2Search() {
    if (!v2SearchSymbol.trim()) return
    setV2Searching(true)
    try {
      const res = await researchV2Api.queryCards({ symbol: v2SearchSymbol.trim(), render: true })
      setV2SearchLines(res.rendered ?? [])
      setV2SearchCardCount((res as Record<string, unknown>).card_count as number ?? 0)
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : '检索失败')
    } finally {
      setV2Searching(false)
    }
  }

  async function handleV2BatchAction(action: 'confirm' | 'discard') {
    const ids = [...v2SelectedIds]
    if (ids.length === 0) return
    setV2BatchBusy(true); setV2BatchMsg('')
    let ok = 0; let fail = 0
    for (let i = 0; i < ids.length; i++) {
      setV2BatchMsg(`正在处理 ${i + 1}/${ids.length}...`)
      try {
        await researchV2Api.updateJudgment(ids[i], {}, action === 'confirm', action)
        ok++
      } catch {
        fail++
      }
    }
    setV2BatchMsg(`已处理 ${ok} 张${fail > 0 ? `，${fail} 张失败` : ''}`)
    setV2SelectedIds(new Set())
    loadV2Cards()
    setV2BatchBusy(false)
    setTimeout(() => setV2BatchMsg(''), 4000)
  }

  async function handleV2LibBatchAction(action: 'confirm' | 'discard') {
    const ids = [...v2LibSelectedIds]
    if (ids.length === 0) return
    setV2LibBatchBusy(true); setV2LibBatchMsg('')
    let ok = 0; let skip = 0; let fail = 0
    for (let i = 0; i < ids.length; i++) {
      setV2LibBatchMsg(`正在处理 ${i + 1}/${ids.length}...`)
      const card = v2Cards.find(c => c.card_id === ids[i])
      // 批量 confirm 时，已 confirmed 的卡跳过
      if (action === 'confirm' && card && !card.judgment.is_ai_prefilled) {
        skip++; continue
      }
      try {
        await researchV2Api.updateJudgment(ids[i], {}, action === 'confirm', action)
        ok++
      } catch { fail++ }
    }
    const parts = [`已处理 ${ok} 张`]
    if (skip > 0) parts.push(`${skip} 张已确认跳过`)
    if (fail > 0) parts.push(`${fail} 张失败`)
    setV2LibBatchMsg(parts.join('，'))
    setV2LibSelectedIds(new Set())
    loadV2Cards()
    setV2LibBatchBusy(false)
    setTimeout(() => setV2LibBatchMsg(''), 4000)
  }

  function toggleV2LibSelect(cardId: string) {
    setV2LibSelectedIds(prev => {
      const next = new Set(prev)
      next.has(cardId) ? next.delete(cardId) : next.add(cardId)
      return next
    })
  }

  function toggleV2Select(cardId: string) {
    setV2SelectedIds(prev => {
      const next = new Set(prev)
      next.has(cardId) ? next.delete(cardId) : next.add(cardId)
      return next
    })
  }

  function openDocDetail(doc: ResearchDocument) {
    setDocDetail(doc)
    setDocEditFields({
      title: doc.title || '',
      object_name: doc.object_name || '',
      market_name: doc.market_name || '',
      author: doc.author || '',
      publish_time: doc.publish_time || '',
    })
  }

  async function saveDocDetail() {
    if (!docDetail) return
    setDocSaving(true)
    try {
      const updated = await researchApi.updateDocument(docDetail.id, docEditFields)
      setDocuments(prev => prev.map(d => d.id === docDetail.id ? updated : d))
      setDocDetail(null)
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '保存失败')
    } finally {
      setDocSaving(false)
    }
  }

  const v2Pending = v2Cards.filter(c => c.judgment.is_ai_prefilled && c.judgment.validity_status !== 'invalidated')
  const v2Confirmed = v2Cards.filter(c => !c.judgment.is_ai_prefilled && c.judgment.user_endorsement !== 'disagree' && c.judgment.validity_status !== 'invalidated')

  // 待审核（不含刚解析的那张）
  const pendingCards = cards.filter(c => !c.is_approved && c.id !== parseResult?.card.id)

  // 解析按钮是否可用
  const canParse = !parsing && (
    (sourceType === 'text' && inputText.trim().length > 0) ||
    (sourceType === 'markdown' && (inputText.trim().length > 0 || inputFile !== null)) ||
    (sourceType === 'link' && inputUrl.trim().length > 0) ||
    (sourceType === 'pdf' && inputFile !== null)
  )

  function clearImport() {
    setInputText(''); setInputTitle(''); setInputUrl(''); setInputFile(null)
    setParseResult(null); setParseError(null)
    if (mdFileRef.current)  mdFileRef.current.value = ''
    if (pdfFileRef.current) pdfFileRef.current.value = ''
  }

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

  return (
    <div>
      {/* ── 页面标题 ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <div style={{
          width: 38, height: 38, borderRadius: 10,
          background: 'linear-gradient(135deg, #1B2A4A, #2D4A7A)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 17,
        }}>🔬</div>
        <div>
          <div style={{ fontSize: 20, fontWeight: 700, color: '#1B2A4A', letterSpacing: -0.3 }}>投研观点</div>
          <div style={{ fontSize: 12, color: '#9CA3AF', marginTop: 1 }}>资料导入 · AI提炼 · 观点审核 · 观点库 · 决策检索</div>
        </div>
      </div>

      {/* ── Tab 导航 ── */}
      <div style={{ display: 'flex', gap: 0, marginBottom: 16, borderBottom: '2px solid #E5E7EB' }}>
        {([
          { key: 'import',  label: '资料导入', badge: pendingCards.length > 0 ? pendingCards.length : null },
          { key: 'library', label: '观点库',   badge: v2Cards.length > 0 ? v2Cards.length : null },
          { key: 'search',  label: '决策检索', badge: null },
        ] as const).map(tab => (
          <button key={tab.key} onClick={() => setActiveTab(tab.key)}
            style={{
              background: 'none', border: 'none', cursor: 'pointer',
              padding: '10px 20px', fontSize: 13, fontWeight: 500,
              color: activeTab === tab.key ? '#1D4ED8' : '#6B7280',
              borderBottom: `2px solid ${activeTab === tab.key ? '#1D4ED8' : 'transparent'}`,
              marginBottom: -2, display: 'inline-flex', alignItems: 'center', gap: 6,
              transition: 'color 0.15s',
            }}>
            {tab.label}
            {tab.badge !== null && (
              <span style={{
                fontSize: 10, fontWeight: 600, padding: '1px 6px', borderRadius: 10,
                background: activeTab === tab.key ? '#EFF6FF' : '#F3F4F6',
                color: activeTab === tab.key ? '#1D4ED8' : '#9CA3AF',
              }}>{tab.badge}</span>
            )}
          </button>
        ))}
      </div>

      {/* ══════════ Tab 1：资料导入 ══════════ */}
      {activeTab === 'import' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

          {/* ── AI 解析区 ── */}
          <div style={{ ...S.card, overflow: 'hidden', padding: 0 }}>

            {/* 左右两栏 */}
            <div style={{ display: 'flex', flexDirection: 'row', alignItems: 'stretch' }}>

              {/* 左半区：输入 */}
              <div style={{ width: '40%', padding: 20, display: 'flex', flexDirection: 'column', gap: 12, boxSizing: 'border-box' }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#374151', display: 'flex', alignItems: 'center', gap: 6 }}>
                  <Sparkles size={14} style={{ color: '#7C3AED' }} /> AI 研报解析
                </div>

                {/* 来源类型选择 */}
                <div style={{ display: 'flex', flexWrap: 'nowrap', gap: 6 }}>
                  {SOURCE_TYPES.map(({ key, label, icon }) => (
                    <button key={key}
                      onClick={() => { setSourceType(key); setParseResult(null); setParseError(null); setInputFile(null) }}
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: 5,
                        padding: '5px 12px', borderRadius: 6, fontSize: 12, fontWeight: 500, cursor: 'pointer',
                        whiteSpace: 'nowrap',
                        border: `1px solid ${sourceType === key ? '#BFDBFE' : '#E5E7EB'}`,
                        background: sourceType === key ? '#EFF6FF' : '#fff',
                        color: sourceType === key ? '#1D4ED8' : '#6B7280',
                      }}>
                      {icon}{label}
                    </button>
                  ))}
                </div>

                {/* 纯文本 */}
                {sourceType === 'text' && (
                  <>
                    <div>
                      <label style={S.label}>来源标题（可选）</label>
                      <input style={S.input} value={inputTitle} onChange={e => setInputTitle(e.target.value)} placeholder="研报标题、文章名…" />
                    </div>
                    <div style={{ flex: 1 }}>
                      <label style={S.label}>研报 / 文章内容</label>
                      <textarea value={inputText} onChange={e => setInputText(e.target.value)}
                        placeholder="粘贴研报、文章、分析内容…" rows={10} style={S.textarea} />
                    </div>
                  </>
                )}

                {/* Markdown */}
                {sourceType === 'markdown' && (
                  <>
                    <div>
                      <label style={S.label}>上传 Markdown 文件（.md / .txt）</label>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <label style={{ ...S.btnSecondary, cursor: 'pointer' }}>
                          <FileText size={12} /> 选择文件
                          <input ref={mdFileRef} type="file" accept=".md,.markdown,.txt" style={{ display: 'none' }}
                            onChange={e => { setInputFile(e.target.files?.[0] ?? null); setInputText('') }} />
                        </label>
                        {inputFile && <span style={{ fontSize: 12, color: '#059669' }}>✓ {inputFile.name}</span>}
                      </div>
                    </div>
                    <div style={{ flex: 1 }}>
                      <label style={S.label}>或直接粘贴 Markdown 内容</label>
                      <textarea value={inputText} onChange={e => { setInputText(e.target.value); setInputFile(null) }}
                        placeholder="也可不上传文件，直接粘贴内容…" rows={8} style={S.textarea} />
                    </div>
                  </>
                )}

                {/* 链接 URL */}
                {sourceType === 'link' && (
                  <>
                    <div>
                      <label style={S.label}>链接地址</label>
                      <input style={S.input} value={inputUrl} onChange={e => setInputUrl(e.target.value)}
                        placeholder="https://mp.weixin.qq.com/s/..." />
                    </div>
                    <div style={{ fontSize: 12, color: '#9CA3AF', lineHeight: 1.6 }}>
                      系统将自动抓取链接正文并 AI 解析。若无法抓取（需登录的页面），请切换到「纯文本粘贴」方式手动粘贴内容。
                    </div>
                  </>
                )}

                {/* PDF */}
                {sourceType === 'pdf' && (
                  <>
                    <div>
                      <label style={S.label}>上传 PDF 文件</label>
                      <label style={{ ...S.btnSecondary, cursor: 'pointer', display: 'inline-flex' }}>
                        <FileText size={12} /> 选择 PDF
                        <input ref={pdfFileRef} type="file" accept=".pdf" style={{ display: 'none' }}
                          onChange={e => setInputFile(e.target.files?.[0] ?? null)} />
                      </label>
                      {inputFile && (
                        <div style={{ fontSize: 12, color: '#059669', marginTop: 6 }}>✓ {inputFile.name}</div>
                      )}
                    </div>
                    <div style={{ fontSize: 12, color: '#9CA3AF', lineHeight: 1.6 }}>
                      支持文字版 PDF（最多 20 页）。扫描件图片 PDF 无法提取文字，请手动粘贴关键段落。
                    </div>
                  </>
                )}

                {/* 解析按钮 */}
                <div style={{ display: 'flex', gap: 8 }}>
                  <button
                    style={{ ...S.btnPrimary, background: 'linear-gradient(135deg, #7C3AED, #4F46E5)', opacity: canParse ? 1 : 0.55 }}
                    disabled={!canParse} onClick={handleParse}>
                    {parsing ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
                    {parsing ? 'AI 解析中…' : 'AI 解析'}
                  </button>
                  {(inputText || inputFile || inputUrl || parseResult) && (
                    <button style={S.btnSecondary} onClick={clearImport}>清空</button>
                  )}
                </div>
              </div>

              {/* 分割线 */}
              <div style={{ width: 1, background: '#E5E7EB', flexShrink: 0, alignSelf: 'stretch' }} />

              {/* 右半区：预览 */}
              <div style={{ width: '60%', padding: 20, display: 'flex', flexDirection: 'column', gap: 10, boxSizing: 'border-box' }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#374151', display: 'flex', alignItems: 'center', gap: 6 }}>
                  <Sparkles size={14} style={{ color: '#7C3AED' }} /> 解析结果预览
                </div>
                {parseError && (
                  <div style={{ background: '#FEE2E2', borderRadius: 8, padding: '10px 12px', fontSize: 12, color: '#DC2626', display: 'flex', gap: 6 }}>
                    <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} />{parseError}
                  </div>
                )}
                {parseResult ? (
                  <ParseCardPreview card={parseResult.card} />
                ) : (
                  <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: '#9CA3AF', fontSize: 13, gap: 8, paddingTop: 40 }}>
                    <Sparkles size={26} strokeWidth={1.5} style={{ opacity: 0.3 }} />
                    <span>粘贴内容后点击「AI 解析」</span>
                  </div>
                )}
              </div>
            </div>

            {/* ── 解析完成后：元数据编辑 + 操作区（全宽） ── */}
            {parseResult && (
              <div style={{ borderTop: '1px solid #E5E7EB', padding: '16px 20px' }}>

                {/* 元数据编辑行 */}
                <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr 1fr 1fr 1.5fr 1fr 1fr', gap: 8, marginBottom: 14 }}>
                  <div>
                    <label style={S.label}>标题 *</label>
                    <input style={S.input} value={metaTitle} onChange={e => setMetaTitle(e.target.value)} />
                  </div>
                  <div>
                    <label style={S.label}>标的名称</label>
                    <input style={S.input} value={metaObject} onChange={e => setMetaObject(e.target.value)} />
                  </div>
                  <div>
                    <label style={S.label}>市场</label>
                    <select style={{ ...S.select, width: '100%' }} value={metaMarket} onChange={e => setMetaMarket(e.target.value)}>
                      <option value="">—</option>
                      <option value="A股">A股</option><option value="港股">港股</option>
                      <option value="美股">美股</option><option value="宏观">宏观</option>
                      <option value="行业">行业</option><option value="其他">其他</option>
                    </select>
                  </div>
                  <div>
                    <label style={S.label}>作者/来源</label>
                    <input style={S.input} value={metaAuthor} onChange={e => setMetaAuthor(e.target.value)} />
                  </div>
                  <div>
                    <label style={S.label}>发布时间</label>
                    <input style={S.input} value={metaTime} onChange={e => setMetaTime(e.target.value)} placeholder="2024-01" />
                  </div>
                  <div>
                    <label style={S.label}>标签（逗号分隔）</label>
                    <input style={S.input} value={metaTags} onChange={e => setMetaTags(e.target.value)} placeholder="如：科技,AI,成长" />
                  </div>
                  <div>
                    <label style={S.label}>立场</label>
                    <select style={{ ...S.select, width: '100%' }} value={metaStance} onChange={e => setMetaStance(e.target.value)}>
                      <option value="">未设置</option>
                      <option value="bullish">做多</option><option value="bearish">做空</option><option value="neutral">中性</option>
                    </select>
                  </div>
                  <div>
                    <label style={S.label}>时间维度</label>
                    <select style={{ ...S.select, width: '100%' }} value={metaHorizon} onChange={e => setMetaHorizon(e.target.value)}>
                      <option value="">未设置</option>
                      <option value="短期">短期</option><option value="中期">中期</option>
                      <option value="长期">长期</option><option value="不限">不限</option>
                    </select>
                  </div>
                </div>

                {/* 4个操作按钮 */}
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <button
                    style={{ ...S.btnPrimary, opacity: approvingId === parseResult.card.id ? 0.6 : 1 }}
                    disabled={approvingId === parseResult.card.id || !metaTitle.trim()}
                    onClick={() => handleApproveFromParse('strong')}>
                    {approvingId === parseResult.card.id ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                    认可·直接录入
                  </button>
                  <button style={{ ...S.btnSecondary, background: editOpen ? '#EFF6FF' : '#fff', color: editOpen ? '#1D4ED8' : '#374151' }}
                    onClick={() => setEditOpen(v => !v)}>
                    <Pencil size={12} /> 修改后录入
                  </button>
                  <button style={S.btnSecondary} onClick={handleSaveOnly}>
                    <FileText size={12} /> 仅保留资料
                  </button>
                  <button style={S.btnDanger} onClick={handleDiscardParsed}>
                    <Trash2 size={12} /> 丢弃
                  </button>
                </div>

                {/* 修改后录入 — 展开编辑区 */}
                {editOpen && (
                  <div style={{ marginTop: 14, background: '#F8FAFC', border: '1px solid #E5E7EB', borderRadius: 10, padding: '14px 16px' }}>
                    <div style={{ fontSize: 12, fontWeight: 500, color: '#374151', marginBottom: 10 }}>修改观点内容后录入</div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                      <div>
                        <label style={S.label}>核心结论</label>
                        <textarea rows={5} style={S.textarea} value={editThesis} onChange={e => setEditThesis(e.target.value)} />
                      </div>
                      <div>
                        <label style={S.label}>操作建议</label>
                        <textarea rows={5} style={S.textarea} value={editAction} onChange={e => setEditAction(e.target.value)} />
                      </div>
                      <div>
                        <label style={S.label}>失效条件</label>
                        <textarea rows={5} style={S.textarea} value={editInvalidation} onChange={e => setEditInvalidation(e.target.value)} />
                      </div>
                    </div>
                    <div style={{ marginTop: 12 }}>
                      <button
                        style={{ ...S.btnPrimary, opacity: approvingId === parseResult.card.id ? 0.6 : 1 }}
                        disabled={approvingId === parseResult.card.id || !metaTitle.trim()}
                        onClick={() => handleApproveFromParse('reference')}>
                        {approvingId === parseResult.card.id ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                        确认录入观点库
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* ── 待审核候选卡列表 ── */}
          {pendingCards.length > 0 && (
            <div style={{ ...S.card, padding: '20px 24px' }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
                待审核观点卡
                <span style={{ fontSize: 10, fontWeight: 600, padding: '2px 7px', borderRadius: 10, background: '#EDE9FE', color: '#7C3AED' }}>{pendingCards.length}</span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {pendingCards.map(c => (
                  <CandidateCard key={c.id} card={c} approving={approvingId === c.id}
                    onApprove={() => handleApproveCard(c)}
                    onDiscard={() => handleDiscardCard(c)} />
                ))}
              </div>
            </div>
          )}

          {/* ── 已导入资料列表 ── */}
          <div style={{ ...S.card, padding: '20px 24px' }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
              <FileText size={14} style={{ color: '#6B7280' }} /> 已导入资料
              <span style={{ fontSize: 11, color: '#9CA3AF', fontWeight: 400 }}>（{documents.length} 份）</span>
            </div>
            {documents.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '30px 0', color: '#9CA3AF', fontSize: 13 }}>暂无已导入资料</div>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid #F3F4F6' }}>
                      {['标题', '类型', '标的', '市场', '状态', '上传时间', ''].map(h => (
                        <th key={h} style={{ textAlign: 'left', padding: '6px 10px', fontSize: 11, fontWeight: 600, color: '#9CA3AF', whiteSpace: 'nowrap' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {documents.map(doc => {
                      const st = DOC_STATUS[doc.parse_status ?? ''] ?? { label: doc.parse_status ?? '—', bg: '#F3F4F6', color: '#9CA3AF' }
                      const canReparse = doc.parse_status === 'pending' || doc.parse_status === 'saved_only'
                      return (
                        <tr key={doc.id} style={{ borderBottom: '1px solid #F9FAFB', cursor: 'pointer' }} onClick={() => openDocDetail(doc)}>
                          <td style={{ padding: '8px 10px', maxWidth: 220 }}>
                            <div style={{ fontWeight: 500, color: '#1B2A4A', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{doc.title || '（无标题）'}</div>
                          </td>
                          <td style={{ padding: '8px 10px', color: '#6B7280', whiteSpace: 'nowrap' }}>{doc.source_type ?? '—'}</td>
                          <td style={{ padding: '8px 10px', color: '#6B7280', whiteSpace: 'nowrap' }}>{doc.object_name ?? '—'}</td>
                          <td style={{ padding: '8px 10px', color: '#6B7280', whiteSpace: 'nowrap' }}>{doc.market_name ?? '—'}</td>
                          <td style={{ padding: '8px 10px' }}>
                            <span style={{ fontSize: 10, fontWeight: 500, padding: '2px 7px', borderRadius: 10, background: st.bg, color: st.color }}>{st.label}</span>
                          </td>
                          <td style={{ padding: '8px 10px', color: '#9CA3AF', whiteSpace: 'nowrap' }}>
                            {doc.uploaded_at ? new Date(doc.uploaded_at).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit', year: '2-digit' }) : '—'}
                          </td>
                          <td style={{ padding: '8px 10px', whiteSpace: 'nowrap' }}>
                            <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                              {canReparse && (
                                <button
                                  title="重新解析"
                                  disabled={reparsingDocId === doc.id}
                                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#7C3AED', padding: 4, opacity: reparsingDocId === doc.id ? 0.5 : 1 }}
                                  onClick={e => { e.stopPropagation(); handleReparse(doc) }}>
                                  {reparsingDocId === doc.id ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                                </button>
                              )}
                              <button style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#9CA3AF', padding: 4 }}
                                onClick={e => { e.stopPropagation(); handleDeleteDocument(doc.id) }}>
                                <Trash2 size={13} />
                              </button>
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* ══════════ v2: 自动拉取资讯 ══════════ */}
          <div style={{ ...S.card, padding: '16px 20px' }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
              <Sparkles size={14} style={{ color: '#F59E0B' }} /> 自动拉取资讯 (Alpha Vantage v2)
            </div>

            {v2Holdings.length > 0 && (
              <div style={{ marginBottom: 10 }}>
                <label style={S.label}>选择持仓标的（可多选，当前支持美股）</label>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 220, overflowY: 'auto', border: '1px solid #E5E7EB', borderRadius: 8, padding: '8px 10px' }}>
                  {v2Holdings.filter(h => h.supported).map(h => (
                    <label key={h.symbol!} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: '#374151', cursor: 'pointer' }}>
                      <input type="checkbox"
                        checked={v2Selected.has(h.symbol!)}
                        onChange={e => {
                          const next = new Set(v2Selected)
                          e.target.checked ? next.add(h.symbol!) : next.delete(h.symbol!)
                          setV2Selected(next)
                        }} />
                      <span style={{ flex: 1 }}>{h.asset_name}</span>
                      <span style={{ color: '#9CA3AF', fontSize: 11 }}>{h.symbol}</span>
                      <span style={{ color: '#9CA3AF', fontSize: 11, minWidth: 40, textAlign: 'right' }}>{(h.weight * 100).toFixed(1)}%</span>
                    </label>
                  ))}
                </div>
                <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                  <button style={{ ...S.btnSecondary, fontSize: 11, padding: '4px 10px' }}
                    onClick={() => setV2Selected(new Set(v2Holdings.filter(h => h.supported && h.symbol).map(h => h.symbol!)))}>全选</button>
                  <button style={{ ...S.btnSecondary, fontSize: 11, padding: '4px 10px' }}
                    onClick={() => setV2Selected(new Set())}>清空</button>
                </div>
              </div>
            )}

            <div style={{ marginBottom: 8 }}>
              <label style={S.label}>或手动输入（用于持仓外探索）</label>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <input style={{ ...S.input, maxWidth: 200 }} value={v2ManualSym} onChange={e => setV2ManualSym(e.target.value)} placeholder="如 META:US" />
              </div>
            </div>

            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <button style={{ ...S.btnPrimary, opacity: v2Fetching || (v2Selected.size === 0 && !v2ManualSym.trim()) ? 0.6 : 1 }}
                disabled={v2Fetching || (v2Selected.size === 0 && !v2ManualSym.trim())} onClick={handleV2Fetch}>
                {v2Fetching ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />} 刷新已选
              </button>
              {(v2Selected.size > 0 || v2ManualSym.trim()) && !v2Fetching && (
                <span style={{ fontSize: 11, color: '#9CA3AF' }}>
                  {v2Selected.size + (v2ManualSym.trim() ? 1 : 0)} 个标的，约需 {(v2Selected.size + (v2ManualSym.trim() ? 1 : 0)) * 40} 秒
                </span>
              )}
            </div>

            {v2FetchProgress && <div style={{ fontSize: 12, color: '#3B82F6', marginTop: 6 }}>{v2FetchProgress}</div>}
            {v2FetchMsg && <div style={{ fontSize: 12, color: '#6B7280', marginTop: 4, whiteSpace: 'pre-line' }}>{v2FetchMsg}</div>}
          </div>

          {/* ══════════ v2: 待审核卡列表 ══════════ */}
          {v2Pending.length > 0 && (
            <div style={{ ...S.card, padding: '16px 20px' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#374151' }}>
                  v2 待审核观点卡 ({v2Pending.length})
                </div>
                <div style={{ display: 'flex', gap: 6 }}>
                  <button style={{ ...S.btnSecondary, fontSize: 11, padding: '3px 10px' }}
                    onClick={() => setV2SelectedIds(new Set(v2Pending.map(c => c.card_id)))}>全选</button>
                  <button style={{ ...S.btnSecondary, fontSize: 11, padding: '3px 10px' }}
                    onClick={() => setV2SelectedIds(new Set())}>清空</button>
                </div>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {v2Pending.map(card => (
                  <div key={card.card_id} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, background: '#FFF7ED', border: v2SelectedIds.has(card.card_id) ? '2px solid #3B82F6' : '1px solid #FED7AA', borderRadius: 8, padding: '10px 14px' }}>
                    <input type="checkbox" checked={v2SelectedIds.has(card.card_id)}
                      onChange={() => toggleV2Select(card.card_id)}
                      style={{ marginTop: 3, cursor: 'pointer', flexShrink: 0 }} />
                    <div style={{ flex: 1, cursor: 'pointer' }} onClick={() => openV2Review(card)}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                        <span style={{ fontSize: 10, background: '#FEE2E2', color: '#DC2626', padding: '1px 6px', borderRadius: 4 }}>待确认</span>
                        <span style={{ fontSize: 10, background: '#EFF6FF', color: '#3B82F6', padding: '1px 6px', borderRadius: 4 }}>{SOURCE_TYPE_LABEL[card.facts.source_type] ?? card.facts.source_type}</span>
                        <span style={{ fontSize: 10, background: '#F3F4F6', color: '#6B7280', padding: '1px 6px', borderRadius: 4 }}>{card.narrative.event_type}</span>
                        {card.facts.primary_symbol && <span style={{ fontSize: 10, color: '#6B7280' }}>{card.facts.primary_symbol}</span>}
                      </div>
                      <div style={{ fontSize: 12, color: '#374151', lineHeight: 1.5 }}>{card.narrative.thesis ?? '(无论点)'}</div>
                    </div>
                  </div>
                ))}
              </div>

              {/* 批量操作栏（sticky bottom） */}
              {v2SelectedIds.size > 0 && (
                <div style={{ position: 'sticky', bottom: 0, marginTop: 12, background: '#fff', borderRadius: 10, padding: '10px 16px',
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderTop: '1px solid #E5E7EB', boxShadow: '0 -4px 12px rgba(0,0,0,0.08)' }}>
                  <span style={{ fontSize: 13, color: '#374151', fontWeight: 500 }}>已选 {v2SelectedIds.size} 张</span>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button disabled={v2BatchBusy} onClick={() => handleV2BatchAction('discard')}
                      style={{ background: '#FEE2E2', color: '#DC2626', border: 'none', borderRadius: 6, padding: '6px 14px', fontSize: 12, cursor: 'pointer', fontWeight: 500, opacity: v2BatchBusy ? 0.6 : 1 }}>
                      <Trash2 size={12} style={{ verticalAlign: -2, marginRight: 4 }} />批量丢弃
                    </button>
                    <button disabled={v2BatchBusy} onClick={() => handleV2BatchAction('confirm')}
                      style={{ background: '#3B82F6', color: '#fff', border: 'none', borderRadius: 6, padding: '6px 14px', fontSize: 12, cursor: 'pointer', fontWeight: 500, opacity: v2BatchBusy ? 0.6 : 1 }}>
                      <Check size={12} style={{ verticalAlign: -2, marginRight: 4 }} />批量确认并入库
                    </button>
                  </div>
                </div>
              )}
              {v2BatchMsg && (
                <div style={{ marginTop: 8, fontSize: 12, color: '#3B82F6', textAlign: 'center' }}>{v2BatchMsg}</div>
              )}
            </div>
          )}

          {/* ══════════ v2: 已审核卡列表 ══════════ */}
          {v2Confirmed.length > 0 && (
            <div style={{ ...S.card, padding: '16px 20px' }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 12 }}>
                v2 已确认观点卡 ({v2Confirmed.length})
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {v2Confirmed.map(card => (
                  <div key={card.card_id} onClick={() => openV2Review(card)}
                    style={{ background: '#F0FDF4', border: '1px solid #BBF7D0', borderRadius: 8, padding: '10px 14px', cursor: 'pointer' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                      <span style={{ fontSize: 10, background: '#D1FAE5', color: '#059669', padding: '1px 6px', borderRadius: 4 }}>已确认</span>
                      {stanceBadge(card.judgment.stance)}
                      <span style={{ fontSize: 10, color: '#6B7280' }}>{card.facts.primary_symbol}</span>
                    </div>
                    <div style={{ fontSize: 12, color: '#374151' }}>{card.narrative.thesis ?? ''}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ══════════ v2: 丢弃撤销 Toast ══════════ */}
          {v2DiscardedId && (
            <div style={{ position: 'fixed', bottom: 24, left: '50%', transform: 'translateX(-50%)', zIndex: 1100,
              background: '#1F2937', color: '#fff', borderRadius: 10, padding: '10px 20px', fontSize: 13,
              display: 'flex', alignItems: 'center', gap: 12, boxShadow: '0 4px 12px rgba(0,0,0,0.3)' }}>
              <span>观点卡已丢弃</span>
              <button onClick={handleV2Restore}
                style={{ background: '#3B82F6', color: '#fff', border: 'none', borderRadius: 6, padding: '4px 12px', fontSize: 12, cursor: 'pointer', fontWeight: 500 }}>
                撤销
              </button>
              <button onClick={() => setV2DiscardedId(null)}
                style={{ background: 'none', border: 'none', color: '#9CA3AF', cursor: 'pointer', fontSize: 14 }}>✕</button>
            </div>
          )}

        </div>
      )}

      {/* ══════════ Tab 2：观点库 ══════════ */}
      {activeTab === 'library' && (
        <div style={{ ...S.card, padding: '20px 24px' }}>
          {/* 多维筛选器 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: '1 1 200px', minWidth: 160, border: '1px solid #E5E7EB', borderRadius: 8, padding: '6px 10px', background: '#fff' }}>
              <Search size={13} color="#9CA3AF" />
              <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索标题、标的、论点…"
                style={{ border: 'none', outline: 'none', flex: 1, fontSize: 12, color: '#374151', background: 'transparent', fontFamily: 'inherit' }} />
            </div>
            <input value={filterObject} onChange={e => setFilterObject(e.target.value)} placeholder="标的 symbol"
              style={{ ...S.select, width: 100 }} />
            <select value={filterHorizon} onChange={e => setFilterHorizon(e.target.value)} style={S.select}>
              <option value="all">全部维度</option>
              <option value="short">短期</option><option value="medium">中期</option><option value="long">长期</option>
            </select>
            <select value={filterValidity} onChange={e => setFilterValidity(e.target.value)} style={S.select}>
              <option value="all">全部状态</option>
              <option value="active">有效</option><option value="expired">已过期</option><option value="invalidated">已失效</option>
            </select>
            <select value={filterStance} onChange={e => setFilterStance(e.target.value)} style={S.select}>
              <option value="all">全部立场</option>
              <option value="bullish">做多</option><option value="bearish">做空</option><option value="neutral">中性</option><option value="watch">观察</option>
            </select>
            <span style={{ fontSize: 12, color: '#9CA3AF' }}>{filteredV2.length} 条</span>
            <button style={{ ...S.btnSecondary, fontSize: 11, padding: '3px 10px' }}
              onClick={() => setV2LibSelectedIds(new Set(filteredV2.map(c => c.card_id)))}>全选</button>
            <button style={{ ...S.btnSecondary, fontSize: 11, padding: '3px 10px' }}
              onClick={() => setV2LibSelectedIds(new Set())}>清空</button>
          </div>

          {filteredV2.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '40px 0', color: '#9CA3AF', fontSize: 13 }}>
              {v2Cards.length === 0 ? '暂无 v2 观点卡，请在「资料导入」Tab 拉取或上传' : '没有符合条件的观点'}
            </div>
          ) : (
            <div>
              <div style={{
                display: 'grid', gridTemplateColumns: '28px 2fr 70px 60px 80px 80px 90px',
                gap: 8, padding: '6px 10px',
                fontSize: 11, fontWeight: 600, color: '#9CA3AF', textTransform: 'uppercase',
                borderBottom: '1px solid #F3F4F6',
              }}>
                <span></span><span>论点 / 标的</span><span>立场</span><span>维度</span><span>状态</span><span>来源</span><span>时间</span>
              </div>
              {filteredV2.map(card => {
                const j = card.judgment
                const isPending = j.is_ai_prefilled
                const HORIZON_CN: Record<string, string> = { short: '短期', medium: '中期', long: '长期' }
                const ENDORSEMENT_CN: Record<string, string> = { endorse: '认可', reference_only: '参考', disagree: '反对' }
                const VALIDITY_CN: Record<string, { label: string; bg: string; color: string }> = {
                  active: { label: '有效', bg: '#D1FAE5', color: '#059669' },
                  expired: { label: '过期', bg: '#FEF3C7', color: '#D97706' },
                  invalidated: { label: '失效', bg: '#F3F4F6', color: '#9CA3AF' },
                }
                const SOURCE_SHORT: Record<string, string> = {
                  user_upload: '用户上传', alpha_vantage_news: '公开新闻',
                  alpha_vantage_fundamental: '基本面', alpha_vantage_earnings: '财报',
                  perplexity_search: '联网', hybrid: '混合',
                }
                const vBadge = VALIDITY_CN[j.validity_status] ?? VALIDITY_CN.active
                return (
                  <div key={card.card_id}
                    style={{
                      display: 'grid', gridTemplateColumns: '28px 2fr 70px 60px 80px 80px 90px',
                      gap: 8, padding: '8px 10px', alignItems: 'center',
                      borderBottom: '1px solid #F9FAFB',
                      border: v2LibSelectedIds.has(card.card_id) ? '2px solid #3B82F6' : undefined,
                      borderRadius: v2LibSelectedIds.has(card.card_id) ? 6 : 0,
                      background: isPending ? '#FFFBEB' : 'transparent',
                    }}>
                    <input type="checkbox" checked={v2LibSelectedIds.has(card.card_id)}
                      onChange={() => toggleV2LibSelect(card.card_id)}
                      style={{ cursor: 'pointer' }} />
                    <div style={{ minWidth: 0, cursor: 'pointer' }} onClick={() => openV2Review(card)}>
                      <div style={{ fontSize: 12, color: '#374151', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {card.narrative.thesis?.slice(0, 40) ?? '(无论点)'}
                      </div>
                      <div style={{ fontSize: 10, color: '#9CA3AF' }}>
                        {card.facts.primary_symbol ?? '—'}
                        {isPending && <span style={{ marginLeft: 6, fontSize: 9, background: '#FEE2E2', color: '#DC2626', padding: '0 4px', borderRadius: 3 }}>待确认</span>}
                      </div>
                    </div>
                    <span>{stanceBadge(j.stance)}</span>
                    <span style={{ fontSize: 11, color: '#6B7280' }}>{HORIZON_CN[j.horizon] ?? j.horizon}</span>
                    <span style={{ fontSize: 10, background: vBadge.bg, color: vBadge.color, padding: '1px 5px', borderRadius: 4, textAlign: 'center' }}>{vBadge.label}</span>
                    <span style={{ fontSize: 10, color: '#9CA3AF' }}>{SOURCE_SHORT[card.facts.source_type] ?? card.facts.source_type}</span>
                    <span style={{ fontSize: 10, color: '#9CA3AF' }}>{new Date(card.created_at).toLocaleDateString()}</span>
                  </div>
                )
              })}
            </div>
          )}

          {/* 观点库批量操作栏 */}
          {v2LibSelectedIds.size > 0 && (
            <div style={{ position: 'sticky', bottom: 0, marginTop: 12, background: '#fff', borderRadius: 10, padding: '10px 16px',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderTop: '1px solid #E5E7EB', boxShadow: '0 -4px 12px rgba(0,0,0,0.08)' }}>
              <span style={{ fontSize: 13, color: '#374151', fontWeight: 500 }}>已选 {v2LibSelectedIds.size} 张</span>
              <div style={{ display: 'flex', gap: 8 }}>
                <button disabled={v2LibBatchBusy} onClick={() => handleV2LibBatchAction('discard')}
                  style={{ background: '#FEE2E2', color: '#DC2626', border: 'none', borderRadius: 6, padding: '6px 14px', fontSize: 12, cursor: 'pointer', fontWeight: 500, opacity: v2LibBatchBusy ? 0.6 : 1 }}>
                  <Trash2 size={12} style={{ verticalAlign: -2, marginRight: 4 }} />批量丢弃
                </button>
                <button disabled={v2LibBatchBusy} onClick={() => handleV2LibBatchAction('confirm')}
                  style={{ background: '#3B82F6', color: '#fff', border: 'none', borderRadius: 6, padding: '6px 14px', fontSize: 12, cursor: 'pointer', fontWeight: 500, opacity: v2LibBatchBusy ? 0.6 : 1 }}>
                  <Check size={12} style={{ verticalAlign: -2, marginRight: 4 }} />批量确认并入库
                </button>
              </div>
            </div>
          )}
          {v2LibBatchMsg && (
            <div style={{ marginTop: 8, fontSize: 12, color: '#3B82F6', textAlign: 'center' }}>{v2LibBatchMsg}</div>
          )}
        </div>
      )}

      {/* ══════════ Tab 3：决策检索 (v2) ══════════ */}
      {activeTab === 'search' && (
        <div style={{ ...S.card, padding: '20px 24px' }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 16, display: 'flex', alignItems: 'center', gap: 6 }}>
            <Search size={14} style={{ color: '#3B82F6' }} /> 决策检索（v2 — 决策引擎实际消费的内容）
          </div>

          {/* 持仓单选列表 */}
          {v2Holdings.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <label style={S.label}>选择持仓标的（当前支持美股）</label>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 3, maxHeight: 200, overflowY: 'auto', border: '1px solid #E5E7EB', borderRadius: 8, padding: '8px 10px' }}>
                {v2Holdings.filter(h => h.supported).map(h => (
                  <label key={h.symbol!} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: '#374151', cursor: 'pointer', padding: '2px 0' }}>
                    <input type="radio" name="v2search_holding" checked={v2SearchSymbol === h.symbol}
                      onChange={() => setV2SearchSymbol(h.symbol!)} />
                    <span style={{ flex: 1 }}>{h.asset_name}</span>
                    <span style={{ color: '#9CA3AF', fontSize: 11 }}>{h.symbol}</span>
                    <span style={{ color: '#9CA3AF', fontSize: 11, minWidth: 40, textAlign: 'right' }}>{(h.weight * 100).toFixed(1)}%</span>
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* 手动输入 */}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 16, maxWidth: 400 }}>
            <div style={{ flex: 1 }}>
              <label style={S.label}>或手动输入 Symbol</label>
              <input style={S.input} value={v2SearchSymbol} onChange={e => setV2SearchSymbol(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') handleV2Search() }}
                placeholder="如 LI:US、NVDA:US" />
            </div>
            <button style={{ ...S.btnPrimary, marginTop: 18, opacity: v2Searching || !v2SearchSymbol.trim() ? 0.6 : 1 }}
              disabled={v2Searching || !v2SearchSymbol.trim()} onClick={handleV2Search}>
              {v2Searching ? <Loader2 size={12} className="animate-spin" /> : <Search size={12} />} 检索
            </button>
          </div>

          {/* 结果区 */}
          {v2SearchLines.length > 0 && (
            <div>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#374151', marginBottom: 10 }}>
                决策引擎当前消费 {v2SearchCardCount} 张观点卡（共 {v2SearchLines.length} 段文本）
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {v2SearchLines.map((line, i) => {
                  const prefixMatch = line.match(/^\[(用户资料|第三方数据|联网参考)\]\s*/)
                  const prefix = prefixMatch ? prefixMatch[1] : null
                  const text = prefixMatch ? line.slice(prefixMatch[0].length) : line
                  const badgeStyle: Record<string, { bg: string; color: string }> = {
                    '用户资料':   { bg: '#DBEAFE', color: '#1D4ED8' },
                    '第三方数据': { bg: '#FEF3C7', color: '#D97706' },
                    '联网参考':   { bg: '#F3F4F6', color: '#6B7280' },
                  }
                  const badge = prefix ? badgeStyle[prefix] ?? { bg: '#F3F4F6', color: '#6B7280' } : null
                  return (
                    <div key={i} style={{ background: '#F8FAFC', border: '1px solid #E5E7EB', borderRadius: 8, padding: '8px 12px', fontSize: 12, color: '#374151', lineHeight: 1.6 }}>
                      {badge && <span style={{ fontSize: 10, background: badge.bg, color: badge.color, padding: '1px 5px', borderRadius: 3, marginRight: 6, fontWeight: 500 }}>[{prefix}]</span>}
                      {text}
                    </div>
                  )
                })}
              </div>
            </div>
          )}
          {v2SearchLines.length === 0 && v2SearchSymbol.trim() && !v2Searching && (
            <div style={{ color: '#9CA3AF', fontSize: 13, textAlign: 'center', padding: '24px 0' }}>
              该标的暂无可消费的观点数据
            </div>
          )}
        </div>
      )}

      {/* ══════════ v2: 审核详情弹窗（全 Tab 共用）══════════ */}
      {v2ReviewCard && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', zIndex: 1000, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '40px 20px', overflowY: 'auto' }}
          onClick={e => { if (e.target === e.currentTarget) setV2ReviewCard(null) }}>
          <div style={{ ...S.card, width: '100%', maxWidth: 720, padding: '20px 24px', margin: 'auto' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <div style={{ fontSize: 15, fontWeight: 700, color: '#1B2A4A' }}>观点卡审核</div>
              <button style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#9CA3AF' }} onClick={() => setV2ReviewCard(null)}><X size={18} /></button>
            </div>
            <div style={{ background: '#F9FAFB', borderRadius: 8, padding: '12px 16px', marginBottom: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: '#9CA3AF', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 1 }}>事实层（只读）</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 16px', fontSize: 12, color: '#374151' }}>
                <div><span style={{ color: '#9CA3AF' }}>来源：</span>{SOURCE_TYPE_LABEL[v2ReviewCard.facts.source_type] ?? v2ReviewCard.facts.source_type}</div>
                <div><span style={{ color: '#9CA3AF' }}>时间：</span>{new Date(v2ReviewCard.facts.as_of).toLocaleDateString()}</div>
                <div><span style={{ color: '#9CA3AF' }}>标的：</span>{v2ReviewCard.facts.primary_symbol ?? '未指定'}</div>
                <div><span style={{ color: '#9CA3AF' }}>入库：</span>{new Date(v2ReviewCard.facts.ingested_at).toLocaleDateString()}</div>
              </div>
              {v2ReviewCard.facts.source_refs.filter(r => r.ref_type === 'url').map((r, i) => (
                <div key={i} style={{ marginTop: 6, fontSize: 11 }}>
                  <a href={r.ref_value} target="_blank" rel="noreferrer" style={{ color: '#3B82F6' }}>{r.title ?? '原文链接'} ↗</a>
                </div>
              ))}
              {v2ReviewCard.facts.raw_facts.summary && (
                <div style={{ marginTop: 8, fontSize: 12, color: '#6B7280', lineHeight: 1.5, borderTop: '1px solid #E5E7EB', paddingTop: 8 }}>
                  {String(v2ReviewCard.facts.raw_facts.summary).slice(0, 300)}
                </div>
              )}
            </div>
            <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 8, padding: '12px 16px', marginBottom: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: '#9CA3AF', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 1 }}>叙事层（只读）</div>
              {v2ReviewCard.narrative.thesis && <div style={{ fontSize: 13, fontWeight: 600, color: '#1B2A4A', marginBottom: 8, lineHeight: 1.5 }}>{v2ReviewCard.narrative.thesis}</div>}
              {v2ReviewCard.narrative.bull_case && <div style={{ fontSize: 12, color: '#059669', marginBottom: 4, lineHeight: 1.5 }}>🟢 {v2ReviewCard.narrative.bull_case}</div>}
              {v2ReviewCard.narrative.bear_case && <div style={{ fontSize: 12, color: '#DC2626', marginBottom: 4, lineHeight: 1.5 }}>🔴 {v2ReviewCard.narrative.bear_case}</div>}
              <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                <span style={{ fontSize: 10, background: '#EFF6FF', color: '#3B82F6', padding: '1px 6px', borderRadius: 4 }}>{v2ReviewCard.narrative.event_type}</span>
                {v2ReviewCard.narrative.topics.map((t, i) => (
                  <span key={i} style={{ fontSize: 10, background: '#F3F4F6', color: '#6B7280', padding: '1px 6px', borderRadius: 4 }}>{t}</span>
                ))}
              </div>
            </div>
            <div style={{ background: '#fff', border: '2px solid #3B82F6', borderRadius: 8, padding: '12px 16px', marginBottom: 16 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: '#3B82F6', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 1 }}>
                判断层（可编辑）
                {v2ReviewCard.judgment.is_ai_prefilled && (
                  <span style={{ marginLeft: 8, fontSize: 10, background: '#FEE2E2', color: '#DC2626', padding: '1px 6px', borderRadius: 4, fontWeight: 500, textTransform: 'none', letterSpacing: 0 }}>AI 预填，待确认</span>
                )}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '10px 12px', marginBottom: 10 }}>
                <div>
                  <label style={S.label}>立场</label>
                  <select style={S.select} value={v2JudgEdit.stance ?? ''} onChange={e => setV2JudgEdit(p => ({ ...p, stance: e.target.value }))}>
                    <option value="bullish">做多</option><option value="bearish">做空</option>
                    <option value="neutral">中性</option><option value="watch">观察</option>
                  </select>
                </div>
                <div>
                  <label style={S.label}>置信度</label>
                  <select style={S.select} value={v2JudgEdit.confidence ?? ''} onChange={e => setV2JudgEdit(p => ({ ...p, confidence: e.target.value }))}>
                    <option value="low">低</option><option value="medium">中</option><option value="high">高</option>
                  </select>
                </div>
                <div>
                  <label style={S.label}>操作类型</label>
                  <select style={S.select} value={v2JudgEdit.action_type ?? ''} onChange={e => setV2JudgEdit(p => ({ ...p, action_type: e.target.value }))}>
                    <option value="consider_add">考虑加仓</option><option value="consider_reduce">考虑减仓</option>
                    <option value="consider_exit">考虑清仓</option><option value="hold_observe">持有观察</option>
                    <option value="no_action">无操作</option>
                  </select>
                </div>
                <div>
                  <label style={S.label}>时间跨度</label>
                  <select style={S.select} value={v2JudgEdit.horizon ?? ''} onChange={e => setV2JudgEdit(p => ({ ...p, horizon: e.target.value }))}>
                    <option value="short">短期(&lt;1月)</option><option value="medium">中期(1-6月)</option><option value="long">长期(&gt;6月)</option>
                  </select>
                </div>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div>
                  <label style={S.label}>触发条件（可选）</label>
                  <textarea style={{ ...S.textarea, minHeight: 40 }} rows={2} value={v2JudgEdit.trigger_conditions ?? ''}
                    onChange={e => setV2JudgEdit(p => ({ ...p, trigger_conditions: e.target.value }))} />
                </div>
                <div>
                  <label style={S.label}>失效条件（可选）</label>
                  <textarea style={{ ...S.textarea, minHeight: 40 }} rows={2} value={v2JudgEdit.invalidation_conditions ?? ''}
                    onChange={e => setV2JudgEdit(p => ({ ...p, invalidation_conditions: e.target.value }))} />
                </div>
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              {v2ReviewCard.judgment.is_ai_prefilled ? (
                <>
                  <button style={{ ...S.btnDanger, opacity: v2Confirming ? 0.6 : 1 }} disabled={v2Confirming} onClick={() => handleV2Action('discard')}><Trash2 size={12} /> 丢弃</button>
                  <button style={{ ...S.btnSecondary, opacity: v2Confirming ? 0.6 : 1 }} disabled={v2Confirming} onClick={() => handleV2Action('confirm', 'reference_only')}>仅作参考</button>
                  <button style={{ ...S.btnPrimary, opacity: v2Confirming ? 0.6 : 1 }} disabled={v2Confirming} onClick={() => handleV2Action('confirm', 'endorse')}><Check size={12} /> 确认并入库</button>
                </>
              ) : (
                <>
                  <button style={{ ...S.btnDanger, opacity: v2Confirming ? 0.6 : 1 }} disabled={v2Confirming} onClick={() => handleV2Action('discard')}><Trash2 size={12} /> 丢弃</button>
                  <button style={{ ...S.btnSecondary, opacity: v2Confirming ? 0.6 : 1 }} disabled={v2Confirming} onClick={() => handleV2Action('unconfirm')}>撤回确认</button>
                  <button style={{ ...S.btnPrimary, opacity: v2Confirming ? 0.6 : 1 }} disabled={v2Confirming} onClick={() => handleV2Action('modify')}><Pencil size={12} /> 保存修改</button>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ══════════ 文档详情/编辑弹窗 ══════════ */}
      {docDetail && (() => {
        const relCard = cards.find(c => c.document_id === docDetail.id)
        const relV2 = v2Cards.filter(c =>
          c.facts.source_type === 'user_upload' && docDetail.uploaded_at &&
          Math.abs(new Date(c.facts.ingested_at).getTime() - new Date(docDetail.uploaded_at!).getTime()) < 120000
        )
        const v2Conf = relV2.filter(c => !c.judgment.is_ai_prefilled).length
        const v2Pend = relV2.filter(c => c.judgment.is_ai_prefilled).length
        return (
          <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', zIndex: 1000, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '40px 20px', overflowY: 'auto' }}
            onClick={e => { if (e.target === e.currentTarget) setDocDetail(null) }}>
            <div style={{ ...S.card, width: '100%', maxWidth: 680, padding: '20px 24px', margin: 'auto' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <div style={{ fontSize: 15, fontWeight: 700, color: '#1B2A4A' }}>文档详情</div>
                <button style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#9CA3AF' }} onClick={() => setDocDetail(null)}><X size={18} /></button>
              </div>

              {/* 区块 1: 基本信息（可编辑）*/}
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: '#9CA3AF', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 1 }}>基本信息</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 12px' }}>
                  <div style={{ gridColumn: '1 / -1' }}>
                    <label style={S.label}>标题</label>
                    <input style={S.input} value={docEditFields.title ?? ''} onChange={e => setDocEditFields(p => ({ ...p, title: e.target.value }))} />
                  </div>
                  <div>
                    <label style={S.label}>标的名称</label>
                    <input style={S.input} value={docEditFields.object_name ?? ''} onChange={e => setDocEditFields(p => ({ ...p, object_name: e.target.value }))} />
                  </div>
                  <div>
                    <label style={S.label}>市场</label>
                    <input style={S.input} value={docEditFields.market_name ?? ''} onChange={e => setDocEditFields(p => ({ ...p, market_name: e.target.value }))} />
                  </div>
                  <div>
                    <label style={S.label}>作者</label>
                    <input style={S.input} value={docEditFields.author ?? ''} onChange={e => setDocEditFields(p => ({ ...p, author: e.target.value }))} />
                  </div>
                  <div>
                    <label style={S.label}>发布时间</label>
                    <input style={S.input} value={docEditFields.publish_time ?? ''} onChange={e => setDocEditFields(p => ({ ...p, publish_time: e.target.value }))} />
                  </div>
                </div>
              </div>

              {/* 区块 2: 解析结果（只读）*/}
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: '#9CA3AF', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 1 }}>解析结果</div>
                {relCard ? (
                  <ParseCardPreview card={relCard} />
                ) : (
                  <div style={{ color: '#9CA3AF', fontSize: 12, padding: '12px 0' }}>暂无解析结果</div>
                )}
              </div>

              {/* 区块 3: 关联 v2 卡（只读）*/}
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: '#9CA3AF', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 1 }}>关联 v2 观点卡</div>
                {relV2.length > 0 ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: '#374151' }}>
                    <span>{relV2.length} 张（{v2Conf} 张已确认，{v2Pend} 张待审核）</span>
                    <button style={{ ...S.btnSecondary, fontSize: 11, padding: '3px 10px' }}
                      onClick={() => { setDocDetail(null); setActiveTab('library') }}>
                      前往观点库
                    </button>
                  </div>
                ) : (
                  <div style={{ color: '#9CA3AF', fontSize: 12 }}>无关联 v2 卡</div>
                )}
              </div>

              {/* 底部按钮 */}
              <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', borderTop: '1px solid #E5E7EB', paddingTop: 12 }}>
                <button style={{ ...S.btnDanger, opacity: docSaving ? 0.6 : 1 }} disabled={docSaving}
                  onClick={e => { e.stopPropagation(); handleDeleteDocument(docDetail.id); setDocDetail(null) }}>
                  <Trash2 size={12} /> 删除
                </button>
                <button style={S.btnSecondary} onClick={() => setDocDetail(null)}>关闭</button>
                <button style={{ ...S.btnPrimary, opacity: docSaving ? 0.6 : 1 }} disabled={docSaving} onClick={saveDocDetail}>
                  {docSaving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />} 保存修改
                </button>
              </div>
            </div>
          </div>
        )
      })()}

      {/* ── 新建/编辑 Modal ── */}
      {modalOpen && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
          zIndex: 1000, display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
          padding: '40px 20px', overflowY: 'auto',
        }} onClick={e => { if (e.target === e.currentTarget) { setModalOpen(false); setEditTarget(null) } }}>
          <div style={{ ...S.card, width: '100%', maxWidth: 760, padding: '24px 28px', position: 'relative', margin: 'auto' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
              <div style={{ fontSize: 15, fontWeight: 700, color: '#1B2A4A' }}>{editTarget ? '编辑观点' : '新建观点'}</div>
              <button style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#9CA3AF' }}
                onClick={() => { setModalOpen(false); setEditTarget(null) }}><X size={18} /></button>
            </div>
            {formError && (
              <div style={{ background: '#FEE2E2', borderRadius: 8, padding: '8px 12px', fontSize: 12, color: '#DC2626', marginBottom: 12, display: 'flex', gap: 6 }}>
                <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} />{formError}
              </div>
            )}
            <ViewpointForm
              initial={editTarget ? {
                title: editTarget.title, object_type: editTarget.object_type,
                object_name: editTarget.object_name, stance: editTarget.stance,
                thesis: editTarget.thesis, horizon: editTarget.horizon,
                validity_status: editTarget.validity_status,
                user_approval_level: editTarget.user_approval_level,
                supporting_points: [], opposing_points: [], risks: [],
              } : EMPTY_FORM}
              onSave={handleSave}
              onCancel={() => { setModalOpen(false); setEditTarget(null) }}
              saving={formSaving}
            />
          </div>
        </div>
      )}
    </div>
  )
}

// ── 观点行 ────────────────────────────────────────────────────
function ViewpointRow({ vp, onEdit, onDelete, onStatusChange }: {
  vp: Viewpoint; onEdit: () => void; onDelete: () => void; onStatusChange: (s: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const createdAt = vp.created_at ? new Date(vp.created_at).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit', year: '2-digit' }) : '—'
  const approvalLabel: Record<string, string> = { reference: '参考', partial: '部分认可', strong: '强认可' }

  return (
    <div style={{ borderBottom: '1px solid #F3F4F6' }}>
      <div style={{
        display: 'grid', gridTemplateColumns: '2fr 70px 60px 70px 80px 90px 80px',
        gap: 8, padding: '10px 10px', cursor: 'pointer', fontSize: 13,
        background: expanded ? '#F8FAFC' : 'transparent',
      }} onClick={() => setExpanded(v => !v)}>
        <div style={{ overflow: 'hidden' }}>
          <div style={{ fontWeight: 500, color: '#1B2A4A', fontSize: 13, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{vp.title}</div>
          {vp.object_name && <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 1 }}>{vp.object_name}</div>}
        </div>
        <div style={{ display: 'flex', alignItems: 'center' }}>{stanceBadge(vp.stance)}</div>
        <div style={{ display: 'flex', alignItems: 'center', fontSize: 12, color: '#6B7280' }}>{vp.horizon ?? '—'}</div>
        <div style={{ display: 'flex', alignItems: 'center', fontSize: 11, color: '#6B7280' }}>{approvalLabel[vp.user_approval_level ?? 'reference'] ?? '—'}</div>
        <div style={{ display: 'flex', alignItems: 'center' }}>{validityBadge(vp.validity_status)}</div>
        <div style={{ display: 'flex', alignItems: 'center', fontSize: 11, color: '#9CA3AF' }}>{createdAt}</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }} onClick={e => e.stopPropagation()}>
          <button title="编辑" style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#9CA3AF', padding: 4 }} onClick={onEdit}><Pencil size={13} /></button>
          <button title="删除" style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#9CA3AF', padding: 4 }} onClick={onDelete}><Trash2 size={13} /></button>
          {expanded ? <ChevronUp size={13} color="#9CA3AF" /> : <ChevronDown size={13} color="#9CA3AF" />}
        </div>
      </div>
      {expanded && (
        <div style={{ padding: '8px 16px 14px', background: '#F8FAFC', fontSize: 12, color: '#374151' }}>
          {vp.thesis && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 10, fontWeight: 600, color: '#9CA3AF', textTransform: 'uppercase', marginBottom: 3 }}>核心论点</div>
              <div style={{ lineHeight: 1.6 }}>{vp.thesis}</div>
            </div>
          )}
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <span style={{ fontSize: 11, color: '#9CA3AF' }}>切换状态：</span>
            {(['active', 'suspect', 'invalid'] as const).map(s => {
              const v = VALIDITY[s]
              const isActive = vp.validity_status === s
              return (
                <button key={s} style={{
                  fontSize: 10, padding: '2px 8px', borderRadius: 8, cursor: 'pointer', fontWeight: 500,
                  background: isActive ? v.bg : '#fff', color: isActive ? v.color : '#9CA3AF',
                  border: `1px solid ${isActive ? v.color : '#E5E7EB'}`,
                }} onClick={() => onStatusChange(s)}>{v.label}</button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

// ── 待审核候选卡 ──────────────────────────────────────────────
function CandidateCard({ card, approving, onApprove, onDiscard }: {
  card: ResearchCard; approving: boolean; onApprove: () => void; onDiscard: () => void
}) {
  const title = card.document_title ?? '（解析结果）'
  const objectName = card.document_object_name

  return (
    <div style={{ background: '#F5F3FF', border: '1px solid #DDD6FE', borderRadius: 10, padding: '12px 16px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 13, color: '#1B2A4A', marginBottom: 2 }}>{title}</div>
          {objectName && <div style={{ fontSize: 11, color: '#7C3AED', marginBottom: 3 }}>标的：{objectName}</div>}
          <div style={{ display: 'flex', gap: 6, marginBottom: card.thesis ? 6 : 0 }}>
            {stanceBadge(card.stance)}
            {card.horizon && <span style={{ fontSize: 10, color: '#9CA3AF', padding: '2px 6px', border: '1px solid #E5E7EB', borderRadius: 8 }}>{card.horizon}</span>}
          </div>
          {card.thesis && (
            <div style={{ fontSize: 12, color: '#374151', lineHeight: 1.6, overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' as const }}>
              {card.thesis}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
          <button
            style={{ ...S.btnPrimary as React.CSSProperties, background: 'linear-gradient(135deg, #7C3AED, #4F46E5)', padding: '5px 10px', fontSize: 11, opacity: approving ? 0.6 : 1 }}
            disabled={approving} onClick={onApprove}>
            {approving ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />} 确认入库
          </button>
          <button style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#9CA3AF', padding: '5px 6px' }} onClick={onDiscard} title="丢弃">
            <X size={14} />
          </button>
        </div>
      </div>
    </div>
  )
}

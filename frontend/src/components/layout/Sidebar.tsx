/**
 * Sidebar — 侧边栏导航
 *
 * IA Shell 只在既有投资入口外增加一级模块与「投资规划」分组；
 * 投资入口的名称、顺序、route 与页面能力保持不变。
 */
import { NavLink, useLocation } from 'react-router-dom'
import { Compass } from 'lucide-react'

// ── 类型 ──────────────────────────────────────────────────

interface NavItemDef {
  label: string
  to: string
}

interface NavGroupDef {
  icon: string
  title: string
  items: NavItemDef[]
}

// ── 显示开关（设为 true 可恢复对应导航入口，路由和页面不受影响）──
const SHOW_PROFIT_ANALYSIS = false   // 收益分析（模块建设中）
const SHOW_FINANCE_PLANNING = false  // 财务规划分组
const SHOW_BALANCE_SHEET = false     // 资产负债总览分组

// ── 投资主线：名称、顺序、route 为当前产品 Source of Truth ──────────
const INVEST_ITEMS: NavItemDef[] = [
  { label: '用户画像',       to: '/profile' },
  { label: '投资账户总览',   to: '/dashboard' },
  { label: '投资纪律',       to: '/discipline' },
  { label: '投研观点',       to: '/research' },
  { label: '投资决策',       to: '/decision' },
  { label: '投资行动',       to: '/action' },
  ...(SHOW_PROFIT_ANALYSIS ? [{ label: '收益分析', to: '/placeholder/收益分析' }] : []),
]

const PRIMARY_ITEMS: NavItemDef[] = [
  { label: '首页',     to: '/' },
  { label: '财富总览', to: '/wealth' },
  { label: '养老规划', to: '/retirement' },
  { label: '消费分析', to: '/consumption' },
]

// 当前没有独立的数据管理或设置页面，复用既有 Placeholder 作为系统入口。
const SYSTEM_ITEMS: NavItemDef[] = [
  { label: '数据管理', to: '/placeholder/数据管理' },
  { label: '设置',     to: '/placeholder/设置' },
]

// ── 历史分组导航（按开关条件显示）──────────────────────────────
const NAV_GROUPS: NavGroupDef[] = [
  ...(SHOW_FINANCE_PLANNING ? [{
    icon: '🏠',
    title: '财务规划',
    items: [
      { label: '生活账户总览', to: '/placeholder/生活账户总览' },
      { label: '养老规划',     to: '/placeholder/养老规划' },
      { label: '购房规划',     to: '/placeholder/购房规划' },
      { label: '消费规划',     to: '/placeholder/消费规划' },
    ],
  }] : []),
  ...(SHOW_BALANCE_SHEET ? [{
    icon: '📊',
    title: '资产负债总览',
    items: [
      { label: '个人资产负债总览', to: '/placeholder/个人资产负债总览' },
      { label: '家族资产负债总览', to: '/placeholder/家族资产负债总览' },
    ],
  }] : []),
]

// ── 组件 ──────────────────────────────────────────────────

export default function Sidebar() {
  const { pathname } = useLocation()
  const investmentActive = INVEST_ITEMS.some((item) => item.to === pathname)

  return (
    <aside
      style={{
        width: 'var(--sidebar-w)',
        flexShrink: 0,
        background: '#1F2937',
        display: 'flex',
        flexDirection: 'column',
        overflowY: 'auto',
        overflowX: 'hidden',
      }}
    >
      {/* ── Brand 区 ── */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '20px 16px 16px',
          flexShrink: 0,
        }}
      >
        <div
          style={{
            width: 36, height: 36,
            borderRadius: 10,
            background: 'rgba(255,255,255,0.16)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexShrink: 0,
          }}
        >
          <Compass size={20} color="#F9FAFB" strokeWidth={2} />
        </div>
        <div>
          <div style={{ fontSize: 17, fontWeight: 700, color: '#F9FAFB' }}>WealthPilot</div>
          <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.40)', marginTop: 1 }}>
            个人财富规划系统
          </div>
        </div>
      </div>

      {/* ── 导航 ── */}
      <nav style={{ flex: 1, paddingBottom: 16 }} aria-label="主导航">
        <div style={{ padding: '20px 14px 6px' }}>
          {PRIMARY_ITEMS.slice(0, 2).map((item) => (
            <NavItem key={item.to} item={item} />
          ))}

          <div style={{ margin: '14px 0 6px' }}>
            <div style={{ padding: '8px 16px', borderRadius: 8, background: investmentActive ? 'rgba(255,255,255,0.08)' : 'transparent' }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: investmentActive ? '#F9FAFB' : 'rgba(255,255,255,0.85)' }}>
                投资规划
              </span>
            </div>
            <div style={{ paddingLeft: 12 }}>
              {INVEST_ITEMS.map((item) => (
                <NavItem key={item.to} item={item} nested />
              ))}
            </div>
          </div>

          {PRIMARY_ITEMS.slice(2).map((item) => (
            <NavItem key={item.to} item={item} />
          ))}
        </div>

        {/* 保持既有隐藏分组的实现与开关语义。 */}
        {NAV_GROUPS.map((group) => (
          <div key={group.title}>
            <div style={{ height: 1, background: 'rgba(255,255,255,0.07)', margin: '6px 12px' }} />
            <div style={{ padding: '14px 12px 6px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 8px 8px' }}>
                <span style={{ fontSize: 14 }}>{group.icon}</span>
                <span style={{ fontSize: 13, fontWeight: 700, color: 'rgba(255,255,255,0.85)', letterSpacing: 0.1 }}>
                  {group.title}
                </span>
              </div>
              {group.items.map((item) => (
                <NavItem key={item.to} item={item} />
              ))}
            </div>
          </div>
        ))}

        <div style={{ height: 1, background: 'rgba(255,255,255,0.07)', margin: '10px 12px 6px' }} />
        <div style={{ padding: '8px 22px 4px', fontSize: 11, fontWeight: 700, color: 'rgba(255,255,255,0.42)', letterSpacing: 0.4 }}>系统</div>
        <div style={{ padding: '0 14px 6px' }}>
          {SYSTEM_ITEMS.map((item) => (
            <NavItem key={item.to} item={item} />
          ))}
        </div>
      </nav>
    </aside>
  )
}

// ── NavItem：单个导航项 ────────────────────────────────────

function NavItem({ item, nested = false }: { item: NavItemDef; nested?: boolean }) {
  return (
    <NavLink
      to={item.to}
      end
      style={({ isActive }) => ({
        display: 'flex',
        alignItems: 'center',
        padding: nested ? '8px 16px' : '9px 16px',
        borderRadius: 8,
        fontSize: 13,
        fontWeight: 500,
        color: isActive ? '#FFFFFF' : '#9CA3AF',
        textDecoration: 'none',
        marginBottom: 2,
        whiteSpace: 'nowrap' as const,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        transition: 'all 0.14s',
        background: isActive ? 'rgba(255,255,255,0.10)' : 'transparent',
        cursor: 'pointer',
      })}
      onMouseEnter={e => { if (!e.currentTarget.classList.contains('active')) (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.06)' }}
      onMouseLeave={e => { if (!e.currentTarget.classList.contains('active')) (e.currentTarget as HTMLElement).style.background = 'transparent' }}
    >
      {item.label}
    </NavLink>
  )
}

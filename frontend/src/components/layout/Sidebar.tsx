/**
 * Sidebar — 侧边栏导航
 *
 * 分组 / 一级模块 / 二级页面分别呈现；保留既有顺序、route 与页面能力。
 */
import { useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { ChevronDown, Compass } from 'lucide-react'
import './Sidebar.css'

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

// ── 投资主线：仅调整展示名，顺序与 route 保持不变 ──────────
const INVEST_ITEMS: NavItemDef[] = [
  { label: '用户画像',       to: '/profile' },
  { label: '投资账户总览',   to: '/dashboard' },
  { label: '投资纪律',       to: '/discipline' },
  { label: '投资观点',       to: '/research' },
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
  const [expanded, setExpanded] = useState(true)
  const investmentOpen = investmentActive || expanded

  return (
    <aside className="wp-sidebar">
      {/* ── Brand 区 ── */}
      <div className="wp-sidebar-brand">
        <div className="wp-sidebar-logo">
          <Compass size={20} strokeWidth={1.8} />
        </div>
        <div>
          <div className="wp-sidebar-title">WealthPilot</div>
          <div className="wp-sidebar-subtitle">
            个人财富规划系统
          </div>
        </div>
      </div>

      {/* ── 导航 ── */}
      <nav className="wp-sidebar-nav" aria-label="主导航">
        <section aria-labelledby="sidebar-overview">
          <h2 id="sidebar-overview" className="wp-sidebar-group">总览</h2>
          {PRIMARY_ITEMS.slice(0, 2).map((item) => (
            <NavItem key={item.to} item={item} />
          ))}
        </section>
        <section aria-labelledby="sidebar-finance">
          <h2 id="sidebar-finance" className="wp-sidebar-group">财富管理</h2>
          <div>
            <button type="button" className="wp-sidebar-item wp-sidebar-parent"
              aria-expanded={investmentOpen} aria-controls="sidebar-investment"
              aria-disabled={investmentActive}
              title={investmentActive ? '当前位于投资规划内，保持展开' : undefined}
              onClick={() => { if (!investmentActive) setExpanded(value => !value) }}>
              <span>投资规划</span>
              <ChevronDown size={14} className={investmentOpen ? 'wp-sidebar-chevron open' : 'wp-sidebar-chevron'} />
            </button>
            <div id="sidebar-investment" className="wp-sidebar-children" hidden={!investmentOpen}>
              {INVEST_ITEMS.map((item) => (
                <NavItem key={item.to} item={item} nested />
              ))}
            </div>
          </div>

          {PRIMARY_ITEMS.slice(2).map((item) => (
            <NavItem key={item.to} item={item} />
          ))}
        </section>

        {/* 保持既有隐藏分组的实现与开关语义。 */}
        {NAV_GROUPS.map((group) => (
          <section key={group.title}>
            <h2 className="wp-sidebar-group">{group.title}</h2>
            {group.items.map((item) => (
              <NavItem key={item.to} item={item} />
            ))}
          </section>
        ))}

        <section className="wp-sidebar-system" aria-labelledby="sidebar-system">
          <h2 id="sidebar-system" className="wp-sidebar-group">系统</h2>
          {SYSTEM_ITEMS.map((item) => (
            <NavItem key={item.to} item={item} />
          ))}
        </section>
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
      className={({ isActive }) => `wp-sidebar-item${nested ? ' wp-sidebar-secondary' : ''}${isActive ? ' is-active' : ''}`}
    >
      {item.label}
    </NavLink>
  )
}

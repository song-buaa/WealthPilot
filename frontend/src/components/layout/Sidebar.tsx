/**
 * Sidebar — 侧边栏导航
 * 设计依据：ui_preview-有导航栏.html + UI设计规范第三节
 *
 * 结构：
 *   Brand（Logo区）
 *   投资主线 6 项平铺（无分组标题）
 *   其他分组按开关控制显示（财务规划 / 资产负债总览 / 收益分析）
 */
import { NavLink } from 'react-router-dom'

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

// ── 投资主线（始终显示，无分组标题，平铺）──────────────────
const INVEST_ITEMS: NavItemDef[] = [
  { label: '用户画像',       to: '/profile' },
  { label: '投资账户总览',   to: '/dashboard' },
  { label: '投资纪律',       to: '/discipline' },
  { label: '投研观点',       to: '/research' },
  { label: '投资决策',       to: '/decision' },
  { label: '投资行动',       to: '/action' },
  ...(SHOW_PROFIT_ANALYSIS ? [{ label: '收益分析', to: '/placeholder/收益分析' }] : []),
]

// ── 分组导航（按开关条件显示）──────────────────────────────
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
  return (
    <aside
      style={{
        width: 'var(--sidebar-w)',
        flexShrink: 0,
        background: 'linear-gradient(180deg, #1B2A4A 0%, #0F1E35 100%)',
        borderRight: '1px solid rgba(255,255,255,0.05)',
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
          borderBottom: '1px solid rgba(255,255,255,0.07)',
          flexShrink: 0,
        }}
      >
        {/* 品牌图标 */}
        <div
          style={{
            width: 36, height: 36,
            borderRadius: 10,
            background: 'linear-gradient(135deg, #3B82F6, #1D4ED8)',
            boxShadow: '0 2px 8px rgba(59,130,246,0.4)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 17, flexShrink: 0,
          }}
        >
          📊
        </div>
        {/* 品牌名称 */}
        <div>
          <div style={{ fontSize: 15, fontWeight: 700, color: '#fff' }}>WealthPilot</div>
          <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.38)', marginTop: 1 }}>
            个人投资决策工作台
          </div>
        </div>
      </div>

      {/* ── 导航 ── */}
      <nav style={{ flex: 1, paddingBottom: 16 }}>
        {/* 投资主线（平铺，无分组标题） */}
        <div style={{ padding: '20px 14px 6px' }}>
          {INVEST_ITEMS.map((item) => (
            <NavItem key={item.to} item={item} />
          ))}
        </div>

        {/* 其他分组（按开关条件显示） */}
        {NAV_GROUPS.map((group) => (
          <div key={group.title}>
            <div style={{ height: 1, background: 'rgba(255,255,255,0.07)', margin: '6px 12px' }} />
            <div style={{ padding: '14px 12px 6px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 8px 8px' }}>
                <span style={{ fontSize: 14 }}>{group.icon}</span>
                <span style={{ fontSize: 13, fontWeight: 700, color: 'rgba(255,255,255,0.88)', letterSpacing: 0.1 }}>
                  {group.title}
                </span>
              </div>
              {group.items.map((item) => (
                <NavItem key={item.to} item={item} />
              ))}
            </div>
          </div>
        ))}
      </nav>
    </aside>
  )
}

// ── NavItem：单个导航项 ────────────────────────────────────

function NavItem({ item }: { item: NavItemDef }) {
  return (
    <NavLink
      to={item.to}
      style={({ isActive }) => ({
        display: 'flex',
        alignItems: 'center',
        padding: isActive ? '12px 16px 12px 14px' : '12px 16px 12px 16px',
        borderRadius: 8,
        fontSize: 15,
        fontWeight: isActive ? 600 : 500,
        color: isActive ? '#93C5FD' : 'rgba(255,255,255,0.55)',
        textDecoration: 'none',
        marginBottom: 4,
        whiteSpace: 'nowrap' as const,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        transition: 'all 0.14s',
        background: isActive ? 'rgba(59,130,246,0.16)' : 'transparent',
        borderLeft: isActive ? '2px solid #3B82F6' : '2px solid transparent',
        cursor: 'pointer',
      })}
      onMouseEnter={e => { if (!e.currentTarget.classList.contains('active')) (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.06)' }}
      onMouseLeave={e => { if (!e.currentTarget.classList.contains('active')) (e.currentTarget as HTMLElement).style.background = 'transparent' }}
    >
      {item.label}
    </NavLink>
  )
}

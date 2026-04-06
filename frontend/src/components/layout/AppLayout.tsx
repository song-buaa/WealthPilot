/**
 * AppLayout — 整体布局框架
 * 左：Sidebar（220px 固定），右：主内容区（flex:1，overflow-y auto）
 * 投资决策页特殊处理：height:100% overflow:hidden（由页面内部自行管理滚动）
 */
import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from './Sidebar'

export default function AppLayout() {
  const { pathname } = useLocation()
  const isFullHeight = pathname === '/decision' || pathname === '/allocation/chat'

  return (
    <div className="flex h-full overflow-hidden">
      {/* ── 侧边栏 ── */}
      <Sidebar />

      {/* ── 主内容区 ── */}
      {isFullHeight ? (
        // 投资决策 / 配置对话页：不加 padding，页面内部自行管理双栏布局
        <main className="flex-1 min-w-0 overflow-hidden flex flex-col">
          <Outlet />
        </main>
      ) : (
        <main
          className="flex-1 min-w-0 overflow-y-auto"
          style={{ padding: '28px 64px' }}
        >
          <Outlet />
        </main>
      )}
    </div>
  )
}

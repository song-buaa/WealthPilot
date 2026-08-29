/**
 * App.tsx — 路由配置
 * HashRouter + 嵌套路由（全局使用 AppLayout）
 */
import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import AppLayout from '@/components/layout/AppLayout'
import RouteErrorBoundary from '@/components/shared/RouteErrorBoundary'
import Dashboard   from '@/pages/Dashboard'
import Discipline  from '@/pages/Discipline'
import Research    from '@/pages/Research'
import Decision    from '@/pages/Decision'
import Placeholder from '@/pages/Placeholder'
import Action      from '@/pages/Action'
import { ToastProvider } from '@/components/Toast'
import UserProfile from '@/pages/UserProfile'
import DemoPasswordGate from '@/components/DemoPasswordGate'
import WealthOverview from '@/pages/WealthOverview'
import Retirement from '@/pages/Retirement'
import Consumption from '@/pages/Consumption'

export default function App() {
  return (
    <ToastProvider>
    <DemoPasswordGate>
    <HashRouter>
      <Routes>
        <Route element={<AppLayout />}>
          {/* 首页沿用现有 Dashboard；/dashboard 继续作为投资账户总览的既有稳定 route。 */}
          <Route index element={<RouteErrorBoundary><Dashboard /></RouteErrorBoundary>} />

          {/* 功能页 — 每个页面包裹 ErrorBoundary，单页崩溃不影响导航 */}
          <Route path="/dashboard"  element={<RouteErrorBoundary><Dashboard /></RouteErrorBoundary>} />
          <Route path="/discipline" element={<RouteErrorBoundary><Discipline /></RouteErrorBoundary>} />
          <Route path="/research"   element={<RouteErrorBoundary><Research /></RouteErrorBoundary>} />
          <Route path="/decision"   element={<RouteErrorBoundary><Decision /></RouteErrorBoundary>} />
          <Route path="/profile"    element={<RouteErrorBoundary><UserProfile /></RouteErrorBoundary>} />
          <Route path="/action"        element={<RouteErrorBoundary><Action /></RouteErrorBoundary>} />
          <Route path="/wealth" element={<RouteErrorBoundary><WealthOverview /></RouteErrorBoundary>} />
          <Route path="/retirement" element={<RouteErrorBoundary><Retirement /></RouteErrorBoundary>} />
          <Route path="/consumption" element={<RouteErrorBoundary><Consumption /></RouteErrorBoundary>} />

          {/* 所有未实现功能统一走 Placeholder */}
          <Route path="/placeholder/:name" element={<Placeholder />} />

          {/* 404 兜底 */}
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Route>
      </Routes>
    </HashRouter>
    </DemoPasswordGate>
    </ToastProvider>
  )
}

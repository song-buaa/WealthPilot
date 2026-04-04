/**
 * App.tsx — 路由配置
 * HashRouter + 嵌套路由（全局使用 AppLayout）
 */
import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import AppLayout from '@/components/layout/AppLayout'
import Dashboard  from '@/pages/Dashboard'
import Discipline from '@/pages/Discipline'
import Research   from '@/pages/Research'
import Decision   from '@/pages/Decision'
import Placeholder from '@/pages/Placeholder'

export default function App() {
  return (
    <HashRouter>
      <Routes>
        <Route element={<AppLayout />}>
          {/* 默认落地页：投资账户总览 */}
          <Route index element={<Navigate to="/dashboard" replace />} />

          {/* 四个功能页 */}
          <Route path="/dashboard"  element={<Dashboard />} />
          <Route path="/discipline" element={<Discipline />} />
          <Route path="/research"   element={<Research />} />
          <Route path="/decision"   element={<Decision />} />

          {/* 所有未实现功能统一走 Placeholder */}
          <Route path="/placeholder/:name" element={<Placeholder />} />

          {/* 404 兜底 */}
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Route>
      </Routes>
    </HashRouter>
  )
}

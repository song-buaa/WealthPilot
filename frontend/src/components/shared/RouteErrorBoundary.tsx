/**
 * RouteErrorBoundary — 路由级 Error Boundary
 * 单个页面崩溃时显示错误信息，不影响侧边栏导航。
 */

import React from 'react'
import { AlertTriangle } from 'lucide-react'

interface Props {
  children: React.ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export default class RouteErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          padding: '48px 24px', textAlign: 'center', height: '100%', minHeight: 300,
        }}>
          <AlertTriangle size={40} strokeWidth={1.5} style={{ marginBottom: 12, opacity: 0.5, color: '#EF4444' }} />
          <div style={{ fontSize: 16, fontWeight: 600, color: '#6B7280', marginBottom: 4 }}>
            页面加载出错
          </div>
          <div style={{ fontSize: 13, color: '#9CA3AF', lineHeight: 1.6, maxWidth: 420, marginBottom: 16 }}>
            {this.state.error?.message || '未知错误'}
          </div>
          <button
            onClick={() => this.setState({ hasError: false, error: null })}
            style={{
              padding: '7px 14px', background: '#fff', color: '#374151',
              border: '1px solid #E5E7EB', borderRadius: 8,
              fontSize: 12, fontWeight: 500, cursor: 'pointer',
            }}
          >
            重试
          </button>
        </div>
      )
    }

    return this.props.children
  }
}

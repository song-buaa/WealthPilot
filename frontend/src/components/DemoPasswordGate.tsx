/**
 * DemoPasswordGate — 公开 demo 访问密码门。
 *
 * 包裹子组件，未验证密码时显示密码输入页面。
 * 密码验证后通过 cookie 持久化（后端中间件读 cookie）。
 */
import { useState, useEffect, type ReactNode } from 'react'

const API_BASE = '/api'

interface Props {
  children: ReactNode
}

export default function DemoPasswordGate({ children }: Props) {
  const [status, setStatus] = useState<'loading' | 'no_password' | 'need_password' | 'authenticated'>('loading')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    // 检查 demo 状态
    fetch(`${API_BASE}/demo/status`)
      .then(r => r.json())
      .then(data => {
        if (!data.public_demo_mode) {
          setStatus('no_password')
          return
        }
        if (!data.password_required) {
          setStatus('no_password')
          return
        }
        // 尝试用已有 cookie 访问一个受保护的端点
        fetch(`${API_BASE}/portfolio/summary`)
          .then(r => {
            if (r.ok) {
              setStatus('authenticated')
            } else {
              setStatus('need_password')
            }
          })
          .catch(() => setStatus('need_password'))
      })
      .catch(() => setStatus('no_password'))
  }, [])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    try {
      const res = await fetch(`${API_BASE}/demo/verify-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      const data = await res.json()
      if (data.valid) {
        setStatus('authenticated')
      } else {
        setError(data.error || '密码错误')
      }
    } catch {
      setError('网络错误')
    }
  }

  if (status === 'loading') {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', background: '#F9FAFB' }}>
        <div style={{ color: '#9CA3AF', fontSize: 14 }}>加载中...</div>
      </div>
    )
  }

  if (status === 'no_password' || status === 'authenticated') {
    return <>{children}</>
  }

  // 密码输入页面
  return (
    <div style={{
      display: 'flex', justifyContent: 'center', alignItems: 'center',
      height: '100vh', background: 'linear-gradient(135deg, #1B2A4A 0%, #0F1E35 100%)',
    }}>
      <div style={{
        background: '#fff', borderRadius: 16, padding: '40px 36px', width: 360,
        boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
      }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <div style={{ fontSize: 28, marginBottom: 8 }}>📊</div>
          <div style={{ fontSize: 18, fontWeight: 700, color: '#1B2A4A' }}>WealthPilot</div>
          <div style={{ fontSize: 13, color: '#9CA3AF', marginTop: 4 }}>个人投资决策工作台 · 演示版</div>
        </div>

        <form onSubmit={handleSubmit}>
          <input
            type="password"
            placeholder="请输入访问密码"
            value={password}
            onChange={e => setPassword(e.target.value)}
            autoFocus
            style={{
              width: '100%', padding: '12px 16px', borderRadius: 8,
              border: '1px solid #E5E7EB', fontSize: 14, outline: 'none',
              boxSizing: 'border-box',
            }}
          />
          {error && (
            <div style={{ color: '#EF4444', fontSize: 12, marginTop: 8 }}>{error}</div>
          )}
          <button
            type="submit"
            style={{
              width: '100%', padding: '12px', borderRadius: 8, marginTop: 16,
              background: 'linear-gradient(135deg, #3B82F6, #1D4ED8)',
              color: '#fff', fontWeight: 600, fontSize: 14,
              border: 'none', cursor: 'pointer',
            }}
          >
            进入演示
          </button>
        </form>

        <div style={{ textAlign: 'center', marginTop: 20, fontSize: 11, color: '#D1D5DB' }}>
          本系统仅供演示，不构成投资建议
        </div>
      </div>
    </div>
  )
}

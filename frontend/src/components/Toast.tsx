/**
 * Toast 通知组件 — M6
 * 右上角弹出，3 秒自动消失，支持 success / error / info
 */
import React, { createContext, useContext, useState, useCallback, useRef } from 'react'

type ToastType = 'success' | 'error' | 'info'

interface ToastItem {
  id: number
  type: ToastType
  message: string
}

interface ToastContextValue {
  showToast: (type: ToastType, message: string) => void
}

const ToastContext = createContext<ToastContextValue>({ showToast: () => {} })

export function useToast() {
  return useContext(ToastContext)
}

const TOAST_STYLES: Record<ToastType, { bg: string; border: string; color: string; icon: string; accent: string }> = {
  success: { bg: '#F0FDF4', border: '#BBF7D0', color: '#166534', icon: '\u2713', accent: '#16A34A' },
  error:   { bg: '#FEF2F2', border: '#FECACA', color: '#991B1B', icon: '\u2717', accent: '#DC2626' },
  info:    { bg: '#EFF6FF', border: '#BFDBFE', color: '#1E40AF', icon: '\u2139', accent: '#3B82F6' },
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const nextId = useRef(0)

  const showToast = useCallback((type: ToastType, message: string) => {
    const id = ++nextId.current
    setToasts(prev => [...prev, { id, type, message }])
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id))
    }, 3000)
  }, [])

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      {/* Toast container — fixed top-right */}
      <div style={{
        position: 'fixed', top: 16, right: 16, zIndex: 9999,
        display: 'flex', flexDirection: 'column', gap: 8,
        pointerEvents: 'none',
      }}>
        {toasts.map(t => {
          const s = TOAST_STYLES[t.type]
          return (
            <div key={t.id} style={{
              background: s.bg, border: `1px solid ${s.border}`, color: s.color,
              padding: '12px 16px', borderRadius: 12, fontSize: 13, fontWeight: 500,
              boxShadow: '0 6px 20px rgba(15,30,53,0.28)',
              borderLeft: `4px solid ${s.accent}`,
              display: 'flex', alignItems: 'center', gap: 8,
              animation: 'toast-slide-in 0.3s ease-out',
              pointerEvents: 'auto', maxWidth: 360,
            }}>
              <span style={{ fontSize: 16 }}>{s.icon}</span>
              <span>{t.message}</span>
            </div>
          )
        })}
      </div>
      <style>{`
        @keyframes toast-slide-in {
          from { transform: translateX(100%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
      `}</style>
    </ToastContext.Provider>
  )
}

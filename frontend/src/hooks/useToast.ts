import { createContext, useContext } from 'react'

export type ToastType = 'success' | 'error' | 'info'

interface ToastContextValue {
  showToast: (type: ToastType, message: string) => void
}

export const ToastContext = createContext<ToastContextValue>({ showToast: () => {} })

export function useToast() {
  return useContext(ToastContext)
}

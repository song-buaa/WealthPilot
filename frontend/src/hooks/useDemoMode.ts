/**
 * useDemoMode — 前端 demo 模式状态检测。
 *
 * 通过 /api/demo/status 判断是否在 demo 模式下。
 * 用于隐藏下单按钮等 polish 级 UI 控制。
 * 安全保证由后端 403 中间件提供，前端仅做 UX 优化。
 */
import { useState, useEffect } from 'react'

let _cached: boolean | null = null

export function useDemoMode(): boolean {
  const [isDemo, setIsDemo] = useState(_cached ?? false)

  useEffect(() => {
    if (_cached !== null) return
    fetch('/api/demo/status')
      .then(r => r.json())
      .then(d => {
        _cached = d.public_demo_mode ?? false
        setIsDemo(_cached)
      })
      .catch(() => {
        _cached = false
        setIsDemo(false)
      })
  }, [])

  return isDemo
}

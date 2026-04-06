/**
 * DataTip — 全局浮动 Tooltip
 * 监听 data-tip 属性，鼠标悬浮时显示 tooltip。
 * 从 Dashboard.tsx 抽取为共享组件。
 */

import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'

export default function DataTip() {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const box = ref.current!
    function move(ev: MouseEvent) {
      const w = box.offsetWidth || 160
      const h = box.offsetHeight || 32
      let x = ev.clientX + 14
      let y = ev.clientY - h - 8
      if (x + w > window.innerWidth - 8) x = ev.clientX - w - 14
      if (y < 8) y = ev.clientY + 16
      box.style.left = x + 'px'
      box.style.top  = y + 'px'
    }
    function onOver(ev: MouseEvent) {
      const el = (ev.target as Element).closest('[data-tip]') as HTMLElement | null
      if (!el) { box.style.display = 'none'; return }
      box.textContent = el.dataset.tip ?? ''
      box.style.display = 'block'
      move(ev)
    }
    function onMove(ev: MouseEvent) {
      if (box.style.display === 'block') move(ev)
    }
    function onOut(ev: MouseEvent) {
      const rel = ev.relatedTarget as Element | null
      if (!rel?.closest('[data-tip]')) box.style.display = 'none'
    }
    document.addEventListener('mouseover', onOver)
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseout',  onOut)
    return () => {
      document.removeEventListener('mouseover', onOver)
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseout',  onOut)
    }
  }, [])
  return createPortal(
    <div ref={ref} style={{
      position: 'fixed', zIndex: 9999,
      background: '#1F2937', color: '#F9FAFB',
      borderRadius: 6, padding: '5px 10px',
      fontSize: 12, fontWeight: 500,
      whiteSpace: 'nowrap', pointerEvents: 'none',
      display: 'none',
      boxShadow: '0 3px 10px rgba(0,0,0,0.18)',
    }} />,
    document.body
  )
}

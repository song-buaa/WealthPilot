import type { CSSProperties } from 'react'

/** Shared with the portfolio overview's import/export section. */
export function dataManagementBarStyle(open = false): CSSProperties {
  return {
    width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    background: '#fff', border: '1px solid #E5E7EB', borderRadius: open ? '12px 12px 0 0' : 12,
    padding: '12px 20px', fontSize: 13, fontWeight: 500, color: '#374151', cursor: 'pointer',
    boxShadow: 'var(--shadow-sm)',
  }
}

export const dataManagementExportButtonStyle: CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 5,
  background: '#fff', color: '#374151', border: '1px solid #E5E7EB',
  borderRadius: 8, padding: '3px 10px', fontSize: 11, fontWeight: 500, cursor: 'pointer',
  textDecoration: 'none', boxShadow: 'var(--shadow-sm)',
}

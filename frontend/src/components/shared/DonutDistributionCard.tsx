import type { ReactNode } from 'react'
import DonutDistributionChart, { type DonutDistributionEntry } from './DonutDistributionChart'

type DonutDistributionCardProps = {
  title: ReactNode
  entries: DonutDistributionEntry[]
  emptyText: string
  valueLabel: string
  titleRight?: ReactNode
  style?: React.CSSProperties
  titleStyle?: React.CSSProperties
  tooltipPrecision?: number
}

/** Reusable chart shell for the investment platform and wealth liability distributions. */
export default function DonutDistributionCard({ title, entries, emptyText, valueLabel, titleRight, style, titleStyle, tooltipPrecision = 1 }: DonutDistributionCardProps) {
  return <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12, padding: '16px 14px 8px', boxShadow: 'var(--shadow-sm)', ...style }}>
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 10, ...titleStyle }}>
      <span>{title}</span>{titleRight}
    </div>
    {entries.length === 0 ? <div style={{ textAlign: 'center', color: '#9CA3AF', fontSize: 12, padding: '40px 0' }}>{emptyText}</div> : <DonutDistributionChart entries={entries} valueLabel={valueLabel} height={230} cx="40%" innerRadius={52} outerRadius={82} showLegend tooltipPrecision={tooltipPrecision} />}
  </div>
}

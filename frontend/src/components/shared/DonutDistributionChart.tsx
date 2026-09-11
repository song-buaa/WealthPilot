import { useState } from 'react'
import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Sector, Tooltip } from 'recharts'
import { fmtCny } from '@/lib/fmt'
import { chartPalette } from './chartPalette'

export type DonutDistributionEntry = { name: string; value: number }

type DonutDistributionChartProps = {
  entries: DonutDistributionEntry[]
  valueLabel: string
  height: number
  cx?: string
  innerRadius: number
  outerRadius: number
  showLegend?: boolean
  tooltipPrecision?: number
}

/** Shared interaction and rendering rules for wealth distribution donuts. */
export default function DonutDistributionChart({ entries, valueLabel, height, cx = '50%', innerRadius, outerRadius, showLegend = false, tooltipPrecision = 1 }: DonutDistributionChartProps) {
  const [activeIndex, setActiveIndex] = useState<number | undefined>(undefined)
  const total = entries.reduce((sum, entry) => sum + entry.value, 0)
  const renderActiveShape = (props: unknown) => {
    const { cx: centerX, cy, innerRadius: activeInnerRadius, outerRadius: activeOuterRadius, startAngle, endAngle, fill } = props as {
      cx: number; cy: number; innerRadius: number; outerRadius: number
      startAngle: number; endAngle: number; fill: string
    }
    return <Sector cx={centerX} cy={cy} innerRadius={activeInnerRadius} outerRadius={activeOuterRadius + 8} startAngle={startAngle} endAngle={endAngle} fill={fill} style={{ filter: 'drop-shadow(0 2px 6px rgba(0,0,0,0.08))' }} />
  }

  return <ResponsiveContainer width="100%" height={height}>
    <PieChart>
      <Pie data={entries} cx={cx} cy="50%" innerRadius={innerRadius} outerRadius={outerRadius} paddingAngle={2} dataKey="value" startAngle={90} endAngle={-270} activeIndex={activeIndex} activeShape={renderActiveShape} onMouseEnter={(_, index) => setActiveIndex(index)} onMouseLeave={() => setActiveIndex(undefined)} cursor="pointer">
        {entries.map((entry, index) => <Cell key={entry.name} fill={chartPalette[index % chartPalette.length]} />)}
      </Pie>
      <Tooltip content={({ active, payload }) => {
        if (!active || !payload?.length) return null
        const { name, value } = payload[0] as { name: string; value: number }
        const pct = total > 0 ? (value / total * 100).toFixed(tooltipPrecision) : (0).toFixed(tooltipPrecision)
        return <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 8, padding: '8px 12px', fontSize: 12, color: '#1F2937', boxShadow: '0 3px 10px rgba(0,0,0,0.1)', lineHeight: 1.8 }}><div style={{ fontWeight: 600, marginBottom: 2 }}>{name}</div><div>{valueLabel}: <b>{fmtCny(value)}</b></div><div>占比: <b>{pct}%</b></div></div>
      }} />
      {showLegend && <Legend layout="vertical" align="right" verticalAlign="middle" iconType="circle" iconSize={8} formatter={(name: string, entry) => {
        const value = (entry.payload as { value?: number }).value ?? 0
        return <span style={{ fontSize: 11, color: '#6B7280' }}>{name}{'  '}<span style={{ color: '#9CA3AF' }}>{total > 0 ? (value / total * 100).toFixed(1) : '0.0'}%</span></span>
      }} />}
    </PieChart>
  </ResponsiveContainer>
}

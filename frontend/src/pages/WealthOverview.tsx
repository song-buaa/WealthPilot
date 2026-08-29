import { Landmark } from 'lucide-react'
import EmptyState from '@/components/shared/EmptyState'
import PageHeader from '@/components/shared/PageHeader'

/** Stable shell only; cross-domain wealth data is intentionally not queried here. */
export default function WealthOverview() {
  return (
    <div>
      <PageHeader icon="◈" title="财富总览" subtitle="统一查看个人财富状态与资产结构" />
      <section style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12 }}>
        <EmptyState
          icon={Landmark}
          title="财富总览正在建设中"
          desc="投资、消费与养老模块的数据将在后续逐步接入此处。"
        />
      </section>
    </div>
  )
}

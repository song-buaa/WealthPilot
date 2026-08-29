import { PiggyBank } from 'lucide-react'
import EmptyState from '@/components/shared/EmptyState'
import PageHeader from '@/components/shared/PageHeader'

/** Stable shell only; retirement calculations and recommendations are out of scope. */
export default function Retirement() {
  return (
    <div>
      <PageHeader icon="◌" title="养老规划" subtitle="规划退休目标、养老资产与退休准备度" />
      <section style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12 }}>
        <EmptyState
          icon={PiggyBank}
          title="养老规划正在建设中"
          desc="退休目标、养老资产与测算能力将在后续阶段单独建设。"
        />
      </section>
    </div>
  )
}

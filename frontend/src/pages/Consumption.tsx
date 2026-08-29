import { ReceiptText } from 'lucide-react'
import EmptyState from '@/components/shared/EmptyState'
import PageHeader from '@/components/shared/PageHeader'

/** Stable shell only; raw bank facts are never read or displayed by this page. */
export default function Consumption() {
  return (
    <div>
      <PageHeader icon="◇" title="消费分析" subtitle="了解每个月花了多少钱、花在哪里，以及消费为何发生变化" />
      <section style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12 }}>
        <EmptyState
          icon={ReceiptText}
          title="消费分析正在建设中"
          desc="消费事件、分类与分析能力完成后，将在此提供正式的消费视图。"
        />
      </section>
    </div>
  )
}

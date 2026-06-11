/**
 * BrokerSyncTab — 券商 API 同步面板
 * 嵌入 Dashboard 的 ImportSection 作为第 4 个 tab。
 */
import { useState, useEffect, useCallback } from 'react'
import { RefreshCw, CheckCircle, XCircle, Clock, Loader2 } from 'lucide-react'
import { getSyncStatus, triggerSync, type SyncStatusItem } from '@/lib/broker-sync-api'

// 平台显示名映射（与 Dashboard.tsx 一致）
const PLATFORM_DISPLAY: Record<string, string> = { '雪盈证券': '盈透证券' }
function displayPlatform(p: string): string { return PLATFORM_DISPLAY[p] ?? p }

interface Props {
  onRefresh: () => void
}

const STATUS_CONFIG: Record<string, { icon: React.ReactNode; color: string; text: string }> = {
  success: { icon: <CheckCircle size={12} />, color: '#059669', text: '同步成功' },
  failed:  { icon: <XCircle size={12} />,    color: '#DC2626', text: '同步失败' },
  running: { icon: <Loader2 size={12} className="animate-spin" />, color: '#D97706', text: '同步中' },
  never:    { icon: <Clock size={12} />,      color: '#9CA3AF', text: '从未同步' },
  disabled: { icon: <Clock size={12} />,      color: '#D1D5DB', text: '演示模式' },
}

export function BrokerSyncTab({ onRefresh }: Props) {
  const [status, setStatus] = useState<SyncStatusItem[]>([])
  const [syncing, setSyncing] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  const fetchStatus = useCallback(async () => {
    try {
      const res = await getSyncStatus()
      setStatus(res.brokers)
    } catch (e) {
      console.error('获取同步状态失败', e)
    }
  }, [])

  useEffect(() => { fetchStatus() }, [fetchStatus])

  const handleSync = async (broker: 'tiger' | 'futu' | 'snowball' | 'guojin' | 'all') => {
    setSyncing(broker)
    setMessage(null)

    // 记录触发前的时间戳,用于检测是否有新 run
    const brokersToCheck = broker === 'all' ? ['tiger', 'futu', 'snowball'] : [broker]
    const prevTimes = Object.fromEntries(
      brokersToCheck.map(b => [b, status.find(s => s.broker === b)?.last_sync_time])
    )

    try {
      await triggerSync(broker)
      setMessage('⏳ 同步中...')

      // 轮询最多 30 秒,每 2 秒检查一次
      let attempts = 0
      const maxAttempts = 15
      while (attempts < maxAttempts) {
        await new Promise(r => setTimeout(r, 2000))
        attempts++
        const newStatus = await getSyncStatus()
        setStatus(newStatus.brokers)

        const allUpdated = brokersToCheck.every(b => {
          const item = newStatus.brokers.find(s => s.broker === b)
          return item && item.last_sync_time !== prevTimes[b]
        })

        if (allUpdated) {
          setMessage('✅ 同步完成')
          onRefresh()
          break
        }
      }

      if (attempts >= maxAttempts) {
        setMessage('⚠️ 同步可能仍在进行中,请稍后刷新查看')
      }
    } catch (e: any) {
      setMessage(`❌ 触发失败: ${e.message}`)
    } finally {
      setSyncing(null)
    }
  }

  const btnSync: React.CSSProperties = {
    padding: '4px 12px', fontSize: 12, fontWeight: 500, borderRadius: 6,
    border: 'none', cursor: 'pointer', color: '#fff', background: '#3B82F6',
    transition: 'background 0.15s',
  }
  const btnSyncAll: React.CSSProperties = {
    ...btnSync, width: '100%', padding: '8px 0', fontSize: 13, marginTop: 12,
    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
  }
  const btnDisabled: React.CSSProperties = { opacity: 0.5, cursor: 'not-allowed' }

  return (
    <div>
      <p style={{ fontSize: 12, color: '#6B7280', marginTop: 0, marginBottom: 14, lineHeight: 1.6 }}>
        通过券商 OpenAPI 自动同步持仓数据。每天北京时间 22:00 自动执行,也可手动触发。
      </p>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {status.map(item => {
          const cfg = STATUS_CONFIG[item.last_sync_status ?? 'never'] ?? STATUS_CONFIG.never
          const isSyncing = syncing === item.broker || syncing === 'all'
          return (
            <div key={item.broker} style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              background: '#F9FAFB', borderRadius: 8, padding: '10px 14px',
              border: '1px solid #E5E7EB',
            }}>
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: '#374151' }}>{displayPlatform(item.platform)}</span>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 11, color: cfg.color }}>
                    {isSyncing ? <Loader2 size={11} className="animate-spin" /> : cfg.icon}
                    {isSyncing ? '同步中...' : cfg.text}
                  </span>
                </div>
                <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 3 }}>
                  {item.last_sync_time
                    ? `上次: ${item.last_sync_time}${item.last_position_count != null ? ` · ${item.last_position_count} 条` : ''}`
                    : '尚未同步过'}
                </div>
                {item.error_message && (
                  <div style={{ fontSize: 11, color: '#DC2626', marginTop: 2, maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {item.error_message}
                  </div>
                )}
              </div>
              <button
                onClick={() => handleSync(item.broker as 'tiger' | 'futu' | 'snowball' | 'guojin')}
                disabled={isSyncing || syncing === 'all'}
                style={{ ...btnSync, ...(isSyncing || syncing === 'all' ? btnDisabled : {}) }}
              >
                {isSyncing ? '同步中' : '同步'}
              </button>
            </div>
          )
        })}
      </div>

      <button
        onClick={() => handleSync('all')}
        disabled={syncing !== null}
        style={{ ...btnSyncAll, ...(syncing !== null ? btnDisabled : {}) }}
      >
        <RefreshCw size={13} />
        {syncing === 'all' ? '同步中...' : '同步全部'}
      </button>

      {message && (
        <p style={{ fontSize: 12, color: '#6B7280', textAlign: 'center', marginTop: 10 }}>{message}</p>
      )}

      <p style={{ fontSize: 11, color: '#9CA3AF', textAlign: 'center', marginTop: 10 }}>
        下次自动同步: 今日 22:00 北京时间
      </p>
    </div>
  )
}

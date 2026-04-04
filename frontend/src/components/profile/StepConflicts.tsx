/**
 * Step 5 — 冲突检测（自动触发）
 * 有冲突 → 展示冲突卡片 + 两个选项按钮
 * 无冲突 → 直接进入 Step 6
 */
import React, { useEffect, useState } from 'react'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { profileApi, type UserProfile, type ConflictItem } from '@/lib/api'

const TARGET_RETURN_DOWN: Record<string, string> = {
  '>20%': '10-20%', '10-20%': '5-10%', '5-10%': '<5%', '<5%': '<5%',
}

interface Props {
  data: Partial<UserProfile>
  conflicts: ConflictItem[]
  onChange: (patch: Partial<UserProfile>) => void
  onConflictsChecked: (c: ConflictItem[]) => void
  onNext: () => void
  onPrev: () => void
}

export default function StepConflicts({ data, conflicts, onChange, onConflictsChecked, onNext, onPrev }: Props) {
  const [checking, setChecking] = useState(false)
  const [checked, setChecked]   = useState(false)
  const [resolved, setResolved] = useState<number[]>([])  // 已处理的冲突索引

  useEffect(() => {
    if (checked) return
    if (!data.max_drawdown || !data.target_return || !data.fund_usage_timeline) {
      setChecked(true)
      onConflictsChecked([])
      return
    }
    setChecking(true)
    profileApi
      .checkConflicts(data.max_drawdown, data.target_return, data.fund_usage_timeline)
      .then(res => {
        onConflictsChecked(res.conflicts)
        setChecked(true)
        if (res.conflicts.length === 0) onNext()
      })
      .finally(() => setChecking(false))
  }, [])  // eslint-disable-line

  function handleOption(idx: number, option: string) {
    const conflict = conflicts[idx]
    if (!conflict) return

    if (option === '优先收益') {
      // 提升风险等级 +1（上限5）
      const cur = data.risk_normalized_level ?? 3
      onChange({ risk_normalized_level: Math.min(cur + 1, 5) })
    } else {
      // 将 target_return 降一档
      const cur = data.target_return ?? '5-10%'
      onChange({ target_return: TARGET_RETURN_DOWN[cur] ?? cur })
    }
    setResolved(r => [...r, idx])
  }

  const unresolvedConflicts = conflicts.filter((_, i) => !resolved.includes(i))
  const allResolved = unresolvedConflicts.length === 0

  if (checking) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: 20, color: '#6B7280', fontSize: 13 }}>
        <Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} />
        正在检测目标一致性...
      </div>
    )
  }

  if (!checked) return null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {conflicts.length === 0 ? (
        <div style={{ padding: 16, background: '#F0FDF4', borderRadius: 10, border: '1px solid #BBF7D0', fontSize: 13, color: '#166534' }}>
          投资目标一致性检测通过，无冲突。
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, fontWeight: 600, color: '#92400E' }}>
            <AlertTriangle size={15} />
            发现 {conflicts.length} 个目标冲突，请选择解决方式
          </div>
          {conflicts.map((c, i) => (
            <div key={i} style={{
              padding: 16, background: '#FFFBEB', borderRadius: 10, border: '1px solid #FDE68A',
              opacity: resolved.includes(i) ? 0.5 : 1,
            }}>
              <div style={{ fontSize: 13, color: '#92400E', lineHeight: 1.6, marginBottom: 12 }}>
                {c.message}
              </div>
              {!resolved.includes(i) && (
                <div style={{ display: 'flex', gap: 8 }}>
                  {c.options.map(opt => (
                    <button
                      key={opt}
                      onClick={() => handleOption(i, opt)}
                      style={{
                        padding: '7px 16px', borderRadius: 8, fontSize: 12, fontWeight: 500,
                        border: '1px solid #F59E0B', background: '#fff', color: '#92400E',
                        cursor: 'pointer',
                      }}
                    >
                      {opt}
                    </button>
                  ))}
                </div>
              )}
              {resolved.includes(i) && (
                <div style={{ fontSize: 12, color: '#059669' }}>已解决</div>
              )}
            </div>
          ))}
        </>
      )}

      <div style={{ display: 'flex', gap: 10, marginTop: 4 }}>
        <button onClick={onPrev} style={{ padding: '8px 20px', borderRadius: 8, fontSize: 13, fontWeight: 500, background: '#fff', color: '#374151', border: '1px solid #E5E7EB', cursor: 'pointer' }}>上一步</button>
        {(conflicts.length === 0 || allResolved) && (
          <button onClick={onNext} style={{
            padding: '8px 24px', borderRadius: 8, fontSize: 13, fontWeight: 500,
            background: 'linear-gradient(135deg,#3B82F6,#1D4ED8)', color: '#fff', border: 'none', cursor: 'pointer',
          }}>下一步</button>
        )}
      </div>
    </div>
  )
}

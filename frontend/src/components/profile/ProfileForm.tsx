/**
 * ProfileForm — 无画像时的填写页
 * 模块A + 模块B，A确认后B才解锁
 */
import React, { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { profileApi, type UserProfile } from '@/lib/api'
import ModuleA from './ModuleA'
import ModuleB from './ModuleB'

const RISK_TYPE: Record<number, string> = { 1: '保守型', 2: '稳健型', 3: '平衡型', 4: '成长型', 5: '进取型' }

interface Props {
  onProfileCreated: (profile: UserProfile) => void
}

export default function ProfileForm({ onProfileCreated }: Props) {
  const [data, setData]               = useState<Partial<UserProfile>>({})
  const [moduleADone, setModuleADone] = useState(false)
  const [moduleAOpen, setModuleAOpen] = useState(true)
  const [isGenerating, setIsGenerating] = useState(false)
  const [genError, setGenError]       = useState('')

  function patchData(patch: Partial<UserProfile>) {
    setData(prev => ({ ...prev, ...patch }))
  }

  function handleModuleAConfirm() {
    setModuleADone(true)
    setModuleAOpen(false)
  }

  async function handleGenerate() {
    setIsGenerating(true)
    setGenError('')
    try {
      await profileApi.save(data)
      const result = await profileApi.generate()
      const full = await profileApi.get()
      onProfileCreated({ ...full, ...result } as UserProfile)
    } catch (e) {
      setGenError(`生成失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setIsGenerating(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* 模块A */}
      <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #E5E7EB', overflow: 'hidden' }}>
        <div
          style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 18px', cursor: moduleADone ? 'pointer' : 'default', borderBottom: moduleAOpen ? '1px solid #F3F4F6' : 'none' }}
          onClick={() => moduleADone && setModuleAOpen(o => !o)}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 14, fontWeight: 700, color: '#1B2A4A' }}>模块A：风险评估 + 基础信息</span>
            {moduleADone && <span style={{ fontSize: 11, padding: '2px 8px', background: '#D1FAE5', color: '#065F46', borderRadius: 20, fontWeight: 600 }}>已完成</span>}
          </div>
          {moduleADone && (moduleAOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />)}
        </div>

        {/* 折叠后的摘要 */}
        {moduleADone && !moduleAOpen && (
          <div style={{ padding: '10px 18px', fontSize: 13, color: '#6B7280' }}>
            风险等级：R{data.risk_normalized_level} {data.risk_normalized_level ? RISK_TYPE[data.risk_normalized_level] : ''} &nbsp;|&nbsp; 收入稳定性：{data.income_stability ?? '—'} &nbsp;|&nbsp; 资金使用：{data.fund_usage_timeline ?? '—'}
          </div>
        )}

        {moduleAOpen && (
          <div style={{ padding: '18px' }}>
            <ModuleA data={data} onChange={patchData} onConfirm={handleModuleAConfirm} />
          </div>
        )}
      </div>

      {/* 模块B */}
      <div style={{ background: '#fff', borderRadius: 12, border: `1px solid ${moduleADone ? '#E5E7EB' : '#F3F4F6'}`, overflow: 'hidden' }}>
        <div style={{ padding: '14px 18px', borderBottom: '1px solid #F3F4F6' }}>
          <span style={{ fontSize: 14, fontWeight: 700, color: moduleADone ? '#1B2A4A' : '#9CA3AF' }}>模块B：投资目标</span>
        </div>
        <div style={{ padding: '18px' }}>
          <ModuleB
            locked={!moduleADone}
            data={data}
            onChange={patchData}
            onGenerate={handleGenerate}
            isGenerating={isGenerating}
          />
        </div>
      </div>

      {genError && (
        <div style={{ padding: '10px 14px', background: '#FEE2E2', borderRadius: 8, fontSize: 13, color: '#B91C1C' }}>
          {genError}
        </div>
      )}
    </div>
  )
}

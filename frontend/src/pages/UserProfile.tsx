/**
 * UserProfile — 用户画像
 * 两个状态：无画像 → 填写页；已有画像 → 结果页
 * v3.6.3: 新增投资理念板块（MD 文档查阅/上传/下载）
 */
import React, { useEffect, useState, useRef } from 'react'
import { Loader2, User, Download, Upload, RefreshCw, ChevronUp, ChevronDown } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { profileApi, philosophyApi, type UserProfile as TUserProfile } from '@/lib/api'
import ProfileForm from '@/components/profile/ProfileForm'
import ProfileResult from '@/components/profile/ProfileResult'

export default function UserProfile() {
  const [profile, setProfile] = useState<TUserProfile | null>(null)
  const [loading, setLoading] = useState(true)

  // 投资理念状态
  const [philosophy, setPhilosophy] = useState<{ source: string; content: string } | null>(null)
  const [philOpen, setPhilOpen] = useState(true)
  const [philUploading, setPhilUploading] = useState(false)
  const philFileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    Promise.all([
      profileApi.get().then(data => {
        if (data && Object.keys(data).length > 0) setProfile(data as TUserProfile)
      }),
      philosophyApi.get().then(setPhilosophy),
    ])
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  async function handlePhilUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setPhilUploading(true)
    try {
      const result = await philosophyApi.upload(file)
      setPhilosophy(result)
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : '上传失败')
    } finally {
      setPhilUploading(false)
      if (philFileRef.current) philFileRef.current.value = ''
    }
  }

  async function handlePhilReset() {
    if (!confirm('确定恢复默认投资理念？当前内容将丢失。')) return
    try {
      const result = await philosophyApi.reset()
      setPhilosophy(result)
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : '恢复失败')
    }
  }

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 300, gap: 8, color: '#9CA3AF' }}>
        <Loader2 size={18} style={{ animation: 'spin 1s linear infinite' }} />
        <span style={{ fontSize: 13 }}>加载中…</span>
      </div>
    )
  }

  return (
    <div>
      {/* 页面标题 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <div style={{
          width: 38, height: 38, borderRadius: 10,
          background: 'linear-gradient(135deg, #1B2A4A, #2D4A7A)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <User size={18} color="#fff" />
        </div>
        <div>
          <div style={{ fontSize: 20, fontWeight: 700, color: '#1B2A4A', letterSpacing: -0.3 }}>用户画像</div>
          <div style={{ fontSize: 12, color: '#9CA3AF', marginTop: 1 }}>风险评估 · 投资目标 · 投资理念</div>
        </div>
      </div>

      {profile
        ? <ProfileResult profile={profile} onUpdate={setProfile} />
        : <ProfileForm onProfileCreated={setProfile} />
      }

      {/* ── 投资理念板块 ── */}
      <div style={{
        background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12,
        overflow: 'hidden', marginTop: 20,
      }}>
        {/* 折叠标题行 */}
        <div
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '14px 18px', cursor: 'pointer',
          }}
          onClick={() => setPhilOpen(v => !v)}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 14, fontWeight: 700, color: '#1B2A4A' }}>投资理念</span>
            <span style={{
              fontSize: 10, fontWeight: 500, padding: '2px 7px', borderRadius: 10,
              background: '#F0FDF4', color: '#16A34A',
            }}>官方版</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }} onClick={e => e.stopPropagation()}>
            <input ref={philFileRef} type="file" accept=".md,.txt" style={{ display: 'none' }} onChange={handlePhilUpload} />
            {philOpen && (
              <>
                {/* 下载 */}
                {philosophy && (
                  <button
                    style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '5px 12px', borderRadius: 6, fontSize: 12, fontWeight: 500, background: '#F3F4F6', color: '#374151', border: 'none', cursor: 'pointer' }}
                    onClick={() => {
                      const blob = new Blob([philosophy.content], { type: 'text/markdown' })
                      const url = URL.createObjectURL(blob)
                      const a = document.createElement('a')
                      a.href = url
                      a.download = 'investment_philosophy.md'
                      a.click()
                      URL.revokeObjectURL(url)
                    }}
                  >
                    <Download size={12} />下载
                  </button>
                )}
                {/* 上传 */}
                <button
                  style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '5px 12px', borderRadius: 6, fontSize: 12, fontWeight: 500, background: '#F3F4F6', color: '#374151', border: 'none', cursor: 'pointer' }}
                  disabled={philUploading}
                  onClick={() => philFileRef.current?.click()}
                >
                  {philUploading ? <Loader2 size={12} style={{ animation: 'spin 1s linear infinite' }} /> : <Upload size={12} />}上传
                </button>
                {/* 恢复默认 */}
                <button
                  style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '5px 12px', borderRadius: 6, fontSize: 12, fontWeight: 500, background: '#F3F4F6', color: '#374151', border: 'none', cursor: 'pointer' }}
                  onClick={handlePhilReset}
                >
                  <RefreshCw size={12} />恢复默认
                </button>
              </>
            )}
            {philOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </div>
        </div>

        {/* 内容 */}
        {philOpen && philosophy && (
          <div style={{ padding: '16px 24px 20px' }}>
            <PhilosophyContent content={philosophy.content} />
          </div>
        )}
      </div>
    </div>
  )
}


// ── 投资理念 MD 渲染（参照 Discipline.tsx 的 HandbookContent）──

function PhilosophyContent({ content }: { content: string }) {
  // 去掉 YAML frontmatter
  const body = content.replace(/^---[\s\S]*?---\s*/, '').trim()

  // 拆分为前言 + 按 ## 标题分段
  const { preamble, sections } = parsePhilosophy(body)
  const [openIdx, setOpenIdx] = useState<number | null>(null)

  return (
    <div>
      {preamble && (
        <div className="handbook-md" style={{ marginBottom: 12 }}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{preamble}</ReactMarkdown>
        </div>
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {sections.map((sec, i) => {
          const isOpen = openIdx === i
          return (
            <div key={i} style={{ border: '1px solid #E5E7EB', borderRadius: 8, overflow: 'hidden' }}>
              <div
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '10px 14px', cursor: 'pointer',
                  background: isOpen ? '#F0F7FF' : '#FAFAFA',
                  transition: 'background 0.1s',
                  borderBottom: isOpen ? '1px solid #E5E7EB' : undefined,
                }}
                onClick={() => setOpenIdx(isOpen ? null : i)}
              >
                <div style={{ fontSize: 13, fontWeight: 600, color: '#1B2A4A', lineHeight: 1.4 }}>
                  {sec.title}
                </div>
                {isOpen
                  ? <ChevronUp size={14} color="#9CA3AF" style={{ flexShrink: 0 }} />
                  : <ChevronDown size={14} color="#9CA3AF" style={{ flexShrink: 0 }} />}
              </div>
              {isOpen && (
                <div style={{ padding: '14px 16px', background: '#fff' }}>
                  <div className="handbook-md">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{sec.body.trim()}</ReactMarkdown>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}


function parsePhilosophy(md: string): { preamble: string; sections: Array<{ title: string; body: string }> } {
  // 拆分标题行（## 开头）
  const lines = md.split('\n')
  const sections: Array<{ title: string; body: string }> = []
  let preambleLines: string[] = []
  let currentTitle = ''
  let currentBody: string[] = []

  for (const line of lines) {
    if (line.match(/^##\s/)) {
      if (currentTitle) {
        sections.push({ title: currentTitle, body: currentBody.join('\n') })
      }
      currentTitle = line.replace(/^##\s+/, '').trim()
      currentBody = []
    } else if (currentTitle) {
      currentBody.push(line)
    } else {
      preambleLines.push(line)
    }
  }
  if (currentTitle) {
    sections.push({ title: currentTitle, body: currentBody.join('\n') })
  }

  // 前言去掉 h1 标题行和水平分隔线（---）
  const preamble = preambleLines.join('\n')
    .replace(/^#\s[^\n]*\n?/, '')
    .replace(/^---+\s*$/gm, '')
    .trim()

  return { preamble, sections }
}

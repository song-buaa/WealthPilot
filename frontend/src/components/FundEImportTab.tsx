/**
 * FundEImportTab — 基金账户 Excel 持仓导入
 * 嵌入 Dashboard ImportSection 的第 5 个 tab。
 */
import { useState, useRef } from 'react'
import { Upload, Loader2 } from 'lucide-react'

interface ImportResult {
  total: number
  platforms: Record<string, number>
  message: string
}

interface Props {
  onRefresh: () => void
}

export function FundEImportTab({ onRefresh }: Props) {
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<ImportResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setLoading(true)
    setError(null)
    setResult(null)

    try {
      const formData = new FormData()
      formData.append('file', file)
      const res = await fetch('/api/portfolio/import/fund-e-excel', {
        method: 'POST',
        body: formData,
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '导入失败')
      setResult(data)
      onRefresh()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '导入失败')
    } finally {
      setLoading(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  return (
    <div>
      <p style={{ fontSize: 12, color: '#6B7280', marginTop: 0, marginBottom: 12, lineHeight: 1.6 }}>
        从基金账户 App 导出持仓 Excel，上传后自动解析全市场公募基金持仓。
        按销售平台分组<strong>替换</strong>境内基金持仓，不影响境外券商和中信证券。
      </p>

      <div style={{ background: '#F9FAFB', borderRadius: 6, border: '1px solid #F3F4F6', padding: '8px 12px', fontSize: 11, color: '#6B7280', marginBottom: 14, lineHeight: 1.7 }}>
        <strong style={{ color: '#374151' }}>操作步骤：</strong><br />
        ① 基金账户 App → 持有查询 → 右上角导出<br />
        ② 填写邮箱 + 短信验证码，等待邮件<br />
        ③ 解压附件压缩包（密码为身份证后6位）<br />
        ④ 上传解压后的 .xlsx 文件
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <button
          onClick={() => inputRef.current?.click()}
          disabled={loading}
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '7px 16px', borderRadius: 8, fontSize: 13, fontWeight: 500,
            border: 'none', cursor: loading ? 'not-allowed' : 'pointer',
            background: '#3B82F6', color: '#fff', opacity: loading ? 0.6 : 1,
          }}
        >
          {loading ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
          {loading ? '导入中...' : '选择文件导入'}
        </button>
      </div>
      <input ref={inputRef} type="file" accept=".xlsx" style={{ display: 'none' }} onChange={handleFile} />

      {result && (
        <div style={{ marginTop: 12, padding: '10px 14px', background: '#F0FDF4', border: '1px solid #BBF7D0', borderRadius: 8 }}>
          <div style={{ fontSize: 13, fontWeight: 500, color: '#166534', marginBottom: 6 }}>✅ {result.message}</div>
          <div style={{ fontSize: 11, color: '#6B7280', lineHeight: 1.7 }}>
            {Object.entries(result.platforms).map(([plat, cnt]) => (
              <div key={plat}>{plat}：{cnt} 条</div>
            ))}
          </div>
        </div>
      )}

      {error && (
        <div style={{ marginTop: 12, padding: '10px 14px', background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: 8, fontSize: 13, color: '#991B1B' }}>
          ❌ {error}
        </div>
      )}
    </div>
  )
}

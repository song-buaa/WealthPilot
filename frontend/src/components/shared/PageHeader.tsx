/**
 * PageHeader — 页面标题三件套（按 WealthPilot UI 设计规范第四节）
 *
 * 图标容器：38x38 圆角10px ocean渐变背景
 * 标题：20px/700/#1B2A4A
 * 副标题：12px/400/#9CA3AF
 */
interface PageHeaderProps {
  icon: string
  title: string
  subtitle?: string
}

export default function PageHeader({ icon, title, subtitle }: PageHeaderProps) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
      <div style={{
        width: 38, height: 38, borderRadius: 10,
        background: 'linear-gradient(135deg, #1B2A4A, #2D4A7A)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 17,
      }}>
        {icon}
      </div>
      <div>
        <div style={{ fontSize: 20, fontWeight: 700, color: '#1B2A4A', letterSpacing: '-0.3px' }}>
          {title}
        </div>
        {subtitle && (
          <div style={{ fontSize: 12, color: '#9CA3AF', marginTop: 1 }}>
            {subtitle}
          </div>
        )}
      </div>
    </div>
  )
}

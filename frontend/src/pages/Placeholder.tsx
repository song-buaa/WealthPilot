/**
 * Placeholder — 通用占位页
 * 文字内容源自 app_pages/placeholder.py，保持原文不改动
 */
import { useParams } from 'react-router-dom'

// ── 描述文字映射（保留 Streamlit 版原文）──────────────────

const DESCRIPTIONS: Record<string, string> = {
  用户画像和投资目标:
    '从风险偏好、投资期限、收入结构、家庭情况等维度建立用户画像，' +
    '确立个人投资目标（绝对收益 / 相对收益 / 资产保值）和投资风格基调（稳健 / 平衡 / 积极）。\n\n' +
    '将覆盖的功能：风险测评问卷、目标收益率设定、可接受最大回撤、' +
    '投资期限、资产规模分层、投资风格标签。',

  新增资产配置:
    '针对新用户初始建仓，或新增一笔资金（如年终奖、季度奖金）进行增量资产配置方案设计，' +
    '结合当前持仓、用户画像和市场环境，给出具体的配置建议。\n\n' +
    '将覆盖的功能：增量资金录入、现有持仓扫描、缺口分析、' +
    '分批买入计划生成、配置方案对比。',

  投资记录:
    '记录每笔买入、卖出操作及决策理由，形成可追溯的投资行为日志，' +
    '定期复盘操作质量，识别重复性错误与行为偏差。\n\n' +
    '将覆盖的功能：操作日志录入、决策理由归档、' +
    '纪律遵守率统计、复盘分析报告。',

  收益分析:
    '每次登录时自动快照账户总览数据，跟踪整体资产配置组合的历史表现，' +
    '分析收益归因（大盘贡献 vs 选股贡献 vs 择时贡献）。\n\n' +
    '将覆盖的功能：净值曲线、收益归因分解、' +
    '最大回撤追踪、阶段性表现对比。',

  生活账户总览:
    '汇总非投资性资产与负债：个人养老金、企业年金、社保余额、住房公积金等资产，' +
    '以及房贷、信用贷、信用卡等负债，形成完整的生活财务画像。\n\n' +
    '将覆盖的功能：公积金 / 社保余额录入、养老金账户同步、' +
    '房贷剩余本金追踪、信用卡额度与账单汇总。',

  养老规划:
    '整合个人养老金、企业年金、商业养老险的投资规划，' +
    '结合预期退休时间和养老支出目标，测算资金缺口与月供方案。\n\n' +
    '将覆盖的功能：退休年龄 / 支出目标设定、养老金账户汇总、' +
    '资金缺口测算、分阶段储蓄建议。',

  购房规划:
    '购房资金规划（首付来源、时间节点）、房贷方案比较，' +
    '以及购房后对整体资产负债结构的影响分析。\n\n' +
    '将覆盖的功能：首付倒推计划、等额还款 vs 等额本金对比、' +
    '提前还款影响测算、购房后资产负债重新评估。',

  消费规划:
    '购车、旅行、大额消费等支出规划，信用卡账单分析，' +
    '月度 / 年度支出预算管理，帮助在财富积累阶段控制生活支出。\n\n' +
    '将覆盖的功能：大额消费计划录入、信用卡账单导入分析、' +
    '月度预算设定、超支告警。',

  个人资产负债总览:
    '合并投资规划（投资账户）和财务规划（生活账户）的所有资产与负债，' +
    '计算个人完整的净资产，提供全面的财务健康度视图。\n\n' +
    '将覆盖的功能：全资产 / 全负债汇总、净资产趋势、' +
    '资产分布热力图、财务健康度评分。',

  家族资产负债总览:
    '连接多个成员账户（配偶、子女等），合并计算家庭整体资产负债结构，' +
    '支持家庭层面的财富传承规划与税务优化参考。\n\n' +
    '将覆盖的功能：多账户绑定、家庭净资产汇总、' +
    '成员贡献分析、家庭财务健康报告。',
}

const DEFAULT_DESC = '该模块正在规划建设中，敬请期待。'

// ── 组件 ──────────────────────────────────────────────────

interface PlaceholderProps {
  name?: string
  icon?: string
}

export default function Placeholder({ name: nameProp, icon = '🚧' }: PlaceholderProps) {
  // 支持两种用法：直接传 name prop，或从路由参数获取
  const params = useParams<{ name: string }>()
  const name = nameProp ?? params.name ?? '功能规划中'
  const desc = DESCRIPTIONS[name] ?? DEFAULT_DESC

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        minHeight: 400,
        padding: '48px 24px',
        textAlign: 'center',
        color: '#9CA3AF',
      }}
    >
      {/* 图标 */}
      <div style={{ fontSize: 40, marginBottom: 16, opacity: 0.5 }}>{icon}</div>

      {/* 标题 */}
      <div
        style={{
          fontSize: 16, fontWeight: 600, color: '#6B7280',
          marginBottom: 8,
        }}
      >
        {name}
      </div>

      {/* 提示标签 */}
      <div
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          padding: '4px 12px', borderRadius: 20,
          background: '#FEF3C7', color: '#92400E',
          fontSize: 12, fontWeight: 500,
          marginBottom: 16,
          border: '1px solid #FDE68A',
        }}
      >
        🚧 该模块正在建设中
      </div>

      {/* 描述 */}
      <div
        style={{
          fontSize: 13, color: '#9CA3AF',
          maxWidth: 420, lineHeight: 1.7,
          whiteSpace: 'pre-line',
          textAlign: 'left',
        }}
      >
        {desc}
      </div>

      {/* 底部提示 */}
      <div
        style={{
          marginTop: 32, fontSize: 12, color: '#D1D5DB',
          display: 'flex', alignItems: 'center', gap: 6,
        }}
      >
        📐 功能规划中，敬请期待
      </div>
    </div>
  )
}

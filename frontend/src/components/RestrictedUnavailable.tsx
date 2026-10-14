import { Link } from 'react-router-dom'

export default function RestrictedUnavailable() {
  return <section role="status" style={{ padding: 28 }}>
    <h2>此功能尚未开放</h2>
    <p>当前生产 Web 支持财富总览、投资账户查询和已启用券商的手动同步。</p>
    <p>投资决策、交易、导入和其他页面暂不可用。</p>
    <Link to="/wealth">返回财富总览</Link>
  </section>
}

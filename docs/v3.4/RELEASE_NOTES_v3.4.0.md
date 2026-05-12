# v3.4.0 - 多券商市场能力扩展

WealthPilot 第一次实现真实券商下单。从 Mock 到真实 Tiger 老虎证券 API 的完整切换,同时新增 Symbol 标准化和美股新建仓评估能力。

## 三条核心故事线

### 1. Tiger 真实接入 (M0-M6)

- 6 个里程碑: SDK 探索 -> Adapter 实现 -> 凭证管理 -> OrderManager 路由 -> UI 调整 -> 实盘验证
- 美港双市场沙箱验证: 港股 7/7 + 美股 6/6
- 港股实盘验证: 众安在线 06060 BUY+SELL 全链路(成本 RMB 41)
- 4 个安全闸门: paper-only / market 白名单 / outside_rth=False / MARKET 单拒绝

### 2. Symbol 标准化

- 全系统统一 TICKER:MARKET 格式(如 LI:US / 0700:HK)
- 港股 zfill(4) 对齐港交所官方标准
- broker_sync 3 个 Adapter + market_data 4 个服务全部对齐
- 修复 v2 定义但未落地的历史债

### 3. 新建仓评估 (M8)

- 用户可以问"苹果能不能买"得到完整分析
- Alpha Vantage 数据驱动(PE/PB/EPS/52 周/分析师评级)
- 投资纪律对新建仓场景适配(白名单规则 + 语义改写)
- 港股新建仓友好拦截("v3.5 接入 Tiger 行情后支持")

## 数字

- 183 单元测试全绿
- 13/15 端到端验收项真实验证通过
- 27 条 positions 数据迁移(currency 字段修复)
- 30+ 中文公司名 -> TICKER:MARKET 映射表

## 已知限制

- A 股交易暂不支持(v3.5 通过国金 QMT 接入)
- 港股新建仓评估暂不支持(v3.5 接入 Tiger 行情后开放)
- Alpha Vantage 限额: 多 key 轮换至 100 次/天(4 个免费 key)
- not_found 重试 / partially_filled 真实场景未触发(代码逻辑由单测覆盖)

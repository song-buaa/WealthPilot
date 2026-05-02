# WealthPilot 投资纪律体系概览

本文档基于 `app/discipline/` 模块的真实代码实现，
描述 WealthPilot 完整投资纪律体系的 11 条规则。
完整规则细节和阈值参数见 `handbook_official.md`（v1.4）。

## 三引擎架构

WealthPilot 把纪律检查拆分为 3 层引擎，按"硬约束 → 策略判断 → 行为约束"分层：

| 层级 | 引擎 | 代码位置 | 职责 | 触发动作 |
|------|------|---------|------|----------|
| Layer 1 | Risk Engine | `app/discipline/risk_engine.py` | 硬性约束检查（仓位/杠杆/流动性等） | BLOCK |
| Layer 2 | Decision Engine | `app/discipline/decision_engine.py` | 策略判断（止损逻辑/底仓/动态仓位） | 建议 |
| Layer 3 | Psychology Engine | `app/discipline/psychology_engine.py` | 行为约束（情绪冷却/单日跌幅熔断） | 冻结 |

编排器：`app/discipline/engine_runner.py`
配置源：`app/discipline/config.py`（同步自 `data/handbook_official.md` v1.4）

## 11 条纪律清单

### 仓位与杠杆类（Layer 1 - Risk Engine）

#### 纪律 1：杠杆工具分级管理
- 代码：`risk_engine.py:_check_leverage()`
- Level 0 禁止：期权、融资融券、借贷投资 → BLOCK
- Level 1 杠杆 ETF 上限：`level_1_max_pct = 0.05`（5%）→ 超限 BLOCK
- 总杠杆率分档：≤1.05 正常 / 1.05~1.20 可接受 / 1.20~1.35 警戒 / >1.35 超限

#### 纪律 2：偏离度控制与再平衡
- 代码：`risk_engine.py:_check_deviation()`
- 警告阈值：`deviation_warning_pct = 0.10`（偏离 10%）
- 强制再平衡：`deviation_force_rebalance_pct = 0.20`（偏离 20%）→ BLOCK

#### 纪律 3：单标的仓位上限
- 代码：`risk_engine.py:_check_position_limit()` + `decision_engine/rule_engine.py:check()`（决策管道简化版）
- 硬性上限：`max_position_pct = 0.40`（40%）→ 超限 BLOCK
- 警戒区：`warning_position_pct = 0.30`（30%~40%）→ 禁止加仓
- 决策管道简化版判定：position_ratio >= 1.0 → violation；>= 0.8 → warning

#### 纪律 4：流动性管理（子弹纪律）
- 代码：`risk_engine.py:_check_liquidity()`
- 阈值：`min_cash_pct = 0.20`（流动性 >= 20%）→ 加仓后低于则 BLOCK
- 极端储备：`extreme_reserve_pct = 0.10`

#### 纪律 5：资产配置区间（跨资产约束）
- 代码：`config.py:asset_allocation_ranges`
- 权益：40%~80%
- 固收：20%~60%
- 另类 / 衍生品：各 <= 10%
- 货币：10,000~100,000 元

#### 纪律 6：加仓节奏纪律
- 代码：`risk_engine.py:_check_add_rhythm()`
- 单次加仓上限：`max_single_add_pct = 0.10`（10%）→ 超限 BLOCK
- 最少分批：`min_batches_required = 2`
- 最小间隔：`min_interval_between_adds_days = 1` 个交易日 → 违反 BLOCK

### 策略判断类（Layer 2 - Decision Engine）

#### 纪律 7：止损与逻辑判断
- 代码：`decision_engine.py:_rule7_stop_loss()`
- 硬止损：`logic_intact = False`（投资逻辑被破坏）→ SELL
- 软止损：回撤 >= `soft_stop_review_trigger_pct = 0.30`（30%）→ 强制复核（不自动卖出）

#### 纪律 8：账户级防御熔断
- 代码：`risk_engine.py:_check_circuit_breaker()`
- 触发：`drawdown_trigger_pct = 0.25`（账户回撤 25%）→ 暂停一切加仓
- 恢复：`resume_threshold_pct = 0.15`（回撤收窄至 15%）

#### 纪律 9：长期持仓底仓机制
- 代码：`decision_engine.py:_rule10_core_floor()`
- 阈值：`core_holding_floor_pct = 0.10`（10% 底仓）
- 触发：核心持仓（持有 >= 1 年）卖出后仓位 < 10% → 警告

#### 纪律 10：动态仓位管理 + 左侧交易
- 代码：`decision_engine.py:_rule1_dynamic_position()` + `_rule2_left_side()`
- 逆向操作：利好 + 上涨 → 分批减仓；利空 + 下跌 + 逻辑完好 → 逆向加仓
- 左侧交易：下跌趋势建仓，上涨趋势卖出，追涨 HOLD
- 做 T 策略：卖出后回调 >= 20% 加仓，>= 10% 买回原仓位

### 行为约束类（Layer 3 - Psychology Engine）

#### 纪律 11：情绪冷却与禁止交易
- 代码：`psychology_engine.py:run()`
- 4 种禁止情绪：不甘心 / 贪婪 / 恐慌 / 侥幸 → 24 小时冷却期
- 单日净值跌幅：`daily_nav_drop_trigger_pct = 0.05`（5%）→ 24 小时冷却期
- 冷却期内禁止一切操作

## 决策管道与完整体系的关系

PositionDecision 决策管道（本 Skill 描述的工作流）在 Step 3 仅调用 `decision_engine/rule_engine.py`，
只检查纪律 3（单标仓位上限）。

完整 11 条纪律检查由 `engine_runner.py` 编排，主要在以下场景使用：
- 投资纪律页面（用户主动查看纪律状态）
- 组合健康度评估
- 异步纪律审计

这种"决策管道简化 + 完整体系异步"的分流设计，是为了平衡决策响应延迟和纪律完整性。

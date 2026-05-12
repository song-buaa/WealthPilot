# Symbol 标准化专项 — 已知问题

> 更新日期: 2026-05-12 | 专项版本: M1

---

## Issue 1: positions 表港股 currency 字段错误 (CNY 而非 HKD)

**现象**: positions 表中部分港股标的的 `currency` 字段为 `CNY` 而非 `HKD`。

| id | ticker | name | currency (实际) | currency (应为) |
|----|--------|------|:---:|:---:|
| 26 | 00068 | MANYCORE TECH | CNY | HKD |
| 27 | 01879 | 曦智科技-P | CNY | HKD |

**推测根因**: `PositionUpsertService` 在将 broker_sync 快照写入业务 positions 表时,做了汇率换算(`market_value_cny`, `fx_rate_to_cny`),但同时将 `currency` 字段覆盖为 CNY(换算后的币种),而非保留原始币种 HKD。

**影响**:
- `infer_symbol_from_ticker("00068", "CNY")` 无法推断为 `0068:HK`,迁移脚本跳过这些行
- `fx_rate_to_cny` 如果以 CNY→CNY 计算则汇率为 1,但实际应为 HKD→CNY (约 0.87),导致 amount_cny_equivalent 可能不准
- `_infer_symbol()` 在 API 层也无法正确识别这些港股持仓

**修复优先级**: v3.4 收尾前修。M6 实盘验证报告会暴露此问题。

**处理方式**: 留给 v3.4 主线处理,Symbol 专项不修。需要检查 `PositionUpsertService._build_business_position()` 中 currency 字段的赋值逻辑。

---

## Issue 2: Tiger SDK 港股代码格式 (4 位 vs 5 位)

**现象**: WealthPilot 内部标准化为 4 位港股代码(`0700:HK`),但 Tiger SDK 期望 5 位代码(`00700`)。

**状态**: **已修复** (M1 实施中发现并修复)。

**修复内容**: 在 `TigerBrokerAdapter.place_order()` 中,传给 `Contract()` 前对港股代码做 `zfill(5)` 转换:
```python
# backend/services/action/brokers/tiger.py:187-188
tiger_symbol = pure_symbol.zfill(5) if market == "HK" and pure_symbol.isdigit() else pure_symbol
contract = Contract(symbol=tiger_symbol, ...)
```

**注意**: 此修复仅在 `place_order` 路径。如果后续新增 Tiger SDK 调用路径(如 `get_positions`、`get_order_status` 中需要构造 Contract),也需要同样做 zfill(5)。建议 M3 时抽取为内部方法 `_to_tiger_code(pure_symbol, market)`。

---

## Issue 3: 6 位 CNY 代码无法区分 A 股 vs 基金

**现象**: 中国基金代码与 A 股代码共享 6 位数字空间(如 `000001` 可能是平安银行也可能是华夏成长基金)。仅凭 `ticker + currency=CNY` 无法可靠区分。

**当前策略**: `infer_symbol_from_ticker()` 对所有 6 位 CNY 代码保守返回 `None`,不做推断。

**影响**: positions 表中约 190 行 CNY 基金/A 股持仓的 `symbol_v2` 字段未被填充。

**处理方式**: 接受现状。A 股交易走 v3.5 国金 QMT,基金不交易。broker_sync 层(有明确 `market` 字段)是 A 股正确 symbol 的数据源。

# v3.11 技术债登记

## 1. Position 业务表丢失完整 symbol

**根因**: `PositionSnapshot` 表有完整 `symbol`（如 `LI:US`），但 `PositionUpsertService._denormalize_ticker()` 在 upsert 到 Position 业务表时只保留纯 ticker（`LI`），丢掉了 `:MARKET` 后缀。后续链路（position_aggregator → AggregatedPosition → PositionInfo → 前端）全程只有纯 ticker。

**影响**: 任何需要 `TICKER:MARKET` 格式的功能（如执行计划需要精确查行情/K线）都需要从 currency 推断 market，不是真值。

**当前兜底**: `_serialize_target_position` 用 `market_data.quote.currency`（真实行情来源）映射 market，标记 `symbol_source`。

**建议**: Position 表新增 `symbol` 列（`TICKER:MARKET` 格式），upsert 时保留完整 symbol 不做 denormalize。或在 `_denormalize_ticker` 之外另存一个 `normalized_symbol` 字段。

## 2. 港股 4 位 vs 5 位 symbol 不一致

**根因**: WP 内部标准化港股为 4 位 zfill(4)（如 `0700:HK`），但 Tiger SDK 和富途 SDK 都需要 5 位（`00700` / `HK.00700`）。`symbol_to_tiger_ticker("0700:HK")` → `"0700"`，Tiger 查不到；`symbol_to_futu("0700:HK")` → `"HK.0700"`，富途 ret=-1。

**影响**: 港股标的在因子计算、行情获取时需要用 5 位代码（如 `00700:HK`）才能正常工作。4 位码在两个 SDK 端均查不到数据。

**当前兜底**: 用户或决策链路如果传 `00700:HK` 则正常工作；传 `0700:HK` 则降级。

**建议**: 统一港股 symbol 标准为 5 位（`00700:HK`），或在 SDK adapter 层自动补零。需要全链路排查：`normalize_symbol` / `symbol_to_tiger_ticker` / `symbol_to_futu` / 持仓表。

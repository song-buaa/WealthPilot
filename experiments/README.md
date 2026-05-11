# Experiments — 探索性脚本

本目录存放 v3.x 演进过程中的探索性脚本，
用于验证新数据源、新模块的可行性。

## 当前内容

### market_data_m0/
v3.1 市场数据接入探索：
- `verify_m3a.py`：Futu 资金流向数据接入验证（v3.1 M3-a）
- `verify_m3b.py`：Tiger K 线技术指标接入验证（v3.1 M3-b）

这些脚本已在 v3.1 release 中正式集成（见 backend/market_data/），
此处保留作为 v3.3 接入新行情数据源时的参考。

## 不保证可用

experiments/ 下的脚本不在 CI / 测试范围内，可能因依赖升级而失效。
当前活代码请参考 backend/。

"""
执行计划引擎包。

sys.path 注入: 确保 backend/ 在 sys.path 中，使 factors.py / rule_engine.py
内部的 `from utils.symbol import ...` / `from services.market_data.xxx import ...`
短路径 import 在 uvicorn 环境下也能工作。

背景: 项目大量模块使用相对 backend/ 的短路径 import（与 broker_sync、
executing_agent 一致），依赖 sys.path 包含 backend/ 目录。uvicorn 从项目根
启动时 sys.path[0] 是项目根而非 backend/，需要显式注入。
"""
import sys
from pathlib import Path

_backend_dir = str(Path(__file__).parent.parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

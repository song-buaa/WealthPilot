"""
日期时间序列化工具。

项目约定：DB 存 UTC（datetime.utcnow），API 序列化时拼接 Z 后缀，
前端 new Date("...Z") 自动转换为本地时间。
"""
from datetime import datetime
from typing import Optional


def utc_iso(dt: Optional[datetime]) -> Optional[str]:
    """将 UTC datetime 序列化为带 Z 后缀的 ISO 8601 字符串。"""
    return dt.isoformat() + "Z" if dt else None

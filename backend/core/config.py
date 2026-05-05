"""
应用配置（从环境变量/.env 读取）。

使用方式:
    from backend.core.config import settings
    print(settings.tiger_id)
"""
import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    """应用配置,字段值从环境变量读取（dotenv 已在 main.py 加载）。"""

    # Tiger Brokers OpenAPI
    tiger_id: str | None = field(default_factory=lambda: os.environ.get("TIGER_ID"))
    tiger_license: str = field(default_factory=lambda: os.environ.get("TIGER_LICENSE", "TBNZ"))
    tiger_private_key_path: str | None = field(default_factory=lambda: os.environ.get("TIGER_PRIVATE_KEY_PATH"))
    tiger_account: str | None = field(default_factory=lambda: os.environ.get("TIGER_ACCOUNT"))
    tiger_env: str = field(default_factory=lambda: os.environ.get("TIGER_ENV", "PROD"))
    tiger_language: str = field(default_factory=lambda: os.environ.get("TIGER_LANGUAGE", "zh_CN"))
    tiger_read_only_mode: bool = field(
        default_factory=lambda: os.environ.get("TIGER_READ_ONLY_MODE", "true").lower() == "true"
    )

    # Futu Brokers OpenAPI
    futu_account: str | None = field(default_factory=lambda: os.environ.get("FUTU_ACCOUNT"))
    futu_opend_host: str = field(default_factory=lambda: os.environ.get("FUTU_OPEND_HOST", "127.0.0.1"))
    futu_opend_port: int = field(default_factory=lambda: int(os.environ.get("FUTU_OPEND_PORT", "11111")))
    futu_read_only_mode: bool = field(
        default_factory=lambda: os.environ.get("FUTU_READ_ONLY_MODE", "true").lower() == "true"
    )


settings = Settings()

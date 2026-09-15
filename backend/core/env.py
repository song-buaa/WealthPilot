"""关键外部服务环境变量的安全加载辅助函数。"""
from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from dotenv import load_dotenv


TIGER_CREDENTIAL_ENV_KEYS = (
    "TIGER_ID",
    "TIGER_ACCOUNT",
    "TIGER_PRIVATE_KEY_PATH",
)


def normalized_optional_env(name: str) -> str | None:
    """读取可选环境变量；空字符串和纯空白统一视为未配置。"""
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def clear_blank_environment_values(names: Iterable[str]) -> None:
    """删除空环境变量，让 dotenv 中的有效值可以正常生效。"""
    for name in names:
        if name in os.environ and normalized_optional_env(name) is None:
            os.environ.pop(name, None)


def load_project_environment(dotenv_path: str | Path) -> None:
    """加载项目 .env，同时防止空 shell 环境变量遮蔽有效凭证。"""
    clear_blank_environment_values(TIGER_CREDENTIAL_ENV_KEYS)
    load_dotenv(dotenv_path)

"""
WealthPilot v3.4 凭证管理 — CredentialProvider 抽象层。

安全设计原则(v3.2 PRD §5.4):
- 凭证仅存本地 keyring(macOS Keychain / Linux Secret Service)
- 不写文件、不入数据库、不入日志
- 用完即丢(Adapter 不长期持有完整私钥)
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)

# 凭证 dict 必须包含的字段
REQUIRED_CREDENTIAL_FIELDS = {"tiger_id", "account_id", "private_key_pem"}


class CredentialNotFoundError(Exception):
    """凭证未绑定。"""


class CredentialProvider(ABC):
    """凭证提供者抽象基类。"""

    @abstractmethod
    def load(self, broker_key: str) -> Optional[dict]:
        """加载凭证。

        Args:
            broker_key: 如 'tiger.paper' / 'tiger.live'

        Returns:
            凭证 dict(含 tiger_id / account_id / private_key_pem)或 None(未绑定)
        """

    @abstractmethod
    def save(self, broker_key: str, credentials: dict) -> None:
        """保存凭证到安全存储。"""

    @abstractmethod
    def delete(self, broker_key: str) -> None:
        """删除凭证。"""

    def load_or_raise(self, broker_key: str) -> dict:
        """加载凭证,未绑定时抛 CredentialNotFoundError。"""
        creds = self.load(broker_key)
        if not creds:
            raise CredentialNotFoundError(
                f"未绑定凭证: {broker_key}。"
                f"请运行 `python backend/scripts/v3_4/bind_tiger_credentials.py` 绑定。"
            )
        missing = REQUIRED_CREDENTIAL_FIELDS - set(creds.keys())
        if missing:
            raise ValueError(f"凭证缺少必要字段: {missing}")
        return creds


class KeyringCredentialProvider(CredentialProvider):
    """基于 Python keyring 的凭证提供者。

    keyring service_name 命名规范:
      wealthpilot.broker.tiger.paper
      wealthpilot.broker.tiger.live
    """

    SERVICE_PREFIX = "wealthpilot.broker"
    USERNAME = "credentials"  # keyring 需要 username,统一用固定值

    def _service_name(self, broker_key: str) -> str:
        return f"{self.SERVICE_PREFIX}.{broker_key}"

    def load(self, broker_key: str) -> Optional[dict]:
        import keyring
        service = self._service_name(broker_key)
        data = keyring.get_password(service, self.USERNAME)
        if not data:
            return None
        return json.loads(data)

    def save(self, broker_key: str, credentials: dict) -> None:
        import keyring
        missing = REQUIRED_CREDENTIAL_FIELDS - set(credentials.keys())
        if missing:
            raise ValueError(f"凭证缺少必要字段: {missing}")
        service = self._service_name(broker_key)
        keyring.set_password(service, self.USERNAME, json.dumps(credentials))
        logger.info(
            "[KeyringCredentialProvider] 凭证已保存: %s (私钥指纹: %s...)",
            broker_key,
            credentials.get("private_key_pem", "")[:16],
        )

    def delete(self, broker_key: str) -> None:
        import keyring
        service = self._service_name(broker_key)
        try:
            keyring.delete_password(service, self.USERNAME)
            logger.info("[KeyringCredentialProvider] 凭证已删除: %s", broker_key)
        except keyring.errors.PasswordDeleteError:
            pass  # 不存在也不报错


class InMemoryCredentialProvider(CredentialProvider):
    """内存凭证提供者,用于测试(不污染真实 keychain)。"""

    def __init__(self):
        self._store: dict[str, dict] = {}

    def load(self, broker_key: str) -> Optional[dict]:
        return self._store.get(broker_key)

    def save(self, broker_key: str, credentials: dict) -> None:
        missing = REQUIRED_CREDENTIAL_FIELDS - set(credentials.keys())
        if missing:
            raise ValueError(f"凭证缺少必要字段: {missing}")
        self._store[broker_key] = credentials

    def delete(self, broker_key: str) -> None:
        self._store.pop(broker_key, None)

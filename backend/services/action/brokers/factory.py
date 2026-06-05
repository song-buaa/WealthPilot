"""
WealthPilot v3.4 Broker 工厂 — 统一创建 BrokerAdapter 实例。
"""
from __future__ import annotations

from typing import Optional

from backend.services.action.brokers.base import BrokerAdapter
from backend.services.action.brokers.credentials import (
    CredentialProvider,
    KeyringCredentialProvider,
)


class UnsupportedBrokerError(Exception):
    """不支持的券商。"""


def get_broker_adapter(
    broker_name: str = "tiger",
    mode: str = "paper",
    credential_provider: Optional[CredentialProvider] = None,
) -> BrokerAdapter:
    """工厂函数: 创建 BrokerAdapter 实例。

    Args:
        broker_name: "mock" / "tiger" / "ibkr"
        mode: "paper" / "live" / "mock"
        credential_provider: 凭证提供者(默认 KeyringCredentialProvider)

    Returns:
        BrokerAdapter 实例

    Raises:
        UnsupportedBrokerError: 不支持的 broker_name
    """
    if broker_name == "mock" or mode == "mock":
        from backend.services.action.brokers.mock import get_mock_adapter
        return get_mock_adapter()

    if broker_name == "tiger":
        from backend.services.action.brokers.tiger import TigerBrokerAdapter
        provider = credential_provider or KeyringCredentialProvider()
        broker_key = f"tiger.{mode}"
        return TigerBrokerAdapter(credential_provider=provider, broker_key=broker_key)

    if broker_name == "ibkr":
        from backend.services.action.brokers.ibkr import IBKRBrokerAdapter
        from backend.core.config import settings
        return IBKRBrokerAdapter(
            host=settings.ibkr_host,
            port=settings.ibkr_port,
            client_id=settings.ibkr_client_id,
            account_id=settings.ibkr_account or "",
        )

    raise UnsupportedBrokerError(f"不支持的券商: {broker_name}")

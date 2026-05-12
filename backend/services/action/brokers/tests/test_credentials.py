"""
CredentialProvider + Broker 工厂 单元测试 (M2)。

KeyringCredentialProvider 测试使用 keyring 的 in-memory backend,不污染真实 keychain。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.services.action.brokers.credentials import (
    CredentialNotFoundError,
    InMemoryCredentialProvider,
    KeyringCredentialProvider,
    REQUIRED_CREDENTIAL_FIELDS,
)
from backend.services.action.brokers.factory import (
    UnsupportedBrokerError,
    get_broker_adapter,
)
from backend.services.action.brokers.tiger import TIGER_PAPER_ACCOUNT

FAKE_PEM = "-----BEGIN RSA PRIVATE KEY-----\nfake_key\n-----END RSA PRIVATE KEY-----\n"
VALID_CREDS = {
    "tiger_id": "20159046",
    "account_id": TIGER_PAPER_ACCOUNT,
    "private_key_pem": FAKE_PEM,
}


# ============================================================
# InMemoryCredentialProvider
# ============================================================

class TestInMemoryProvider:
    def test_save_and_load(self):
        p = InMemoryCredentialProvider()
        p.save("tiger.paper", dict(VALID_CREDS))
        loaded = p.load("tiger.paper")
        assert loaded["tiger_id"] == "20159046"

    def test_load_nonexistent_returns_none(self):
        p = InMemoryCredentialProvider()
        assert p.load("tiger.paper") is None

    def test_delete(self):
        p = InMemoryCredentialProvider()
        p.save("tiger.paper", dict(VALID_CREDS))
        p.delete("tiger.paper")
        assert p.load("tiger.paper") is None

    def test_delete_nonexistent_no_error(self):
        p = InMemoryCredentialProvider()
        p.delete("tiger.paper")  # no error

    def test_save_missing_field_raises(self):
        p = InMemoryCredentialProvider()
        with pytest.raises(ValueError, match="缺少必要字段"):
            p.save("tiger.paper", {"tiger_id": "123"})  # missing account_id, private_key_pem

    def test_load_or_raise_success(self):
        p = InMemoryCredentialProvider()
        p.save("tiger.paper", dict(VALID_CREDS))
        creds = p.load_or_raise("tiger.paper")
        assert creds["account_id"] == TIGER_PAPER_ACCOUNT

    def test_load_or_raise_not_found(self):
        p = InMemoryCredentialProvider()
        with pytest.raises(CredentialNotFoundError, match="未绑定凭证"):
            p.load_or_raise("tiger.paper")


# ============================================================
# KeyringCredentialProvider (mock keyring)
# ============================================================

class TestKeyringProvider:
    def test_service_name_format(self):
        p = KeyringCredentialProvider()
        assert p._service_name("tiger.paper") == "wealthpilot.broker.tiger.paper"
        assert p._service_name("tiger.live") == "wealthpilot.broker.tiger.live"

    def test_save_and_load(self):
        import json
        import keyring as kr_module
        p = KeyringCredentialProvider()
        with patch.object(kr_module, "set_password") as mock_set, \
             patch.object(kr_module, "get_password", return_value=json.dumps(VALID_CREDS)):
            p.save("tiger.paper", dict(VALID_CREDS))
            mock_set.assert_called_once()

            loaded = p.load("tiger.paper")
            assert loaded["tiger_id"] == "20159046"

    def test_load_nonexistent(self):
        import keyring as kr_module
        p = KeyringCredentialProvider()
        with patch.object(kr_module, "get_password", return_value=None):
            assert p.load("tiger.paper") is None

    def test_delete(self):
        import keyring as kr_module
        p = KeyringCredentialProvider()
        with patch.object(kr_module, "delete_password") as mock_del:
            p.delete("tiger.paper")
            mock_del.assert_called_once()

    def test_save_missing_field_raises(self):
        p = KeyringCredentialProvider()
        with pytest.raises(ValueError, match="缺少必要字段"):
            p.save("tiger.paper", {"tiger_id": "123"})


# ============================================================
# Broker 工厂
# ============================================================

class TestBrokerFactory:
    def test_mock_broker(self):
        adapter = get_broker_adapter(broker_name="mock")
        assert adapter.broker_name == "mock"

    def test_mock_mode(self):
        adapter = get_broker_adapter(broker_name="tiger", mode="mock")
        assert adapter.broker_name == "mock"

    def test_tiger_paper(self):
        provider = InMemoryCredentialProvider()
        provider.save("tiger.paper", dict(VALID_CREDS))

        with patch("backend.services.action.brokers.tiger.TradeClient"), \
             patch("backend.services.action.brokers.tiger.TigerOpenClientConfig") as MockConfig:
            mock_config_inst = MagicMock()
            mock_config_inst.is_paper = True
            MockConfig.return_value = mock_config_inst

            adapter = get_broker_adapter(
                broker_name="tiger", mode="paper",
                credential_provider=provider,
            )
            assert adapter.broker_name == "tiger"

    def test_tiger_live_blocked_by_paper_only(self):
        provider = InMemoryCredentialProvider()
        provider.save("tiger.live", {
            "tiger_id": "20159046",
            "account_id": "4472659",
            "private_key_pem": FAKE_PEM,
        })

        with patch("backend.services.action.brokers.tiger.TradeClient"), \
             patch("backend.services.action.brokers.tiger.TigerOpenClientConfig"), \
             pytest.raises(AssertionError, match="实盘交易未开启"):
            get_broker_adapter(
                broker_name="tiger", mode="live",
                credential_provider=provider,
            )

    def test_tiger_no_credentials(self):
        provider = InMemoryCredentialProvider()  # empty
        with pytest.raises(CredentialNotFoundError):
            get_broker_adapter(
                broker_name="tiger", mode="paper",
                credential_provider=provider,
            )

    def test_unsupported_broker(self):
        with pytest.raises(UnsupportedBrokerError):
            get_broker_adapter(broker_name="futu")


# ============================================================
# 凭证安全: Adapter 不长期持有私钥
# ============================================================

class TestCredentialSecurity:
    def test_adapter_does_not_store_private_key(self):
        provider = InMemoryCredentialProvider()
        provider.save("tiger.paper", dict(VALID_CREDS))

        with patch("backend.services.action.brokers.tiger.TradeClient"), \
             patch("backend.services.action.brokers.tiger.TigerOpenClientConfig") as MockConfig:
            mock_config_inst = MagicMock()
            mock_config_inst.is_paper = True
            MockConfig.return_value = mock_config_inst

            from backend.services.action.brokers.tiger import TigerBrokerAdapter
            adapter = TigerBrokerAdapter(
                credential_provider=provider,
                broker_key="tiger.paper",
            )

            # Adapter 实例上不应有 _credentials / _private_key / private_key_pem 属性
            assert not hasattr(adapter, "_credentials")
            assert not hasattr(adapter, "_private_key")
            assert not hasattr(adapter, "private_key_pem")

    def test_required_fields_constant(self):
        assert REQUIRED_CREDENTIAL_FIELDS == {"tiger_id", "account_id", "private_key_pem"}

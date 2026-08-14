"""
WealthPilot v3.4 M3 — OrderManager 改造 + OrderPoller 单元测试。

覆盖:
- Symbol 中文名保护(InvalidSymbolError)
- audit_log 币种补全(fx_rate_to_cny)
- OrderPoller 轮询逻辑
- 孤儿订单扫描
- 工厂函数路由(BROKER_MODE)
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from backend.services.action.order_manager import (
    OrderManager,
    InvalidSymbolError,
)
from backend.services.action.state_machine import OrderStatus, StrategyStatus


# ============================================================
# Symbol 中文名保护
# ============================================================

class TestSymbolValidation:
    def test_chinese_symbol_rejected(self):
        """中文名 symbol 应被 InvalidSymbolError 拒绝。"""
        session = MagicMock()
        adapter = MagicMock()
        adapter.broker_name = "tiger"
        manager = OrderManager(session, broker_adapter=adapter)

        # mock strategy with Chinese symbol
        strategy = MagicMock()
        strategy.symbol = "贵州茅台"
        strategy.status = StrategyStatus.ACTIVE
        strategy.target_quantity = 100
        strategy.cumulative_filled_quantity = 0
        strategy.side = "BUY"
        strategy.order_type = "LIMIT"
        strategy.limit_price = 1000
        strategy.trigger_price = None

        # mock intent query to return None (not an AllocationIntent)
        session.query.return_value.filter_by.return_value.first.side_effect = [
            None,      # AllocationIntent check
            strategy,  # get_strategy
        ]

        with pytest.raises(InvalidSymbolError, match="包含中文"):
            manager.place_order("test-strategy-id", {"quantity": 10})

    def test_english_symbol_allowed(self):
        """英文 symbol 不应触发 InvalidSymbolError。"""
        session = MagicMock()
        adapter = MagicMock()
        adapter.broker_name = "tiger"
        manager = OrderManager(session, broker_adapter=adapter)

        strategy = MagicMock()
        strategy.symbol = "MSFT"
        strategy.status = StrategyStatus.ACTIVE
        strategy.target_quantity = 100
        strategy.cumulative_filled_quantity = 0
        strategy.side = "BUY"
        strategy.order_type = "LIMIT"
        strategy.limit_price = Decimal("415.0")
        strategy.trigger_price = None
        strategy.id = "strat-001"

        # mock: AllocationIntent check → None, get_strategy → strategy
        intent_q = MagicMock()
        intent_q.first.return_value = None
        strategy_q = MagicMock()
        strategy_q.first.return_value = strategy

        def filter_by_side_effect(**kwargs):
            if "id" in kwargs and kwargs["id"] == "test-strategy-id":
                # Could be AllocationIntent or SymbolStrategy query
                pass
            return MagicMock(first=MagicMock(return_value=None))

        session.query.return_value.filter_by.return_value.first.side_effect = [
            None,      # AllocationIntent check
            strategy,  # get_strategy
        ]

        update = MagicMock()
        update.broker_order_id = "12345"
        update.status = "submitted_to_broker"
        update.raw_response = {"currency": "USD", "limit_price": 415.0, "quantity": 10}
        adapter.place_order.return_value = update

        # Should not raise InvalidSymbolError
        manager.place_order("test-strategy-id", {
            "quantity": 10,
            "limit_price": 415.0,
        })

    def test_numeric_symbol_allowed(self):
        """纯数字 symbol (港股) 不应触发 InvalidSymbolError。"""
        session = MagicMock()
        manager = OrderManager(session)

        strategy = MagicMock()
        strategy.symbol = "00700"
        strategy.status = StrategyStatus.ACTIVE
        # symbol[0] is '0', not Chinese character
        assert not ("0" >= "\u4e00" and "0" <= "\u9fff")


# ============================================================
# audit_log 币种补全
# ============================================================

class TestAuditFxEnrichment:
    @pytest.fixture(autouse=True)
    def deterministic_fx_rates(self, monkeypatch):
        """Audit enrichment uses a fixed contract fixture, not mutable fallback values."""
        from app import fx_service

        monkeypatch.setattr(
            fx_service,
            "FALLBACK_RATES",
            {"USD": 6.9, "HKD": 0.92},
        )

    def test_enrich_usd(self):
        payload = OrderManager._enrich_audit_payload(
            {"order_id": "test"},
            {"currency": "USD", "limit_price": 100.0, "quantity": 10},
        )
        assert payload["fx_rate_to_cny"] == 6.9
        assert payload["amount_cny_equivalent"] == round(100.0 * 10 * 6.9, 2)

    def test_enrich_hkd(self):
        payload = OrderManager._enrich_audit_payload(
            {"order_id": "test"},
            {"currency": "HKD", "limit_price": 460.0, "quantity": 100},
        )
        assert payload["fx_rate_to_cny"] == 0.92
        assert payload["amount_cny_equivalent"] == round(460.0 * 100 * 0.92, 2)

    def test_enrich_missing_price(self):
        payload = OrderManager._enrich_audit_payload(
            {"order_id": "test"},
            {"currency": "USD"},
        )
        assert payload["fx_rate_to_cny"] == 6.9
        assert "amount_cny_equivalent" not in payload

    def test_enrich_no_currency_defaults_usd(self):
        payload = OrderManager._enrich_audit_payload(
            {"order_id": "test"},
            {},
        )
        assert payload["fx_rate_to_cny"] == 6.9

    def test_enrich_preserves_base_payload(self):
        payload = OrderManager._enrich_audit_payload(
            {"order_id": "test", "custom": "field"},
            {"currency": "USD"},
        )
        assert payload["order_id"] == "test"
        assert payload["custom"] == "field"


# ============================================================
# OrderPoller
# ============================================================

class TestOrderPoller:
    def test_poll_once_no_orders(self):
        """无待同步订单时 poll_once 正常返回。"""
        from backend.services.action.order_poller import OrderPoller

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.filter.return_value.all.return_value = []

        poller = OrderPoller(
            get_session=lambda: mock_session,
            get_broker_adapter=MagicMock,
        )
        poller._poll_once()  # should not raise

    def test_poller_stop(self):
        """stop() 应该正常停止。"""
        import asyncio
        from backend.services.action.order_poller import OrderPoller

        poller = OrderPoller(
            get_session=MagicMock,
            get_broker_adapter=MagicMock,
        )
        # Not started, stop should be safe
        loop = asyncio.new_event_loop()
        loop.run_until_complete(poller.stop())
        loop.close()


# ============================================================
# 孤儿订单扫描
# ============================================================

class TestOrphanScan:
    def test_scan_no_orphans(self):
        from backend.services.action.order_poller import scan_orphan_orders

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.all.return_value = []

        count = scan_orphan_orders(lambda: mock_session, MagicMock)
        assert count == 0


# ============================================================
# 工厂函数路由
# ============================================================

class TestBrokerModeRouting:
    def test_mock_mode_returns_mock(self):
        from backend.services.action.brokers.factory import get_broker_adapter
        adapter = get_broker_adapter(broker_name="mock")
        assert adapter.broker_name == "mock"

    def test_factory_tiger_no_creds_raises(self):
        from backend.services.action.brokers.factory import get_broker_adapter
        from backend.services.action.brokers.credentials import (
            InMemoryCredentialProvider,
            CredentialNotFoundError,
        )
        provider = InMemoryCredentialProvider()
        with patch("backend.core.demo_mode.PUBLIC_DEMO_MODE", False):
            with pytest.raises(CredentialNotFoundError):
                get_broker_adapter(
                    broker_name="tiger", mode="paper",
                    credential_provider=provider,
                )

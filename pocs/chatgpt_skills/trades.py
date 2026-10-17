"""MCP workflow glue; existing drafts, risk rules, order manager and broker own execution."""
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from app.models import Portfolio
from backend.services.action.models import ActionDraft, SymbolStrategy, OrderRecord
from backend.services.action.order_manager import OrderManager
from backend.services.action.risk_engine import check_order_risk, validate_confirmation, CONFIRMATION_TEXT
from backend.utils.symbol import parse_symbol, normalize_symbol, _CURRENCY_TO_MARKET
from pocs.poc02_wealth_mcp.reader import readonly_snapshot, service_sessions
from pocs.chatgpt_skills.contracts import ExplicitOrder


class TradeError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


class TradeWorkflow:
    def __init__(self, path, portfolio_id, adapter, *, synthetic, discipline_provider=None):
        self.path, self.portfolio_id, self.adapter = path, portfolio_id, adapter
        self.synthetic, self.discipline_provider = synthetic, discipline_provider
        self.owner = f"mcp-portfolio:{portfolio_id}"
        self.engine = create_engine("sqlite:///"+str(path), poolclass=NullPool,
                                    connect_args={"timeout": 5})

    def close(self):
        self.adapter.shutdown()
        self.engine.dispose()

    @contextmanager
    def session(self, write=False):
        if not write:
            with readonly_snapshot(self.path) as connection, Session(bind=connection, autoflush=False,
                    join_transaction_mode="rollback_only") as session:
                yield session
            return
        with Session(self.engine) as session:
            session.info["portfolio_id"] = self.portfolio_id
            # SQLite serializes confirmation/replay reservation across processes.
            session.execute(text("BEGIN IMMEDIATE"))
            try:
                if session.get(Portfolio, self.portfolio_id) is None:
                    raise TradeError("PORTFOLIO_NOT_FOUND")
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def owned(self, session, draft_id):
        draft = session.get(ActionDraft, draft_id)
        if draft is None or draft.user_id != self.owner:
            raise TradeError("DRAFT_NOT_FOUND")
        return draft

    def risk(self, session, order):
        from backend.api.action import _get_portfolio_total_value, _get_position_value
        from app.fx_service import FALLBACK_RATES
        # Import before temporary session binding; never call the broad data_loader.load
        # (research/LLM/market fallbacks are irrelevant to an explicit order).
        from decision_engine.data_loader import LoadedData, InvestmentRules, PositionInfo
        from decision_engine.rule_engine import check
        from decision_engine.types import IntentResult
        from app.discipline.config import get_rules
        from app.utils.position_aggregator import aggregate_investment_positions, find_target
        ticker, _ = parse_symbol(order["symbol"])
        with service_sessions(session.connection()):
            total = _get_portfolio_total_value(session, self.portfolio_id)
            value = _get_position_value(session, ticker, self.portfolio_id)
            if self.discipline_provider:
                discipline = self.discipline_provider(order["symbol"], order["side"], self.portfolio_id)
            else:
                positions, total_value = aggregate_investment_positions(pid=self.portfolio_id)
                target, _ = find_target(positions, ticker)
                config = get_rules()
                rules = InvestmentRules(config["single_asset_limits"]["max_position_pct"],
                    config["asset_allocation_ranges"]["equity_max"],
                    config["liquidity_limits"]["min_cash_pct"],
                    config["leverage_limits"]["leverage_ratio_warning_max"])
                data = LoadedData(profile=None, positions=[PositionInfo.from_aggregated(p) for p in positions],
                    target_position=PositionInfo.from_aggregated(target) if target else None,
                    rules=rules, total_assets=total_value)
                intent = IntentResult(ticker, "买入判断" if order["side"] == "BUY" else "卖出判断", "未知", None, 1.0)
                discipline = asdict(check(data, intent))
        # Same fixed fallback rates as the legacy single-order API; no network/quote inference.
        fx = Decimal("1") if order["currency"] == "CNY" else Decimal(str(FALLBACK_RATES[order["currency"]]))
        result = check_order_risk(symbol=order["symbol"], side=order["side"], quantity=order["quantity"],
            limit_price=Decimal(order["limit_price"]), portfolio_total_value=total,
            current_position_value=value, discipline_result=discipline, fx_rate_to_cny=fx)
        return result, dict(passed=result.passed, requires_confirmation=result.requires_confirmation,
            warnings=[dict(rule=w.rule, severity=w.severity, message=w.message, detail=w.detail) for w in result.warnings],
            confirmation_text_required=CONFIRMATION_TEXT if result.requires_confirmation else None,
            discipline_available=discipline is not None, fx_rate_to_cny=str(fx),
            fx_basis="legacy FALLBACK_RATES; not a live FX quote")

    def draft_order(self, draft):
        payload = json.loads(draft.payload)
        order = payload["mcp"]["order"]
        # A legacy Web edit must not silently execute different parameters than
        # the MCP preview. A changed draft must be recreated/reviewed explicitly.
        if payload.get("symbol_strategies") != [order] or payload.get("allocation_intents"):
            raise TradeError("DRAFT_CHANGED_REVIEW_REQUIRED")
        return order

    def result(self, session, draft, risk_override=None):
        order = self.draft_order(draft)
        risk = risk_override
        if risk is None:
            _, risk = self.risk(session, order)
        preview = dict(order, broker=self.adapter.broker_name, account_scope="configured operator account",
                       time_in_force="GTC", price_basis="explicit user limit; no quote or price inference",
                       estimated_notional=str(Decimal(order["limit_price"])*order["quantity"]),
                       fees="not estimated by legacy single-order workflow",
                       execution_path="ActionDraft → SymbolStrategy → OrderManager.place_order")
        strategies = session.query(SymbolStrategy).filter_by(source_draft_id=draft.id).all()
        ids = [s.id for s in strategies]
        orders = session.query(OrderRecord).filter(OrderRecord.strategy_id.in_(ids)).all() if ids else []
        records = [dict(order_id=o.id, strategy_id=o.strategy_id, symbol=o.symbol, side=o.side,
                        quantity=o.quantity, filled_quantity=o.filled_quantity, status=o.status,
                        broker=o.broker_name, limit_price=str(o.limit_price) if o.limit_price is not None else None,
                        avg_filled_price=str(o.avg_filled_price) if o.avg_filled_price is not None else None,
                        updated_at=(o.filled_at or o.submitted_at or o.created_at).isoformat()) for o in orders]
        digest = hashlib.sha256(canonical({"draft_id": draft.id, "preview": preview, "risk": risk}).encode()).hexdigest()
        return dict(schema_version="wealthpilot.trade.v1", synthetic=self.synthetic, broker=self.adapter.broker_name,
                    draft_id=draft.id, draft_status=draft.status, preview_hash=digest,
                    preview=preview, risk=risk, orders=records, queried_at=datetime.now(timezone.utc).isoformat())

    def create(self, instruction, request_id, raw_order):
        order = ExplicitOrder.model_validate(raw_order)
        ticker, market = parse_symbol(order.symbol)
        order.symbol = normalize_symbol(ticker, market)
        currency_market = _CURRENCY_TO_MARKET.get(order.currency)
        if not ticker or currency_market is None or normalize_symbol(ticker, currency_market) != order.symbol:
            raise TradeError("ORDER_CURRENCY_MARKET_MISMATCH")
        data = order.model_dump(mode="json")
        with self.session(write=True) as session:
            existing = session.query(ActionDraft).filter_by(user_id=self.owner, conversation_id=request_id).first()
            if existing is not None:
                if json.loads(existing.payload)["mcp"]["order"] != data:
                    raise TradeError("REQUEST_ID_CONFLICT")
                return self.result(session, existing)
            payload = {"symbol_strategies": [data], "allocation_intents": [], "missing_fields": [], "risk_notes": [],
                       "mcp": {"order": data, "request_id": request_id, "portfolio_id": self.portfolio_id}}
            draft = OrderManager(session, self.adapter).create_draft(request_id, payload, instruction)
            draft.user_id = self.owner
            session.flush()
            return self.result(session, draft)

    def preview(self, draft_id):
        with self.session() as session:
            return self.result(session, self.owned(session, draft_id))

    def confirm(self, draft_id, preview_hash, confirmed, risk_confirmation=""):
        if confirmed is not True:
            raise TradeError("EXPLICIT_CONFIRMATION_REQUIRED")
        with self.session(write=True) as session:
            draft = self.owned(session, draft_id)
            current = self.result(session, draft)
            payload = json.loads(draft.payload)
            if current["orders"] and payload["mcp"].get("confirmed_preview_hash") == preview_hash:
                return current  # Existing attempt remains non-retryable even if holdings/risk changed.
            if current["preview_hash"] != preview_hash:
                raise TradeError("PREVIEW_CHANGED_REVIEW_REQUIRED")
            if draft.status != "draft":
                raise TradeError("DRAFT_NOT_PENDING")
            order = self.draft_order(draft)
            risk_result, _ = self.risk(session, order)
            if not validate_confirmation(risk_result, risk_confirmation):
                raise TradeError("RISK_CONFIRMATION_REQUIRED")
            payload["mcp"]["confirmed_preview_hash"] = preview_hash
            draft.payload = canonical(payload)
            manager = OrderManager(session, self.adapter)
            strategies = manager.confirm_draft(draft.id)
            strategy = strategies[0]
            # Persist the authorized draft/strategy before a broker can receive an order.
            # After a crash in this window the confirmed draft is never auto-resubmitted.
            manager._audit("mcp_trade_confirmation", {"draft_id": draft.id, "preview_hash": preview_hash,
                            "confirmed": True, "risk_acknowledged": bool(risk_confirmation)})
            session.commit()
            draft = self.owned(session, draft_id)
            strategy = session.query(SymbolStrategy).populate_existing().filter_by(id=strategy.id).one()
            if (strategy.symbol != order["symbol"] or strategy.side != order["side"]
                    or strategy.target_quantity != order["quantity"] or strategy.order_type != order["order_type"]
                    or Decimal(str(strategy.limit_price)) != Decimal(order["limit_price"])
                    or (Decimal(str(strategy.trigger_price)) if strategy.trigger_price is not None else None)
                       != (Decimal(order["trigger_price"]) if order.get("trigger_price") is not None else None)):
                raise TradeError("DRAFT_CHANGED_REVIEW_REQUIRED")
            if session.query(OrderRecord).filter_by(strategy_id=strategy.id).first() is None:
                if not self.synthetic or self.adapter.broker_name != "mock":
                    raise TradeError("REAL_MCP_WRITES_FORBIDDEN")
                params = {"quantity": order["quantity"], "order_type": order["order_type"],
                    "limit_price": order["limit_price"], "stop_price": order.get("trigger_price"), "time_in_force": "GTC"}
                review_id = manager.create_order_review(strategy.id, params, risk_snapshot=current["risk"])
                session.commit()
                manager.place_order(strategy.id, {**params, "review_id": review_id,
                                                  "confirmation_text": risk_confirmation})
            return self.result(session, draft, risk_override=current["risk"])

    def status(self, draft_id):
        with self.session(write=True) as session:
            draft = self.owned(session, draft_id)
            manager = OrderManager(session, self.adapter)
            ids = [s.id for s in session.query(SymbolStrategy).filter_by(source_draft_id=draft.id)]
            session.commit()
            for order in session.query(OrderRecord).filter(OrderRecord.strategy_id.in_(ids)).all():
                # Original method persists broker-authoritative state and cumulative fills.
                try:
                    manager.sync_order_status(order.id)
                    session.commit()
                except ValueError:
                    raise TradeError("BROKER_ORDER_STATE_UNAVAILABLE") from None
            return self.result(session, draft)

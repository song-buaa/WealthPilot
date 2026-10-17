"""B0 owner resolution. No account table, implicit Portfolio 1 or prefix guessing."""
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class ExecutionOwner:
    portfolio_id: int
    broker: str
    account_id: str

    @property
    def holdings_broker(self):
        return "snowball" if self.broker == "ibkr" else self.broker


def portfolio_scope(session):
    pid = session.info.get("portfolio_id")
    if os.environ.get("WEALTHPILOT_SQLITE_CONTRACT"):
        from app.database import DB_PATH
        from app.shared_sqlite import verify
        verified_pid = verify(DB_PATH, os.environ["WEALTHPILOT_SQLITE_CONTRACT"])["portfolio_id"]
        if pid is not None and pid != verified_pid:
            raise ValueError("CONTRACT_PORTFOLIO_MISMATCH")
        pid = verified_pid
    if pid is None:
        from app import state
        pid = state.portfolio_id
    if type(pid) is not int or pid <= 0:
        raise ValueError("EXPLICIT_PORTFOLIO_REQUIRED")
    from app.models import Portfolio
    if session.get(Portfolio, pid) is None:
        raise ValueError("PORTFOLIO_NOT_FOUND")
    return pid


def execution_owner(session, adapter):
    pid = portfolio_scope(session)
    if adapter is None:
        raise ValueError("BROKER_ADAPTER_REQUIRED")
    broker = adapter.broker_name
    account = getattr(adapter, "_account_id", None)
    if not account or str(account).lower() in {"pending", "unknown", "__unconfigured__"}:
        raise ValueError("EXPLICIT_BROKER_ACCOUNT_REQUIRED")
    from app.models import Position
    from backend.services.action.models import OrderRecord
    holdings = "snowball" if broker == "ibkr" else broker
    conflict = session.query(Position.id).filter(Position.broker == holdings,
        Position.broker_account_id == str(account), Position.portfolio_id != pid).first()
    order_conflict = session.query(OrderRecord.id).filter(OrderRecord.broker_name == broker,
        OrderRecord.broker_account_id == str(account), OrderRecord.portfolio_id.isnot(None),
        OrderRecord.portfolio_id != pid).first()
    if conflict or order_conflict:
        raise ValueError("ACCOUNT_PORTFOLIO_AMBIGUOUS_RECONCILIATION_REQUIRED")
    return ExecutionOwner(pid, broker, str(account))

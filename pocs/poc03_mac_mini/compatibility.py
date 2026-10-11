"""Offline import/read-only smoke in the proposed minimal business environment."""
import importlib.abc
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import socket
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pocs/poc02_wealth_mcp"))


def main():
    forbidden = ("backend.main", "backend.agents", "backend.skills", "backend.services.action",
                 "backend.services.broker_sync", "backend.services.execution_plan", "decision_engine")
    class Guard(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            if any(fullname == p or fullname.startswith(p + ".") for p in forbidden):
                raise ImportError("FORBIDDEN_RUNTIME_MODULE")
    sys.meta_path.insert(0, Guard())
    def blocked(*args, **kwargs):
        raise RuntimeError("NETWORK_DISABLED_IN_COMPATIBILITY_TEST")
    socket.socket.connect = blocked
    socket.create_connection = blocked
    for key in list(os.environ):
        if key.startswith(("TIGER_", "IBKR_", "FUTU_", "OPENAI_")):
            os.environ.pop(key)
    os.environ.update(PYTHON_DOTENV_DISABLED="1", PUBLIC_DEMO_MODE="false")
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from app.database import Base
    from app.models import Portfolio, Position, WealthItem
    from reader import read_overview, readonly_snapshot
    from tigeropen.trade.trade_client import TradeClient
    import hashlib
    with tempfile.TemporaryDirectory(prefix="poc03-compat-") as temporary:
        db = Path(temporary) / "fixture.db"
        engine = create_engine("sqlite:///" + str(db))
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(Portfolio(id=1, name="synthetic"))
            session.add(Position(portfolio_id=1, name="fixture", platform="fixture", segment="投资", asset_class="货币", market_value_cny=1000))
            session.add(WealthItem(portfolio_id=1, kind="LIABILITY", name="fixture", item_type="credit_card", category="credit_card", current_value=100, included_in_net_worth=True))
            session.commit()
        engine.dispose()
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        overview = read_overview(db, 1)
        assert overview["total_assets_minor"] == 100000
        assert overview["total_liabilities_minor"] == 10000
        assert overview["net_worth_minor"] == 90000
        denied = False
        with readonly_snapshot(db) as connection:
            try: connection.execute(text("UPDATE positions SET market_value_cny=1"))
            except Exception: denied = True
        assert denied and hashlib.sha256(db.read_bytes()).hexdigest() == before
    print(json.dumps({"result": "PASS", "python": platform.python_version(), "architecture": platform.machine(),
        "native_arm_validation": "PASS" if platform.machine() == "arm64" else "BLOCKED_NOT_ARM_RUNTIME",
        "fixture_amounts_correct": True, "database_writes_denied": True,
        "tiger_sdk_read_method_imported": hasattr(TradeClient, "get_prime_assets"),
        "forbidden_modules_not_loaded": not any(any(k == p or k.startswith(p + ".") for p in forbidden) for k in sys.modules),
        "real_database_accessed": False, "network_disabled": True,
        "versions": {p: importlib.metadata.version(p) for p in ("SQLAlchemy", "openai", "tigeropen", "requests", "cryptography")}}))


if __name__ == "__main__":
    main()

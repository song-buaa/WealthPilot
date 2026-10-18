"""Playwright-only synthetic service. Never imports backend.main or real config."""
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pocs/poc03_mac_mini"))
safe_env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TMPDIR") if k in os.environ}
os.environ.clear(); os.environ.update(safe_env)
os.environ["GUOJIN_ACCOUNT"] = "synthetic"
os.environ.update(PYTHON_DOTENV_DISABLED="1", PUBLIC_DEMO_MODE="false", BROKER_MODE="mock",
                  IBKR_READ_ONLY_MODE="true", ENABLE_IBKR_LIVE_TRADING="false", ENABLE_TIGER_LIVE_TRADING="false")
original_connect = socket.socket.connect
def connect(sock, address):
    if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("FIXTURE_EXTERNAL_NETWORK_FORBIDDEN")
    return original_connect(sock, address)
socket.socket.connect = connect

import common
import deploy
import runner
import shared_database
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app import database
from app.models import Base, Portfolio, Position, WealthItem
from backend.restricted_web import create_app
from backend.services import portfolio_service
from services.broker_sync.guojin.sync_service import GuojinSyncService
from services.broker_sync.schema import Position as SnapshotPosition
import uvicorn


def factory(broker):
    if "--source-fails" in sys.argv:
        raise RuntimeError("SYNTHETIC_SOURCE_UNAVAILABLE")
    service = GuojinSyncService.__new__(GuojinSyncService)
    service.adapter = SimpleNamespace(account_id="synthetic")
    position = SnapshotPosition(broker="guojin", account_id="synthetic", symbol="000001:CN", raw_symbol="000001",
        name="合成同步资产", asset_class="equity", vehicle_type="COMMON_STOCK", economic_asset_class="EQUITY",
        classification_source="USER_EXPLICIT", classification_verification_status="EXPLICIT",
        market="CN", quantity=Decimal(1), avg_cost=Decimal(2500), cost_method="weighted_average",
        cost_basis=Decimal(2500), current_price=Decimal(2500), market_value=Decimal(2500), currency="CNY",
        unrealized_pnl=Decimal(0), unrealized_pnl_pct=Decimal(0), snapshot_time=datetime.now(timezone.utc),sync_source="api")
    service.fetch_positions = lambda: ([position], {})
    return service


with tempfile.TemporaryDirectory(prefix="restricted-web-browser-") as temporary:
    root = deploy.layout(Path(temporary).resolve() / "fixture")
    db = root / "data/wealthpilot.db"
    os.environ["WEALTHPILOT_DB_PATH"] = str(db)
    database.DB_PATH = str(db)
    engine = create_engine("sqlite:///" + str(db))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Portfolio(id=7,name="synthetic"))
        session.add(Position(portfolio_id=7,name="合成原有资产",platform="fixture",segment="投资",asset_class="货币",market_value_cny=1000))
        session.add(WealthItem(portfolio_id=7,kind="LIABILITY",name="合成负债",item_type="credit_card",category="credit_card",current_value=100,included_in_net_worth=True))
        session.commit()
    engine.dispose(); db.chmod(0o400)
    common.write_json(root / "config/runtime.json", {"revision":"a"*40,"mode":"real"})
    common.write_json(root / "config/wealth.json", {"database_path":str(db),"portfolio_id":7})
    common.write_json(root / "data/migration-reference.json", {"database_sha256":common.digest(db),"portfolio_id":7,"cash_reference":{"configured":False}})
    common.write_json(root / "config/access-evidence.json", {k:True for k in runner.PROOF_FIELDS})
    shared_database.prepare_shared(root, services_stopped=True, apply=True)
    contract = root / "config/database-contract.json"
    os.environ["WEALTHPILOT_SQLITE_CONTRACT"] = str(contract)
    portfolio_service._get_tiger_account_cash = lambda: (0.0, [])
    config = {"database_path":str(db),"database_contract_path":str(contract),"portfolio_id":7,
              "enabled_brokers":["guojin"],"lock_path":str(root/"run/web.lock"),"revision":"a"*40}
    app = create_app(config, ROOT / "frontend/dist", factory=factory)
    uvicorn.run(app,host="127.0.0.1",port=int(sys.argv[1]),access_log=False,log_level="error")

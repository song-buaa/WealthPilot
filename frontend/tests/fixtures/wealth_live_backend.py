"""Isolated browser fixture: real portfolio/wealth services and temporary SQLite."""
import os
from pathlib import Path
import sys
import tempfile

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
temporary = tempfile.TemporaryDirectory(prefix="wealth-browser-")
os.environ["WEALTHPILOT_DB_PATH"] = str(Path(temporary.name) / "fixture.db")
os.environ["PUBLIC_DEMO_MODE"] = "true"
os.environ["BROKER_MODE"] = "mock"

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn
from app.database import Base, get_engine, get_session
from app.models import Portfolio, Position
from backend.services import portfolio_service, wealth_service

Base.metadata.create_all(get_engine())
portfolio_service._get_tiger_account_cash = lambda: (0, [])
with get_session() as session:
    session.add(Portfolio(id=1, name="Isolated portfolio"))
    session.add(Position(portfolio_id=1, name="Fixture investment", platform="fixture",
                         segment="投资", asset_class="权益", market_value_cny=1000))
    session.commit()
for item_type, value, linked, kind in [
    ("personal_pension", 100, True, "asset"),
    ("enterprise_annuity", 50, True, "asset"),
    ("housing_fund", 200, False, "asset"),
    ("credit_card", 1500, False, "liability"),
]:
    wealth_service.create_item(1, dict(kind=kind, name=item_type, item_type=item_type,
                                     current_value=value, already_investment_accounted=linked))

app = FastAPI()
sync = {"time": "old", "status": "success", "polls": 0}

@app.get("/api/demo/status")
def demo_status():
    return {"public_demo_mode": False}

@app.get("/api/portfolio/summary")
def portfolio_summary():
    return portfolio_service.get_summary(1)

@app.get("/api/portfolio/positions")
def positions():
    return portfolio_service.get_positions(1)

@app.get("/api/wealth/summary")
def wealth_summary():
    return wealth_service.get_summary(1)

@app.get("/api/wealth/items")
def wealth_items(kind: str | None = None):
    items = wealth_service.list_items(1, kind)
    return {"items": items, "total": len(items)}

@app.post("/api/broker-sync/trigger")
def trigger():
    sync.update(time="new", status="running", polls=0)
    return {"brokers_triggered": ["tiger"]}

@app.get("/api/broker-sync/status")
def sync_status():
    if sync["status"] == "running":
        sync["polls"] += 1
        if sync["polls"] >= 2:
            # A committed Portfolio change, never a mocked wealth response.
            with get_session() as session:
                session.query(Position).one().market_value_cny = 1400
                session.commit()
            sync["status"] = "success"
    return {"brokers": [dict(broker="tiger", platform="fixture", last_sync_time=sync["time"],
                             last_sync_status=sync["status"])]}

@app.get("/api/{path:path}")
def unused_endpoint(path: str):
    return [] if "targets" in path else {"items": [], "total": 0}

app.mount("/", StaticFiles(directory=root / "frontend" / "dist", html=True), name="frontend")
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]))

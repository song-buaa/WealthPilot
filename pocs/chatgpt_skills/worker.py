"""Isolated business subprocess. JSON lines only; no raw errors or financial logs."""
import argparse
import contextlib
import io
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.append(str(Path(__file__).resolve().parents[2] / "backend"))
MOCK_APPLICATION_ID = 0x57504D4B
SAFE_ERRORS = {"PORTFOLIO_NOT_FOUND", "ITEM_NOT_FOUND", "ACCOUNT_NOT_FOUND", "DRAFT_NOT_FOUND",
               "REQUEST_ID_CONFLICT", "EXPLICIT_CONFIRMATION_REQUIRED", "PREVIEW_CHANGED_REVIEW_REQUIRED",
               "RISK_CONFIRMATION_REQUIRED", "DRAFT_NOT_PENDING", "DRAFT_CHANGED_REVIEW_REQUIRED", "BROKER_ORDER_STATE_UNAVAILABLE", "ORDER_CURRENCY_MARKET_MISMATCH", "MOCK_TRADE_FIXTURE_NOT_ENABLED"}


def init_mock(path, portfolio_id):
    # Explicit fixture creation only; never init/migrate a preexisting database.
    path = Path(path).resolve()
    if path.exists():
        raise ValueError("NEW_FIXTURE_PATH_REQUIRED")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ.update(WEALTHPILOT_DB_PATH=str(path), PUBLIC_DEMO_MODE="true", BROKER_MODE="mock")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.database import Base
    from app.models import Portfolio
    import backend.services.action.models  # noqa: F401
    engine = create_engine("sqlite:///"+str(path))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Portfolio(id=portfolio_id, name="Synthetic MCP trade fixture"))
        session.commit()
    engine.dispose()
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA application_id={MOCK_APPLICATION_ID}")
        db.execute("PRAGMA journal_mode=WAL")
    path.chmod(0o600)


def checked_mock(path):
    path = Path(path).resolve(strict=True)
    with sqlite3.connect(path.as_uri()+"?mode=ro", uri=True) as db:
        if db.execute("PRAGMA application_id").fetchone()[0] != MOCK_APPLICATION_ID:
            raise ValueError("EXPLICIT_MOCK_FIXTURE_REQUIRED")
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--init-mock")
    parser.add_argument("--mock-database")
    parser.add_argument("--portfolio-id", type=int, default=1)
    parser.add_argument("--real-config")
    parser.add_argument("--analysis-env-file")
    args = parser.parse_args()
    os.umask(0o077)
    logging.getLogger().handlers = [logging.NullHandler()]
    if args.init_mock:
        init_mock(args.init_mock, args.portfolio_id)
        print(json.dumps({"ok": True, "synthetic": True})); return
    workflow = None
    try:
        if args.mock_database:
            path = checked_mock(args.mock_database)
            os.environ.update(WEALTHPILOT_DB_PATH=str(path), PUBLIC_DEMO_MODE="true", DEMO_ACCESS_PASSWORD="synthetic-only", BROKER_MODE="mock",
                              DEMO_ALLOW_MARKET_DATA="false", AV_DEV_MOCK="1")
            from backend.services.action.brokers.mock import MockBrokerAdapter
            from pocs.chatgpt_skills.trades import TradeWorkflow
            workflow = TradeWorkflow(path, args.portfolio_id,
                MockBrokerAdapter(fill_delay_seconds=.1, rejection_probability=0,
                                  partial_fill_probability=0, network_failure_probability=0), synthetic=True)
        config = None
        if args.real_config:
            config = json.loads(Path(args.real_config).read_text())
            if args.mock_database and Path(config["database_path"]).resolve() == path:
                raise ValueError("READ_AND_MOCK_TRADE_DATABASE_MUST_DIFFER")
            from pocs.poc02_wealth_mcp.worker import configure_environment
            configure_environment(config)
        for line in sys.stdin:
            try:
                request = json.loads(line)
                operation, a = request["operation"], request["arguments"]
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    if operation in ("analyze_position", "review_portfolio", "analyze_performance"):
                        import asyncio
                        from pocs.chatgpt_skills.analysis import analyze
                        if args.analysis_env_file:
                            values = json.loads(Path(args.analysis_env_file).read_text())
                            allowed = {"WEALTHPILOT_OPENAI_API_KEY", "PERPLEXITY_API_KEY", "ALPHA_VANTAGE_API_KEY", "ALPHAVANTAGE_API_KEY", "AV_API_KEY", "AV_API_KEY_1", "AV_API_KEY_2", "AV_API_KEY_3", "AV_API_KEY_4", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"}
                            os.environ.update({k:v for k,v in values.items() if k in allowed and isinstance(v,str)})
                        if config:
                            os.environ["WEALTHPILOT_DB_PATH"] = config["database_path"]
                        result = asyncio.run(analyze(config["database_path"] if config else path,
                            config["portfolio_id"] if config else args.portfolio_id, operation, a,
                            contract=config.get("database_contract_path") if config else None, synthetic=config is None, worker_process=True))
                    elif operation == "get_wealth_overview" and workflow:
                        from pocs.poc02_wealth_mcp.reader import read_overview
                        result = read_overview(path, args.portfolio_id)
                        result["synthetic"] = True
                    elif operation.startswith("get_") and operation in ("get_manual_wealth_items", "get_investment_details"):
                        from pocs.chatgpt_skills.reader import read_details
                        result = read_details(config["database_path"] if config else path,
                            config["portfolio_id"] if config else args.portfolio_id, operation, a,
                            database_contract=config.get("database_contract_path") if config else None,
                            synthetic=config is None, cash_configured=bool(config and config.get("tiger_cash")))
                    elif workflow is None:
                        raise ValueError("MOCK_TRADE_FIXTURE_NOT_ENABLED")
                    elif operation == "create_trade_draft":
                        result = workflow.create(a["instruction"], a["request_id"], a["order"])
                    elif operation == "get_trade_preview":
                        result = workflow.preview(a["draft_id"])
                    elif operation == "confirm_trade":
                        result = workflow.confirm(a["draft_id"], a["preview_hash"], a["confirmed"], a.get("risk_confirmation", ""))
                    elif operation == "get_trade_order_status":
                        result = workflow.status(a["draft_id"])
                    else:
                        raise ValueError("UNKNOWN_OPERATION")
                response = {"ok": True, "result": result}
            except Exception as error:
                code = str(error) if str(error) in SAFE_ERRORS else "BUSINESS_OPERATION_UNAVAILABLE"
                response = {"ok": False, "error": code}
            print(json.dumps(response, ensure_ascii=False, default=str), flush=True)
    finally:
        if workflow:
            workflow.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(json.dumps({"ok": False, "error": "WORKER_START_REFUSED"}), flush=True)
        raise SystemExit(1)

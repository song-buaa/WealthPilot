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
from app.asset_views import ERRORS as ASSET_ERRORS
SAFE_ERRORS |= ASSET_ERRORS
from backend.services.portfolio_history.sources import ERRORS as HISTORY_ERRORS
SAFE_ERRORS |= HISTORY_ERRORS

# Transport metadata only; importing it must not load a model client in the
# independent MCP environment. Values come from local operator configuration.
MODEL_ENV_KEYS = {"WEALTHPILOT_LLM_PROFILE", "WEALTHPILOT_LLM_MAIN_MODEL",
                  "WEALTHPILOT_LLM_LIGHT_MODEL", "WEALTHPILOT_LLM_MAIN_EFFORT",
                  "WEALTHPILOT_LLM_LIGHT_EFFORT"}


def load_analysis_environment(path, *, model_policy_only=False):
    values = json.loads(Path(path).read_text())
    # Model constants are resolved at first business import. A running worker
    # must not mix cached model IDs with a newly reloaded profile or effort.
    allowed = MODEL_ENV_KEYS if model_policy_only else {
        "WEALTHPILOT_OPENAI_API_KEY", "PERPLEXITY_API_KEY", "ALPHA_VANTAGE_API_KEY",
        "ALPHAVANTAGE_API_KEY", "AV_API_KEY", "AV_API_KEY_1", "AV_API_KEY_2",
        "AV_API_KEY_3", "AV_API_KEY_4", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
        "WEALTHPILOT_STAGE_TARGETS_PATH"}
    os.environ.update({key:value for key,value in values.items()
                       if key in allowed and isinstance(value,str)})


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
    import backend.services.portfolio_history.models  # noqa: F401 - explicit synthetic fixture only
    import services.broker_sync.models  # noqa: F401 - same synthetic fixture
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
    parser.add_argument("--frozen-database")
    parser.add_argument("--asset-view-source")
    parser.add_argument("--source-contract")
    parser.add_argument("--synthetic-input", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    logging.getLogger().handlers = [logging.NullHandler()]
    if args.init_mock:
        init_mock(args.init_mock, args.portfolio_id)
        print(json.dumps({"ok": True, "synthetic": True})); return
    workflow = None
    try:
        # app.config can be imported by an earlier read operation. Establish
        # model policy before that first import, not only at analysis time.
        if args.analysis_env_file:
            load_analysis_environment(args.analysis_env_file, model_policy_only=True)
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
        if args.frozen_database or args.asset_view_source:
            if config or workflow:
                raise ValueError("ASSET_VIEW_INVALID")
            config = dict(database_path=str(Path(args.frozen_database or args.asset_view_source).resolve(strict=True)),
                          portfolio_id=args.portfolio_id, database_contract_path=args.source_contract)
        for line in sys.stdin:
            try:
                request = json.loads(line)
                operation, a = request["operation"], request["arguments"]
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    if operation == "capture_asset_view" and args.frozen_database:
                        from pocs.chatgpt_skills.asset_view import capture
                        import socket
                        previous_connect = socket.socket.connect
                        def no_network(*_):
                            raise RuntimeError("FROZEN_CAPTURE_NETWORK_FORBIDDEN")
                        socket.socket.connect = no_network
                        try:
                            result = capture(config["database_path"], args.portfolio_id, a["internal"], synthetic=args.synthetic_input)
                        finally:
                            socket.socket.connect = previous_connect
                    elif a.get("snapshot_id") and operation in ("get_wealth_overview", "get_manual_wealth_items", "get_investment_details"):
                        from app.asset_views import load_view, frozen_result
                        view = load_view(config["database_path"] if config else path,
                            config["portfolio_id"] if config else args.portfolio_id, a["snapshot_id"],
                            config.get("database_contract_path") if config else None)
                        result = frozen_result(view, operation, a)
                    elif operation in ("analyze_position", "review_portfolio", "analyze_performance"):
                        import asyncio
                        from pocs.chatgpt_skills.analysis import analyze
                        if args.analysis_env_file:
                            load_analysis_environment(args.analysis_env_file)
                        if config:
                            os.environ["WEALTHPILOT_DB_PATH"] = config["database_path"]
                        result = asyncio.run(analyze(config["database_path"] if config else path,
                            config["portfolio_id"] if config else args.portfolio_id, operation, a,
                            contract=config.get("database_contract_path") if config else None,
                            synthetic=args.synthetic_input or config is None, worker_process=True))
                    elif operation == 'get_instrument_tradability' and (workflow or config):
                        from pocs.chatgpt_skills.reader import read_tradability
                        result=read_tradability(config['database_path'] if config else path,
                            config['portfolio_id'] if config else args.portfolio_id,a,
                            database_contract=config.get('database_contract_path') if config else None,
                            synthetic=args.synthetic_input or config is None)
                    elif operation == 'get_trade_history' and (workflow or config):
                        from pocs.chatgpt_skills.reader import read_history
                        result=read_history(config['database_path'] if config else path,
                            config['portfolio_id'] if config else args.portfolio_id,a,
                            database_contract=config.get('database_contract_path') if config else None,
                            synthetic=args.synthetic_input or config is None)
                    elif operation == "get_trade_and_sync_status" and (workflow or config):
                        from pocs.poc02_wealth_mcp.reader import readonly_snapshot
                        from sqlalchemy.orm import Session
                        from app.trade_status import query
                        from pocs.chatgpt_skills.contracts import TradeSyncQuery
                        request = TradeSyncQuery.model_validate(a)
                        with readonly_snapshot(config["database_path"] if config else path,
                                config.get("database_contract_path") if config else None) as connection:
                            pid=config["portfolio_id"] if config else args.portfolio_id
                            with Session(bind=connection,autoflush=False,join_transaction_mode="rollback_only") as db:
                                result=query(db,pid,**request.model_dump(),synthetic=config is None)
                    elif operation == "get_wealth_overview" and (workflow or config):
                        from pocs.poc02_wealth_mcp.reader import read_overview
                        result = read_overview(config["database_path"] if config else path,
                            config["portfolio_id"] if config else args.portfolio_id,
                            database_contract=config.get("database_contract_path") if config else None)
                        result["synthetic"] = args.synthetic_input or config is None
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

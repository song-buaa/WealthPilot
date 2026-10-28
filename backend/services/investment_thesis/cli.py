"""WealthPilot 4.3 E1 manual research bridge. No dotenv or backend lifespan."""
import argparse
import json
import os
import sqlite3
from types import SimpleNamespace

from pydantic import ValidationError
from .contracts import load_json, canonical
from .store import ThesisStore, version_diff
from .service import record_review, OriginalWorker


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default=os.environ.get("WEALTHPILOT_E1_DB_PATH"))
    parser.add_argument("--portfolio-id", type=int, required=True, help="Explicit local owner scope, not an account lookup")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    save = commands.add_parser("save")
    save.add_argument("--input", required=True)
    save.add_argument("--expected-revision", type=int, required=True)
    save.add_argument("--request-id", required=True)
    review = commands.add_parser("review")
    review.add_argument("--input", required=True)
    review.add_argument("--request-id", required=True)
    group = review.add_mutually_exclusive_group()
    group.add_argument("--analysis-file")
    group.add_argument("--run-analysis", action="store_true", help="Only explicit marked synthetic Worker in B1")
    review.add_argument("--business-python")
    review.add_argument("--mock-database")
    review.add_argument("--asset-view")
    review.add_argument("--scenario")
    for name in ("show", "history", "diff", "reviews"):
        sub = commands.add_parser(name)
        sub.add_argument("--thesis-id", required=True)
        if name == "show":
            sub.add_argument("--revision", type=int)
        if name == "diff":
            sub.add_argument("--from-revision", type=int, required=True)
            sub.add_argument("--to-revision", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        store = ThesisStore(args.store, args.portfolio_id)
        if args.command == "init":
            result = store.init()
        elif args.command == "save":
            result = store.save(load_json(args.input), args.expected_revision, args.request_id)
        elif args.command == "review":
            executor = None
            if args.run_analysis:
                if not args.business_python or not args.mock_database:
                    raise ValueError("E1_EXPLICIT_SYNTHETIC_WORKER_SETTINGS_REQUIRED")
                executor = OriginalWorker(SimpleNamespace(business_python=args.business_python,
                    portfolio_id=args.portfolio_id, real_config=None, mock_database=args.mock_database))
            result = record_review(store, load_json(args.input), args.request_id,
                analysis=load_json(args.analysis_file) if args.analysis_file else None,
                view=load_json(args.asset_view) if args.asset_view else None,
                scenario=load_json(args.scenario) if args.scenario else None, executor=executor)
        elif args.command == "show":
            result = store.get(args.thesis_id, args.revision)
        elif args.command == "history":
            result = store.history(args.thesis_id)
        elif args.command == "reviews":
            result = store.reviews(args.thesis_id)
        else:
            a = store.get(args.thesis_id, args.from_revision)
            b = store.get(args.thesis_id, args.to_revision)
            result = version_diff(a["content"], b["content"])
        print(canonical(result))
        return 2 if isinstance(result, dict) and result.get("state") == "FAILED" else 0
    except (ValueError, OSError, sqlite3.Error, ValidationError) as error:
        code = str(error) if str(error).startswith("E1_") and len(str(error)) < 150 else "E1_INPUT_OR_STORE_INVALID"
        print(json.dumps({"error": code, "saved": False, "execution_authorized": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Manual B3-2 bridge. It does not install or start any scheduler."""
import argparse
from datetime import datetime
import json

from .contracts import load_json
from .monitoring import ReviewMonitorStore, migrate_monitoring
from .store import ThesisStore


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timezone required")
    return parsed


def parser():
    value = argparse.ArgumentParser(description="Manual E1 review discovery; no provider, notification or trade")
    value.add_argument("--store", required=True)
    value.add_argument("--portfolio-id", type=int, required=True)
    commands = value.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    enable = commands.add_parser("enable")
    enable.add_argument("--input", required=True); enable.add_argument("--request-id", required=True)
    scan = commands.add_parser("scan")
    scan.add_argument("--as-of", type=instant, required=True); scan.add_argument("--events")
    action = commands.add_parser("transition")
    action.add_argument("--event-key", required=True); action.add_argument("--input", required=True)
    action.add_argument("--request-id", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--as-of", type=instant, required=True)
    return value


def main(argv=None):
    args = parser().parse_args(argv)
    store = ThesisStore(args.store, args.portfolio_id)
    try:
        if args.command == "init":
            result = migrate_monitoring(store)
        else:
            monitor = ReviewMonitorStore(store)
            if args.command == "enable":
                result = monitor.enable(load_json(args.input), args.request_id)
            elif args.command == "scan":
                result = monitor.scan(as_of=args.as_of, events=load_json(args.events) if args.events else [])
            elif args.command == "transition":
                result = monitor.transition(args.event_key, load_json(args.input), args.request_id)
            else:
                result = monitor.actionable(as_of=args.as_of)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        code = str(error) if str(error).startswith("E1_") else "E1_MONITOR_OPERATION_FAILED"
        print(json.dumps({"ok": False, "error": code}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

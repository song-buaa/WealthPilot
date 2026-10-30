"""Explicit offline internal S3 run/read/replay/diff entry; no Web/MCP writes."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--request", type=Path, required=True)
    run.add_argument("--p1-root", type=Path, required=True)
    run.add_argument("--archive", type=Path, required=True)
    for action in ("read", "replay"):
        item = sub.add_parser(action)
        item.add_argument("bundle", type=Path)
    diff = sub.add_parser("diff")
    diff.add_argument("old", type=Path)
    diff.add_argument("new", type=Path)
    args = parser.parse_args()
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    # Block socket access before any business import. Inputs must already exist.
    import socket
    def deny(*_args, **_kwargs):
        raise RuntimeError("S3_OFFLINE_NETWORK_FORBIDDEN")
    socket.socket.connect = deny
    socket.create_connection = deny
    from backend.services.quality_valuation_research import make_bundle, save_bundle, read_bundle, replay, difference
    if args.command == "run":
        bundle, files = make_bundle(args.p1_root, json.loads(args.request.read_text()))
        path = save_bundle(args.archive, bundle, files)
        result = dict(artifact_id=bundle["artifact_id"], bundle=str(path / "bundle.json"),
                      report=str(path / "comparison.md"), status=bundle["payload"]["result"]["status"])
    elif args.command == "read":
        result = read_bundle(args.bundle)["payload"]["result"]
    elif args.command == "replay":
        result = replay(args.bundle)
    else:
        result = difference(args.old, args.new)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()

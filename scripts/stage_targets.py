#!/usr/bin/env python3
"""Local operator stage-target configuration. Never exposed as an MCP write tool."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.services.stage_targets import StageTargetStore  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True, help="Private local configuration path, outside Git")
    parser.add_argument("--portfolio-id", required=True, type=int)
    sub = parser.add_subparsers(dest="command", required=True)
    candidate = sub.add_parser("candidate")
    candidate.add_argument("--content", required=True, help="Private JSON target proposal")
    candidate.add_argument("--source", required=True)
    sub.add_parser("show")
    activate = sub.add_parser("activate")
    activate.add_argument("--version", required=True)
    activate.add_argument("--digest", required=True)
    activate.add_argument("--operator", required=True)
    activate.add_argument("--confirmation-reference", required=True, help="Reference to the actual user confirmation")
    activate.add_argument("--confirm-stage-target", required=True, choices=["I_CONFIRM_THIS_VERSION_AND_DIGEST"])
    deactivate = sub.add_parser("deactivate")
    deactivate.add_argument("--version", required=True)
    deactivate.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    store = StageTargetStore(args.store)
    if args.command == "candidate":
        result = store.create_candidate(json.loads(Path(args.content).read_text()),
                                        portfolio_id=args.portfolio_id, source=args.source)
    elif args.command == "activate":
        result = store.activate(args.version, portfolio_id=args.portfolio_id, expected_digest=args.digest,
            operator=args.operator, reference=args.confirmation_reference, explicit_confirmation=True)
    elif args.command == "deactivate":
        result = store.deactivate(args.version, portfolio_id=args.portfolio_id, reason=args.reason)
    else:
        result = dict(active=store.resolve(args.portfolio_id), candidates=store.candidates(args.portfolio_id))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

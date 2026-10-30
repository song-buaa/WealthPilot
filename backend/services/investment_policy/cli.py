"""Explicit local candidate CLI. No activate, defaults, providers or business wiring."""
import argparse
import json
from pathlib import Path

from .contracts import canonical
from .service import PolicyService
from .store import PolicyStore, utc_now


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Explicit private isolated candidate SQLite path, outside Git")
    parser.add_argument("--shared-contract", help="Existing approved shared SQLite identity contract; no schema initialization permission")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-candidate")
    stage = sub.add_parser("stage")
    for field in ("scope", "philosophy", "policy", "approval-manifest", "request-id"):
        stage.add_argument("--" + field, required=True)
    stage.add_argument("--version", required=True, type=int)
    stage.add_argument("--expected-head-version", required=True, type=int)
    stage.add_argument("--created-at")
    validate = sub.add_parser("validate")
    for field in ("scope", "bundle-id", "request-id"):
        validate.add_argument("--" + field, required=True)
    for name in ("show", "context"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--scope", required=True)
        cmd.add_argument("--bundle-id", required=name == "show")
    args = parser.parse_args(argv)
    from .colocated import configured_store
    store = configured_store(args.db, shared_contract=args.shared_contract)
    if args.command == "init-candidate":
        result = store.init_candidate()
    elif args.command == "stage":
        manifest = json.loads(Path(args.approval_manifest).read_text())
        result = store.stage(scope=args.scope, version=args.version, philosophy_path=args.philosophy,
            policy_path=args.policy, expected_hashes=manifest["source_hashes"], request_id=args.request_id,
            expected_head_version=args.expected_head_version, created_at=args.created_at or utc_now())
    elif args.command == "validate":
        result = store.transition(scope=args.scope, bundle_id=args.bundle_id, state="VALIDATED", expected_state="DRAFT",
                                  request_id=args.request_id, at=utc_now())
    elif args.command == "show":
        result = store.read_bundle(args.scope, args.bundle_id)
    else:
        result = PolicyService(store).get_active_policy_context(args.scope)
    print(canonical(result))


if __name__ == "__main__":
    main()

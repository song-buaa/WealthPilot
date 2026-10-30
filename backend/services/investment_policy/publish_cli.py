"""Trusted operator CLI. Not exposed through MCP, HTTP, scheduler or PEER.

Never run on production without separate installation/activation authorization.
Files attest a human decision; this CLI does not authenticate the human itself.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

from .contracts import canonical
from .formal_publisher import PolicyPublisher
from .parser import PolicyError
from .store import PolicyStore


def private_artifact(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise PolicyError("PRIVATE_APPROVAL_ARTIFACT_REQUIRED")
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600 or stat.S_IMODE(path.parent.stat().st_mode) != 0o700):
        raise PolicyError("PRIVATE_APPROVAL_ARTIFACT_REQUIRED")
    return path.read_bytes()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--environment", required=True, choices=["ISOLATED_REHEARSAL", "PRODUCTION"])
    parser.add_argument("--shared-contract", help="Reviewed existing shared SQLite identity contract; never a migration flag")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("approve", "release"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--authorization", required=True)
        cmd.add_argument("--confirm-authorization-sha256", required=True)
        cmd.add_argument("--request-id", required=True)
        if name == "release":
            cmd.add_argument("--installation", required=True)
    status = sub.add_parser("lifecycle")
    status.add_argument("--scope", required=True)
    args = parser.parse_args(argv)
    from .colocated import configured_store
    publisher = PolicyPublisher(configured_store(args.db, shared_contract=args.shared_contract), environment=args.environment)
    if args.command == "lifecycle":
        result = publisher.lifecycle(args.scope)
    else:
        raw = private_artifact(args.authorization)
        if hashlib.sha256(raw).hexdigest() != args.confirm_authorization_sha256:
            raise PolicyError("EXPLICIT_APPROVAL_ARTIFACT_CONFIRMATION_REQUIRED")
        authorization = json.loads(raw)
        if args.command == "approve":
            result = publisher.approve(authorization=authorization, request_id=args.request_id)
        else:
            result = publisher.release(authorization=authorization, installation=json.loads(private_artifact(args.installation)),
                                       request_id=args.request_id)
        # Do not print private paths, source prose or financial parameters.
        result = {k: result[k] for k in ("scope", "bundle_id", "state", "action", "request_id", "recorded_at", "effective_at", "bundle_sha256") if k in result}
        result["execution_authorized"] = False
    print(canonical(result))


if __name__ == "__main__":
    main()

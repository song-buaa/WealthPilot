#!/usr/bin/env python3
"""Operator-only policy schema plan/install/recover. No services are started.

PRODUCTION apply requires separate human approval. Plans/receipts contain private
metadata and must stay outside Git. This command never loads dotenv or providers.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.services.investment_policy import schema_install as installer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "apply", "recover"])
    parser.add_argument("--plan", required=True)
    parser.add_argument("--database")
    parser.add_argument("--contract")
    parser.add_argument("--backup")
    parser.add_argument("--e1-database")
    parser.add_argument("--e1-backup")
    parser.add_argument("--code-sha")
    parser.add_argument("--authorization")
    parser.add_argument("--confirm-authorization-sha256")
    parser.add_argument("--receipt-directory")
    args = parser.parse_args()
    try:
        if args.command == "plan":
            if not all([args.database, args.contract, args.backup, args.e1_database, args.e1_backup, args.code_sha]):
                parser.error("plan requires database/contract/two backups/e1 database/code SHA")
            plan = installer.make_plan(database=args.database, contract_path=args.contract,
                backup=args.backup, manifest=installer.read_json(args.backup+".manifest.json"),
                e1_database=args.e1_database, e1_backup=args.e1_backup,
                e1_manifest=installer.read_json(args.e1_backup+".manifest.json"), code_sha=args.code_sha)
            installer.write_json(args.plan, plan)
            result = {"status": "PLAN_ONLY_NO_DATABASE_WRITE", "plan_sha256": installer.digest(plan)}
        else:
            if not all([args.authorization, args.confirm_authorization_sha256, args.receipt_directory]):
                parser.error("apply/recover requires authorization/hash/receipt directory")
            installer.require(installer.file_sha(args.authorization) == args.confirm_authorization_sha256,
                              "INSTALL_AUTHORIZATION_FILE_HASH_MISMATCH")
            result = installer.execute(plan=installer.read_json(args.plan),
                authorization=installer.read_json(args.authorization), receipt_directory=args.receipt_directory,
                recover=args.command == "recover")
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        # Private audit files hold details; no paths/SQL/records in public stdout.
        print(json.dumps({"status": "BLOCKED_KEEP_CLIENTS_STOPPED", "error_type": type(exc).__name__,
                          "sqlite_errorname": getattr(exc, "sqlite_errorname", None)}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

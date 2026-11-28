"""Run the ETF Owner job once or under the existing APScheduler mechanism."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.shared_sqlite import private_file
from backend.services.instruments.etf_refresh import latest_completed_session, refresh, register_refresh
from backend.services.investment_policy.configuration import owner_listings
from backend.services.investment_policy.etf_release import publish


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True, help="Owner-only raw Ed25519 seed in hex")
    parser.add_argument("--portfolio-id", type=int, required=True)
    parser.add_argument("--listing-file", type=Path, help="Optional 4.3 Owner public listing export")
    parser.add_argument("--listing-sha256", help="Independent SHA of the optional listing export")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--watch", action="store_true")
    operation.add_argument("--rollback-receipt", help="Signed historical receipt basename")
    args = parser.parse_args()
    if args.portfolio_id <= 0:
        parser.error("portfolio id must be positive")
    key_file = private_file(args.key_file, 0o600)
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key_file.read_text().strip()))

    if bool(args.listing_file) != bool(args.listing_sha256):
        parser.error("listing file and independent SHA must be provided together")

    def run():
        listings = None
        if args.listing_file:
            raw = private_file(args.listing_file).read_bytes()
            if sha256(raw).hexdigest() != args.listing_sha256:
                raise ValueError("ETF_LISTING_EXPORT_PIN_MISMATCH")
            listings = owner_listings(json.loads(raw), latest_completed_session(datetime.now(timezone.utc)),
                                      verify_owner=True)
        print(json.dumps(refresh(args.directory, key, args.portfolio_id, listings=listings), ensure_ascii=False),
              flush=True)

    if args.rollback_receipt:
        receipt = publish(args.directory, key, args.portfolio_id, {}, datetime.now(timezone.utc),
                          rollback_receipt=args.rollback_receipt)
        print(receipt.model_dump_json() if receipt else "NO_ACTIVE_VERSION")
    elif args.watch:
        scheduler = BlockingScheduler()
        register_refresh(scheduler, run)
        scheduler.start()
    else:
        run()


if __name__ == "__main__":
    main()

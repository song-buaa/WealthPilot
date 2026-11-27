"""Explicit E1 operator migration using the original consistent SQLite backup."""
from pathlib import Path

from pocs.poc03_mac_mini import snapshot

from .portfolio_store import migrate_portfolio_governance


def migrate_with_backup(store, backup_path):
    # Validate the original E1 owner and SQLite identity before creating a backup.
    with store.connection() as db:
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("E1_PORTFOLIO_SCHEMA_INTEGRITY")
    backup_path = Path(backup_path).absolute()
    manifest = snapshot.backup(store.path, backup_path)
    snapshot.verify(backup_path, manifest)

    def verify_inventory(db):
        if snapshot.inventory(db) != manifest["tables"]:
            raise ValueError("E1_PORTFOLIO_BACKUP_OUTDATED")

    result = migrate_portfolio_governance(store, verify_before=verify_inventory)
    return {**result, "backup_sha256": manifest["sha256"], "execution_authorized": False}

"""Offline CLI for importing explicit local consumption-statement sources."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import sqlite3
import sys
from zipfile import ZIP_DEFLATED, ZipFile

from app import database
from backend.services.consumption.import_runner import (
    BootstrapError,
    BootstrapSource,
    SourceKind,
    bootstrap_prepared_sources,
    prepare_sources,
)


SOURCE_SUFFIXES = {
    SourceKind.CMB_CREDIT: {".pdf"},
    SourceKind.CCB_CREDIT: {".eml"},
    SourceKind.CMB_DEBIT: {".pdf"},
}


def _paths(values: list[list[str]] | None) -> list[Path]:
    return [Path(value) for group in values or [] for value in group]


def _source_files(kind: SourceKind, paths: list[Path]) -> list[BootstrapSource]:
    """Read only explicitly supplied files/directories/ZIPs into memory."""
    values: list[BootstrapSource] = []
    suffixes = SOURCE_SUFFIXES[kind]
    for path in paths:
        if not path.exists():
            raise BootstrapError(f"{kind.value} source path is unavailable")
        if path.is_file() and path.suffix.lower() in suffixes:
            values.append(BootstrapSource(kind, path.read_bytes(), path.name))
            continue
        if path.is_dir():
            files = sorted(
                candidate for candidate in path.iterdir()
                if candidate.is_file() and candidate.suffix.lower() in suffixes
            )
            if not files:
                raise BootstrapError(f"{kind.value} source directory has no supported files")
            values.extend(BootstrapSource(kind, candidate.read_bytes(), candidate.name) for candidate in files)
            continue
        if path.is_file() and path.suffix.lower() == ".zip":
            try:
                with ZipFile(path) as archive:
                    members = [
                        member for member in archive.infolist()
                        if not member.is_dir()
                        and not member.filename.startswith("__MACOSX/")
                        and not Path(member.filename).name.startswith(".")
                        and Path(member.filename).suffix.lower() in suffixes
                    ]
                    if not members:
                        raise BootstrapError(f"{kind.value} ZIP has no supported files")
                    values.extend(
                        BootstrapSource(kind, archive.read(member), path.name)
                        for member in members
                    )
            except BootstrapError:
                raise
            except Exception as exc:
                raise BootstrapError(f"{kind.value} ZIP could not be read ({type(exc).__name__})") from exc
            continue
        raise BootstrapError(f"{kind.value} source has an unsupported format")
    return values


def _archive_sources(paths: list[Path]) -> list[BootstrapSource]:
    """Classify only explicit archive/directory members with existing adapters.

    EML is uniquely the supported CCB source.  A PDF is accepted only when one
    and exactly one of the two existing CMB adapters produces a valid statement.
    """
    values: list[BootstrapSource] = []
    for path in paths:
        if not path.exists():
            raise BootstrapError("archive source path is unavailable")
        if path.is_dir():
            entries = [
                (candidate.read_bytes(), candidate.name)
                for candidate in sorted(path.iterdir())
                if candidate.is_file() and candidate.suffix.lower() in {".pdf", ".eml"}
            ]
        elif path.is_file() and path.suffix.lower() == ".zip":
            try:
                with ZipFile(path) as archive:
                    entries = [
                        (archive.read(member), member.filename)
                        for member in archive.infolist()
                        if not member.is_dir()
                        and not member.filename.startswith("__MACOSX/")
                        and not Path(member.filename).name.startswith(".")
                        and Path(member.filename).suffix.lower() in {".pdf", ".eml"}
                    ]
            except Exception as exc:
                raise BootstrapError(f"archive could not be read ({type(exc).__name__})") from exc
        else:
            raise BootstrapError("archive source must be a ZIP or directory")
        if not entries:
            raise BootstrapError("archive source has no supported files")
        for source_bytes, member_name in entries:
            suffix = Path(member_name).suffix.lower()
            if suffix == ".eml":
                values.append(BootstrapSource(SourceKind.CCB_CREDIT, source_bytes, path.name))
                continue
            candidates = []
            for kind in (SourceKind.CMB_CREDIT, SourceKind.CMB_DEBIT):
                candidate = BootstrapSource(kind, source_bytes, path.name)
                try:
                    prepared = prepare_sources((candidate,))[0]
                except BootstrapError:
                    continue
                candidates.append((kind, prepared.parsed_statement))
            if len(candidates) != 1:
                raise BootstrapError("CMB PDF source type could not be determined safely")
            kind, statement = candidates[0]
            values.append(
                BootstrapSource(kind, source_bytes, path.name, parser=lambda _, value=statement: value)
            )
    return values


def _backup_database(db_path: Path) -> Path | None:
    if not db_path.is_file():
        return None
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_path = backup_dir / f"wealthpilot-before-consumption-{stamp}.db"
    sequence = 1
    while backup_path.exists():
        backup_path = backup_dir / f"wealthpilot-before-consumption-{stamp}-{sequence}.db"
        sequence += 1
    source = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return backup_path


def _display_db_path(db_path: Path) -> str:
    root = Path(__file__).resolve().parents[2]
    try:
        return str(db_path.resolve().relative_to(root))
    except ValueError:
        return db_path.name


def _print_summary(result) -> None:
    print("Consumption Bootstrap Complete")
    print("Accounts:")
    for outcome in result.account_outcomes:
        state = "created/reused" if outcome.created and outcome.reused else (
            "created" if outcome.created else "reused"
        )
        print(f"- {outcome.label}: {state}")
    print(f"Statements: parsed: {result.source_statement_count}")
    print(f"Import batches: new: {result.new_batch_count}; reused existing: {result.reused_batch_count}")
    print(f"Raw rows: inserted: {result.inserted_raw_row_count}; reused/skipped: {result.reused_raw_row_count}")
    print(f"Economic Events: active: {result.active_event_count}")
    print(
        "Eligibility: "
        f"eligible: {result.eligibility_counts.get('ELIGIBLE', 0)}; "
        f"ineligible: {result.eligibility_counts.get('INELIGIBLE', 0)}; "
        f"needs_review: {result.eligibility_counts.get('NEEDS_REVIEW', 0)}"
    )
    print(
        "Classification: "
        f"classified: {result.classification_counts.get('CLASSIFIED', 0)}; "
        f"needs_review: {result.classification_counts.get('NEEDS_REVIEW', 0)}; "
        f"not_applicable: {result.classification_counts.get('NOT_APPLICABLE', 0)}"
    )
    print(
        f"Analytics: months available: {result.analytics_month_count}; "
        f"latest month status: {result.latest_month_status or 'N/A'}"
    )
    print(f"Analytics validation: non-empty: {'PASS' if result.analytics_non_empty else 'FAIL'}; "
          f"12-month invariant: {'PASS' if result.invariant_passed else 'FAIL'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import explicit local consumption statements offline.")
    parser.add_argument("--cmb-credit", nargs="+", action="append", metavar="PATH")
    parser.add_argument("--ccb-credit", nargs="+", action="append", metavar="PATH")
    parser.add_argument("--cmb-debit", nargs="+", action="append", metavar="PATH")
    parser.add_argument("--archive", nargs="+", action="append", metavar="PATH", help="Explicit ZIP or directory containing the three supported source types.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and validate only; make no DB writes.")
    parser.add_argument("--no-backup", action="store_true", help="Skip the pre-import local SQLite backup.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    requested = {
        SourceKind.CMB_CREDIT: _paths(args.cmb_credit),
        SourceKind.CCB_CREDIT: _paths(args.ccb_credit),
        SourceKind.CMB_DEBIT: _paths(args.cmb_debit),
    }
    archive_paths = _paths(args.archive)
    if not any(requested.values()) and not archive_paths:
        print("Bootstrap failed: at least one explicit statement path is required", file=sys.stderr)
        return 2
    try:
        sources = [source for kind, paths in requested.items() for source in _source_files(kind, paths)]
        sources.extend(_archive_sources(archive_paths))
        prepared = prepare_sources(sources)
        db_path = Path(database.DB_PATH)
        print(f"Target DB: {_display_db_path(db_path)}")
        print("Sources:")
        names = defaultdict(list)
        for item in prepared:
            names[item.kind].append(item.source_name)
        for kind in SourceKind:
            if names[kind]:
                print(f"- {kind.value.replace('_', ' ').title()}: {', '.join(sorted(set(names[kind])))}")
        if args.dry_run:
            result = bootstrap_prepared_sources(None, prepared, dry_run=True)  # type: ignore[arg-type]
            print(f"Dry run: parsed statements: {result.source_statement_count}; raw rows: {result.parsed_row_count}; DB writes: NO")
            return 0
        backup = None if args.no_backup else _backup_database(db_path)
        if backup is not None:
            print(f"Backup: created ({backup.name})")
        else:
            print("Backup: not needed (target DB does not exist)")
        database.init_db()
        session = database.get_session()
        try:
            result = bootstrap_prepared_sources(session, prepared)
        finally:
            session.close()
        _print_summary(result)
        return 0 if result.analytics_non_empty and result.invariant_passed else 1
    except BootstrapError as exc:
        print(f"Bootstrap failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate the Stage 1E Development-only human chart review pack."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.services.technical_patterns.real_review import generate_review_pack


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cache-dir",
        required=True,
        type=Path,
        help="Local temporary directory containing Stage 0 canonical cache exports",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
    )
    args = parser.parse_args()
    manifest = generate_review_pack(
        repo_root=args.repo_root.resolve(),
        cache_dir=args.cache_dir.resolve(),
        workers=args.workers,
    )
    print(f"gate_status={manifest['gate_status']}")
    print(f"case_count={manifest['case_count']}")
    print(f"manifest_hash={manifest['manifest_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

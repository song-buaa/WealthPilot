#!/usr/bin/env python3
"""Run nine candidate scopes entirely from immutable Dataset v2 artifacts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend.services.pattern_data.immutable_dataset import content_hash  # noqa: E402
from backend.services.technical_patterns.runtime_validation_v2 import (  # noqa: E402
    run_dataset_v2_validation,
)


def main() -> int:
    result = run_dataset_v2_validation(
        REPO_ROOT / "docs/pattern_review/REAL_IBKR_PATTERN_DATASET_V2_MANIFEST.json",
        artifact_root=REPO_ROOT,
    )
    result["manifest_hash"] = content_hash(result)
    path = REPO_ROOT / "docs/pattern_review/REAL_IBKR_PATTERN_RUNTIME_VALIDATION_V2_MANIFEST.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for item in result["promotion_scopes"]:
        print(
            item["pattern_type"], item["economic_asset_class"], item["verdict"],
            item["holdout_result"], item["untouched_result"],
        )
    print("validation manifest:", result["manifest_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

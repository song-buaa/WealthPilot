"""Read-only C projection against original U1 and ACTIVE synthetic policy."""
from copy import deepcopy

import pytest

from backend.services.allocation_service import project_frozen_concentration
from backend.services.investment_policy.contracts import digest
from pocs.chatgpt_skills.worker import frozen_concentration
from tests.test_b2_allocation import clock, source, owner_file, publish
from tests.test_b3_allocation import b3
from tests.test_c_allocation_integration import active
from tests.test_c_concentration import STAMP, AT, TESLA


@pytest.mark.parametrize("with_owner", [True, False])
def test_same_source_projection_does_not_write(active, b3, tmp_path, monkeypatch, with_owner):
    from backend.services.wealth_service import freeze_investable_scope
    view, classes = b3
    view["captured_at"] = STAMP
    quality = view["data_quality"]
    quality["accounts"][0]["account_synced_at"] = STAMP
    for row in quality["security_inputs"]:
        row.update(cash_as_of=STAMP, positions_as_of=STAMP, price_as_of=AT.date().isoformat(), price_time_basis="close")
    quality["security_inputs"][4]["symbol"] = "TSLA:US"
    owner_file(view, tmp_path/"owner.json", monkeypatch, manual_classes=classes)
    freeze_investable_scope(view)
    if not with_owner:
        quality.pop("investable_owner_evidence")
    db = tmp_path/"frozen.sqlite"
    publish(view, db, monkeypatch)
    before = db.read_bytes()
    policy = active[0].path.read_bytes()
    value = frozen_concentration(db, 7, view["snapshot_id"])
    assert value == project_frozen_concentration(7, deepcopy(view))
    assert value["snapshot_sha256"] == digest(view)
    assert db.read_bytes() == before and active[0].path.read_bytes() == policy
    assert not value["execution_authorized"]
    assert value["issuers"][TESLA]["direct_cny"] == "300"
    if with_owner:
        assert value["issuers"][TESLA]["risk_status"] == "HUMAN_REVIEW"
    else:
        assert value["status"] == "CONDITIONAL_RISK"
        assert value["denominator_cny"] is None
        assert value["assumptions"]
        row = value["issuers"][TESLA]
        assert not row["policy_hits"] and row["known_lower_bound_pct"] is None
    assert frozen_concentration(db, 7, view["snapshot_id"], expected_policy={"wrong": "bundle"})["status"] == "UNKNOWN"

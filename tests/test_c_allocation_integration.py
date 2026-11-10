"""Original B2/B3 read path with isolated ACTIVE policy and owner ETF manifest."""

from datetime import timedelta
from decimal import Decimal
import hashlib
import json

import pytest

from app import asset_views
from backend.services.investment_policy import runtime, store
from backend.services.investment_policy.contracts import digest
from tests import test_policy_integration as policies
from tests.test_allocation_snapshot_errors import client  # noqa: F401
from tests.test_b2_allocation import clock, owner_file, request, source  # noqa: F401
from tests.test_b3_allocation import B3_POLICY, b3  # noqa: F401
from tests.test_c_concentration import AT, STAMP, TESLA, holdings


@pytest.fixture
def active(tmp_path, monkeypatch, clock):
    raw = B3_POLICY.decode().replace("超过10%", "超过6%").replace("超过12%", "超过7%").replace("超过15%", "超过8%")
    monkeypatch.setattr(policies, "POLICY", raw.encode())
    monkeypatch.setattr(policies, "utc_now", lambda: STAMP)
    monkeypatch.setattr(runtime, "utc_now", lambda: STAMP)
    monkeypatch.setattr(store, "utc_now", lambda: STAMP)
    monkeypatch.setattr(asset_views, "now", lambda: AT)
    return policies.active.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize("manifest_state", ["valid", "wrong_pin", "wrong_scope", "absent"])
def test_original_snapshot_reads_pinned_etf_and_keeps_b3(active, b3, tmp_path, monkeypatch, client, manifest_state):
    view, classes = b3
    view["captured_at"] = STAMP
    quality = view["data_quality"]
    quality["accounts"][0]["account_synced_at"] = STAMP
    for row in quality["security_inputs"]:
        row.update(cash_as_of=STAMP, positions_as_of=STAMP,
                   price_as_of=AT.date().isoformat(), price_time_basis="close")
    quality["security_inputs"][4]["symbol"] = "TSLA:US"
    path = tmp_path / "etf-owner.json"
    if manifest_state != "absent":
        record = holdings("QQQ:US", {"TSLA:US":".1"})
        data = json.dumps({"schema_version":"wealthpilot.etf-evidence.v1",
                           "portfolio_id": 8 if manifest_state == "wrong_scope" else 7,
                           "records":[record.model_dump(mode="json")]}).encode()
        path.write_bytes(data)
        path.chmod(0o600)
        monkeypatch.setenv("WEALTHPILOT_ETF_EVIDENCE_FILE", str(path))
        monkeypatch.setenv("WEALTHPILOT_ETF_EVIDENCE_SHA256",
                           "e"*64 if manifest_state == "wrong_pin" else hashlib.sha256(data).hexdigest())
    owner_file(view, tmp_path/"owner.json", monkeypatch, manual_classes=classes,
               valid_until=(AT+timedelta(days=1)).isoformat())
    before_policy = active[0].path.read_bytes()
    result = request(view, tmp_path, monkeypatch, client)
    assert result.status_code == 200, result.text
    assert active[0].path.read_bytes() == before_policy
    if manifest_state != "absent":
        assert path.read_bytes() == data
    meta = result.json()["policy_allocation"]
    c = meta["concentration"]
    assert c["snapshot_id"] == meta["snapshot_id"]
    assert c["snapshot_sha256"] == meta["snapshot_sha256"] == digest(view)
    assert c["policy_reference"] == meta["policy_reference"]
    assert c["classification_sha256"] == digest(meta["hierarchy"]["classified_assets"])
    assert Decimal(c["denominator_cny"]) == Decimal(meta["denominator_cny"]) == 1000
    row = c["issuers"][TESLA]
    assert row["risk_status"] == "HUMAN_REVIEW" and row["proven_upper_bound_pct"] is None
    assert isinstance(row["direct_pct"], str) and Decimal(row["direct_pct"]) == 30
    assert Decimal(row["direct_cny"]) == 300
    assert Decimal(row["indirect_known_cny"]) == (15 if manifest_state == "valid" else 0)
    assert not c["execution_authorized"]

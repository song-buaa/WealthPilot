"""Human preview/commit over original E1. Never issues orders or policy changes."""
from datetime import timedelta, timezone
import secrets

from .bridge import ResearchBridge
from .contracts import canonical, now
from .portfolio_contracts import PortfolioConfirmation, PortfolioPreviewRequest
from .portfolio_sources import binding, clear_absent_risk, validate_source_scope
from .portfolio_store import PortfolioStore, require_schema
from .store import digest


def timestamp(value):
    return value.astimezone(timezone.utc).isoformat()


def _checkpoint(scenario, path, item):
    if item.stage_index is None:
        return None
    stage = scenario["paths"][path][item.stage_index]
    products = stage["allocation"]
    return {"status": "PLANNED_NOT_EXECUTION_FACT", "basis": scenario["basis"], "item_id": item.item_id,
            "path": path, "stage_index": item.stage_index, "stage_label": stage["label"],
            "investment_assets_cny": stage["investment_assets_cny"], "debt_principal_cny": stage["loan_balances_cny"],
            "liabilities_cny": stage["liabilities_cny"], "cash": stage["cash"],
            "budget_gaps": products.get("secondary"), "etf_products": products.get("etf", {}).get("products"),
            "direct_products": products.get("direct_equity", {}).get("issuers"), "new_scenario_risks": stage["newly_breached_issuers"],
            "scenario_risk_statuses": {issuer: row["scenario_risk_status"]
                                       for issuer, row in stage["concentration"].get("issuers", {}).items()},
            "holdings": stage["scenario_holdings"], "actions": stage["actions"], "completion_condition": "Verify against qualified new facts and owner execution evidence",
            "share_quantities": None, "review_due_at": timestamp(item.review_due_at),
            "reconsider_or_revoke_when": item.revoke_when}


def _event_plan(item, prior, fact, policy_sha, *, qualified, at):
    if not item.risk_key or item.kind in {"FORMAL_POLICY_EXCEPTION", "OPERATIONAL_BLOCKED"}:
        return []
    action = item.event_action
    if not qualified:
        if action is not None:
            raise ValueError("E1_PORTFOLIO_ACTUAL_RISK_REQUIRED")
        return []
    hit = fact is not None and fact["hit"]
    existing = prior["state"] if prior else None
    current_policy = not prior or prior["policy_bundle_sha256"] == policy_sha
    expired = bool(prior and prior["expires_at"] <= timestamp(at))
    due = bool(prior and prior["review_due_at"] <= timestamp(at))
    if action is None:
        if not hit:
            return []
        else:
            action = "DECIDE"
    if action == "EXPIRE":
        if not expired or not current_policy or existing in {"RESOLVED", "EXPIRED", "BLOCKED_WITH_REASON"}:
            raise ValueError("E1_PORTFOLIO_RISK_NOT_EXPIRABLE")
        return ["EXPIRED"]
    if action == "RESOLVE":
        if not prior or existing in {"RESOLVED", "EXPIRED"} or not current_policy or not fact or hit:
            raise ValueError("E1_PORTFOLIO_RISK_CLEAR_EVIDENCE_REQUIRED")
        return ["RESOLVED"]
    if action == "MONITOR":
        if existing != "DECISION_RECORDED" or not current_policy or expired:
            raise ValueError("E1_PORTFOLIO_RISK_TRANSITION_INVALID")
        return ["MONITORING"]
    if action == "BLOCK":
        if not hit:
            raise ValueError("E1_PORTFOLIO_ACTUAL_RISK_REQUIRED")
        return ["NEW", "REVIEW_OPEN", "BLOCKED_WITH_REASON"] if not prior else (
            [] if existing == "BLOCKED_WITH_REASON" and current_policy and not due else ["BLOCKED_WITH_REASON"])
    if not hit:
        raise ValueError("E1_PORTFOLIO_ACTUAL_RISK_REQUIRED")
    reopen = prior and (not current_policy or due or expired or existing in {"RESOLVED", "EXPIRED"})
    if action == "REOPEN":
        if not reopen:
            raise ValueError("E1_PORTFOLIO_RISK_REOPEN_UNPROVEN")
        return ["REOPENED", "REVIEW_OPEN"]
    if action == "OPEN" and prior and not reopen:
        return [] if existing != "NEW" else ["REVIEW_OPEN"]
    if not prior:
        states = ["NEW", "REVIEW_OPEN"]
    elif reopen:
        states = ["REOPENED", "REVIEW_OPEN"]
    else:
        states = []
    if action == "DECIDE":
        states.append("DECISION_RECORDED")
    return states


class PortfolioGovernance(ResearchBridge):
    """Shares E1 preview expiry and token handling, with distinct portfolio objects."""
    def __init__(self, thesis_store, source_reader, *, wall_clock=now, capacity=4, **options):
        super().__init__(PortfolioStore(thesis_store), capacity=capacity, **options)
        self.source_reader = source_reader
        self.wall_clock = wall_clock

    def preview(self, raw):
        request = PortfolioPreviewRequest.model_validate(raw)
        source = self.source_reader(request)
        validate_source_scope(source, request, self.store.portfolio_id)
        if len(canonical(source).encode()) > 16 * 1024 * 1024:
            raise ValueError("E1_PORTFOLIO_EVIDENCE_TOO_LARGE")
        at = self.wall_clock()
        normalized = request.model_dump(mode="json")
        request_fingerprint = digest(normalized)
        policy_ref = source["policy"]["reference"]
        qualified = source["qualification"]["status"] == "QUALIFIED"
        facts = source["risk_facts"]
        scenario = source["scenario"]
        with self.store.connection() as db:
            require_schema(db)
            prior = self.store.request(db, request.request_id)
            if prior is not None:
                if prior["confirmation_receipt"]["request_fingerprint"] != request_fingerprint:
                    raise ValueError("E1_REQUEST_ID_CONFLICT")
                raise ValueError("E1_PORTFOLIO_ALREADY_RECORDED_READ_HISTORY")
            head = self.store.head(db, request.bundle_id)
            if (head["revision"] if head else 0) != request.expected_revision:
                raise ValueError("E1_REVISION_CONFLICT_REPREVIEW")
            existing = self.store.risk_heads(db)
            previews = []
            for item in request.items:
                if item.kind == "REVOKE_DECISION" and head["bundle_state"] == "REVOKED":
                    raise ValueError("E1_PORTFOLIO_ALREADY_REVOKED")
                if item.review_due_at <= at:
                    raise ValueError("E1_PORTFOLIO_REVIEW_IN_PAST")
                prior_event = existing.get(item.risk_key)
                fact = facts.get(item.risk_key) or clear_absent_risk(source, prior_event)
                if item.risk_key and not (prior_event or fact and fact["hit"]):
                    raise ValueError("E1_PORTFOLIO_RISK_NOT_FOUND")
                research = [self.store.research(db, link) for link in item.research]
                state_plan = _event_plan(item, prior_event, fact, policy_ref["bundle_sha256"], qualified=qualified, at=at)
                previews.append({"proposal": item.model_dump(mode="json"), "research_sha256": [row["sha256"] for row in research],
                                 "prior_risk_sha256": prior_event["sha256"] if prior_event else None,
                                 "event_states": state_plan, "actual_risk": fact,
                                 "disposition_state": "PENDING_HUMAN_DECISION" if item.kind == "FORMAL_POLICY_EXCEPTION"
                                 else "AWAITING_CONFIRMATION",
                                 "checkpoint": _checkpoint(scenario, request.selected_path, item)})
        source_binding = binding(source)
        payload = {"portfolio_id": self.store.portfolio_id, "request": normalized, "source_binding": source_binding,
                   "policy_reference": policy_ref, "qualification": source["qualification"],
                   "actual_risk_candidates": {key: fact for key, fact in facts.items() if fact["hit"]},
                   "simulation": scenario, "items": previews,
                   "supersedes": head["decision_revision_ref"] if head else None}
        fingerprint = digest(payload)
        # Full archive stays in the bounded private preview cache; the visible
        # projection contains enough context to make an informed selection.
        public = {**payload, "simulation": {key: value for key, value in scenario.items()
                    if key != "evidence_archive"} if scenario else None}
        receipt = {"schema_version": "wealthpilot.e1.portfolio-preview.v1", "request_id": request.request_id,
                   "preview_fingerprint": fingerprint, "payload": public, "saved": False,
                   "expires_at": timestamp(at + timedelta(seconds=self.ttl)),
                   "confirmation_required": "保存这次组合决定", "execution_authorized": False}
        return self._remember(receipt, archive=source, request_fingerprint=request_fingerprint)

    def commit(self, raw):
        receipt = PortfolioConfirmation.model_validate(raw)
        with self.lock:
            return self._commit(receipt)

    def _commit(self, receipt):
        if receipt.confirmation == "取消":
            preview = self._pending(receipt.preview_token)
            self._verify_preview(receipt, preview)
            self.pending.pop(receipt.preview_token, None)
            return {"saved": False, "cancelled": True, "execution_authorized": False}
        if not receipt.confirmed or receipt.confirmation != "保存这次组合决定" or not receipt.selected_item_ids:
            raise ValueError("E1_EXPLICIT_CONFIRMATION_REQUIRED")
        selection = sorted(receipt.selected_item_ids)
        confirmation_fingerprint = digest({"preview_fingerprint": receipt.preview_fingerprint, "selected": selection})
        token_sha256 = digest(receipt.preview_token)
        with self.store.connection() as db:
            require_schema(db)
            prior = self.store.request(db, receipt.request_id)
        if prior:
            return self._retry(prior, confirmation_fingerprint, token_sha256)
        preview = self._pending(receipt.preview_token)
        self._verify_preview(receipt, preview)
        pending = self.pending[receipt.preview_token]
        payload = preview["payload"]
        request = PortfolioPreviewRequest.model_validate(payload["request"])
        known = {item["proposal"]["item_id"]: item for item in payload["items"]}
        if set(selection) - set(known):
            raise ValueError("E1_PORTFOLIO_SELECTION_CHANGED")
        selected = [entry for entry in payload["items"] if entry["proposal"]["item_id"] in selection]
        # Requalify source/Policy/ETF on every first commit; reuse archived
        # snapshot bytes and original evaluated result only if still equivalent.
        with self.store.connection(write=True) as db:
            prior = self.store.request(db, receipt.request_id)
            if prior:
                return self._retry(prior, confirmation_fingerprint, token_sha256)
            head = self.store.head(db, request.bundle_id)
            if (head["revision"] if head else 0) != request.expected_revision:
                raise ValueError("E1_REVISION_CONFLICT_REPREVIEW")
            fresh = self.source_reader(request)
            validate_source_scope(fresh, request, self.store.portfolio_id)
            if binding(fresh) != payload["source_binding"]:
                raise ValueError("E1_PORTFOLIO_SOURCE_CHANGED_REPREVIEW")
            self._pending(receipt.preview_token)
            at = self.wall_clock()
            risk_heads = self.store.risk_heads(db)
            decisions, events = self._selected(request, selected, payload, risk_heads, at)
            record = self._record(request, payload, pending, decisions, events, at, confirmation_fingerprint, token_sha256)
            self.store.insert(db, record, events)
        readback = self.store.query(bundle_id=request.bundle_id, revision=record["revision"], include_evidence=True)["records"]
        if len(readback) != 1 or readback[0]["sha256"] != digest(record):
            raise ValueError("E1_SAVE_READBACK_MISMATCH")
        self.pending.pop(receipt.preview_token, None)
        return self._saved(readback[0])

    @staticmethod
    def _verify_preview(receipt, preview):
        if (receipt.request_id != preview["request_id"]
                or not secrets.compare_digest(receipt.preview_fingerprint, preview["preview_fingerprint"])):
            raise ValueError("E1_PREVIEW_CHANGED_REPREVIEW")

    @staticmethod
    def _saved(record):
        return {"saved": True, "state": "SAVED_AND_READ_BACK", "request_id": record["request_id"],
                "record_sha256": record["sha256"], "record": {k: v for k, v in record.items() if k != "evidence_archive"},
                "execution_authorized": False}

    def _retry(self, record, fingerprint, token_sha256):
        original = record["confirmation_receipt"]
        if not (secrets.compare_digest(original["selection_fingerprint"], fingerprint)
                and secrets.compare_digest(original["token_sha256"], token_sha256)):
            raise ValueError("E1_REQUEST_ID_CONFLICT")
        return self._saved(record)

    @staticmethod
    def _selected(request, selected, payload, risk_heads, at):
        decisions, events = [], []
        qualified = payload["qualification"]["status"] == "QUALIFIED"
        policy_sha = payload["policy_reference"]["bundle_sha256"]
        for entry in selected:
            item = next(item for item in request.items if item.item_id == entry["proposal"]["item_id"])
            if item.review_due_at <= at:
                raise ValueError("E1_PORTFOLIO_REVIEW_IN_PAST")
            prior = risk_heads.get(item.risk_key)
            prior_sha = prior["sha256"] if prior else None
            if prior_sha != entry["prior_risk_sha256"]:
                raise ValueError("E1_PORTFOLIO_EVENT_CHANGED_REPREVIEW")
            transitions = _event_plan(item, prior, entry["actual_risk"], policy_sha, qualified=qualified, at=at)
            if transitions != entry["event_states"]:
                raise ValueError("E1_PORTFOLIO_EVENT_CHANGED_REPREVIEW")
            state = ("PENDING_HUMAN_DECISION" if item.kind == "FORMAL_POLICY_EXCEPTION" else
                     "HUMAN_DECISION_RECORDED" if qualified else "RESEARCH_DECISION_WITH_ASSUMPTIONS")
            temporary_exception = ({"status": "PENDING_HUMAN_DECISION", "risk_key": item.risk_key,
                                    "effective": False, "expires_at": timestamp(item.expires_at)}
                                   if item.kind == "FORMAL_POLICY_EXCEPTION" else None)
            decisions.append({**entry, "disposition_state": state, "temporary_exception": temporary_exception,
                              "operational_blockage_basis": "HUMAN_REPORTED" if item.kind == "OPERATIONAL_BLOCKED" else None,
                              "policy_exception_active": False})
            rev = prior["revision"] if prior else 0
            for index, next_state in enumerate(transitions, 1):
                current_fact = entry["actual_risk"]
                evaluated_snapshot = {"id": request.snapshot_id, "sha256": request.snapshot_sha256}
                events.append({"risk_key": item.risk_key, "revision": rev+index, "state": next_state,
                               "policy_bundle_sha256": policy_sha, "recorded_at": timestamp(at),
                               "reason": item.reason, "actual_risk": current_fact or prior["actual_risk"],
                               "actual_risk_snapshot_ref": evaluated_snapshot if current_fact else prior["actual_risk_snapshot_ref"],
                               "actual_risk_policy_reference": payload["policy_reference"] if current_fact else prior["actual_risk_policy_reference"],
                               "evaluation_snapshot_ref": evaluated_snapshot,
                               "policy_reference": payload["policy_reference"],
                               "review_timing_basis": "HUMAN_SCHEDULE_POLICY_SLA_UNVERIFIED",
                               "review_due_at": timestamp(item.review_due_at), "expires_at": timestamp(item.expires_at),
                               "decision_bundle_ref": request.bundle_id, "decision_revision_ref": request.expected_revision+1,
                               "execution_authorized": False})
        return decisions, events

    def _record(self, request, payload, pending, decisions, events, at, confirmation_fingerprint, token_sha256):
        archive = pending["archive"]
        scenario = archive["scenario"]
        due = min(timestamp(item.review_due_at) for item in request.items
                  if item.item_id in {row["proposal"]["item_id"] for row in decisions})
        revision = request.expected_revision + 1
        return {
            "schema_version": "wealthpilot.e1.portfolio-decision.v1", "portfolio_id": self.store.portfolio_id,
            "bundle_id": request.bundle_id, "revision": revision, "request_id": request.request_id,
            "decision_bundle_ref": request.bundle_id, "decision_revision_ref": f"{request.bundle_id}:{revision}",
            "bundle_state": "REVOKED" if decisions[0]["proposal"]["kind"] == "REVOKE_DECISION" else "RECORDED",
            "supersedes": payload["supersedes"], "base_snapshot_ref": {"id": request.snapshot_id, "sha256": request.snapshot_sha256},
            "original_policy_reference": payload["policy_reference"], "review_due_at": due, "recorded_at": timestamp(at),
            "scenario_ref": {"path": request.selected_path, "input_sha256": scenario["scenario_revision_sha256"],
                             "evaluation_sha256": scenario["evaluation_sha256"], "replay_status": "HISTORICAL_RESULT_FROZEN_REPLAY_NOT_EXECUTED"}
                            if scenario else None,
            "qualification": payload["qualification"], "decisions": decisions,
            "risk_revisions": [{"risk_key": row["risk_key"], "revision": row["revision"], "state": row["state"]} for row in events],
            "expected_checkpoints": [row["checkpoint"] for row in decisions if row["checkpoint"] is not None],
            "evidence_requirements": ["COMPARABLE_U1_FACTS", "OWNER_EXECUTION_EVIDENCE", "CURRENT_ACTIVE_POLICY"],
            "confirmation_receipt": {"confirmed_at": timestamp(at), "confirmation": "保存这次组合决定",
                "authority": "PRIVATE_OPERATOR_CLIENT_ATTESTATION",
                "preview_fingerprint": pending["preview"]["preview_fingerprint"],
                "selection_fingerprint": confirmation_fingerprint, "request_fingerprint": pending["request_fingerprint"],
                "token_sha256": token_sha256, "selected_item_ids": sorted(row["proposal"]["item_id"] for row in decisions)},
            "evidence_archive": archive, "execution_authorized": False,
        }

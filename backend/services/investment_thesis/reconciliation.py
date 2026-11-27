"""F review over the unchanged E1 record and an explicitly selected existing U1."""
from collections import defaultdict
from decimal import localcontext

from pydantic import TypeAdapter

from .portfolio_store import PortfolioStore, require_schema
from .portfolio_sources import clear_absent_risk, read_portfolio_sources
from .portfolio_contracts import PortfolioSourceRequest
from .snapshot_delta import (
    SnapshotFacts, REACHED, PARTIAL, UNKNOWN, UNOBSERVED, compare_cash, compare_debt, compare_positions,
    decimal, instant, issuer_value_deltas, listing, symbol_key, ZERO,
)
from .reconciliation_evidence import execution_for_trade, read_execution_evidence
from .store import digest


def _plan(store, db, request):
    require_schema(db)
    if request.bundle_id:
        head = store.head(db, request.bundle_id)
        if not head:
            raise ValueError("E1_PORTFOLIO_DECISION_NOT_FOUND")
        if request.revision is None or head["revision"] == request.revision:
            return head, head
        row = db.execute("SELECT * FROM portfolio_decision_revisions WHERE portfolio_id=? AND bundle_id=? AND revision=?",
                         (store.portfolio_id, request.bundle_id, request.revision)).fetchone()
        return store.thesis_store._read(row), head
    # Use each bundle's real head, omitting revoked and risk-only bundles.
    row = db.execute(
        "SELECT r.* FROM portfolio_decision_revisions r JOIN "
        "(SELECT bundle_id, MAX(revision) AS revision FROM portfolio_decision_revisions "
        "WHERE portfolio_id=? GROUP BY bundle_id) h USING(bundle_id,revision) "
        "WHERE r.portfolio_id=? AND json_extract(r.payload,'$.bundle_state')='RECORDED' "
        "AND json_array_length(r.payload,'$.expected_checkpoints')>0 "
        "ORDER BY json_extract(r.payload,'$.recorded_at') DESC,r.rowid DESC LIMIT 1",
        (store.portfolio_id, store.portfolio_id)).fetchone()
    if not row:
        raise ValueError("E1_PORTFOLIO_DECISION_NOT_FOUND")
    value = store.thesis_store._read(row)
    return value, value

def _current_sources(database, portfolio_id, request, view, contract):
    from backend.services.investment_policy.runtime import current

    session = current(portfolio_id)
    context = session.context
    if context["mode"] != "POLICY_ACTIVE" or context.get("status") != "ACTIVE":
        # Permit fact comparison with an unavailable policy, but do not
        # construct an ACTIVE reference or any risk clearances.
        return {"policy": {"reference": None}, "qualification": {"status": "UNKNOWN", "issues": ["POLICY_UNAVAILABLE"]},
                "actual_allocation": {}, "actual_concentration": {}, "risk_facts": {}}
    return read_portfolio_sources(database, portfolio_id, PortfolioSourceRequest(
        snapshot_id=view["snapshot_id"], snapshot_sha256=request.snapshot_sha256,
        policy_bundle_sha256=context["policy_reference"]["bundle_sha256"]), contract, include_conditional=True)


def _expected_stage(record, checkpoint):
    scenario = record["evidence_archive"].get("scenario") or {}
    path = scenario.get("paths", {}).get(checkpoint["path"], [])
    if checkpoint["stage_index"] >= len(path):
        raise ValueError("E1_PORTFOLIO_SCENARIO_ARCHIVE_MISMATCH")
    steps = [event for stage in path[1:checkpoint["stage_index"] + 1] for event in stage["actions"]]
    return path, steps


def _cash_origin(ref, old, steps, path):
    if not ref:
        return None
    if ref.startswith("position:"):
        row = old.by_ref.get(ref, {})
        return row.get("account_ref"), row.get("original_currency") or row.get("currency")
    # The original D result owns the claimed lot origin. A fully consumed
    # lot can still be found in earlier events by walking its parent action.
    for stage in reversed(path):
        if ref in stage.get("cash", {}):
            lot = stage["cash"][ref]
            return lot["account"], lot["currency"]
    if not ref.startswith("action:"):
        return None
    parent = next((event["inputs"] for event in steps if event["action_id"] == ref[7:]), {})
    if parent.get("kind") == "TRANSFER_CASH":
        return parent["destination_account"], parent["destination_currency"]
    if parent.get("kind") == "SELL_TO_CASH":
        row = old.by_ref.get(parent["position_ref"], {})
        return row.get("account_ref"), row.get("original_currency") or row.get("currency")
    return None


def _trade_key(action, old, steps, path):
    if action["kind"] == "SELL_TO_CASH":
        row = old.by_ref.get(action["position_ref"])
        if row and (security := listing(row)) and row.get("account_ref"):
            return (row["account_ref"], *security)
    if action["kind"] == "BUY_WITH_CASH":
        origin = _cash_origin(action["cash_ref"], old, steps, path)
        if origin and all(origin):
            return origin[0], symbol_key(action["symbol"], origin[1]), origin[1]
    return None


def _position_progress(action, row, target):
    if not row or row["issue"] or row["attribution"] == "POSSIBLE_ACCOUNT_TRANSFER":
        return UNKNOWN, "POSITION_COMPARABILITY_UNVERIFIED"
    change = row["quantity_delta"]
    if change == 0:
        return UNOBSERVED, "NO_QUANTITY_CHANGE_PRICE_ONLY_OR_UNCHANGED"
    if action["kind"] == "SELL_TO_CASH" and change > 0 or action["kind"] == "BUY_WITH_CASH" and change < 0:
        return UNOBSERVED, "QUANTITY_MOVED_AWAY_FROM_TARGET"
    if action["kind"] == "SELL_TO_CASH" and target == ZERO:
        return (REACHED, None) if row["current_quantity"] == ZERO else (PARTIAL, "NONZERO_POSITION_REMAINS")
    current = row["current_amount_cny"]
    if current is None or target is None:
        return PARTIAL, "TARGET_AMOUNT_OR_VALUATION_UNVERIFIED"
    reached = current <= target if action["kind"] == "SELL_TO_CASH" else current >= target
    return (REACHED, None) if reached else (PARTIAL, "TARGET_AMOUNT_REMAINS")


def _checkpoint(checkpoint, path, steps, old, new, positions, debts, cash, evidence, links, start):
    targets = {}
    for target in checkpoint["holdings"]:
        symbol, currency = target.get("symbol"), target.get("currency")
        if symbol and currency and target["account_ref"]:
            key = target["account_ref"], symbol_key(symbol, currency), currency
            targets[key] = targets.get(key, ZERO) + decimal(target["amount_cny"])
    projections = []
    prior_planned_repayment = {}
    stage_path = path[:checkpoint["stage_index"] + 1]
    trade_targets = {event["action_id"]: _trade_key(event["inputs"], old, steps, stage_path)
                     for event in steps if event["kind"] in {"SELL_TO_CASH", "BUY_WITH_CASH"}}
    sides = defaultdict(set)
    for event in steps:
        if event["action_id"] in trade_targets and event["outcome"] == "APPLIED":
            sides[trade_targets[event["action_id"]]].add(event["kind"])
    for event in steps:
        action = event["inputs"]
        action_id = action["action_id"]
        expected = {"action_id": action_id, "kind": action["kind"], "planned_outcome": event["outcome"],
                    "assumption": action["assumption"]}
        if event.get("blocked_reason") or event["outcome"] in {"FAILED", "UNKNOWN", "IN_FLIGHT", "RESTRICTED", "UNSETTLED"}:
            expected["planned_blocker"] = event.get("blocked_reason") or event["cash_state"]
        proof = {"status": "EVIDENCE_PENDING"}
        if action["kind"] in {"SELL_TO_CASH", "BUY_WITH_CASH"}:
            key = trade_targets[action_id]
            row = positions.get(key)
            if row is None and key and old.quantity(key) == new.quantity(key) == ZERO:
                before_at = instant(old.accounts[key[0]].get("positions_as_of"))
                after_at = instant(new.accounts[key[0]].get("positions_as_of"))
                if before_at and after_at and after_at > before_at:
                    row = {"before_quantity":ZERO, "current_quantity":ZERO, "quantity_delta":ZERO,
                           "before_amount_cny":ZERO, "current_amount_cny":ZERO, "attribution":"ZERO_AT_BOTH_SOURCES",
                           "issue":None}
            # A zero holding explicit in the selected stage includes a full
            # sale whose row was removed by D. It never infers a share count.
            target = targets.get(key, ZERO) if key and (key in targets or
                       action["kind"] == "SELL_TO_CASH" and not expected.get("planned_blocker")) else None
            state, issue = _position_progress(action, row, target)
            if len(sides[key]) > 1:
                state, issue = UNKNOWN, "OPPOSING_STAGE_ACTIONS_REQUIRE_SEPARATE_EXECUTION_EVIDENCE"
            current = row.get("current_amount_cny") if row else None
            remaining = (max((current-target if action["kind"] == "SELL_TO_CASH" else target-current), ZERO)
                         if current is not None and target is not None else None)
            identity_conflict = row and row["issue"] in {"SECURITY_IDENTITY_CHANGED", "SECURITY_IDENTITY_UNVERIFIED"}
            if key and not expected.get("planned_blocker") and not identity_conflict:
                proof = execution_for_trade(links, evidence, action_id, key,
                    "SELL" if action["kind"] == "SELL_TO_CASH" else "BUY", decimal(action["amount"]), start, new.captured)
            expected.update(symbol=key[1] if key else action.get("symbol"), account_ref=key[0] if key else None,
                            target_amount_cny=target, remaining_cny=remaining, current=row,
                            comparison_scope="CUMULATIVE_STAGE_POSITION_TARGET")
        elif action["kind"] == "REPAY_DEBT_FROM_CASH":
            ref = action["loan_ref"]
            observation = debts.get(ref, {})
            baseline = decimal(path[0]["loan_balances_cny"].get(ref))
            before_step = baseline - prior_planned_repayment.get(ref, ZERO) if baseline is not None else None
            step = decimal(event["principal_repaid_cny"])
            prior_planned_repayment[ref] = prior_planned_repayment.get(ref, ZERO) + step
            target = before_step-step if before_step is not None and step > 0 else None
            delta = observation.get("principal_delta_cny")
            actual = (observation.get("current") or {}).get("principal_cny")
            if delta is None or target is None:
                state, issue = UNKNOWN, "DEBT_EVIDENCE_PENDING"
            elif actual >= before_step:
                state, issue = UNOBSERVED, "DEBT_REDUCTION_NOT_OBSERVED"
            else:
                state, issue = (REACHED, None) if actual <= target else (PARTIAL, "DEBT_TARGET_REMAINS")
            proof = {"status": "EVIDENCE_PENDING", "issue": "DEBT_PAYMENT_AND_FUNDING_SOURCE_UNVERIFIED"}
            expected.update(loan_ref=ref, target_principal_cny=target, current=observation,
                            remaining_cny=max(actual-target, ZERO) if delta is not None and target is not None else None)
        else:
            origin = _cash_origin(action["cash_ref"], old, steps, stage_path)
            destination = (action["destination_account"], action["destination_currency"])
            # Balance deltas are shown at both ends. Without transfer-owner
            # linkage, neither currency movement proves arrival.
            state, issue = UNKNOWN, "TRANSFER_OR_SETTLEMENT_EVIDENCE_PENDING"
            expected.update(source_cash=cash.get(origin), destination_cash=cash.get(destination),
                            source_account=origin[0] if origin else None, destination_account=destination[0],
                            destination_currency=destination[1])
            proof["issue"] = issue
        if expected.get("planned_blocker"):
            state, issue = UNKNOWN, "SCENARIO_ACTION_WAS_BLOCKED_OR_UNSETTLED"
        projections.append({**expected, "observation_status": state, "comparability_issue": issue,
                            "execution": proof})
    states = {row["observation_status"] for row in projections}
    overall = REACHED if states == {REACHED} else PARTIAL if REACHED in states or PARTIAL in states else (
        UNKNOWN if UNKNOWN in states or not states else UNOBSERVED)
    return {"item_id":checkpoint["item_id"], "stage_label":checkpoint["stage_label"],
            "stage_index":checkpoint["stage_index"], "path":checkpoint["path"], "basis":checkpoint["basis"],
            "observation_status":overall, "actions":projections, "review_due_at":checkpoint["review_due_at"],
            "expected_denominator_cny":checkpoint["investment_assets_cny"], "expected_budget_gaps":checkpoint["budget_gaps"],
            "reconsider_or_revoke_when":checkpoint["reconsider_or_revoke_when"]}


def _risks(record, risk_heads, current, same_policy):
    old_facts = {key: fact for key, fact in record["evidence_archive"].get("risk_facts", {}).items() if fact["hit"]}
    prior = {key: event for key, event in risk_heads.items() if event["state"] not in {"RESOLVED", "EXPIRED"}}
    for key, fact in old_facts.items():
        prior.setdefault(key, {"actual_risk":fact, "policy_bundle_sha256":record["original_policy_reference"]["bundle_sha256"]})
    facts = current["risk_facts"]
    policy_ref = current["policy"]["reference"] or {}
    policy_sha = policy_ref.get("bundle_sha256")
    result = {key: {"status": ("NEW_FORMAL_HIT" if key not in prior else
                              "CONTINUING_HIT" if prior[key]["policy_bundle_sha256"] == policy_sha else
                              "REASSESSED_CURRENT_POLICY_HIT"),
                    "current_fact": fact} for key, fact in facts.items() if fact["hit"]}
    for key, event in prior.items():
        if key in result:
            continue
        bound = same_policy and event["policy_bundle_sha256"] == policy_sha
        fact = (facts.get(key) or clear_absent_risk(current, event)) if bound else None
        result[key] = {"status": "CLEARED_BY_COMPARABLE_EVIDENCE" if fact and not fact["hit"] and bound else "UNRESOLVED",
                       "prior_event_state":event.get("state"), "current_fact":fact}
    return result


def reconcile_portfolio(thesis_store, database, request, contract=None, *, at=None):
    from .contracts import now
    from .portfolio_access import load_portfolio_view, require_portfolio_access, validate_view_origin

    require_portfolio_access("read")
    at = at or now()
    store = PortfolioStore(thesis_store)
    portfolio_id = store.portfolio_id
    with store.connection() as db:
        db.execute("BEGIN")
        record, head = _plan(store, db, request)
        risk_heads = store.risk_heads(db)
    base_ref = record["base_snapshot_ref"]
    archived = record["evidence_archive"]["snapshot"]
    if (archived["portfolio_id"] != portfolio_id or archived["snapshot_id"] != base_ref["id"]
            or digest(archived) != base_ref["sha256"]):
        raise ValueError("E1_PORTFOLIO_SCENARIO_ARCHIVE_MISMATCH")
    validate_view_origin(archived)
    view = load_portfolio_view(database, portfolio_id, request.snapshot_id, contract)
    if digest(view) != request.snapshot_sha256:
        raise ValueError("E1_PORTFOLIO_SNAPSHOT_CHANGED")
    old, new = SnapshotFacts(archived), SnapshotFacts(view, at=at)
    recorded_at = instant(record["recorded_at"])
    if not old.captured or not new.captured or not recorded_at or not max(old.captured, recorded_at) < new.captured <= at:
        raise ValueError("E1_PORTFOLIO_NEWER_SNAPSHOT_REQUIRED")
    current = _current_sources(database, portfolio_id, request, view, contract)
    policy = current["policy"]["reference"]
    same_policy = policy == record["original_policy_reference"]
    expires = [instant(item["proposal"]["expires_at"]) for item in record["decisions"]]
    lifecycle = ("SUPERSEDED" if head["revision"] != record["revision"] else
                 "REVOKED" if record["bundle_state"] == "REVOKED" else
                 "REVIEW_REQUIRED" if not same_policy or any(value and value <= at for value in expires) else
                 "REVIEW_DUE" if instant(record["review_due_at"]) <= at else "CURRENT")
    result = {"schema_version":"wealthpilot.e1.portfolio-reconciliation.v1",
              "decision_bundle_ref":record["decision_bundle_ref"], "decision_revision_ref":record["decision_revision_ref"],
              "decision_state":lifecycle, "plan_basis":record["qualification"]["status"],
              "operative_plan":lifecycle in {"REVIEW_DUE", "CURRENT"} and record["qualification"]["status"] == "QUALIFIED",
              "base_snapshot_ref":base_ref, "latest_snapshot_ref":{"id":request.snapshot_id,"sha256":request.snapshot_sha256},
              "original_policy_reference":record["original_policy_reference"], "current_policy_reference":policy,
              "policy_changed":bool(policy and not same_policy), "review_due_at":record["review_due_at"],
              "selected_dispositions":record["decisions"], "execution_authorized":False}
    if lifecycle in {"SUPERSEDED", "REVOKED"}:
        return {**result, "checkpoints":[], "next_review":"SELECT_CURRENT_DECISION"}
    with localcontext() as context:
        context.prec = 60
        start = max(old.captured, recorded_at)
        planned = [_expected_stage(record, checkpoint) for checkpoint in record["expected_checkpoints"]]
        action_ids = {event["action_id"] for _, steps in planned for event in steps
                      if event["kind"] in {"SELL_TO_CASH", "BUY_WITH_CASH"}}
        if set(request.order_links) - action_ids:
            raise ValueError("E1_PORTFOLIO_ORDER_LINK_OUTSIDE_PLAN")
        from sqlalchemy.exc import SQLAlchemyError
        try:
            evidence = read_execution_evidence(database, portfolio_id, old.captured, new.captured,
                                               request.order_links.values(), contract)
        except (OSError, SQLAlchemyError, ValueError) as error:
            if isinstance(error, ValueError) and str(error) != "HISTORY_QUERY_LIMIT":
                raise
            evidence = {"history":[], "orders":{}, "corporate_accounts":[],
                        "issues":["EXECUTION_OWNER_READ_UNAVAILABLE"]}
        old_listings = record["evidence_archive"]["policy"]["structured_bundle"].get("listings") or {}
        new_listings = current["policy"].get("structured_bundle", {}).get("listings") or {}
        # Issuer owner expects typed listing receipts; fallback is the same
        # built-in verified issuer map when no extended listing is published.
        from backend.services.investment_policy.contracts import parse_snapshot
        if old_listings:
            old_listings = parse_snapshot(record["evidence_archive"]["policy"]["structured_bundle"]).listings
        if new_listings:
            new_listings = parse_snapshot(current["policy"]["structured_bundle"]).listings
        positions = compare_positions(old, new, old_listings, new_listings, evidence["corporate_accounts"])
        cash, debts = compare_cash(old, new), compare_debt(old, new)
        checkpoints = [_checkpoint(checkpoint, path, steps, old, new, positions, debts, cash,
                                   evidence, request.order_links, start)
                       for checkpoint, (path, steps) in zip(record["expected_checkpoints"], planned)]
        allocation, concentration = current["actual_allocation"], current["actual_concentration"]
        denominator = decimal(current["qualification"].get("investable_denominator_cny"))
        for stage in checkpoints:
            expected_denominator = decimal(stage["expected_denominator_cny"])
            stage["current_minus_planned_denominator_cny"] = (
                denominator-expected_denominator if denominator is not None and expected_denominator is not None else None)
            stage["budget_amount_differences_cny"] = {}
            for bucket, expected in (stage["expected_budget_gaps"] or {}).items():
                latest = allocation.get("secondary", {}).get(bucket, {})
                left, right = decimal(expected["amount_cny"]), decimal(latest.get("amount_cny"))
                stage["budget_amount_differences_cny"][bucket] = right-left if left is not None and right is not None else None
        # Evaluation time is not an observed fact. Expiry and the source as-of
        # remain in the projection, so qualification changes change the receipt.
        concentration = {key:value for key,value in concentration.items() if key != "evaluated_at"}
        result.update(
            snapshot_at=new.captured.isoformat(), position_deltas=list(positions.values()),
            issuer_deltas=issuer_value_deltas(positions), cash_deltas=list(cash.values()), debt_deltas=debts,
            checkpoints=checkpoints, evidence_issues=evidence["issues"], current_qualification=current["qualification"],
            current_investable_denominator_cny=current["qualification"].get("investable_denominator_cny"), current_allocation=allocation,
            current_concentration=concentration, risk_reassessment=_risks(record, risk_heads, current, same_policy),
            next_review="REOPEN_E_PREVIEW_FOR_HUMAN_REVIEW" if lifecycle == "REVIEW_REQUIRED" else
                        "REVIEW_REMAINING_CHECKPOINTS_AND_EVIDENCE",
            evidence_requirements=record["evidence_requirements"],
        )
    wire = TypeAdapter(dict).dump_python(result, mode="json")
    return {**wire, "reconciliation_sha256":digest(wire)}

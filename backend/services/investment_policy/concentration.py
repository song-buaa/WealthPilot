"""C issuer and BTC diagnostics on an already frozen Portfolio and ACTIVE policy."""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, localcontext

from backend.services.instruments.classification import CLASSIFICATION_VERSION
from backend.services.instruments.etf_holdings import ETFHoldings
from backend.services.instruments.issuers import IDENTITIES, ISSUERS, VERSION, direct_issuer

from .contracts import PolicyReference, Snapshot, digest
from .parser import PolicyError
from .store import clock

ZERO = Decimal(0)


def _pinned_policy(session, portfolio_id, at):
    raw = session.context
    if raw.get("mode") != "POLICY_ACTIVE" or raw.get("status") != "ACTIVE":
        raise PolicyError("POLICY_CONTEXT_UNAVAILABLE")
    reference = PolicyReference.model_validate(raw["policy_reference"])
    snapshot = Snapshot.model_validate(raw["snapshot"])
    if (session.portfolio_id != portfolio_id or reference.scope != f"portfolio:{portfolio_id}"
            or reference.bundle_version != snapshot.policy_version
            or reference.structured_sha256 != digest(snapshot) or clock(raw["effective_at"]) > at):
        raise PolicyError("CONCENTRATION_POLICY_BINDING_MISMATCH")
    return reference, snapshot


def _denominator(view, projection, at):
    assets = projection.policy_assets
    captured = clock(view["captured_at"])
    evaluated = clock(projection.evaluated_at)
    if (assets.portfolio_id != view["portfolio_id"] or assets.snapshot_id != view["snapshot_id"]
            or assets.as_of != view["captured_at"] or projection.snapshot_sha256 != digest(view)
            or not captured <= evaluated <= at):
        raise ValueError("CONCENTRATION_SNAPSHOT_BINDING_MISMATCH")
    owner = view["data_quality"].get("investable_owner_evidence")
    if (projection.status == "QUALIFIED" and assets.denominator_qualified
            and assets.denominator_cny is not None and assets.denominator_cny > 0
            and evaluated == at
            and at - captured <= timedelta(minutes=5) and owner
            and clock(owner["confirmed_at"]) <= captured <= at <= clock(owner["valid_until"])):
        return assets.denominator_cny
    return None


def _canonical(row):
    return (row.get("classification_verification_status") in {"VERIFIED", "EXPLICIT", "DETERMINISTIC"}
            and isinstance(row.get("classification_source"), str) and bool(row["classification_source"].strip())
            and row.get("classification_version") == CLASSIFICATION_VERSION)


def _btc_identity(ref, row, quality):
    # Same frozen Owner row/fact convention as B3; C never consumes user arguments.
    coverage = quality.get("investable_scope_evidence", {}).get("coverage", {})
    btc = coverage.get("btc", {})
    marker = f"{ref}:BTC_SPOT_UNLEVERED"
    prefix = marker + ":source:"
    refs = btc.get("evidence_refs", [])
    classes = quality.get("investable_owner_evidence", {}).get("manual_classes", {})
    classification = classes.get(ref, {})
    return (ref.startswith("wealth:") and row.get("item_type") == "other_asset"
            and btc.get("status") == "PRESENT" and ref in btc.get("asset_refs", [])
            and ref not in coverage.get("gold", {}).get("asset_refs", [])
            and classification.get("economic_asset_class") == "ALTERNATIVE"
            and bool(classification.get("evidence_refs")) and marker in refs
            and not any(f"{ref}:{fact}" in coverage.get("accounts", {}).get("evidence_refs", [])
                        for fact in ("US_EQUITY_ETF", "US_DIRECT_EQUITY", "CN_DIRECT_EQUITY"))
            and any(r.startswith(prefix) and r[len(prefix):].strip()
                    and not r.endswith((":GOLD_EXPOSURE", ":BTC_SPOT_UNLEVERED", ":US_EQUITY_ETF",
                                        ":US_DIRECT_EQUITY", ":CN_DIRECT_EQUITY")) for r in refs))


def _issuer_rules(snapshot):
    rules = [r for r in snapshot.rules if r.metric == "issuer_weight"
             and r.scope == "ALL_INVESTABLE_ASSETS_LOOKTHROUGH" and r.operator in {"gt", "gte"}]
    review = next((r for r in rules if r.rule_id == "issuer.review" and not r.subject
                   and r.action == "HUMAN_REVIEW" and r.operator == "gte" and r.threshold > 0), None)
    if review is None:
        raise PolicyError("CONCENTRATION_REVIEW_RULE_UNAVAILABLE")
    exceptions = {}
    for rule in rules:
        if rule.subject:
            issuer = IDENTITIES.get((rule.subject + ":US", "USD"))
            if issuer is None:
                raise PolicyError("CONCENTRATION_POLICY_SUBJECT_UNMAPPED")
            exceptions.setdefault(issuer, []).append(rule)
    return rules, exceptions, review


def _risk(rules, lower, denominator, complete):
    if denominator is None:
        return "CONDITIONAL_RISK", []
    matches = []
    for rule in rules:
        boundary = denominator * rule.threshold
        if lower > boundary or (lower == boundary and rule.operator == "gte"):
            matches.append({"rule_id": rule.rule_id, "action": rule.action, "operator": rule.operator,
                            "threshold": rule.threshold, "source_clause": rule.source_clause.model_dump()})
    if any(hit["action"] == "HUMAN_REVIEW" for hit in matches):
        return "HUMAN_REVIEW", matches
    if matches:
        return "WARNING", matches
    return ("SAFE_BOUNDED" if complete else "UNKNOWN"), []


def _percent(amount, denominator):
    return amount * 100 / denominator if denominator is not None else None


def _critical(amount, denominator, threshold, complete):
    if denominator is None or amount <= 0:
        return None
    critical = amount / threshold
    return {
        "i_critical_cny": critical,
        "r_trigger_cny": max(ZERO, denominator - critical),
        "already_triggered": amount >= denominator * threshold,
        "exposure_basis": "EXACT" if complete else "KNOWN_LOWER_BOUND_ONLY",
        "assumptions": ("ISSUER_EXPOSURE_FIXED", "PRICES_FX_AND_ETF_WEIGHTS_FIXED",
                        "NO_CHANGE_IN_FEES", "REAL_NET_DEBT_REPAYMENT_REDUCES_I_ONE_FOR_ONE"),
        "execution_authorized": False,
    }


def analyze_concentration(view, *, projection, session, at, etf_evidence: dict[str, ETFHoldings],
                          evidence_pins: dict[str, str], classified_assets=None):
    """C/D internal contract. Pass only the original U1/A-min result and pinned PolicySession.

    Evidence pins come from the source owner independently of the supplied records.
    Future integration must verify that the pinned session remains current before publishing.
    Values are CNY Decimal and percentages are in points, e.g. Decimal('8') is 8%.
    """
    if at.utcoffset() is None:
        raise ValueError("CONCENTRATION_AWARE_CLOCK_REQUIRED")
    with localcontext() as ctx:
        ctx.prec = 60
        return _analyze(view, projection, session, at, etf_evidence, evidence_pins, classified_assets)


def _analyze(view, projection, session, at, etf_evidence, evidence_pins, classified_assets):
    reference, snapshot = _pinned_policy(session, view["portfolio_id"], at)
    denominator = _denominator(view, projection, at)
    issuer_rules, exceptions, review = _issuer_rules(snapshot)
    btc_rules = [r for r in snapshot.rules if r.metric == "btc_weight" and not r.subject
                 and r.scope == "ALL_INVESTABLE_ASSETS_ECONOMIC" and r.operator in {"gt", "gte"}]
    if not btc_rules:
        raise PolicyError("CONCENTRATION_BTC_RULES_UNAVAILABLE")

    quality = view["data_quality"]
    positions = quality.get("investable_security_inputs", quality.get("security_inputs", []))
    rows = {f"position:{r['id']}": r for r in positions}
    rows.update({f"wealth:{r['id']}": r for r in view["views"]["get_manual_wealth_items"]["items"]
                 if str(r.get("kind", "")).lower() == "asset"})
    refs = [item.source_ref for item in projection.assets]
    if len(refs) != len(set(refs)) or set(refs) != set(rows):
        raise ValueError("CONCENTRATION_INVENTORY_BINDING_MISMATCH")
    direct, indirect = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    members, indirect_members, etfs = defaultdict(list), defaultdict(list), {}
    coverage_issues = set()
    unknown_cny = ZERO
    btc_amount = ZERO
    btc_coverage = quality.get("investable_scope_evidence", {}).get("coverage", {}).get("btc", {})
    btc_complete = (denominator is not None and btc_coverage.get("status") in {"ABSENT", "PRESENT"})
    principal = ZERO
    for item in projection.assets:
        ref = item.source_ref
        if item.state != "INCLUDED":
            if item.state == "UNKNOWN":
                coverage_issues.add("ASSET_SCOPE_UNVERIFIED")
            continue
        amount, row = item.amount_cny, rows[ref]
        if amount is None or not amount.is_finite() or amount < 0:
            raise ValueError("CONCENTRATION_INVALID_AMOUNT")
        principal += amount
        if not amount:
            continue
        if classified_assets is not None:
            if ref not in classified_assets:
                coverage_issues.add("B3_CLASSIFICATION_UNVERIFIED")
                unknown_cny += amount
                if ref in btc_coverage.get("asset_refs", []):
                    btc_complete = False
                continue
            if Decimal(classified_assets[ref]["amount_cny"]) != amount:
                raise ValueError("CONCENTRATION_CLASSIFICATION_AMOUNT_MISMATCH")
        if _btc_identity(ref, row, quality):
            btc_amount += amount
            continue
        if ref in btc_coverage.get("asset_refs", []):
            btc_complete = False
        position = ref.startswith("position:")
        verified = position and _canonical(row)
        if ((verified and row.get("vehicle_type") == "CASH" and row.get("economic_asset_class") == "CASH")
                or (not position and row.get("item_type") == "bank_cash")):
            continue
        issuer = direct_issuer(row) if verified else None
        if issuer:
            direct[issuer] += amount
            members[issuer].append({"source_ref": ref, "symbol": row.get("symbol"),
                                    "security_symbol": row.get("security_symbol") or row.get("symbol"),
                                    "currency": row.get("original_currency") or row.get("currency"),
                                    "account_ref": row.get("account_ref"), "amount_cny": amount})
            continue
        if (verified and row.get("vehicle_type") == "ETF" and row.get("economic_asset_class") == "EQUITY"
                and (row.get("original_currency") or row.get("currency")) == "USD"):
            symbol = row.get("symbol")
            evidence = etf_evidence.get(symbol)
            if row.get("security_symbol") not in (None, symbol) or row.get("security_currency") not in (None, "USD", "CNY"):
                evidence = None
            status = (evidence.status(snapshot_at=clock(view["captured_at"]), at=at,
                                      expected_sha256=evidence_pins.get(symbol),
                                      valuation_as_of=row.get("price_as_of"),
                                      valuation_basis=row.get("price_time_basis"))
                      if evidence is not None and evidence.symbol == symbol else "UNAVAILABLE")
            resolved = ZERO
            if status == "FRESH":
                for constituent in evidence.rows:
                    underlying = direct_issuer({"symbol": constituent.symbol, "currency": constituent.currency,
                                               "economic_asset_class": "EQUITY", "vehicle_type": "COMMON_STOCK"})
                    if underlying:
                        resolved += constituent.weight_lower_bound
                        contribution = amount * constituent.weight_lower_bound
                        indirect[underlying] += contribution
                        indirect_members[underlying].append({
                            "etf_source_ref": ref, "security_symbol": constituent.symbol,
                            "currency": constituent.currency, "weight_lower_bound": constituent.weight_lower_bound,
                            "known_cny": contribution,
                        })
            etfs[ref] = {"symbol": symbol, "amount_cny": amount, "freshness_status": status,
                         "valuation_as_of": row.get("price_as_of"), "resolved_weight": resolved,
                         "evidence_coverage": evidence.coverage if evidence else None,
                         "evidence_sha256": digest(evidence) if evidence else None,
                         "holdings_as_of": evidence.holdings_as_of if evidence else None,
                         "known_at": evidence.known_at if evidence else None,
                         "valid_until": evidence.valid_until if evidence else None,
                         "source": evidence.source if evidence else None,
                         "source_sha256": evidence.source_sha256 if evidence else None,
                         "parser_version": evidence.parser_version if evidence else None,
                         "method_version": evidence.method_version if evidence else None,
                         "evidence_scope": evidence.scope if evidence else None}
            if resolved < 1 or evidence.scope != "COMPLETE_UNLEVERED":
                coverage_issues.add("ETF_LOOKTHROUGH_INCOMPLETE")
                unknown_cny += amount * (1 - resolved)
            continue
        coverage_issues.add("SECURITY_IDENTITY_OR_SCOPE_UNVERIFIED")
        unknown_cny += amount

    if denominator is not None and principal != denominator:
        raise ValueError("CONCENTRATION_DENOMINATOR_NOT_CLOSED")
    complete = denominator is not None and not coverage_issues
    issuer_rows = {}
    for issuer in sorted(direct.keys() | indirect.keys()):
        applicable = [r for r in issuer_rules if not r.subject
                      and not (issuer in exceptions and r.rule_id == "issuer.warning.default")]
        applicable += exceptions.get(issuer, [])
        lower = direct[issuer] + indirect[issuer]
        risk, hits = _risk(applicable, lower, denominator, complete)
        issuer_rows[issuer] = {
            "issuer_name": ISSUERS[issuer][0],
            "direct_cny": direct[issuer], "direct_pct": _percent(direct[issuer], denominator),
            "direct_members": members[issuer], "indirect_members": indirect_members[issuer],
            "indirect_known_cny": indirect[issuer],
            "indirect_known_pct": _percent(indirect[issuer], denominator),
            "known_lower_bound_cny": lower, "known_lower_bound_pct": _percent(lower, denominator),
            "proven_upper_bound_pct": _percent(lower, denominator) if complete else None,
            "risk_status": risk, "policy_hits": hits, "unknown_coverage": not complete,
            "repayment_threshold": _critical(lower, denominator, review.threshold, complete),
        }
    btc_risk, btc_hits = _risk(btc_rules, btc_amount, denominator, btc_complete)
    return {
        "snapshot_id": view["snapshot_id"], "snapshot_sha256": projection.snapshot_sha256,
        "snapshot_at": view["captured_at"], "evaluated_at": at.isoformat(),
        "policy_reference": reference.model_dump(), "issuer_mapping_version": VERSION,
        "classification_sha256": digest(classified_assets) if classified_assets is not None else None,
        "denominator_cny": denominator, "status": "CONDITIONAL_RISK" if denominator is None else "DIAGNOSTIC",
        "issues": sorted(coverage_issues | ({"DENOMINATOR_UNVERIFIED", *projection.policy_assets.issues}
                                            if denominator is None else set())),
        "unknown_coverage": not complete, "unresolved_principal_cny": unknown_cny if denominator is not None else None,
        "issuers": issuer_rows, "etfs": etfs,
        "btc": {"known_spot_cny": btc_amount, "known_lower_bound_pct": _percent(btc_amount, denominator),
                "risk_status": btc_risk, "policy_hits": btc_hits, "unknown_coverage": not btc_complete},
        "execution_authorized": False,
    }

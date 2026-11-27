"""
资产配置模块 — 服务层

编排数据库查询 → 计算引擎 → 返回结果，不含 AI 逻辑。
"""

from __future__ import annotations

from typing import List, Optional

from app.allocation.calculator import (
    allocate_increment,
    build_allocation_snapshot,
    build_deviation_snapshot,
    calc_initial_allocation,
)
from app.allocation.classifier import classify_position
from app.allocation.defaults import get_default_targets
from app.allocation.discipline import auto_correct_violations, check_discipline
from app.allocation.types import (
    AllocAssetClass,
    AllocationResult,
    AllocationSnapshot,
    AssetTarget,
    CN_TO_ALLOC,
    DeviationSnapshot,
    DisciplineCheckResult,
)
from app.database import get_session
from app.models import Position


def get_snapshot(portfolio_id: int, *, snapshot_id=None) -> AllocationSnapshot:
    """获取当前配置快照"""
    from backend.services.investment_policy.runtime import current, bind
    pinned = current(portfolio_id)
    if pinned.mode != 'LEGACY':
        if snapshot_id and pinned.assets and pinned.assets.snapshot_id != snapshot_id:
            from backend.services.investment_policy.parser import PolicyError
            raise PolicyError('POLICY_SCOPE_MISMATCH')
        from backend.services.investment_policy.consumers import snapshot
        with bind(pinned):
            if pinned.mode == 'POLICY_ACTIVE' and pinned.assets is None:
                with _assembled_policy_assets(portfolio_id, snapshot_id):
                    return snapshot(portfolio_id)
            return snapshot(portfolio_id)
    with get_session() as session:
        positions = (
            session.query(Position)
            .filter(Position.portfolio_id == portfolio_id)
            .all()
        )
        return build_allocation_snapshot(positions, segment="投资")


def _assembled_policy_assets(portfolio_id, snapshot_id=None, *, frozen_view=None, evaluation_at=None,
                             etf_inputs=None, strategic=True):
    """Server-owned read only: pin policy, original stored U1 bytes and A-min.

    Never call prepare, live Portfolio, FX providers or broker synchronization.
    API arguments select an existing ID; they cannot provide assets/evidence.
    """
    from contextlib import contextmanager
    from decimal import Decimal
    from datetime import timedelta
    import os
    import sqlite3
    from app import asset_views
    from app.database import DB_PATH
    from backend.services.investable_assets import project_investable_assets
    from backend.services.investment_policy.contracts import digest, MetricEvidence, parse_snapshot
    from backend.services.investment_policy.allocation_hierarchy import build_hierarchy, formal_strategic_review
    from backend.services.investment_policy.concentration_adapter import allocation_concentration
    from backend.services.investment_policy.runtime import current, bind, assets_qualified
    from backend.services.investment_policy.parser import PolicyError
    from backend.services.investment_policy.store import clock, utc_now

    @contextmanager
    def assembled():
        pinned = current(portfolio_id)
        pinned.ensure_current()
        try:
            contract = os.environ.get('WEALTHPILOT_SQLITE_CONTRACT')
            view = frozen_view if frozen_view is not None else (
                asset_views.load_view(DB_PATH, portfolio_id, snapshot_id, contract) if snapshot_id
                else asset_views.latest_view(DB_PATH, portfolio_id, contract))
        except (OSError, ValueError) as exc:
            if (isinstance(exc, ValueError) and str(exc) not in asset_views.ERRORS
                    and not str(exc).startswith('SHARED_')):
                raise
            raise PolicyError('POLICY_FULL_INVESTABLE_DENOMINATOR_UNVERIFIED') from None
        except sqlite3.OperationalError as exc:
            if (getattr(exc, 'sqlite_errorcode', None) not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED,
                    sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_READONLY}
                    and str(exc) not in {'no such table: wealth_snapshots', 'no such column: payload_json',
                        'no such column: analysis_request_id', 'no such column: portfolio_id'}):
                raise
            raise PolicyError('POLICY_FULL_INVESTABLE_DENOMINATOR_UNVERIFIED') from None
        def reject(code, issues):
            error = PolicyError(code)
            error.qualification = {'status':'UNKNOWN', 'snapshot_id':view['snapshot_id'],
                'snapshot_sha256':digest(view), 'issues':issues, 'execution_authorized':False}
            if (frozen_view is None and os.environ.get("WEALTHPILOT_ETF_RELEASE_DIRECTORY")
                    and timedelta(0) <= clock(utc_now()) - clock(view["captured_at"]) < timedelta(minutes=5)):
                research = _conditional_concentration(portfolio_id, view, pinned, code)
                if "etf_observation" in research:
                    error.qualification["concentration"] = research
            raise error
        evaluated_at = evaluation_at or clock(utc_now())
        owner = view['data_quality'].get('investable_owner_evidence', {})
        owner_issue = ('INVESTABLE_OWNER_EVIDENCE_MISSING' if not owner else
                       'INVESTABLE_OWNER_EVIDENCE_EXPIRED' if clock(owner['valid_until']) < evaluated_at else None)
        if owner_issue:
            reject('POLICY_FULL_INVESTABLE_DENOMINATOR_UNVERIFIED',
                ['INVESTABLE_OWNER_EVIDENCE_UNAVAILABLE_OR_EXPIRED', owner_issue,
                 *[i for i in view['data_quality'].get('issues', []) if i.startswith('INVESTABLE_')]])
        projection = project_investable_assets(view, portfolio_id=portfolio_id,
            snapshot_id=view['snapshot_id'], snapshot_sha256=digest(view), at=evaluated_at, strategic=strategic)
        # A new request-local session; never mutate a caller's pinned assets.
        from backend.services.investment_policy.runtime import PolicySession
        session = PolicySession(portfolio_id, assets=projection.policy_assets)
        if session.context.get('policy_reference') != pinned.context.get('policy_reference'):
            raise PolicyError('POLICY_VERSION_CONFLICT')
        if projection.valid_until is None:
            reject('POLICY_FULL_INVESTABLE_DENOMINATOR_UNVERIFIED', projection.policy_assets.issues)
        session.allocation_source = {'snapshot_id':view['snapshot_id'], 'snapshot_sha256':digest(view),
            'as_of':view['captured_at'], 'evaluated_at':projection.evaluated_at,
            'expires_at':min(projection.valid_until, clock(owner['valid_until'])).isoformat(),
            'owner_evidence_sha256':view['data_quality'].get('investable_owner_evidence', {}).get('sha256'),
            'issues':projection.policy_assets.issues}
        if not assets_qualified(session, complete=True):
            reject('POLICY_FULL_INVESTABLE_DENOMINATOR_UNVERIFIED', projection.policy_assets.issues)
        amounts = {k:Decimal(0) for k in ('cash', 'fixed', 'equity', 'alt', 'deriv')}
        # Canonical economic class only, never broker STK or a display name.
        from backend.services.instruments.classification import ECONOMIC_CLASS_TO_CN, CLASSIFICATION_VERSION
        from app.allocation.types import CN_TO_ALLOC
        classes = {str(k):CN_TO_ALLOC[v].value for k,v in ECONOMIC_CLASS_TO_CN.items() if v in CN_TO_ALLOC}
        q = view['data_quality']
        rows = {f'position:{r["id"]}':r for r in q.get('investable_security_inputs', q['security_inputs'])}
        rows.update({f'wealth:{r["id"]}':r for r in view['views']['get_manual_wealth_items']['items']})
        manual_classes = view['data_quality'].get('investable_owner_evidence', {}).get('manual_classes', {})
        included = []
        for item in projection.assets:
            if item.state != 'INCLUDED':
                continue
            row = rows[item.source_ref]
            if item.source_ref.startswith('position:'):
                verified = (row.get('classification_verification_status') in {'VERIFIED','EXPLICIT','DETERMINISTIC'}
                    and isinstance(row.get('classification_source'), str) and row['classification_source'].strip()
                    and row.get('classification_version') == CLASSIFICATION_VERSION)
                economic = row.get('economic_asset_class') if verified else None
                classification_refs = [row['classification_source']] if verified else []
            elif row.get('item_type') == 'bank_cash':
                economic, classification_refs = 'CASH', ['wealth:bank_cash']
            else:
                evidence = manual_classes.get(item.source_ref, {})
                classification_refs = evidence.get('evidence_refs') or []
                economic = evidence.get('economic_asset_class') if classification_refs else None
            key = classes.get(economic)
            if not key:
                reject('POLICY_BUCKET_CLASSIFICATION_UNVERIFIED', [f'{item.source_ref}:CLASSIFICATION_UNVERIFIED'])
            amounts[key] += item.amount_cny
            included.append((item, row, economic, classification_refs))
        denominator = projection.policy_assets.denominator_cny
        if sum(amounts.values()) != denominator:
            raise PolicyError('POLICY_ASSET_WEIGHTS_NOT_CLOSED')
        weights = {k:v/denominator for k,v in amounts.items()}
        # Decimal division can repeat. Assign only division residue to the
        # largest class; source CNY amounts remain exact, no threshold relaxed.
        largest = max(amounts, key=amounts.get)
        weights[largest] += Decimal(1)-sum(weights.values())
        session.assets = session.assets.model_copy(update={'major_class_weights': {
            k:MetricEvidence(value=v, complete=True, fresh=True, identity_verified=True,
                denominator_qualified=True, as_of=view['captured_at']) for k,v in weights.items()}})
        session.allocation_amounts = amounts
        context = session.consumer("allocation")
        policy_snapshot = parse_snapshot(context["snapshot"])
        session.allocation_hierarchy = build_hierarchy(policy_snapshot,
            denominator=denominator, included=included, coverage=q['investable_scope_evidence']['coverage'])
        session.allocation_hierarchy["strategic_review"] = formal_strategic_review(
            policy_snapshot, session.allocation_hierarchy, policy_context=context,
            portfolio_id=portfolio_id, snapshot_sha256=projection.snapshot_sha256, denominator=denominator)
        session.allocation_concentration = allocation_concentration(view, projection=projection, session=session,
            at=evaluated_at, classified_assets=session.allocation_hierarchy['classified_assets'], etf_inputs=etf_inputs)
        with bind(session):
            session.ensure_current()
            yield session
            session.ensure_current()
    return assembled()


def read_frozen_concentration(database, portfolio_id, snapshot_id, contract=None, *, expected_policy=None):
    from app.asset_views import load_view
    from backend.services.investment_policy.runtime import bind, current

    session = current(portfolio_id)
    with bind(session):
        if expected_policy is not None and session.context.get("policy_reference") != expected_policy:
            return {"status": "UNKNOWN", "issues": ["POLICY_VERSION_CONFLICT"], "execution_authorized": False}
        view = load_view(database, portfolio_id, snapshot_id, contract)
        return project_frozen_concentration(portfolio_id, view)


def project_frozen_concentration(portfolio_id, view):
    """Original Allocation qualification for an explicitly selected server-owned U1 view."""
    from backend.services.investment_policy.parser import PolicyError
    from backend.services.investment_policy.runtime import current

    session = current(portfolio_id)
    session.ensure_current()
    if session.mode != 'POLICY_ACTIVE':
        return {"status": "UNKNOWN", "issues": ["POLICY_CONTEXT_UNAVAILABLE"], "execution_authorized": False}
    try:
        with _assembled_policy_assets(portfolio_id, view["snapshot_id"], frozen_view=view) as assembled:
            return assembled.allocation_concentration
    except PolicyError as error:
        reason = str(error)
        if reason not in {"POLICY_FULL_INVESTABLE_DENOMINATOR_UNVERIFIED", "POLICY_BUCKET_CLASSIFICATION_UNVERIFIED"}:
            raise
    return _conditional_concentration(portfolio_id, view, session, reason)


def _conditional_concentration(portfolio_id, view, session, reason, *, at=None, etf_inputs=None):
    from backend.services.investable_assets import project_investable_assets
    from backend.services.investment_policy.concentration_adapter import allocation_concentration
    from backend.services.investment_policy.contracts import digest
    from backend.services.investment_policy.store import clock, utc_now

    at = at or clock(utc_now())
    projection = project_investable_assets(view, portfolio_id=portfolio_id, snapshot_id=view["snapshot_id"],
                                            snapshot_sha256=digest(view), at=at)
    # A failed Allocation qualification cannot be promoted by the research view.
    projection = projection.model_copy(update={"status": "UNKNOWN"})
    result = allocation_concentration(view, projection=projection, session=session,
                                      at=at, classified_assets=None, etf_inputs=etf_inputs)
    result["issues"] = sorted(set(result["issues"]) | {reason, "DENOMINATOR_UNVERIFIED"})
    result["assumptions"] = ["EXISTING_FROZEN_SOURCE_ONLY", "NO_QUALIFIED_DENOMINATOR",
                             "OBSERVED_AMOUNTS_DO_NOT_PROVE_PORTFOLIO_RISK"]
    session.ensure_current()
    return result


def get_targets() -> List[AssetTarget]:
    """
    获取目标区间。
    V1: 返回固定的默认目标（稳健偏进取）。
    后续迭代时可根据用户画像动态生成。
    """
    from backend.services.investment_policy.runtime import current
    if current().mode != 'LEGACY':
        from backend.services.investment_policy.consumers import targets
        return targets()
    return get_default_targets()


def get_deviation(portfolio_id: int) -> DeviationSnapshot:
    """获取偏离度快照"""
    from backend.services.investment_policy.runtime import current
    if current(portfolio_id).mode != 'LEGACY':
        from backend.services.investment_policy.consumers import deviation
        return deviation(portfolio_id)
    snapshot = get_snapshot(portfolio_id)
    targets = get_targets()
    return build_deviation_snapshot(snapshot, targets)


def compute_increment_plan(
    portfolio_id: int,
    increment_amount: float,
    user_requested_deriv: bool = False,
) -> AllocationResult:
    """
    计算增量分配方案。
    含纪律校验 + block 级自动修正。
    """
    from backend.services.investment_policy.runtime import current
    if current(portfolio_id).mode != 'LEGACY':
        from backend.services.investment_policy.consumers import conditional_plan
        return conditional_plan(increment_amount,portfolio_id)
    snapshot = get_snapshot(portfolio_id)
    targets = get_targets()

    result = allocate_increment(
        new_money=increment_amount,
        current=snapshot,
        targets=targets,
        user_requested_deriv=user_requested_deriv,
    )

    # 纪律校验
    check = check_discipline(result.allocations, snapshot, targets)

    if not check.passed:
        # block 级违规自动修正
        corrected = auto_correct_violations(result.allocations, snapshot, targets)
        # 用修正后的分配重建 plan_items
        from app.allocation.calculator import round_allocation, _build_result
        result = _build_result(corrected, increment_amount, snapshot, targets)
        # 重新校验
        check = check_discipline(result.allocations, snapshot, targets)

    result.discipline_check = check
    return result


def compute_initial_plan(
    total_amount: float,
) -> AllocationResult:
    """
    计算初始配置方案（从零开始）。
    货币类默认只分配到下限。
    """
    from backend.services.investment_policy.runtime import current
    if current().mode != 'LEGACY':
        from backend.services.investment_policy.consumers import conditional_plan
        return conditional_plan(total_amount)
    targets = get_targets()
    result = calc_initial_allocation(total_amount, targets)

    # 构建空快照用于纪律校验
    empty_snapshot = AllocationSnapshot(total_investable_assets=0)
    check = check_discipline(result.allocations, empty_snapshot, targets)
    result.discipline_check = check
    return result


def run_discipline_check(
    portfolio_id: int,
    proposed_allocations: dict,
) -> DisciplineCheckResult:
    """对任意拟议分配执行纪律校验"""
    from backend.services.investment_policy.runtime import current, diagnose
    if current(portfolio_id).mode != 'LEGACY':
        diagnose(portfolio_id)
        return DisciplineCheckResult(passed=False, violations=[])
    snapshot = get_snapshot(portfolio_id)
    targets = get_targets()
    return check_discipline(proposed_allocations, snapshot, targets)


def classify_asset(
    holding_id: int,
    asset_class: str,
) -> bool:
    """
    手动归类：更新持仓的 asset_class 字段。
    asset_class 传入英文值（如 'equity'），转换为中文存储。
    """
    from app.allocation.types import ALLOC_TO_CN
    from backend.services.instruments.classification import (
        AssetClassificationEvidence,
        business_position_classification_fields,
        classify_instrument,
    )
    cn_class = ALLOC_TO_CN.get(AllocAssetClass(asset_class))
    if not cn_class:
        return False

    with get_session() as session:
        pos = session.query(Position).filter(Position.id == holding_id).first()
        if not pos:
            return False
        evidence = AssetClassificationEvidence(
            broker="manual_ui",
            broker_security_type=getattr(pos, "broker_security_type", None),
            vehicle_type_hint=getattr(pos, "vehicle_type", None),
            explicit_economic_asset_class=cn_class,
            explicit_source="USER_EXPLICIT_UI",
            long_name=pos.name,
            currency=pos.currency,
        )
        fields = business_position_classification_fields(
            classify_instrument(evidence), evidence=evidence
        )
        for field, value in fields.items():
            setattr(pos, field, value)
        session.commit()
        return True


def get_unclassified_holdings(portfolio_id: int) -> list:
    """获取未分类持仓列表"""
    from backend.services.instruments.classification import economic_asset_class_cn

    with get_session() as session:
        positions = (
            session.query(Position)
            .filter(Position.portfolio_id == portfolio_id)
            .all()
        )
        unclassified = []
        for pos in positions:
            if getattr(pos, "segment", "投资") != "投资":
                continue
            canonical_label = economic_asset_class_cn(pos)
            ac = classify_position(canonical_label, pos.name)
            if ac == AllocAssetClass.UNCLASSIFIED:
                unclassified.append({
                    "id": pos.id,
                    "name": pos.name,
                    "ticker": pos.ticker,
                    "platform": pos.platform,
                    "asset_class": canonical_label,
                    "market_value_cny": pos.market_value_cny,
                })
        return unclassified


def simulate_exit_scenario(asset_view, legs, *, amount_qualified=False, target=None):
    """4.2 static scenario on existing U1 inputs; never persists positions or orders."""
    from backend.services.allocation_scenarios import simulate
    return simulate(asset_view,legs,amount_qualified=amount_qualified,target=target)


def read_frozen_transition(database, portfolio_id, request, contract=None):
    """Common D entry point for explicit Web and MCP/PEER simulation requests."""
    from app.asset_views import load_view
    from backend.services.investment_policy.runtime import current

    session = current(portfolio_id)
    view = load_view(database, portfolio_id, request.snapshot_id, contract)
    return project_frozen_transition(portfolio_id, view, request, session=session)


def project_frozen_transition(portfolio_id, view, request, *, session, at=None, freeze_evidence=False, etf_inputs=None):
    from datetime import timedelta
    from decimal import Decimal, localcontext
    from pydantic import TypeAdapter, ValidationError

    from backend.services.allocation_scenarios import CashLot, ScenarioHolding, TransitionRejected, simulate_transition
    from backend.services.instruments.issuers import IDENTITIES, ISSUERS
    from backend.services.execution_batch.trusted_instruments import TRUSTED_INSTRUMENTS
    from backend.services.investable_assets import ScopeEvidence, _amount, _manual_date_current, _source_current, project_investable_assets
    from backend.services.investment_policy.allocation_hierarchy import US_EQUITY_ETFS, _identity, build_hierarchy, formal_strategic_review
    from backend.services.investment_policy.concentration import analyze_scenario_concentration, _canonical, _pinned_policy
    from backend.services.investment_policy.concentration_adapter import _read_etf_evidence
    from backend.services.investment_policy.contracts import digest
    from backend.services.investment_policy.parser import PolicyError
    from backend.services.investment_policy.store import clock, utc_now

    at = at or clock(utc_now())
    session.ensure_current()
    reference, policy = _pinned_policy(session, portfolio_id, at)
    if request.policy_bundle_sha256 != reference.bundle_sha256:
        raise PolicyError("POLICY_VERSION_CONFLICT")
    if request.snapshot_id != view["snapshot_id"] or request.snapshot_sha256 != digest(view):
        raise TransitionRejected("SCENARIO_SNAPSHOT_BINDING_MISMATCH")
    projection = project_investable_assets(view, portfolio_id=portfolio_id, snapshot_id=request.snapshot_id,
                                            snapshot_sha256=request.snapshot_sha256, at=at)
    quality = view["data_quality"]
    owner = quality.get("investable_owner_evidence")
    denominator = projection.policy_assets.denominator_cny
    if (not owner or not clock(owner['confirmed_at']) <= clock(view["captured_at"]) <= at
            <= min(clock(owner['valid_until']), clock(view["captured_at"]) + timedelta(minutes=5))):
        denominator = None
    if denominator is not None and (request.assumed_amounts_cny or request.assumed_loan_principal_cny
                                    or request.assumed_fx_to_cny):
        raise TransitionRejected("SCENARIO_ASSUMPTIONS_REQUIRE_UNQUALIFIED_BASELINE")

    positions = {f"position:{row['id']}": row for row in
                 quality.get("investable_security_inputs", quality.get("security_inputs", []))}
    manual = {f"wealth:{row['id']}": row for row in view["views"]["get_manual_wealth_items"]["items"]}
    account_rows = {row["account_ref"]: row for row in quality.get("investable_accounts", quality.get("accounts", []))}
    accounts = {ref for ref, account in account_rows.items()
                if account.get("publication_status") == "matched" and account.get("last_attempt_status") == "success"
                and account.get("complete") is True and not account.get("fill_sync_pending")
                and _source_current(account.get("account_synced_at"), at)}
    if denominator is None:
        # Historic or incomplete accounts can be used only as named scenario assumptions.
        accounts = set(account_rows)
    coverage = {domain: {"evidence_refs": [], "asset_refs": []} for domain in ("accounts", "gold", "btc")}
    try:
        scope = ScopeEvidence.model_validate(quality.get("investable_scope_evidence"))
    except ValidationError:
        scope = None
    if scope is not None:
        coverage.update(scope.model_dump(mode="json")["coverage"])
    rates = {"CNY": Decimal(1)}
    fx_candidates = {}
    for item in projection.assets:
        row = positions.get(item.source_ref)
        if item.state != "INCLUDED" or not row:
            continue
        currency = row.get("original_currency") or row.get("currency")
        rate = _amount(row.get("fx_rate_to_cny"))
        if currency and currency != "CNY" and rate:
            fx_candidates.setdefault(currency, set()).add(rate)
    rates.update({currency: next(iter(values)) for currency, values in fx_candidates.items() if len(values) == 1})
    for currency, assumption in request.assumed_fx_to_cny.items():
        if currency in rates and rates[currency] != assumption:
            raise TransitionRejected("SCENARIO_ASSUMED_FX_CONFLICT")
        rates[currency] = assumption

    holdings, cash, cash_sources, loans = {}, {}, {}, {}
    manual_classes = (owner or {}).get("manual_classes", {})
    def currency_unverified_reserve(row, evaluated_at):
        return (row.get("currency") != "CNY" or row.get("quality_issues")
                or not row.get("effective_included_in_net_worth", row.get("included_in_net_worth"))
                or not _manual_date_current(row.get("value_as_of"), evaluated_at)
                or not _manual_date_current(row.get("last_verified_at"), evaluated_at))

    assumed_refs = set(request.assumed_amounts_cny)
    denomination_current = timedelta(0) <= at - clock(view["captured_at"]) <= timedelta(minutes=5)
    with localcontext() as arithmetic:
        arithmetic.prec = 60
        for item in projection.assets:
            ref = item.source_ref
            assumed = ref in assumed_refs
            if assumed and (item.state != "UNKNOWN" or ref not in positions):
                raise TransitionRejected("SCENARIO_ASSUMED_ASSET_NOT_UNCLASSIFIED_POSITION")
            assumed_refs.discard(ref)
            row = positions.get(ref) or manual[ref]
            included = item.state == "INCLUDED" or assumed
            is_manual_cash = ref in manual and row.get("item_type") == "bank_cash"
            economic = row.get("economic_asset_class") if ref in positions and _canonical(row) else None
            evidence = [row["classification_source"]] if economic else []
            if ref in manual:
                attested = manual_classes.get(ref, {})
                economic = "CASH" if is_manual_cash else attested.get("economic_asset_class")
                evidence = attested.get("evidence_refs", [])
            reserve = (item.state == "EXCLUDED" and item.reason == "DEBT_RESERVE"
                       and (is_manual_cash or economic == "CASH"))
            if reserve:
                reserve = (not currency_unverified_reserve(row, at) if is_manual_cash else
                           row.get("currency") == "CNY" and row.get("account_ref") in accounts
                           and _source_current(row.get("cash_as_of"), at) and denomination_current)
            if not included and not reserve:
                continue
            amount = request.assumed_amounts_cny[ref] if assumed else item.amount_cny
            if not amount:
                continue
            currency = row.get("original_currency") or row.get("currency")
            account = row.get("account_ref") or (ref if is_manual_cash else None)
            if economic == "CASH" or is_manual_cash:
                cash_sources[ref] = ScenarioHolding(ref, amount, account, currency, row, economic,
                                                   tuple(evidence), amount)
                rate = rates.get(currency)
                cash_state = "AVAILABLE" if _identity(ref, row, economic, coverage, set(), {}) else "UNKNOWN"
                if rate is None and included:
                    # Keep value in D but block spending without a coherent FX rate.
                    cash[ref] = CashLot(account, currency, None, None, included, "UNKNOWN", frozen_cny=amount,
                                        availability_basis="UNKNOWN")
                elif rate is not None:
                    units = (amount / rate).quantize(Decimal("0.00000001"), rounding="ROUND_DOWN")
                    cash[ref] = CashLot(account, currency, units, rate, included, cash_state, frozen_cny=amount,
                        availability_basis="ASSUMED_FROM_FROZEN_BALANCE" if cash_state == "AVAILABLE" else "UNKNOWN")
            else:
                holdings[ref] = ScenarioHolding(ref, amount, account, currency, row, economic,
                                                 tuple(evidence), amount)
    if assumed_refs:
        raise TransitionRejected("SCENARIO_ASSUMED_ASSET_NOT_FOUND")
    unresolved_loans = set(request.assumed_loan_principal_cny)
    for ref, row in manual.items():
        if (str(row.get("kind", "")).lower() != "liability" or row.get("currency") != "CNY"
                or not row.get("effective_included_in_net_worth", row.get("included_in_net_worth"))):
            continue
        known = (not row.get("quality_issues") and _manual_date_current(row.get("value_as_of"), at)
                 and _manual_date_current(row.get("last_verified_at"), at))
        principal = _amount(row.get("current_value")) if known else None
        if ref in unresolved_loans:
            if principal is not None:
                raise TransitionRejected("SCENARIO_ASSUMED_LOAN_CONFLICT")
            principal = request.assumed_loan_principal_cny[ref]
            unresolved_loans.remove(ref)
        if principal is not None:
            loans[ref] = principal
    if unresolved_loans:
        raise TransitionRejected("SCENARIO_ASSUMED_LOAN_NOT_FOUND")

    instruments = {}
    for symbol in {*getattr(policy, "listings", {}), *(symbol for symbol, currency in IDENTITIES if currency == "USD")}:
        listing = getattr(policy, "listings", {}).get(symbol)
        issuer = IDENTITIES.get((symbol, "USD"))
        if not symbol.endswith(":US") or (issuer and ISSUERS[issuer][1] != "US"):
            continue
        instruments[symbol] = ({"symbol": symbol, "currency": "USD", "vehicle_type": "COMMON_STOCK",
                                "economic_asset_class": "EQUITY", "cusip": getattr(listing, "cusip", None)},
                               "EQUITY", ("scenario:canonical_issuer_listing",))
    for ticker, url in US_EQUITY_ETFS.items():
        symbol = f"{ticker}:US"
        instruments[symbol] = ({"symbol": symbol, "currency": "USD", "vehicle_type": "ETF",
                                "economic_asset_class": "EQUITY"}, "EQUITY", (url,))
    for ticker in ("IB01", "CBU3"):
        product = TRUSTED_INSTRUMENTS[ticker]
        symbol = f"{product.local_symbol}:LSE"
        instruments[symbol] = ({"symbol": symbol, "currency": "USD", "vehicle_type": "ETF",
                                "economic_asset_class": "FIXED_INCOME"}, "FIXED_INCOME", (product.verification_source,))
    evidence = _read_etf_evidence(portfolio_id, at=at) if etf_inputs is None else etf_inputs

    def evaluate(securities, balances, scenario_d, qualified_baseline):
        holdings = dict(securities)
        from dataclasses import replace
        for ref, lot in balances.items():
            if not lot.included or not lot.cny:
                continue
            original = cash_sources.get(ref)
            holdings[ref] = replace(original, amount_cny=lot.cny) if original else ScenarioHolding(
                ref, lot.cny, lot.account, lot.currency, {"vehicle_type": "CASH"}, "CASH", ("scenario:cash_lot",))
        if scenario_d is None:
            allocation = {"status": "UNKNOWN", "issues": ["NO_ASSUMED_OR_QUALIFIED_DENOMINATOR"]}
        else:
            visible = sum((holding.amount_cny for holding in holdings.values()), Decimal(0))
            if scenario_d > visible:
                holdings["scenario:unknown"] = ScenarioHolding("scenario:unknown", scenario_d-visible,
                    None, "CNY", {}, None, ())
            allocation = build_hierarchy(policy, denominator=scenario_d,
                included=[(h, h.identity, h.economic, h.evidence) for h in holdings.values()], coverage=coverage)
            allocation["basis"] = "SCENARIO_ONLY" if qualified_baseline else "ILLUSTRATIVE_ASSUMPTION"
            if qualified_baseline:
                allocation["strategic_review"] = formal_strategic_review(policy, allocation,
                    policy_context=session.context, portfolio_id=portfolio_id,
                    snapshot_sha256=projection.snapshot_sha256, denominator=scenario_d, scenario=True)
        try:
            concentration = analyze_scenario_concentration(view, projection=projection, session=session,
                at=at, evidence=evidence, classified_assets=allocation.get("classified_assets"),
                holdings=holdings, denominator=scenario_d, qualified_baseline=qualified_baseline, coverage=coverage)
        except PolicyError as error:
            if str(error) not in {"CONCENTRATION_REVIEW_RULE_UNAVAILABLE", "CONCENTRATION_BTC_RULES_UNAVAILABLE",
                                  "CONCENTRATION_POLICY_SUBJECT_UNMAPPED"}:
                raise
            concentration = {"status": "UNKNOWN" if qualified_baseline else "CONDITIONAL_RISK",
                             "basis": "SCENARIO_ONLY", "issues": [str(error)], "execution_authorized": False}
        return allocation, concentration

    wealth_observed = (projection.wealth_totals_status == "OBSERVED" and not request.assumed_loan_principal_cny
                       and timedelta(0) <= at - clock(view["captured_at"]) <= timedelta(minutes=5))
    debt = projection.liabilities_cny if wealth_observed else None
    if debt is not None and sum(loans.values(), Decimal(0)) > debt:
        raise TransitionRejected("SCENARIO_LOAN_TOTAL_NOT_CLOSED")
    paths = simulate_transition(request, holdings=holdings, cash=cash, loans=loans,
        total_debt=debt, net_assets=projection.net_assets_cny if wealth_observed and denominator is not None else None,
        denominator=denominator, rates=rates, accounts=accounts, instruments=instruments, evaluate=evaluate,
        archive_stage_holdings=freeze_evidence)
    session.ensure_current()
    records, pins, source_issue, caps, observations = evidence
    evaluation_sha256 = digest({
        "request_sha256": digest(request), "evaluated_at": at.isoformat(),
        "policy_reference": reference.model_dump(), "etf_records": pins, "close_caps": {symbol: str(cap) for symbol, cap in caps.items()},
        "etf_source_issue": source_issue, "etf_observations": {key: digest(row) for key, row in observations.items()},
    })
    archive = {}
    if freeze_evidence:
        from pathlib import Path
        import hashlib

        # Hash the actual loaded implementation family. An installed runtime
        # also carries the original deployment revision when available.
        from backend.services.investment_policy.runtime import _INSTALLED_RUNTIME
        base = Path(__file__).resolve().parent
        methods = ("allocation_service.py", "allocation_scenarios.py", "investable_assets.py",
                   "investment_policy/allocation_hierarchy.py", "investment_policy/concentration.py",
                   "investment_policy/etf_observation.py", "investment_policy/etf_release.py",
                   "investment_policy/concentration_adapter.py", "instruments/issuers.py",
                   "instruments/classification.py", "instruments/etf_holdings.py")
        archive = {"evidence_archive": {
            "normalized_input": request.model_dump(mode="json"),
            "method": "wealthpilot.portfolio-transition.v1",
            "code_revision": (_INSTALLED_RUNTIME or {}).get("code_sha"),
            "code_sha256": {name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in methods},
            "frozen_etf_evidence": {"formal": {key: item.model_dump(mode="json") for key, item in records.items()},
                "formal_pins": pins, "source_issue": source_issue,
                "close_caps_usd": {key: str(value) for key, value in caps.items()},
                "observations": {key: item.model_dump(mode="json") for key, item in observations.items()}},
        }}
    return TypeAdapter(dict).dump_python({
        **archive, "schema_version": "wealthpilot.portfolio-transition.v1", "scenario_revision_sha256": digest(request),
        "evaluated_at": at.isoformat(), "evaluation_sha256": evaluation_sha256,
        "snapshot_id": request.snapshot_id, "snapshot_sha256": request.snapshot_sha256,
        "policy_reference": reference.model_dump(),
        "basis": "QUALIFIED_BASELINE_SCENARIO" if denominator is not None else "ILLUSTRATIVE_ASSUMPTION",
        "baseline_issues": projection.policy_assets.issues if denominator is not None else
            sorted(set(projection.policy_assets.issues) | {"NO_QUALIFIED_BASELINE_DENOMINATOR"}),
        "paths": paths, "execution_authorized": False, "order_created": False,
    }, mode="json")

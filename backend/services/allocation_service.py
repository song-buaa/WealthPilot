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


def _assembled_policy_assets(portfolio_id, snapshot_id=None, *, frozen_view=None):
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
            raise error
        owner = view['data_quality'].get('investable_owner_evidence', {})
        if not owner or clock(owner['valid_until']) < clock(utc_now()):
            reject('POLICY_FULL_INVESTABLE_DENOMINATOR_UNVERIFIED',
                ['INVESTABLE_OWNER_EVIDENCE_UNAVAILABLE_OR_EXPIRED',
                 *[i for i in view['data_quality'].get('issues', []) if i.startswith('INVESTABLE_')]])
        evaluated_at = clock(utc_now())
        projection = project_investable_assets(view, portfolio_id=portfolio_id,
            snapshot_id=view['snapshot_id'], snapshot_sha256=digest(view), at=evaluated_at)
        # A new request-local session; never mutate a caller's pinned assets.
        from backend.services.investment_policy.runtime import PolicySession
        session = PolicySession(portfolio_id, assets=projection.policy_assets)
        if session.context.get('policy_reference') != pinned.context.get('policy_reference'):
            raise PolicyError('POLICY_VERSION_CONFLICT')
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
            at=evaluated_at, classified_assets=session.allocation_hierarchy['classified_assets'])
        session.allocation_source = {'snapshot_id':view['snapshot_id'], 'snapshot_sha256':digest(view),
            'as_of':view['captured_at'], 'evaluated_at':projection.evaluated_at,
            'expires_at':min(clock(view['captured_at'])+timedelta(minutes=5), clock(owner['valid_until'])).isoformat(),
            'owner_evidence_sha256':view['data_quality'].get('investable_owner_evidence', {}).get('sha256'),
            'issues':projection.policy_assets.issues}
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
    from backend.services.investable_assets import project_investable_assets
    from backend.services.investment_policy.concentration_adapter import allocation_concentration
    from backend.services.investment_policy.contracts import digest
    from backend.services.investment_policy.parser import PolicyError
    from backend.services.investment_policy.runtime import current
    from backend.services.investment_policy.store import clock, utc_now

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
    at = clock(utc_now())
    projection = project_investable_assets(view, portfolio_id=portfolio_id, snapshot_id=view["snapshot_id"],
                                            snapshot_sha256=digest(view), at=at)
    # A failed Allocation qualification cannot be promoted by the research view.
    projection = projection.model_copy(update={"status": "UNKNOWN"})
    result = allocation_concentration(view, projection=projection, session=session,
                                      at=at, classified_assets=None)
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

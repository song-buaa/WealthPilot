"""Portfolio/Wealth's pure projection of ONE frozen U1 view into PolicyAssets.

No loader, database, provider or policy resolver. Scope evidence is an internal
source-owner extension of the frozen view, never client arguments or a second
asset ledger. Old views without it remain UNKNOWN. No production wiring in A-min.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import Field, ValidationError

from app.asset_views import decode, dumps, snapshot_number, _source_current, _manual_date_current, _stored_fx_current
from backend.services.investment_policy.contracts import Contract, digest
from backend.services.investment_policy.runtime import PolicyAssets


class Disposition(Contract):
    state: Literal['INCLUDED', 'EXCLUDED', 'UNKNOWN']
    reason: Literal['FREE_INVESTMENT', 'LIVING_CASH', 'EMERGENCY_RESERVE',
                    'DEBT_RESERVE', 'RESTRICTED', 'PENSION', 'DUPLICATE', 'UNKNOWN']
    evidence_refs: list[str] = Field(default_factory=list)


class Coverage(Contract):
    status: Literal['PRESENT', 'ABSENT', 'UNKNOWN']
    as_of: str
    evidence_refs: list[str] = Field(default_factory=list)
    asset_refs: list[str] = Field(default_factory=list)


class ScopeEvidence(Contract):
    schema_version: Literal['wealthpilot.investable-scope.v1']
    portfolio_id: int
    snapshot_id: str
    as_of: str
    dispositions: dict[str, Disposition]
    coverage: dict[str, Coverage]
    # wealth:<id> -> position:<id> links, evidenced by that pension's disposition.
    pension_links: dict[str, list[str]] = Field(default_factory=dict)


class ProjectedAsset(Contract):
    source_ref: str
    state: Literal['INCLUDED', 'EXCLUDED', 'UNKNOWN']
    reason: str
    amount_cny: Decimal | None
    issues: list[str]


class InvestableProjection(Contract):
    schema_version: Literal['wealthpilot.investable-projection.v1'] = 'wealthpilot.investable-projection.v1'
    snapshot_sha256: str
    evaluated_at: str
    status: Literal['QUALIFIED', 'UNKNOWN']
    policy_assets: PolicyAssets
    assets: list[ProjectedAsset]
    # Original wealth totals are distinct from D; debt is NEVER subtracted from D.
    total_assets_cny: Decimal | None
    liabilities_cny: Decimal | None
    net_assets_cny: Decimal | None
    wealth_totals_status: Literal['OBSERVED', 'UNKNOWN']
    amount_unit: Literal['CNY major decimal'] = 'CNY major decimal'
    execution_authorized: Literal[False] = False


def _amount(value):
    try:
        if isinstance(value, bool):
            return None
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _evidenced(refs):
    return bool(refs) and all(ref.strip() for ref in refs)


def project_investable_assets(view, *, portfolio_id, snapshot_id, snapshot_sha256, at):
    """Validate source identity/hash and derive qualification, never trust flags.

    The hash must come from the original frozen-input owner. A content digest
    binds bytes, not truth or authentication. Caller must already hold that
    owner's trusted view; no API/MCP accepts this evidence from users.
    """
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise ValueError('INVESTABLE_AWARE_CLOCK_REQUIRED')
    source = decode(dumps(view))  # Copy; preserves original U1 schema validation.
    if (type(portfolio_id) is not int or portfolio_id <= 0
            or source.get('portfolio_id') != portfolio_id
            or source.get('snapshot_id') != snapshot_id or source.get('state') != 'ready'):
        raise ValueError('INVESTABLE_SNAPSHOT_SCOPE_MISMATCH')
    snapshot_number(snapshot_id)
    actual_hash = digest(source)
    if snapshot_sha256 != actual_hash:
        raise ValueError('INVESTABLE_SNAPSHOT_HASH_MISMATCH')
    quality = source.get('data_quality', {})
    issues = []
    if not _source_current(source.get('captured_at'), at):
        issues.append('INVESTABLE_SNAPSHOT_STALE')
    raw = quality.get('investable_scope_evidence')
    try:
        evidence = ScopeEvidence.model_validate(raw) if raw else None
    except ValidationError:
        evidence = None
        issues.append('INVESTABLE_SCOPE_EVIDENCE_INVALID')
    if evidence and (evidence.portfolio_id != portfolio_id or evidence.snapshot_id != snapshot_id):
        raise ValueError('INVESTABLE_EVIDENCE_SCOPE_MISMATCH')
    if not evidence:
        issues.append('INVESTABLE_SCOPE_EVIDENCE_MISSING')
    elif not _source_current(evidence.as_of, at):
        issues.append('INVESTABLE_SCOPE_EVIDENCE_STALE')

    inputs = quality.get('security_inputs', [])
    manual = source.get('views', {}).get('get_manual_wealth_items', {}).get('items', [])
    accounts = quality.get('accounts', [])
    account_map = {a.get('account_ref'): a for a in accounts}
    if len(account_map) != len(accounts) or None in account_map:
        issues.append('INVESTABLE_ACCOUNT_IDENTITY_UNKNOWN')
    for account in accounts:
        # Inventory includes excluded rows too. A failed/partial last-good
        # account cannot prove that other freely investable holdings are absent.
        if (account.get('publication_status') != 'matched' or account.get('last_attempt_status') != 'success'
                or account.get('quality') != 'current' or account.get('complete') is not True
                or account.get('fill_sync_pending') or not _source_current(account.get('account_synced_at'), at)):
            issues.append('INVESTABLE_ACCOUNT_INVENTORY_UNVERIFIED')
    source_rows = [('position', row) for row in inputs]
    source_rows += [('wealth', row) for row in manual if str(row.get('kind', '')).lower() == 'asset']
    keys = [f'{kind}:{row.get("id")}' for kind, row in source_rows]
    if len(keys) != len(set(keys)) or any(type(row.get('id')) is not int or row['id'] <= 0 for _, row in source_rows):
        raise ValueError('INVESTABLE_DUPLICATE_OR_INVALID_SOURCE_ID')
    if evidence and set(evidence.dispositions) - set(keys):
        issues.append('INVESTABLE_UNKNOWN_DISPOSITION_SOURCE')
    if any(row.get('account_ref') not in account_map for row in inputs):
        issues.append('INVESTABLE_ACCOUNT_COVERAGE_UNKNOWN')

    coverage_ok = {}
    for domain in ('accounts', 'manual_assets', 'pensions', 'gold', 'btc', 'deduplication'):
        item = evidence.coverage.get(domain) if evidence else None
        ok = bool(item and item.status != 'UNKNOWN' and _evidenced(item.evidence_refs)
                  and _source_current(item.as_of, at) and set(item.asset_refs) <= set(keys))
        if item and item.status == 'PRESENT' and domain != 'accounts':
            ok = ok and bool(item.asset_refs)
        if item and item.status == 'ABSENT' and item.asset_refs:
            ok = False
        if domain == 'accounts' and accounts:
            ok = ok and item.status == 'PRESENT' and set(item.asset_refs) == {k for k in keys if k.startswith('position:')}
        if domain == 'manual_assets' and any(k.startswith('wealth:') for k in keys):
            ok = ok and item.status == 'PRESENT' and set(item.asset_refs) == {k for k in keys if k.startswith('wealth:')}
        if domain == 'deduplication':
            ok = ok and item.status == 'PRESENT' and set(item.asset_refs) == set(keys)
        coverage_ok[domain] = ok
        if not ok:
            issues.append('INVESTABLE_'+domain.upper()+'_COVERAGE_UNKNOWN')

    linked_positions = set()
    pension_ok = coverage_ok['pensions']
    pension_rows = {f'wealth:{row.get("id")}' for row in manual
                    if row.get('item_type') in {'personal_pension', 'pension_insurance', 'basic_pension',
                                                'enterprise_annuity'} or row.get('already_investment_accounted')}
    if pension_rows:
        covered = evidence.coverage.get('pensions') if evidence else None
        pension_ok = bool(pension_ok and covered and covered.status == 'PRESENT'
                          and pension_rows <= set(covered.asset_refs))
    for row in manual:
        if not row.get('already_investment_accounted'):
            continue
        key = f'wealth:{row.get("id")}'
        links = evidence.pension_links.get(key, []) if evidence else []
        disposition = evidence.dispositions.get(key) if evidence else None
        valid = (bool(links) and len(links) == len(set(links))
                 and set(links) <= {k for k in keys if k.startswith('position:')}
                 and not linked_positions.intersection(links)
                 and disposition and _evidenced(disposition.evidence_refs)
                 and disposition.state == 'EXCLUDED' and disposition.reason == 'DUPLICATE')
        if not valid:
            pension_ok = False
        linked_positions.update(links)
    if evidence and set(evidence.pension_links) - {f'wealth:{row.get("id")}' for row in manual if row.get('already_investment_accounted')}:
        pension_ok = False
    if not pension_ok:
        issues.append('PENSION_HOLDING_LINK_UNVERIFIED')

    projected, included, fx_ok = [], Decimal(0), True
    for kind, row in source_rows:
        key = f'{kind}:{row["id"]}'
        disposition = evidence.dispositions.get(key) if evidence else None
        row_issues = []
        state = disposition.state if disposition else 'UNKNOWN'
        reason = disposition.reason if disposition else 'UNKNOWN'
        if (not disposition or not _evidenced(disposition.evidence_refs)
                or state == 'UNKNOWN' or reason == 'UNKNOWN'
                or (state == 'INCLUDED') != (reason == 'FREE_INVESTMENT')):
            row_issues.append('ASSET_PURPOSE_UNVERIFIED')
        if key in linked_positions and not pension_ok:
            row_issues.append('PENSION_HOLDING_LINK_UNVERIFIED')
        if state == 'EXCLUDED' and reason in {'LIVING_CASH', 'EMERGENCY_RESERVE', 'DEBT_RESERVE'}:
            if not (row.get('vehicle_type') == 'CASH' or kind == 'wealth' and row.get('item_type') == 'bank_cash'):
                row_issues.append('ASSET_EXCLUSION_NOT_APPLICABLE')
        if reason == 'PENSION' and key not in pension_rows | linked_positions:
            row_issues.append('PENSION_HOLDING_LINK_UNVERIFIED')
        if state == 'EXCLUDED' and reason == 'DUPLICATE' and key not in (evidence.pension_links if evidence else {}):
            row_issues.append('ASSET_DUPLICATION_LINK_UNVERIFIED')
        amount = _amount(row.get('market_value_cny') if kind == 'position' else row.get('current_value'))
        if state != 'EXCLUDED':
            if amount is None:
                row_issues.append('ASSET_VALUE_UNVERIFIED')
            currency = row.get('original_currency') or row.get('currency')
            fx_valid = currency == 'CNY' or bool(currency and (kind == 'wealth' or row.get('fx_quality') == 'current')
                                               and _stored_fx_current(row, at))
            if not fx_valid:
                row_issues.append('FX_STORED_VALUATION_UNVERIFIED')
                fx_ok = False
            if kind == 'position':
                account = account_map.get(row.get('account_ref'), {})
                if (account.get('publication_status') != 'matched' or account.get('last_attempt_status') != 'success'
                        or account.get('fill_sync_pending') or not _source_current(account.get('account_synced_at'), at)):
                    row_issues.append('ACCOUNT_SOURCE_UNVERIFIED_OR_STALE')
                cash = row.get('vehicle_type') == 'CASH'
                if not _source_current(row.get('cash_as_of' if cash else 'positions_as_of'), at):
                    row_issues.append('ASSET_SOURCE_TIME_UNKNOWN_OR_STALE')
                if not cash:
                    # Like U1's qualify_view, defer the optional market helper;
                    # its legacy package initializer adjusts sys.path.
                    from backend.services.execution_plan.market_hours import valuation_quality
                    symbol = row.get('security_symbol') or row.get('symbol') or ''
                    price = valuation_quality(row.get('price_as_of'), symbol.rsplit(':', 1)[-1], at=at,
                                              basis=row.get('price_time_basis'))
                    if price['status'] not in {'intraday', 'recent_close'}:
                        row_issues.append('PRICE_TIME_UNKNOWN_OR_STALE')
            elif (not _manual_date_current(row.get('value_as_of'), at)
                  or not _manual_date_current(row.get('last_verified_at'), at) or row.get('quality_issues')):
                row_issues.append('MANUAL_ASSET_VALUE_UNVERIFIED_OR_STALE')
        if row_issues:
            state = 'UNKNOWN'
            issues.extend(f'{key}:{code}' for code in row_issues)
        elif state == 'INCLUDED':
            included += amount
        projected.append(ProjectedAsset(source_ref=key, state=state, reason=reason,
                                        amount_cny=amount, issues=row_issues))

    if not projected or included <= 0:
        issues.append('INVESTABLE_POSITIVE_DENOMINATOR_UNVERIFIED')
    totals = source.get('totals', {})
    assets, debt = _amount(totals.get('total_assets')), _amount(totals.get('total_liabilities'))
    # Debt freshness affects wealth/net-worth, not investment-only qualification.
    wealth_ok = all(not row.get('quality_issues') and _manual_date_current(row.get('value_as_of'), at)
                    and _manual_date_current(row.get('last_verified_at'), at)
                    for row in manual)
    net = assets-debt if assets is not None and debt is not None and wealth_ok else None
    qualified = not issues
    receipt = PolicyAssets(portfolio_id=portfolio_id, snapshot_id=snapshot_id, as_of=source['captured_at'],
        denominator_cny=included if qualified else None, denominator_qualified=qualified,
        complete_inventory=all(coverage_ok.values()), fx_qualified=fx_ok,
        pension_exclusion_verified=bool(pension_ok),
        deduplication_verified=bool(pension_ok and coverage_ok['deduplication'] and not issues),
        external_assets_verified=bool(coverage_ok['gold'] and coverage_ok['btc']),
        coverage_evidence=sorted({ref for item in evidence.coverage.values() for ref in item.evidence_refs}) if evidence else [],
        issues=sorted(set(issues)))
    return InvestableProjection(snapshot_sha256=actual_hash, evaluated_at=at.isoformat(),
        status='QUALIFIED' if qualified else 'UNKNOWN',
        policy_assets=receipt, assets=projected, total_assets_cny=assets, liabilities_cny=debt,
        net_assets_cny=net, wealth_totals_status='OBSERVED' if net is not None else 'UNKNOWN')

"""Capture original service results on one frozen DB in the existing RO worker."""
from datetime import date, datetime
import json

from app.asset_views import VERSION, PIPELINE, now


def time_quality(value, max_age, *, daily=False):
    try:
        if len(value) == 10:
            age = (date.today()-date.fromisoformat(value)).days
            return ("current" if 0 <= age <= 1 else "stale") if daily else "unknown"
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            return "unknown"
        age = (now()-stamp).total_seconds()
        return "current" if 0 <= age <= max_age else "stale"
    except (TypeError, ValueError):
        return "unknown"


def capture(path, portfolio_id, internal, *, synthetic=False):
    from pocs.chatgpt_skills.analysis import readonly_business_database
    from pocs.chatgpt_skills.reader import read_details, account_ref
    from pocs.poc02_wealth_mcp.reader import read_overview
    from backend.services.instruments.issuers import exposure
    from backend.services import wealth_service, portfolio_service
    from app.database import get_session
    from app.models import Position
    from services.broker_sync.models import PositionSnapshotRun
    from backend.api.broker_sync import _public_quality, public_sync_attempt
    from services.broker_sync.repository import publication_status
    with readonly_business_database(path):
        overview = read_overview(path, portfolio_id)
        overview["synthetic"] = synthetic
        details = read_details(path, portfolio_id, "get_investment_details", {}, synthetic=synthetic)
        manual = read_details(path, portfolio_id, "get_manual_wealth_items", {}, synthetic=synthetic)
        wealth = wealth_service.get_summary(portfolio_id, record_snapshot=False, include_cashflow=False)
        totals = wealth_service._current_totals(portfolio_id)
        positions = portfolio_service.get_positions(portfolio_id)
        companies = exposure(details["items"])
        issues = set(overview["issues"] + details["issues"] + manual["issues"])
        issues.update(internal.get("prepare_issues", []))
        accounts = []
        with get_session() as session:
            from app.fill_sync import pending_accounts
            unsettled = pending_accounts(session,portfolio_id)
            rows = session.query(Position).filter_by(portfolio_id=portfolio_id, segment="投资").all()
            requested = internal.get("owners", [])
            owners = {(p.broker, p.broker_account_id, p.sync_source) for p in rows if p.broker and p.broker_account_id}
            owners.update((o["broker"], o["account"], "api") for o in requested if o["account"])
            owners.update((broker,account,"api") for broker,account in unsettled if account)
            for broker, account, source in sorted(owners, key=str):
                runs = session.query(PositionSnapshotRun).filter_by(portfolio_id=portfolio_id,
                    broker=broker, account_id=account, sync_source=source).order_by(PositionSnapshotRun.id.desc()).all()
                latest = runs[0] if runs else None
                good = next((r for r in runs if r.status == "success"), None)
                quality = _public_quality(good)
                sample = next((p for p in rows if p.broker == broker and p.broker_account_id == account), None)
                if sample is not None:
                    ref = account_ref(portfolio_id, sample)
                else:
                    import hashlib
                    ref = "account_" + hashlib.sha256(repr((portfolio_id,broker,account)).encode()).hexdigest()[:20]
                entry = dict(account_ref=ref, broker=broker,
                    **public_sync_attempt(latest),
                    last_good_run_id=good.id if good else None,
                    last_good_quality=quality["quality"],
                    account_synced_at=good.finished_at.isoformat() if good and good.finished_at else None,
                    **quality)
                if entry["account_synced_at"]:
                    from datetime import timezone
                    stamp = good.finished_at
                    entry["account_synced_at"] = (stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp).isoformat()
                entry["fill_sync_pending"] = (broker,account) in unsettled
                if entry["fill_sync_pending"]:
                    issues.add("FILL_SYNC_PENDING_OR_UNVERIFIED")
                # Recheck the actual frozen input, not a pre-Backup observation.
                entry["publication_status"] = publication_status(session, good)
                if entry["publication_status"] != "matched":
                    issues.add("SYNC_PUBLICATION_"+entry["publication_status"].upper())
                accounts.append(entry)
                issues.update(quality.get("issues") or [])
                if not good:
                    issues.add("NO_SUCCESSFUL_SYNC")
                if latest and latest.status != "success":
                    issues.add("LATEST_POSITION_SYNC_FAILED" if latest.status == "failed" else "SYNC_IN_PROGRESS")
                if good and good.finished_at:
                    from datetime import timezone, datetime
                    stamp = good.finished_at.replace(tzinfo=timezone.utc) if good.finished_at.tzinfo is None else good.finished_at
                    if not 0 <= (now()-stamp).total_seconds() <= 300:
                        issues.add("STALE_ACCOUNT_SYNC")
                # Source reading time is not the security price/valuation time.
                owned = [p for p in rows if p.broker == broker and p.broker_account_id == account]
                entry['time_quality'] = {name:time_quality(quality.get(name), limit, daily=(name=='price_as_of'))
                    for name,limit in [('positions_as_of',300),('cash_as_of',300),('price_as_of',86400)]}
                for relevant,name,code in [(any(p.vehicle_type!='CASH' for p in owned),'positions_as_of','POSITION_TIME'),
                        (any(p.vehicle_type=='CASH' for p in owned),'cash_as_of','CASH_TIME')]:
                    if relevant and entry['time_quality'][name]!='current':
                        issues.add(code+'_'+entry['time_quality'][name].upper())
                if quality.get("quality") != "current":
                    issues.add("SOURCE_QUALITY_PARTIAL")
            if any(not o["account"] for o in requested):
                issues.add("SYNC_ACCOUNT_UNCONFIGURED")
            for row in rows:
                if row.economic_asset_class in (None, "UNKNOWN"):
                    issues.add("UNCLASSIFIED_HOLDINGS")
        if companies["unmapped_direct_holdings"]:
            issues.add("ISSUER_MAPPING_INCOMPLETE")
        for item in manual["items"]:
            if item["already_investment_accounted"]:
                issues.add("PENSION_HOLDING_LINK_UNVERIFIED")
            if not item["value_as_of"] or not 0 <= (date.today()-date.fromisoformat(item["value_as_of"])).days <= 30:
                issues.add("MANUAL_VALUE_STALE")
            if item["currency"] != "CNY":
                from app.fx_service import fx_date_is_current
                if not fx_date_is_current(item["fx_rate_date"]):
                    issues.add("MANUAL_FX_UNVERIFIED")
        by_ref = {a["account_ref"]:a for a in accounts}
        security_inputs = []
        for item in details["items"]:
            a = by_ref.get(item["account_ref"], {})
            security_inputs.append({**{k:item.get(k) for k in ("id","symbol","ticker","account_ref","security_symbol",
                "vehicle_type","economic_asset_class","market_value_cny","currency","original_currency","fx_rate_date","fx_rate_to_cny",
                "quantity","current_price","price_basis")},
                "fx_quality":(a.get("fx") or {}).get(item.get("original_currency") or item.get("currency"),{}).get("quality","unverified"),
                **{k:a.get(k) for k in ("positions_as_of","cash_as_of","price_as_of","price_time_basis","time_evidence")}})
        quality = dict(status="partial" if issues else "ok", issues=sorted(issues), policy_version="d1.1",
            can_propose_rebalance=not issues, accounts=accounts,
            security_inputs=security_inputs,
            manual_inputs=[{k:item[k] for k in ('id','source_type','sync_mode','value_as_of','last_verified_at',
                'included_in_net_worth','already_investment_accounted')} for item in manual['items']],
            time_contract="account_synced_at / positions_as_of / cash_as_of / price_as_of / fx.rate_date / manual.value_as_of are distinct; captured_at is not a source date")
        result = dict(schema_version=VERSION, pipeline=PIPELINE, state="ready", portfolio_id=portfolio_id,
            captured_at=internal.get("frozen_at",now().isoformat()), data_quality=quality, issuer_exposure=companies,
            views=dict(get_wealth_overview=overview, get_manual_wealth_items=manual,
                       get_investment_details=details, wealth_summary=wealth,
                       portfolio_summary=details["portfolio_summary"], positions=positions),
            totals={k:totals[k] for k in ("total_assets", "total_liabilities", "net_worth", "investment_assets",
                 "investment_profit_loss", "non_investment_assets", "pension_benefit_value")}, internal=internal)
        from app.asset_views import qualify_view
        qualify_view(result, at=now())
        return result

"""U2 durable progress on original orders, not an order/holdings state machine.

Database-only helpers. No broker client, scheduler, cash mutation or ORM action
imports: the original publisher and read-only workers share this contract.
"""
from datetime import datetime, timezone
from decimal import Decimal
import json
import time

from sqlalchemy import inspect, text

VERSION = 1
SUPPORTED = {"ibkr": "snowball", "tiger": "tiger"}


def decode(value):
    result = json.loads(value or "{}")
    return result if result.get("version") == VERSION else {}


def encode(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def orders(db, portfolio_id, broker=None, account=None):
    # Old isolated readonly fixtures may intentionally have no trading tables.
    connection=db.connection() if callable(getattr(db,"connection",None)) else db
    if "order_records" not in inspect(connection).get_table_names():
        return []
    clause, args = "portfolio_id=:p", {"p": portfolio_id}
    if broker is not None:
        clause += " AND broker_name=:b AND broker_account_id=:a"
        args.update(b=broker, a=account)
    return list(db.execute(text("SELECT * FROM order_records WHERE " + clause + " ORDER BY created_at,id"), args).mappings())


def pending(row):
    progress = decode(row.get("fill_sync_json"))
    return bool(row['broker_name'] in SUPPORTED and row.get('broker_account_id') and progress
                and int(row["filled_quantity"] or 0) > int(progress.get("covered_quantity", 0)))


def pending_accounts(db, portfolio_id):
    return {(SUPPORTED.get(r["broker_name"], r["broker_name"]), r["broker_account_id"])
            for r in orders(db, portfolio_id) if pending(r)}


def _snapshots(db, run_id):
    return list(db.execute(text("SELECT * FROM position_snapshots WHERE run_id=:r"), {"r":run_id}).mappings())


def _facts(rows):
    facts = {}
    for row in rows:
        key = row["symbol"] + "|" + row["currency"]
        if key in facts:
            raise ValueError("DUPLICATE_SNAPSHOT_IDENTITY")
        raw = json.loads(row["raw_data_json"] or "{}")
        facts[key] = {"quantity": str(row["quantity"]), "currency": row["currency"],
                      "con_id": raw.get("con_id"), "cash": row["vehicle_type"] == "CASH"}
    return facts


def _notional(row):
    return (Decimal(str(row["avg_filled_price"])) * int(row["filled_quantity"] or 0)
            if row["avg_filled_price"] is not None else None)


def capture_baseline(session, owner):
    """Called inside original pre-send Tx A, before any broker I/O.

    Consecutive open requests share the oldest unresolved account anchor. Never
    create a post-fill baseline to make a legacy recovery appear verified.
    """
    if owner.broker not in SUPPORTED:
        return None
    previous = orders(session, owner.portfolio_id, owner.broker, owner.account_id)
    for row in previous:
        progress = decode(row["fill_sync_json"])
        if progress and ((pending(row) and progress.get('auto_retry',True)) or row["status"] not in {"filled","cancelled","expired","rejected"}):
            return encode({"version":VERSION, "anchor":progress.get("anchor")})
    if "position_snapshot_runs" not in inspect(session.connection()).get_table_names():
        return encode({"version":VERSION,"anchor":None})
    from services.broker_sync.models import PositionSnapshotRun
    from services.broker_sync.repository import context, publication_status
    run = session.query(PositionSnapshotRun).filter_by(portfolio_id=owner.portfolio_id,
        broker=owner.holdings_broker, account_id=owner.account_id, sync_source="api",status="success").order_by(
            PositionSnapshotRun.id.desc()).first()
    anchor = None
    if run and run.finished_at and context(run).get("complete") and publication_status(session,run)=="matched":
        stamp = run.finished_at.replace(tzinfo=timezone.utc) if run.finished_at.tzinfo is None else run.finished_at
        if 0 <= (datetime.now(timezone.utc)-stamp).total_seconds() <= 300:
            snapshots = _snapshots(session,run.id)
            if len(snapshots) <= 2000:
                anchor = {"run_id":run.id,"facts":_facts(snapshots),
                    "before_dispatch_at":datetime.now(timezone.utc).isoformat(),
                    "source_times":{k:context(run).get(k) for k in ('positions_as_of','cash_as_of')},"watermarks":{
                    row["id"]:{"quantity":int(row["filled_quantity"] or 0),
                        "notional":str(_notional(row)) if _notional(row) is not None else "0" if not row["filled_quantity"] else None,
                        "commission":decode(row["fill_sync_json"]).get("commission") if row["filled_quantity"] else "0"}
                    for row in previous}}
    return encode({"version":VERSION,"anchor":anchor})


def observe(order, raw):
    """Database-only outbox on the same original order observation commit."""
    if order.broker_name not in SUPPORTED or not order.portfolio_id or not order.broker_account_id:
        return
    progress = decode(order.fill_sync_json) or {"version":VERSION,"anchor":None}
    old_commission = progress.get("actual_commissions")
    quantity = int(order.filled_quantity or 0)
    if quantity <= 0:
        return
    if quantity > int(progress.get("target_quantity",0)):
        progress.update(target_quantity=quantity, next_attempt_at=time.time()+2, auto_retry=True)
    evidence = progress.get("executions",{})
    if raw.get("reconciliation_disputed"):
        progress["evidence_conflict"] = True
        progress["covered_quantity"] = 0
    for item in raw.get("execution_evidence",[]):
        if (item.get("exec_id") and item.get("observed_account_id")==order.broker_account_id
                and (item.get("order_ref")==order.id or raw.get("perm_id") and item.get("perm_id")==raw["perm_id"])):
            # Same execution may gain its actual commission later. Conflicting
            # economic facts are not silently merged into a coverage proof.
            clean = {k:item.get(k) for k in ("exec_id","shares","price","timestamp","commission","commission_currency","con_id")}
            prior = evidence.get(item["exec_id"])
            if prior and any(prior.get(k)!=clean.get(k) for k in ("shares","price","con_id")):
                progress["evidence_conflict"] = True
            if prior and prior.get("commission") is not None and clean["commission"] is not None and (
                    prior["commission"]!=clean["commission"] or prior.get("commission_currency")!=clean["commission_currency"]):
                progress["evidence_conflict"] = True
            if prior and clean["commission"] is None and prior.get("commission") is not None:
                clean.update(commission=prior["commission"],commission_currency=prior.get("commission_currency"))
            evidence[item["exec_id"]] = clean
    progress["executions"] = evidence
    if raw.get("broker_filled_at"):
        progress["broker_filled_at"] = raw["broker_filled_at"]
    raw_currency = raw.get("contract_currency") or raw.get("currency")
    claimed = json.loads(order.submission_payload_json or "{}").get("parameters",{}).get("resolved_contract") or {}
    progress["currency"] = claimed.get("currency") or raw_currency or progress.get("currency")
    progress["con_id"] = claimed.get("con_id") or raw.get("con_id") or progress.get("con_id")
    if evidence and not progress.get("evidence_conflict"):
        try:
            shares = sum(Decimal(str(v["shares"])) for v in evidence.values())
            notional = sum(Decimal(str(v['shares']))*Decimal(str(v['price'])) for v in evidence.values())
            valid_identity = all(not progress.get('con_id') or str(v['con_id'])==str(progress['con_id']) for v in evidence.values())
            valid_prices = all(Decimal(str(v['shares']))>0 and Decimal(str(v['price']))>0 for v in evidence.values())
            if shares>quantity or not valid_identity or not valid_prices:
                progress['evidence_conflict']=True
            if (shares==quantity and valid_identity and valid_prices and order.avg_filled_price is not None
                    and abs(notional-Decimal(str(order.avg_filled_price))*quantity)<=Decimal('0.01')
                    and all(v["commission"] is not None and v["commission_currency"] for v in evidence.values())):
                fees={}
                for v in evidence.values():
                    ccy=v['commission_currency'];fee=Decimal(str(v['commission']))
                    if not fee.is_finite():raise ValueError('INVALID_ACTUAL_COMMISSION')
                    fees[ccy]=fees.get(ccy,Decimal(0))+fee
                progress['actual_commissions']={k:str(v) for k,v in fees.items()}
                if set(fees)=={progress.get('currency')}:
                    progress['commission']=str(fees[progress['currency']])
                else:progress.pop('commission',None)
            else:
                progress.pop("commission",None)
                progress.pop('actual_commissions',None)
        except (ValueError,TypeError,ArithmeticError):
            progress.pop("commission",None)
            progress.pop('actual_commissions',None)
    if progress.get("evidence_conflict"):
        progress.pop("commission",None)
        progress.pop('actual_commissions',None)
        progress['covered_quantity']=0
    if progress.get('actual_commissions') and progress['actual_commissions']!=old_commission:
        progress['next_attempt_at']=time.time()+2
        progress['auto_retry']=True
    order.fill_sync_json = encode(progress)


def due(rows, now=None):
    now = time.time() if now is None else now
    return [r for r in rows if pending(r) and decode(r["fill_sync_json"]).get('auto_retry',True)
            and decode(r["fill_sync_json"]).get("next_attempt_at",0)<=now]


def bind_targets(session, run, now=None, *, force=False):
    """Under the existing account claim lock; repeated processes re-read here."""
    broker = "ibkr" if run.broker=="snowball" else run.broker
    candidates=orders(session,run.portfolio_id,broker,run.account_id)
    if not force and not due(candidates,now):return None
    # A later manual/cron response may regress a broker cache. Recheck tracked
    # coverage on EVERY publication, not only the currently pending targets.
    rows=[r for r in candidates if int(r['filled_quantity'] or 0)>0 and decode(r.get('fill_sync_json'))]
    if not rows:return None
    targets={r["id"]:int(r["filled_quantity"]) for r in rows}
    for row in rows:
        progress=decode(row["fill_sync_json"])
        attempt=int(progress.get("attempts",0))+1
        progress.update(last_run_id=run.id,attempted_quantity=targets[row["id"]],attempts=attempt,
                        auto_retry=True,
                        next_attempt_at=(time.time() if now is None else now)+min(300,5*2**min(attempt,6)))
        session.execute(text("UPDATE order_records SET fill_sync_json=:v WHERE id=:i"),{"i":row["id"],"v":encode(progress)})
    return targets


def coverage(session, run, snapshots, targets):
    """Corroborate cumulative account deltas; time alone is NEVER a proof.

    Unknown baseline/identity/fees, zero-net round trips and unmatched native
    cash remain UNVERIFIED. We never synthesize positions/cash from the math.
    """
    broker="ibkr" if run.broker=="snowball" else run.broker
    rows=orders(session,run.portfolio_id,broker,run.account_id)
    by_id={r["id"]:r for r in rows}
    current=_facts(snapshots)
    for order_id, target in targets.items():
        row=by_id.get(order_id)
        if row is None:continue
        progress=decode(row["fill_sync_json"])
        # A fill observed after source I/O cannot be acknowledged by this run.
        reasons=[]
        anchor=progress.get("anchor")
        position_ok=cash_ok=clock_ok=False
        clocks_missing=False
        if not anchor:
            reasons.append("PRETRADE_BASELINE_UNAVAILABLE")
        elif int(row["filled_quantity"])!=target:
            reasons.append("FILL_ADVANCED_DURING_SYNC")
        else:
            base=anchor["facts"]
            expected={k:Decimal(v["quantity"]) for k,v in base.items()}
            deltas={}
            cash_deltas={}
            unknown=False
            relevant=[]
            for other in rows:
                # An uncertain accepted order may have unseen fills; it also
                # invalidates an account delta proof when its local qty is 0.
                if other['status']=='unknown':unknown=True
                before=anchor["watermarks"].get(other["id"],{"quantity":0,"notional":"0","commission":"0"})
                qty=int(other["filled_quantity"] or 0)-int(before["quantity"])
                if qty<0:unknown=True;continue
                if qty==0:continue
                p=decode(other["fill_sync_json"])
                ccy=p.get("currency")
                key=other["symbol"]+"|"+str(ccy)
                # IBKR LSE aliases must resolve by conId rather than ticker.
                if p.get("con_id"):
                    matches=[k for k,v in {**base,**current}.items() if str(v.get("con_id"))==str(p["con_id"]) and v["currency"]==ccy and not v["cash"]]
                    if len(set(matches))==1:key=matches[0]
                    elif len(set(matches))>1:unknown=True
                if not ccy or other["side"] not in {"BUY","SELL"} or p.get("evidence_conflict"):
                    unknown=True
                sign=1 if other["side"]=="BUY" else -1
                deltas[key]=deltas.get(key,Decimal(0))+sign*qty
                expected[key]=expected.get(key,Decimal(0))+sign*qty
                relevant.append((key,p))
                amount=_notional(other)
                if amount is None or before.get("notional") is None or p.get("commission") is None or before.get("commission") is None:
                    cash_deltas[ccy]=None
                elif cash_deltas.get(ccy,0) is not None:
                    cash_deltas[ccy]=cash_deltas.get(ccy,Decimal(0))-sign*(amount-Decimal(before["notional"]))-(Decimal(p["commission"])-Decimal(before["commission"]))
            if not deltas or any(delta==0 for delta in deltas.values()):
                reasons.append("NETTED_FILLS_REQUIRE_BROKER_WATERMARK")
            elif unknown:
                reasons.append("FILL_IDENTITY_OR_AUTHORITY_UNVERIFIED")
            else:
                position_ok=all(Decimal(current.get(k,{"quantity":"0"})["quantity"])==expected[k] for k in deltas)
                # Same ticker with contradictory contract IDs is not evidence.
                position_ok=position_ok and all(not p.get("con_id") or k not in current or str(current[k].get("con_id"))==str(p["con_id"]) for k,p in relevant)
                if not position_ok:reasons.append("POSITION_DELTA_NOT_OBSERVED")
            cash_ok=bool(cash_deltas)
            for ccy,delta in cash_deltas.items():
                initial=[v for v in base.values() if v["cash"] and v["currency"]==ccy]
                latest=[v for v in current.values() if v["cash"] and v["currency"]==ccy]
                if delta is None or len(initial)!=1 or len(latest)!=1 or abs(Decimal(latest[0]["quantity"])-(Decimal(initial[0]["quantity"])+delta))>Decimal("0.01"):
                    cash_ok=False
            if not cash_ok:reasons.append("NATIVE_CASH_OR_ACTUAL_FEES_UNVERIFIED")
            # A matching quantity/cash delta can itself be an older cached
            # change. Never bless fetched_at/finished_at as a broker watermark.
            # Require native source clocks bracketing actual executions as
            # WELL as the independent identity/quantity/cash checks above.
            def stamp(value):
                try:
                    parsed=datetime.fromisoformat(value)
                    return parsed if parsed.tzinfo is not None else None
                except (ValueError,TypeError):return None
            before=stamp(anchor.get('before_dispatch_at'))
            groups=[[stamp(v.get('timestamp')) for v in p.get('executions',{}).values()]
                    or [stamp(p.get('broker_filled_at'))] for _,p in relevant]
            executed=[v for group in groups for v in group if v is not None]
            clocks=json.loads(run.context_json or '{}')
            clock_ok=bool(before and groups and all(group and all(v is not None for v in group) for group in groups))
            for name in ('positions_as_of','cash_as_of'):
                initial=stamp(anchor.get('source_times',{}).get(name))
                latest=stamp(clocks.get(name))
                if initial is None or latest is None:clocks_missing=True
                if not (initial and latest and before and executed and initial<=before<=min(executed)
                        and max(executed)<=latest<=datetime.now(timezone.utc)):
                    clock_ok=False
            if not clock_ok:reasons.append('BROKER_FILL_WATERMARK_UNVERIFIED')
        verified=position_ok and cash_ok and not reasons
        progress.update(coverage={"run_id":run.id,"target_quantity":target,
            "position_delta_matched":position_ok,"cash_delta_matched":cash_ok,"watermark_verified":clock_ok,
            "positions_verified":position_ok and clock_ok,"cash_verified":cash_ok and clock_ok,
            "issues":reasons,"basis":"PRETRADE_ACCOUNT_DELTA_WITH_ACTUAL_FEES_AND_BROKER_WATERMARK" if verified else "UNVERIFIED"})
        progress["covered_quantity"]=target if verified else 0
        # A successful official publication with permanently absent evidence
        # stays visibly UNVERIFIED, not a perpetual background source request.
        # Failures never enter this branch; new fills/manual/prepare re-enable.
        if not anchor or (position_ok and cash_ok and clocks_missing) or (cash_ok and 'NETTED_FILLS_REQUIRE_BROKER_WATERMARK' in reasons):
            progress['auto_retry']=False
        session.execute(text("UPDATE order_records SET fill_sync_json=:v WHERE id=:i"),{"i":order_id,"v":encode(progress)})


def lease_expired(run):
    if not run or run['status']!='running':return False
    lease=json.loads(run['context_json'] or '{}').get('lease_until')
    return not lease or lease<=datetime.now(timezone.utc).isoformat()


def sync_state(row, run=None):
    p=decode(row.get("fill_sync_json"))
    qty=int(row["filled_quantity"] or 0)
    if qty<=0:return "NOT_REQUIRED"
    if not p:return "SYNC_UNVERIFIED"
    if p.get("covered_quantity",0)>=qty:return "SYNCED"
    if not run or p.get("attempted_quantity",0)<qty:return "SYNC_PENDING"
    if run["status"]=="running":return "SYNC_PENDING" if lease_expired(run) else "SYNC_RUNNING"
    if run["status"]=="failed":return "SYNC_FAILED"
    proof=p.get("coverage",{})
    return "SYNC_DELAYED" if "POSITION_DELTA_NOT_OBSERVED" in proof.get("issues",[]) else "SYNC_UNVERIFIED"

"""One deterministic, long-only moving-weighted-cost calculation, original units.

    No synthetic opening positions, fee estimates, interest accrual or FX fetch.
"""
from datetime import date
from decimal import Decimal, localcontext
from .sources import number

D = Decimal


def covering(events, imports, key):
    usable=[]
    for r in imports:
        c=r['coverage']
        if c['scope']=='account' or key in c['instrument_keys']:
            usable.append(c)
    intervals=[c for c in usable if c['complete_executions'] and c['corporate_actions_reviewed']]
    def instant(e):return e['executed_at'] if len(e['executed_at'])>10 else e['executed_at']+'T00:00:00+00:00'
    first=min((instant(e) for e in events),default='')
    anchors=[c for c in intervals if key in c['opening_zero_keys'] and c['coverage_start']<=first]
    start=min((c['coverage_start'] for c in anchors),default=None)
    end=start
    if start:
        for c in sorted(intervals,key=lambda c:c['coverage_start']):
            if c['coverage_start']<=end< c['coverage_end']:end=c['coverage_end']
    complete=bool(start and events and all(start<=instant(e)<end for e in events))
    cash_end=start
    if start:
        for c in sorted(usable,key=lambda c:c['coverage_start']):
            if c['complete_cashflows'] and c['coverage_start']<=cash_end<c['coverage_end']:cash_end=c['coverage_end']
    return dict(start=start,end=end,complete=complete,cash_end=cash_end,
        cashflows_complete=bool(complete and cash_end and cash_end>=end))


def attribution(events, imports, valuation=None):
    """Amounts are decimal strings; null means uncomputable, never zero by default."""
    events=sorted(events,key=lambda e:(e['executed_at'],e['external_id']))
    key=events[0]['instrument_key'];currency=next((e['currency'] for e in events if e['kind']=='EXECUTION'),events[0]['currency'])
    coverage=covering(events,imports,key)
    issues=[]
    if not coverage['complete']:issues.append('EXECUTION_HISTORY_OR_OPENING_ZERO_UNPROVEN')
    if not coverage['cashflows_complete']:issues.append('DIVIDEND_FEE_CASHFLOW_COVERAGE_INCOMPLETE')
    executions=[e for e in events if e['kind']=='EXECUTION']
    income=[e for e in events if e['kind']!='EXECUTION']
    corporate=any('CORPORATE_ACTIONS_UNSUPPORTED' in r.get('source_metadata',{}).get('issues',[]) for r in imports)
    if corporate:issues.append('CORPORATE_ACTIONS_UNSUPPORTED')
    unsupported=corporate or any(e['sec_type'] not in {'STK','ETF'} or D(e.get('multiplier','1'))!=1 for e in events)
    if unsupported:issues.append('SECURITY_TYPE_OR_MULTIPLIER_UNSUPPORTED')
    mixed=any(e['currency']!=currency for e in events)
    if mixed:issues.append('MIXED_INSTRUMENT_CURRENCIES')
    # Same-time opposite fills need a broker sequence; arbitrary ID sorting is not evidence.
    stamps={}
    for e in executions:stamps.setdefault(e['executed_at'],set()).add(e['side'])
    if any(len(s)>1 for s in stamps.values()):issues.append('EXECUTION_SEQUENCE_UNPROVEN')
    qty=principal=fee_basis=gross_realized=net_realized=D(0)
    buy_qty=sell_qty=buy_outlay=sell_proceeds=total_fees=D(0)
    costs_known=True
    dates=[]
    for e in executions:
        amount=D(e['quantity'])*D(e['price'])
        fee=None
        if e.get('commission') is None or not e.get('commission_currency') or e.get('taxes') is None:
            costs_known=False;issues.append('ACTUAL_FEES_UNKNOWN')
        elif e['commission_currency']!=currency:
            costs_known=False;issues.append('FEE_CURRENCY_NOT_NATIVE')
        else:fee=D(e['commission'])+D(e['taxes']);total_fees+=fee
        q=D(e['quantity'])
        if e['side']=='BUY':
            buy_qty+=q;qty+=q;principal+=amount;fee_basis+=fee or 0;buy_outlay+=amount+(fee or 0)
            dates.append(e['executed_at'][:10])
        elif qty<q:
            issues.append('SELL_EXCEEDS_KNOWN_LONG_POSITION');qty-=q
        else:
            fraction=q/qty
            allocated=principal*fraction;allocated_fee=fee_basis*fraction
            gross_realized+=amount-allocated
            net_realized+=amount-allocated-allocated_fee-(fee or 0)
            principal-=allocated;fee_basis-=allocated_fee;qty-=q
            sell_qty+=q;sell_proceeds+=amount-(fee or 0)
    eligible=coverage['complete'] and not unsupported and not mixed and not any(i in issues for i in
        ('EXECUTION_SEQUENCE_UNPROVEN','SELL_EXCEEDS_KNOWN_LONG_POSITION'))
    net_eligible=eligible and costs_known
    known_income=sum((D(e['amount']) for e in income if e['currency']==currency),D(0))
    if any(e['currency']!=currency for e in income):issues.append('INCOME_CURRENCY_NOT_NATIVE');net_eligible=False
    dividends=sum((D(e['amount']) for e in income if e['kind']=='DIVIDEND' and e['currency']==currency),D(0))
    mv=terminal=None
    if valuation:
        if D(valuation['quantity'])!=qty:issues.append('POSITION_QUANTITY_RECONCILIATION_MISMATCH')
        elif valuation.get('currency')!=currency:issues.append('POSITION_CURRENCY_UNVERIFIED')
        elif not valuation.get('qualified'):issues.append('VALUATION_SOURCE_TIME_OR_QUALITY_UNVERIFIED')
        elif executions and (valuation['as_of']<executions[-1]['executed_at'] or
                not valuation.get('positions_as_of') or valuation['positions_as_of']<executions[-1]['executed_at']):
            issues.append('VALUATION_OR_POSITION_PRECEDES_FILL')
        elif not coverage['end'] or valuation['as_of']>=coverage['end']:
            issues.append('HISTORY_DOES_NOT_COVER_VALUATION')
        else:
            mv=D(valuation['market_value']);terminal=valuation['as_of'][:10]
    elif eligible and qty==0 and executions:
        mv=D(0);terminal=max(e['executed_at'][:10] for e in events)
    else:issues.append('CURRENT_HOLDING_VALUATION_UNAVAILABLE')
    if mv is None and eligible and qty==0 and executions and 'POSITION_QUANTITY_RECONCILIATION_MISMATCH' not in issues:
        mv=D(0);terminal=max(e['executed_at'][:10] for e in events)
    unrealized=mv-principal-fee_basis if net_eligible and mv is not None else None
    total=(net_realized+unrealized+known_income) if net_eligible and unrealized is not None and coverage['cashflows_complete'] else None
    holding_return=annual=None
    if len(set(dates))>1 or (terminal and any(e['executed_at'][:10]!=terminal for e in income)) or (
            terminal and any(e['side']=='SELL' and e['executed_at'][:10]!=terminal for e in executions)):
        issues.append('MULTIPLE_DATED_CASHFLOWS_MWR_NOT_IMPLEMENTED')
    elif total is not None and buy_outlay>0 and terminal and dates:
        days=(date.fromisoformat(terminal)-date.fromisoformat(dates[0])).days
        if days>0:
            holding_return=total/buy_outlay
            if days>=365 and holding_return>D(-1):
                with localcontext() as ctx:
                    ctx.prec=28
                    annual=(D(1)+holding_return)**(D('365.25')/D(days))-1
            else:issues.append('ANNUALIZATION_REQUIRES_365_DAYS_AND_POSITIVE_END_VALUE')
        else:issues.append('HOLDING_INTERVAL_UNKNOWN_OR_ZERO')
    else:issues.append('HOLDING_RETURN_INPUTS_INCOMPLETE')
    fx_pnl=translated=None
    # Dated original FX for every monetary flow, never today's rate for history.
    if total is not None:
        fx_complete=all(e.get('fx') and e['fx']['rate_date']==e['executed_at'][:10] for e in events)
        current_fx=(valuation or {}).get('fx')
        if mv and not (current_fx and current_fx['rate_date']==terminal):fx_complete=False
        if fx_complete:
            translated=D(0)
            for e in executions:
                value=D(e['quantity'])*D(e['price'])
                fees=D(e['commission'])+D(e['taxes'])
                translated+=((value-fees) if e['side']=='SELL' else -(value+fees))*D(e['fx']['rate'])
            translated+=sum((D(e['amount'])*D(e['fx']['rate']) for e in income),D(0))
            if mv:translated+=mv*D(current_fx['rate'])
            if holding_return is not None:
                reference=D(executions[0]['fx']['rate']);fx_pnl=translated-total*reference
            else:issues.append('MULTI_CASHFLOW_FX_ATTRIBUTION_DEFERRED')
        else:issues.append('HISTORICAL_OR_VALUATION_FX_MISSING')
    reported=[e.get('broker_realized_pnl') for e in executions if e['side']=='SELL']
    broker_realized=sum((D(v) for v in reported),D(0)) if reported and all(v is not None for v in reported) else None
    broker_cost=(valuation or {}).get('broker_cost_basis')
    cost_diff=D(broker_cost)-(principal+fee_basis) if broker_cost is not None and net_eligible else None
    if cost_diff is not None and abs(cost_diff)>D('0.01'):issues.append('BROKER_COST_METHOD_OR_HISTORY_DIFFERENCE')
    realized_diff=broker_realized-net_realized if broker_realized is not None and net_eligible else None
    if realized_diff is not None and abs(realized_diff)>D('0.01'):issues.append('BROKER_REALIZED_METHOD_DIFFERENCE')
    def val(value):return number(value) if value is not None else None
    components=None
    if total is not None:
        by_kind={kind:sum((D(e['amount']) for e in income if e['kind']==kind),D(0))
            for kind in ('DIVIDEND','INCOME_TAX','INTEREST','FEE')}
        parts=dict(price=gross_realized+mv-principal,dividend=by_kind['DIVIDEND'],
            income_tax=by_kind['INCOME_TAX'],interest=by_kind['INTEREST'],other_cashflow=by_kind['FEE'],
            actual_trade_fees=-total_fees)
        components={k:val(v) for k,v in parts.items()}
        components['unexplained_residual']=val(total-sum(parts.values(),D(0)))
    return dict(method='MOVING_WEIGHTED_AVERAGE_LONG_ONLY',currency=currency,
        coverage=coverage,quality='partial' if issues else 'complete',issues=sorted(set(issues)),
        first_observed_buy=dates[0] if dates else None,buy_dates=sorted(set(dates)),
        cumulative_bought=val(buy_qty),cumulative_sold=val(sell_qty),computed_quantity=val(qty),
        current_holding=valuation,attribution_as_of=terminal,cost_basis=val(principal+fee_basis) if net_eligible else None,
        gross_realized_price_pnl=val(gross_realized) if eligible else None,
        realized_pnl=val(net_realized) if net_eligible else None,unrealized_pnl=val(unrealized),
        known_dividends=val(dividends),cash_income_complete=coverage['cashflows_complete'],
        actual_fees_by_currency=_fee_totals(executions),
        total_holding_pnl=val(total),native_attribution=components,holding_period_return=val(holding_return),annualized_return=val(annual),
        profit_on_cumulative_outlay=val(total/buy_outlay) if total is not None and buy_outlay>0 else None,
        return_semantics='profit_on_cumulative_outlay is NOT MWR/TWR; HPR only one initial funding date, no intermediate withdrawal/income; annualization minimum 365 days',
        translated_total_pnl_cny=val(translated),fx_effect_cny=val(fx_pnl),
        fx_method='single-funding first-flow-rate bridge; FX effect includes cross term; null when insufficient evidence',
        broker_reported_realized_pnl=val(broker_realized),broker_reported_cost_basis=broker_cost,
        preferred_realized_pnl=val(broker_realized) if coverage['complete'] and broker_realized is not None else val(net_realized) if net_eligible else None,
        preferred_realized_basis='BROKER_REPORTED_OBSERVED_SALES' if coverage['complete'] and broker_realized is not None else 'SYSTEM_MOVING_WEIGHTED' if net_eligible else 'UNKNOWN',
        realized_reconciliation_difference=val(realized_diff),cost_reconciliation_difference=val(cost_diff),
        unattributed_residual=None if broker_realized is None else val(realized_diff),
        broker_basis='source report authoritative; may be FIFO/tax basis; system result never overwrites it',
        income_basis='only actual cashflow facts; accumulating ETF growth stays in price, never synthetic interest')


def _fee_totals(executions):
    known={};unknown=0
    for e in executions:
        if e.get('commission') is None or not e.get('commission_currency'):unknown+=1
        else:known[e['commission_currency']]=known.get(e['commission_currency'],Decimal(0))+Decimal(e['commission'])
        if e.get('taxes') is None:unknown+=1
        else:known[e['currency']]=known.get(e['currency'],Decimal(0))+Decimal(e['taxes'])
    return dict(known_amounts={k:number(v) for k,v in known.items()},unknown_components=unknown)

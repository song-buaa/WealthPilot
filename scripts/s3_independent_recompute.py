"""Independent stdlib-only S3-A verifier. Does not import WealthPilot services.

    python scripts/s3_independent_recompute.py /path/to/bundle.json
"""
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import sys


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)



def verify_quality(card, assessment):
    """Independent financial arithmetic and evidence-bound floor checks.

    SEC authenticity/semantic qualification remains the original Owner's remit.
    This verifies its frozen projection against quoted qualified raw fields.
    """
    from datetime import datetime
    q=card['quality'];i=q['financial_inputs']
    raw={r['record_id']:r for r in assessment.get('quality_field_records',[])}
    def d(r): return Decimal(r['value'])
    def eq(a,b): assert abs(a-b)<Decimal('.00000002'), 'quality arithmetic differs'
    for fact in i['facts']:
        assert fact['evidence_refs']
        for ref in fact['evidence_refs']:
            r=raw[ref]
            assert (r['field'],r.get('fiscal_period_start'),r['period'],r['scope'])==(fact['field'],fact['start'],fact['period'],fact['scope'])
            eq(d(fact),Decimal(str(r['values'][r['field']])))
    by_key={(f['field'],f['start'],f['period'],f['scope']):f for f in i['facts']}
    for field,row in i['balance'].items():
        assert row==by_key.get((field,None,i['period'],'instant')), 'balance projection differs'
    for period in i['cash_periods']:
        for field,row in period.items():
            if isinstance(row,dict) and 'field' in row:
                assert row==by_key.get((field,period['start'],period['period'],period['scope'])), 'cash projection differs'
        if period.get('cash_conversion'):eq(d(period['cash_conversion']),d(period['cfo'])/d(period['net_income']))
        if period.get('fcf'):eq(d(period['fcf']),d(period['cfo'])-d(period['capex']))
        if period.get('fcf_less_sbc_sensitivity'):eq(d(period['fcf_less_sbc_sensitivity']),d(period['cfo'])-d(period['capex'])-d(period['sbc']))
        if period.get('cash_after_productive_assets'):eq(d(period['cash_after_productive_assets']),d(period['cfo'])-d(period['productive_asset_payments']))
    for row in i['quarters']:
        for field in ('revenue','operating_income'):
            assert row[field]==by_key.get((field,row['start'],row['period'],'quarterly')), 'quarter projection differs'
        eq(d(row['margin']),d(row['operating_income'])/d(row['revenue']))
    roe=q['capital_efficiency']['roe']
    if roe['status'].startswith('CALCULATED'):
        opening=[r for r in i['facts'] if r['field']=='equity' and (datetime.fromisoformat(roe['start'])-datetime.fromisoformat(r['period'])).days==1][0]
        ending=i['balance']['equity']
        eq(d(roe['average_equity']),(d(opening)+d(ending))/2)
        eq(d(roe['ratio']),d(i['latest_cash']['net_income'])/d(roe['average_equity']))
    assert q['normalization']['status']=='UNKNOWN' and q['normalization']['adjustments']==[]
    assert q['capital_efficiency']['roic']['status']=='UNKNOWN'
    floors=q['floors'];b=i['balance'];cash=i['latest_cash']
    usable=q['applicability']['status']=='APPLICABLE_BOUNDED_NONFINANCIAL_SCREEN' and i['fresh'] and not i['rejected_refs'] and not i['conflicts']
    expected={'financial_sustainability':'暂无法判断','profit_cash_support':'暂无法判断','core_business_change':'暂无法判断'}
    major=[]
    if usable:
        fields=['assets','liabilities','equity','current_assets','current_liabilities','cash','cash_including_restricted','noncurrent_debt']
        debt=b.get('current_debt');dr=[debt] if debt else [b.get('current_long_term_debt'),b.get('commercial_paper')]
        if all(b.get(k) for k in fields) and all(dr) and cash:
            a,l,e,ca,cl,c,cr,nd=[d(b[k]) for k in fields];due=sum((d(r) for r in dr),Decimal(0))
            consistent=not debt or not b.get('current_long_term_debt') or d(debt)>=d(b['current_long_term_debt'])
            if debt and b.get('current_long_term_debt') and b.get('commercial_paper'):
                consistent=consistent and d(debt)==d(b['current_long_term_debt'])+d(b['commercial_paper'])
            if consistent and a>0 and l>=0 and ca>=0 and cl>=0 and c>=0 and cr>=c and nd>=0 and abs(a-l-e)<=1:
                expected['financial_sustainability']='通过' if ca>=cl and c>=due and d(cash['cfo'])>0 else '警示'
                if ca<cl and c<due and d(cash['cfo'])<0:major.append('CONFIRMED_CURRENT_LIQUIDITY_SHORTFALL_AND_NEGATIVE_CFO')
        if cash:
            if cash.get('cash_conversion') and d(cash['cfo'])<d(cash['net_income']):expected['profit_cash_support']='警示'
            if all(cash.get(k) for k in ['net_income','operating_income','capex','sbc','repurchases','receivables_change','inventory_change','payables_change','diluted_shares']) and cash.get('fcf') and cash.get('fcf_less_sbc_sensitivity'):
                good=(d(cash['net_income'])>0 and d(cash['operating_income'])>0 and d(cash['diluted_shares'])>0
                      and d(cash['capex'])>=0 and d(cash['sbc'])>=0 and d(cash['repurchases'])>=0
                      and cash.get('cash_conversion') and d(cash['cfo'])>=d(cash['net_income']) and d(cash['cfo'])-d(cash['capex'])-d(cash['sbc'])>0)
                expected['profit_cash_support']='通过' if good else '警示'
        quarters=i['quarters'];obs=[]
        for current in quarters[-2:]:
            prior=next((r for r in quarters if 350<=(datetime.fromisoformat(current['period'])-datetime.fromisoformat(r['period'])).days<=380
                and abs((datetime.fromisoformat(current['period'])-datetime.fromisoformat(current['start'])).days-(datetime.fromisoformat(r['period'])-datetime.fromisoformat(r['start'])).days)<=7),None)
            if prior and d(prior['revenue'])>0:
                obs.append((d(current['revenue'])/d(prior['revenue'])-1,d(current['operating_income'])/d(current['revenue'])-d(prior['operating_income'])/d(prior['revenue']),d(current['operating_income'])))
        continuous=len(quarters)>=2 and (datetime.fromisoformat(quarters[-1]['start'])-datetime.fromisoformat(quarters[-2]['period'])).days==1
        if continuous and quarters[-1]['period']==i['period'] and len(obs)==2:
            expected['core_business_change']='通过' if all(g>=0 and m>=Decimal('-.05') and op>0 for g,m,op in obs) else '警示'
            if all(g<0 and op<0 for g,m,op in obs):major.append('CONFIRMED_TWO_COMPARABLE_QUARTERS_REVENUE_DECLINE_AND_OPERATING_LOSS')
    for key,state in expected.items():assert floors[key]['status']==state, ('quality floor differs',key)
    assert sorted(q['major_risks'])==sorted(major)
    expected_blockers=[k.upper()+'_'+('UNKNOWN' if r['status']=='暂无法判断' else 'WARNING') for k,r in floors.items() if r['status']!='通过']+major
    assert sorted(q['priority_blockers'])==sorted(expected_blockers), 'quality blockers differ'
    for floor in floors.values():
        for ref in floor.get('evidence_refs',[]):assert ref in q['evidence_catalog']
    return len(i['cash_periods'])


def verify(path):
    if not __debug__:
        raise RuntimeError("S3_INDEPENDENT_RECOMPUTE_REQUIRES_ASSERTIONS")
    path = Path(path)
    b = json.loads(path.read_text())
    p = b["payload"]
    assert hashlib.sha256(canonical(p).encode()).hexdigest() == b["artifact_id"], "artifact hash"
    for relative, expected in p["view_hashes"].items():
        view = (path.parent / relative).resolve()
        assert path.parent.resolve() in view.parents, "unsafe view path"
        assert hashlib.sha256(view.read_bytes()).hexdigest() == expected, "view hash"
    for relative, expected in p["source_files"].items():
        source = (path.parent / "sources" / relative).resolve()
        assert (path.parent / "sources").resolve() in source.parents, "unsafe source path"
        assert hashlib.sha256(source.read_bytes()).hexdigest() == expected, "raw source hash"
    checked = 0
    quality_periods = 0
    if p["result"]["method_version"] == "s3-b.quality-evidence.v1":
        for card in p["result"]["cards"]:
            if card.get("quality"):
                quality_periods += verify_quality(card, p["assessments"][card["symbol"]])
    with localcontext() as context:
        context.prec = 40
        for card in p["result"]["cards"]:
            if not card.get("scenarios"):
                continue
            eps = Decimal(str(card["cross_checks"]["diluted_eps_ttm"]["values"]["diluted_eps_ttm"]))
            price = Decimal(str(card["cross_checks"]["observed_price"]["values"]["observed_price"]))
            cases = [(name, card["assumptions"][name], card["assumptions"]["required_return"], result)
                     for name, result in card["scenarios"].items()]
            cases += [("sensitivity", s["parameters"], s["required_return"], s["result"])
                      for s in card["sensitivity"] if s["result"]]
            for label, params, rate, result in cases:
                g, dilution, multiple, payout = [Decimal(params[k]) for k in
                    ("earnings_growth", "annual_share_change", "terminal_pe", "dividend_payout")]
                rate = Decimal(rate)
                dividend_cash = Decimal(0)
                present = Decimal(0)
                for t in range(1, 6):
                    e = eps * ((1 + g) / (1 + dilution)) ** t
                    dividend = e * payout
                    dividend_cash += dividend
                    present += dividend / (1 + rate) ** t
                    for field, expected in (("eps", e), ("dividend", dividend),
                                            ("relative_shares", (1 + dilution) ** t),
                                            ("earnings_index", (1 + g) ** t)):
                        assert abs(Decimal(result["years"][t - 1][field]) - expected) < Decimal(".00000002"), (card["symbol"], label, field)
                terminal = e * multiple
                wealth = terminal + dividend_cash
                present += terminal / (1 + rate) ** 5
                implied_eps = price * (1 + rate) ** 5 / multiple
                expected = dict(terminal_price=terminal, dividends_cash=dividend_cash, terminal_wealth=wealth,
                                total_return=wealth / price - 1,
                                annualized_return=(wealth / price) ** (Decimal(1) / 5) - 1,
                                conditional_value=present, margin_to_conditional_value=1 - price / present,
                                implied_terminal_eps_no_dividends=implied_eps,
                                implied_earnings_growth_no_dividends=(implied_eps / eps) ** (Decimal(1) / 5) * (1 + dilution) - 1)
                for field, value in expected.items():
                    assert abs(value - Decimal(result[field])) < Decimal(".00000002"), (card["symbol"], label, field)
                checked += 1
        # Recompute the disclosed classification and tie ranks without importing
        # its implementation; this detects fabricated ranking/priority fields.
        for group in ("优先考虑", "继续观察", "暂不优先"):
            ordered = []
            for c in p["result"]["cards"]:
                if not c.get("scenarios"):
                    assert c["group"] == "暂不优先" and c["rank"] is None
                    continue
                good = Decimal(c["scenarios"]["base"]["annualized_return"]) >= Decimal(c["assumptions"]["required_return"])
                downside = Decimal(c["scenarios"]["cautious"]["total_return"]) >= Decimal(str(p["request"].get("downside_floor", "-.1")))
                robust=bool(c.get('sensitivity')) and all(v.get('result') and Decimal(v['result']['annualized_return'])>=Decimal(v['required_return']) for v in c.get('sensitivity',[]))
                is_b=p['result']['method_version']=='s3-b.quality-evidence.v1'
                expected_group = ("暂不优先" if not good or not downside or (is_b and c['quality']['major_risks']) else "继续观察"
                                  if c["quality"]["priority_blockers"] or (c["high_sensitivity"] and not (is_b and robust)) else "优先考虑")
                assert c["group"] == expected_group
                if expected_group == group:
                    ordered.append(c)
            ordered.sort(key=lambda c: (-Decimal(c["scenarios"]["base"]["annualized_return"]),
                                        -Decimal(c["scenarios"]["cautious"]["total_return"]), c["symbol"]))
            anchor = None
            downside_anchor = None
            evidence_anchor = None
            rank = 0
            for i, c in enumerate(ordered, 1):
                r = Decimal(c["scenarios"]["base"]["annualized_return"])
                downside = Decimal(c["scenarios"]["cautious"]["total_return"])
                evidence = tuple(v["status"] for v in c["quality"]["floors"].values())
                if (anchor is None or anchor - r > Decimal(str(p["request"].get("tie_band", ".02")))
                        or abs(downside_anchor - downside) > Decimal(str(p["request"].get("downside_tie_band", ".05")))
                        or evidence != evidence_anchor):
                    anchor, downside_anchor, evidence_anchor, rank = r, downside, evidence, i
                assert c["rank"] == rank, "ranking differs"
    return dict(artifact_id=b["artifact_id"], independent=True, source_hashes_verified=len(p["source_files"]),
                scenario_and_sensitivity_cases=checked, quality_cash_periods_verified=quality_periods, calculations_and_base_ranks="PASS")


if __name__ == "__main__":
    print(json.dumps(verify(sys.argv[1]), ensure_ascii=False, indent=2))

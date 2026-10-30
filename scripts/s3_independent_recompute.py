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


def verify(path):
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
                expected_group = ("暂不优先" if not good or not downside else "继续观察"
                                  if c["quality"]["priority_blockers"] or c["high_sensitivity"] else "优先考虑")
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
                scenario_and_sensitivity_cases=checked, calculations_and_base_ranks="PASS")


if __name__ == "__main__":
    print(json.dumps(verify(sys.argv[1]), ensure_ascii=False, indent=2))

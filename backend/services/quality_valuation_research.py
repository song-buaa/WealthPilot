"""Small offline S3 artifact adapter, not a database/Agent/Provider runtime.

Reuses Evidence.assess, financial_research.build and E1's canonical serializer.
Never opens E1 or the business database. Explicit output saves only S3 research
artifacts, not a human investment decision or an execution instruction.
"""
import hashlib
import gzip
import json
import os
from pathlib import Path
import shutil
import tempfile
import zlib

from backend.services.investment_thesis.contracts import canonical
from backend.services.market_data.evidence import assess, stamp
from backend.services.market_data.quality_valuation import ResearchRequest, compare

ROOT = Path(__file__).resolve().parents[2]
METHOD_FILES = (
    "backend/services/market_data/quality_valuation.py",
    "backend/services/market_data/quality_evaluation.py",
    "backend/services/quality_valuation_research.py",
    "backend/services/market_data/evidence.py",
    "backend/services/market_data/financial_research.py",
    "backend/services/market_data/av_fundamentals_service.py",
    "backend/services/market_data/technical_evidence.py",
    "research_v2/sec_disclosures.py", "research_v2/official_disclosures.py",
    "backend/services/instruments/issuers.py",
    "backend/services/investment_thesis/contracts.py",
    "scripts/s3_independent_recompute.py",
)


# Exact previously accepted P1 Owner versions. The pinned historical manifest is
# retained as a receipt; current Owner requalification is still compared below.
P1_OWNER_HASHES = {
    'backend/services/market_data/evidence.py':'7a1605f39e26655930e9ad7324abb36636926f42bc24f1b770869c52b0cadc79',
    'backend/services/market_data/financial_research.py':'b785e84ed28b72f9ae0fb6528867505fb3d35f4ac5f30f89b02067ad4051bba4',
    'research_v2/sec_disclosures.py':'20dc9cb60d1c86aa78ecf6f8540101a76509a5a4888b1dcd0985c4ccf98f63ae',
}


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def method_hashes():
    return {name: file_hash(ROOT / name) for name in METHOD_FILES}


def safe_file(root, relative):
    root = Path(root).resolve()
    path = root / relative
    if path.is_symlink() or root not in path.resolve().parents or not path.is_file():
        raise ValueError("SOURCE_FILE_MISSING_OR_OUTSIDE_ROOT")
    return path


def _load(root, name, expected=None):
    path = safe_file(root, name)
    if expected and file_hash(path) != expected:
        raise ValueError("SOURCE_HASH_MISMATCH")
    return json.loads(path.read_text())


def frozen_inputs(root, request):
    """Consume the user-pinned P1 receipt, requalify with the original Owner.

    Raw source bytes and receipt clocks remain unchanged. No network or collector
    is invoked. Historical replay is at the frozen decision_at, never 'now'.
    A hash is integrity, not a new proof of source authenticity.
    """
    request = ResearchRequest.model_validate(request)
    manifest = _load(root, "P1_ACCEPTANCE_MANIFEST.json", request.source_manifest_sha256)
    if stamp(manifest["decision_at"]) != request.decision_at:
        raise ValueError("P1_SHARED_DECISION_CLOCK_MISMATCH")
    for name, expected in manifest["method_files"].items():
        if name not in METHOD_FILES or (file_hash(ROOT / name) != expected and P1_OWNER_HASHES.get(name) != expected):
            raise ValueError("P1_OWNER_METHOD_VERSION_MISMATCH")
    requests = _load(root, "SEC_ONLY_HANDOFF/requests.json")
    files = {"p1/P1_ACCEPTANCE_MANIFEST.json": safe_file(root, "P1_ACCEPTANCE_MANIFEST.json"),
             "p1/P1_CORPORATE_ACTIONS.json": safe_file(root, "P1_CORPORATE_ACTIONS.json"),
             "sec/requests.json": safe_file(root, "SEC_ONLY_HANDOFF/requests.json")}
    inputs, errors = {}, {}
    for symbol in request.companies:
        ticker = symbol.removesuffix(":US")
        company_files = {}
        try:
            meta = manifest["symbols"][ticker]
            values = {}
            for kind in ("assessment", "derived", "sec-records", "bars"):
                name = f"{ticker}-p1-{kind}.json"
                values[kind] = _load(root, name, meta[kind + "_sha256"])
                company_files["p1/" + name] = safe_file(root, name)
            price_name = f"{ticker}-compact-price-original.json"
            price = _load(root, price_name, meta["price_new_raw_sha256"])
            price_receipt_name = f"{ticker}-compact-price-receipt.json"
            receipt = _load(root, price_receipt_name)
            if (receipt["raw_sha256"] != meta["price_new_raw_sha256"]
                    or receipt["finished_at"] != meta["price_new_fetched_at"]
                    or stamp(receipt["finished_at"]) > request.decision_at):
                raise ValueError("PRICE_RECEIPT_CLOCK_OR_HASH_MISMATCH")
            for name in (price_name, price_receipt_name):
                company_files["p1/" + name] = safe_file(root, name)
            old_price = safe_file(root, f"price-historical-frozen/{ticker}.json.gz")
            old_receipt = safe_file(root, f"price-historical-frozen/{ticker}.json")
            if hashlib.sha256(gzip.decompress(old_price.read_bytes())).hexdigest() != meta["price_old_raw_sha256"]:
                raise ValueError("HISTORICAL_PRICE_RAW_HASH_MISMATCH")
            company_files[f"price-historical-frozen/{ticker}.json.gz"] = old_price
            company_files[f"price-historical-frozen/{ticker}.json"] = old_receipt
            for item in requests:
                if item.get("symbol") == symbol and "path" in item:
                    source = safe_file(Path(root) / "SEC_ONLY_HANDOFF", item["path"])
                    if file_hash(source) != item["sha256"] or stamp(item.get("fetched_at")) is None or stamp(item["fetched_at"]) > request.decision_at:
                        raise ValueError("RAW_SEC_HASH_OR_CLOCK_MISMATCH")
                    company_files["sec/" + item["path"]] = source
            original = values["assessment"]
            if original["symbol"] != symbol or stamp(original["as_of"]) != request.decision_at:
                raise ValueError("P1_IDENTITY_OR_CLOCK_MISMATCH")
            source_records = {row["record_id"]: row for row in values["sec-records"]}
            market = {row["kind"]: row["values"] for row in original["records"] if row["kind"] in {"technical", "quote", "fundamentals", "capital_flow"}}
            checked = assess(market, symbol=symbol, research=list(source_records.values()),
                             disclosure_verifier=source_records.get, at=request.decision_at)
            # Ensure even a modified supplied assessment cannot elevate a field.
            for key in ("verified_field_records", "historical_field_records", "technical_levels"):
                if digest(checked[key]) != digest(original[key]):
                    raise ValueError("OWNER_REQUALIFICATION_DIFFERENCE")
            day = checked.get("technical_levels", {}).get("day", {})
            close = price["Time Series (Daily)"][meta["price_observed_date"]]["4. close"]
            if (not day.get("bars") or float(day["bars"][-1]["close"]) != float(close)
                    or day["end_at"] != meta["price_observed_date"] or day["currency"] != "USD"):
                raise ValueError("QUALIFIED_PRICE_RAW_CLOSURE_MISMATCH")
            # Explicit opt-in quality projection from the same frozen raw SEC
            # materials via original identity/parser/Registry Owners, no fetching.
            from research_v2.sec_disclosures import DIRECTORY, discover_filings, parse_companyfacts
            from backend.services.instruments.issuers import resolve_sec_issuer
            from backend.services.market_data.evidence import assess_quality_facts
            raw = {item['url']: item for item in requests if (item.get('symbol')==symbol or item.get('url')==DIRECTORY) and 'path' in item}
            def source(url):
                item = raw[url]
                path = safe_file(Path(root)/'SEC_ONLY_HANDOFF',item['path'])
                if file_hash(path)!=item['sha256'] or not stamp(item.get('fetched_at')) or stamp(item['fetched_at'])>request.decision_at:
                    raise ValueError('QUALITY_RAW_HASH_OR_CLOCK_MISMATCH')
                company_files['sec/'+item['path']] = path
                return path.read_bytes(), item['fetched_at']
            directory = source(DIRECTORY)
            cik = next(row['source_verification']['issuer_identity']['cik'] for row in source_records.values()
                       if row.get('source_verification',{}).get('issuer_identity'))
            submissions = source('https://data.sec.gov/submissions/CIK'+cik+'.json')
            issuer = resolve_sec_issuer(symbol,json.loads(directory[0]),json.loads(submissions[0]))
            if not issuer or issuer['issuer_type']!='domestic':
                raise ValueError('QUALITY_US_GAAP_DOMESTIC_SCOPE_REQUIRED')
            filings = discover_filings(issuer,json.loads(submissions[0]))
            quality_records = parse_companyfacts(source('https://data.sec.gov/api/xbrl/companyfacts/CIK'+cik+'.json'),
                issuer,filings,identity_digest=hashlib.sha256(directory[0]+submissions[0]).hexdigest(),quality=True)
            quality_by_id = {r['record_id']:r for r in quality_records}
            checked['quality_scope'] = dict(issuer_type=issuer['issuer_type'], sic=json.loads(submissions[0]).get('sic'),
                source_url=issuer['submissions_url'], source_document_sha256=hashlib.sha256(submissions[0]).hexdigest(), fetched_at=submissions[1])
            checked['quality_field_records'] = assess_quality_facts(quality_records, symbol=symbol,
                at=request.decision_at, disclosure_verifier=quality_by_id.get)
            inputs[symbol] = checked
            files.update(company_files)
        except (ValueError, KeyError, TypeError, OSError, EOFError, zlib.error, json.JSONDecodeError) as error:
            safe = str(error)
            errors[symbol] = (safe if type(error) is ValueError and safe.isupper()
                              and safe.replace("_", "").isalnum() else "FROZEN_COMPANY_SOURCE_OR_QUALIFICATION_REJECTED")
    return inputs, errors, files


def make_bundle(root, raw_request):
    request = ResearchRequest.model_validate(raw_request)
    assessments, failures, files = frozen_inputs(root, request)
    files.update({"methods/" + name: ROOT / name for name in METHOD_FILES})
    payload = dict(request=request.model_dump(mode="json"), assessments=assessments,
                   input_failures=failures, method_files=method_hashes(),
                   source_files={name: file_hash(path) for name, path in sorted(files.items())},
                   result=compare(request, assessments))
    _project_source_failures(payload["result"], failures)
    payload["view_hashes"] = view_hashes(payload["result"])
    return dict(artifact_id=digest(payload), payload=payload), files


def _project_source_failures(result, failures):
    """Same frozen failure projection for initial run and historical replay."""
    for card in result["cards"]:
        if card["symbol"] in failures:
            card["source_failure"] = failures[card["symbol"]]


def markdown(result):
    lines = ["# S3 公司池质量与估值研究", "", f"决策截止：{result['decision_at']}；方法：{result['method_version']}", "",
             "Research Only：四底线按版本化财务/经营证据规则逐项判断；正向路径可达，UNKNOWN或警示仍保留对应blocker，重大风险不可由回报抵消。", "",
             "公司自身研究；配置优先级 UNKNOWN。1–20家仅为输入合同容量，真实验收仅限冻结AAPL/NVDA历史样例。", "",
             "情景参数、10%要求回报示例、−10%谨慎累计回报下限及并列阈值均为研发参数，不是正式Investment Policy。", "",
             "| 公司 | 四底线 数据/持续性/现金/核心业务 | 分档 | 档内名次 | 基准年化 | 谨慎五年回报 | 条件价值区间 USD | 排序稳定 |", "|---|---|---|---|---|---|---|---|"]
    for card in result["cards"]:
        s = card.get("scenarios", {})
        floors = card.get("quality", {}).get("floors", {})
        states = "/".join(floors[k]["status"] for k in (
            "data_trust", "financial_sustainability", "profit_cash_support", "core_business_change") if k in floors) or "暂无法判断"
        base = s.get("base", {}).get("annualized_return", "UNKNOWN")
        cautious = s.get("cautious", {}).get("total_return", "UNKNOWN")
        lines.append(f"| {card['symbol']} | {states} | {card['group']} | {card['rank']} | {base} | {cautious} | {card.get('conditional_value_range', 'UNKNOWN')} | {'稳定' if not card['ranking_unstable'] else '排序不稳'} |")
    lines += ["", "回报值为小数（0.10代表10%）。条件价值是基于研究情景及要求回报的折现条件价格，不是客观公允价值或完整DCF。", "",
              "质量证据不足、一般警示、研发阈值未满足与已证实的经营恶化分别表达。通过仅限观察窗口，不保证长期竞争优势或无持续经营风险。", "",
              "五年EPS按整体盈利增长和相对股数变化推演，期末乘假设PE；分红按各年EPS×派息率计算，不再叠加回购收益。分红持有现金不再投资。", "",
              "## 逐公司判断卡", ""]
    for card in result["cards"]:
        lines += [f"### {card['symbol']}", "", card["rank_reason"], "",
                  "```json", json.dumps(card, ensure_ascii=False, sort_keys=True, indent=2), "```", ""]
    return "\n".join(lines)


def views(result):
    return {"comparison.md": markdown(result), **{
        "cards/" + card["symbol"].replace(":", "_") + ".json": canonical(card) + "\n"
        for card in result["cards"]}}


def view_hashes(result):
    return {name: hashlib.sha256(body.encode()).hexdigest() for name, body in views(result).items()}


def save_bundle(destination, bundle, source_files):
    """Content-addressed immutable directory; atomic rename and idempotent replay.

    Existing SQL repositories require business DB or E1 decision writes. S3-A
    therefore stores an explicit local research artifact, without changing either
    schema. Reuses canonical serialization, not their write workflows.
    """
    bundle = json.loads(canonical(bundle))  # Normalize tuples as JSON arrays before idempotent equality.
    if (digest(bundle["payload"]) != bundle["artifact_id"]
            or bundle["payload"].get("view_hashes") != view_hashes(bundle["payload"]["result"])):
        raise ValueError("INVALID_ARTIFACT_OR_VIEW_HASH")
    destination = Path(destination).absolute()
    if destination.is_symlink():
        raise ValueError("ARCHIVE_SYMLINK_REJECTED")
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / bundle["artifact_id"]
    if target.is_symlink():
        raise ValueError("ARTIFACT_SYMLINK_REJECTED")
    if target.exists():
        if read_bundle(target / "bundle.json") != bundle:
            raise ValueError("IMMUTABLE_ARTIFACT_CONFLICT")
        return target
    stage = Path(tempfile.mkdtemp(prefix=".s3-", dir=destination))
    try:
        (stage / "bundle.json").write_text(canonical(bundle) + "\n")
        for name, body in views(bundle["payload"]["result"]).items():
            view = stage / name
            view.parent.mkdir(parents=True, exist_ok=True)
            view.write_text(body)
        for name, path in source_files.items():
            out = stage / "sources" / name
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, out)
            if file_hash(out) != bundle["payload"]["source_files"][name]:
                raise ValueError("SOURCE_CHANGED_DURING_FREEZE")
        try:
            os.rename(stage, target)
        except OSError:
            if not target.exists() or read_bundle(target / "bundle.json") != bundle:
                raise
        return target
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def read_bundle(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("ARTIFACT_SYMLINK_REJECTED")
    bundle = json.loads(path.read_text())
    if digest(bundle["payload"]) != bundle["artifact_id"]:
        raise ValueError("ARTIFACT_HASH_MISMATCH")
    for name, expected in bundle["payload"]["source_files"].items():
        if file_hash(safe_file(path.parent / "sources", name)) != expected:
            raise ValueError("ARCHIVED_SOURCE_HASH_MISMATCH")
    for name, expected in bundle["payload"]["view_hashes"].items():
        if file_hash(safe_file(path.parent, name)) != expected:
            raise ValueError("ARCHIVED_VIEW_HASH_MISMATCH")
    return bundle


def replay(path):
    bundle = read_bundle(path)
    p = bundle["payload"]
    if p["method_files"] != method_hashes():
        raise ValueError("METHOD_DRIFT_REPLAY_REQUIRES_ORIGINAL_VERSION")
    recalculated = compare(p["request"], p["assessments"])
    _project_source_failures(recalculated, p.get("input_failures", {}))
    if digest(recalculated) != digest(p["result"]):
        raise ValueError("RECOMPUTATION_DIFFERENCE")
    return dict(artifact_id=bundle["artifact_id"], verified=True, result_sha256=digest(recalculated))


def difference(old_path, new_path):
    old, new = read_bundle(old_path)["payload"], read_bundle(new_path)["payload"]
    return dict(data_changed=digest(old["assessments"]) != digest(new["assessments"]),
                assumptions_changed=old["request"]["companies"] != new["request"]["companies"],
                method_changed=old["method_files"] != new["method_files"],
                pool_changed=list(old["request"]["companies"]) != list(new["request"]["companies"]),
                before=[{k: c[k] for k in ("symbol", "group", "rank")} for c in old["result"]["cards"]],
                after=[{k: c[k] for k in ("symbol", "group", "rank")} for c in new["result"]["cards"]])

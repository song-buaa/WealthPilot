"""全能行原生文本批次。只解析/校验，不负责 GUI、网络或数据库写入。"""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import csv
import hashlib
import io
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

FILES = {"stock": "stock.txt", "hgt": "hgt.xls", "sgt": "sgt.xls"}
HK_COLUMNS = ("证券名称", "证券代码", "交易市场", "当前数量", "股份数量", "可用股份",
              "参考成本价(港币)", "最新价(港币)", "持仓成本(人民币)", "最新市值(人民币)",
              "参考盈亏(人民币)", "参考盈亏比例(%)", "冻结数量", "在途买入", "在途卖出")
STOCK_COLUMNS = ("证券代码", "证券名称", "股票余额", "可用余额", "冻结数量", "盈亏", "成本价",
                 "盈亏比例()", "市价", "市值", "成本金额", "市场代码", "交易市场", "股东帐户",
                 "实际数量", "单位数量", "资讯", "历史成交")
ERRORS = {"NATIVE_BATCH_INCOMPLETE", "NATIVE_BATCH_INVALID", "NATIVE_ACCOUNT_UNPROVEN",
          "NATIVE_RECONCILIATION_FAILED", "NATIVE_BATCH_STALE", "NATIVE_LEGACY_REVIEW_REQUIRED"}


def require(condition, code="NATIVE_BATCH_INVALID"):
    if not condition:
        raise ValueError(code)


def number(value):
    try:
        result = Decimal(str(value).strip())
        require(result.is_finite())
        return result
    except (InvalidOperation, TypeError):
        raise ValueError("NATIVE_BATCH_INVALID") from None


def stamp(value):
    try:
        result = datetime.fromisoformat(value)
        require(result.tzinfo is not None)
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError):
        raise ValueError("NATIVE_BATCH_INVALID") from None


def sha(data):
    return hashlib.sha256(data).hexdigest()


def source_times(metadata):
    """Ordering clock; observed action time is explicitly NOT broker valuation time."""
    return metadata.get("query_action_at") or metadata["queried_at"]


def private_json(path):
    from app.shared_sqlite import private_file
    try:
        value = json.loads(private_file(path, 0o600).read_text())
        require(isinstance(value, dict))
        return value
    except (OSError, ValueError):
        raise ValueError("NATIVE_BATCH_INVALID") from None


def batch_bytes(path):
    from app.shared_sqlite import private_file
    try:
        file = private_file(path, 0o600)
        require(0 < file.stat().st_size <= 2*1024*1024, "NATIVE_BATCH_INCOMPLETE")
        return file.read_bytes()
    except FileNotFoundError:
        raise ValueError("NATIVE_BATCH_INCOMPLETE") from None


def read_config(path):
    config = private_json(path)
    require(config.get("schema") == "guojin.native.v1")
    require(type(config.get("portfolio_id")) is int and config["portfolio_id"] > 0)
    require(isinstance(config.get("account_id"), str) and config["account_id"].strip(), "NATIVE_ACCOUNT_UNPROVEN")
    require(isinstance(config.get("shareholder_accounts"), list) and config["shareholder_accounts"], "NATIVE_ACCOUNT_UNPROVEN")
    root = Path(config.get("inbox", ""))
    require(root.is_absolute() and root.is_dir() and root.resolve() == root)
    require(root.stat().st_uid == __import__("os").getuid() and root.stat().st_mode & 0o077 == 0)
    return config


def _table(lines, expected):
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter="\t", strict=True))
    require(bool(rows), "NATIVE_BATCH_INCOMPLETE")
    def trim(row):
        # Native exports have a single empty trailing column.
        return row[:-1] if row and row[-1] == "" else row
    require(tuple(trim(rows[0])) == expected)
    result = []
    for row in rows[1:]:
        if not row or all(not value.strip() for value in row):
            continue
        row = trim(row)
        require(len(row) == len(expected))
        result.append(dict(zip(expected, row)))
    return result


@dataclass(frozen=True)
class ParsedExports:
    rows: list
    cash: Decimal
    securities_value: Decimal
    total_assets: Decimal
    available: Decimal
    counts: dict
    shareholders: frozenset
    saved_at: str
    reconciliation: dict


def reconcile_assets(securities_value, cash, reported_securities, total_assets):
    """Keep both broker facts; valuation tolerance never proves source completeness."""
    require(abs(reported_securities + cash - total_assets) <= Decimal("0.02"),
            "NATIVE_RECONCILIATION_FAILED")
    detail_total = securities_value + cash
    difference = detail_total - total_assets
    securities_difference = securities_value - reported_securities
    absolute = max(abs(difference), abs(securities_difference))
    ratio = absolute / abs(total_assets) if total_assets else None
    exact = absolute == 0
    warning = total_assets > 0 and absolute <= Decimal("2000") and ratio <= Decimal("0.005")
    require(exact or warning, "NATIVE_RECONCILIATION_FAILED")
    return dict(status="PASS" if exact else "WARNING", currency="CNY",
        details_securities_cny=str(securities_value), cash_balance_cny=str(cash),
        details_total_cny=str(detail_total), reported_securities_cny=str(reported_securities),
        account_total_assets_cny=str(total_assets), difference_cny=str(difference),
        securities_difference_cny=str(securities_difference), absolute_difference_cny=str(absolute),
        difference_ratio=str(ratio) if ratio is not None else None,
        max_ratio="0.005", max_absolute_cny="2000", policy="guojin.valuation-difference.v1")


def parse_exports(contents):
    """Actual native headers, strict GB18030. No owner/date is invented here."""
    require(set(contents) == set(FILES), "NATIVE_BATCH_INCOMPLETE")
    try:
        texts = {key:data.decode("gb18030", errors="strict").splitlines() for key,data in contents.items()}
        stock = texts["stock"]
        require(stock[0] == "标题：资金股票")
        header = next(i for i,line in enumerate(stock) if line.startswith("证券代码\t"))
        end = next(i for i in range(header+1,len(stock)) if stock[i].startswith("----------------"))
        saved = [line.removeprefix("保存时间：").strip() for line in stock[end+1:] if line.startswith("保存时间：")]
        require(len(saved) == 1)
        datetime.strptime(saved[0], "%Y-%m-%d  %H:%M:%S")  # export time, NEVER a quote/query time
        fields = {}
        for line in stock[:header]:
            for part in line.split("\t"):
                if "：" in part:
                    k,v = part.split("：",1)
                    k = re.sub(r"\s", "", k)
                    if k in {"资金余额", "股票市值", "总资产", "可用余额", "冻结金额", "可取金额"}:
                        require(k not in fields)
                        fields[k] = number(v)
        require(set(fields) == {"资金余额", "股票市值", "总资产", "可用余额", "冻结金额", "可取金额"})
        tables = {"stock":_table(stock[header:end], STOCK_COLUMNS),
                  "hgt":_table(texts["hgt"], HK_COLUMNS), "sgt":_table(texts["sgt"], HK_COLUMNS)}
        rows, seen, shareholders = [], set(), set()
        for channel, table in tables.items():
            for raw in table:
                code = raw["证券代码"].strip()
                match = re.fullmatch(r'=\"(\d{5})\"', code)
                if match:
                    code = match[1]
                is_hk = channel != "stock"
                require(bool(re.fullmatch(r"\d{5}" if is_hk else r"\d{6}", code)))
                if is_hk:
                    require(raw["交易市场"] == {"hgt":"沪HK", "sgt":"深HK"}[channel])
                    qty = number(raw["当前数量"])
                    require(qty == number(raw["股份数量"]))
                    value, cost = number(raw["最新市值(人民币)"]), number(raw["持仓成本(人民币)"])
                    pnl, ratio = number(raw["参考盈亏(人民币)"]), number(raw["参考盈亏比例(%)"])/100
                    quote, quote_cost = number(raw["最新价(港币)"]), number(raw["参考成本价(港币)"])
                    available = number(raw["可用股份"])
                    require(all(number(raw[k]) >= 0 for k in ("冻结数量", "在途买入", "在途卖出")))
                    market, quote_currency = "HK", "HKD"
                else:
                    market = {"上海Ａ股":"SH", "深圳Ａ股":"SZ"}.get(raw["交易市场"])
                    require(market is not None)
                    require(raw["市场代码"] == {"SH":"2", "SZ":"1"}[market])
                    qty = number(raw["股票余额"])
                    require(qty == number(raw["实际数量"]) and number(raw["单位数量"]) == 1)
                    require(bool(raw["股东帐户"].strip()), "NATIVE_ACCOUNT_UNPROVEN")
                    shareholders.add(raw["股东帐户"].strip())
                    value, cost = number(raw["市值"]), number(raw["成本金额"])
                    pnl, ratio = number(raw["盈亏"]), number(raw["盈亏比例()"])/100
                    quote, quote_cost = number(raw["市价"]), number(raw["成本价"])
                    available, quote_currency = number(raw["可用余额"]), "CNY"
                    require(number(raw["冻结数量"]) >= 0)
                require(qty >= 0 and available >= 0 and available <= qty and value >= 0 and quote >= 0)
                require(qty > 0 or value == 0)
                identity = (channel, code, market)
                require(identity not in seen, "DUPLICATE_SNAPSHOT_IDENTITY")
                seen.add(identity)
                # No raw owner/name of the shareholder is copied into Snapshot raw_data.
                rows.append(dict(channel=channel, code=code, market=market, name=raw["证券名称"],
                    quantity=qty, available_quantity=available, market_value=value, cost_basis=cost,
                    unrealized_pnl=pnl, unrealized_pnl_pct=ratio, quote=quote, quote_cost=quote_cost,
                    quote_currency=quote_currency, valuation_currency="CNY"))
        value = sum((row["market_value"] for row in rows), Decimal(0))
        reconciliation = reconcile_assets(value, fields["资金余额"], fields["股票市值"], fields["总资产"])
        return ParsedExports(rows, fields["资金余额"], value, fields["总资产"], fields["可用余额"],
            {k:len(v) for k,v in tables.items()}, frozenset(shareholders), saved[0], reconciliation)
    except (UnicodeError, csv.Error, KeyError, IndexError, StopIteration, TypeError):
        raise ValueError("NATIVE_BATCH_INVALID") from None


def load_batch(config, *, candidate=None):
    """Operator's private receipt binds otherwise ownerless HK text to the GUI query.

    It is provenance from the existing exporter, not proof supplied by the XLS.
    A process running as this OS user is already inside the trusted boundary.
    """
    root = Path(config["inbox"])
    # The existing producer validates a sealed candidate BEFORE switching ready.
    # Normal sync callers continue to consume only the ready pointer.
    name = candidate if candidate is not None else private_json(root/"ready.json").get("batch")
    require(isinstance(name,str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name))
    directory = root/name
    require(directory.is_dir() and directory.resolve() == directory)
    receipt_bytes = batch_bytes(directory/"batch.json")
    receipt = json.loads(receipt_bytes)
    observed_source = receipt.get("schema") == "guojin.native-batch.v2"
    require(observed_source or receipt.get("schema") == "guojin.native-batch.v1")
    require((receipt.get("portfolio_id"),receipt.get("account_id")) ==
            (config["portfolio_id"],config["account_id"]), "NATIVE_ACCOUNT_UNPROVEN")
    require(set(receipt.get("files", {})) == set(FILES), "NATIVE_BATCH_INCOMPLETE")
    data, queried, actions = {}, {}, {}
    for key, filename in FILES.items():
        evidence = receipt["files"][key]
        require(evidence.get("account_id") == config["account_id"] and
                evidence.get("account_verified_in_gui") is True and
                isinstance(evidence.get("evidence_ref"),str) and bool(evidence["evidence_ref"].strip()), "NATIVE_ACCOUNT_UNPROVEN")
        if observed_source:
            require(evidence.get("complete") is False and evidence.get("counter_status") == "UNKNOWN"
                    and evidence.get("row_count_basis") == "EXPORTED_ROWS")
            require(evidence.get("queried_at") is None and evidence.get("source_time_quality") == "UNKNOWN")
            actions[key] = stamp(evidence.get("query_action_at"))
            observed, exported = stamp(evidence.get("observed_at")), stamp(evidence.get("exported_at"))
            require(actions[key] <= observed <= exported <= actions[key] + timedelta(minutes=15), "NATIVE_BATCH_STALE")
            proof = evidence.get("query_evidence", {})
            require(proof.get("page_verified") is True and proof.get("account_verified") is True
                    and proof.get("readonly_action_verified") is True
                    and all(type(proof.get(k)) is int and proof[k] > 0 for k in ("pid", "hwnd", "dpi")),
                    "NATIVE_ACCOUNT_UNPROVEN")
        else:
            queried[key] = stamp(evidence.get("queried_at"))
        data[key] = batch_bytes(directory/filename)
        require(0 < len(data[key]) <= 2*1024*1024, "NATIVE_BATCH_INCOMPLETE")
        require(sha(data[key]) == evidence.get("sha256"))
    now = datetime.now(timezone.utc)
    clocks = actions if observed_source else queried
    require(max(clocks.values())-min(clocks.values()) <= timedelta(minutes=15))
    require(all(-60 <= (now-t).total_seconds() <= 86400 for t in clocks.values()), "NATIVE_BATCH_STALE")
    parsed = parse_exports(data)
    exported = datetime.strptime(parsed.saved_at, "%Y-%m-%d  %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    require(-60 <= (exported-clocks["stock"]).total_seconds() <= 900, "NATIVE_BATCH_STALE")
    require(parsed.shareholders <= set(config["shareholder_accounts"]), "NATIVE_ACCOUNT_UNPROVEN")
    for key in FILES:
        require(type(receipt["files"][key].get("row_count")) is int and
                receipt["files"][key]["row_count"] == parsed.counts[key], "NATIVE_BATCH_INCOMPLETE")
    require(abs(number(receipt.get("account_total_assets_cny"))-parsed.total_assets) <= Decimal("0.02"), "NATIVE_RECONCILIATION_FAILED")
    # No time precision is invented for prices. Source text has no quote timestamp.
    batch_id = sha(json.dumps(receipt, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
    metadata = dict(source="guojin_qnx_native", complete=not observed_source, positions_as_of=None, cash_as_of=None,
        price_as_of=None, issues=["SOURCE_VALUATION_TIME_UNKNOWN"],
        time_evidence={k:"QNX_NATIVE_SOURCE_TIME_UNSUPPORTED" for k in ("positions_as_of","cash_as_of","price_as_of")},
        queried_at={k:(None if observed_source else queried[k].isoformat()) for k in FILES},
        native_batch_id=batch_id, account_total_assets_cny=str(parsed.total_assets),
        reconciliation=parsed.reconciliation,
        cash_balance_cny=str(parsed.cash), security_count=len(parsed.rows),
        native_batch={"id":batch_id, "queries":{k:v.isoformat() for k,v in clocks.items()},
                      "clock_basis":"OBSERVED_ACTION" if observed_source else "BROKER_QUERY",
                      "hashes":{k:sha(v) for k,v in data.items()}, "export_saved_at":parsed.saved_at})
    if parsed.reconciliation["status"] == "WARNING":
        metadata["issues"].append("NATIVE_VALUATION_DIFFERENCE_WARNING")
    if observed_source:
        metadata.update(publication_mode="observed_rows",
            query_action_at={k:v.isoformat() for k,v in actions.items()},
            source_completeness="UNKNOWN", counter_status="UNKNOWN")
        metadata["issues"].extend(["SOURCE_QUERY_TIME_UNKNOWN", "SOURCE_COMPLETENESS_UNKNOWN",
                                   "OBSERVED_ROWS_ONLY_LAST_GOOD_RETAINED"])
    return parsed, metadata

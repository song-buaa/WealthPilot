"""Bounded grammar for the approved Markdown layout; no LLM or numeric defaults.

Selectors describe structure/semantics, not personal target values. Unsupported or
ambiguous layouts fail. Source bytes never change. Future formats need a reviewed
parser version, not a manually maintained JSON policy.
"""
from __future__ import annotations

from decimal import Decimal
import hashlib
import re

from .contracts import Allocation, Rule, Snapshot, SourceClause


class PolicyError(ValueError):
    pass


def one(pattern, text):
    matches = re.findall(pattern, text, re.MULTILINE)
    if len(matches) != 1:
        raise PolicyError("UNSUPPORTED_OR_AMBIGUOUS_MARKDOWN: " + pattern)
    return matches[0]


def clean(text):
    return text.replace("**", "").replace("`", "")


def pct(value):
    return Decimal(value.replace("%", "")) / 100


class Document:
    def __init__(self, data: bytes, expected_hash: str, role: str):
        self.sha = hashlib.sha256(data).hexdigest()
        if self.sha != expected_hash:
            raise PolicyError("SOURCE_SHA256_MISMATCH: " + role)
        self.role = role
        self.lines = data.decode("utf-8").splitlines()
        self.sections = {}
        heads = [(i, m.group(1)) for i, line in enumerate(self.lines)
                 if (m := re.match(r"^#{2,3} (\d+(?:\.\d+)?)\.?(?:\s|$)", line))]
        for index, (start, key) in enumerate(heads):
            end = heads[index + 1][0] if index + 1 < len(heads) else len(self.lines)
            if key in self.sections:
                raise PolicyError("DUPLICATE_SECTION")
            self.sections[key] = (start, end)

    def ref(self, section):
        if section not in self.sections:
            raise PolicyError("MISSING_SECTION: " + section)
        start, end = self.sections[section]
        return SourceClause(document=self.role, source_sha256=self.sha, section=section,
                            line_start=start + 1, line_end=end, text="\n".join(self.lines[start:end]))

    def text(self, section):
        return clean(self.ref(section).text)

    def table(self, section):
        result = []
        for line in self.text(section).splitlines():
            if line.startswith("|"):
                row = [c.strip() for c in line.strip("|").split("|")]
                result.append(row)
        if len(result) < 3 or any(set(c) - set("-: ") for c in result[1]):
            raise PolicyError("MISSING_TABLE: " + section)
        return result[2:]


def derive(philosophy: bytes, policy: bytes, *, expected_hashes: dict, policy_version: int) -> Snapshot:
    if set(expected_hashes) != {"philosophy", "policy"}:
        raise PolicyError("EXACTLY_TWO_APPROVED_SOURCES_REQUIRED")
    docs = {"philosophy": Document(philosophy, expected_hashes["philosophy"], "philosophy"),
            "policy": Document(policy, expected_hashes["policy"], "policy")}
    approval_dates = []
    for raw in (philosophy, policy):
        text = clean(raw.decode())
        if "APPROVED_CONTENT" not in text or "SYSTEM_NOT_ACTIVE" not in text:
            raise PolicyError("APPROVED_CONTENT_ATTESTATION_REQUIRED")
        approval_dates.append(one(r"内容批准日期：(\d{4}-\d{2}-\d{2})", text))
    if approval_dates[0] != approval_dates[1]:
        raise PolicyError("CONTENT_APPROVAL_DATE_MISMATCH")
    doc = docs["policy"]
    if "全部合格可投资资产" not in doc.text("1.2"):
        raise PolicyError("DENOMINATOR_UNSUPPORTED")

    selectors = {"美国股票ETF子组合": "us_etf", "美国直接个股（主动投资）": "us_direct",
                 "中国公司主动精选个股": "china", "黄金": "gold", "BTC现货": "btc",
                 "IB01短期美国国债ETF": "ib01", "CBU3短期美国国债ETF": "cbu3", "未投资交易现金": "cash"}
    neutral = {}
    for row in doc.table("2.1"):
        if row[0] == "合计":
            if pct(one(r"^(\d+(?:\.\d+)?%)$", row[1])) != 1:
                raise PolicyError("TOTAL_NOT_ONE")
            continue
        if row[0] not in selectors or selectors[row[0]] in neutral:
            raise PolicyError("UNKNOWN_OR_DUPLICATE_BUDGET_ROW")
        neutral[selectors[row[0]]] = pct(one(r"^(\d+(?:\.\d+)?%)$", row[1]))
    growth, stable = one(r"(\d+(?:\.\d+)?)%增长及另类[＋+]([\d.]+)%稳健", doc.text("2.1"))
    symbols, weights = one(r"([A-Z.]+(?:／[A-Z.]+)+)按ETF子组合内部([\d.%／]+)配置", doc.text("3.1"))
    names, amounts = symbols.split("／"), weights.split("／")
    if len(names) != len(amounts) or len(set(names)) != len(names):
        raise PolicyError("ETF_STRUCTURE_AMBIGUOUS")
    direct = {}
    for row in doc.table("3.2"):
        if row[0] == "合计":
            if pct(row[1]) != neutral.get("us_direct"):
                raise PolicyError("DIRECT_TOTAL_MISMATCH")
            continue
        # Keep source share-class identity; do not claim a verified issuer registry.
        identity = "unallocated" if row[0].startswith("其他自主选股") else row[0]
        if identity in direct:
            raise PolicyError("DUPLICATE_DIRECT_EQUITY")
        direct[identity] = pct(one(r"^(\d+(?:\.\d+)?%)$", row[1]))
    specified = one(r"已指定六股合计([\d.]+)%", doc.text("3.2"))
    flex = doc.text("2.2")
    china_max = one(r"0%\s*≤\s*C\s*<\s*([\d.]+)%", flex)
    alt_max = one(r"0%\s*≤\s*A\s*≤\s*([\d.]+)%", flex)
    joint = one(r"C＋A\s*≤\s*([\d.]+)%", flex)
    base = one(r"U＝([\d.]+)%－C－A", flex)
    floor = one(r"U≥([\d.]+)%", flex)
    if pct(base) != pct(growth):
        raise PolicyError("US_FORMULA_BASE_MISMATCH")
    if "优先由美国ETF" not in doc.text("2.3") or "不直接持有中国股票" not in doc.text("3.3"):
        raise PolicyError("BUDGET_ROUTING_OR_CHINA_ETF_UNKNOWN")
    cash = one(r"总可投资资产([\d.]+)%以内", doc.text("5.1"))
    if "占用" not in doc.text("5.1") or "不额外增加总配置" not in doc.text("5.1"):
        raise PolicyError("CASH_BUDGET_NOT_WITHIN_STABLE")
    allocation = Allocation(denominator="QUALIFIED_INVESTABLE_ASSETS", neutral=neutral,
        etf_internal=dict(zip(names, map(pct, amounts))), direct_equity=direct,
        growth_total=pct(growth), stable_total=pct(stable), specified_equity_total=pct(specified),
        flex_china_max_exclusive=pct(china_max), flex_alternatives_max=pct(alt_max),
        flex_joint_max=pct(joint), us_core_floor=pct(floor), temporary_cash_max=pct(cash),
        china_equity_etf_target=Decimal(0),
        source_clauses={s: doc.ref(s) for s in ("1.2", "2.1", "2.2", "2.3", "3.1", "3.2", "3.3", "5.1")})

    rules = []
    def add(rule_id, section, metric, scope, operator, threshold, action, subject=None):
        rules.append(Rule(rule_id=rule_id, policy_version=policy_version, source_clause=doc.ref(section),
            metric=metric, scope=scope, operator=operator, threshold=threshold, action=action,
            severity=action, data_quality_requirement="verified identity + qualified denominator + fresh as_of; partial lower bound can trigger, cannot clear",
            manual_review_required=action == "HUMAN_REVIEW", subject=subject))

    for row in doc.table("6.1"):
        action = one(r"\b(WARNING|HUMAN_REVIEW)\b", row[2])
        op, amount = one(r"(严格超过|达到或超过)([\d.]+)%", row[1])
        if "普通股票" in row[0]:
            key, subject = "issuer.warning.default", None
        elif "任何股票" in row[0]:
            key, subject = "issuer.review", None
        else:
            subject = one(r"(?<![A-Z0-9])([A-Z][A-Z0-9.]*)(?![A-Z0-9])", row[0])
            key = "issuer.warning." + subject
        add(key, "6.1", "issuer_weight", "ALL_INVESTABLE_ASSETS_LOOKTHROUGH", "gt" if op == "严格超过" else "gte", pct(amount), action, subject)
    for row in doc.table("6.2"):
        if not re.search(r"\b(WARNING|HUMAN_REVIEW)\b", row[1]):
            continue
        action = one(r"\b(WARNING|HUMAN_REVIEW)\b", row[1])
        op, amount = one(r"(严格超过|达到或超过)([\d.]+)%", row[0])
        add("btc." + action.lower(), "6.2", "btc_weight", "ALL_INVESTABLE_ASSETS_ECONOMIC", "gt" if op == "严格超过" else "gte", pct(amount), action)
    deviation = one(r"绝对偏离严格超过([\d.]+)个百分点", doc.text("6.3"))
    add("allocation.deviation", "6.3", "major_class_absolute_deviation", "APPROVED_MAJOR_ASSET_CLASS", "gt", pct(deviation), "HUMAN_REVIEW")
    # Boolean prohibitions are explicit; no invented numerical debt/leverage limits.
    for key, phrase in (("new_consumer_credit_for_equities", "为股票投资新增消费信用贷"),
                        ("unauthorized_real_trade", "在没有明确交易授权时执行任何真实交易")):
        if phrase not in doc.text("5.3"):
            raise PolicyError("EXPLICIT_PROHIBITION_MISSING")
        add("prohibition." + key, "5.3", key, "EXPLICIT_BEHAVIOR", "prohibited", None, "HARD_BLOCK")
    expected = {"issuer.warning.default", "issuer.review", "btc.warning", "btc.human_review", "allocation.deviation"}
    if not expected.issubset({r.rule_id for r in rules}) or not any(r.subject for r in rules):
        raise PolicyError("MISSING_REQUIRED_RULES")
    # Qualitative constraints and retired defaults remain source clauses, not fallback rules.
    for section, phrase in (("6.3", "年度做一次全面配置复核"), ("7.3", "均不要求永久保留固定底仓"),
                            ("7.2", "不重新引入旧规则"), ("5.3", "不继承旧的"), ("7.1", "不设固定百分比")):
        if phrase not in doc.text(section):
            raise PolicyError("QUALITATIVE_OR_RETIREMENT_CLAUSE_MISSING")
    contexts = {s: doc.ref(s) for s in doc.sections}
    contexts["philosophy"] = SourceClause(document="philosophy", source_sha256=docs["philosophy"].sha,
        section="full", line_start=1, line_end=len(docs["philosophy"].lines), text=philosophy.decode())
    return Snapshot(policy_version=policy_version, source_hashes={k: d.sha for k, d in docs.items()},
                    content_approved_on=approval_dates[0], allocation=allocation, rules=rules, contextual_clauses=contexts)

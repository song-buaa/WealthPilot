"""Bounded grammar for the approved Markdown layout; no LLM or numeric defaults.

Selectors describe structure/semantics, not personal target values. Unsupported or
ambiguous layouts fail. Source bytes never change. Future formats need a reviewed
parser version, not a manually maintained JSON policy.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import hashlib
import re

from backend.services.instruments.issuers import policy_target_identity

from .contracts import (
    Allocation, PolicyAmendmentApproval, Rule, Snapshot, SnapshotV2, SnapshotV2WithAmendment,
    SourceClause, StrategicReviewScope,
)


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


def derive(philosophy: bytes, policy: bytes, *, expected_hashes: dict, policy_version: int,
           expected_schema_version: str | None = None) -> Snapshot:
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
    equity_section = doc.text("3.2")
    count, specified = one(r"已指定(六|八)股合计([\d.]+)%", equity_section)
    amendment_lines = [line for line in doc.lines if line.startswith("美国核心股内容批准：")]
    targets = {label: policy_target_identity(label)[1] for label in direct if label != "unallocated"}
    if count == "八":
        if len(amendment_lines) != 1 or amendment_lines[0] not in equity_section.splitlines():
            raise PolicyError("POLICY_AMENDMENT_CONTENT_MARKER_REQUIRED")
        amendment = amendment_lines[0].removeprefix("美国核心股内容批准：")
        if (len(targets) != 8 or None in targets.values() or len(set(targets.values())) != 8
                or direct.get("unallocated") != 0 or any(direct[label] <= 0 for label in targets)):
            raise PolicyError("EIGHT_DISTINCT_QUALIFIED_DIRECT_TARGETS_REQUIRED")
    elif (len(targets) != 6 or any(
            ticker in {"NVDA", "ANT"} for label in targets for ticker in policy_target_identity(label)[0]
    )):
        raise PolicyError("POLICY_AMENDMENT_CONTENT_MARKER_REQUIRED")
    elif amendment_lines:
        raise PolicyError("UNEXPECTED_POLICY_AMENDMENT_CONTENT_MARKER")
    else:
        amendment = None
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
    fields = dict(policy_version=policy_version, source_hashes={k: d.sha for k, d in docs.items()},
                  content_approved_on=approval_dates[0], allocation=allocation, rules=rules, contextual_clauses=contexts)
    markers = ("B3节点合同：", "B3节点 growth_and_alternatives：", "B3节点 stable_pool：",
               "B3节点复核单位：", "B3节点现金范围：", "B3节点产品决定：", "B3节点内容批准：")
    has_versioned_clause = any(line.startswith(markers) for line in doc.lines)
    btc_sections = {"4.3", "9.3"} & doc.sections.keys()
    btc_markers = [line for line in doc.lines if line.startswith(("BTC治理合同：", "BTC治理内容批准："))]
    has_btc_governance = bool(btc_sections or btc_markers)
    if has_btc_governance and (amendment is None or not has_versioned_clause):
        raise PolicyError("BTC_GOVERNANCE_REQUIRES_AMENDMENT_BASE")
    schema_version = expected_schema_version
    if schema_version is None:
        schema_version = "wp.policy.v2" if has_versioned_clause else "wp.policy.v1"
    if schema_version == "wp.policy.v1":
        if amendment is not None or has_btc_governance:
            raise PolicyError("POLICY_SOURCE_SCHEMA_MISMATCH")
        return Snapshot(**fields)
    if schema_version != "wp.policy.v2" or not has_versioned_clause:
        raise PolicyError("POLICY_SOURCE_SCHEMA_MISMATCH")

    section = doc.text("6.3")
    prefix_lines = [line for line in doc.lines if line.startswith("B3节点")]
    if len(prefix_lines) != 7 or any(line not in section.splitlines() for line in prefix_lines):
        raise PolicyError("B3_SOURCE_CLAUSE_LAYOUT_INVALID")
    one(r"^B3节点合同：wp\.policy\.v2$", section)
    nodes = {
        key: pct(one(rf"^B3节点 {key}：(\d+(?:\.\d+)?%)$", section))
        for key in ("growth_and_alternatives", "stable_pool")
    }
    review_unit = one(r"^B3节点复核单位：(PORTFOLIO_PER_SNAPSHOT)$", section)
    cash_scope = one(r"^B3节点现金范围：(QUALIFIED_TEMPORARY_INVESTMENT_CASH_IN_STABLE_POOL)$", section)
    decision_ref = one(r"^B3节点产品决定：(https://github\.com/song-buaa/WealthPilot/issues/111#issuecomment-6814354290)$", section)
    approval = one(r"^B3节点内容批准：([^\n]+)$", section)
    content_status = "DRAFT_CONTENT"
    approval_ref = None
    if approval != content_status:
        approved_on, approval_ref = one(r"^APPROVED_CONTENT \| (\d{4}-\d{2}-\d{2}) \| (\S(?:.*\S)?)$", approval)
        if (approved_on < approval_dates[0] or approval_ref in {
                decision_ref, "https://github.com/song-buaa/WealthPilot/issues/111#issuecomment-6816141673"
        }):
            raise PolicyError("B3_INDEPENDENT_CONTENT_APPROVAL_REQUIRED")
        fields["content_approved_on"] = approved_on
        content_status = "APPROVED_CONTENT"
    mapping = StrategicReviewScope(nodes=nodes, review_unit=review_unit, cash=cash_scope,
        product_decision_ref=decision_ref, content_status=content_status,
        content_approval_ref=approval_ref, source_clause=doc.ref("6.3"))
    if amendment is None:
        return SnapshotV2(**fields, strategic_review_scope=mapping)
    if mapping.content_status != "APPROVED_CONTENT":
        raise PolicyError("B3_BASE_CONTENT_APPROVAL_REQUIRED")
    approval_date, reference = None, None
    if amendment != "DRAFT_CONTENT":
        day, reference = one(
            r"^APPROVED_CONTENT \| (\d{4}-\d{2}-\d{2}) \| "
            r"(https://github\.com/song-buaa/WealthPilot/issues/[1-9]\d*#issuecomment-[1-9]\d*)$",
            amendment,
        )
        issue_number = reference.split("/issues/")[1].split("#")[0]
        approval_date = date.fromisoformat(day)
        if (day < fields["content_approved_on"] or issue_number in {"111", "121", "124"}
                or reference == mapping.content_approval_ref):
            raise PolicyError("INDEPENDENT_POLICY_AMENDMENT_CONTENT_APPROVAL_REQUIRED")
        fields["content_approved_on"] = day
    snapshot = SnapshotV2WithAmendment(**fields, strategic_review_scope=mapping,
        amendment_approval=PolicyAmendmentApproval(
            status="APPROVED_CONTENT" if approval_date else "DRAFT_CONTENT",
            approved_on=approval_date, reference=reference))
    if has_btc_governance:
        return _derive_btc_governance(snapshot, doc, btc_markers)
    return snapshot


# Digests of the reviewed BTC additions in this contract version. A change to
# the machine-interpreted prose requires an explicit contract revision.
BTC_SOURCE_FRAGMENT_SHA256 = {
    "4.3": "9edb48d64020171def4b5012e08c661afd9ac7b1cc79dde143497c867f6e9fc9",
    "5.1": "b5a9551ed7175ce16076c2529b69993cf1ea290460bb604a991a9580d0ded340",
    "5.2": "a87d829711b66432f288eb45e14c817dec05d701fe53646c756504d76e86f0e2",
    "5.3": "f26963a674bb51e6c7ca57ff2e068afb07aa27efbbbf2614a31da7645eebec05",
    "6.2": "1a89af4d2ada2e71408fdb526ee35a5f66a83b7b26c0549d99d407fd7aa82e04",
    "9.3": "405aa7fd7a85247953256175f6c2a4c42d02be39b90e0a146ff583c86de34cb9",
}
BTC_APPENDED_PARAGRAPHS = {"5.1": 2, "5.2": 1, "5.3": 1, "6.2": 4}


def _btc_source_fragment(doc, section):
    text = doc.ref(section).text.rstrip("\n")
    if section in BTC_APPENDED_PARAGRAPHS:
        paragraphs = text.split("\n\n")
        return "\n\n".join(paragraphs[-BTC_APPENDED_PARAGRAPHS[section]:])
    lines = text.splitlines()[1:]
    return "\n".join(line for line in lines if not line.startswith(
        ("BTC治理合同：", "BTC治理内容批准："))).strip("\n")


def _derive_btc_governance(base, doc, markers):
    from .btc_governance import BtcGovernance, SECTIONS, SnapshotV2WithBtcGovernance, VERSION

    if len(markers) != 2 or any(line not in doc.text("9.3").splitlines() for line in markers):
        raise PolicyError("BTC_GOVERNANCE_SOURCE_LAYOUT_INVALID")
    one(rf"^BTC治理合同：{re.escape(VERSION)}$", doc.text("9.3"))
    stamp = one(r"^BTC治理内容批准：([^\n]+)$", doc.text("9.3"))
    # Bind the structured interpretation to the reviewed L2 prose, without
    # storing private Markdown in the repository. L1 edits may alter the
    # preceding cash paragraph, while the reviewed BTC additions stay fixed.
    for section, expected in BTC_SOURCE_FRAGMENT_SHA256.items():
        fragment = _btc_source_fragment(doc, section)
        if hashlib.sha256(fragment.encode()).hexdigest() != expected:
            raise PolicyError("BTC_GOVERNANCE_SOURCE_FRAGMENT_MISMATCH: " + section)
    approved_on, ref = None, None
    if stamp != "DRAFT_CONTENT":
        day, ref, issue = _btc_approval(stamp)
        approved_on = date.fromisoformat(day)
        if issue in {"111", "121", "124", "126", "127"}:
            raise PolicyError("BTC_INDEPENDENT_CONTENT_APPROVAL_REQUIRED")
    governance = BtcGovernance(content_approval=PolicyAmendmentApproval(
        status="APPROVED_CONTENT" if approved_on else "DRAFT_CONTENT", approved_on=approved_on, reference=ref),
        source_clauses={key:doc.ref(key) for key in SECTIONS})
    return SnapshotV2WithBtcGovernance(**(base.model_dump() | {
        "btc_governance":governance,
        "content_approved_on":approved_on.isoformat() if approved_on else base.content_approved_on}))


def _btc_approval(stamp):
    return one(
        r"^APPROVED_CONTENT \| (\d{4}-\d{2}-\d{2}) \| "
        r"(https://github\.com/song-buaa/WealthPilot/issues/([1-9]\d*)(?:#issuecomment-[1-9]\d*)?)$",
        stamp,
    )

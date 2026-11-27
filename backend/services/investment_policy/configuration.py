"""Frozen configuration source compiled against an immutable approved policy."""
from datetime import date
from decimal import Decimal, localcontext
import hashlib
import re
from typing import ClassVar, Literal

from pydantic import Field, field_serializer, field_validator, model_validator

from backend.services.instruments.issuers import IDENTITIES, policy_target_identity

from .btc_governance import BtcGovernance, SECTIONS, verify_btc_scope
from .contracts import Allocation, Contract, Rule, SnapshotV2, StrategicReviewScope, digest
from .parser import Document, PolicyError
from .store import clock

SCHEMA = "wp.policy.config.v1"
# Asset membership is a capability boundary. The weights live in the config.
GROWTH = ("us_etf", "us_direct", "china", "gold", "btc")
ASSET_NAMES = {
    "us_etf": "美国股票 ETF 子组合",
    "us_direct": "美国直接个股",
    "china": "中国主动精选个股",
    "gold": "黄金",
    "btc": "BTC 现货",
    "ib01": "IB01 短期美国国债 ETF",
    "cbu3": "CBU3 短期美国国债 ETF",
    "cash": "未投资交易现金",
}
HASH = r"^[a-f0-9]{64}$"


def ratio(value):
    # No binary-float coercion, exponents, special values, or unbounded scale.
    if not isinstance(value, (str, Decimal, int)) or isinstance(value, bool):
        raise ValueError("EXACT_DECIMAL_RATIO_REQUIRED")
    text = format(value, "f") if isinstance(value, Decimal) else str(value)
    if len(text) > 14 or not re.fullmatch(r"(?:0|1)(?:\.[0-9]{1,12})?", text):
        raise ValueError("EXACT_DECIMAL_RATIO_REQUIRED")
    number = Decimal(text)
    if not 0 <= number <= 1:
        raise ValueError("EXACT_DECIMAL_RATIO_REQUIRED")
    return number.normalize()


class Listing(Contract):
    """An independently admitted, US common-stock security from the issuer Owner."""
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9.-]{0,14}:US$")
    issuer_id: str = Field(pattern=r"^SEC_CIK_[0-9]{10}$")
    issuer_name: str = Field(pattern=r"^[^\r\n|<>]{1,160}$")
    listing_market: Literal["NASDAQ", "NYSE", "NYSE_AMERICAN"]
    currency: Literal["USD"] = "USD"
    vehicle: Literal["COMMON_STOCK"] = "COMMON_STOCK"
    share_class: str = Field(pattern=r"^[A-Za-z0-9 .-]{1,40}$")
    cusip: str | None = Field(default=None, pattern=r"^[A-Z0-9]{9}$")
    evidence_url: str
    evidence_sha256: str = Field(pattern=HASH)
    valid_from: date
    valid_until: date

    @model_validator(mode="after")
    def provenance(self):
        cik = str(int(self.issuer_id.removeprefix("SEC_CIK_")))
        if (not re.fullmatch(rf"https://www\.sec\.gov/Archives/edgar/data/{cik}/[0-9]{{18}}/[A-Za-z0-9_.-]+\.htm",
                             self.evidence_url)
                or self.valid_until < self.valid_from
                or (self.symbol == "ANT:US" and (self.share_class, self.cusip, self.listing_market)
                    != ("Class A", "03703U107", "NASDAQ"))):
            raise ValueError("LISTING_PROVENANCE_CONFLICT")
        return self


class TargetConfig(Contract):
    contract: Literal["wp.policy.config.v1"] = SCHEMA
    neutral: dict[str, Decimal]
    etf_internal: dict[str, Decimal]
    direct_issuer_targets: dict[str, Decimal]
    unallocated: Decimal

    @model_validator(mode="before")
    @classmethod
    def classify_shape(cls, value):
        if isinstance(value, dict):
            unknown = set(value) - set(cls.model_fields)
            if unknown:
                rule_fields = {"rules", "risk_rules", "constraints", "discipline", "thresholds", "repayment", "btc_governance"}
                if unknown & rule_fields:
                    raise ValueError("L2_RULE_CHANGE_REQUIRES_SEPARATE_APPROVAL")
                raise ValueError("L3_UNKNOWN_CONFIGURATION_CAPABILITY")
        return value

    @field_validator("neutral", "etf_internal", "direct_issuer_targets", mode="before")
    @classmethod
    def exact_weights(cls, values):
        if not isinstance(values, dict):
            raise ValueError("TARGET_MAPPING_REQUIRED")
        return {key: ratio(value) for key, value in values.items()}

    @field_validator("unallocated", mode="before")
    @classmethod
    def exact_unallocated(cls, value):
        return ratio(value)

    @field_serializer("neutral", "etf_internal", "direct_issuer_targets")
    def serialize_weights(self, values):
        return {key: format(value, "f") for key, value in values.items()}

    @field_serializer("unallocated")
    def serialize_unallocated(self, value):
        return format(value, "f")

    @model_validator(mode="after")
    def shapes(self):
        if (len(self.direct_issuer_targets) > 128
                or any(not re.fullmatch(r"SEC_CIK_[0-9]{10}", issuer) or value <= 0
                       for issuer, value in self.direct_issuer_targets.items())):
            raise ValueError("DISTINCT_QUALIFIED_TARGETS_REQUIRED")
        return self


class ConfiguredAllocation(Allocation):
    allow_dynamic_growth: ClassVar[bool] = True


class ConfiguredStrategicScope(StrategicReviewScope):
    fixed_node_targets: ClassVar[bool] = False


class ConfiguredSnapshot(SnapshotV2):
    schema_version: Literal["wp.policy.config.v1"] = SCHEMA
    parser_version: Literal["policy-config-v1"] = "policy-config-v1"
    direct_target_count: ClassVar[int | None] = None
    allocation: ConfiguredAllocation
    strategic_review_scope: ConfiguredStrategicScope
    listings: dict[str, Listing]
    target_issuers: dict[str, str]
    target_symbols: dict[str, str]

    @property
    def content_status(self):
        return "EXTERNAL_APPROVAL_REQUIRED"


class ConfiguredSnapshotWithBtcGovernance(ConfiguredSnapshot):
    btc_governance: BtcGovernance

    @model_validator(mode="after")
    def inherited_btc_approval(self):
        if self.btc_governance.content_approval.status != "APPROVED_CONTENT":
            raise ValueError("BTC_APPROVED_BASE_REQUIRED")
        verify_btc_scope(self)
        return self


def owner_listings(values, as_of, *, verify_owner=False):
    """Normalize admitted listings, rejecting conflicting security/class records."""
    result = {}
    seen_classes, seen_cusips = set(), set()
    for value in values:
        item = Listing.model_validate(value)
        share = (item.issuer_id, item.share_class.casefold())
        if verify_owner and IDENTITIES.get((item.symbol, "USD"), item.issuer_id) != item.issuer_id:
            raise PolicyError("LISTING_OWNER_CONFLICT")
        if (item.symbol in result or share in seen_classes or item.cusip in seen_cusips
                or not item.valid_from <= as_of <= item.valid_until):
            raise PolicyError("LISTING_DUPLICATE_OR_NOT_EFFECTIVE")
        result[item.symbol] = item
        seen_classes.add(share)
        if item.cusip:
            seen_cusips.add(item.cusip)
    return result


def from_snapshot(snapshot):
    if isinstance(snapshot, ConfiguredSnapshot):
        identities = snapshot.target_issuers
    else:
        identities = {label: policy_target_identity(label)[1] for label in snapshot.allocation.direct_equity
                      if label != "unallocated"}
    if None in identities.values() or len(set(identities.values())) != len(identities):
        raise PolicyError("BASE_TARGET_IDENTITY_UNAVAILABLE")
    a = snapshot.allocation
    return TargetConfig(neutral=a.neutral, etf_internal=a.etf_internal,
        direct_issuer_targets={issuer: a.direct_equity[label] for label, issuer in identities.items()},
        unallocated=a.direct_equity["unallocated"])


def pct(value):
    return format(value * 100, "f") + "%"


def display_policy(template, snapshot, config, targets, growth, stable):
    """Render affected target paragraphs and keep independent policy prose."""
    doc = Document(template, snapshot.source_hashes["policy"], "policy")
    lines = doc.lines[:]
    for i, line in enumerate(lines[:doc.sections["1"][0]]):
        if "文档状态：" in line:
            lines[i] = "> **文档状态：由结构化配置生成。发布资格及生效时间由独立发布凭据证明。**"
        if "重要边界：" in line and "本文件尚未发布到 WealthPilot" in line:
            lines[i] = "> **重要边界：**实际生效状态以当前 PolicyReference 和发布凭据为准。任何交易均须独立明确授权。"
    with localcontext() as ctx:
        ctx.prec = 60
        budget_table = [
            "| 资产与预算 | 中性目标 |", "|---|---:|",
            *(f"| {label} | {pct(config.neutral[key])} |" for key, label in ASSET_NAMES.items()),
            "| 合计 | 100% |", "",
            f"一级战略节点为增长及另类 {pct(growth)}、稳健池 {pct(stable)}。",
            "以上均占全部合格可投资资产。临时投资现金占用稳健预算。",
        ]
        etf_table = [
            "| 美国股票ETF | ETF内部目标 | 占总预算目标 |", "|---|---:|---:|",
            *(f"| {key} | {pct(value)} | {pct(config.neutral['us_etf'] * value)} |"
              for key, value in sorted(config.etf_internal.items())),
        ]
        direct_table = [
            "| 发行人及上市证券 | 占合格可投资资产目标 |", "|---|---:|",
            *(f"| {label} | {pct(weight)} |" for label, weight in sorted(targets.items()) if label != "unallocated"),
            f"| 未指定战略预算 | {pct(config.unallocated)} |",
            f"| 合计 | {pct(config.neutral['us_direct'])} |",
        ]
    tables = {"2.1":budget_table, "3.1":etf_table, "3.2":direct_table}
    for key in ("3.2", "3.1", "2.3", "2.1"):
        start, end = doc.sections[key]
        paragraphs = "\n".join(lines[start + 1:end]).strip().split("\n\n")
        adjusted = []
        for paragraph in paragraphs:
            if paragraph.startswith("|") and key in {"3.2", "2.1"}:
                continue  # Its replacement is generated from the target config.
            if key == "2.1" and "股票权益及全球分散/另类配置" in paragraph:
                continue
            if key == "2.3" and paragraph.startswith("例如中国主动股"):
                paragraph = ("实际预算承接按第2.3节原则重新计算。中性ETF目标仍以第2.1节为准，"
                             "也不因实际配置低于目标而要求立即买满。")
            if key == "2.3" and "找不到长期认可" in paragraph:
                paragraph = ("战略目标及未指定预算均不要求立即买满。"
                    "实际未使用的主动投资预算取决于实际投入。" + paragraph[paragraph.index("找不到长期认可"):])
            if key == "3.1" and "按ETF子组合内部" in paragraph:
                paragraph = "若ETF总预算因合格预算承接而改变，仍按内部比例推算。"
            if key == "3.2":
                if paragraph.startswith("美国核心股内容批准："):
                    continue
                paragraph = re.sub(r"^已指定[六八]股合计[^。]*。", "", paragraph)
                paragraph = re.sub(r"即使[六八]股战略目标已占满[\d.]+%，", "", paragraph)
                if "Alphabet直接配置目标高于微软。" in paragraph:
                    direct = config.direct_issuer_targets
                    goog = direct.get("SEC_CIK_0001652044", Decimal(0))
                    msft = direct.get("SEC_CIK_0000789019", Decimal(0))
                    if goog <= msft or not msft:
                        paragraph = paragraph.replace("Alphabet直接配置目标高于微软。", "")
            if paragraph:
                adjusted.append(paragraph)
        body = ["\n".join(tables[key])] if key in tables else []
        body.extend(adjusted)
        lines[start+1:end] = ["", *("\n\n".join(body)).splitlines(), ""]
    # Keep historical B3 approval and policy scope; project only the effective node targets.
    for i, line in enumerate(lines):
        for node, value in (("growth_and_alternatives", growth), ("stable_pool", stable)):
            if line.startswith(f"B3节点 {node}："):
                lines[i] = f"B3节点 {node}：{pct(value)}"
        # Update affected budget references in mixed narrative while preserving constraints.
        if line.startswith("- `C`：") and "长期中性" in line:
            lines[i] = re.sub(r"长期中性[\d.]+%", f"长期中性{pct(config.neutral['china'])}", line)
        if line.startswith("- `A`：") and "已确认中性为黄金" in line:
            lines[i] = re.sub(r"已确认中性为黄金[\d.]+%＋BTC[\d.]+%",
                             f"已确认中性为黄金{pct(config.neutral['gold'])}＋BTC{pct(config.neutral['btc'])}", line)
        if line.startswith("- `U`："):
            lines[i] = re.sub(r"U＝[\d.]+%", f"U＝{pct(growth)}", line)
        if line.startswith("- `S`："):
            lines[i] = re.sub(r"目标 \*\*[\d.]+%", f"目标 **{pct(stable)}", line)
        if "闲置交易现金长期中性" in line:
            lines[i] = re.sub(r"长期中性[\d.]+%", f"长期中性{pct(config.neutral['cash'])}", line)
            lines[i] = re.sub(r"占用[\d.]+%的稳健预算", f"占用{pct(stable)}的稳健预算", lines[i])
        if "中国权益" in line and "中性预算" in line:
            lines[i] = re.sub(r"(保留)[\d.]+%(的?中性预算)",
                             lambda m: m[1] + pct(config.neutral["china"]) + m[2], line)
    heading = f"> 配置来源 {SCHEMA} · SHA256 {digest(config)}。内容授权由独立正式发布凭据绑定。"
    return ("\n".join([lines[0], "", heading, *lines[1:]]) + "\n").encode()


def target_bindings(base, config, admitted):
    """Resolve the Owner's current data once at preview and recheck on publish."""
    by_issuer = {}
    for symbol, listing in admitted.items():
        by_issuer.setdefault(listing.issuer_id, []).append(symbol)
    for (symbol, currency), issuer in IDENTITIES.items():
        if currency == "USD" and issuer.startswith("SEC_CIK_") and symbol not in admitted:
            by_issuer.setdefault(issuer, []).append(symbol)
    old_labels = {policy_target_identity(label)[1]: label for label in base.allocation.direct_equity if label != "unallocated"}
    labels, aliases = {}, {}
    for issuer in config.direct_issuer_targets:
        symbols = by_issuer.get(issuer)
        if not symbols:
            raise PolicyError("UNQUALIFIED_TARGET_ISSUER")
        labels[old_labels.get(issuer, "/".join(sorted(symbol.removesuffix(":US") for symbol in symbols)))] = issuer
        aliases.update(dict.fromkeys(symbols, issuer))
    if len(labels) != len(config.direct_issuer_targets):
        raise PolicyError("CONFLICTING_TARGET_LABEL")
    return {"labels":labels, "symbols":aliases}


def inherited_policy(base):
    inputs = {
        "source_hashes":base.source_hashes,
        "rules":[rule.model_dump(mode="json", exclude={"source_clause", "policy_version"}) for rule in base.rules],
        "constraints":base.allocation.model_dump(mode="json", exclude={
            "neutral", "etf_internal", "direct_equity", "specified_equity_total",
            "growth_total", "stable_total", "source_clauses"}),
    }

    governance = getattr(base, "btc_governance", None)
    if governance is not None:
        inputs["btc_governance"] = governance.model_dump(mode="json")
    return inputs


def compile_config(base, philosophy, template, config, listing_values, *, version, created_at, bindings):
    if not isinstance(base, SnapshotV2) or isinstance(base, ConfiguredSnapshot) or base.content_status != "APPROVED_CONTENT":
        raise PolicyError("APPROVED_MARKDOWN_POLICY_BASE_REQUIRED")
    admitted = owner_listings(listing_values, clock(created_at).date())
    labels = bindings["labels"]
    symbols = bindings["symbols"]
    if (len(labels) != len(config.direct_issuer_targets)
            or set(labels.values()) != set(config.direct_issuer_targets)
            or set(symbols.values()) != set(labels.values())
            or any(symbol in admitted and admitted[symbol].issuer_id != issuer for symbol, issuer in symbols.items())):
        raise PolicyError("INVALID_FROZEN_TARGET_BINDINGS")
    if set(base.allocation.neutral) != set(config.neutral) or set(base.allocation.etf_internal) != set(config.etf_internal):
        raise PolicyError("L3_ASSET_CAPABILITY_CHANGE")
    if config.neutral["btc"] != base.allocation.neutral["btc"]:
        raise PolicyError("L2_BTC_STRATEGIC_TARGET_REQUIRES_SEPARATE_APPROVAL")
    if config.neutral["cash"] != base.allocation.neutral["cash"]:
        raise PolicyError("L2_CASH_POLICY_REQUIRES_SEPARATE_APPROVAL")
    growth = sum(config.neutral[key] for key in GROWTH)
    stable = sum(config.neutral.values()) - growth
    targets = {label: config.direct_issuer_targets[issuer] for label, issuer in labels.items()}
    targets["unallocated"] = config.unallocated
    markdown = display_policy(template, base, config, targets, growth, stable)
    source_hash = hashlib.sha256(markdown).hexdigest()
    parsed = Document(markdown, source_hash, "policy")
    refs = {key: parsed.ref(key) for key in parsed.sections}
    refs["philosophy"] = base.contextual_clauses["philosophy"]
    allocation = ConfiguredAllocation(**({
        **base.allocation.model_dump(exclude={"source_clauses"}),
        "neutral": config.neutral, "etf_internal": config.etf_internal, "direct_equity": targets,
        "growth_total": growth, "stable_total": stable,
        "specified_equity_total": sum(config.direct_issuer_targets.values(), Decimal(0)),
        "source_clauses": {key: refs[key] for key in base.allocation.source_clauses},
    }))
    rules = [Rule(**(rule.model_dump(exclude={"source_clause", "policy_version"}) | {
        "source_clause": refs[rule.source_clause.section], "policy_version": version})) for rule in base.rules]
    scope = ConfiguredStrategicScope(**(base.strategic_review_scope.model_dump() | {
        "nodes": {"growth_and_alternatives": growth, "stable_pool": stable}, "source_clause": refs["6.3"]}))
    fields = dict(policy_version=version, allocation=allocation, rules=rules,
        content_approved_on=base.content_approved_on,
        source_hashes={"policy":source_hash, "philosophy":hashlib.sha256(philosophy).hexdigest()},
        contextual_clauses=refs, strategic_review_scope=scope, listings=admitted, target_issuers=labels, target_symbols=symbols)
    if hasattr(base, "btc_governance"):
        governance = base.btc_governance.model_dump() | {"source_clauses":{key:refs[key] for key in SECTIONS}}
        if any(refs[key].text != base.contextual_clauses[key].text for key in SECTIONS):
            # Section 5.1 contains an existing dynamic stable-pool percentage.
            # Only the previously allowed target rendering can change there.
            for key in SECTIONS:
                if key == "5.1":
                    source = re.sub(r"占用[\d.]+%的稳健预算", "占用<目标>的稳健预算", base.contextual_clauses[key].text)
                    rendered = re.sub(r"占用[\d.]+%的稳健预算", "占用<目标>的稳健预算", refs[key].text)
                    if source == rendered:
                        continue
                if refs[key].text != base.contextual_clauses[key].text:
                    raise PolicyError("L2_BTC_SOURCE_CLAUSE_CHANGED")
        snapshot = ConfiguredSnapshotWithBtcGovernance(**fields, btc_governance=governance)
    else:
        snapshot = ConfiguredSnapshot(**fields)
    return markdown, snapshot

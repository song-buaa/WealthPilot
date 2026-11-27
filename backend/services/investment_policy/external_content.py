"""Frozen Markdown revisions with approval carried by the release receipt."""
from hashlib import sha256
from decimal import Decimal
import difflib
import re
from typing import Literal

from .btc_governance import BtcGovernance, verify_btc_scope
from pydantic import field_validator, model_validator

from backend.services.instruments.issuers import policy_target_identity

from .configuration import ConfiguredSnapshot, ConfiguredStrategicScope, from_snapshot, pct
from .contracts import Rule, SnapshotV2, SourceClause, digest, parse_snapshot
from .parser import Document, PolicyError

SCHEMA = "wp.policy.external.v1"
META = re.compile(
    r"^> \*\*(?:文档状态：|内容批准日期：|重要边界：\*\*本文件尚未发布到 WealthPilot)"
    r"|^> 配置来源 wp\.policy\.config\.v1 · SHA256 [0-9a-f]{64}。内容授权由独立正式发布凭据绑定。\s*$"
    r"|^(?:B3节点内容批准|美国核心股内容批准|BTC治理内容批准)："
)
DESCRIPTIVE_HEADING = "## 11. 非执行政策说明"


class ExternalSnapshot(ConfiguredSnapshot):
    schema_version: Literal["wp.policy.external.v1"] = SCHEMA
    content_approved_on: None = None
    inherited_content_approval_date: str

    @field_validator("content_approved_on")
    @classmethod
    def valid_date(cls, value):
        # The signed release carries the actual date for these bytes.
        return value


class ExternalBtcSnapshot(ExternalSnapshot):
    btc_governance: BtcGovernance

    @model_validator(mode="after")
    def verify_sources(self):
        verify_btc_scope(self)
        return self


def content_only(raw):
    """Produce a private editing starting point before the candidate is sealed."""
    return b"".join(line for line in raw.splitlines(keepends=True) if not META.match(line.decode("utf-8")))


def _issuer_thresholds(old, new, rules):
    before, after = old.table("6.1"), new.table("6.1")
    issuer_rules = [rule.rule_id for rule in rules.values() if rule.metric == "issuer_weight"]
    if len(before) != len(after) or len(before) != len(issuer_rules):
        raise PolicyError("UNSUPPORTED_POLICY_RULE_CHANGE")
    changes = {}
    expected = old.ref("6.1").text
    candidate = new.ref("6.1").text
    for rule_id, left, right in zip(issuer_rules, before, after, strict=True):
        rule = rules[rule_id]
        if len(left) != 3 or len(right) != 3:
            raise PolicyError("UNSUPPORTED_POLICY_RULE_CHANGE")
        if left == right:
            continue
        phrase = "严格超过" if rule.operator == "gt" else "达到或超过"
        expression = rf"({phrase})([0-9]+(?:\.[0-9]{{1,4}})?)%"
        old_value = re.search(expression, left[1])
        new_value = re.search(expression, right[1])
        if not old_value or not new_value or Decimal(old_value[2])/100 != rule.threshold:
            raise PolicyError("UNSUPPORTED_POLICY_RULE_CHANGE")
        # Only the number in the existing rule table cell can change.
        old_line = next(line for line in expected.splitlines() if left[0] in line and line.startswith("|"))
        new_line = re.sub(expression, lambda m: m[1]+new_value[2]+"%", old_line, count=1)
        expected = expected.replace(old_line, new_line, 1)
        changes[rule_id] = Decimal(new_value[2])/100
        if rule_id == "issuer.warning.default":
            expected = expected.replace("不重复按普通"+old_value[2]+"%规则", "不重复按普通"+new_value[2]+"%规则")
        if rule_id == "issuer.review":
            expected = expected.replace("\n"+old_value[2]+"%不是自动卖出", "\n"+new_value[2]+"%不是自动卖出")
            expected = expected.replace("达到"+old_value[2]+"%后，原则上", "达到"+new_value[2]+"%后，原则上")
            expected = expected.replace("达到"+old_value[2]+"%时", "达到"+new_value[2]+"%时")
    # Whitespace at the boundary can change when adding the descriptive section.
    if expected.rstrip("\n") != candidate.rstrip("\n"):
        raise PolicyError("UNSUPPORTED_POLICY_RULE_CHANGE")
    thresholds = {k:changes.get(k, rules[k].threshold) for k in issuer_rules}
    default, review = thresholds["issuer.warning.default"], thresholds["issuer.review"]
    if not 0 < default < review <= 1 or any(
            not default <= threshold < review for k, threshold in thresholds.items()
            if k not in {"issuer.warning.default", "issuer.review"}):
        raise PolicyError("INVALID_ISSUER_THRESHOLD_ORDER")
    return changes


def derive_revision(base, previous_philosophy, previous_policy, philosophy, policy, version, bindings):
    if not isinstance(base, SnapshotV2) or not philosophy.strip() or not policy.strip():
        raise PolicyError("VERSIONED_POLICY_BASE_REQUIRED")
    if content_only(philosophy) != philosophy or content_only(policy) != policy:
        raise PolicyError("APPROVAL_METADATA_MUST_BE_EXTERNAL")
    old_raw = content_only(previous_policy)
    old = Document(old_raw, sha256(old_raw).hexdigest(), "policy")
    new = Document(policy, sha256(policy).hexdigest(), "policy")
    if list(new.sections) not in (list(old.sections), [*old.sections, "11"]):
        raise PolicyError("UNSUPPORTED_POLICY_SECTION_CHANGE")
    if "11" in new.sections and new.ref("11").text.splitlines()[0] != DESCRIPTIVE_HEADING:
        raise PolicyError("DESCRIPTIVE_POLICY_SECTION_REQUIRED")
    if old.lines[:next(iter(old.sections.values()))[0]] != new.lines[:next(iter(new.sections.values()))[0]]:
        raise PolicyError("UNSUPPORTED_POLICY_PREAMBLE_CHANGE")
    for section in old.sections.keys() - {"6.1", "11"}:
        if old.ref(section).text.rstrip("\n") != new.ref(section).text.rstrip("\n"):
            raise PolicyError("UNSUPPORTED_EXECUTABLE_POLICY_CHANGE")
    prior_rules = {rule.rule_id: rule for rule in base.rules}
    changed_rules = _issuer_thresholds(old, new, prior_rules)

    ph_sha = sha256(philosophy).hexdigest()
    refs = {section: new.ref(section) for section in new.sections}
    refs["philosophy"] = SourceClause(document="philosophy", source_sha256=ph_sha, section="full",
        line_start=1, line_end=len(philosophy.splitlines()), text=philosophy.decode("utf-8"))
    rules = [Rule.model_validate(rule.model_dump() | {
        "policy_version":version, "source_clause":refs[rule.source_clause.section],
        "threshold":changed_rules.get(rule.rule_id, rule.threshold)}) for rule in base.rules]
    allocation = base.allocation.model_dump() | {
        "source_clauses":{section:refs[section] for section in base.allocation.source_clauses}}
    scope = ConfiguredStrategicScope.model_validate(base.strategic_review_scope.model_dump() | {"source_clause":refs["6.3"]})
    fields = dict(policy_version=version, source_hashes={"policy":new.sha, "philosophy":ph_sha},
        inherited_content_approval_date=(base.inherited_content_approval_date
            if isinstance(base, ExternalSnapshot) else base.content_approved_on),
        allocation=allocation, rules=rules,
        contextual_clauses=refs, strategic_review_scope=scope, **bindings)
    if hasattr(base, "btc_governance"):
        from .btc_governance import SECTIONS
        snapshot = ExternalBtcSnapshot(**fields, btc_governance=base.btc_governance.model_dump() | {
            "source_clauses":{key:refs[key] for key in SECTIONS}})
    else:
        snapshot = ExternalSnapshot(**fields)
    changes = {"executable_thresholds":{key:{"before":pct(prior_rules[key].threshold), "after":pct(value)}
               for key,value in changed_rules.items()}, "descriptive_diff":{}}
    for label, previous, revised in (("philosophy", previous_philosophy, philosophy),
        ("policy_notes", old.ref("11").text.encode() if "11" in old.sections else b"",
         new.ref("11").text.encode() if "11" in new.sections else b"")):
        if previous != revised:
            changes["descriptive_diff"][label] = list(difflib.unified_diff(
                previous.decode().splitlines(), revised.decode().splitlines(), lineterm=""))
    return snapshot, changes


def source_bindings(base):
    if isinstance(base, ConfiguredSnapshot):
        return dict(target_issuers=base.target_issuers, target_symbols=base.target_symbols,
                    listings={key:item.model_dump(mode="json") for key, item in base.listings.items()})
    config = from_snapshot(base)
    labels, symbols = {}, {}
    for label in base.allocation.direct_equity.keys() - {"unallocated"}:
        tickers, issuer = policy_target_identity(label)
        if issuer not in config.direct_issuer_targets:
            raise PolicyError("BASE_TARGET_IDENTITY_UNAVAILABLE")
        labels[label] = issuer
        symbols.update({symbol+":US":issuer for symbol in tickers})
    return dict(target_issuers=labels, target_symbols=symbols, listings={})



def make_bundle(store, db, scope, parent_id, sources, request_id, head, at, bindings):
    row = db.execute("SELECT * FROM policy_bundles WHERE scope=? AND bundle_id=?", (scope,parent_id)).fetchone()
    if row is None or row["version"] > head:
        raise PolicyError("INVALID_EXTERNAL_POLICY_PREDECESSOR")
    base = parse_snapshot(store._bundle(db, scope, parent_id)["snapshot"])
    ph, policy = sources
    snapshot, changes = derive_revision(base, bytes(row["philosophy"]), bytes(row["policy"]), ph, policy, head+1, bindings)
    bundle_id = "policy-" + digest(dict(scope=scope, request_id=request_id, version=head+1,
        predecessor=parent_id, source_hashes=snapshot.source_hashes, at=at))[:24]
    return dict(scope=scope, bundle_id=bundle_id, bundle_version=head+1, predecessor_version=head,
        source_hashes=snapshot.source_hashes, source_files={"philosophy":"philosophy.md", "policy":"policy.md"},
        snapshot=snapshot.model_dump(mode="json"), structured_sha256=digest(snapshot), created_at=at,
        content_approved_on=snapshot.content_approved_on, content_status=snapshot.content_status,
        deployment_status="NOT_DEPLOYED", effective_at=None, external_predecessor=parent_id, external_bindings=bindings), changes

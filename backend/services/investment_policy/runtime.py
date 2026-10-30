"""One policy owner and per-analysis pin. Default LEGACY; no initialization/write."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import json
import os

from pydantic import Field

from .contracts import Contract, MetricEvidence, Snapshot, canonical
from .evaluation import evaluate_snapshot
from .parser import PolicyError
from .service import PolicyService
from .store import PolicyStore, clock, utc_now

POLICY_ENV_KEYS = {"WEALTHPILOT_POLICY_DB_PATH", "WEALTHPILOT_POLICY_SCOPE"}
_current = ContextVar("wealthpilot_policy_session", default=None)


class PolicyAssets(Contract):
    """Explicit source-owner qualification; never accepted as MCP tool arguments.

    Stored investment-only holdings cannot prove a full investable denominator.
    Pension exclusions, deduplication, FX, external gold/BTC and freshness must
    already be evidenced by the asset owner. Otherwise measurements stay UNKNOWN.
    """
    portfolio_id: int
    snapshot_id: str
    as_of: str
    denominator_cny: Decimal | None = None
    denominator_qualified: bool = False
    complete_inventory: bool = False
    fx_qualified: bool = False
    pension_exclusion_verified: bool = False
    deduplication_verified: bool = False
    external_assets_verified: bool = False
    coverage_evidence: list[str] = Field(default_factory=list)
    issuer_weights: dict[str, MetricEvidence] = Field(default_factory=dict)
    bucket_weights: dict[str, MetricEvidence] = Field(default_factory=dict)
    btc_weight: MetricEvidence | None = None
    major_class_weights: dict[str, MetricEvidence] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)


def resolve(portfolio_id):
    path = os.environ.get("WEALTHPILOT_POLICY_DB_PATH")
    scope = os.environ.get("WEALTHPILOT_POLICY_SCOPE")
    if not path:
        return {"mode": "LEGACY", "status": "NOT_ACTIVE", "policy_reference": None, "reason": "NO_POLICY_CONFIG"}
    if scope != f"portfolio:{portfolio_id}":
        return {"mode": "UNAVAILABLE", "status": "UNAVAILABLE", "policy_reference": None, "reason": "POLICY_SCOPE_MISMATCH"}
    try:
        value = PolicyService(PolicyStore(path)).get_active_policy_context(scope)
    except (ValueError, OSError):
        value = {"status": "UNAVAILABLE", "reason": "POLICY_STORE_UNAVAILABLE", "policy_reference": None}
    value["mode"] = {"ACTIVE": "POLICY_ACTIVE", "NOT_ACTIVE": "LEGACY"}.get(value["status"], "UNAVAILABLE")
    value["read_at"] = utc_now()
    return value


class PolicySession:
    def __init__(self, portfolio_id, *, assets=None):
        self.portfolio_id = portfolio_id
        self._payload = canonical(resolve(portfolio_id))
        self.assets = assets
        self.receipts = {}

    @property
    def context(self):
        return json.loads(self._payload)

    @property
    def mode(self):
        return self.context["mode"]

    def ensure_current(self):
        actual = resolve(self.portfolio_id)
        if actual["mode"] != self.mode or actual.get("policy_reference") != self.context.get("policy_reference"):
            raise PolicyError("POLICY_VERSION_CONFLICT")
        if actual["status"] == "UNAVAILABLE":
            raise PolicyError("POLICY_CONTEXT_UNAVAILABLE")

    def consumer(self, name):
        self.ensure_current()
        self.receipts[name] = self.context.get("policy_reference")
        return self.context

    def attach_asset_view(self, view):
        """Consume only the existing frozen-input owner's snapshot, no refresh.

        Old investment-only eligibility is not full policy qualification. A new
        source-owner receipt must bind the same immutable ID/scope; absence is
        explicit UNKNOWN, never inferred from net worth or a recent capture.
        """
        if not view or not view.get('snapshot_id'):
            return
        if view.get('portfolio_id')!=self.portfolio_id:
            raise PolicyError('POLICY_ASSET_SCOPE_MISMATCH')
        if self.assets and self.assets.snapshot_id!=view['snapshot_id']:
            raise PolicyError('POLICY_ASSET_SNAPSHOT_CONFLICT')
        if self.assets:
            return
        receipt=view.get('data_quality',{}).get('policy_asset_qualification')
        if receipt:
            assets=PolicyAssets.model_validate(receipt)
            if assets.snapshot_id!=view['snapshot_id'] or assets.portfolio_id!=self.portfolio_id:
                raise PolicyError('POLICY_ASSET_RECEIPT_MISMATCH')
            self.assets=assets
        else:
            self.assets=PolicyAssets(portfolio_id=self.portfolio_id,snapshot_id=view['snapshot_id'],
                as_of=view.get('captured_at') or utc_now(),issues=['LEGACY_U1_IS_NOT_FULL_INVESTABLE_QUALIFICATION',
                    *view.get('data_quality',{}).get('issues',[])])


@contextmanager
def bind(session):
    token = _current.set(session)
    try:
        yield session
    finally:
        _current.reset(token)


def current(portfolio_id=None):
    session = _current.get()
    if session and (portfolio_id is None or session.portfolio_id == portfolio_id):
        return session
    if session:
        raise PolicyError("POLICY_SCOPE_MISMATCH")
    if portfolio_id is None:
        import sys
        portfolio_id = getattr(sys.modules.get('app.state'), 'portfolio_id', 0) or 1
    return PolicySession(portfolio_id)


def context(portfolio_id=None, *, consumer="policy", view="full", symbol=None):
    if view not in {"full", "portfolio", "position", "risk", "philosophy"}:
        raise PolicyError("POLICY_VIEW_INVALID")
    session = current(portfolio_id)
    value = session.consumer(consumer)
    value = deepcopy(value)
    if value["mode"] != "POLICY_ACTIVE":
        return value
    snap = value.pop("snapshot")
    value.update(bundle_id=value["policy_reference"]["bundle_id"], bundle_version=value["policy_reference"]["bundle_version"],
        bundle_sha=value["policy_reference"]["bundle_sha256"], scope=value["policy_reference"]["scope"],
        targets=snap["allocation"], rules=snap["rules"], philosophy=snap["contextual_clauses"]["philosophy"],
        clauses=snap["contextual_clauses"], precision="UNKNOWN_REQUIRES_QUALIFIED_ASSET_VIEW", execution_authorized=False)
    value['synthetic']=value.get('release',{}).get('synthetic',False) is True
    value['obligations'] = {k: v for k,v in snap['contextual_clauses'].items()
                            if k in {'5.2','5.3','6.3','7.1','7.2','7.3','8','9.2','10'}}
    if view == "position":
        # Source identity is preserved; no guessing aliases/issuer mappings.
        direct = snap["allocation"]["direct_equity"]
        value["position_target"] = {k:v for k,v in direct.items() if symbol and symbol in k.split("（")[0].split("/")}
    if view in {'portfolio','position','full','risk'}:
        with bind(session):
            value['risk_diagnostic'] = diagnose(session.portfolio_id,issuer_symbol=symbol)
    if view == "risk":
        for key in ("targets", "philosophy", "clauses"):
            value.pop(key, None)
    if view == "philosophy":
        for key in ("targets", "rules", "clauses", "obligations"):
            value.pop(key, None)
    if view != "full":
        value.pop("clauses", None)
    return value


def diagnose(portfolio_id=None, *, issuer_symbol=None, behaviors=None):
    session = current(portfolio_id)
    portfolio_id = session.portfolio_id
    raw = session.consumer("discipline")
    if raw["mode"] != "POLICY_ACTIVE":
        return {"status": "UNKNOWN", "mode": raw["mode"], "policy_reference": raw.get("policy_reference"), "auto_trade_allowed": False}
    metrics, qualified = {}, False
    assets = session.assets
    if assets and assets.portfolio_id == portfolio_id:
        # Qualification is necessary even when the individual metric claims it.
        qualified = assets_qualified(session)
        if qualified:
            def qualify(evidence):
                return evidence.model_copy(update={'complete':evidence.complete and assets.complete_inventory})
            if issuer_symbol and issuer_symbol in assets.issuer_weights:
                metrics["issuer_weight"] = qualify(assets.issuer_weights[issuer_symbol])
            if assets.btc_weight:
                metrics["btc_weight"] = qualify(assets.btc_weight)
    result = evaluate_snapshot(Snapshot.model_validate(raw["snapshot"]), metrics=metrics, issuer_symbol=issuer_symbol,
        behavior_facts=behaviors or {}, as_of=utc_now(), max_age=timedelta(minutes=5))
    # Portfolio diagnostics retain every evidenced issuer risk. UNKNOWN coverage
    # cannot replace a known breach; repeat only issuer-specific rules here.
    result['issuers']={}
    if issuer_symbol is None and assets and qualified:
        with bind(session):
            for symbol in assets.issuer_weights:
                checked=diagnose(portfolio_id,issuer_symbol=symbol,behaviors=behaviors)
                triggers=[dict(t,issuer=symbol) for t in checked['triggers'] if t['rule_id'].startswith('issuer.')]
                unknown=[dict(u,issuer=symbol) for u in checked['unknown'] if u.get('rule_id','').startswith('issuer.')]
                result['issuers'][symbol]={'triggers':triggers,'unknown':unknown}
                result['triggers'].extend(triggers)
                result['unknown'].extend(unknown)
    # Evaluate each class with its own deviation evidence, never use a netted average.
    result["major_classes"] = {}
    if assets and qualified:
        expected = {'growth_and_alternatives':Snapshot.model_validate(raw['snapshot']).allocation.growth_total,
                    'stable_pool':Snapshot.model_validate(raw['snapshot']).allocation.stable_total}
        for key, evidence in assets.major_class_weights.items():
            if key not in expected:
                result['unknown'].append({'reason':'NOT_A_POLICY_MAJOR_CLASS','class':key})
                continue
            value = abs(evidence.value-expected[key]) if evidence.value is not None else None
            evidence = qualify(evidence).model_copy(update={'value':value})
            result["major_classes"][key] = evaluate_snapshot(Snapshot.model_validate(raw["snapshot"]),
                metrics={"major_class_absolute_deviation": evidence}, issuer_symbol=None,
                behavior_facts={}, as_of=utc_now(), max_age=timedelta(minutes=5))
            result['triggers'].extend(dict(t,major_class=key) for t in result['major_classes'][key]['triggers'])
            result['unknown'].extend(result['major_classes'][key]['unknown'])
        actions={item['action'] for item in result['triggers']}
        for level in ['HARD_BLOCK','HUMAN_REVIEW','WARNING']:
            if level in actions:
                result['status']=level
                break
    result.update(policy_reference=raw["policy_reference"], mode=raw["mode"],
        asset_snapshot_id=assets.snapshot_id if assets else None,
        asset_issues=assets.issues if assets else ["FULL_INVESTABLE_ASSET_DENOMINATOR_UNVERIFIED", "PENSION_DEDUP_FX_EXTERNAL_GOLD_BTC_COVERAGE_UNKNOWN"])
    return result


def assets_qualified(session, *, complete=False):
    assets=session.assets
    if not assets or assets.portfolio_id!=session.portfolio_id:
        return False
    try:
        return bool(assets.denominator_qualified and assets.denominator_cny is not None and assets.denominator_cny.is_finite()
            and assets.denominator_cny>0 and assets.coverage_evidence and assets.fx_qualified
            and assets.pension_exclusion_verified and assets.deduplication_verified and assets.external_assets_verified
            and (assets.complete_inventory or not complete)
            and timedelta(0)<=clock(utc_now())-clock(assets.as_of)<=timedelta(minutes=5))
    except PolicyError:
        return False


def assert_editable(portfolio_id=None):
    if current(portfolio_id).mode != "LEGACY":
        raise PolicyError("POLICY_BUNDLE_READ_ONLY_USE_CONTROLLED_RELEASE")


def prompt(portfolio_id):
    value = context(portfolio_id, consumer="peer", view="portfolio")
    if value["mode"] == "LEGACY":
        return ""
    return "\n[唯一生效政策，数值只使用本结构；政策不是交易授权；旧索引和旧数值字段不适用。仓位/分母缺失为UNKNOWN，禁止生成精确交易数量。]\n" + canonical(value)


class AbsentLegacyThreshold(str):
    """Compatibility with original prompt percent formatting; never a number."""
    def __new__(cls, value=None):
        return super().__new__(cls, "NOT_APPLICABLE_USE_POLICY_REFERENCE")

    def __format__(self, spec):
        return str(self)


def sanitize_loaded(loaded, portfolio_id):
    raw = current(portfolio_id).consumer("peer")
    if raw["mode"] == "LEGACY":
        return loaded
    from copy import copy
    from decision_engine.data_loader import InvestmentRules
    loaded = copy(loaded)
    loaded.rules = InvestmentRules(*[AbsentLegacyThreshold() for _ in range(4)])
    loaded.full_discipline_rules = {}
    loaded.retrieved_principles = []  # Unversioned Chroma is not formal policy.
    loaded.policy_context = context(portfolio_id, consumer="peer", view="portfolio")
    loaded.rules_provenance = raw.get("policy_reference") or {"status": "UNAVAILABLE"}
    return loaded

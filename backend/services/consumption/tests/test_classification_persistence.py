"""Persistence Golden Cases A–AE for the deterministic classification layer."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app.database import Base
from backend.services.consumption.classification import ClassificationResolver
from backend.scripts.replay_consumption_classification import (
    automatic_consumption_event_ids,
    replay_automatic_consumption,
)
from backend.scripts.apply_consumption_rule import apply_local_rule
from backend.services.consumption.classification_design import (
    ClassificationStatus, EligibilityStatus, PrimaryCategory,
)
from backend.services.consumption.economic_events import EventType, FxSource, ResolutionStatus
from backend.services.consumption.models import (
    Account, AccountPurposePreference, ConsumptionInterpretation,
    ConsumptionInterpretationAudit, EconomicEvent, EventRawLink, ImportBatch,
    RawTransaction, TravelContext, UserClassificationRule,
)


FIXTURES = Path(__file__).parents[4] / "tests" / "fixtures" / "consumption" / "classification"


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _account(session, account_id: str) -> Account:
    found = session.get(Account, account_id)
    if found:
        return found
    found = Account(id=account_id, institution="TEST", account_type="CREDIT_CARD")
    session.add(found)
    session.flush()
    return found


def _event(session, event_id: str, event_type: EventType, description: str, *, account_id: str = "card",
           when: date = date(2026, 7, 1), amount: str = "88", original_event_id: str | None = None,
           parser_provenance: str = "{}") -> EconomicEvent:
    account = _account(session, account_id)
    batch = ImportBatch(id=f"batch-{event_id}", account_id=account.id, source_format="TEST", institution="TEST",
        statement_type=account.account_type, source_file_hash=(event_id * 64)[:64], parser_version="test",
        statement_period_availability="SOURCE_UNAVAILABLE", coverage_status="OBSERVED_ONLY", row_count=1)
    raw = RawTransaction(id=f"raw-{event_id}", import_batch_id=batch.id, account_id=account.id,
        source_row_index=1, source_row_identity=f"row-{event_id}", source_row_fingerprint_candidate=f"source-{event_id}",
        match_fingerprint=f"match-{event_id}", dedup_status="UNIQUE", transaction_date=when,
        transaction_date_availability="AVAILABLE", posting_date=None, posting_date_availability="SOURCE_UNAVAILABLE",
        amount=Decimal(amount), currency="CNY", raw_description=description, parser_provenance=parser_provenance, source_field_availability="{}")
    event = EconomicEvent(id=event_id, semantic_key=f"key-{event_id}", event_type=event_type.value, event_date=when,
        analytics_effective_date=when, amount=abs(Decimal(amount)), currency="CNY", economic_direction="OUTFLOW",
        base_currency="CNY", base_amount=abs(Decimal(amount)), fx_rate=Decimal("1"), fx_source=FxSource.NATIVE_CNY.value,
        resolution_status=ResolutionStatus.RESOLVED.value, normalizer_version="test", rule_sources="[]", provenance="{}",
        original_event_id=original_event_id)
    session.add_all((batch, raw, event))
    session.flush()
    session.add(EventRawLink(event_id=event.id, raw_transaction_id=raw.id, link_role="PRIMARY", rule_source="TEST", evidence="{}"))
    session.flush()
    return event


def _project(item: ConsumptionInterpretation, fields: list[str]) -> dict[str, str | None]:
    values = {
        "event_id": item.event_id, "eligibility_status": item.eligibility_status,
        "eligibility_source": item.eligibility_source, "eligibility_reason": item.eligibility_reason,
        "classification_status": item.classification_status, "primary_category": item.primary_category,
        "secondary_category": item.secondary_category, "classification_source": item.classification_source,
        "classification_reason": item.classification_reason, "applied_rule_id": item.rule_id,
        "inherited_from_event_id": item.inherited_from_event_id,
    }
    return {field: values[field] for field in fields}


@pytest.mark.parametrize("case_dir", sorted(path for path in FIXTURES.iterdir() if path.is_dir()), ids=lambda path: path.name.upper())
def test_a_through_t_persisted_golden_cases(db_session, case_dir):
    events_data = json.loads((case_dir / "input_events.json").read_text())
    context = json.loads((case_dir / "context.json").read_text())
    expected = json.loads((case_dir / "expected_classification.json").read_text())
    events = {}
    for value in events_data:
        events[value["event_id"]] = _event(db_session, value["event_id"], EventType(value["event_type"]), value["descriptor"],
            account_id=value["account_id"], when=date.fromisoformat(value["event_date"]) if value.get("event_date") else date(2026, 7, 1),
            amount=value.get("amount", "0"), original_event_id=value.get("original_event_id"))
    for value in context.get("account_purpose_priors", []):
        _account(db_session, value["account_id"])
        db_session.add(AccountPurposePreference(account_id=value["account_id"], preferred_primary_category=value["preferred_primary"],
            effective_from=date.fromisoformat(value["effective_from"]), effective_to=date.fromisoformat(value["effective_to"]) if value.get("effective_to") else None))
    for value in context.get("travel_contexts", []):
        db_session.add(TravelContext(destination=value["destination"], start_date=date.fromisoformat(value["start_date"]), end_date=date.fromisoformat(value["end_date"])))
    for value in context.get("user_rules", []):
        db_session.add(UserClassificationRule(id=value["rule_id"], eligibility_action=value["eligibility_status"],
            primary_category=value.get("primary_category"), secondary_category=value.get("secondary_category"),
            account_id=value.get("account_id"), match_text=value.get("match_text"),
            amount=Decimal(value["amount"]) if value.get("amount") is not None else None,
            amount_tolerance=Decimal(value.get("amount_tolerance", "0")),
            effective_from=date.fromisoformat(value["effective_from"]) if value.get("effective_from") else None,
            effective_to=date.fromisoformat(value["effective_to"]) if value.get("effective_to") else None))
    db_session.flush()
    resolver = ClassificationResolver()
    for value in context.get("user_confirmations", []):
        resolver.confirm_event(db_session, value["event_id"], eligibility_status=EligibilityStatus(value["eligibility_status"]),
            primary_category=PrimaryCategory(value["primary_category"]) if value.get("primary_category") else None,
            secondary_category=value.get("secondary_category"), reason=value.get("reason", "USER_CONFIRMED"))
    actual = [resolver.resolve_event(db_session, events[value["event_id"]]) for value in events_data]
    assert [_project(item, expected["fields"]) for item in actual] == expected["results"]
    assert tuple(resolver.replay(db_session)) == tuple(actual)


def test_u_eligible_unknown_is_persisted_and_schema_is_complete(db_session):
    event = _event(db_session, "u", EventType.CONSUMPTION, "unrecognised merchant")
    result = ClassificationResolver().resolve_event(db_session, event)
    assert (result.eligibility_status, result.classification_status, result.primary_category) == ("ELIGIBLE", "NEEDS_REVIEW", None)
    tables = set(inspect(db_session.bind).get_table_names())
    assert {"consumption_interpretations", "consumption_interpretation_audits", "consumption_user_rules", "consumption_travel_contexts", "consumption_account_purpose_preferences"}.issubset(tables)


@pytest.mark.parametrize(("description", "primary", "secondary"), [
    ("兰州拉面", "DAILY", "FOOD_DINING"),
    ("云南米粉", "DAILY", "FOOD_DINING"),
    ("过桥米线", "DAILY", "FOOD_DINING"),
    ("冒菜", "DAILY", "FOOD_DINING"),
    ("麻辣烫", "DAILY", "FOOD_DINING"),
    ("精品咖啡", "DAILY", "FOOD_DINING"),
    ("快餐", "DAILY", "FOOD_DINING"),
    ("特色小吃", "DAILY", "FOOD_DINING"),
    ("包点", "DAILY", "FOOD_DINING"),
    ("饭店", "DAILY", "FOOD_DINING"),
    ("小湘厨木桶饭", "DAILY", "FOOD_DINING"),
    ("柳螺记螺蛳粉", "DAILY", "FOOD_DINING"),
    ("隆江猪脚饭", "DAILY", "FOOD_DINING"),
    ("川浩麻辣香锅", "DAILY", "FOOD_DINING"),
    ("湖南常德牛肉粉", "DAILY", "FOOD_DINING"),
    ("徐掌柜煲仔饭", "DAILY", "FOOD_DINING"),
    ("八角面馆", "DAILY", "FOOD_DINING"),
    ("田金花卤味", "DAILY", "FOOD_DINING"),
    ("鲜猪杂汤粉王", "DAILY", "FOOD_DINING"),
    ("十里鸡公煲", "DAILY", "FOOD_DINING"),
    ("浙里食局", "DAILY", "FOOD_DINING"),
    ("欧粑粑", "DAILY", "FOOD_DINING"),
    ("停车服务", "DAILY", "TRANSPORT_AUTO"),
    ("快充服务", "DAILY", "TRANSPORT_AUTO"),
    ("车辆充电", "DAILY", "TRANSPORT_AUTO"),
    ("通行宝", "DAILY", "TRANSPORT_AUTO"),
    ("顺易通", "DAILY", "TRANSPORT_AUTO"),
    ("中国石化", "DAILY", "TRANSPORT_AUTO"),
    ("中国石油", "DAILY", "TRANSPORT_AUTO"),
    ("XX加油站", "DAILY", "TRANSPORT_AUTO"),
    ("XX能源有限公司", "DAILY", "TRANSPORT_AUTO"),
    ("地铁", "DAILY", "TRANSPORT_AUTO"),
    ("停简单平台商户", "DAILY", "TRANSPORT_AUTO"),
    ("停车场", "DAILY", "TRANSPORT_AUTO"),
    ("理想汽车", "DAILY", "TRANSPORT_AUTO"),
    ("宠物用品", "DAILY", "PET"),
    ("猫粮", "DAILY", "PET"),
    ("猫砂", "DAILY", "PET"),
    ("冲浪课程", "DAILY", "SPORTS_HOBBY"),
    ("健身中心", "DAILY", "SPORTS_HOBBY"),
    ("男装", "DAILY", "SHOPPING"),
    ("女装", "DAILY", "SHOPPING"),
    ("服饰", "DAILY", "SHOPPING"),
    ("品牌专卖店", "DAILY", "SHOPPING"),
])
def test_high_confidence_generic_merchant_semantics_use_raw_description(db_session, description, primary, secondary):
    event = _event(db_session, f"semantic-{description}", EventType.CONSUMPTION, description)
    result = ClassificationResolver().resolve_event(db_session, event)
    assert (result.classification_status, result.primary_category, result.secondary_category) == (
        "CLASSIFIED", primary, secondary,
    )


@pytest.mark.parametrize(("description", "primary", "secondary"), [
    ("App Store 订阅", "DAILY", "DIGITAL_COMMUNICATION"),
    ("云上艾珀服务", "DAILY", "DIGITAL_COMMUNICATION"),
    ("中国联通话费", "DAILY", "DIGITAL_COMMUNICATION"),
    ("中国移动话费", "DAILY", "DIGITAL_COMMUNICATION"),
    ("公共事业缴费", "DAILY", "HOME_LIVING"),
    ("公用事业缴费", "DAILY", "HOME_LIVING"),
    ("水费缴纳", "DAILY", "HOME_LIVING"),
    ("社保缴费", "DAILY", "HOME_LIVING"),
    ("京东家政", "DAILY", "HOME_LIVING"),
    ("电力缴费", "DAILY", "HOME_LIVING"),
    ("城市体育中心", "DAILY", "SPORTS_HOBBY"),
    ("杭州乐刻网络技术有限公司", "DAILY", "SPORTS_HOBBY"),
    ("中铁出行", "TRAVEL", "LONG_DISTANCE_TRANSPORT"),
    ("航旅纵横会员服务", "TRAVEL", "LONG_DISTANCE_TRANSPORT"),
    ("天猫超市", "DAILY", "SHOPPING"),
])
def test_additional_high_confidence_semantics(db_session, description, primary, secondary):
    event = _event(db_session, f"additional-{description}", EventType.CONSUMPTION, description)
    result = ClassificationResolver().resolve_event(db_session, event)
    assert (result.classification_status, result.primary_category, result.secondary_category) == (
        "CLASSIFIED", primary, secondary,
    )


@pytest.mark.parametrize("description", [
    "普通财付通商户",
    "支付宝普通商户",
    "某某科技有限公司",
    "个人收款",
    "某品牌旗舰店",
    "阿宝果业",
])
def test_ambiguous_merchant_semantics_remain_needs_review(db_session, description):
    event = _event(db_session, f"ambiguous-{description}", EventType.CONSUMPTION, description)
    result = ClassificationResolver().resolve_event(db_session, event)
    assert (result.classification_status, result.primary_category, result.secondary_category) == (
        "NEEDS_REVIEW", None, None,
    )


@pytest.mark.parametrize(("description", "secondary"), [
    ("拼多多支付-宠物用品店", "PET"),
    ("拼多多支付-男装旗舰店", "SHOPPING"),
    ("拼多多支付-品牌好货", "SHOPPING"),
    ("拼多多平台商户", "SHOPPING"),
])
def test_pinduoduo_falls_back_to_shopping_after_more_specific_semantics(db_session, description, secondary):
    event = _event(db_session, f"pdd-{description}", EventType.CONSUMPTION, description)
    result = ClassificationResolver().resolve_event(db_session, event)
    assert (result.classification_status, result.primary_category, result.secondary_category) == (
        "CLASSIFIED", "DAILY", secondary,
    )


@pytest.mark.parametrize(("description", "secondary"), [
    ("咖啡", "FOOD_DINING"),
    ("停车服务", "LOCAL_TRANSPORT"),
    ("柳螺记螺蛳粉", "FOOD_DINING"),
    ("小湘厨木桶饭", "FOOD_DINING"),
])
def test_travel_context_overrides_generic_food_and_transport_semantics(db_session, description, secondary):
    db_session.add(TravelContext(destination="HK", start_date=date(2026, 7, 1), end_date=date(2026, 7, 3)))
    db_session.flush()
    event = _event(db_session, f"travel-{description}", EventType.CONSUMPTION, description, when=date(2026, 7, 2))
    result = ClassificationResolver().resolve_event(db_session, event)
    assert (result.primary_category, result.secondary_category, result.classification_source) == (
        "TRAVEL", secondary, "TRAVEL_CONTEXT",
    )


def test_generic_semantic_rules_are_month_invariant_and_replay_is_idempotent(db_session):
    historical = _event(
        db_session, "historic-transport", EventType.CONSUMPTION, "支付宝-中国石化加油站",
        when=date(2025, 9, 10),
    )
    august = _event(
        db_session, "august-transport", EventType.CONSUMPTION, "财付通-中国石化加油站",
        when=date(2026, 8, 10),
    )
    resolver = ClassificationResolver()

    first = resolver.replay(db_session, (historical.id, august.id))
    assert {(item.primary_category, item.secondary_category) for item in first} == {
        ("DAILY", "TRANSPORT_AUTO"),
    }
    assert resolver.replay(db_session, (historical.id, august.id)) == first


def test_empty_and_existing_sqlite_initialization_is_idempotent(tmp_path, monkeypatch):
    from app import database

    path = tmp_path / "existing-consumption.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TABLE consumption_accounts (
                id VARCHAR(36) PRIMARY KEY, institution VARCHAR(30) NOT NULL,
                account_type VARCHAR(30) NOT NULL, display_name VARCHAR(120),
                masked_account_identifier VARCHAR(64), base_currency VARCHAR(3) NOT NULL,
                status VARCHAR(20) NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
            )
        """)
    engine.dispose()
    monkeypatch.setattr(database, "DB_PATH", str(path))
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_SessionLocal", None)
    database.init_db(); database.init_db()
    tables = set(inspect(database.get_engine()).get_table_names())
    assert "consumption_interpretations" in tables
    database.get_engine().dispose()


def test_v_confirmation_appends_auditable_revision(db_session):
    event = _event(db_session, "v", EventType.OTHER, "房东转账", account_id="debit", amount="6500")
    resolver = ClassificationResolver()
    first = resolver.resolve_event(db_session, event)
    second = resolver.confirm_event(db_session, event.id, eligibility_status=EligibilityStatus.ELIGIBLE,
        primary_category=PrimaryCategory.HOUSING, secondary_category="RENT", reason="LOCAL_RENT", actor_id="local-user")
    revisions = db_session.query(ConsumptionInterpretation).filter_by(event_id=event.id).order_by(ConsumptionInterpretation.revision_number).all()
    audit = db_session.query(ConsumptionInterpretationAudit).one()
    assert [(item.revision_number, item.is_active) for item in revisions] == [(1, False), (2, True)]
    assert (second.user_confirmed, audit.old_interpretation_id, audit.new_interpretation_id, audit.actor_type) == (True, first.id, second.id, "LOCAL_USER")


def test_w_x_rule_replay_is_idempotent_and_cannot_override_confirmation(db_session):
    event = _event(db_session, "w", EventType.OTHER, "房东 房租", account_id="debit", amount="6500")
    rule = UserClassificationRule(eligibility_action="ELIGIBLE", primary_category="HOUSING", secondary_category="RENT",
        account_id="debit", match_text="房东", amount=Decimal("6500"), amount_tolerance=Decimal("1"), effective_from=date(2026, 1, 1))
    db_session.add(rule); db_session.flush()
    resolver = ClassificationResolver()
    first = resolver.resolve_event(db_session, event)
    assert resolver.resolve_event(db_session, event).id == first.id
    confirmed = resolver.confirm_event(db_session, event.id, eligibility_status=EligibilityStatus.ELIGIBLE,
        primary_category=PrimaryCategory.DAILY, secondary_category="FOOD_DINING")
    assert resolver.replay(db_session, (event.id,))[0].id == confirmed.id


def test_automatic_replay_skips_confirmations_and_user_rules_and_is_idempotent(db_session):
    resolver = ClassificationResolver()
    automatic = _event(db_session, "automatic", EventType.CONSUMPTION, "拼多多平台商户")
    automatic_interpretation = resolver.resolve_event(db_session, automatic)
    automatic_interpretation.resolver_version = "consumption-classification-v2"

    confirmed = _event(db_session, "confirmed", EventType.CONSUMPTION, "拼多多平台商户")
    confirmed_interpretation = resolver.confirm_event(
        db_session, confirmed.id, eligibility_status=EligibilityStatus.ELIGIBLE,
        primary_category=PrimaryCategory.DAILY, secondary_category="FOOD_DINING",
    )

    rule = UserClassificationRule(
        eligibility_action="ELIGIBLE", primary_category="HOUSING", secondary_category="RENT",
        match_text="规则保护", effective_from=date(2026, 1, 1),
    )
    db_session.add(rule)
    db_session.flush()
    ruled = _event(db_session, "ruled", EventType.CONSUMPTION, "规则保护")
    ruled_interpretation = resolver.resolve_event(db_session, ruled)

    assert automatic_consumption_event_ids(db_session) == (automatic.id,)
    first = replay_automatic_consumption(db_session)
    assert (first.target_event_count, first.updated_interpretation_count, first.skipped_user_explicit_count) == (1, 1, 2)
    assert resolver.resolve_event(db_session, automatic).resolver_version == "consumption-classification-v3"
    assert resolver.resolve_event(db_session, confirmed).id == confirmed_interpretation.id
    assert resolver.resolve_event(db_session, ruled).id == ruled_interpretation.id
    assert replay_automatic_consumption(db_session).updated_interpretation_count == 0


def test_y_z_account_prior_is_weak_and_rule_dates_are_inclusive(db_session):
    event = _event(db_session, "z", EventType.OTHER, "固定收款人", account_id="debit", when=date(2026, 8, 1), amount="6500")
    db_session.add(AccountPurposePreference(account_id="debit", preferred_primary_category="TRAVEL", effective_from=date(2026, 8, 1)))
    rule = UserClassificationRule(eligibility_action="ELIGIBLE", primary_category="HOUSING", secondary_category="RENT",
        account_id="debit", match_text="固定收款人", amount=Decimal("6500"), amount_tolerance=Decimal("0"),
        effective_from=date(2026, 8, 1), effective_to=date(2026, 8, 1))
    db_session.add(rule); db_session.flush()
    assert ClassificationResolver().resolve_event(db_session, event).primary_category == "HOUSING"
    event_two = _event(db_session, "y", EventType.CONSUMPTION, "unknown", account_id="debit", when=date(2026, 8, 2))
    result = ClassificationResolver().resolve_event(db_session, event_two)
    assert (result.classification_status, result.classification_source) == ("NEEDS_REVIEW", "ACCOUNT_PURPOSE_PRIOR")


def test_aa_ab_ac_ad_travel_inactive_rule_property_and_foreign_digital_priority(db_session):
    resolver = ClassificationResolver()
    db_session.add(TravelContext(destination="HK", start_date=date(2026, 7, 3), end_date=date(2026, 7, 6)))
    db_session.add(UserClassificationRule(eligibility_action="ELIGIBLE", primary_category="TRAVEL", secondary_category="OTHER", match_text="unused", status="INACTIVE"))
    db_session.add(AccountPurposePreference(account_id="card", preferred_primary_category="DAILY", effective_from=date(2026, 1, 1)))
    db_session.flush()
    taxi = _event(db_session, "aa", EventType.CONSUMPTION, "滴滴打车", when=date(2026, 7, 3))
    property_fee = _event(db_session, "ac", EventType.CONSUMPTION, "物业费", when=date(2026, 7, 4))
    digital = _event(db_session, "ad", EventType.CONSUMPTION, "Vercel USD subscription", when=date(2026, 7, 5))
    assert (resolver.resolve_event(db_session, taxi).primary_category, resolver.resolve_event(db_session, taxi).secondary_category) == ("TRAVEL", "LOCAL_TRANSPORT")
    assert resolver.resolve_event(db_session, property_fee).secondary_category == "PROPERTY_FEE"
    assert resolver.resolve_event(db_session, digital).primary_category == "DAILY"


def test_ae_matched_refund_reads_original_classification_and_unmatched_is_not_applicable(db_session):
    resolver = ClassificationResolver()
    original = _event(db_session, "origin", EventType.CONSUMPTION, "航空机票")
    refund = _event(db_session, "refund", EventType.REFUND, "退款", original_event_id=original.id)
    unmatched = _event(db_session, "unmatched", EventType.REFUND, "退款")
    source = resolver.resolve_event(db_session, original)
    assert resolver.get_effective_classification(db_session, refund).id == source.id
    result = resolver.resolve_event(db_session, unmatched)
    assert (result.eligibility_status, result.classification_status) == ("INELIGIBLE", "NOT_APPLICABLE")


def test_generic_local_rule_runner_is_idempotent_and_replays_without_user_specific_logic(db_session):
    event = _event(db_session, "local-rule", EventType.OTHER, "synthetic recurring transfer", account_id="debit", amount="6500")
    first, created, matched = apply_local_rule(
        db_session, account_id="debit", match_text="recurring transfer", amount=Decimal("6500"),
        amount_tolerance=Decimal("0"), effective_from=date(2026, 1, 1), effective_to=None,
        eligibility_action=EligibilityStatus.ELIGIBLE, primary_category=PrimaryCategory.HOUSING,
        secondary_category="RENT",
    )
    second, created_again, matched_again = apply_local_rule(
        db_session, account_id="debit", match_text="recurring transfer", amount=Decimal("6500"),
        amount_tolerance=Decimal("0"), effective_from=date(2026, 1, 1), effective_to=None,
        eligibility_action=EligibilityStatus.ELIGIBLE, primary_category=PrimaryCategory.HOUSING,
        secondary_category="RENT",
    )
    active = db_session.query(ConsumptionInterpretation).filter_by(event_id=event.id, is_active=True).one()
    assert (created, created_again, first.id == second.id, matched, matched_again) == (True, False, True, 1, 1)
    assert (active.eligibility_status, active.primary_category, active.secondary_category) == ("ELIGIBLE", "HOUSING", "RENT")
    assert len(event.projection_revisions) == 1
    assert event.projection_revisions[0].base_net_amount == Decimal("6500")


def test_user_rule_can_match_preserved_source_transaction_type(db_session):
    event = _event(
        db_session, "source-type", EventType.OTHER, "用途信息不足", account_id="debit",
        parser_provenance=json.dumps({"source_transaction_type": "转账汇款"}),
    )
    _rule, _created, matched = apply_local_rule(
        db_session, account_id=None, match_text="转账汇款", amount=None,
        amount_tolerance=Decimal("0"), effective_from=None, effective_to=None,
        eligibility_action=EligibilityStatus.INELIGIBLE, primary_category=None, secondary_category=None,
    )
    active = db_session.query(ConsumptionInterpretation).filter_by(event_id=event.id, is_active=True).one()
    assert matched == 1
    assert (active.eligibility_status, active.classification_status, active.classification_source) == (
        "INELIGIBLE", "NOT_APPLICABLE", "USER_RULE",
    )

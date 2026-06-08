"""
ExecutionPlan / ExecutionTranche 数据模型读写验证。

运行: python -m pytest backend/services/execution_plan/tests/test_models.py -v
"""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from backend.services.execution_plan.models import ExecutionPlan, ExecutionTranche


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class TestExecutionPlanCRUD:

    def test_create_plan(self, db_session):
        plan = ExecutionPlan(
            symbol="LI:US",
            market="US",
            side="BUY",
            target_basis="QUANTITY",
            target_value=100,
        )
        db_session.add(plan)
        db_session.flush()

        assert plan.id is not None
        assert len(plan.id) == 36
        assert plan.plan_status == "draft"
        assert plan.plan_version == 1

    def test_plan_json_fields(self, db_session):
        anchors = [120.0, 115.0, 110.0]
        event_lock = {"enabled": True, "reason": "earnings", "scope": "all_remaining"}
        factor = {
            "atr14": 5.2, "volatility_annual": 0.45,
            "data_source_meta": {"price_source": "futu", "kline_source": "tiger"},
        }
        constraints = {"max_position_pct": 0.40, "max_single_add_pct": 0.10}

        plan = ExecutionPlan(
            symbol="0700:HK",
            market="HK",
            side="ADD",
            user_anchor_prices=json.dumps(anchors),
            manual_event_lock=json.dumps(event_lock),
            factor_snapshot=json.dumps(factor),
            constraints_applied=json.dumps(constraints),
            one_shot_baseline_price=130.5,
            rationale="Test rationale",
            risk_notes="Test risk",
        )
        db_session.add(plan)
        db_session.flush()

        loaded = db_session.query(ExecutionPlan).get(plan.id)
        assert json.loads(loaded.user_anchor_prices) == anchors
        assert json.loads(loaded.manual_event_lock)["enabled"] is True
        assert json.loads(loaded.factor_snapshot)["atr14"] == 5.2
        assert json.loads(loaded.constraints_applied)["max_position_pct"] == 0.40
        assert float(loaded.one_shot_baseline_price) == 130.5

    def test_plan_status_update(self, db_session):
        plan = ExecutionPlan(symbol="AAPL:US", market="US", side="BUY")
        db_session.add(plan)
        db_session.flush()

        plan.plan_status = "active"
        plan.plan_version = 2
        db_session.flush()

        loaded = db_session.query(ExecutionPlan).get(plan.id)
        assert loaded.plan_status == "active"
        assert loaded.plan_version == 2


class TestExecutionTrancheCRUD:

    def test_create_tranche(self, db_session):
        plan = ExecutionPlan(symbol="LI:US", market="US", side="BUY")
        db_session.add(plan)
        db_session.flush()

        tranche = ExecutionTranche(
            plan_id=plan.id,
            sequence=1,
            quantity=50,
            trigger_type="IMMEDIATE",
            order_type="LIMIT",
            limit_price=25.0,
        )
        db_session.add(tranche)
        db_session.flush()

        assert tranche.id is not None
        assert tranche.status == "pending"
        assert float(tranche.quantity) == 50

    def test_multiple_tranches(self, db_session):
        plan = ExecutionPlan(symbol="0700:HK", market="HK", side="ADD")
        db_session.add(plan)
        db_session.flush()

        for i, (price, qty) in enumerate([(320.0, 100), (310.0, 100), (300.0, 100)], 1):
            db_session.add(ExecutionTranche(
                plan_id=plan.id,
                sequence=i,
                quantity=qty,
                trigger_type="PRICE_BELOW",
                trigger_price=price,
                limit_price=price - 0.2,
                order_type="LIMIT",
                min_interval_days=1,
            ))
        db_session.flush()

        tranches = (
            db_session.query(ExecutionTranche)
            .filter_by(plan_id=plan.id)
            .order_by(ExecutionTranche.sequence)
            .all()
        )
        assert len(tranches) == 3
        assert float(tranches[0].trigger_price) == 320.0
        assert float(tranches[2].trigger_price) == 300.0
        assert all(t.status == "pending" for t in tranches)

    def test_tranche_status_update(self, db_session):
        plan = ExecutionPlan(symbol="AAPL:US", market="US", side="BUY")
        db_session.add(plan)
        db_session.flush()

        tranche = ExecutionTranche(
            plan_id=plan.id, sequence=1, quantity=10,
            trigger_type="IMMEDIATE", order_type="LIMIT",
        )
        db_session.add(tranche)
        db_session.flush()

        tranche.status = "armed"
        db_session.flush()
        assert db_session.query(ExecutionTranche).get(tranche.id).status == "armed"

        tranche.status = "filled"
        db_session.flush()
        assert db_session.query(ExecutionTranche).get(tranche.id).status == "filled"

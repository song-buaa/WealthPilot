"""
执行计划 API 路由 — v3.11 M7 最小版。

端点:
- POST /api/execution-plan/generate — 生成执行计划草案(只读预览,不下单)
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter()
logger = logging.getLogger(__name__)


class GenerateRequest(BaseModel):
    symbol: str                           # TICKER:MARKET
    market: str                           # US / HK
    side: str                             # BUY / ADD / REDUCE / SELL
    target_position_pct: float            # 0~1
    current_position_pct: float = 0.0
    current_price: float = 0.0
    total_assets: float = 0.0
    user_anchor_prices: Optional[list[float]] = None
    quick_mode: bool = False
    source_decision_ref: str = ""


@router.post("/generate")
def generate_execution_plan(req: GenerateRequest):
    """生成执行计划草案 — 只读预览,不下单。

    内部调 invoke_skill("wp-generate-execution-plan"),
    走确定性 orchestrator: factors → rule_engine → LLM(仅解释) → validator。
    """
    from backend.skills import invoke_skill

    try:
        result = invoke_skill(
            "wp-generate-execution-plan",
            symbol=req.symbol,
            market=req.market,
            side=req.side,
            target_position_pct=req.target_position_pct,
            current_position_pct=req.current_position_pct,
            current_price=req.current_price,
            total_assets=req.total_assets,
            user_anchor_prices=req.user_anchor_prices or [],
            quick_mode=req.quick_mode,
            source_decision_ref=req.source_decision_ref,
        )
    except Exception as e:
        logger.error("执行计划生成失败: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    # 数据不足拒绝(不是错误,是正常的诚实拒绝)
    if isinstance(result, dict) and result.get("insufficient_data"):
        return result  # 200 + insufficient_data=true,前端判断展示

    # validator 拦截
    if isinstance(result, dict) and result.get("error") == "plan_value_mismatch":
        raise HTTPException(
            status_code=422,
            detail={
                "error": "plan_value_mismatch",
                "failures": result.get("validation_failures", []),
            },
        )

    return result


@router.post("/persist-draft")
def persist_draft(req: GenerateRequest):
    """生成执行计划 + 持久化为 draft 到 DB，返回 plan_id。"""
    from backend.skills import invoke_skill
    from app.database import get_session
    from backend.services.execution_plan.models import ExecutionPlan, ExecutionTranche
    import json

    result = invoke_skill(
        "wp-generate-execution-plan",
        symbol=req.symbol, market=req.market, side=req.side,
        target_position_pct=req.target_position_pct,
        current_position_pct=req.current_position_pct,
        current_price=req.current_price,
        total_assets=req.total_assets,
        user_anchor_prices=req.user_anchor_prices or [],
        quick_mode=req.quick_mode,
        source_decision_ref=req.source_decision_ref,
    )
    if isinstance(result, dict) and (result.get("insufficient_data") or result.get("error")):
        raise HTTPException(status_code=422, detail=result)

    psb = result["plan_summary_block"]
    session = get_session()
    try:
        plan = ExecutionPlan(
            symbol=req.symbol, market=req.market, side=req.side,
            target_basis="QUANTITY",
            target_value=psb.get("total_quantity"),
            user_anchor_prices=json.dumps(req.user_anchor_prices) if req.user_anchor_prices else None,
            one_shot_baseline_price=psb.get("current_price"),
            factor_snapshot=json.dumps(result.get("factor_snapshot"), default=str),
            constraints_applied=json.dumps(result.get("constraints_applied"), default=str),
            rationale=result.get("rationale", ""),
            risk_notes=result.get("risk_notes", ""),
            source_decision_ref=req.source_decision_ref,
        )
        session.add(plan)
        session.flush()

        for t in psb.get("tranches", []):
            session.add(ExecutionTranche(
                plan_id=plan.id,
                sequence=t["sequence"],
                quantity=t["quantity"],
                trigger_type=t.get("trigger_type", "IMMEDIATE"),
                trigger_price=t.get("trigger_price"),
                limit_price=t.get("limit_price"),
                order_type=t.get("order_type", "LIMIT"),
                min_interval_days=1,
            ))

        session.commit()
        return {"plan_id": plan.id, **result}
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.post("/{plan_id}/confirm")
def confirm_plan(plan_id: str):
    """确认执行计划 → 拆成 N 条 SymbolStrategy 进投资行动模块。"""
    from app.database import get_session
    from backend.services.execution_plan.confirm_service import confirm_execution_plan

    session = get_session()
    try:
        result = confirm_execution_plan(session, plan_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        session.rollback()
        logger.error("执行计划确认失败: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()

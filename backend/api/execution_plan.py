"""
执行计划 API 路由 — v3.11。

端点:
- POST /api/execution-plan/generate — 生成执行计划草案
- POST /api/execution-plan/persist-draft — 生成+持久化
- POST /api/execution-plan/{plan_id}/confirm — 确认→拆 SymbolStrategy
- POST /api/execution-plan/adjust — Step C 对话式调整
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


class AdjustRequest(BaseModel):
    user_text: str
    symbol: str
    market: str
    side: str
    target_position_pct: float
    current_position_pct: float = 0.0
    current_price: float = 0.0
    total_assets: float = 0.0
    user_anchor_prices: Optional[list[float]] = None
    quick_mode: bool = False
    source_decision_ref: str = ""


@router.post("/adjust")
def adjust_plan(req: AdjustRequest):
    """Step C: 对话式调整 — 解析用户意图 → 合并参数 → 规则引擎重算。"""
    from backend.services.execution_plan.adjustment_parser import parse_adjustment

    parsed = parse_adjustment(req.user_text)

    if "ambiguous" in parsed:
        return {"status": "ambiguous", "message": parsed["ambiguous"]}
    if "out_of_scope" in parsed:
        return {"status": "out_of_scope", "message": parsed.get("message", "超出可调整范围")}
    if "error" in parsed:
        return {"status": "error", "message": parsed["error"]}

    params = parsed.get("params", {})
    if not params:
        return {"status": "ambiguous", "message": "没有识别到可调整的参数。"}

    merged = {
        "symbol": req.symbol, "market": req.market, "side": req.side,
        "target_position_pct": params.get("target_position_pct", req.target_position_pct),
        "current_position_pct": req.current_position_pct,
        "current_price": req.current_price, "total_assets": req.total_assets,
        "quick_mode": req.quick_mode, "source_decision_ref": req.source_decision_ref,
    }

    if "batch_count" in params:
        merged["batch_count_override"] = params["batch_count"]

    if "user_anchor_prices" in params:
        merged["user_anchor_prices"] = params["user_anchor_prices"]
    elif req.user_anchor_prices:
        merged["user_anchor_prices"] = req.user_anchor_prices

    if "first_batch_immediate" in params:
        if params["first_batch_immediate"]:
            merged["quick_mode"] = True

    from backend.skills import invoke_skill
    try:
        result = invoke_skill("wp-generate-execution-plan", **merged)
    except Exception as e:
        logger.error("调整重算失败: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    if isinstance(result, dict) and result.get("insufficient_data"):
        return {"status": "insufficient_data", **result}
    if isinstance(result, dict) and result.get("error") == "plan_value_mismatch":
        raise HTTPException(status_code=422, detail=result)

    parts = []
    if "batch_count" in params: parts.append(f"分{params['batch_count']}批")
    if "user_anchor_prices" in params: parts.append(f"按价位 {','.join(str(p) for p in params['user_anchor_prices'])}")
    if "target_position_pct" in params: parts.append(f"目标仓位改为{params['target_position_pct']*100:.0f}%")
    if "first_batch_immediate" in params: parts.append("首批立即执行" if params["first_batch_immediate"] else "首批等回调")

    return {
        "status": "adjusted",
        "adjustment_applied": params,
        "adjustment_description": "、".join(parts) or "参数调整",
        **result,
    }

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
    held_shares: int = 0                  # 该标的聚合持仓股数
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
            held_shares=req.held_shares,
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
    from backend.core.demo_mode import PUBLIC_DEMO_MODE
    import json

    result = invoke_skill(
        "wp-generate-execution-plan",
        symbol=req.symbol, market=req.market, side=req.side,
        target_position_pct=req.target_position_pct,
        current_position_pct=req.current_position_pct,
        current_price=req.current_price,
        total_assets=req.total_assets,
        held_shares=req.held_shares,
        user_anchor_prices=req.user_anchor_prices or [],
        quick_mode=req.quick_mode,
        source_decision_ref=req.source_decision_ref,
    )
    if isinstance(result, dict) and (result.get("insufficient_data") or result.get("error")):
        raise HTTPException(status_code=422, detail=result)

    psb = result["plan_summary_block"]
    session = get_session()
    try:
        # 演示模式下标记 source，便于区分和清理
        source_ref = req.source_decision_ref
        if PUBLIC_DEMO_MODE:
            source_ref = f"demo:{source_ref}" if source_ref else "demo"

        # 把关键输入补进 factor_snapshot，供 update-tranches 校验用
        fs = result.get("factor_snapshot") or {}
        fs["total_assets"] = req.total_assets
        fs["current_position_pct"] = req.current_position_pct
        fs["held_shares"] = req.held_shares

        plan = ExecutionPlan(
            symbol=req.symbol, market=req.market, side=req.side,
            target_basis="QUANTITY",
            target_value=psb.get("total_quantity"),
            user_anchor_prices=json.dumps(req.user_anchor_prices) if req.user_anchor_prices else None,
            one_shot_baseline_price=psb.get("current_price"),
            factor_snapshot=json.dumps(fs, default=str),
            constraints_applied=json.dumps(result.get("constraints_applied"), default=str),
            rationale=result.get("rationale", ""),
            risk_notes=result.get("risk_notes", ""),
            source_decision_ref=source_ref,
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


class TrancheUpdate(BaseModel):
    sequence: int
    quantity: int


class UpdateTranchesRequest(BaseModel):
    tranches: list[TrancheUpdate]


@router.patch("/{plan_id}/update-tranches")
def update_tranches(plan_id: str, req: UpdateTranchesRequest):
    """用户手改批次数量 → 纪律校验 → 更新 DB + 重算目标仓位。"""
    from app.database import get_session
    from backend.services.execution_plan.models import ExecutionPlan, ExecutionTranche
    from app.discipline.config import get_rules
    import json

    session = get_session()
    try:
        plan = session.query(ExecutionPlan).filter_by(id=plan_id).first()
        if not plan:
            raise HTTPException(status_code=404, detail="计划不存在")
        if plan.plan_status != "draft":
            raise HTTPException(status_code=400, detail=f"只能修改 draft 状态的计划，当前: {plan.plan_status}")

        db_tranches = (
            session.query(ExecutionTranche)
            .filter_by(plan_id=plan_id)
            .order_by(ExecutionTranche.sequence)
            .all()
        )
        if len(req.tranches) != len(db_tranches):
            raise HTTPException(status_code=400, detail=f"批数不匹配: 提交{len(req.tranches)}批，计划有{len(db_tranches)}批")

        # ── 读取纪律配置 ──
        rules = get_rules()
        sal = rules.get("single_asset_limits", {})
        ps = rules.get("position_sizing", {})
        max_position_pct = sal.get("max_position_pct", 0.40)
        max_single_add_pct = ps.get("max_single_add_pct", 0.10)

        # ── 读取计划基准价和总资产 ──
        baseline_price = float(plan.one_shot_baseline_price) if plan.one_shot_baseline_price else 0
        factor_data = json.loads(plan.factor_snapshot) if plan.factor_snapshot else {}
        total_assets = factor_data.get("total_assets", 1000000)

        is_buy_side = plan.side in ("BUY", "ADD")
        violations = []

        # ── 校验 1: 每批 ≥ 1 ──
        for t in req.tranches:
            if t.quantity < 1:
                violations.append(f"第{t.sequence}批数量为{t.quantity}，最少1股")

        # ── 校验 2: 单批上限 ──
        if baseline_price > 0 and total_assets > 0:
            max_single_shares = int(total_assets * max_single_add_pct / baseline_price)
            for t in req.tranches:
                if t.quantity > max_single_shares:
                    violations.append(
                        f"第{t.sequence}批 {t.quantity} 股超过单批上限 {max_single_shares} 股"
                        f"（单次加仓≤{max_single_add_pct:.0%}，对应 {max_single_shares} 股）"
                    )

        new_total = sum(t.quantity for t in req.tranches)

        # ── 校验 3: 加仓方向——改后仓位 ≤ max_position_pct ──
        if is_buy_side and baseline_price > 0 and total_assets > 0:
            # 当前仓位(从 factor_snapshot 读) + 新增部分
            current_pct = factor_data.get("current_position_pct", 0.0)
            new_increment_pct = (new_total * baseline_price) / total_assets
            final_pct = current_pct + new_increment_pct
            if final_pct > max_position_pct:
                violations.append(
                    f"改后仓位 {final_pct:.1%} 超过单票上限 {max_position_pct:.0%}"
                )

        # ── 校验 4: 减仓方向——卖出总量 ≤ 实际持仓 ──
        if not is_buy_side:
            from app.models import Position
            ticker = plan.symbol.split(":")[0] if ":" in plan.symbol else plan.symbol
            held_qty = sum(
                float(p.quantity or 0)
                for p in session.query(Position).filter(Position.ticker == ticker).all()
            )
            if held_qty > 0 and new_total > held_qty:
                violations.append(f"卖出 {new_total} 股超过持仓 {int(held_qty)} 股")

        if violations:
            raise HTTPException(status_code=422, detail={"violations": violations})

        # ── 全部通过，更新 DB ──
        seq_to_qty = {t.sequence: t.quantity for t in req.tranches}
        for dbt in db_tranches:
            if dbt.sequence in seq_to_qty:
                dbt.quantity = seq_to_qty[dbt.sequence]

        # 重算目标仓位并更新 plan
        plan.target_value = new_total
        if baseline_price > 0 and total_assets > 0:
            current_pct = factor_data.get("current_position_pct", 0.0)
            new_increment_pct = (new_total * baseline_price) / total_assets
            if is_buy_side:
                new_target_pct = current_pct + new_increment_pct
            else:
                new_target_pct = max(0, current_pct - new_increment_pct)
        else:
            new_target_pct = None

        session.commit()
        return {
            "plan_id": plan_id,
            "total_quantity": new_total,
            "target_position_pct": round(new_target_pct, 4) if new_target_pct is not None else None,
            "tranches": [{"sequence": t.sequence, "quantity": int(t.quantity)} for t in db_tranches],
        }
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error("更新批次失败: %s", e)
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

# M1.2: LangGraph DecisionState 已定义，见 backend/graph/decision_graph.py
# M1.3: 将迁移 run_chat_stream 到 StateGraph
"""
WealthPilot — FastAPI 入口

启动方式：
    uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
"""

import os as _os
from dotenv import load_dotenv
# 显式指定 .env 路径，避免从 worktree 等非项目根目录启动时找不到
_project_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
load_dotenv(_os.path.join(_project_root, ".env"))

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.state import startup
from backend.api import portfolio, discipline, research, decision, tasks, profile, allocation
from backend.api import conversations as conversations_api
from backend.api import broker_sync as broker_sync_api
from backend.api import action as action_api
from backend.api import knowledge as knowledge_api
from backend.api import philosophy as philosophy_api
from backend.api import execution_plan as execution_plan_api


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 初始化数据库 + 确保默认 portfolio 存在
    startup()

    # v3.4 M3: Broker Adapter 初始化(通过 BROKER_MODE 环境变量切换)
    _broker_mode = _os.getenv("BROKER_MODE", "mock")
    if _broker_mode == "mock":
        from backend.services.action.brokers.mock import get_mock_adapter
        get_mock_adapter()
    else:
        # tiger.paper / tiger.live — 工厂函数按需创建,不需要全局单例
        pass

    # v3.4 M3: 孤儿订单启动扫描 + 轮询 worker
    from backend.services.action.order_poller import OrderPoller, scan_orphan_orders
    from backend.services.action.brokers.factory import get_broker_adapter
    from app.state import get_session

    def _get_adapter():
        # 复用 action.py 的 adapter 单例（避免 IBKR client_id 冲突）
        from backend.api.action import _get_adapter as _action_get_adapter
        return _action_get_adapter()

    orphan_count = scan_orphan_orders(get_session, _get_adapter)
    if orphan_count:
        print(f"[lifespan] 发现并处理 {orphan_count} 笔孤儿订单")

    order_poller = OrderPoller(
        get_session=get_session,
        get_broker_adapter=_get_adapter,
    )
    await order_poller.start()

    # APScheduler: 每天北京时间 22:00 自动同步券商持仓
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    import pytz

    scheduler = AsyncIOScheduler(timezone=pytz.timezone("Asia/Shanghai"))
    scheduler.add_job(
        lambda: [broker_sync_api._run_sync(b, "cron") for b in ["tiger", "futu", "snowball", "guojin"]],
        trigger=CronTrigger(hour=22, minute=0),
        id="daily_broker_sync",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    # M6: 触发评估循环(盘中每 15 分钟)
    from apscheduler.triggers.interval import IntervalTrigger

    def _run_trigger_evaluation():
        from app.database import get_session as _gs
        from backend.services.execution_plan.trigger_evaluator import evaluate_triggers
        session = _gs()
        try:
            result = evaluate_triggers(session)
            if result["armed"] > 0 or result["skipped_interval"] > 0:
                print(f"[trigger] 评估完成: {result}", flush=True)
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"[trigger] 评估异常: {e}", flush=True)
        finally:
            session.close()

    scheduler.add_job(
        _run_trigger_evaluation,
        trigger=IntervalTrigger(minutes=15),
        id="trigger_evaluation",
        replace_existing=True,
        misfire_grace_time=900,
    )

    scheduler.start()
    print("[scheduler] 定时同步(22:00) + 触发评估(每15分钟) 已启动")

    yield

    # shutdown
    await order_poller.stop()

    scheduler.shutdown()
    print("[scheduler] 定时同步已停止")

    if _broker_mode == "mock":
        from backend.services.action.brokers.mock import shutdown_mock_adapter
        shutdown_mock_adapter()


app = FastAPI(
    title="WealthPilot API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS：允许本地前端开发服务器访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由注册
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["portfolio"])
app.include_router(discipline.router, prefix="/api/discipline", tags=["discipline"])
app.include_router(research.router,   prefix="/api/research",   tags=["research"])
app.include_router(decision.router,   prefix="/api/decision",   tags=["decision"])
app.include_router(tasks.router,      prefix="/api/tasks",      tags=["tasks"])
app.include_router(profile.router,    prefix="/api/profile",    tags=["profile"])
app.include_router(allocation.router, prefix="/api/allocation", tags=["allocation"])
app.include_router(conversations_api.router, prefix="/api/conversations", tags=["conversations"])
app.include_router(broker_sync_api.router, prefix="/api/broker-sync", tags=["broker-sync"])
app.include_router(action_api.router, prefix="/api/action", tags=["action"])
app.include_router(knowledge_api.router, prefix="/api/knowledge", tags=["knowledge"])
app.include_router(philosophy_api.router, prefix="/api/philosophy", tags=["philosophy"])
app.include_router(execution_plan_api.router, prefix="/api/execution-plan", tags=["execution-plan"])


@app.get("/api/health")
def health():
    return {"status": "ok"}

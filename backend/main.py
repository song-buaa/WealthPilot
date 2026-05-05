# M1.2: LangGraph DecisionState 已定义，见 backend/graph/decision_graph.py
# M1.3: 将迁移 run_chat_stream 到 StateGraph
"""
WealthPilot — FastAPI 入口

启动方式：
    uvicorn backend.main:app --reload --port 8000
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
from backend.api import broker_sync as broker_sync_api


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 初始化数据库 + 确保默认 portfolio 存在
    startup()

    # APScheduler: 每天北京时间 22:00 自动同步券商持仓
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    import pytz

    scheduler = AsyncIOScheduler(timezone=pytz.timezone("Asia/Shanghai"))
    scheduler.add_job(
        lambda: [broker_sync_api._run_sync(b, "cron") for b in ["tiger", "futu"]],
        trigger=CronTrigger(hour=22, minute=0),
        id="daily_broker_sync",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.start()
    print("[scheduler] 定时同步已启动,每天北京时间 22:00 执行")

    yield

    scheduler.shutdown()
    print("[scheduler] 定时同步已停止")


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
app.include_router(broker_sync_api.router, prefix="/api/broker-sync", tags=["broker-sync"])


@app.get("/api/health")
def health():
    return {"status": "ok"}

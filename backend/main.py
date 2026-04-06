"""
WealthPilot — FastAPI 入口

启动方式：
    uvicorn backend.main:app --reload --port 8000
"""

from dotenv import load_dotenv
load_dotenv()

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.state import startup
from backend.api import portfolio, discipline, research, decision, tasks, profile, allocation


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 初始化数据库 + 确保默认 portfolio 存在
    startup()
    yield


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


@app.get("/api/health")
def health():
    return {"status": "ok"}

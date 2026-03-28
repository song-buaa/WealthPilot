"""
共享 LLM 客户端工厂（OpenAI）

由意图体系各模块共享，避免重复创建连接。
OpenAI 客户端自动读取 HTTPS_PROXY / ALL_PROXY 环境变量，无需手动配置代理。
"""
from __future__ import annotations

import os
from typing import Optional

import openai

_client: Optional[openai.OpenAI] = None

# PRD §3.1 / §3.4 指定模型（已替换为 OpenAI 对应档位）
MODEL_MAIN = "gpt-4.1"        # 主力推理：意图识别、Subtask 分析、输出渲染
MODEL_MINI = "gpt-4.1-mini"   # 轻量任务：澄清问题生成等


def get_client() -> openai.OpenAI:
    """返回全局共享的 OpenAI 客户端（懒加载）。"""
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "未找到 OPENAI_API_KEY 环境变量。\n"
                "请执行：export OPENAI_API_KEY='sk-...'"
            )
        _client = openai.OpenAI(api_key=api_key)
    return _client


def reset_client() -> None:
    """重置客户端缓存（API 调用失败后可调用，下次重新创建）。"""
    global _client
    _client = None

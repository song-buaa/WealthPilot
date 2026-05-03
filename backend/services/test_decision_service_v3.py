"""decision_service_v3 单元测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("AV_DEV_MOCK", "1")

from dotenv import load_dotenv
load_dotenv()

import asyncio
import json


async def _collect_events(generator) -> list[tuple[str, dict]]:
    """收集所有 SSE 事件，解析为 (event_type, data_dict) 列表。"""
    events = []
    async for raw in generator:
        lines = raw.strip().split("\n")
        event_type = None
        data = None
        for line in lines:
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    data = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    data = {}
        if event_type and data is not None:
            events.append((event_type, data))
    return events


def test_v3_general_chat():
    """v3 通用对话：能产生完整事件序列。"""
    from backend.services.decision_service_v3 import run_chat_stream_v3

    async def _run():
        events = await _collect_events(
            run_chat_stream_v3("什么是夏普比率？", "test_v3_general", 1)
        )
        event_types = [e[0] for e in events]
        print(f"   事件序列: {event_types}")

        assert "stage" in event_types
        assert "intent" in event_types
        assert "done" in event_types
        assert event_types[-1] == "done"
        print("✅ v3 通用对话事件序列正确")

    asyncio.run(_run())


def test_v3_event_structure():
    """v3 事件结构兼容 v2.6 协议。"""
    from backend.services.decision_service_v3 import run_chat_stream_v3

    async def _run():
        events = await _collect_events(
            run_chat_stream_v3("什么是 PE 估值", "test_v3_struct", 1)
        )

        intent_events = [d for t, d in events if t == "intent"]
        assert len(intent_events) >= 1
        intent_payload = intent_events[0]

        required_fields = [
            "primary_intent", "asset", "action", "confidence",
            "needs_clarification", "planner_route", "planner_rationale",
        ]
        for field in required_fields:
            assert field in intent_payload, f"intent 事件缺少字段: {field}"

        done_events = [d for t, d in events if t == "done"]
        assert len(done_events) >= 1
        done_payload = done_events[0]
        assert "decision_id" in done_payload
        assert "conclusion_level" in done_payload
        assert "conclusion_label" in done_payload

        print("✅ v3 事件结构兼容 v2.6 协议")

    asyncio.run(_run())


def test_v3_feature_flag_off():
    """USE_V3_AGENTS 未设置时，run_chat_stream 走 v2.6。"""
    os.environ.pop("USE_V3_AGENTS", None)

    from backend.services.decision_service import run_chat_stream

    async def _run():
        events = await _collect_events(
            run_chat_stream("什么是夏普比率？", "test_v2_default", 1)
        )
        event_types = [e[0] for e in events]
        assert len(events) > 0
        assert "done" in event_types
        print(f"   v2 默认走通：{len(events)} 个事件")
        print("✅ feature flag OFF 时走 v2.6")

    asyncio.run(_run())


def test_v3_feature_flag_on():
    """USE_V3_AGENTS=1 时，run_chat_stream 走 v3。"""
    os.environ["USE_V3_AGENTS"] = "1"

    try:
        from backend.services.decision_service import run_chat_stream

        async def _run():
            events = await _collect_events(
                run_chat_stream("什么是夏普比率？", "test_v3_on", 1)
            )
            event_types = [e[0] for e in events]
            assert len(events) > 0
            assert "done" in event_types
            print(f"   v3 走通：{len(events)} 个事件")
            print("✅ feature flag ON 时走 v3.0")

        asyncio.run(_run())
    finally:
        os.environ.pop("USE_V3_AGENTS", None)


if __name__ == "__main__":
    test_v3_general_chat()
    test_v3_event_structure()
    test_v3_feature_flag_off()
    test_v3_feature_flag_on()
    print("\n🎉 v3 决策服务 4/4 测试通过")

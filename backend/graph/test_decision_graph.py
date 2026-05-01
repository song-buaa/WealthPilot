"""
M1.3 Step 1 — decision_graph unit tests (mock intent_recognizer)
"""

import sys, os, unittest, importlib
from unittest.mock import MagicMock
from dataclasses import dataclass, field
from typing import Optional, List

# 确保项目根目录在 sys.path
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _project_root)

# ── 在 import decision_graph 之前，先 mock 掉重依赖 ────────────────────────
# intent_engine 内部会 import openai 等，需要整棵树 mock 掉
_mock_intent_engine = MagicMock()
sys.modules["intent_engine"] = _mock_intent_engine
sys.modules["intent_engine.intent_recognizer"] = _mock_intent_engine.intent_recognizer

# mock decision_service 的两个工具函数
_mock_ds = MagicMock()
_mock_ds._detect_feature_type = MagicMock(return_value="buy")
_mock_ds._is_asset_clear = MagicMock(return_value=False)
sys.modules.setdefault("backend", MagicMock())
sys.modules.setdefault("backend.services", MagicMock())
sys.modules["backend.services.decision_service"] = _mock_ds

# 直接 import 本目录的 decision_graph 模块（绕过 backend.graph 包路径问题）
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "decision_graph",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "decision_graph.py"),
)
_dg_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dg_mod)

build_decision_graph = _dg_mod.build_decision_graph


# ── 模拟 IntentPayload / IntentEntities ──────────────────────────────────

@dataclass
class FakeEntities:
    asset: Optional[str] = None
    multi_assets: List[str] = field(default_factory=list)
    time_horizon: Optional[str] = None

@dataclass
class FakePayload:
    primary_intent: str = "GeneralChat"
    secondary_intents: List[str] = field(default_factory=list)
    subtasks: List[str] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    entities: FakeEntities = field(default_factory=FakeEntities)
    confidence: float = 0.9


def _make_payload(intent, confidence=0.9, asset=None, multi_assets=None, actions=None):
    return FakePayload(
        primary_intent=intent,
        confidence=confidence,
        actions=actions or [],
        entities=FakeEntities(asset=asset, multi_assets=multi_assets or []),
    )


# ── 测试用例 ─────────────────────────────────────────────────────────────

_mock_recognize = _mock_intent_engine.intent_recognizer.recognize


class TestDecisionGraph(unittest.TestCase):

    def setUp(self):
        self.graph = build_decision_graph()

    def test_position_single_route(self):
        _mock_recognize.return_value = (
            _make_payload("PositionDecision", asset="理想汽车", actions=["buy"]),
            None,
        )
        result = self.graph.invoke({
            "user_query": "理想汽车能买吗",
            "session_id": "test-001",
            "conversation_history": [],
        })
        self.assertEqual(result["route"], "position_single")
        self.assertEqual(result["sse_handler"], "_stream_position_decision")
        print("  position_single_route  PASS")

    def test_portfolio_route(self):
        _mock_recognize.return_value = (
            _make_payload("PortfolioReview"),
            None,
        )
        result = self.graph.invoke({
            "user_query": "帮我看看持仓",
            "session_id": "test-002",
            "conversation_history": [],
        })
        self.assertEqual(result["route"], "portfolio")
        self.assertEqual(result["sse_handler"], "_stream_portfolio_intent")
        print("  portfolio_route  PASS")

    def test_clarify_route(self):
        _mock_recognize.return_value = (
            _make_payload("PositionDecision", asset="股票", actions=["buy"]),
            None,
        )
        result = self.graph.invoke({
            "user_query": "我想买一只股票",
            "session_id": "test-003",
            "conversation_history": [],
        })
        self.assertEqual(result["route"], "clarify")
        self.assertEqual(result["sse_handler"], "_build_clarification_reply")
        print("  clarify_route  PASS")

    def test_low_confidence_route(self):
        _mock_recognize.return_value = (
            _make_payload("PositionDecision", confidence=0.3),
            "您能再说得具体一些吗？",
        )
        result = self.graph.invoke({
            "user_query": "嗯",
            "session_id": "test-004",
            "conversation_history": [],
        })
        self.assertEqual(result["route"], "low_confidence")
        self.assertEqual(result["sse_handler"], "low_confidence")
        print("  low_confidence_route  PASS")


if __name__ == "__main__":
    print("\n=== M1.3 Step 1: DecisionGraph Unit Tests ===\n")
    unittest.main(verbosity=0)

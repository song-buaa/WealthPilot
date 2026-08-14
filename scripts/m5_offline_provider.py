"""Deterministic external-provider fixtures for the M5 offline gate.

This module replaces only unstable boundaries (LLM, search, knowledge retrieval,
and clock input).  The FastAPI/SSE endpoint and the PEER pipeline remain real.
Unknown requests fail closed; there is deliberately no live fallback here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any


class OfflineFixtureError(RuntimeError):
    """The offline provider could not satisfy a request from frozen fixtures."""


@dataclass(frozen=True)
class ProviderCall:
    stage: str
    case_id: str


class OfflineFixtureStore:
    """Validated, query-addressed M5 fixture store."""

    def __init__(self, path: Path):
        if not path.is_file():
            raise OfflineFixtureError(f"offline fixture missing: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OfflineFixtureError(f"offline fixture unreadable: {path}: {exc}") from exc

        if payload.get("schema_version") != "1.0":
            raise OfflineFixtureError("offline fixture schema_version must be 1.0")
        if not payload.get("frozen_timestamp"):
            raise OfflineFixtureError("offline fixture frozen_timestamp is required")
        cases = payload.get("cases")
        if not isinstance(cases, list) or not cases:
            raise OfflineFixtureError("offline fixture cases must be a non-empty list")

        self.path = path
        self.frozen_timestamp = str(payload["frozen_timestamp"])
        self.search_results = list(payload.get("search_results") or [])
        self.principles = list(payload.get("principles") or [])
        self._by_query: dict[str, dict[str, Any]] = {}
        self._by_id: dict[str, dict[str, Any]] = {}
        for case in cases:
            case_id = case.get("case_id")
            query = case.get("user_query")
            if not case_id or not query or not isinstance(case.get("intent"), dict):
                raise OfflineFixtureError("every offline case needs case_id, user_query, and intent")
            if case_id in self._by_id or query in self._by_query:
                raise OfflineFixtureError(f"duplicate offline fixture case/query: {case_id}")
            if not isinstance(case.get("planner"), dict) or not case["planner"].get("route"):
                raise OfflineFixtureError(f"offline fixture planner missing: {case_id}")
            if case["planner"]["route"] != "clarify" and not case.get("answer"):
                raise OfflineFixtureError(f"offline fixture answer missing: {case_id}")
            self._by_id[case_id] = case
            self._by_query[query] = case

    def case_for_query(self, query: str) -> dict[str, Any]:
        try:
            return self._by_query[query]
        except KeyError as exc:
            raise OfflineFixtureError(f"no offline fixture for query: {query!r}") from exc

    def case_ids(self) -> set[str]:
        return set(self._by_id)


class _FakeCompletions:
    def __init__(self, provider: "OfflineOpenAIProvider"):
        self._provider = provider

    def create(self, **kwargs):
        return self._provider.create(**kwargs)


class _FakeChat:
    def __init__(self, provider: "OfflineOpenAIProvider"):
        self.completions = _FakeCompletions(provider)


class OfflineOpenAIClient:
    """Small OpenAI-client-compatible façade backed by frozen fixtures."""

    def __init__(self, provider: "OfflineOpenAIProvider"):
        self.chat = _FakeChat(provider)


class OfflineOpenAIProvider:
    def __init__(self, fixtures: OfflineFixtureStore):
        self.fixtures = fixtures
        self.calls: list[ProviderCall] = []
        self.client = OfflineOpenAIClient(self)

    @staticmethod
    def _response(content: str):
        message = SimpleNamespace(content=content, annotations=[])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    @staticmethod
    def _message_text(messages: list[dict[str, Any]]) -> str:
        return "\n".join(str(item.get("content", "")) for item in messages)

    def _record(self, stage: str, case: dict[str, Any]) -> None:
        self.calls.append(ProviderCall(stage=stage, case_id=case["case_id"]))

    def _case_from_messages(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        # Exact query matching comes first, including queries embedded in JSON payloads.
        combined = self._message_text(messages)
        matches = [case for query, case in self.fixtures._by_query.items() if query in combined]
        if len(matches) != 1:
            raise OfflineFixtureError(
                f"offline LLM request matched {len(matches)} cases; request is not deterministic"
            )
        return matches[0]

    def create(self, **kwargs):
        messages = kwargs.get("messages") or []
        if not isinstance(messages, list) or not messages:
            raise OfflineFixtureError("offline LLM request has no messages")
        combined = self._message_text(messages)
        case = self._case_from_messages(messages)

        if "投资意图识别系统" in combined:
            self._record("intent", case)
            return self._response(json.dumps(case["intent"], ensure_ascii=False))

        if "WealthPilot 决策系统的编排者（Planner）" in combined:
            self._record("planner", case)
            return self._response(json.dumps(case["planner"], ensure_ascii=False))

        if "用户问题包含超出标准工作流的诉求" in combined:
            self._record("skill_selector", case)
            return self._response('{"extra_skills": [], "rationale": "固定外部输入"}')

        if "WealthPilot 的输出审查官" in combined:
            self._record("review_score", case)
            return self._response('{"score": 1.0, "rationale": "冻结评分", "jump_step": null}')

        answer = str(case.get("answer") or "")
        if not answer:
            raise OfflineFixtureError(f"offline answer missing for {case['case_id']}")

        # Generic portfolio engines require JSON; general chat requires plain text.
        if case["planner"]["route"] == "portfolio":
            self._record("expression", case)
            return self._response(json.dumps({
                "chat_answer": answer,
                "key_findings": case.get("key_findings") or ["固定外部推理输入"],
            }, ensure_ascii=False))

        if case["planner"]["route"] == "general":
            self._record("expression", case)
            return self._response(answer)

        raise OfflineFixtureError(
            f"unexpected expression request for route={case['planner']['route']} case={case['case_id']}"
        )


class OfflineKnowledgeStore:
    """Knowledge-store seam that returns auditable fixture chunks."""

    def __init__(self, principles: list[dict[str, Any]]):
        self._principles = principles
        self._config = {"decay": {"enabled": False}}

    def is_ready(self) -> bool:
        return True

    def retrieve(self, *, source_types=None, top_k=5, **_kwargs):
        from backend.knowledge.schemas import RetrievedChunk

        allowed = set(source_types or [])
        rows = [row for row in self._principles if not allowed or row["source_type"] in allowed]
        return [RetrievedChunk(**row) for row in rows[:top_k]]


def install_offline_provider(fixtures: OfflineFixtureStore) -> OfflineOpenAIProvider:
    """Install external seams after app import and return the call recorder."""
    import openai
    from backend.knowledge.store import KnowledgeStore
    from decision_engine import data_loader, llm_engine
    from intent_engine import _llm_client

    provider = OfflineOpenAIProvider(fixtures)
    openai.OpenAI = lambda *args, **kwargs: provider.client  # type: ignore[assignment]
    _llm_client._client = provider.client
    llm_engine._client = provider.client

    knowledge = OfflineKnowledgeStore(fixtures.principles)
    KnowledgeStore.get_instance = classmethod(lambda cls: knowledge)  # type: ignore[method-assign]

    fixed_search = list(fixtures.search_results)
    data_loader.search_portfolio_research = lambda _positions: list(fixed_search)

    original_payload_builder = llm_engine._build_portfolio_payload

    def _frozen_payload_builder(*args, **kwargs):
        payload = original_payload_builder(*args, **kwargs)
        payload["current_date"] = fixtures.frozen_timestamp[:10]
        return payload

    llm_engine._build_portfolio_payload = _frozen_payload_builder
    return provider

"""Shared model policy and API compatibility; never grants investment authority.

Only an explicit legacy profile selects GPT-4.1. Provider failures keep the
existing caller's unavailable/retry behavior, never change model families.
"""
from __future__ import annotations

import logging
import json
import os
import time

import openai
import httpx
from app.analysis_runtime import current as runtime_budget, DeadlineReached
from pydantic import ValidationError
from app.llm_observation import current, _active_call

logger = logging.getLogger(__name__)
SOL = "gpt-6.1-sol"
LUNA = "gpt-6-luna"
LEGACY_PROFILE = "legacy-4.1"


def model_for(role: str) -> str:
    profile = os.getenv("WEALTHPILOT_LLM_PROFILE", "gpt6")
    if profile not in {"gpt6", LEGACY_PROFILE}:
        raise ValueError("LLM_PROFILE_UNSUPPORTED")
    if role not in {"main", "light"}:
        raise ValueError("LLM_ROLE_UNSUPPORTED")
    default = ({"main": "gpt-4.1", "light": "gpt-4.1-mini"} if profile == LEGACY_PROFILE
               else {"main": SOL, "light": LUNA})[role]
    model = os.getenv("WEALTHPILOT_LLM_" + role.upper() + "_MODEL", default).strip()
    if model != default:
        raise ValueError("LLM_MODEL_PROFILE_MISMATCH")
    return model


MODEL_MAIN = model_for("main")
MODEL_LIGHT = model_for("light")


class LLMCallError(RuntimeError):
    """Safe error for caller fallbacks: contains no provider body or prompt."""

    def __init__(self, kind: str):
        self.kind = kind
        super().__init__("LLM_" + kind.upper())


def failure_message(error: LLMCallError) -> str:
    """Preserve the existing user-facing fallback contract, without raw errors."""
    if error.kind == "timeout":
        return "系统繁忙，请稍后再试。"
    if error.kind == "network":
        return "模型服务连接失败，请稍后再试。"
    if error.kind in {"authentication", "model_access"}:
        return "当前专业模型不可用，请核查访问权限。"
    if error.kind in {"quota", "rate_limit"}:
        return "模型服务额度或频率受限，请稍后再试。"
    if error.kind in {"truncated", "empty_output", "refusal", "invalid_output"}:
        return "推理结果暂不可用，请稍后重试。"
    return "API 调用失败，请稍后再试。"


def error_kind(error: Exception) -> str:
    if isinstance(error, LLMCallError):
        return error.kind
    if isinstance(error, openai.LengthFinishReasonError):
        return "truncated"
    if isinstance(error, openai.ContentFilterFinishReasonError):
        return "refusal"
    if isinstance(error, (json.JSONDecodeError, ValidationError, openai.APIResponseValidationError)):
        return "invalid_output"
    if isinstance(error, openai.APITimeoutError):
        return "timeout"
    if isinstance(error, openai.APIConnectionError):
        return "network"
    status = getattr(error, "status_code", None)
    if status == 401:
        return "authentication"
    if status in {403, 404}:
        return "model_access"
    if status == 429:
        return "quota" if getattr(error, "code", None) == "insufficient_quota" else "rate_limit"
    if status == 400:
        return "invalid_request"
    if isinstance(status, int) and status >= 500:
        return "provider"
    return "provider_error"


def request_options(model: str, *, endpoint: str = "chat", **kwargs) -> dict:
    """Adapt the existing caller's output contract, not its business facts."""
    options = dict(kwargs, model=model, store=False)
    if model.startswith("gpt-4.1") and os.getenv("WEALTHPILOT_LLM_PROFILE") != LEGACY_PROFILE:
        raise LLMCallError("legacy_profile_required")
    if model in {SOL, LUNA}:
        main = model == SOL
        effort = options.pop("reasoning_effort", None)
        if effort is None:
            effort = os.getenv("WEALTHPILOT_LLM_MAIN_EFFORT" if main else "WEALTHPILOT_LLM_LIGHT_EFFORT",
                               "medium" if main else "none")
        allowed = {"low", "medium", "high", "xhigh", "max"}
        if not main:
            allowed.add("none")
        if effort not in allowed:
            raise LLMCallError("reasoning_effort_unsupported")
        if endpoint == "chat" and (options.get("tools") or options.get("functions")) and (main or effort != "none"):
            raise LLMCallError("tools_require_responses")
        if effort != "none":
            for name in ("temperature", "top_p", "top_logprobs", "logprobs"):
                options.pop(name, None)
        requested = options.pop("max_tokens", 2000)
        # Completion budgets include hidden reasoning. Preserve visible headroom.
        budget = max(8192 if main else 4096, requested + (4096 if main else 2048)) if effort != "none" else requested
        if endpoint == "chat":
            options.setdefault("max_completion_tokens", budget)
            options["reasoning_effort"] = effort
        else:
            options.setdefault("max_output_tokens", budget)
            options["reasoning"] = {"effort": effort}
        minimum=90.0 if main else 20.0
        supplied=options.get("timeout")
        options["timeout"] = (httpx.Timeout(read=max(supplied.read or minimum,minimum),connect=supplied.connect,write=supplied.write,pool=supplied.pool)
            if isinstance(supplied,httpx.Timeout) else max(float(supplied or 0),minimum))
    else:
        # Explicit rollback / injected test providers keep their old API contract.
        options.pop("reasoning_effort", None)
        if endpoint != "chat" and "max_tokens" in options:
            options["max_output_tokens"] = options.pop("max_tokens")
        options.setdefault("timeout", 60.0)
    budget = runtime_budget()
    if budget is not None:
        remaining = budget.remaining()
        if budget.finished.is_set() or remaining <= 0:
            raise LLMCallError("timeout")
        supplied=options.get("timeout")
        default=20 if model==LUNA else 60
        supplied_read=supplied.read if isinstance(supplied,httpx.Timeout) else supplied
        read = budget.policy.sol_read_seconds if model in {SOL, "gpt-4.1"} else float(supplied_read or default)
        options["timeout"] = httpx.Timeout(read=min(read, remaining),
            connect=min(budget.policy.connect_seconds, remaining),
            pool=min(budget.policy.connect_seconds, remaining),
            write=min(budget.policy.write_seconds, remaining))
    return options


def _client_options(client):
    # The SDK alone owns transport retries (at most one). Test fakes stay intact.
    if type(client).__module__.startswith("openai"):
        return client.with_options(max_retries=1)
    return client


def _invoke(client, endpoint: str, options: dict):
    started = time.monotonic()
    trace = current()
    call = None
    try:
        if trace:call=trace.begin(options, endpoint)
    except Exception:
        trace.metadata_unavailable = True  # Never turn missing telemetry into zero calls.
    call_token = _active_call.set(call)
    raw = result = error = None
    kind = None
    try:
        # The opt-in, dedicated acceptance process may pin billable request
        # modes. Normal application calls retain their existing contract.
        from app.llm_acceptance import acceptance_options
        options = acceptance_options(options)
        configured = _client_options(client)
        if trace and type(configured).__module__.startswith("openai"):
            # Public raw-response API preserves SDK retries_taken, even when
            # schema parsing fails. The SDK still owns its original retry policy.
            raw = (configured.chat.completions.with_raw_response.create(**options) if endpoint == "chat"
                   else configured.responses.with_raw_response.parse(**options))
            try:
                result = raw.parse()
            except TypeError:
                # SDK Responses post-parser can raise TypeError for malformed
                # output bodies. Only this wire parse boundary is reclassified.
                raise LLMCallError("invalid_output") from None
        else:
            result = (configured.chat.completions.create(**options) if endpoint == "chat"
                      else configured.responses.parse(**options))
        returned = getattr(result, "model", None)
        requested = options["model"]
        if isinstance(returned, str) and requested in {SOL, LUNA} and not returned.startswith(requested):
            raise LLMCallError("model_mismatch")
        if endpoint == "chat":
            choices = getattr(result, "choices", [])
            if not choices:
                raise LLMCallError("empty_output")
            choice = choices[0]
            if getattr(choice, "finish_reason", None) == "length":
                raise LLMCallError("truncated")
            message = choice.message
            if isinstance(getattr(message, "refusal", None), str) and message.refusal:
                raise LLMCallError("refusal")
            content = getattr(message, "content", None)
            if content is None or isinstance(content, str) and not content.strip():
                raise LLMCallError("empty_output")
        else:
            if getattr(result, "status", None) == "incomplete":
                raise LLMCallError("truncated")
            if getattr(result, "output_parsed", None) is None:
                refused = any(getattr(block, "type", None) == "refusal"
                              for item in getattr(result, "output", [])
                              for block in getattr(item, "content", []))
                raise LLMCallError("refusal" if refused else "empty_output")
        logger.info("llm_call model=%s endpoint=%s status=completed elapsed_ms=%d",
                    requested, endpoint, int((time.monotonic() - started) * 1000))
        return result
    except DeadlineReached:
        kind = "timeout"
        raise LLMCallError(kind) from None
    except Exception as caught:
        error = caught
        kind = error_kind(caught)
        logger.warning("llm_call model=%s endpoint=%s status=%s elapsed_ms=%d",
                       options["model"], endpoint, kind, int((time.monotonic() - started) * 1000))
        raise LLMCallError(kind) from None
    finally:
        _active_call.reset(call_token)
        if trace:
            # A reference estimate is not an invoice: no tools, image inputs,
            # custom/regional endpoint or unknown service tier is priced here.
            def non_text(value):
                if isinstance(value, dict):
                    return value.get("type") in ("image_url", "input_image", "input_audio") or any(non_text(v) for v in value.values())
                return isinstance(value, list) and any(non_text(v) for v in value)
            try:
                standard = (str(getattr(client, "base_url", "")) == "https://api.openai.com/v1/"
                            and not any(options.get(key) for key in ("tools", "functions", "web_search_options", "audio", "extra_body"))
                            and options.get("modalities") in (None, ["text"])
                            and not non_text(options.get("messages", options.get("input"))))
                trace.complete(call, result=result, error=error, kind=kind, raw=raw,
                    options=options, elapsed_ms=int((time.monotonic()-started)*1000), standard_text=standard)
            except Exception:
                # Observation failure must not replace a professional result or
                # its original error; never log the offending metadata/body.
                trace.invalidate(call)


def _observed_options(endpoint, kwargs):
    try:
        return request_options(endpoint=endpoint, **kwargs)
    except LLMCallError as error:
        trace = current()
        if trace:
            try:
                call = trace.begin(kwargs, endpoint)
                trace.complete(call, kind=error.kind)
                if call is not None and not trace.closed:
                    call.update(status="rejected", sdk_retry_count=0, transport_attempt_count=0)
            except Exception:
                trace.metadata_unavailable = True
        raise


def chat_completion(client, **kwargs):
    return _invoke(client, "chat", _observed_options("chat", kwargs))


def parse_response(client, **kwargs):
    return _invoke(client, "responses", _observed_options("responses", kwargs))

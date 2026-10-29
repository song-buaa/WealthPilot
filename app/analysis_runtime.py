"""Small normal asynchronous-L1 runtime policy; no research/trading authority."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import json
import asyncio
import math
import os
from pathlib import Path
import threading
import time

ENV_KEYS = {'WEALTHPILOT_L1_ANALYSIS_TIMEOUT_SECONDS', 'WEALTHPILOT_L1_SOL_READ_TIMEOUT_SECONDS'}
_current = ContextVar('normal_async_l1_budget', default=None)
_hook_lock = threading.Lock()
_process_budget = None  # only bound in the dedicated --single-analysis child


@dataclass(frozen=True)
class Policy:
    total_seconds: float = 600
    sol_read_seconds: float = 180
    connect_seconds: float = 10
    write_seconds: float = 30
    cleanup_grace_seconds: float = .2

    @property
    def ipc_seconds(self):
        return self.total_seconds + 60

    @property
    def finalization_seconds(self):
        return min(10, self.total_seconds / 10)


def policy(env_file=None):
    values = dict(os.environ)
    if env_file:
        configured = json.loads(Path(env_file).read_text())
        if not isinstance(configured, dict):
            raise ValueError('ANALYSIS_CONFIGURATION_INVALID')
        values.update({k:v for k,v in configured.items() if k in ENV_KEYS})
    def duration(key, default):
        raw = values.get(key, default)
        if isinstance(raw,bool):raise ValueError('ANALYSIS_TIME_POLICY_INVALID')
        value = float(raw)
        if not math.isfinite(value) or value <= 0:
            raise ValueError('ANALYSIS_TIME_POLICY_INVALID')
        return value
    return Policy(total_seconds=duration('WEALTHPILOT_L1_ANALYSIS_TIMEOUT_SECONDS', 600),
                  sol_read_seconds=duration('WEALTHPILOT_L1_SOL_READ_TIMEOUT_SECONDS', 180))


class DeadlineReached(BaseException):
    """Bypasses SDK Exception-based retry loops; converted at the API boundary."""


class Budget:
    def __init__(self, settings, deadline=None):
        self.policy = settings
        self.deadline = min(time.monotonic()+settings.total_seconds, deadline) if deadline is not None else time.monotonic()+settings.total_seconds
        self.finished = threading.Event()
        self.observation = None

    def remaining(self):
        return max(0, self.deadline-time.monotonic()-self.policy.finalization_seconds)

    def before_send(self, request):
        remaining = self.remaining()
        if self.finished.is_set() or (self.observation is not None and self.observation.closed) or remaining <= 0:
            raise DeadlineReached()
        # HTTPX phases are not a total deadline. The enclosing coroutine owns
        # that deadline, and the single-use child is reclaimed after its frame.
        original = request.extensions.get('timeout') or {}
        request.extensions['timeout'] = {key:min(float(original.get(key) or remaining), remaining)
                                        for key in ('connect','read','write','pool')}


def process_budget():
    return _process_budget


def current():
    return _current.get() or _process_budget


def _install_send_guard():
    import httpx
    with _hook_lock:
        if getattr(httpx.Client._send_single_request, '_wp_deadline_guard', False):
            return
        original = httpx.Client._send_single_request
        @wraps(original)
        def send(client, request, *args, **kwargs):
            budget = current()
            if budget is not None:
                budget.before_send(request)
                from app.llm_observation import current as observation, _active_call
                trace=observation()
                if trace:
                    try:trace.transport_started(_active_call.get(),request)
                    except Exception:trace.metadata_unavailable=True
            return original(client, request, *args, **kwargs)
        send._wp_deadline_guard = True
        httpx.Client._send_single_request = send


@contextmanager
def budget_context(settings=None, deadline=None, *, process_scope=False):
    global _process_budget
    existing = current()
    if existing is not None:
        yield existing
        return
    _install_send_guard()
    budget = Budget(settings or policy(), deadline)
    if process_scope:
        _process_budget = budget  # retained CLOSED until this one-use process exits
    token = _current.set(budget)
    try:
        yield budget
    finally:
        budget.finished.set()  # copied to_thread contexts share this terminal flag
        _current.reset(token)


def budgeted_analysis(fn=None, *, on_expired=None):
    if fn is None:
        return lambda target:budgeted_analysis(target,on_expired=on_expired)
    @wraps(fn)
    async def wrapped(*args, **kwargs):
        if not kwargs.get('async_l1', False):
            return await fn(*args, **kwargs)
        with budget_context(deadline=kwargs.get('deadline_monotonic')) as budget:
            from app.llm_observation import current as observation
            budget.observation=observation()
            remaining=budget.deadline-time.monotonic()
            if remaining<=0:raise TimeoutError()
            # Includes setup/recursive frozen-input analysis and finalization at
            # every await boundary; the inner work timeout reserves finalization.
            async with asyncio.timeout(remaining):
                result=await fn(*args, **kwargs)
            if time.monotonic()>budget.deadline:
                if on_expired is None:raise TimeoutError()
                result=on_expired(result)
            return result
    return wrapped

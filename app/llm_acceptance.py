"""Opt-in three-stock acceptance admission, not a production billing service.

Only scripts/v42_peer_acceptance.py installs the process-wide HTTPX guard.
No prompts, response bodies, account identifiers or credentials enter the ledger.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading
from urllib.parse import urlsplit, parse_qs

from app.llm_observation import current

PRICE_VERSION = "acceptance-standard-text-max-cache-2026-10-23-v1"
PRICE_SOURCE = "https://developers.openai.com/api/docs/pricing?latest-pricing-expanded=true"
# USD per million: use the highest documented 12h cache-write input price,
# never a cache discount. Output cap includes the hidden reasoning budget.
PRICES = {"gpt-6.1-sol": (Decimal("5"), Decimal("10")),
          "gpt-6-luna": (Decimal("0.25"), Decimal("0.50"))}
SYMBOLS = ("LI:US", "PDD:US", "3690:HK")
_active = None
_installation = threading.Lock()
_network_permission = ContextVar("acceptance_network_permission", default=False)
_audit_installed = False


def _audit(event, args):
    if _active is None:
        return
    if event in {"os.fork", "os.forkpty", "os.posix_spawn", "os.exec", "subprocess.Popen"}:
        raise AdmissionDenied("CHILD_PROCESS_UNSUPPORTED")
    if event in {"socket.connect", "socket.sendto"}:
        if _active.closed or not _network_permission.get():
            raise AdmissionDenied("NETWORK_PATH_UNSUPPORTED")


@contextmanager
def permitted_network():
    token = _network_permission.set(True)
    try:
        yield
    finally:
        _network_permission.reset(token)


class AdmissionDenied(PermissionError):
    def __init__(self, code):
        super().__init__("ACCEPTANCE_" + code)


def acceptance_options(options):
    if _active is None:
        return options
    if options.get("service_tier", "default") != "default":
        raise AdmissionDenied("BILLING_MODE_UNSUPPORTED")
    return dict(options, service_tier="default")


class Budget:
    """One immutable campaign ledger, flock across processes, lock across threads.

    Reservations are committed and fsynced before transport, never refunded on
    failure. Existing ledgers are resumed, never silently reset by a new process.
    """
    def __init__(self, path, *, max_attempts=24, max_usd="3", existing_only=False):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 24:
            raise AdmissionDenied("LIMIT_INVALID")
        dollars = Decimal(str(max_usd))
        if not dollars.is_finite() or not 0 < dollars <= 3:
            raise AdmissionDenied("LIMIT_INVALID")
        self.path = Path(path)
        self.max_attempts = max_attempts
        self.max_nanos = int((dollars * 10**9).to_integral_value(rounding=ROUND_CEILING))
        self.lock = threading.RLock()
        self.pid = os.getpid()
        self.closed = False
        self.runs = set()
        if existing_only and not self.path.is_file():
            raise AdmissionDenied("LEDGER_REQUIRED")
        import fcntl
        # Serialize creation before exposing a completed ledger to another
        # acceptance process. Retain both files; never reset a damaged ledger.
        init_fd = os.open(str(self.path) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(init_fd, "r+") as init_lock:
            fcntl.flock(init_lock, fcntl.LOCK_EX)
            self._private(os.fstat(init_lock.fileno()))
            try:
                if existing_only:
                    fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
                    os.close(fd)
                    fd = None
                else:
                    fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                pass
            else:
                if fd is not None:
                    with os.fdopen(fd, "w") as stream:
                        json.dump(dict(version=PRICE_VERSION, max_attempts=max_attempts,
                            max_nanos=self.max_nanos, attempts=[], analyses={}, reserved_nanos=0, denials={}), stream)
                        stream.flush(); os.fsync(stream.fileno())
                    directory = os.open(self.path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
        self.snapshot()  # An incomplete/crashed/foreign ledger fails closed.

    @staticmethod
    def _private(info):
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 65536):
            raise AdmissionDenied("LEDGER_INVALID")

    @contextmanager
    def transaction(self, *, expected_sha256=None):
        import fcntl
        with self.lock:
            fd = os.open(self.path, os.O_RDWR | os.O_NOFOLLOW)
            with os.fdopen(fd, "r+", encoding="utf-8", newline="") as stream:
                fcntl.flock(stream, fcntl.LOCK_EX)
                info = os.fstat(stream.fileno())
                self._private(info)
                try:
                    original = stream.read()
                    data = json.loads(original)
                    assert isinstance(data, dict)
                    keys = {"version", "max_attempts", "max_nanos", "attempts", "analyses", "reserved_nanos", "denials"}
                    schema = data.get("ledger_schema", 1)
                    assert type(schema) is int and schema in {1, 2}
                    assert set(data) == keys | ({"ledger_schema", "li_retest"} if schema == 2 else set())
                    assert data["version"] == PRICE_VERSION
                    assert data["max_attempts"] == self.max_attempts and data["max_nanos"] == self.max_nanos
                    assert type(data["reserved_nanos"]) is int and 0 <= data["reserved_nanos"] <= self.max_nanos
                    assert isinstance(data["attempts"], list) and isinstance(data["analyses"], dict) and isinstance(data["denials"], dict)
                    assert len(data["attempts"]) <= self.max_attempts
                    assert sum(a["reserved_nanos"] for a in data["attempts"]) == data["reserved_nanos"]
                    assert set(data["analyses"]) <= set(SYMBOLS)
                    assert all(re.fullmatch(r"[0-9a-f]{32}", v) for v in data["analyses"].values())
                    runs = set(data["analyses"].values())
                    assert len(runs) == len(data["analyses"])
                    if schema == 2:
                        retest = data["li_retest"]
                        assert set(retest) == {"symbol", "authorization_id", "analysis_run_id", "original_run_id", "authorized_against_sha256"}
                        assert retest["symbol"] == "LI:US" and retest["original_run_id"] == data["analyses"]["LI:US"]
                        assert all(re.fullmatch(r"[0-9a-f]{32}", retest[k]) for k in ("authorization_id", "analysis_run_id"))
                        assert re.fullmatch(r"[0-9a-f]{64}", retest["authorized_against_sha256"])
                        assert retest["analysis_run_id"] not in runs
                        runs.add(retest["analysis_run_id"])
                    for a in data["attempts"]:
                        assert set(a) == {"analysis_run_id", "model", "endpoint", "input_token_bound", "output_token_bound", "reserved_nanos"}
                        assert a["analysis_run_id"] in runs and a["model"] in PRICES
                        assert a["endpoint"] in {"chat", "responses"}
                        assert all(type(a[k]) is int and a[k] > 0 for k in ("input_token_bound", "output_token_bound", "reserved_nanos"))
                    assert set(data["denials"]) <= {"RUN_UNASSOCIATED", "ENDPOINT_UNSUPPORTED", "REQUEST_UNBOUNDED",
                        "MODEL_UNPRICED", "BILLING_MODE_UNSUPPORTED", "OUTPUT_UNBOUNDED", "INPUT_UNBOUNDED", "BUDGET_EXHAUSTED"}
                    assert all(type(v) is int and 0 <= v <= 2**31 for v in data["denials"].values())
                except (ValueError, KeyError, TypeError, AssertionError):
                    raise AdmissionDenied("LEDGER_INVALID") from None
                if expected_sha256 is not None and hashlib.sha256(original.encode()).hexdigest() != expected_sha256:
                    raise AdmissionDenied("LEDGER_CHANGED")
                before = json.dumps(data, sort_keys=True)
                yield data
                # Reading/validating old ledgers must not rewrite their bytes.
                if json.dumps(data, sort_keys=True) != before:
                    stream.seek(0); json.dump(data, stream, allow_nan=False)
                    stream.truncate(); stream.flush(); os.fsync(stream.fileno())

    def claim(self, symbol, identifier):
        from app.llm_observation import run_id
        if symbol not in SYMBOLS or run_id(identifier) != identifier or os.getpid() != self.pid:
            raise AdmissionDenied("RUN_UNASSOCIATED")
        with self.transaction() as data:
            if (symbol in data["analyses"] or identifier in data["analyses"].values()
                    or identifier == data.get("li_retest", {}).get("analysis_run_id")):
                raise AdmissionDenied("ANALYSIS_ALREADY_CLAIMED")
            data["analyses"][symbol] = identifier
        self.runs.add(identifier)

    def claim_retest(self, symbol, identifier, *, authorization_id, expected_ledger_sha256):
        """Explicit operator admission for exactly one LI revalidation.

        Both IDs and the reviewed original ledger digest are supplied outside
        model/request options. This does not authorize executing this in Prod.
        The original flock/inode/fsync remains the only budget state owner.
        """
        if (symbol != "LI:US" or os.getpid() != self.pid
                or not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier)
                or not isinstance(authorization_id, str) or not re.fullmatch(r"[0-9a-f]{32}", authorization_id)
                or not isinstance(expected_ledger_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_ledger_sha256)):
            raise AdmissionDenied("RETEST_NOT_AUTHORIZED")
        with self.transaction(expected_sha256=expected_ledger_sha256) as data:
            if "li_retest" in data:
                raise AdmissionDenied("RETEST_ALREADY_CLAIMED")
            if "LI:US" not in data["analyses"] or identifier in data["analyses"].values():
                raise AdmissionDenied("RETEST_NOT_AUTHORIZED")
            # Keep original history durable before extending the in-place
            # ledger. Never replace its inode (old flock users may be waiting).
            # This wrapped artifact is recovery evidence, not another Budget.
            original = self.path.read_bytes().decode("utf-8")
            if hashlib.sha256(original.encode()).hexdigest() != expected_ledger_sha256:
                raise AdmissionDenied("LEDGER_CHANGED")
            try:
                fd = os.open(str(self.path) + ".pre-li-retest", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                raise AdmissionDenied("MIGRATION_RECOVERY_REQUIRED") from None
            with os.fdopen(fd, "w") as backup:
                json.dump(dict(recovery_only=True, original_ledger_utf8=original,
                    sha256=expected_ledger_sha256), backup)
                backup.flush(); os.fsync(backup.fileno())
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            data.update(ledger_schema=2, li_retest=dict(symbol=symbol, authorization_id=authorization_id,
                analysis_run_id=identifier, original_run_id=data["analyses"]["LI:US"],
                authorized_against_sha256=expected_ledger_sha256))
        self.runs.add(identifier)

    def snapshot(self):
        with self.transaction() as data:
            return dict(**({"ledger_schema":2, "li_retest":dict(data["li_retest"])} if "li_retest" in data else {}),
                price_version=PRICE_VERSION, price_source=PRICE_SOURCE,
                attempt_count=len(data["attempts"]), max_attempts=self.max_attempts,
                reserved_usd=str(Decimal(data["reserved_nanos"]) / 10**9),
                max_usd=str(Decimal(self.max_nanos) / 10**9),
                analyses=dict(data["analyses"]), attempts=list(data["attempts"]),
                denials=dict(data["denials"]), actual_bill="UNKNOWN", reservation_refunds=False)

    def before_send(self, request):
        try:
            self._before_send(request)
        except AdmissionDenied as error:
            with self.transaction() as data:
                key = str(error).removeprefix("ACCEPTANCE_")
                data["denials"][key] = data["denials"].get(key, 0) + 1
            raise

    def _before_send(self, request):
        trace = current()
        if self.closed or os.getpid() != self.pid or trace is None or trace.closed or trace.analysis_run_id not in self.runs:
            raise AdmissionDenied("RUN_UNASSOCIATED")
        if (request.method != "POST" or str(request.url.copy_with(query=None)) not in
                {"https://api.openai.com/v1/chat/completions", "https://api.openai.com/v1/responses"}
                or request.url.query):
            raise AdmissionDenied("ENDPOINT_UNSUPPORTED")
        try:
            body = json.loads(request.content)
        except (ValueError, UnicodeError):
            raise AdmissionDenied("REQUEST_UNBOUNDED") from None
        if not isinstance(body, dict) or body.get("model") not in PRICES:
            raise AdmissionDenied("MODEL_UNPRICED")
        chat = request.url.path.endswith("/chat/completions")
        common = {"model", "store", "stream", "service_tier", "temperature", "top_p"}
        allowed = common | ({"messages", "max_completion_tokens", "max_tokens", "reasoning_effort",
            "response_format", "n", "stop", "seed", "presence_penalty", "frequency_penalty"} if chat else
            {"input", "instructions", "max_output_tokens", "reasoning", "text"})
        if (set(body) - allowed or body.get("service_tier") != "default"
                or body.get("stream", False) is not False or body.get("store") is not False
                or type(body.get("n", 1)) is not int or body.get("n", 1) != 1):
            raise AdmissionDenied("BILLING_MODE_UNSUPPORTED")
        cap_key = "max_completion_tokens" if "max_completion_tokens" in body else "max_tokens"
        cap = body.get(cap_key if chat else "max_output_tokens")
        if type(cap) is not int or not 1 <= cap <= 128000:
            raise AdmissionDenied("OUTPUT_UNBOUNDED")
        content = body.get("messages" if chat else "input")
        if not isinstance(content, (str, list)) or not content:
            raise AdmissionDenied("INPUT_UNBOUNDED")
        if isinstance(content, list):
            for message in content:
                if (not isinstance(message, dict) or set(message) - {"role", "content", "name"}
                        or message.get("role") not in {"user", "system", "developer", "assistant"}):
                    raise AdmissionDenied("INPUT_UNBOUNDED")
                value = message.get("content")
                if isinstance(value, list):
                    if any(not isinstance(v, dict) or set(v) - {"type", "text"}
                           or v.get("type") not in {"text", "input_text", "output_text"}
                           or not isinstance(v.get("text"), str) for v in value):
                        raise AdmissionDenied("INPUT_UNBOUNDED")
                elif not isinstance(value, str):
                    raise AdmissionDenied("INPUT_UNBOUNDED")
        # Byte bound includes schema, JSON escaping and instructions. Additional
        # fixed/per-message framing margin; reject long-context billing entirely.
        input_bound = len(request.content) + 4096 + (256 * len(content) if isinstance(content, list) else 0)
        if input_bound > 128000:
            raise AdmissionDenied("INPUT_UNBOUNDED")
        inp, out = PRICES[body["model"]]
        reserve = int(((input_bound * inp + cap * out) * 1000).to_integral_value(rounding=ROUND_CEILING))
        with self.transaction() as data:
            allowed_runs = set(data["analyses"].values())
            if "li_retest" in data:
                allowed_runs.add(data["li_retest"]["analysis_run_id"])
            if trace.analysis_run_id not in allowed_runs:
                raise AdmissionDenied("RUN_UNASSOCIATED")
            if (len(data["attempts"]) >= self.max_attempts
                    or data["reserved_nanos"] + reserve > self.max_nanos):
                raise AdmissionDenied("BUDGET_EXHAUSTED")
            data["reserved_nanos"] += reserve
            data["attempts"].append(dict(analysis_run_id=trace.analysis_run_id, model=body["model"],
                endpoint="chat" if chat else "responses", input_token_bound=input_bound,
                output_token_bound=cap, reserved_nanos=reserve))


@contextmanager
def guarded_transport(budget, *, process_lifetime=False):
    """Dedicated process only: intercept every SDK send/retry/redirect in HTTPX.

    All other HTTPX paths (including embeddings/search/unknown origins) are
    rejected. Optional sources may degrade through their original error paths.
    No child worker can escape a separate counter; this entry runs PEER locally.
    """
    import httpx
    import subprocess
    import urllib.request
    import requests.sessions
    global _active, _audit_installed
    if not _installation.acquire(blocking=False):
        raise AdmissionDenied("GUARD_ALREADY_ACTIVE")
    sync = httpx.Client._send_single_request
    asynchronous = httpx.AsyncClient._send_single_request
    popen, fork = subprocess.Popen, getattr(os, "fork", None)
    urllib_open, requests_send = urllib.request.OpenerDirector.open, requests.sessions.Session.send
    def send(client, request):
        budget.before_send(request)
        with permitted_network():
            return sync(client, request)
    async def async_send(client, request):
        budget.before_send(request)
        with permitted_network():
            return await asynchronous(client, request)
    def no_child(*args, **kwargs):
        raise AdmissionDenied("CHILD_PROCESS_UNSUPPORTED")
    def provider_read(url, method, data):
        # A separate, reviewed free-data route, never a model or general proxy.
        # Other optional sources degrade through their original error path.
        value = urlsplit(url)
        query = parse_qs(value.query)
        if (budget.closed or value.scheme != "https" or value.netloc not in {"www.alphavantage.co", "alphavantage.co"}
                or value.path != "/query" or method != "GET" or data is not None
                or set(query) - {"function", "symbol", "apikey", "outputsize", "datatype"}
                or len(query.get("function", [])) != 1 or query["function"][0] not in
                {"OVERVIEW", "INCOME_STATEMENT", "CASH_FLOW", "TIME_SERIES_DAILY",
                 "TIME_SERIES_DAILY_ADJUSTED", "TIME_SERIES_WEEKLY", "GLOBAL_QUOTE"}):
            raise AdmissionDenied("PROVIDER_PATH_UNSUPPORTED")
    def open_provider(opener, fullurl, data=None, timeout=__import__('socket')._GLOBAL_DEFAULT_TIMEOUT):
        url = fullurl.full_url if isinstance(fullurl, urllib.request.Request) else fullurl
        method = fullurl.get_method() if isinstance(fullurl, urllib.request.Request) else "GET" if data is None else "POST"
        payload = data if data is not None else getattr(fullurl, "data", None)
        provider_read(url, method, payload)
        with permitted_network():
            # Redirects re-enter this wrapper and must satisfy the same rule.
            return urllib_open(opener, fullurl, data, timeout)
    def send_provider(session, request, **kwargs):
        provider_read(request.url, request.method, request.body)
        with permitted_network():
            # requests redirects call Session.send again; each is validated.
            return requests_send(session, request, **kwargs)
    try:
        if not _audit_installed:
            sys.addaudithook(_audit)
            _audit_installed = True
        _active = budget
        httpx.Client._send_single_request = send
        httpx.AsyncClient._send_single_request = async_send
        subprocess.Popen = no_child
        urllib.request.OpenerDirector.open = open_provider
        requests.sessions.Session.send = send_provider
        if fork is not None:
            os.fork = no_child
        yield budget
    finally:
        budget.closed = True
        if not process_lifetime:
            httpx.Client._send_single_request = sync
            httpx.AsyncClient._send_single_request = asynchronous
            subprocess.Popen = popen
            urllib.request.OpenerDirector.open = urllib_open
            requests.sessions.Session.send = requests_send
            if fork is not None:
                os.fork = fork
            _active = None
            _installation.release()
        # Production acceptance keeps the closed guard installed until exit:
        # timed-out PEER threads must never regain an unguarded transport.

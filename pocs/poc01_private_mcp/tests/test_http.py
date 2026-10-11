"""Actual HTTP transport tests, isolated from the WealthPilot application."""

import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import jsonschema
from mcp import Client
import pytest


POC_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = POC_DIR.parents[1]
TOOL = "get_synthetic_wealth_summary"
FORBIDDEN_MODULES = (
    "app", "backend", "decision_engine", "intent_engine", "research_v2",
    "services", "sqlalchemy", "sqlite3", "ib_async", "tigeropen", "futu", "openai",
)


@pytest.fixture(scope="module")
def endpoint(tmp_path_factory):
    work = tmp_path_factory.mktemp("mcp_http")
    # Abort if the isolated server tries business imports, DB/env access or egress.
    (work / "sitecustomize.py").write_text(
        "import atexit, importlib.abc, json, os, sys\n"
        f"forbidden={FORBIDDEN_MODULES!r}\n"
        "violations=[]\n"
        "def save():\n"
        " with open('isolation.json', 'w') as handle:\n"
        "  json.dump({'violations':violations, 'business_modules_loaded':\n"
        "   [n for n in sys.modules if n.split('.')[0] in forbidden]}, handle)\n"
        "class Guard(importlib.abc.MetaPathFinder):\n"
        " def find_spec(self, fullname, path=None, target=None):\n"
        "  if fullname.split('.')[0] in forbidden:\n"
        "   violations.append('business_import:' + fullname)\n"
        "   save()\n"
        "   raise RuntimeError('Business import forbidden in synthetic PoC')\n"
        "sys.meta_path.insert(0, Guard())\n"
        "def audit(event, args):\n"
        " if event == 'socket.connect':\n"
        "  violations.append('outbound_connect')\n"
        "  save()\n"
        "  raise RuntimeError('Outbound connection forbidden in synthetic PoC')\n"
        " if event == 'open' and isinstance(args[0], (str, bytes)):\n"
        "  name=os.fsdecode(args[0]); base=os.path.basename(name)\n"
        f"  protected=os.path.realpath(name).startswith({str(REPO_DIR / 'data')!r} + os.sep)\n"
        "  if protected or base.startswith('.env') or base.endswith(('.db', '.pem')):\n"
        "   violations.append('protected_file_access')\n"
        "   save()\n"
        "   raise RuntimeError('DB/env/credential access forbidden in synthetic PoC')\n"
        "sys.addaudithook(audit)\n"
        "save()\n"
        "atexit.register(save)\n",
    )
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    url = f"http://127.0.0.1:{port}/mcp"
    env = {"PATH": os.defpath, "PYTHONPATH": str(work), "PYTHONNOUSERSITE": "1",
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
    log = (work / "server.log").open("w")
    process = subprocess.Popen(
        [sys.executable, str(POC_DIR / "server.py"), "--port", str(port)],
        cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail("Server exited at startup; see isolated server.log")
            try:
                with urlopen(url, timeout=0.2):
                    break
            except HTTPError:
                break  # A real HTTP response proves the dedicated listener is ready.
            except (URLError, TimeoutError):
                time.sleep(0.05)
        else:
            pytest.fail("Isolated HTTP listener did not become ready")
        yield url, work
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()
        evidence = json.loads((work / "isolation.json").read_text())
        assert evidence == {"violations": [], "business_modules_loaded": []}


def wire(url, payload, headers=None, method="POST"):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    request = Request(url, data=body if method == "POST" else None, method=method,
                      headers={"Content-Type": "application/json",
                               "Accept": "application/json, text/event-stream",
                               **(headers or {})})
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers), response.read()
    except HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def rpc(method, params=None, request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, **(
        {"params": params} if params is not None else {})}


def test_discovery_and_output_contract(endpoint):
    url, work = endpoint

    async def check():
        async with Client(url, mode="legacy", read_timeout_seconds=5) as client:
            listed = await client.list_tools()
            assert [t.name for t in listed.tools] == [TOOL]
            tool = listed.tools[0]
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.open_world_hint is False
            assert tool.input_schema["properties"]["currency"]["const"] == "CNY"
            result = await client.call_tool(TOOL, {"currency": "CNY"})
            assert not result.is_error
            data = result.structured_content
            jsonschema.validate(data, tool.output_schema)
            assert data["synthetic"] is True
            assert data["source"] == "hardcoded_fixture"
            assert data["amount_unit"] == "minor" and data["minor_unit_scale"] == 2
            assert sum(a["amount_minor"] for a in data["assets"]) == data["total_assets_minor"]
            assert data["net_worth_minor"] == data["total_assets_minor"] - data["total_liabilities_minor"]
            assert data["net_worth_minor"] == 90_000_000
            assert json.loads(result.content[0].text) == data
            repeat = await client.call_tool(TOOL, {})
            assert repeat.structured_content == data
            (work / "contract.json").write_text(json.dumps({
                "tools": [tool.model_dump(by_alias=True, exclude_none=True)],
                "result": result.model_dump(by_alias=True, exclude_none=True),
            }, indent=2))
    asyncio.run(check())


@pytest.mark.parametrize("name,args", [
    (TOOL, {"currency": "USD"}), (TOOL, {"currency": 123}),
    ("place_order", {}),
])
def test_tool_errors_and_recovery(endpoint, name, args):
    async def check():
        async with Client(endpoint[0], mode="legacy", read_timeout_seconds=5) as client:
            result = await client.call_tool(name, args)
            assert result.is_error and result.structured_content is None
            assert result.content and result.content[0].text
            assert "Traceback" not in result.content[0].text
            recovered = await client.call_tool(TOOL, {})
            assert not recovered.is_error and recovered.structured_content["synthetic"] is True
    asyncio.run(check())


def test_legacy_initialize_and_json_rpc(endpoint):
    url, work = endpoint
    status, headers, raw = wire(url, rpc("initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "poc01-wire-test", "version": "1.0"},
    }))
    assert status == 200 and headers["content-type"].startswith("application/json")
    initialized = json.loads(raw)
    assert initialized["result"]["protocolVersion"] == "2025-06-18"
    version_header = {"MCP-Protocol-Version": "2025-06-18"}
    status, _, raw = wire(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, version_header)
    assert status == 202
    status, _, raw = wire(url, rpc("tools/list", request_id=2), version_header)
    assert status == 200 and [t["name"] for t in json.loads(raw)["result"]["tools"]] == [TOOL]
    status, _, raw = wire(url, rpc("tools/call", {"name": TOOL, "arguments": {}}, 3), version_header)
    called = json.loads(raw)
    assert status == 200 and called["result"]["structuredContent"]["synthetic"] is True
    status, _, raw = wire(url, rpc("unsupported/method", request_id=4), version_header)
    error = json.loads(raw)
    assert status == 200 and error["error"]["code"] == -32601
    (work / "wire.json").write_text(json.dumps({
        "initialize": initialized, "call": called, "unsupported_method": error,
    }, indent=2))


@pytest.mark.parametrize("payload,headers,expected", [
    (b"{invalid json", {}, 400),
    (rpc("tools/list"), {"Accept": "text/plain"}, 406),
    (rpc("tools/list"), {"Content-Type": "text/plain"}, 400),
    (rpc("tools/list"), {"Host": "untrusted.example"}, 421),
    (rpc("tools/list"), {"Origin": "https://untrusted.example"}, 403),
])
def test_http_errors(endpoint, payload, headers, expected):
    status, _, _ = wire(endpoint[0], payload, headers)
    assert status == expected
    status, _, raw = wire(endpoint[0], rpc("tools/list"))
    assert status == 200 and len(json.loads(raw)["result"]["tools"]) == 1


def test_no_business_routes_or_legacy_sse(endpoint):
    for path in ("/api/health", "/api/portfolio/summary", "/sse"):
        status, _, _ = wire(endpoint[0].removesuffix("/mcp") + path, {}, method="GET")
        assert status == 404
    # The SDK also offers the optional GET SSE stream; don't wait on an open stream.
    with urlopen(Request(endpoint[0], headers={"Accept": "text/event-stream"}), timeout=5) as response:
        assert response.status == 200
        assert response.headers["content-type"].startswith("text/event-stream")

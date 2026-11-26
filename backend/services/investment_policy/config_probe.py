"""Read-only probe adapters for the installed Policy Configuration CLI.

Candidate projection runs locally in the installed interpreter. ACTIVE Web and
MCP checks go through their running loopback transports. No app startup hooks.
"""
import argparse
import asyncio
from contextlib import nullcontext
import json
import os
from pathlib import Path
import sys
import re
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .contracts import canonical, digest, parse_snapshot
from .formal_publisher import CONSUMERS
from .parser import PolicyError
from .publish_cli import private_artifact
from .runtime import PolicySession, bind, current


class CandidateSession(PolicySession):
    def __init__(self, portfolio_id, context):
        self.portfolio_id = portfolio_id
        self._payload = canonical(context)
        self.assets = None
        self.receipts = {}

    def ensure_current(self):
        # An isolated projection of an explicit candidate, never a real ACTIVE
        # read. This override remains confined to the probe subprocess.
        pass


def _loopback(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise PolicyError("LOCAL_CONSUMER_ENDPOINT_REQUIRED")
    return url


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


async def _mcp(portfolio_id, endpoint):
    from mcp import Client

    if endpoint:
        server = _loopback(endpoint)
    else:
        from pocs.chatgpt_skills.server import create_server

        server = create_server(SimpleNamespace(real_config="probe-never-uses-business-worker",
            mock_database=None, portfolio_id=portfolio_id, business_python=sys.executable, web_base_url=""))
    async with Client(server, mode="legacy", read_timeout_seconds=8) as client:
        result = await client.call_tool("get_investment_policy_context", {"scope":"portfolio"})
        if result.is_error:
            raise PolicyError("MCP_POLICY_PROBE_UNAVAILABLE")
        return result.structured_content or json.loads(result.content[0].text)


def _running_response(name, endpoint, header_file, portfolio_id):
    if name == "mcp":
        return asyncio.run(_mcp(portfolio_id, endpoint))
    headers = json.loads(private_artifact(header_file)) if header_file else {}
    url = _loopback(endpoint) + "?" + urlencode({"view":"portfolio"})
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(Request(url, headers=headers), timeout=8) as response:
        if response.geturl() != url:
            raise PolicyError("WEB_POLICY_PROBE_REDIRECT_REFUSED")
        return json.load(response)


def _reference(name, portfolio_id, phase, endpoint, header_file):
    if name == "backend":
        session = current(portfolio_id)
        parse_snapshot(session.context["snapshot"])
        return session.context["policy_reference"]
    if name == "peer":
        from .runtime import prompt

        return json.loads(prompt(portfolio_id).rsplit("\n", 1)[1])["policy_reference"]
    if name == "allocation":
        from backend.services.allocation_service import get_targets

        session = current(portfolio_id)
        with bind(session):
            get_targets()
        return session.receipts["allocation"]
    if name == "discipline":
        from backend.services.discipline_service import get_rules_config

        with bind(current(portfolio_id)):
            return get_rules_config()["policy_reference"]
    if name == "mcp" and phase == "CANDIDATE":
        return asyncio.run(_mcp(portfolio_id, None))["policy_reference"]
    if phase == "ACTIVE":
        return _running_response(name, endpoint, header_file, portfolio_id)["policy_reference"]
    from app import state
    from backend.api.discipline import get_policy_context

    original_portfolio = state.portfolio_id
    state.portfolio_id = portfolio_id
    try:
        return get_policy_context(view="portfolio")["policy_reference"]
    finally:
        state.portfolio_id = original_portfolio


def candidate_session(portfolio_id, scope, bundle):
    snapshot = parse_snapshot(bundle["snapshot"])
    if bundle["scope"] != scope or digest(snapshot) != bundle["structured_sha256"]:
        raise PolicyError("CANDIDATE_PROBE_BINDING_MISMATCH")
    ref = {key:bundle[key] for key in ("scope", "bundle_id", "bundle_version", "structured_sha256")}
    ref["bundle_sha256"] = digest(bundle)
    return CandidateSession(portfolio_id, dict(mode="POLICY_ACTIVE", status="CANDIDATE_ONLY",
        policy_reference=ref, snapshot=bundle["snapshot"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consumer", required=True, choices=sorted(CONSUMERS))
    parser.add_argument("--endpoint", help="Installed Web policy context or MCP loopback URL")
    parser.add_argument("--header-file", help="Optional 0600 Web headers artifact")
    args = parser.parse_args(argv)
    request = json.load(sys.stdin)
    phase = request["phase"]
    if phase not in {"CANDIDATE", "ACTIVE"}:
        raise PolicyError("PROBE_PHASE_INVALID")
    scope = request["scope"]
    if not re.fullmatch(r"portfolio:[1-9][0-9]{0,17}", scope):
        raise PolicyError("PROBE_SCOPE_INVALID")
    portfolio_id = int(scope.split(":")[1])

    code = Path(__file__).resolve().parents[3]
    installed = json.loads((code.parent / "installed.json").read_text())
    sha = installed["revision"]
    if installed.get("package_verified") is not True or code.parent.name != sha:
        raise PolicyError("INSTALLED_CODE_EVIDENCE_REQUIRED")

    projection = nullcontext()
    if phase == "CANDIDATE":
        projection = bind(candidate_session(portfolio_id, scope, request["candidate_bundle"]))
    live = {}
    identity = {"code_sha":sha, "runtime_id":str(os.getpid())}
    if args.consumer in {"web", "mcp"}:
        if not args.endpoint:
            raise PolicyError("RUNNING_CONSUMER_ENDPOINT_REQUIRED")
        live = _running_response(args.consumer, args.endpoint, args.header_file, portfolio_id)
        identity = live["policy_runtime"]
        if identity.get("code_sha") != sha or not identity.get("runtime_id"):
            raise PolicyError("RUNNING_CONSUMER_REVISION_MISMATCH")
    with projection:
        reference = (live["policy_reference"] if live and phase == "ACTIVE" else
                     _reference(args.consumer, portfolio_id, phase, args.endpoint, args.header_file))
    print(canonical(dict(consumer=args.consumer, phase=phase, **identity, policy_reference=reference,
                         running_reference=live.get("policy_reference"))))



if __name__ == "__main__":
    main()

"""Transport/activation tests in an independent synthetic-only MCP process."""
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

SERVER = Path(__file__).with_name('server.py')


@pytest.fixture(scope='module')
def endpoint(tmp_path_factory):
    folder = tmp_path_factory.mktemp('poc02-http')
    # Synthetic access checks must never reach business imports or a database.
    (folder / 'sitecustomize.py').write_text(
        'import importlib.abc, sys\n'
        'class Guard(importlib.abc.MetaPathFinder):\n'
        ' def find_spec(self, fullname, path=None, target=None):\n'
        '  if fullname.split(".")[0] in {"app", "backend", "sqlite3", "sqlalchemy", "reader"}:\n'
        '   raise RuntimeError("Business imports forbidden before access verification")\n'
        'sys.meta_path.insert(0, Guard())\n')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}/mcp'
    env = {'PATH': os.defpath, 'PYTHONPATH': str(folder), 'PYTHONNOUSERSITE': '1'}
    log = (folder / 'server.log').open('w')
    process = subprocess.Popen([sys.executable, str(SERVER), '--port', str(port)],
        cwd=folder, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail('Synthetic server startup failed')
            try:
                urlopen(url, timeout=0.2).close()
                break
            except HTTPError:
                break
            except (URLError, TimeoutError):
                time.sleep(0.05)
        else:
            pytest.fail('Synthetic server listener unavailable')
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


def test_sdk_discovery_and_contract(endpoint):
    async def run():
        async with Client(endpoint, mode='legacy', read_timeout_seconds=5) as client:
            tools = await client.list_tools()
            assert [t.name for t in tools.tools] == ['get_wealth_overview']
            assert tools.tools[0].annotations.read_only_hint is True
            result = await client.call_tool('get_wealth_overview', {'currency': 'CNY'})
            assert not result.is_error
            data = result.structured_content
            jsonschema.validate(data, tools.tools[0].output_schema)
            assert data['synthetic'] is True and data['net_worth_minor'] == 90000000
            assert data['notice'].startswith('SYNTHETIC')
    asyncio.run(run())


@pytest.mark.parametrize('name,args', [
    ('get_wealth_overview', {'currency': 'USD'}),
    ('get_wealth_overview', {'currency': 123}),
    ('place_order', {}),
])
def test_errors_and_recovery(endpoint, name, args):
    async def run():
        async with Client(endpoint, mode='legacy', read_timeout_seconds=5) as client:
            result = await client.call_tool(name, args)
            assert result.is_error and result.structured_content is None
            assert 'Traceback' not in result.content[0].text
            recovered = await client.call_tool('get_wealth_overview', {})
            assert recovered.structured_content['synthetic'] is True
    asyncio.run(run())


@pytest.mark.parametrize('headers,status', [
    ({'Host': 'untrusted.example'}, 421),
    ({'Origin': 'https://untrusted.example'}, 403),
    ({'Accept': 'text/plain'}, 406),
])
def test_http_boundary(endpoint, headers, status):
    request = Request(endpoint, data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}).encode(),
        headers={'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream', **headers})
    with pytest.raises(HTTPError) as error:
        urlopen(request, timeout=5)
    assert error.value.code == status


@pytest.mark.parametrize('proof', [None, {}])
def test_real_activation_requires_completed_access_evidence(tmp_path, proof):
    config = tmp_path / 'private-config.json'
    config.write_text('{}')
    config.chmod(0o600)
    command = [sys.executable, str(SERVER), '--real-config', str(config)]
    if proof is not None:
        evidence = tmp_path / 'proof.json'
        evidence.write_text(json.dumps(proof))
        evidence.chmod(0o600)
        command += ['--access-evidence', str(evidence)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert completed.returncode != 0
    assert 'requires reviewed' in completed.stderr or 'have not passed' in completed.stderr

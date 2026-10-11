"""Pinned real CLI tests. Fictional key/ID, disposable config, loopback only.

Set TUNNEL_CLIENT_BINARY to v0.0.16; CI downloads the hash-pinned Linux build.
No control-plane access or production readiness is claimed by these tests.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
import deploy
import runner


@pytest.fixture
def tunnel(tmp_path, monkeypatch):
    binary = os.environ.get('TUNNEL_CLIENT_BINARY')
    if not binary:
        pytest.skip('Set TUNNEL_CLIENT_BINARY to the pinned v0.0.16 binary')
    version = subprocess.run([binary, 'run', '--help'], capture_output=True, text=True, check=True)
    assert '0.0.16+5f99daabd4aa4a77049e6d81d54a0d8c18335397' in version.stdout + version.stderr
    root = deploy.layout(tmp_path / 'prod with spaces')
    revision = 'a' * 40
    release = root / 'releases' / revision; release.mkdir(mode=0o700)
    common.write_json(release / 'installed.json', {'revision': revision})
    (release / 'tunnel').mkdir()
    (release / 'tunnel/tunnel-client').symlink_to(Path(binary).resolve())
    common.write_json(root / 'config/wealth-input.json', {'portfolio_id': 1})
    (root / 'config/tunnel-id').write_text('tunnel_' + 'a' * 32)
    (root / 'config/tunnel-id').chmod(0o600)
    monkeypatch.setattr(deploy, 'check_revision', lambda *a: None)
    monkeypatch.setattr(runner, 'check_revision', lambda *a: None)
    monkeypatch.setattr(runner, 'host_guard', lambda *a: None)
    deploy.configure(root, revision, 'fixture-mini', root / 'config/wealth-input.json', root / 'config/tunnel-id')
    key = root / 'credentials/runtime-key'; key.write_text('noncredential-test-fixture'); key.chmod(0o600)
    requests = []; reached = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get('Authorization')))
            if self.path.startswith('/v1/tunnels/'):
                reached.set()
            self.send_response(401); self.end_headers(); self.wfile.write(b'{"error":"fixture"}')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    url = 'http://127.0.0.1:' + str(server.server_port)
    config = common.private_json(root / 'config/tunnel.json')
    config['control_plane']['base_url'] = url
    config['health']['listen_addr'] = '127.0.0.1:0'
    config['mcp']['server_urls'][0]['url'] = url + '/mcp'
    common.write_json(root / 'config/tunnel.json', config)
    command, _ = runner.child_command(root, 'tunnel')
    environment = {'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path)}
    try:
        yield root, command, environment, requests, reached, url
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_pinned_doctor_loads_deployment_json_and_file_key(tunnel):
    root, command, environment, _, _, url = tunnel
    result = subprocess.run([command[0], 'doctor', '--config', str(root / 'config/tunnel.json'), '--json'],
                            capture_output=True, text=True, env=environment, cwd=root, timeout=10)
    checks = {check['id']: check for check in json.loads(result.stdout)['checks']}
    assert all(checks[k]['status'] == 'PASS' for k in ('config_source', 'profile_load', 'control_plane_api_key', 'mcp_target'))
    assert checks['mcp_target']['summary'] == url + '/mcp'
    # Overall doctor failure on a fake MCP/OAuth target is expected, not a production PASS.
    assert result.returncode == 2


def test_pinned_run_uses_runner_json_and_resolves_file_key(tunnel):
    root, command, environment, requests, reached, _ = tunnel
    process = subprocess.Popen(command, env=environment, cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert reached.wait(8), 'run never reached the loopback control plane'
        assert process.poll() is None
        assert not (root / 'stdout').exists()
        assert any(path.startswith('/v1/tunnels/tunnel_' + 'a' * 32) and
                   auth == 'Bearer noncredential-test-fixture' for path, auth in requests)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)


def test_pinned_old_profile_entry_rejects_same_json_before_network(tunnel):
    root, command, environment, requests, _, _ = tunnel
    command[command.index('--config')] = '--profile-file'
    result = subprocess.run(command, capture_output=True, text=True, env=environment, cwd=root, timeout=10)
    assert result.returncode == 1
    assert 'must end with .yaml' in result.stderr
    assert requests == []


def test_pinned_run_rejects_malformed_json_before_network(tunnel):
    root, command, environment, requests, _, _ = tunnel
    (root / 'config/tunnel.json').write_text('{broken fixture')
    result = subprocess.run(command, capture_output=True, text=True, env=environment, cwd=root, timeout=10)
    assert result.returncode == 1
    assert requests == []

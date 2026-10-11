"""Stage 2 private control-plane probe. Never print identities, key or API bodies."""
import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from common import private_json, require, safe_cli, write_json


def request(path, key=None):
    headers = {"Authorization": "Bearer " + key} if key else {}
    try:
        with urlopen(Request("https://api.openai.com" + path, headers=headers), timeout=15) as response:
            body = json.load(response) if path.rsplit("/", 1)[-1] != "poll" else None
            return response.status, body
    except HTTPError as error:
        return error.code, None


def probe(tunnel_id, key, expected, fetch=request):
    path = "/v1/tunnels/" + tunnel_id
    status, metadata = fetch(path, key)
    require(status == 200 and isinstance(metadata, dict), "PRODUCTION_TUNNEL_AUTHORIZATION_FAILED")
    organizations = metadata.get("organization_ids", [])
    workspaces = metadata.get("workspace_ids", [])
    require(bool(expected["organization_ids"]) and bool(expected["workspace_ids"]), "EXPECTED_PRIVATE_SCOPE_REQUIRED")
    scope_equal = set(organizations) == set(expected["organization_ids"]) and set(workspaces) == set(expected["workspace_ids"])
    probes = []
    for endpoint in (path, path + "/poll"):
        for credential in (None, "invalid-poc03-noncredential"):
            code, _ = fetch(endpoint, credential)
            probes.append(code)
    denied = all(code == 401 for code in probes)
    return {"schema": "poc03.private-access-check.v1", "result": "PASS" if scope_equal and denied else "FAIL",
            "authorized_metadata_http": status, "organization_count": len(organizations),
            "workspace_count": len(workspaces), "expected_private_scope_equal": scope_equal,
            "anonymous_and_invalid_statuses": probes, "anonymous_denied": denied,
            "least_privilege_key_permission": "REQUIRES_OPERATOR_REVIEW",
            "other_workspace_impersonation": "NOT_TESTED",
            "poc02_credential_incident": "FAIL_NOT_REMEDIATED_BY_NEW_KEY"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--expected-scope", required=True, help="0600 JSON of intended IDs, never in Git")
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve()
    config = private_json(root / "config/tunnel.json")
    path = root / "credentials/runtime-key"
    require(path.is_file() and not path.is_symlink() and path.stat().st_mode & 0o077 == 0, "PRIVATE_PRODUCTION_KEY_REQUIRED")
    result = probe(config["control_plane"]["tunnel_id"], path.read_text().strip(), private_json(args.expected_scope))
    write_json(root / "evidence/private-access-check.json", result)
    print(json.dumps(result))
    if result["result"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    safe_cli(main)

"""Thin opt-in bridge to a fixed protected loopback prepare endpoint. No DB writes."""
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError


def _private(path):
    import os, stat
    path = Path(path)
    info = path.lstat()
    if (path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 16384):
        raise ValueError("ANALYSIS_PREPARE_NOT_CONFIGURED")
    return path.read_text()


def private_token(path):
    token = _private(path).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise ValueError("ANALYSIS_PREPARE_NOT_CONFIGURED")
    return token


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # never forward the prepare token to another URL


class PrepareClient:
    def __init__(self, path):
        config = json.loads(_private(path))
        if set(config) != {"base_url", "token_file"}:
            raise ValueError("ANALYSIS_PREPARE_NOT_CONFIGURED")
        url = urlsplit(config["base_url"])
        if (url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port or url.username or url.password
                or url.path not in ("", "/") or url.query or url.fragment):
            raise ValueError("ANALYSIS_PREPARE_NOT_CONFIGURED")
        self.base = config["base_url"].rstrip("/") + "/api/asset-analysis"
        self.token = private_token(config["token_file"])
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def _call(self, path, body=None):
        request = Request(self.base+path, data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization":"Bearer "+self.token,"Content-Type":"application/json"})
        try:
            with self.opener.open(request, timeout=5) as response:
                data = response.read(4*1024*1024+1)
                if len(data) > 4*1024*1024:
                    raise ValueError("ANALYSIS_PREPARE_UNAVAILABLE")
                return json.loads(data)
        except (OSError, HTTPError, URLError, ValueError):
            raise ValueError("ANALYSIS_PREPARE_UNAVAILABLE") from None

    def prepare(self, request_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id):
            raise ValueError("ANALYSIS_REQUEST_CONFLICT")
        result = self._call("/prepare", {"analysis_request_id":request_id})
        until = time.monotonic()+35
        while result["state"] == "preparing" and time.monotonic() < until:
            time.sleep(.5)
            result = self._call("/requests/"+request_id)
        return result

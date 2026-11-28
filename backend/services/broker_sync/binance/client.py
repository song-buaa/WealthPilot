"""Official GET-only Binance Spot transport. Authentication never enters logs."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import hmac
import http.client
import json
import re
import ssl
import time
from urllib import error, parse, request

from backend.core.env import normalized_optional_env
from . import valid_asset_code


@dataclass(frozen=True)
class Configuration:
    account_id: str
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    https_connect_port: int | None = None


def configuration():
    """Consume the existing startup-loaded environment; never open .env here."""
    values = [normalized_optional_env(name) for name in
              ("BINANCE_UID", "BINANCE_API_KEY", "BINANCE_API_SECRET")]
    if not all(values):
        raise ValueError("BINANCE_NOT_CONFIGURED")
    uid, key, secret = values
    if (not re.fullmatch(r"[1-9][0-9]{0,29}", uid)
            or not all(re.fullmatch(r"[A-Za-z0-9]{16,256}", v) for v in (key, secret))):
        raise ValueError("BINANCE_CONFIGURATION_INVALID")
    port = normalized_optional_env("BINANCE_HTTPS_CONNECT_PORT")
    if port is not None and (not re.fullmatch(r"[1-9][0-9]{0,4}", port) or int(port) > 65535):
        raise ValueError("BINANCE_CONNECT_PROXY_INVALID")
    return Configuration(uid, key, secret, int(port) if port is not None else None)


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class BinanceReadonlyClient:
    HOST = "https://api.binance.com"
    PUBLIC = {"/api/v3/time", "/api/v3/ticker/price", "/api/v3/aggTrades"}
    SIGNED = {"/api/v3/account", "/api/v3/myTrades"}

    def __init__(self, config, *, transport=None, clock=time.time, sleep=time.sleep):
        self.config = config
        self.transport = transport or self._send
        self.clock, self.sleep = clock, sleep
        self.offset_ms = None
        self.retry_after = None

    def _send(self, path, query, headers):
        if self.config.https_connect_port is not None:
            return self._send_via_connect(path, query, headers)
        # urllib's non-debug transport does not log authenticated request URLs.
        opener = request.build_opener(request.ProxyHandler({}), NoRedirect())
        req = request.Request(self.HOST + path + ("?" + query if query else ""),
                              headers=headers, method="GET")
        try:
            with opener.open(req, timeout=6) as response:
                return response.status, dict(response.headers), response.read(4 * 1024 * 1024)
        except error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read(65536)
        except (OSError, error.URLError):
            raise ConnectionError("BINANCE_CONNECTION_UNAVAILABLE") from None

    def _send_via_connect(self, path, query, headers):
        # CONNECT sends only the destination to the local proxy. HTTPSConnection
        # verifies the official endpoint before sending the signed request.
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        tls.load_default_certs()
        conn = http.client.HTTPSConnection(
            "127.0.0.1", self.config.https_connect_port, timeout=6, context=tls,
        )
        conn.set_tunnel(parse.urlsplit(self.HOST).hostname, 443)
        try:
            conn.request("GET", path + ("?" + query if query else ""), headers=headers)
            with conn.getresponse() as response:
                limit = 65536 if response.status >= 300 else 4 * 1024 * 1024
                return response.status, dict(response.getheaders()), response.read(limit)
        except (OSError, http.client.HTTPException):
            raise ConnectionError("BINANCE_CONNECTION_UNAVAILABLE") from None
        finally:
            conn.close()

    def server_time(self):
        value = self.get("/api/v3/time")
        stamp = value.get("serverTime") if isinstance(value, dict) else None
        if type(stamp) is not int or stamp <= 0:
            raise ValueError("BINANCE_RESPONSE_INVALID")
        self.offset_ms = stamp - int(self.clock() * 1000)
        if abs(self.offset_ms) > 300000:
            raise ValueError("BINANCE_CLOCK_SKEW")
        return stamp

    def exchange_time_ms(self):
        if self.offset_ms is None:
            raise ValueError("BINANCE_CLOCK_SKEW")
        return int(self.clock() * 1000) + self.offset_ms

    def get(self, path, parameters=None):
        if path not in self.PUBLIC | self.SIGNED:
            raise ValueError("BINANCE_READONLY_ENDPOINT_REQUIRED")
        params = dict(parameters or {})
        allowed = {
            "/api/v3/time": set(), "/api/v3/account": {"omitZeroBalances"},
            "/api/v3/myTrades": {"symbol", "fromId", "limit"},
            "/api/v3/ticker/price": {"symbol"}, "/api/v3/aggTrades": {"symbol", "limit"},
        }[path]
        if set(params) - allowed:
            raise ValueError("BINANCE_PARAMETER_REJECTED")
        if "symbol" in params:
            symbol = params["symbol"]
            allowed_symbol = isinstance(symbol, str) and (
                (symbol.endswith("USDT") and valid_asset_code(symbol[:-4])) if path == "/api/v3/aggTrades"
                else symbol in {"BTCUSDT", "USDCUSDT"})
            if not allowed_symbol or path == "/api/v3/myTrades" and symbol != "BTCUSDT":
                raise ValueError("BINANCE_SYMBOL_UNSUPPORTED")
        signed = path in self.SIGNED
        if signed and self.offset_ms is None:
            self.server_time()
        for attempt in range(2):
            values = dict(params)
            headers = {"Accept": "application/json"}
            if signed:
                values.update(timestamp=self.exchange_time_ms(), recvWindow=5000)
                headers["X-MBX-APIKEY"] = self.config.api_key
            query = parse.urlencode(sorted(values.items()))
            if signed:
                digest = hmac.new(self.config.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
                query += "&signature=" + digest
            try:
                status, response_headers, body = self.transport(path, query, headers)
            except (OSError, ConnectionError):
                if attempt == 0:
                    self.sleep(.25)
                    continue
                raise ValueError("BINANCE_CONNECTION_UNAVAILABLE") from None
            if status in {418, 429}:
                retry = response_headers.get("Retry-After", response_headers.get("retry-after"))
                try:
                    self.retry_after = max(0, int(retry))
                except (ValueError, TypeError):
                    try:
                        self.retry_after = max(0, int(parsedate_to_datetime(retry).timestamp() - self.clock()))
                    except (ValueError, TypeError, AttributeError):
                        self.retry_after = None
                # Never spend a lease sleeping through a remote rate-limit window.
                raise ValueError("BINANCE_RATE_LIMITED")
            if status >= 500 and attempt == 0:
                self.sleep(.25)
                continue
            try:
                data = json.loads(body)
            except (ValueError, TypeError):
                raise ValueError("BINANCE_RESPONSE_INVALID") from None
            code = data.get("code") if isinstance(data, dict) else None
            if code == -1021 and signed and attempt == 0:
                self.server_time()
                continue
            if code == -1021:
                raise ValueError("BINANCE_CLOCK_SKEW")
            if code == -1022:
                raise ValueError("BINANCE_SIGNATURE_INVALID")
            if status in {401, 403, 451} or code in {-2014, -2015}:
                raise ValueError("BINANCE_PERMISSION_DENIED")
            if status != 200 or code is not None:
                raise ValueError("BINANCE_SOURCE_UNAVAILABLE")
            return data
        raise ValueError("BINANCE_SOURCE_UNAVAILABLE")

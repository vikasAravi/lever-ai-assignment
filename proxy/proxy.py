#!/usr/bin/env python3
"""Deterministic fault-injecting reverse proxy for the GLPI assignment pack.

Listens on :8080 and forwards everything to GLPI (env UPSTREAM). UI traffic passes
through untouched. Requests whose path starts with /apirest.php or /api.php are
"API calls": each gets a global call number n, and random.Random(f"{seed}:{n}")
decides, drawing in a fixed order, whether the call is answered 502 without being
forwarded, forwarded but answered 504 after GLPI committed it (writes only),
delayed, or left alone. Session tokens handed out by initSession are remembered as
hashes; after every N API calls the oldest live one is expired, and a request that
carries an expired token is answered by the proxy itself: GET gets 200 [] (a product
that answers empty instead of erroring), anything else 401. Faults go to a log file.

Standard library only at runtime, plus PyYAML to read the config. Python 3.12.
"""
import gzip
import hashlib
import http.client
import json
import os
import random
import signal
import sys
import threading
import time
import zlib
from collections import OrderedDict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

try:
    import yaml
except ImportError:  # the Dockerfile installs it; only load_config() needs it
    yaml = None

API_PREFIXES = ("/apirest.php", "/api.php")
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
HOP_BY_HOP = frozenset({"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
                        "te", "trailer", "transfer-encoding", "upgrade"})
JSON_HEADERS = [("Content-Type", "application/json")]
DEFAULT_TOP = {"seed": "candidate-2026", "log_faults": True, "log_path": "/app/logs/faults.log"}
DEFAULT_FAULTS = {"delay_rate": 0.10, "delay_rate_lists": 0.35, "delay_ms_min": 1500, "delay_ms_max": 6000,
                  "error_502_rate": 0.04, "drop_write_rate": 0.06, "session_expiry_every_n_calls": 120}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ConfigError(Exception):
    """A config the proxy refuses to start with."""


def _rate(name, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ConfigError(f"faults.{name} must be a number between 0 and 1, got {value!r}")
    return float(value)


def _integer(name, value, minimum):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"faults.{name} must be an integer >= {minimum}, got {value!r}")
    return value


class Config:
    """Validated settings. Missing keys take the defaults; bad values raise ConfigError."""

    def __init__(self, mapping=None):
        mapping = {} if mapping is None else mapping
        if not isinstance(mapping, dict):
            raise ConfigError("the config file must be a YAML mapping")
        faults = mapping.get("faults") or {}
        if not isinstance(faults, dict):
            raise ConfigError("'faults' must be a mapping")
        # Decision: unknown keys fail fast. A misspelt rate silently falling back to its
        # default would defeat the reviewer's seed/rate swap without anyone noticing.
        unknown = sorted(set(mapping) - set(DEFAULT_TOP) - {"faults"})
        unknown += sorted(f"faults.{k}" for k in set(faults) - set(DEFAULT_FAULTS))
        if unknown:
            raise ConfigError(f"unknown key(s): {', '.join(unknown)}")
        top = {**DEFAULT_TOP, **mapping}
        seed, log_faults, log_path = top["seed"], top["log_faults"], top["log_path"]
        if isinstance(seed, bool) or not isinstance(seed, (str, int)) or str(seed) == "":
            raise ConfigError("seed must be a non-empty string")
        if not isinstance(log_faults, bool):
            raise ConfigError("log_faults must be true or false")
        if not isinstance(log_path, str) or not log_path:
            raise ConfigError("log_path must be a non-empty string")
        self.seed, self.log_faults, self.log_path = str(seed), log_faults, log_path
        self.fingerprint = hashlib.sha256(self.seed.encode("utf-8")).hexdigest()[:8]
        f = {**DEFAULT_FAULTS, **faults}
        self.error_502_rate = _rate("error_502_rate", f["error_502_rate"])
        self.drop_write_rate = _rate("drop_write_rate", f["drop_write_rate"])
        self.delay_rate = _rate("delay_rate", f["delay_rate"])
        self.delay_rate_lists = _rate("delay_rate_lists", f["delay_rate_lists"])
        self.delay_ms_min = _integer("delay_ms_min", f["delay_ms_min"], 0)
        self.delay_ms_max = _integer("delay_ms_max", f["delay_ms_max"], 0)
        if self.delay_ms_max < self.delay_ms_min:
            raise ConfigError("faults.delay_ms_max must be >= faults.delay_ms_min")
        self.session_expiry_every_n_calls = _integer(
            "session_expiry_every_n_calls", f["session_expiry_every_n_calls"], 1)

    def rates_text(self):
        return (f"error_502_rate={self.error_502_rate} drop_write_rate={self.drop_write_rate} "
                f"delay_rate={self.delay_rate} delay_rate_lists={self.delay_rate_lists} "
                f"delay_ms={self.delay_ms_min}-{self.delay_ms_max} "
                f"session_expiry_every_n_calls={self.session_expiry_every_n_calls}")


def load_config(path):
    if yaml is None:
        raise ConfigError("PyYAML is not installed (pip install pyyaml)")
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from None
    return Config(data)


def is_api_path(path):
    return path.startswith(API_PREFIXES)


def is_session_endpoint(path):
    return "/initSession" in path or "/killSession" in path


def is_list_read(method, path):
    """GET to /search/... or to a collection without a numeric id, e.g. /apirest.php/Computer."""
    if method != "GET":
        return False
    rest = path[len("/apirest.php"):] if path.startswith("/apirest.php") else path[len("/api.php"):]
    return rest.startswith("/search/") or not any(seg.isdigit() for seg in rest.split("/"))


def decide(cfg, n, method, list_read, exempt):
    """Fault for API call n as (name, delay_ms). Draws u1..u4 in a fixed order every time, so
    the outcome depends only on (seed, n, method, path): never on timing, threads or earlier draws."""
    rng = random.Random(f"{cfg.seed}:{n}")
    u1, u2, u3, u4 = rng.random(), rng.random(), rng.random(), rng.random()
    delay_ms = cfg.delay_ms_min + int(u4 * (cfg.delay_ms_max - cfg.delay_ms_min + 1))
    if exempt:
        return None, 0
    if u1 < cfg.error_502_rate:
        return "injected_502", 0
    if method in WRITE_METHODS and u2 < cfg.drop_write_rate:
        return "dropped_write", 0
    if u3 < (cfg.delay_rate_lists if list_read else cfg.delay_rate):
        return "delayed", delay_ms
    return None, 0


def session_token_from(headers, data):
    """The session_token in an initSession reply, or None. Decodes gzip/deflate for parsing only;
    the bytes relayed to the client are never touched."""
    encoding = next((v for k, v in headers if k.lower() == "content-encoding"), "").lower()
    try:
        if "gzip" in encoding:
            data = gzip.decompress(data)
        elif "deflate" in encoding:
            data = zlib.decompress(data)
        token = json.loads(data.decode("utf-8")).get("session_token")
    except (ValueError, OSError, EOFError, AttributeError, zlib.error):
        return None
    return token if isinstance(token, str) and token else None


class FaultLog:
    """Appends one line per event to log_path; falls back to stderr when the file cannot be written."""

    def __init__(self, path, enabled):
        self.path, self.enabled, self.lock, self.warned = path, enabled, threading.Lock(), False
        if enabled:
            try:
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            except OSError as exc:
                self._warn(exc)

    def _warn(self, exc):
        if not self.warned:
            self.warned = True
            print(f"proxy: cannot write {self.path} ({exc}); fault lines go to stderr", file=sys.stderr, flush=True)

    def write(self, text):
        if not self.enabled:
            return
        line = f"{now_iso()} {text}\n"
        # Decision: open-append-close per line, so a log the reviewer deletes mid-run starts
        # afresh and nothing sits in a buffer when the container is stopped.
        with self.lock:
            try:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line)
            except OSError as exc:
                self._warn(exc)
                sys.stderr.write("faults.log " + line)
                sys.stderr.flush()


class State:
    """Everything shared between request threads: the call counter, the sessions and the log."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.api_calls = 0
        self.live = OrderedDict()  # sha256(token) -> last 4 chars, oldest first
        self.expired = {}          # sha256(token) -> last 4 chars
        self.log = FaultLog(cfg.log_path, cfg.log_faults)
        self.log.write(f"call=0 fault=startup seed_fingerprint={cfg.fingerprint} {cfg.rates_text()}")

    @staticmethod
    def _key(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def begin_api_call(self, method, path, token, exempt):
        """Count the call, judge its token and apply the scheduled expiry under one lock, so the
        result depends only on the order in which API calls arrived. Returns (n, token_expired)."""
        with self.lock:
            self.api_calls += 1
            n = self.api_calls
            key = self._key(token) if token else None
            expired = key is not None and not exempt and key in self.expired
            if key is not None and "/killSession" in path:
                self.live.pop(key, None)
                self.expired.pop(key, None)
            # Decision: call N is served on its own verdict; the expiry it triggers is seen
            # from call N+1 on. That is "after every N calls" read literally, and it holds
            # whatever order the threads finish in.
            if n % self.cfg.session_expiry_every_n_calls == 0 and self.live:
                oldest, last4 = self.live.popitem(last=False)
                self.expired[oldest] = last4
                self.log.write(f"call={n} fault=session_expired_{last4} method={method} path={path} status=-")
        return n, expired

    def issue(self, token):
        key = self._key(token)
        with self.lock:
            self.expired.pop(key, None)
            self.live.pop(key, None)
            self.live[key] = token[-4:]

    def fault(self, n, name, method, path, status):
        self.log.write(f"call={n} fault={name} method={method} path={path} status={status}")

    def health(self):
        with self.lock:
            return {"ok": True, "seed_fingerprint": self.cfg.fingerprint, "api_calls": self.api_calls}


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # Decision: no per-request access log; faults.log is the record and stays readable.

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client went away mid-answer or between keep-alive requests

    def _handle(self):
        state = self.server.state
        path, _, query = self.path.partition("?")
        method = self.command
        try:
            body = self._read_body()
        except ValueError:
            self.close_connection = True
            return self._send(400, {"error": "bad_request_body"})
        if path.startswith("/_proxy/"):
            if path == "/_proxy/health" and method == "GET":
                return self._send(200, state.health())
            return self._send(404, {"error": "not_found"})
        if not is_api_path(path):
            return self._relay_reply(*self._fetch(body)[1:])
        # Decision: GLPI also accepts the token as a session_token query parameter, so it is
        # read too; otherwise moving the token out of the header would dodge the expiry.
        token = self.headers.get("Session-Token") or parse_qs(query).get("session_token", [None])[0]
        exempt = is_session_endpoint(path)
        n, expired = state.begin_api_call(method, path, token, exempt)
        # Every fault line is written before its answer leaves, so a client that has the
        # answer can rely on the log already holding the line.
        if expired:
            # Decision: an expired token is answered before any random fault and never reaches
            # GLPI, whose real session is still alive and must keep looking dead to the client.
            if method == "GET":
                state.fault(n, "expired_session_empty_200", method, path, 200)
                return self._send(200, [])
            state.fault(n, "expired_session_401", method, path, 401)
            return self._send(401, {"error": "ERROR_SESSION_TOKEN_INVALID"})
        name, delay_ms = decide(state.cfg, n, method, is_list_read(method, path), exempt)
        if name == "injected_502":
            state.fault(n, "injected_502", method, path, 502)
            return self._send(502, {"error": "injected_502"})
        if name == "delayed":
            time.sleep(delay_ms / 1000.0)
        answered, status, reason, headers, data = self._fetch(body)
        if name == "dropped_write" and answered:  # GLPI committed it; the client only sees a timeout
            state.fault(n, "dropped_write", method, path, 504)
            return self._send(504, {"error": "injected_timeout"})
        if exempt and answered and status == 200 and "/initSession" in path:
            fresh = session_token_from(headers, data)
            if fresh:
                state.issue(fresh)
        if name == "delayed":
            state.fault(n, f"delayed_{delay_ms}", method, path, status)
        self._relay_reply(status, reason, headers, data)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _handle

    def _read_body(self):
        """The whole request body (Content-Length or chunked), so it can be replayed upstream."""
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            chunks = []
            while True:
                size = int(self.rfile.readline(1024).split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline(1024).strip():
                        pass  # trailers end with an empty line
                    return b"".join(chunks)
                chunks.append(self.rfile.read(size))
                self.rfile.readline(1024)  # the CRLF after the chunk
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length > 0 else b""

    def _forward_headers(self):
        """The client's headers minus hop-by-hop ones, Host kept as sent, plus X-Forwarded-*."""
        out, host, xff = [], None, None
        for name, value in self.headers.items():
            lname = name.lower()
            if lname == "host":
                host = value
            elif lname == "x-forwarded-for":
                xff = value
            # Decision: Expect is dropped because the body is already read; Content-Length is
            # re-added from the bytes we hold (a chunked request reaches GLPI with a length).
            elif (lname in HOP_BY_HOP or lname.startswith(("proxy-", "x-forwarded-"))
                  or lname in ("content-length", "expect")):
                continue
            else:
                out.append((name, value))
        client_ip = self.client_address[0]
        out.insert(0, ("Host", host or self.server.upstream.netloc))
        out.append(("X-Forwarded-For", f"{xff}, {client_ip}" if xff else client_ip))
        out.append(("X-Forwarded-Proto", "http"))
        if host:
            out.append(("X-Forwarded-Host", host))
        return out

    def _fetch(self, body):
        """One upstream round trip -> (answered, status, reason, headers, data). When GLPI cannot
        be reached, the tuple carries the proxy's own answer and answered is False."""
        up = self.server.upstream
        cls = http.client.HTTPSConnection if up.scheme == "https" else http.client.HTTPConnection
        conn = cls(up.hostname, up.port or (443 if up.scheme == "https" else 80),
                   timeout=self.server.upstream_timeout)
        try:
            conn.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
            for name, value in self._forward_headers():
                conn.putheader(name, value)
            if body or self.command in ("POST", "PUT", "PATCH"):
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body)
            resp = conn.getresponse()
            return True, resp.status, resp.reason, resp.getheaders(), resp.read()
        except (ValueError, http.client.InvalidURL):
            return False, 400, None, JSON_HEADERS, b'{"error":"bad_request"}'
        except TimeoutError:
            return False, 504, None, JSON_HEADERS, b'{"error":"upstream_timeout"}'
        except (OSError, http.client.HTTPException):
            return False, 502, None, JSON_HEADERS, b'{"error":"upstream_unreachable"}'
        finally:
            conn.close()

    def _relay_reply(self, status, reason, headers, data):
        """Send a reply bytes for bytes, minus hop-by-hop headers, with a recomputed Content-Length."""
        no_body = self.command == "HEAD" or status in (204, 304) or 100 <= status < 200
        self.send_response_only(status, reason)
        for name, value in headers:
            lname = name.lower()
            if lname in HOP_BY_HOP or lname.startswith("proxy-") or (lname == "content-length" and not no_body):
                continue
            self.send_header(name, value)
        if not no_body:
            self.send_header("Content-Length", str(len(data)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if not no_body:
            self.wfile.write(data)

    def _send(self, status, obj):
        """One of the proxy's own JSON answers."""
        data = json.dumps(obj).encode("utf-8")
        self.send_response_only(status)
        for name, value in (("Date", self.date_time_string()), ("Content-Type", "application/json"),
                            ("Content-Length", str(len(data))), ("Cache-Control", "no-store")):
            self.send_header(name, value)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def __init__(self, address, cfg, upstream, upstream_timeout=120.0):
        parsed = urlsplit(upstream)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ConfigError(f"UPSTREAM must look like http://host:port, got {upstream!r}")
        super().__init__(address, ProxyHandler)
        self.upstream, self.upstream_timeout, self.state = parsed, upstream_timeout, State(cfg)


def main():
    config_path = os.environ.get("PROXY_CONFIG", "/app/config.yml")
    upstream = os.environ.get("UPSTREAM", "http://glpi:80")
    try:
        port = int(os.environ.get("PROXY_LISTEN_PORT", "8080"))
        server = ProxyServer(("0.0.0.0", port), load_config(config_path), upstream)
    except (ConfigError, ValueError, OSError) as exc:
        sys.exit(f"proxy: refusing to start: {exc}")
    cfg, up = server.state.cfg, server.upstream
    print(f"proxy: listening on :{port}, forwarding to {up.hostname}:{up.port or 80}, config {config_path}, "
          f"seed fingerprint {cfg.fingerprint}, fault log {'on: ' + cfg.log_path if cfg.log_faults else 'off'}",
          file=sys.stderr, flush=True)
    # Decision: docker stop sends SIGTERM; turning it into SystemExit lets the socket close cleanly.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

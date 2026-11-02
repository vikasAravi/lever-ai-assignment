"""Tests for proxy.py. A stub GLPI and the proxy run in-process on free ports.

Run:  python3 -m unittest -v test_proxy
"""
import gzip
import hashlib
import http.client
import io
import json
import os
import random
import secrets
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import proxy  # noqa: E402

HTML = b"<html><body>GLPI</body></html>"
BINARY = bytes(range(256)) * 4
LISTING = [{"id": 1}, {"id": 2}]
QUIET = {"error_502_rate": 0, "drop_write_rate": 0, "delay_rate": 0, "delay_rate_lists": 0,
         "session_expiry_every_n_calls": 1000}
# Default seed and rates, GET list reads, calls 1..60: derived by hand from random.Random
# (u1 for 502, u2 for drop, u3 for delay, u4 for the length). Pins the draw order.
GOLDEN = [(3, "delayed", 3393), (7, "delayed", 4452), (11, "delayed", 2605), (14, "delayed", 5582),
          (15, "delayed", 5753), (17, "delayed", 2379), (19, "injected_502", 0), (21, "injected_502", 0),
          (24, "delayed", 4190), (26, "delayed", 4934), (29, "delayed", 3468), (30, "delayed", 2746),
          (31, "delayed", 1996), (32, "delayed", 2883), (33, "delayed", 3376), (40, "delayed", 2468),
          (41, "delayed", 4722), (42, "delayed", 1535), (47, "delayed", 4365), (49, "delayed", 3331),
          (51, "injected_502", 0), (54, "delayed", 2863), (57, "delayed", 2483), (58, "delayed", 3684)]


def lower(headers):
    return {name.lower(): value for name, value in headers}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class StubHandler(BaseHTTPRequestHandler):
    """Canned GLPI: records every request, hands out a fresh session token per initSession."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the proxy closed a keep-alive connection, e.g. after a timeout

    def _reply(self, status, data, ctype="application/json", extra=()):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, status, obj):
        self._reply(status, json.dumps(obj).encode())

    def _dispatch(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        with self.server.lock:
            self.server.requests.append({"method": self.command, "target": self.path,
                                         "headers": list(self.headers.items()), "body": body})
        path = self.path.partition("?")[0]
        if path.endswith("/initSession"):
            with self.server.lock:
                self.server.issued += 1
                token = f"tok{self.server.issued:02d}{secrets.token_hex(8)}"
            return self._json(200, {"session_token": token})
        if path.endswith("/killSession"):
            return self._json(200, {})
        if path.startswith(("/apirest.php", "/api.php")):
            if self.command in ("POST", "PUT", "PATCH", "DELETE"):
                return self._json(201 if self.command == "POST" else 200, {"id": 99})
            last = path.rsplit("/", 1)[-1]
            return self._json(200, {"id": int(last), "name": "pc"} if last.isdigit() else LISTING)
        if path == "/redirect":
            return self._reply(302, b"", "text/html", [("Location", "/front/central.php")])
        if path == "/gz":
            return self._reply(200, gzip.compress(b'{"zipped": true}'), extra=[("Content-Encoding", "gzip")])
        if path == "/bin":
            return self._reply(200, BINARY, "application/octet-stream")
        if path == "/nocontent":
            self.send_response(204)
            return self.end_headers()
        if path == "/chunked":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for piece in (b"first,", b"second,", b"third"):
                self.wfile.write(b"%x\r\n%s\r\n" % (len(piece), piece))
            return self.wfile.write(b"0\r\n\r\n")
        if path == "/slow":
            time.sleep(1.0)
            try:
                return self._reply(200, b"late", "text/plain")
            except OSError:
                return None
        return self._reply(200, HTML, "text/html; charset=UTF-8",
                           [("Set-Cookie", "glpi_sid=abc; Path=/; HttpOnly"), ("Set-Cookie", "other=1; Path=/"),
                            ("Keep-Alive", "timeout=5"), ("X-Upstream", "stub")])

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _dispatch


class StubServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), StubHandler)
        self.lock, self.requests, self.issued = threading.Lock(), [], 0


class ProxyCase(unittest.TestCase):
    """A stub upstream per test; start_proxy() puts a proxy in front of it on a free port."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_path = os.path.join(self.tmp.name, "logs", "faults.log")
        self.stub = StubServer()
        self.addCleanup(self.stub.server_close)
        self.addCleanup(self.stub.shutdown)
        threading.Thread(target=self.stub.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def start_proxy(self, faults=None, seed="test-seed", upstream=None, upstream_timeout=120.0,
                    log_faults=True, log_path=None):
        cfg = proxy.Config({"seed": seed, "log_faults": log_faults, "log_path": log_path or self.log_path,
                            "faults": {**QUIET, **(faults or {})}})
        server = proxy.ProxyServer(("127.0.0.1", 0), cfg,
                                   upstream or f"http://127.0.0.1:{self.stub.server_address[1]}", upstream_timeout)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        return server

    def call(self, server, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, resp.getheaders(), resp.read()
        finally:
            conn.close()

    def health(self, server):
        return json.loads(self.call(server, "GET", "/_proxy/health")[2])

    def log_lines(self, path=None):
        with open(path or self.log_path, encoding="utf-8") as fh:
            return fh.read().splitlines()

    def upstream_seen(self):
        with self.stub.lock:
            return [(r["method"], r["target"]) for r in self.stub.requests]


class PassThroughTests(ProxyCase):
    def test_ui_request_passes_through_untouched(self):
        server = self.start_proxy()
        status, headers, body = self.call(server, "GET", "/front/central.php?a=1&b=2", headers={
            "Host": "localhost:8080", "Cookie": "glpi_sid=abc", "Connection": "keep-alive",
            "Proxy-Authorization": "secret", "TE": "trailers"})
        self.assertEqual((status, body), (200, HTML))
        self.assertEqual([v for n, v in headers if n.lower() == "set-cookie"],
                         ["glpi_sid=abc; Path=/; HttpOnly", "other=1; Path=/"])
        got = lower(headers)
        self.assertEqual((got["x-upstream"], got["content-length"]), ("stub", str(len(HTML))))
        self.assertNotIn("keep-alive", got)
        seen = self.stub.requests[0]
        self.assertEqual((seen["method"], seen["target"]), ("GET", "/front/central.php?a=1&b=2"))
        sent = lower(seen["headers"])
        self.assertEqual(sent["host"], "localhost:8080")
        self.assertEqual(sent["cookie"], "glpi_sid=abc")
        self.assertEqual((sent["x-forwarded-for"], sent["x-forwarded-proto"], sent["x-forwarded-host"]),
                         ("127.0.0.1", "http", "localhost:8080"))
        for gone in ("connection", "proxy-authorization", "te", "keep-alive"):
            self.assertNotIn(gone, sent)
        self.assertEqual(self.health(server)["api_calls"], 0)
        self.assertEqual(len(self.log_lines()), 1, "only the startup line")

    def test_x_forwarded_for_appends_to_an_existing_chain(self):
        server = self.start_proxy()
        self.call(server, "GET", "/", headers={"X-Forwarded-For": "10.0.0.9", "X-Forwarded-Proto": "https"})
        sent = lower(self.stub.requests[-1]["headers"])
        self.assertEqual((sent["x-forwarded-for"], sent["x-forwarded-proto"]), ("10.0.0.9, 127.0.0.1", "http"))

    def test_redirect_is_relayed_not_followed(self):
        server = self.start_proxy()
        status, headers, body = self.call(server, "GET", "/redirect")
        self.assertEqual((status, lower(headers)["location"], body), (302, "/front/central.php", b""))
        self.assertEqual(self.upstream_seen(), [("GET", "/redirect")])

    def test_gzip_and_binary_bodies_survive_byte_for_byte(self):
        server = self.start_proxy()
        status, headers, body = self.call(server, "GET", "/gz")
        self.assertEqual((status, lower(headers)["content-encoding"]), (200, "gzip"))
        self.assertEqual(gzip.decompress(body), b'{"zipped": true}')
        self.assertEqual(self.call(server, "GET", "/bin")[::2], (200, BINARY))

    def test_chunked_upstream_reply_gets_a_content_length(self):
        server = self.start_proxy()
        status, headers, body = self.call(server, "GET", "/chunked")
        got = lower(headers)
        self.assertEqual((status, body, got["content-length"]), (200, b"first,second,third", str(len(body))))
        self.assertNotIn("transfer-encoding", got)

    def test_head_and_204_replies_have_no_body(self):
        server = self.start_proxy()
        self.assertEqual(self.call(server, "HEAD", "/front/central.php")[::2], (200, b""))
        self.assertEqual(self.call(server, "GET", "/nocontent")[::2], (204, b""))

    def test_request_bodies_reach_upstream_with_a_length(self):
        server = self.start_proxy()
        payload = json.dumps({"input": {"name": "pc-1"}}).encode()
        status, _, body = self.call(server, "POST", "/apirest.php/Computer", body=payload,
                                    headers={"Content-Type": "application/json", "Session-Token": "unknown"})
        self.assertEqual((status, json.loads(body)), (201, {"id": 99}))
        seen = self.stub.requests[-1]
        self.assertEqual(seen["body"], payload)
        self.assertEqual(lower(seen["headers"])["content-length"], str(len(payload)))
        self.assertEqual(lower(seen["headers"])["session-token"], "unknown")
        # a chunked request body is de-chunked and forwarded with a Content-Length
        sock = socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=10)
        try:
            sock.sendall(b"PUT /apirest.php/Computer/3 HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n"
                         b"Content-Type: application/json\r\n\r\n5\r\n{\"a\":\r\n2\r\n1}\r\n0\r\n\r\n")
            resp = http.client.HTTPResponse(sock, method="PUT")
            resp.begin()
            self.assertEqual((resp.status, json.loads(resp.read())), (200, {"id": 99}))
        finally:
            sock.close()
        seen = self.stub.requests[-1]
        self.assertEqual(seen["body"], b'{"a":1}')
        self.assertEqual(lower(seen["headers"])["content-length"], "7")
        self.assertNotIn("transfer-encoding", lower(seen["headers"]))


class ProxyEndpointTests(ProxyCase):
    def test_health_reports_fingerprint_and_count_not_the_seed(self):
        server = self.start_proxy(seed="super-secret-seed")
        status, headers, body = self.call(server, "GET", "/_proxy/health")
        self.assertEqual((status, lower(headers)["content-type"]), (200, "application/json"))
        self.assertEqual(json.loads(body), {"ok": True, "api_calls": 0,
                                            "seed_fingerprint": hashlib.sha256(b"super-secret-seed").hexdigest()[:8]})
        self.assertNotIn(b"super-secret-seed", body)
        self.call(server, "GET", "/apirest.php/Computer")
        self.call(server, "GET", "/front/computer.php")
        self.assertEqual(self.health(server)["api_calls"], 1)
        self.assertEqual(self.upstream_seen(), [("GET", "/apirest.php/Computer"), ("GET", "/front/computer.php")])

    def test_other_proxy_paths_are_404_and_never_forwarded(self):
        server = self.start_proxy()
        status, _, body = self.call(server, "GET", "/_proxy/config")
        self.assertEqual((status, json.loads(body)), (404, {"error": "not_found"}))
        self.assertEqual(self.call(server, "POST", "/_proxy/health", body=b"{}")[0], 404)
        self.assertEqual(self.upstream_seen(), [])

    def test_unreachable_upstream_is_a_502(self):
        server = self.start_proxy(upstream=f"http://127.0.0.1:{free_port()}")
        for method, path in (("GET", "/front/central.php"), ("POST", "/apirest.php/Computer")):
            status, _, body = self.call(server, method, path, body=b"{}" if method == "POST" else None)
            self.assertEqual((status, json.loads(body)), (502, {"error": "upstream_unreachable"}), path)

    def test_upstream_timeout_is_a_504(self):
        server = self.start_proxy(upstream_timeout=0.3)
        status, _, body = self.call(server, "GET", "/slow")
        self.assertEqual((status, json.loads(body)), (504, {"error": "upstream_timeout"}))

    def test_bad_upstream_url_is_refused(self):
        with self.assertRaises(proxy.ConfigError):
            proxy.ProxyServer(("127.0.0.1", 0), proxy.Config(), "glpi:80")


class DecisionTests(unittest.TestCase):
    def test_draws_follow_a_fixed_order(self):
        base = {"delay_ms_min": 100, "delay_ms_max": 900}
        cfg = proxy.Config({"seed": "s", "faults": {**base, "error_502_rate": 0.3, "drop_write_rate": 0.4,
                                                    "delay_rate": 0.2, "delay_rate_lists": 0.6}})
        delay_only = proxy.Config({"seed": "s", "faults": {**base, "error_502_rate": 0, "drop_write_rate": 0,
                                                           "delay_rate": 1, "delay_rate_lists": 1}})
        for n in range(1, 300):
            rng = random.Random(f"s:{n}")
            u1, u2, u3, u4 = (rng.random() for _ in range(4))
            ms = 100 + int(u4 * 801)
            for method, lst in (("GET", True), ("GET", False), ("POST", False), ("DELETE", False), ("HEAD", False)):
                if u1 < 0.3:
                    want = ("injected_502", 0)
                elif method in ("POST", "DELETE") and u2 < 0.4:
                    want = ("dropped_write", 0)
                elif u3 < (0.6 if lst else 0.2):
                    want = ("delayed", ms)
                else:
                    want = (None, 0)
                self.assertEqual(proxy.decide(cfg, n, method, lst, False), want, (n, method, lst))
                self.assertEqual(proxy.decide(cfg, n, method, lst, True), (None, 0), "exempt calls never fault")
                # the delay length comes from u4 whether or not an earlier decision fired
                self.assertEqual(proxy.decide(delay_only, n, method, lst, False), ("delayed", ms))
                self.assertTrue(100 <= ms <= 900)

    def test_default_seed_golden_sequence(self):
        cfg = proxy.Config()
        got = [(n,) + proxy.decide(cfg, n, "GET", True, False) for n in range(1, 61)]
        self.assertEqual([g for g in got if g[1]], GOLDEN)

    def test_different_seeds_differ(self):
        seqs = [[proxy.decide(proxy.Config({"seed": s, "faults": {"error_502_rate": 0.5}}), n, "GET", False, False)
                 for n in range(1, 101)] for s in ("a", "b")]
        self.assertNotEqual(seqs[0], seqs[1])

    def test_path_classification(self):
        for p in ("/apirest.php", "/apirest.php/initSession", "/apirest.php/search/Computer", "/api.php/v2/Assets"):
            self.assertTrue(proxy.is_api_path(p), p)
        for p in ("/", "/front/central.php", "/index.php", "/_proxy/health", "/apirest/x", "/plugins/api.php"):
            self.assertFalse(proxy.is_api_path(p), p)
        for p in ("/apirest.php/search/Computer", "/apirest.php/Computer", "/apirest.php/Computer/",
                  "/api.php/v2/Assets/Computer", "/apirest.php/getMyEntities"):
            self.assertTrue(proxy.is_list_read("GET", p), p)
        for m, p in (("GET", "/apirest.php/Computer/12"), ("GET", "/apirest.php/Computer/12/Infocom"),
                     ("POST", "/apirest.php/Computer"), ("GET", "/api.php/v2/Assets/Computer/7"),
                     ("DELETE", "/apirest.php/Computer"), ("HEAD", "/apirest.php/Computer")):
            self.assertFalse(proxy.is_list_read(m, p), (m, p))
        self.assertTrue(proxy.is_session_endpoint("/apirest.php/initSession"))
        self.assertTrue(proxy.is_session_endpoint("/apirest.php/killSession"))
        self.assertFalse(proxy.is_session_endpoint("/apirest.php/Session"))

    def test_session_token_parsing_handles_encodings_and_junk(self):
        token = "abcdef0123456789"
        plain = json.dumps({"session_token": token}).encode()
        self.assertEqual(proxy.session_token_from([], plain), token)
        self.assertEqual(proxy.session_token_from([("Content-Encoding", "gzip")], gzip.compress(plain)), token)
        for headers, data in (([], b"not json"), ([("Content-Encoding", "gzip")], b"\x1f\x8b broken"),
                              ([], b'["list"]'), ([], b'{"session_token": ""}'), ([], b'{"session_token": 5}')):
            self.assertIsNone(proxy.session_token_from(headers, data), data)


class FaultInjectionTests(ProxyCase):
    SCRIPT = [("POST", "/apirest.php/initSession"), ("GET", "/apirest.php/Computer"),
              ("POST", "/apirest.php/Computer"), ("GET", "/apirest.php/Computer/12"),
              ("DELETE", "/apirest.php/Computer"), ("GET", "/apirest.php/search/Computer?x=1"),
              ("PUT", "/apirest.php/Computer/3")] * 6

    def test_same_seed_same_sequence_across_instances_and_matches_decide(self):
        faults = {"error_502_rate": 0.3, "drop_write_rate": 0.3, "delay_rate": 0.5, "delay_rate_lists": 0.5,
                  "delay_ms_min": 1, "delay_ms_max": 3}

        def run(seed):
            server = self.start_proxy(faults, seed=seed)
            out = []
            for method, path in self.SCRIPT:
                status, _, body = self.call(server, method, path, body=None if method == "GET" else b"{}")
                out.append((status, json.loads(body).get("error") if status >= 500 else None))
            return out

        first = run("alpha")
        self.assertEqual(first, run("alpha"))
        self.assertNotEqual(first, run("beta"))
        self.assertIn((502, "injected_502"), first)
        self.assertIn((504, "injected_timeout"), first)
        cfg = proxy.Config({"seed": "alpha", "faults": {**QUIET, **faults}})
        expected = []
        for n, (method, path) in enumerate(self.SCRIPT, 1):
            path = path.partition("?")[0]
            name, _ = proxy.decide(cfg, n, method, proxy.is_list_read(method, path), proxy.is_session_endpoint(path))
            clean = (201, None) if method == "POST" and "initSession" not in path else (200, None)
            expected.append({"injected_502": (502, "injected_502"), "dropped_write": (504, "injected_timeout")}
                            .get(name, clean))
        self.assertEqual(first, expected)

    def test_injected_502_is_not_forwarded_but_sessions_are(self):
        server = self.start_proxy({"error_502_rate": 1.0})
        status, headers, body = self.call(server, "GET", "/apirest.php/Computer")
        self.assertEqual((status, json.loads(body), lower(headers)["content-type"]),
                         (502, {"error": "injected_502"}, "application/json"))
        status, _, body = self.call(server, "POST", "/apirest.php/initSession",
                                    headers={"Authorization": "user_token abc", "App-Token": "app"})
        self.assertEqual(status, 200)
        token = json.loads(body)["session_token"]
        self.assertEqual(self.call(server, "GET", "/apirest.php/killSession", headers={"Session-Token": token})[0], 200)
        self.assertEqual(self.upstream_seen(), [("POST", "/apirest.php/initSession"), ("GET", "/apirest.php/killSession")])
        self.assertEqual(self.health(server)["api_calls"], 3)
        lines = self.log_lines()
        self.assertEqual(len(lines), 2)
        self.assertRegex(lines[1], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z call=1 fault=injected_502 "
                                   r"method=GET path=/apirest\.php/Computer status=502$")

    def test_dropped_write_is_committed_upstream_then_504(self):
        server = self.start_proxy({"drop_write_rate": 1.0})
        payload = b'{"input":{"name":"t"}}'
        status, _, body = self.call(server, "POST", "/apirest.php/Ticket", body=payload,
                                    headers={"Content-Type": "application/json"})
        self.assertEqual((status, json.loads(body)), (504, {"error": "injected_timeout"}))
        self.assertEqual(self.stub.requests[-1]["body"], payload, "GLPI received and committed the write")
        for method in ("PUT", "PATCH", "DELETE"):
            self.assertEqual(self.call(server, method, "/apirest.php/Ticket/5", body=b"{}")[0], 504, method)
        status, _, body = self.call(server, "GET", "/apirest.php/Ticket")
        self.assertEqual((status, json.loads(body)), (200, LISTING), "reads are never dropped")
        self.assertEqual(len(self.upstream_seen()), 5)
        lines = self.log_lines()
        self.assertEqual([line.split()[2] for line in lines[1:]], ["fault=dropped_write"] * 4)
        self.assertIn("call=1 fault=dropped_write method=POST path=/apirest.php/Ticket status=504", lines[1])

    def test_delay_applies_and_list_reads_use_their_own_rate(self):
        server = self.start_proxy({"delay_rate_lists": 1.0, "delay_ms_min": 60, "delay_ms_max": 60})
        started = time.monotonic()
        status, _, body = self.call(server, "GET", "/apirest.php/search/Computer?criteria[0][value]=x")
        self.assertEqual((status, json.loads(body)), (200, LISTING))
        self.assertGreaterEqual(time.monotonic() - started, 0.06)
        self.call(server, "GET", "/apirest.php/Computer/12")   # item read: delay_rate is 0
        self.call(server, "GET", "/apirest.php/Computer")      # collection without an id: list
        lines = self.log_lines()
        self.assertEqual([line.split()[1:3] for line in lines[1:]],
                         [["call=1", "fault=delayed_60"], ["call=3", "fault=delayed_60"]])
        self.assertIn("method=GET path=/apirest.php/search/Computer status=200", lines[1])
        self.assertNotIn("criteria", "\n".join(lines))
        other_log = os.path.join(self.tmp.name, "second.log")
        server2 = self.start_proxy({"delay_rate": 1.0, "delay_ms_min": 20, "delay_ms_max": 40}, log_path=other_log)
        self.call(server2, "GET", "/apirest.php/Computer")     # list read: delay_rate_lists is 0
        self.call(server2, "GET", "/apirest.php/Computer/12")  # item read: delayed
        faults = [line.split()[1:3] for line in self.log_lines(other_log)[1:]]
        self.assertEqual(len(faults), 1)
        self.assertEqual(faults[0][0], "call=2")
        self.assertTrue(20 <= int(faults[0][1].rsplit("_", 1)[1]) <= 40, faults)

    def test_concurrent_calls_are_counted_and_logged_exactly_once(self):
        server = self.start_proxy({"error_502_rate": 1.0})
        statuses, lock = [], threading.Lock()

        def worker():
            for _ in range(5):
                status = self.call(server, "GET", "/apirest.php/Computer")[0]
                with lock:
                    statuses.append(status)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(statuses, [502] * 50)
        self.assertEqual(self.health(server)["api_calls"], 50)
        self.assertEqual(sorted(int(line.split()[1][5:]) for line in self.log_lines()[1:]), list(range(1, 51)))
        self.assertEqual(self.upstream_seen(), [])


class SessionExpiryTests(ProxyCase):
    def open_session(self, server):
        status, _, body = self.call(server, "POST", "/apirest.php/initSession",
                                    headers={"Authorization": "user_token t", "App-Token": "a"})
        self.assertEqual(status, 200)
        return json.loads(body)["session_token"]

    def get(self, server, token, path="/apirest.php/Location"):
        return self.call(server, "GET", path, headers={"Session-Token": token})

    def test_oldest_session_expires_every_n_calls(self):
        server = self.start_proxy({"session_expiry_every_n_calls": 5})
        a = self.open_session(server)                              # call 1
        b = self.open_session(server)                              # call 2
        for _ in range(3):                                         # calls 3-5; call 5 is still served
            self.assertEqual(self.get(server, a)[::2], (200, json.dumps(LISTING).encode()))
        forwarded = len(self.upstream_seen())
        status, headers, body = self.get(server, a)                # call 6: A expired, answered empty
        self.assertEqual((status, body, lower(headers)["content-type"]), (200, b"[]", "application/json"))
        status, _, body = self.call(server, "POST", "/apirest.php/Location", headers={"Session-Token": a}, body=b"{}")
        self.assertEqual((status, json.loads(body)), (401, {"error": "ERROR_SESSION_TOKEN_INVALID"}))  # call 7
        self.assertEqual(self.call(server, "PUT", "/apirest.php/Location/3", headers={"Session-Token": a},
                                   body=b"{}")[0], 401)            # call 8
        self.assertEqual(len(self.upstream_seen()), forwarded, "calls 6-8 never reached GLPI")
        self.assertEqual(json.loads(self.get(server, b)[2]), LISTING)   # call 9: B is live
        status = self.call(server, "GET", "/apirest.php/killSession", headers={"Session-Token": a})[0]
        self.assertEqual((status, self.upstream_seen()[-1]), (200, ("GET", "/apirest.php/killSession")))  # call 10
        self.assertEqual(json.loads(self.get(server, a)[2]), LISTING)   # call 11: A forgotten, so unknown
        self.assertEqual(self.get(server, b)[2], b"[]")                  # call 12: B expired after call 10
        c = self.open_session(server)                                    # call 13
        self.assertEqual(json.loads(self.get(server, c)[2]), LISTING)   # call 14: fresh token is live
        lines = self.log_lines()
        self.assertEqual([line.split()[1:3] for line in lines[1:]], [
            ["call=5", f"fault=session_expired_{a[-4:]}"], ["call=6", "fault=expired_session_empty_200"],
            ["call=7", "fault=expired_session_401"], ["call=8", "fault=expired_session_401"],
            ["call=10", f"fault=session_expired_{b[-4:]}"], ["call=12", "fault=expired_session_empty_200"]])
        self.assertTrue(lines[1].endswith("method=GET path=/apirest.php/Location status=-"), lines[1])
        self.assertTrue(lines[2].endswith("method=GET path=/apirest.php/Location status=200"), lines[2])
        self.assertTrue(lines[3].endswith("method=POST path=/apirest.php/Location status=401"), lines[3])
        for token in (a, b, c):
            self.assertNotIn(token, "\n".join(lines))
        self.assertEqual(self.health(server)["api_calls"], 14)

    def test_tokens_unknown_to_the_proxy_pass_through(self):
        server = self.start_proxy({"session_expiry_every_n_calls": 1})
        for _ in range(3):
            self.assertEqual(json.loads(self.get(server, "issued-before-the-proxy-started")[2]), LISTING)
        self.assertEqual(self.log_lines()[1:], [], "nothing live, nothing expired, nothing logged")

    def test_killsession_forgets_a_live_token_before_the_expiry_picks(self):
        server = self.start_proxy({"session_expiry_every_n_calls": 3})
        a = self.open_session(server)                                            # call 1
        b = self.open_session(server)                                            # call 2
        self.call(server, "GET", "/apirest.php/killSession", headers={"Session-Token": a})  # call 3
        self.assertEqual(self.get(server, b)[2], b"[]", "B was the oldest live token left")  # call 4
        self.assertEqual(json.loads(self.get(server, a)[2]), LISTING)                       # call 5
        self.assertEqual(self.log_lines()[1].split()[2], f"fault=session_expired_{b[-4:]}")

    def test_token_in_the_query_string_counts_too(self):
        server = self.start_proxy({"session_expiry_every_n_calls": 2})
        a = self.open_session(server)                                        # call 1
        self.open_session(server)                                            # call 2: then A expires
        status, _, body = self.call(server, "GET", f"/apirest.php/Location?session_token={a}&range=0-9")
        self.assertEqual((status, body), (200, b"[]"))                       # call 3: no header, query token
        self.assertEqual(self.call(server, "DELETE", f"/apirest.php/Location/4?session_token={a}", body=b"{}")[0], 401)
        self.assertEqual(self.call(server, "GET", f"/apirest.php/killSession?session_token={a}")[0], 200)  # forgets A
        self.assertEqual(json.loads(self.call(server, "GET", f"/apirest.php/Location?session_token={a}")[2]), LISTING)
        text = "\n".join(self.log_lines())
        self.assertIn("call=3 fault=expired_session_empty_200 method=GET path=/apirest.php/Location status=200", text)
        self.assertIn("call=4 fault=expired_session_401 method=DELETE path=/apirest.php/Location/4 status=401", text)
        self.assertNotIn(a, text)
        self.assertNotIn("session_token", text)

    def test_expired_answer_wins_over_random_faults(self):
        server = self.start_proxy({"session_expiry_every_n_calls": 2, "error_502_rate": 1.0})
        a = self.open_session(server)                 # call 1
        self.open_session(server)                     # call 2: then A expires
        self.assertEqual(self.get(server, a)[::2], (200, b"[]"))  # call 3: expired beats the 502
        self.assertEqual(self.log_lines()[-1].split()[2], "fault=expired_session_empty_200")


class LoggingAndConfigTests(ProxyCase):
    def test_log_directory_is_created_and_startup_line_has_rates(self):
        path = os.path.join(self.tmp.name, "deep", "er", "faults.log")
        self.start_proxy({"error_502_rate": 0.25}, seed="log-seed", log_path=path)
        line = self.log_lines(path)[0]
        self.assertIn("call=0 fault=startup seed_fingerprint=" + hashlib.sha256(b"log-seed").hexdigest()[:8], line)
        for part in ("error_502_rate=0.25", "drop_write_rate=0.0", "delay_rate=0.0", "delay_rate_lists=0.0",
                     "delay_ms=1500-6000", "session_expiry_every_n_calls=1000"):
            self.assertIn(part, line)
        self.assertNotIn("log-seed", line)

    def test_log_off_writes_nothing(self):
        server = self.start_proxy({"error_502_rate": 1.0}, log_faults=False)
        self.assertEqual(self.call(server, "GET", "/apirest.php/Computer")[0], 502)
        self.assertFalse(os.path.exists(self.log_path))

    def test_unwritable_log_falls_back_to_stderr_and_keeps_serving(self):
        blocker = os.path.join(self.tmp.name, "blocker")
        open(blocker, "w").close()
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            server = self.start_proxy({"error_502_rate": 1.0}, log_path=os.path.join(blocker, "faults.log"))
            status = self.call(server, "GET", "/apirest.php/Computer")[0]
        self.assertEqual(status, 502)
        self.assertIn("cannot write", err.getvalue())
        self.assertIn("call=1 fault=injected_502 method=GET path=/apirest.php/Computer status=502", err.getvalue())

    def test_nothing_secret_reaches_the_log(self):
        server = self.start_proxy({"error_502_rate": 1.0})
        self.call(server, "POST", "/apirest.php/Computer?session_token=QUERYTOKEN&x=1", body=b'{"name": "BODYVALUE"}',
                  headers={"Session-Token": "HEADERTOKEN", "Cookie": "glpi_sid=COOKIEVALUE", "App-Token": "APPTOKEN"})
        text = "\n".join(self.log_lines())
        self.assertIn("method=POST path=/apirest.php/Computer status=502", text)
        for secret in ("QUERYTOKEN", "HEADERTOKEN", "COOKIEVALUE", "APPTOKEN", "BODYVALUE", "?"):
            self.assertNotIn(secret, text)

    def test_config_defaults(self):
        cfg = proxy.Config({"seed": "x"})
        self.assertEqual((cfg.delay_rate, cfg.delay_rate_lists, cfg.delay_ms_min, cfg.delay_ms_max), (0.10, 0.35, 1500, 6000))
        self.assertEqual((cfg.error_502_rate, cfg.drop_write_rate, cfg.session_expiry_every_n_calls), (0.04, 0.06, 120))
        self.assertEqual((cfg.log_faults, cfg.log_path), (True, "/app/logs/faults.log"))
        self.assertEqual(proxy.Config().seed, "candidate-2026")
        self.assertEqual(proxy.Config(None).fingerprint, hashlib.sha256(b"candidate-2026").hexdigest()[:8])
        self.assertEqual(proxy.Config({"seed": 2026}).seed, "2026")

    def test_config_validation_fails_fast_with_a_message(self):
        cases = {
            "faults.delay_rate must be a number between 0 and 1": {"faults": {"delay_rate": 1.5}},
            "faults.error_502_rate must be a number between 0 and 1": {"faults": {"error_502_rate": -0.1}},
            "faults.drop_write_rate must be a number": {"faults": {"drop_write_rate": "0.1"}},
            "faults.delay_rate_lists must be a number": {"faults": {"delay_rate_lists": True}},
            "faults.session_expiry_every_n_calls must be an integer >= 1": {"faults": {"session_expiry_every_n_calls": 0}},
            "faults.session_expiry_every_n_calls must be an integer": {"faults": {"session_expiry_every_n_calls": 2.5}},
            "faults.delay_ms_max must be >= faults.delay_ms_min": {"faults": {"delay_ms_min": 100, "delay_ms_max": 50}},
            "faults.delay_ms_min must be an integer >= 0": {"faults": {"delay_ms_min": -1}},
            "unknown key(s): faults.delay_rates": {"faults": {"delay_rates": 0.1}},
            "unknown key(s): seeds": {"seeds": "x"},
            "seed must be a non-empty string": {"seed": ""},
            "log_faults must be true or false": {"log_faults": "yes"},
            "log_path must be a non-empty string": {"log_path": ""},
            "'faults' must be a mapping": {"faults": [1]},
            "must be a YAML mapping": "just a string",
        }
        for message, mapping in cases.items():
            with self.assertRaises(proxy.ConfigError, msg=repr(mapping)) as ctx:
                proxy.Config(mapping)
            self.assertIn(message, str(ctx.exception))

    def test_config_file_loads_with_partial_keys_and_the_shipped_file_matches_the_brief(self):
        path = os.path.join(self.tmp.name, "config.yml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('seed: "file-seed"\nfaults:\n  error_502_rate: 0.5\n  session_expiry_every_n_calls: 7\n')
        cfg = proxy.load_config(path)
        self.assertEqual((cfg.seed, cfg.error_502_rate, cfg.session_expiry_every_n_calls, cfg.delay_rate, cfg.log_faults),
                         ("file-seed", 0.5, 7, 0.10, True))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# comments only\n")
        self.assertEqual(proxy.load_config(path).seed, "candidate-2026")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("faults:\n  delay_rate: 2\n")
        with self.assertRaises(proxy.ConfigError) as ctx:
            proxy.load_config(path)
        self.assertIn("faults.delay_rate", str(ctx.exception))
        with self.assertRaises(proxy.ConfigError) as ctx:
            proxy.load_config(os.path.join(self.tmp.name, "missing.yml"))
        self.assertIn("cannot read", str(ctx.exception))
        shipped = proxy.load_config(os.path.join(HERE, "config.yml"))
        self.assertEqual((shipped.seed, shipped.log_faults, shipped.log_path), ("candidate-2026", True, "/app/logs/faults.log"))
        self.assertEqual((shipped.error_502_rate, shipped.drop_write_rate, shipped.delay_rate, shipped.delay_rate_lists),
                         (0.04, 0.06, 0.10, 0.35))
        self.assertEqual((shipped.delay_ms_min, shipped.delay_ms_max, shipped.session_expiry_every_n_calls), (1500, 6000, 120))


if __name__ == "__main__":
    unittest.main(verbosity=2)

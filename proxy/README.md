# Fault proxy

The only way into GLPI in this pack. `docker compose up` publishes it on
http://localhost:8080; GLPI's own port is not published. For the UI it is a plain
reverse proxy. For GLPI's REST APIs it is a deterministic fault injector.

**Candidates: do not modify, bypass or disable the proxy or its config.** Your
submission is run with a seed you have not seen and with the fault log off. A proxy
that was changed, bypassed or had its rates altered ends the review.

## What it does

- Forwards every request to `UPSTREAM` (default `http://glpi:80`): method, path,
  query, body and headers, minus hop-by-hop headers. `Host` stays as the client sent
  it; `X-Forwarded-For`, `X-Forwarded-Proto` and `X-Forwarded-Host` are added. The
  upstream status, headers and body come back unchanged, byte for byte (gzip, cookies,
  redirects and binary assets survive); redirects are not followed. GLPI unreachable:
  `502 {"error":"upstream_unreachable"}`. No answer within 120 s: `504 {"error":"upstream_timeout"}`.
- An **API call** is a request whose path starts with `/apirest.php` or `/api.php`.
  Only API calls are counted and faulted; everything else passes through clean.
- API call number `n` is judged by `random.Random(f"{seed}:{n}")`, which draws four
  numbers in a fixed order (502, dropped write, delay, delay length). The same seed and
  the same sequence of API calls always produce the same faults, whatever the timing or
  the number of clients. At most one fault per call, decided in this order:
  1. `injected_502`: answered `502 {"error":"injected_502"}`, not forwarded.
  2. `dropped_write` (POST, PUT, PATCH, DELETE only): forwarded, GLPI's answer waited
     for and discarded, the client gets `504 {"error":"injected_timeout"}`. The write
     is committed in GLPI.
  3. `delayed_<ms>`: sleeps a uniform number of milliseconds in
     `[delay_ms_min, delay_ms_max]`, then forwards normally. List reads (a GET to
     `/search/...`, or a GET whose path has no numeric id, such as `/apirest.php/Computer`)
     use `delay_rate_lists`; every other call uses `delay_rate`.
- `initSession` and `killSession` requests are never faulted, but they count.
- **Session expiry.** The proxy reads the `Session-Token` header (or the `session_token`
  query parameter GLPI also accepts) of every API call and remembers, as a SHA-256 hash, every token an `initSession` reply hands out. After every
  `session_expiry_every_n_calls` API calls the oldest live token is expired. A request
  carrying an expired token is answered by the proxy and never reaches GLPI: a GET gets
  `200` with body `[]` (the product answers empty instead of erroring); any other method
  gets `401 {"error":"ERROR_SESSION_TOKEN_INVALID"}`. A `killSession` forgets its token,
  a new `initSession` always yields a fresh live token, and tokens the proxy never saw
  issued pass through untouched.
- `GET /_proxy/health` is served by the proxy itself:
  `{"ok":true,"seed_fingerprint":"<first 8 hex of sha256(seed)>","api_calls":<count>}`.
  The fingerprint lets a reviewer confirm which seed is loaded; the seed is never exposed.

## Config: `config.yml`

Read once at startup from `/app/config.yml` (env `PROXY_CONFIG` overrides the path);
restart the container after a change. Missing keys take the defaults below. A rate
outside 0..1, a non-positive count, a bad type or an unknown key stops the proxy at
startup with a message naming the key.

| Key | Default | Meaning |
|---|---|---|
| `seed` | `"candidate-2026"` | Seeds the fault schedule. Keep it quoted. |
| `log_faults` | `true` | Append one line per fault to `log_path`. |
| `log_path` | `/app/logs/faults.log` | The directory is created if missing; if the file cannot be written the lines go to stderr and the proxy keeps serving. |
| `faults.delay_rate` | `0.10` | Share of API calls delayed. |
| `faults.delay_rate_lists` | `0.35` | Share of API list reads delayed. |
| `faults.delay_ms_min`, `faults.delay_ms_max` | `1500`, `6000` | Bounds of one delay, in milliseconds. |
| `faults.error_502_rate` | `0.04` | Share answered 502 without being forwarded. |
| `faults.drop_write_rate` | `0.06` | Share of writes committed upstream but answered 504. |
| `faults.session_expiry_every_n_calls` | `120` | Expire the oldest live session after every N API calls. |

Environment: `UPSTREAM` (default `http://glpi:80`), `PROXY_LISTEN_PORT` (default
`8080`), `PROXY_CONFIG` (default `/app/config.yml`).

## Log format

One line per injected fault or expired-token answer, plus one line at startup:

```
2026-10-30T17:02:11.482Z call=0 fault=startup seed_fingerprint=4c388ec9 error_502_rate=0.04 drop_write_rate=0.06 delay_rate=0.1 delay_rate_lists=0.35 delay_ms=1500-6000 session_expiry_every_n_calls=120
2026-10-30T17:03:40.118Z call=3 fault=delayed_3393 method=GET path=/apirest.php/search/Location status=200
2026-10-30T17:05:52.004Z call=19 fault=injected_502 method=GET path=/apirest.php/Location status=502
2026-10-30T17:09:12.900Z call=120 fault=session_expired_7f3a method=GET path=/apirest.php/Computer status=-
2026-10-30T17:09:13.215Z call=121 fault=expired_session_empty_200 method=GET path=/apirest.php/Location status=200
```

Fields, space separated: UTC time (ISO-8601), the API call number, the fault
(`injected_502`, `dropped_write`, `delayed_<ms>`, `expired_session_empty_200`,
`expired_session_401`, `session_expired_<last 4 chars of the token>`), the method, the
path with the query string removed, and the status the client was served. A
`session_expired_` line marks the moment of expiry, not an answer, so its status is `-`
and its method and path are those of the call that triggered it. Tokens, cookies, bodies
and query strings are never logged.

## Running it

In the pack, `docker compose up` builds this directory and mounts `config.yml` and the
log directory. Standalone:

```
UPSTREAM=http://127.0.0.1:8081 PROXY_CONFIG=./config.yml PROXY_LISTEN_PORT=8080 python3 proxy.py
```

## Tests

```
python3 -m unittest -v test_proxy
```

Standard library only. The suite starts a stub GLPI and the proxy in-process on free
ports and covers pass-through fidelity, the health endpoint, determinism (two proxies
with the same seed, the pure decision function against a hand-derived draw, a golden
sequence for the default seed), each fault class, the session-expiry schedule, logging
and config validation. The last lines of the output show the executed count and the verdict.

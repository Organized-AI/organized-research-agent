# Prototype SOP index

## Real-data pilot — `collect.py`, `analysis.py`, `api.py`, `index.html`

**Status: in progress.**

| Field | Guidance |
| --- | --- |
| Symptom | A public endpoint returns challenge/block HTML, a 403, or no relevant results. |
| Cause / evidence | Public availability differs by platform and query; do not bypass authentication, challenges, or access controls. |
| Safe approach | Record the access limitation, try another legitimate public source, and report the actual coverage. Do not turn fixtures into live records or pad platform counts. |
| Regression check | Validate provenance, normalized record shape, vector finiteness and tenant filtering before exposing any collected record through the API. |

For a concrete public URL, an optional Camoufox normal anonymous render may establish whether its post body is actually visible. Never proceed from a `js_challenge`, CAPTCHA, login wall, or redirected challenge URL; record the rendered outcome and move to a different public source. Camoufox browser assets stay in the local user cache.

## Prototype interaction checks — `index.html`

**Status: confirmed (local test).**

| Field | Guidance |
| --- | --- |
| Symptom | A route or selected 3D pipeline stage appears blank or non-responsive. |
| Cause / evidence | The single-page local view reads `/api/summary`, `/api/evidence`, and, after embedding, `/api/search`. A stale vector file can make semantic search unavailable. |
| Safe approach | Keep labels tied to actual API denominators. Run `analysis.py` after a collection before calling search; never inject simulated evidence. |
| Regression check | Run the Python test suite, start `api.py`, and inspect the local ledger plus a semantic query. |

## Composition and API fixture — `partials/header.html`, `nginx.conf`, `api.py`

**Status: partially confirmed (local API + SSI-compatible preview; Nginx runtime untested).**

| Field | Guidance |
| --- | --- |
| Symptom | Shared header drift or a demo endpoint appears to offer multi-tenant persistence. |
| Cause / evidence | FastAPI reads ignored local evidence/vector files and rejects non-demo tenants. Nginx is not installed in this environment. |
| Safe approach | Bind FastAPI to loopback and keep raw evidence/vector files local. Treat multi-worker persistence as future work. |
| Regression check | Run the Python suite, request `/api/health`, and run `nginx -t -c nginx.conf` only in an environment with Nginx. |

## Local proxy harness — `local-proxy-test.mjs`

**Status: confirmed (local test).**

| Field | Guidance |
| --- | --- |
| Symptom | A provider adapter needs proxy semantics, but this prototype must never become an open relay. |
| Cause / evidence | The harness accepts only `127.0.0.1` upstreams, rejects CONNECT, caps response bytes, and runs a bounded queued workload. It is a test relay, not Squid and not a production collector. |
| Safe approach | Keep both servers loopback-bound and only target the owned fixture upstream. Preserve allowlisting, byte limit, queue limit, timeout, and graceful-close behavior. Do not add external hosts or credentials. |
| Regression check | Run `.venv/bin/python -m unittest discover -s tests -v`; it must show a forwarded fixture request, oversized-response rejection, blocked external host, queue saturation, and clean shutdown. |

## Untested concerns

- Worker pods, Cloudflare orchestration, distributed leases/idempotency, and real collection browser behavior are future architecture concerns. This deck does not validate them.
- Local process metrics under the bounded test are not evidence of deployment memory limits, real-load stability, or zero OOM risk. Use profiling and bounded object lifetimes before any worker implementation.

## Local static preview — `local-server.mjs`

**Status: confirmed (local test).**

| Field | Guidance |
| --- | --- |
| Symptom | A missing preview asset can trigger a headers-already-sent failure instead of a clean 404. |
| Cause / evidence | The server must await file contents before issuing a 200 response; otherwise an asynchronous read failure reaches the 404 handler after headers are committed. |
| Safe approach | Read the requested file first, then issue the response header. Keep decoded-path containment based on `relative()` rather than a prefix match. |
| Regression check | Run `npm test`, then request `/does-not-exist` and verify an HTTP 404 without server crash. |

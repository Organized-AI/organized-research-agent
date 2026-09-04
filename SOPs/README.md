# Prototype SOP index

## Prototype interaction checks — `index.html`, `styles.css`, `app.js`

**Status: confirmed (local test).**

| Field | Guidance |
| --- | --- |
| Symptom | A route or selected 3D pipeline stage appears blank or non-responsive. |
| Cause / evidence | The deck uses a small client-side view router and seeded fixture shapes in `app.js`; malformed fixture data or a mismatched section id can leave a view empty. |
| Safe approach | Preserve matching `data-view`/section IDs and `data-stage`/fixture keys. Keep public sources clearly illustrative and retain the simulated labels. |
| Regression check | Run `npm test`, then inspect Workspace, Evidence, Opportunities, and Reports in the browser. Click a stage and one evidence row; stage controls must remain semantic buttons with an updated `aria-pressed` state. |

## Composition and API fixture — `partials/header.html`, `nginx.conf`, `api.py`

**Status: partially confirmed (local API + SSI-compatible preview; Nginx runtime untested).**

| Field | Guidance |
| --- | --- |
| Symptom | Shared header drift or a demo endpoint appears to offer multi-tenant persistence. |
| Cause / evidence | The header is a small SSI fragment. The standard-library API writes only fictional workspace metadata into a local SQLite control-plane database and rejects non-demo tenants. Nginx is not installed in this environment. |
| Safe approach | Keep SSI fragments presentation-only. Keep SQLite local to one API/control-plane instance—never mount one SQLite file across pods. Treat worker queues/shards as a later, separately coordinated runtime. |
| Regression check | Run `npm test`, request `/health` from `npm run api`, and run `nginx -t -c nginx.conf` only in an environment with Nginx. |

## Local proxy harness — `local-proxy-test.mjs`

**Status: confirmed (local test).**

| Field | Guidance |
| --- | --- |
| Symptom | A provider adapter needs proxy semantics, but this prototype must never become an open relay. |
| Cause / evidence | The harness accepts only `127.0.0.1` upstreams, rejects CONNECT, caps response bytes, and runs a bounded queued workload. It is a test relay, not Squid and not a production collector. |
| Safe approach | Keep both servers loopback-bound and only target the owned fixture upstream. Preserve allowlisting, byte limit, queue limit, timeout, and graceful-close behavior. Do not add external hosts or credentials. |
| Regression check | Run `npm test`; it must show a forwarded fixture request, oversized-response rejection, blocked external host, queue saturation, and clean shutdown. |

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

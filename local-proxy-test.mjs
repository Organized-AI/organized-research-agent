import http from 'node:http';
import assert from 'node:assert/strict';
import { performance } from 'node:perf_hooks';

const HOST = '127.0.0.1';
const MAX_BYTES = 32 * 1024;
const MAX_QUEUE = 3;
const state = { inFlight: 0, pending: 0, maxPending: 0 };
const listen = (server) => new Promise(resolve => server.listen(0, HOST, () => resolve(server.address().port)));
const close = (server) => new Promise(resolve => server.close(resolve));

function fixtureServer() {
  return http.createServer((req, res) => {
    if (req.url === '/fixture') return res.end(JSON.stringify({ source: 'owned-local-fixture', records: ['fictional'] }));
    if (req.url === '/oversized') return res.end('x'.repeat(MAX_BYTES + 1));
    setTimeout(() => res.end('slow-fixture'), 60);
  });
}

function proxyServer(upstreamPort) {
  return http.createServer((req, res) => {
    if (req.method === 'CONNECT') return res.writeHead(405).end('CONNECT disabled');
    const target = new URL(req.url);
    if (target.hostname !== HOST || Number(target.port) !== upstreamPort) return res.writeHead(403).end('local fixture upstream only');
    if (state.inFlight >= 1) {
      if (state.pending >= MAX_QUEUE) return res.writeHead(429).end('queue saturated');
      state.pending++; state.maxPending = Math.max(state.maxPending, state.pending);
      return setTimeout(() => { state.pending--; proxyRequest(target, res); }, 20);
    }
    proxyRequest(target, res);
  });
}

function proxyRequest(target, clientRes) {
  state.inFlight++;
  const upstream = http.request({ hostname: HOST, port: target.port, path: target.pathname, method: 'GET', timeout: 350 }, res => {
    let seen = 0; const chunks = [];
    res.on('data', chunk => { seen += chunk.length; if (seen > MAX_BYTES) { upstream.destroy(); if (!clientRes.headersSent) clientRes.writeHead(413); clientRes.end('response exceeds fixture limit'); } else chunks.push(chunk); });
    res.on('end', () => { if (!clientRes.writableEnded) clientRes.writeHead(res.statusCode ?? 502, { 'content-type': res.headers['content-type'] ?? 'text/plain' }).end(Buffer.concat(chunks)); });
  });
  upstream.on('timeout', () => upstream.destroy(new Error('upstream timeout')));
  upstream.on('error', () => { if (!clientRes.writableEnded) clientRes.writeHead(502).end('upstream error'); });
  clientRes.on('close', () => { state.inFlight = Math.max(0, state.inFlight - 1); });
  upstream.end();
}

function throughProxy(proxyPort, target) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: HOST, port: proxyPort, path: target, timeout: 600 }, res => { let body = ''; res.setEncoding('utf8'); res.on('data', d => body += d); res.on('end', () => resolve({ status: res.statusCode, body })); });
    req.on('error', reject); req.on('timeout', () => req.destroy(new Error('proxy timeout'))); req.end();
  });
}

export async function runProxyHarness() {
  const fixture = fixtureServer(); const upstreamPort = await listen(fixture); const proxy = proxyServer(upstreamPort); const proxyPort = await listen(proxy); const target = `http://${HOST}:${upstreamPort}`; const started = performance.now();
  try {
    const good = await throughProxy(proxyPort, `${target}/fixture`); assert.equal(good.status, 200); assert.match(good.body, /owned-local-fixture/);
    const tooBig = await throughProxy(proxyPort, `${target}/oversized`); assert.equal(tooBig.status, 413);
    const external = await throughProxy(proxyPort, 'http://example.com/'); assert.equal(external.status, 403);
    const saturation = await Promise.all(Array.from({ length: 5 }, () => throughProxy(proxyPort, `${target}/slow`))); assert.ok(saturation.some(r => r.status === 429)); assert.ok(state.maxPending <= MAX_QUEUE);
    return { upstreamPort, proxyPort, elapsedMs: Math.round(performance.now() - started), maxQueue: state.maxPending, rssMiB: Math.round(process.memoryUsage().rss / 1024 / 1024) };
  } finally { await close(proxy); await close(fixture); }
}

if (import.meta.url === `file://${process.argv[1]}`) runProxyHarness().then(result => console.log(JSON.stringify({ status: 'PASS', ...result }))).catch(error => { console.error(error); process.exitCode = 1; });

// Browser-pane frame transport benchmark (backlog 629d2205). NOT a pass/fail smoke:
// it measures, prints a table, and exits 0 unless the pane never rendered.
//
// REAL backend (tools/smoke/browser_pane_harness.py: the real browser_routes blueprint,
// real headless Chromium on the CDP pipe, real CDP reader) + the REAL static/js/browser-pane.js
// in a real Chromium client, looking at the SAME deterministic dense, constantly-scrolling
// page (/dense.html) on every run. Three numbers per transport, over a fixed window:
//
//   source fps   frames the server's CDP reader received (harness frame_seq delta) -- the
//                ceiling: what Chromium's screencast can produce on this page
//   delivered    <img> `load` events in the client = frames the user actually saw
//   wire         bytes on the stream request per delivered frame (CDP Network.dataReceived,
//                encodedDataLength: what crosses the tunnel, not the decoded size)
//
// RUN:  node tools/smoke/bench-browser-pane-fps.mjs [sse|bin|both] [seconds]
//       (needs tools/smoke/node_modules -- in a worktree, link the main checkout's)
// `sse` forces the legacy EventSource path through the pane's own `mc_bp_transport`
// localStorage switch; `bin` is the pane's default (binary stream, SSE fallback).
import { chromium } from 'playwright';
import { spawn } from 'child_process';
import { mkdtempSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import net from 'net';

const REPO = new URL('../../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const which = process.argv[2] || 'both';
const SECS = Number(process.argv[3] || 12);
const WARM_MS = 4000;
// BENCH_KBPS=1000 throttles the client's downlink to ~1 MB/s (what the 2026-08-26 live measurement
// saw through the tunnel) so the transport's bytes/frame show up as fps; unset = unthrottled localhost.
const KBPS = Number(process.env.BENCH_KBPS || 0);

const freePort = () => new Promise(res => {
  const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
});

const tmp = mkdtempSync(join(tmpdir(), 'bp-fps-bench-'));
const port = await freePort();
const harness = spawn(process.env.PYTHON || 'python', [join(REPO, 'tools/smoke/browser_pane_harness.py'), String(port), tmp],
  { cwd: REPO, stdio: ['pipe', 'pipe', 'pipe'] });
let out = '';
harness.stderr.on('data', d => { out += d; });
const pagePort = await new Promise((res, rej) => {
  const t = setTimeout(() => rej(new Error('harness did not start:\n' + out)), 30000);
  harness.stdout.on('data', d => { out += d; const m = /READY \d+ (\d+)/.exec(out); if (m) { clearTimeout(t); res(Number(m[1])); } });
  harness.on('exit', c => rej(new Error(`harness exited ${c}:\n${out}`)));
});
const BASE = `http://127.0.0.1:${port}`;
const PAGE = `http://127.0.0.1:${pagePort}/dense.html`;
const browser = await chromium.launch();

const pct = (a, p) => { const s = [...a].sort((x, y) => x - y); return s.length ? s[Math.min(s.length - 1, Math.floor(s.length * p))] : 0; };

async function run(transport) {
  const page = await browser.newPage({ viewport: { width: 1300, height: 900 } });
  await page.addInitScript(t => {
    try { if (t === 'sse') localStorage.setItem('mc_bp_transport', 'sse'); else localStorage.removeItem('mc_bp_transport'); } catch (e) {}
    window.__loads = [];
    // Newest frame seq the client has RECEIVED, on either transport (the pane's _bpStats.seq only
    // exists from the binary-transport change on, so the legacy EventSource is observed directly).
    const ES = window.EventSource;
    window.EventSource = function (u, o) {
      const e = new ES(u, o);
      e.addEventListener('message', ev => { const m = /^\{"seq": (\d+)/.exec(ev.data.slice(0, 40)); if (m) window.__lastSeq = +m[1]; });
      return e;
    };
    window.EventSource.prototype = ES.prototype;
    document.addEventListener('load', e => {
      if (e.target && e.target.matches && e.target.matches('#mc-browser-pane [data-bp="screen"]')) window.__loads.push(performance.now());
    }, true);
  }, transport);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Network.enable');
  if (KBPS) await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 40,
    downloadThroughput: KBPS * 1024, uploadThroughput: -1 });
  const streamReqs = new Set(); let wire = 0, decoded = 0, streamUrl = '';
  cdp.on('Network.requestWillBeSent', e => {
    if (/\/api\/browser\/(stream|frames)/.test(e.request.url)) { streamReqs.add(e.requestId); streamUrl = e.request.url.replace(/\?.*/, ''); }
  });
  cdp.on('Network.dataReceived', e => { if (streamReqs.has(e.requestId)) { wire += e.encodedDataLength; decoded += e.dataLength; } });

  await page.goto(BASE + '/');
  await page.waitForFunction(() => typeof window.openBrowserPane === 'function');
  await page.evaluate(() => window.openBrowserPane('about:blank', 'bench'));
  await page.waitForSelector('#mc-browser-pane');
  await page.waitForSelector('#mc-browser-pane [data-bp-tab]', { timeout: 20000 });   // the session is attached
  const url = page.locator('#mc-browser-pane [data-bp="url"]');
  await url.click(); await url.fill(PAGE); await url.press('Enter');
  await page.waitForTimeout(WARM_MS);

  const sid = await page.evaluate(() => fetch('/api/project/bench/browser/status').then(r => r.json())
    .then(d => d.sessions.find(s => s.status === 'running').session_id));
  const st = () => page.evaluate(sid => fetch('/_harness/session/' + sid).then(r => r.json()), sid);
  const s0 = await st(); const n0 = await page.evaluate(() => window.__loads.length);
  const w0 = wire, d0 = decoded; const t0 = Date.now();
  // Lag = how many source frames behind the server's newest the client's newest is, sampled twice a second.
  const lagSamples = [];
  for (let i = 0; i < SECS * 2; i++) {
    await page.waitForTimeout(500);
    const [srv, cli] = await Promise.all([st(), page.evaluate(() => (window._bpStats && window._bpStats.seq) || window.__lastSeq || 0)]);
    if (cli) lagSamples.push(srv.frame_seq - cli);
  }
  const s1 = await st(); const loads = await page.evaluate(() => window.__loads.slice());
  const secs = (Date.now() - t0) / 1000;
  const win = loads.slice(n0);
  const gaps = win.slice(1).map((t, i) => t - win[i]);
  const frames = win.length;
  const r = {
    transport, link: KBPS ? KBPS + ' KB/s' : 'localhost', fpsCap: process.env.MC_BENCH_CONFIG || 'default', stream: streamUrl.replace(/^.*\/api\//, '/api/'), secs: +secs.toFixed(1),
    sourceFps: +((s1.frame_seq - s0.frame_seq) / secs).toFixed(1),
    deliveredFps: +(frames / secs).toFixed(1),
    gapP50ms: +pct(gaps, 0.5).toFixed(0), gapP95ms: +pct(gaps, 0.95).toFixed(0),
    wireKBperFrame: frames ? +((wire - w0) / frames / 1024).toFixed(1) : 0,
    decodedKBperFrame: frames ? +((decoded - d0) / frames / 1024).toFixed(1) : 0,
    wireMBps: +((wire - w0) / secs / 1048576).toFixed(2),
    jpegB64KB: +(s1.frame_b64_len / 1024).toFixed(1),
    lagMsMedian: Math.round(pct(lagSamples, 0.5) / ((s1.frame_seq - s0.frame_seq) / secs) * 1000),
    lagMsMax: Math.round(Math.max(0, ...lagSamples) / ((s1.frame_seq - s0.frame_seq) / secs) * 1000),
  };
  await page.evaluate(sid => fetch('/api/browser/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sid }) }), sid);
  await page.close();
  return r;
}

const rows = [];
let code = 0;
try {
  for (const t of (which === 'both' ? ['sse', 'bin'] : [which])) rows.push(await run(t));
  console.table(rows);
  console.log(JSON.stringify(rows));
  if (rows.some(r => r.deliveredFps === 0)) { console.log('FAIL: a run delivered no frames'); code = 1; }
} catch (e) { console.log('bench failed:', e.stack || e); code = 1; }
finally {
  await browser.close();
  try { harness.stdin.end(); } catch (e) {}
  setTimeout(() => { try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {} process.exit(code); }, 1500);
}

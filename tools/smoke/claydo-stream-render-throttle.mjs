#!/usr/bin/env node
/**
 * Claydo streaming render-throttle smoke (2026-09-24 mobile lag report).
 *
 * WHY THIS EXISTS
 * ----------------
 * Ron's son: while Claydo is generating a reply, the whole app lags and the
 * Android hardware back button is slow. Root cause (read, not guessed):
 * submitClaydo()'s SSE `delta` handler used to run, on EVERY chunk:
 *   - _claydoParseMarkers(assembled)   — regex over the WHOLE reply so far
 *   - botMsg.innerHTML = _claydoFormatText(cleanText)  — full re-render
 *   - _claydoMountTeams(botMsg)        — subtree scan
 *   - histDiv.scrollTop = histDiv.scrollHeight  — forced reflow
 * all synchronously, once per streamed chunk, with no batching. A
 * multi-chunk-per-second reply turned that into a near-continuous run of
 * heavy main-thread work — nothing was ever a single long task, so nothing
 * (including a popstate handler for hardware back) got a turn between chunks.
 *
 * The fix batches every delta into at most one DOM render per animation
 * frame via a scheduled-flag + requestAnimationFrame (_scheduleDeltaRender
 * in claydo.js), regardless of how fast chunks arrive.
 *
 * This test drives a REAL local HTTP server (not Playwright route mocking —
 * that can't emit genuinely time-spaced chunks) that streams the Claydo SSE
 * shape in bursts (several deltas written back-to-back per burst, matching
 * how a real CLI stdout read hands the server multiple tokens in one buffer,
 * not evenly time-spaced), and counts how many times the bot message's DOM
 * subtree actually mutates (a MutationObserver on #claydo-history) during the
 * burst. Before the fix this equals the chunk count 1:1 (every delta is a
 * render, by direct inspection of the old code — see the diff / CHANGELOG);
 * after the fix it's bounded by animation frames elapsed, not chunk count. An
 * earlier version of this test paced chunks individually (2ms apart via
 * setInterval) — over a real socket that jitter alone landed close to one
 * frame per chunk, which made the batched-vs-unbatched render counts nearly
 * identical and the test couldn't tell the fix apart from a regression.
 *
 * Hermetic: a real ephemeral-port http.Server, the real static/index.html +
 * static/js/*.js + static/css/*.css, no external network.
 *
 * RUN   node tools/smoke/claydo-stream-render-throttle.mjs
 * Exit  0 = renders bounded well below chunk count; 1 = a regression.
 */
import http from 'node:http';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');

const JS = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) JS['/static/js/' + f] = readFileSync(resolve(JS_DIR, f));
const CSS = {};
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) CSS['/static/css/' + f] = readFileSync(resolve(CSS_DIR, f));

const N_CHUNKS = 250;        // simulated tokens in the reply
const BURST_SIZE = 15;       // chunks written back-to-back, no yield between them —
                              // mirrors how a real CLI stdout read hands the server
                              // several tokens in one buffer, not evenly time-spaced
const BURST_INTERVAL_MS = 40; // gap between bursts (well under this harness's
                               // 15s waitForFunction timeout for the full 250-chunk reply)
const WORDS = 'the quick brown fox jumps over a lazy dog and keeps going for a while '.split(' ');

let sentChunks = 0;
let raceMode = false;
const server = http.createServer((req, res) => {
  const path = (req.url || '').split('?')[0];
  if (path === '/' || path === '/index.html') {
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
    return res.end(INDEX_HTML);
  }
  if (JS[path]) { res.writeHead(200, { 'content-type': 'text/javascript; charset=utf-8' }); return res.end(JS[path]); }
  if (CSS[path]) { res.writeHead(200, { 'content-type': 'text/css; charset=utf-8' }); return res.end(CSS[path]); }
  if (path === '/api/projects') { res.writeHead(200, { 'content-type': 'application/json' }); return res.end('[]'); }
  if (path === '/api/config') { res.writeHead(200, { 'content-type': 'application/json' }); return res.end('{}'); }
  if (path === '/api/characters') { res.writeHead(200, { 'content-type': 'application/json' }); return res.end('[]'); }
  if (path === '/api/guide/stream' && raceMode) {
    // Race case: the last delta and the terminal event land in ONE network
    // read, so the delta's queued render frame fires after the error handler.
    res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' });
    res.write(`data: ${JSON.stringify({ type: 'delta', text: 'partial answer ' })}

`
      + `data: ${JSON.stringify({ type: 'error', message: 'RACE_ERROR_SENTINEL' })}

`);
    return res.end();
  }
  if (path === '/api/guide/stream') {
    res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' });
    let i = 0, full = '';
    const timer = setInterval(() => {
      if (i >= N_CHUNKS) {
        clearInterval(timer);
        res.write(`data: ${JSON.stringify({ type: 'done', answer: full.trim() })}\n\n`);
        return res.end();
      }
      // Write a whole burst synchronously — no delay between writes in the
      // burst — so several deltas genuinely land within one animation frame
      // client-side, which is the case the batching fix targets.
      for (let b = 0; b < BURST_SIZE && i < N_CHUNKS; b++, i++) {
        const text = WORDS[i % WORDS.length] + ' ';
        full += text;
        sentChunks++;
        res.write(`data: ${JSON.stringify({ type: 'delta', text })}\n\n`);
      }
    }, BURST_INTERVAL_MS);
    req.on('close', () => clearInterval(timer));
    return;
  }
  res.writeHead(404); res.end();
});

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

let browser, exitCode = 1;
try {
  await new Promise((res) => server.listen(0, '127.0.0.1', res));
  const port = server.address().port;

  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

  await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'domcontentloaded' });
  // 390px viewport is mobile: #claydo-fab is display:none there (app.css:7533),
  // the trigger lives in the bottom tab bar instead.
  await page.waitForSelector('#bottom-tab-bar', { timeout: 15000 });
  await page.evaluate(() => openClaydo());
  await page.waitForSelector('#claydo-input', { timeout: 5000 });

  // Count actual DOM mutations to the history pane (one per render call,
  // whether the old per-chunk code or the new per-frame batched code) plus
  // a long-task tally (PerformanceObserver) as a second, independent signal
  // of main-thread cost.
  await page.evaluate(() => {
    window.__mutations = 0;
    window.__longTaskMs = 0;
    window.__longTaskCount = 0;
    new MutationObserver((records) => { window.__mutations += records.length; })
      .observe(document.getElementById('claydo-history'), { childList: true, subtree: true, characterData: true });
    try {
      new PerformanceObserver((list) => {
        for (const e of list.getEntries()) { window.__longTaskMs += e.duration; window.__longTaskCount++; }
      }).observe({ entryTypes: ['longtask'] });
    } catch (e) { /* longtask entry type unsupported — mutation count still proves it */ }
  });

  const t0 = await page.evaluate(() => performance.now());
  await page.evaluate(() => {
    document.getElementById('claydo-input').value = 'tell me a long story';
    submitClaydo();
  });

  // Wait for the stream to finish (bot message settles + input re-enabled).
  await page.waitForFunction(() => {
    const send = document.getElementById('claydo-send');
    return send && !send.disabled;
  }, { timeout: 15000 });
  const t1 = await page.evaluate(() => performance.now());

  const { mutations, longTaskMs, longTaskCount } = await page.evaluate(() => ({
    mutations: window.__mutations, longTaskMs: window.__longTaskMs, longTaskCount: window.__longTaskCount,
  }));

  console.log(`\n  chunks streamed:        ${sentChunks}`);
  console.log(`  stream wall time:       ${(t1 - t0).toFixed(0)}ms`);
  console.log(`  DOM render mutations:   ${mutations}  (pre-fix would be ${sentChunks}, 1:1 with chunks)`);
  console.log(`  long tasks (>50ms):     ${longTaskCount}, total ${longTaskMs.toFixed(0)}ms\n`);

  (sentChunks >= N_CHUNKS * 0.9)
    ? ok(`server streamed the full burst (${sentChunks}/${N_CHUNKS} chunks)`)
    : fail(`server only streamed ${sentChunks}/${N_CHUNKS} chunks — burst was cut short`);

  (mutations > 0 && mutations < sentChunks * 0.5)
    ? ok(`renders (${mutations}) are batched well below chunk count (${sentChunks}) — throttling is working`)
    : fail(`renders (${mutations}) are NOT meaningfully batched vs chunk count (${sentChunks}) — one render per chunk regressed`);

  // Terminal event must win over a render frame queued by a delta in the
  // same read — otherwise the error bubble + Retry is wiped by partial text.
  raceMode = true;
  await page.evaluate(() => {
    document.getElementById('claydo-input').value = 'race';
    submitClaydo();
  });
  await page.waitForFunction(() => !document.getElementById('claydo-send').disabled, { timeout: 10000 });
  // Let any stale rAF callback run before judging.
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  const last = await page.evaluate(() => {
    const msgs = document.querySelectorAll('#claydo-history > *');
    const m = msgs[msgs.length - 1];
    return { text: m ? m.textContent : '', retry: !!(m && m.querySelector('.claydo-retry-btn')) };
  });
  (last.retry && last.text.includes('RACE_ERROR_SENTINEL'))
    ? ok('delta + error in one read: error bubble and Retry survive the queued render frame')
    : fail(`delta + error in one read: terminal output was overwritten (text="${last.text.slice(0, 80)}", retry=${last.retry})`);

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail('uncaught page error: ' + e));

  await ctx.close();
  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error(e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
  if (server) await new Promise((res) => server.close(res));
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(exitCode);

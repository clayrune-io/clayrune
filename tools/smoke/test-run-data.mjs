#!/usr/bin/env node
/**
 * MC-1148 phase 9: the shared test-run data layer in a REAL browser.
 *
 * WHY THIS EXISTS
 * ---------------
 * tools/smoke/test-run-data.test.mjs proves the coordinator's logic with a fake
 * clock. This proves the parts only a browser can: the module loads in the real
 * SPA and publishes window.TestRunData; updates keep arriving with no chat
 * stream open; a frozen page (Chromium lifecycle `frozen`, what a mobile browser
 * does to a backgrounded tab) catches up the moment it resumes; a hidden page
 * stays silent; the SSE `test_run` hint in resume-preview.js pulls a quiet
 * poller forward; and NO EventSource is ever opened for a run.
 *
 * Hermetic: serves the real static/ tree from disk and stubs only
 * /api/project/<id>/test-runs. No MC server needed.
 *
 * RUN: node tools/smoke/test-run-data.mjs   (needs tools/smoke/node_modules)
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(here, '..', '..');
const INDEX = readFileSync(resolve(ROOT, 'static', 'index.html'), 'utf8');
const STATIC = loadStaticJsCss(ROOT);
const PROJECTS = readFileSync(resolve(here, 'fixtures', 'projects.json'), 'utf8');
const PID = JSON.parse(PROJECTS)[0].id;

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (c, m) => (c ? ok(m) : fail(m));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── the stub server ──────────────────────────────────────────────────────────
const srv = { runs: [], mode: 'ok', listCalls: [], streamHint: null };
const snap = (id, seq, state) => ({ run_id: id, project_id: PID, owner_session_id: 'sess1', state, seq,
                                    created_at: `2026-11-06T09:00:${String(seq).padStart(2, '0')}Z` });

async function wirePage(page) {
  await page.addInitScript(() => {
    window.__es = [];
    const Real = window.EventSource;
    window.EventSource = class extends Real { constructor(u, o) { super(u, o); window.__es.push(String(u)); } };
  });
  await page.route('**/*', (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === `/api/project/${PID}/test-runs`) {
      srv.listCalls.push(Date.now());
      if (srv.mode === 'off') return route.fulfill({ status: 404, contentType: 'text/html', body: '<h1>404</h1>' });
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ runs: srv.runs }) });
    }
    if (/\/agent\/stream$/.test(path)) {
      const body = srv.streamHint ? `data: ${JSON.stringify(srv.streamHint)}\n\n` : ': hb\n\n';
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body });
    }
    if (path === '/') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    return route.abort();   // everything else: let the SPA take its real failure fallbacks
  });
}

const browser = await chromium.launch();
try {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await wirePage(page);
  await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 20000 });

  console.log('load');
  check(await page.evaluate(() => typeof window.TestRunData?.subscribe === 'function'), 'window.TestRunData is published by the module');

  // Fast cadences so the smoke is quick; the logic under test is unchanged.
  await page.evaluate((pid) => {
    window.TestRunData.configure({ activeMs: 150, quietStartMs: 150, quietMaxMs: 150, hintDebounceMs: 20 });
    window.__views = [];
    window.__unsub = window.TestRunData.subscribe(pid, 'sess1', (v) => window.__views.push(v));
  }, PID);
  const last = () => page.evaluate(() => { const v = window.__views.at(-1); return v && { n: v.runs.length, state: v.runs[0]?.state, seq: v.runs[0]?.seq, st: v.status.state }; });

  console.log('updates arrive with no chat stream open');
  srv.runs = [snap('r1', 2, 'running')];
  await sleep(700);
  let v = await last();
  check(v && v.state === 'running' && v.st === 'ok', `cold read shows the running run (${JSON.stringify(v)})`);
  srv.runs = [snap('r1', 6, 'passed')];
  await sleep(700);
  v = await last();
  check(v.state === 'passed' && v.seq === 6, 'a later server change reaches the subscriber');
  check(await page.evaluate(() => window.__es.length) === 0, 'no EventSource opened (none per run, none at all)');

  console.log('hidden page is silent, visible page reads at once');
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
  });
  await sleep(300);                               // let an in-flight read finish
  const hiddenFrom = srv.listCalls.length;
  await sleep(900);
  check(srv.listCalls.length === hiddenFrom, `no requests while hidden (${srv.listCalls.length - hiddenFrom})`);
  srv.runs = [snap('r1', 6, 'passed'), snap('r2', 3, 'running')];
  await page.evaluate(() => {
    delete document.hidden; delete document.visibilityState;
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await sleep(300);
  v = await last();
  check(v.n === 2, 'visible again: the run that started while hidden appears');

  console.log('suspended page (mobile tab freeze) catches up on resume');
  // Page.setWebLifecycleState 'frozen' does nothing to a foreground headless tab, so
  // suspension is emulated with the debugger: all page JS stops, timers included, and
  // every overdue timer fires late on resume, which is what a thawed mobile tab sees.
  const cdp = await ctx.newCDPSession(page);
  await cdp.send('Debugger.enable');
  await sleep(300);
  const calls = srv.listCalls.length;
  await cdp.send('Debugger.pause');
  srv.runs = [snap('r1', 6, 'passed'), snap('r2', 9, 'failed')];
  await sleep(6500);                              // longer than the 5s late-timer threshold
  const during = srv.listCalls.length - calls;
  check(during <= 1, `a suspended page makes (almost) no requests (${during})`);
  await cdp.send('Debugger.resume');
  await sleep(900);
  v = await page.evaluate(() => window.__views.at(-1).runs.find((r) => r.run_id === 'r2'));
  check(v && v.state === 'failed' && v.seq === 9, 'after resume the page shows the state reached while suspended');
  await cdp.send('Debugger.disable');

  console.log('SSE hint pulls a quiet poller forward');
  await page.evaluate(() => window.TestRunData.configure({ activeMs: 60000, quietStartMs: 60000, quietMaxMs: 60000 }));
  srv.runs = [snap('r1', 6, 'passed'), snap('r2', 9, 'failed')];
  await sleep(300);
  const quietFrom = srv.listCalls.length;
  await sleep(700);
  check(srv.listCalls.length === quietFrom, 'poller is quiet (60s interval) before the hint');
  srv.runs = [snap('r1', 6, 'passed'), snap('r2', 9, 'failed'), snap('r3', 1, 'running')];
  srv.streamHint = { type: 'test_run', run_id: 'r3', seq: 1 };
  await page.evaluate((pid) => window.connectAgentStream(pid, 'sess1'), PID);
  await sleep(1200);
  v = await page.evaluate(() => window.__views.at(-1).runs.map((r) => r.run_id).sort().join(','));
  check(v === 'r1,r2,r3', `hint made the poller read server truth immediately (${v})`);
  const esUrls = await page.evaluate(() => window.__es);
  check(esUrls.length >= 1 && esUrls.every((u) => /\/agent\/stream\?/.test(u)), `only the chat stream opened an EventSource (${esUrls.length})`);

  console.log('flag off');
  await page.evaluate(() => window.__unsub());
  srv.mode = 'off';
  await page.evaluate((pid) => { window.TestRunData.configure({ activeMs: 150, quietStartMs: 150, quietMaxMs: 150, disabledMs: 60000 }); window.__views = []; window.TestRunData.subscribe(pid, null, (x) => window.__views.push(x)); }, PID);
  await sleep(800);
  v = await last();
  check(v && v.st === 'disabled', `a bare 404 reads as disabled (${JSON.stringify(v)})`);
  const offFrom = srv.listCalls.length;
  await sleep(800);
  check(srv.listCalls.length === offFrom, 'and it does not hammer the server');

  const mine = errors.filter((e) => /test-run|TestRunData/i.test(e));
  check(mine.length === 0, `no uncaught page errors from the module (${mine.join(' | ') || 'none'})`);
} finally {
  await browser.close();
}
console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);

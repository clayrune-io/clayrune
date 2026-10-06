#!/usr/bin/env node
/**
 * Documents tab LOAD behaviour (perf regression guard). Ron, 2026-10-05:
 * "the Documents tab takes ages to load" on phone AND desktop.
 *
 * What was measured (live :5199, 351 rows, 150 KB): the endpoint itself costs
 * 0.2-0.6 s warm (and ~9 s the first time after a server start), and the tab
 * did not even APPEAR until that fetch returned. loadProjectDocuments awaited
 * the fetch before it ever rebuilt the modal, then rebuilt the whole modal
 * (which re-rendered the list) and rendered the list a second time. A click
 * produced nothing on screen for the full wait.
 *
 * Hermetic (route interception, no server). /documents is answered with 351
 * rows after a deliberate 1200 ms delay, so the assertions are about what the
 * UI does DURING and AFTER the wait, not about this machine's speed:
 *   1. the Documents panel is on screen, showing "Loading", within 500 ms of
 *      the click, long before the response
 *   2. a double trigger (two quick tab switches) costs ONE /documents request
 *   3. once the response lands, all 351 rows are painted within 600 ms
 *   4. re-opening the tab paints the cached rows at once (<300 ms), while the
 *      revalidating fetch is still pending, and that fetch is the 2nd, total
 *   5. a failed refresh keeps the rows already shown
 *
 * Per viewport: 1440 and 390.   RUN: node documents-tab-load.mjs
 */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_docload';
const DELAY_MS = 1200;
const ROWS = 351;

const PROJECT = {
  id: PID, name: 'Docs Load Smoke', status: 'active', domain: 'general', emoji: '🧪',
  description: 'Fixture project for the Documents-tab load smoke.', summary: '',
  current_task: 'Idle', next_action: '', blocked: false, blocked_reason: null,
  activity_log: [], backlog: [], project_path: '/tmp/smoke-docload',
  last_updated: '2026-10-05T00:00:00Z', last_updated_relative: 'today', last_completed: null,
  live_agent: null, display_order: 0, provider: 'claude', use_streaming_agent: true,
  distiller_mode: 'proposed', distiller_min_recurrence: 3, distiller_max_topics_per_session: 3,
  distiller_max_preferences_per_session: 3, distiller_max_explorations_per_session: 3,
  distiller_min_turns: 5, distiller_skip_errors: true,
};
const DOCUMENTS = Array.from({ length: ROWS }, (_, i) => (i % 3
  ? { path: `/p/docs/DOC_${i}.md`, title: `Doc ${i}`, kind: 'doc', ts_relative: `${i}h ago`,
      location: `docs/DOC_${i}.md`, filename: `DOC_${i}.md`, deletable: false }
  : { path: `/plans/PLAN_${i}.md`, title: `Plan ${i}`, kind: 'plan', ts_relative: `${i}h ago`,
      task: `Task ${i}`, filename: `PLAN_${i}.md`, deletable: true }));

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (c, g, b) => (c ? ok(g) : fail(b));

async function runAtViewport(browser, width, height, label) {
  console.log(`\n-- ${label} (${width}x${height}) --`);
  const ctx = await browser.newContext({ viewport: { width, height } });
  const reqTimes = [];
  let mode = 'ok';           // 'ok' | 'fail'
  let respondedAt = 0;
  try {
    const page = await ctx.newPage();
    const pageErrors = [];
    // the CDN mermaid import is aborted by this offline harness: not ours
    page.on('pageerror', (e) => { const m = e.message || String(e); if (!/dynamically imported module/.test(m)) pageErrors.push(m); });
    await page.route('**/*', async (route) => {
      const u = new URL(route.request().url());
      const p = u.pathname;
      const json = (b, s = 200) => route.fulfill({ status: s, contentType: 'application/json', body: JSON.stringify(b) });
      if (p === '/' || p === '/index.html')
        return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8') });
      if (p.startsWith('/static/')) {
        const f = resolve(REPO_ROOT, p.slice(1));
        if (f.startsWith(resolve(REPO_ROOT, 'static')) && existsSync(f))
          return route.fulfill({ status: 200, contentType: p.endsWith('.css') ? 'text/css' : 'text/javascript; charset=utf-8', body: readFileSync(f) });
        return route.fulfill({ status: 404, body: '' });
      }
      if (p === '/api/projects') return json([PROJECT]);
      if (p === '/api/config') return json({});
      if (p === `/api/project/${PID}/backlog`) return json([]);
      if (p === `/api/project/${PID}/agent/status`) return json({ sessions: [] });
      if (p === `/api/project/${PID}/terminal/status`) return json({ sessions: [] });
      if (p === `/api/project/${PID}/social/queue`) return json([]);
      if (p === `/api/project/${PID}/documents`) {
        reqTimes.push(Date.now());
        await new Promise((r) => setTimeout(r, DELAY_MS));
        respondedAt = Date.now();
        return mode === 'fail' ? json({ error: 'boom' }, 500) : json(DOCUMENTS);
      }
      return route.abort();
    });
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 20000 });
    await page.evaluate((pid) => window.openProjectModal(pid), PID);
    await page.waitForTimeout(600);

    // paint timestamp in the page's own clock, comparable with Date.now()
    await page.evaluate(() => {
      window.__rowsAt = 0;
      new MutationObserver(() => {
        if (!window.__rowsAt && document.querySelectorAll('.plan-history-card').length) window.__rowsAt = Date.now();
      }).observe(document.body, { childList: true, subtree: true });
    });

    // 1 + 2: click, then a second quick trigger
    const t0 = Date.now();
    await page.evaluate((pid) => { window.switchModalTab(pid, 'documents'); window.switchModalTab(pid, 'documents'); }, PID);
    await page.waitForFunction(() => {
      const a = document.querySelector('.modal-tab-content.active[data-tab="documents"]');
      return a && /Loading/i.test(a.innerText);
    }, null, { timeout: 500 }).then(
      () => ok(`Documents panel on screen showing "Loading" ${Date.now() - t0} ms after the click (response is ${DELAY_MS} ms away)`),
      () => fail(`Documents panel not on screen within 500 ms of the click (response delayed ${DELAY_MS} ms)`));

    await page.waitForSelector('.plan-history-card', { timeout: 10000 });
    await page.waitForTimeout(300);
    check(reqTimes.length === 1, 'two quick triggers → exactly 1 /documents request', `${reqTimes.length} /documents requests for one open`);
    const rowsAt = await page.evaluate(() => window.__rowsAt);
    const paintLag = rowsAt - respondedAt;
    check(paintLag <= 600, `${ROWS} rows painted ${Math.max(paintLag, 0)} ms after the response (≤600)`, `rows painted ${paintLag} ms after the response (>600)`);
    const n = await page.$$eval('.modal-tab-content.active .plan-history-card', (e) => e.length);
    check(n === ROWS, `all ${ROWS} rows rendered`, `rendered ${n}/${ROWS} rows`);

    // 4: leave and come back: cached rows at once, revalidation in flight
    await page.evaluate((pid) => window.switchModalTab(pid, 'backlog'), PID);
    await page.waitForTimeout(200);
    const before = reqTimes.length;
    const t1 = Date.now();
    await page.evaluate((pid) => window.switchModalTab(pid, 'documents'), PID);
    await page.waitForFunction((rows) => document.querySelectorAll('.modal-tab-content.active .plan-history-card').length === rows,
      ROWS, { timeout: 300 }).then(
      () => ok(`re-open paints the cached ${ROWS} rows in ${Date.now() - t1} ms, revalidate still pending`),
      () => fail('re-open did not paint the cached rows within 300 ms'));
    await page.waitForTimeout(200);
    check(reqTimes.length === before + 1, 're-open revalidates with exactly 1 more request', `re-open made ${reqTimes.length - before} requests`);

    // 5: a failing refresh keeps what is on screen
    await page.waitForTimeout(DELAY_MS + 200);
    mode = 'fail';
    await page.evaluate((pid) => window.loadProjectDocuments(pid), PID);
    await page.waitForTimeout(DELAY_MS + 400);
    const kept = await page.$$eval('.modal-tab-content.active .plan-history-card', (e) => e.length);
    check(kept === ROWS, 'a failed refresh keeps the rows already shown', `after a failed refresh ${kept} rows remain`);

    check(pageErrors.length === 0, 'no uncaught page errors', `page errors: ${pageErrors.join(' | ')}`);
  } finally {
    await ctx.close();
  }
}

const browser = await chromium.launch();
try {
  await runAtViewport(browser, 1440, 900, 'desktop');
  await runAtViewport(browser, 390, 844, 'phone');
} finally {
  await browser.close();
}
console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);

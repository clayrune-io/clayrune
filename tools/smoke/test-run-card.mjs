#!/usr/bin/env node
/**
 * MC-1148 phase 10: the in-chat test-run card (static/js/test-run-card.js) in a REAL browser.
 *
 * Hermetic: real index.html + static/ from disk; only /api/projects, /api/config and
 * /api/project/<id>/test-runs are stubbed. No MC server, no config write.
 *
 * Asserts:
 *   - live: a `[test-run:<id>]` line renders one card from the server snapshot, the raw marker is hidden;
 *   - honest bar: unknown total -> native indeterminate <progress> (no value), no percent, no ETA;
 *     known total -> value + percent; all-done while running -> "Finalizing", never "Passed";
 *   - a terminal run shows its verdict + failure list in a keyboard-reachable <details>;
 *   - malicious names/labels land as text: no injected element, no handler run;
 *   - no duplicates: the same marker twice live, repeated mount calls, and THREE refreshModalById
 *     rebuilds of the real project modal each leave exactly one card per run (DOM-decided);
 *   - cold render through the real agentPanelHTML (conversation.js) mounts the same card;
 *   - inactive-tab reopen: switch to another tab and back, the card returns, no stale twin;
 *   - flag off (bare 404): the marker stays an ordinary line, no card DOM, no card requests beyond the probe;
 *   - an id the server does not know -> "no longer available", not a fabricated card;
 *   - when every card is settled the shared subscription is dropped.
 *
 * RUN   node tools/smoke/test-run-card.mjs   (needs tools/smoke/node_modules)
 * Exit 0 = all checks pass; 1 = a check failed / harness error.
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
const ORIGIN = 'http://trc.smoke.test';
const PID = 'smoke_trc';
const SID = 'trc_sid';
const SID2 = 'trc_sid_other';

const PROJECT = {
  id: PID, name: 'Test run card smoke', status: 'active', domain: 'general', emoji: '🧪',
  description: '', summary: '', current_task: 'Idle', next_action: '', blocked: false, blocked_reason: null,
  activity_log: [], backlog: [], project_path: '/smoke/trc', last_updated: '2026-11-06T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null, display_order: 0,
  provider: 'claude', use_streaming_agent: true, roster: [],
  distiller_mode: 'proposed', distiller_min_recurrence: 3, distiller_max_topics_per_session: 3,
  distiller_max_preferences_per_session: 3, distiller_max_explorations_per_session: 3,
  distiller_min_turns: 5, distiller_skip_errors: true,
};

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (c, m) => (c ? ok(m) : fail(m));

const hex = (n) => String(n).padStart(32, '0').replace(/[^0-9a-f]/g, 'a');
const ID = { unknown: hex(1), running: hex(2), done: hex(3), evil: hex(4), failed: hex(5),
             gone: hex(6), twin: hex(7), foreign: hex(8) };
const counts = (o = {}) => ({ pass: 0, fail: 0, skip: 0, error: 0, xfail: 0, xpass: 0, ...o });
const row = (id, over = {}) => ({
  run_id: id, project_id: PID, owner_session_id: SID, state: 'running', seq: 1,
  runner: 'pytest', unit: 'test', display_label: 'pytest tests/unit', capture: 'complete',
  selected_total: null, completed: 0, counts: counts(), latest_failures: [], failure_total: 0,
  collection_errors: 0, not_run: null, eta_seconds: null, total_recalculated: false,
  exit_code: null, created_at: 1000, updated_at: 1042, finished_at: null, ...over,
});
const EVIL = '<img src=x onerror="window.__pwn=1"><script>window.__pwn=2</script>';
const srv = {
  mode: 'ok',
  calls: 0,
  runs: [
    row(ID.unknown),                                                         // total unknown
    row(ID.running, { selected_total: 10, completed: 4, counts: counts({ pass: 4 }), eta_seconds: null }),
    row(ID.twin, { selected_total: 5, completed: 5, counts: counts({ pass: 5 }) }),   // all done, no exit yet
    row(ID.done, { state: 'passed', selected_total: 3, completed: 3, counts: counts({ pass: 3 }), exit_code: 0, finished_at: 1100 }),
    row(ID.failed, { state: 'failed', selected_total: 3, completed: 3, counts: counts({ pass: 1, fail: 2 }),
                     failure_total: 2, latest_failures: [{ name: 'tests/a.py::test_x', phase: 'call' }, { name: 'tests/a.py::test_y', phase: 'setup' }],
                     exit_code: 1, finished_at: 1100 }),
    row(ID.evil, { state: 'failed', display_label: EVIL, selected_total: 1, completed: 1, counts: counts({ fail: 1 }),
                   failure_total: 1, latest_failures: [{ name: EVIL, phase: '<b>p</b>' }], exit_code: 1, finished_at: 1100 }),
    row(ID.foreign, { owner_session_id: 'someone_else' }),
  ],
};

async function fulfill(route) {
  const url = new URL(route.request().url());
  const path = url.pathname;
  const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX });
  const hit = STATIC[path];
  if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
  if (path === '/api/projects') return J([PROJECT]);
  if (path === '/api/config') return J({ user_timezone: '' });
  if (path === '/api/characters') return J([]);
  if (path === '/api/local-auth/status') return J({ configured: true });
  if (path === `/api/project/${PID}/test-runs`) {
    srv.calls++;
    if (srv.mode === 'off') return route.fulfill({ status: 404, contentType: 'text/html', body: '<h1>404</h1>' });
    const sid = url.searchParams.get('session_id');
    const runs = sid ? srv.runs.filter((r) => r.owner_session_id === sid) : srv.runs;
    return J({ runs });
  }
  return route.abort();
}

async function newPage(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message || String(e)));
  await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
  await page.route('**/*', fulfill);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  return { ctx, page, errors };
}

const cardCount = (page, id) => page.locator(`.test-run-card[data-test-run-id="${id}"]`).count();
const cardText = (page, id) => page.locator(`.test-run-card[data-test-run-id="${id}"]`).first().innerText();

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const { ctx, page, errors } = await newPage(browser);
  await page.evaluate(() => window.TestRunData.configure && window.TestRunData.configure({}));

  // ── live path ─────────────────────────────────────────────────────────────
  console.log('live — lines streamed into a chat output');
  await page.evaluate(({ sid, ids }) => {
    const modal = document.createElement('div');
    modal.dataset.modalId = 'trc_live';
    modal.style.cssText = 'position:relative;width:760px;background:var(--surface);padding:8px';
    const out = document.createElement('div');
    out.id = `agent-output-${sid}`;
    out.className = 'agent-output';
    modal.appendChild(out);
    document.body.prepend(modal);
    agentStatusCache[sid] = { projectId: 'smoke_trc', status: 'idle' };
    window.appendAgentLine(sid, 'Started the unit tests in the background.');
    for (const id of ids) window.appendAgentLine(sid, `[test-run:${id}]`);
    window.appendAgentLine(sid, `[test-run:${ids[0]}]`);          // same marker again
  }, { sid: SID, ids: [ID.unknown, ID.running, ID.twin, ID.done, ID.failed, ID.evil, ID.gone, ID.foreign] });
  await page.waitForSelector(`.test-run-card[data-test-run-id="${ID.failed}"]`, { timeout: 6000 }).catch(() => {});

  check(await cardCount(page, ID.unknown) === 1, 'a marker typed twice produced ONE card');
  const vis = await page.evaluate(() => [...document.querySelectorAll('#agent-output-trc_sid .agent-line')]
    .filter((e) => /\[test-run:[0-9a-f]{32}\]/.test(e.textContent) && e.closest('.test-run-card-mount') == null && e.offsetParent !== null).length);
  check(vis === 0, 'no raw marker line is left visible for a run that has a card');

  // unknown total
  const u = page.locator(`.test-run-card[data-test-run-id="${ID.unknown}"]`);
  check(await u.locator('progress').evaluate((p) => !p.hasAttribute('value') && p.position === -1), 'unknown total -> native indeterminate <progress> (no value attribute)');
  const ut = await u.innerText();
  check(!/\d+\s*%/.test(ut) && !/left|ETA/i.test(ut), `unknown total shows no percent and no ETA ("${ut.replace(/\s+/g, ' ')}")`);
  check(/total not announced|Waiting for the first result/.test(ut), 'unknown total says the total is not announced');

  // known total
  const r = page.locator(`.test-run-card[data-test-run-id="${ID.running}"]`);
  check(await r.locator('progress').getAttribute('value') === '40', 'known total 4/10 -> value=40');
  check(/40% · 4 of 10 tests/.test(await r.innerText()), 'known total shows "40% · 4 of 10 tests"');
  check(!/left/.test(await r.innerText()), 'no ETA when the server supplies none');

  // all done but no exit yet
  const t = await cardText(page, ID.twin);
  check(/Finalizing/.test(t) && !/Passed/.test(t), 'every unit done while still running reads Finalizing, never Passed');

  // terminal
  const d = await cardText(page, ID.done);
  check(/Passed/.test(d) && /100%/.test(d) && /3 pass/.test(d), 'a passed run shows Passed, 100%, 3 pass');
  const f = page.locator(`.test-run-card[data-test-run-id="${ID.failed}"]`);
  check(/Failed/.test(await f.innerText()) && /2 fail/.test(await f.innerText()), 'a failed run shows Failed and 2 fail');
  check(await f.locator('details.test-run-card-failures > summary').innerText() === '2 failures', 'failure list is a <details> with a "2 failures" summary');
  check(await f.locator('details').evaluate((e) => e.open), 'a short failure list starts open');
  check((await f.locator('.test-run-card-failure').allInnerTexts()).join('|').includes('tests/a.py::test_x'), 'failure names are listed');
  await f.locator('summary').focus();
  await page.keyboard.press('Enter');
  check(await f.locator('details').evaluate((e) => !e.open), 'the failure list toggles from the keyboard');
  check(await f.locator('[role="status"][aria-live="polite"]').count() === 1, 'one polite live region per card');
  check(await f.locator('progress').getAttribute('aria-label') !== null || await f.locator('progress').isHidden(), 'the progress element is labelled (or hidden when terminal)');
  check(await f.getAttribute('aria-label') === 'Test run pytest tests/unit: Failed', 'the card has an accessible name that carries the verdict');

  // unknown / foreign ids
  check(await cardCount(page, ID.gone) === 0 && /no longer available/.test(await page.locator(`.test-run-card-gone[data-test-run-id="${ID.gone}"]`).innerText().catch(() => '')),
        'an id the server does not know -> "no longer available", no fabricated card');
  check(await cardCount(page, ID.foreign) === 0, 'a run owned by another chat is not shown in this one (marker is only a locator)');

  // malicious
  const evilHtml = await page.locator(`.test-run-card[data-test-run-id="${ID.evil}"]`).innerHTML();
  check(await page.locator(`.test-run-card[data-test-run-id="${ID.evil}"] img, .test-run-card[data-test-run-id="${ID.evil}"] script, .test-run-card[data-test-run-id="${ID.evil}"] b`).count() === 0,
        'malicious label/name/phase produced no injected element');
  check(await page.evaluate(() => window.__pwn === undefined), 'no injected handler ran');
  check((await cardText(page, ID.evil)).includes('<img src=x'), 'the malicious string is shown as literal text');
  check(!/<img/i.test(evilHtml.replace(/&lt;img/g, '')), 'the card HTML carries only escaped markup');

  // ── real project modal: cold render + repeated refreshModalById + tab reopen ──
  console.log('cold — the real project modal, rebuilt repeatedly');
  await page.evaluate(({ pid, sid, sid2, ids }) => {
    document.querySelector('[data-modal-id="trc_live"]')?.remove();
    agentHistory.push({ projectId: pid, sessionId: sid, projectName: 'x', task: 'tests', status: 'idle', startedAt: new Date().toISOString() });
    agentHistory.push({ projectId: pid, sessionId: sid2, projectName: 'x', task: 'other', status: 'idle', startedAt: new Date(Date.now() - 5000).toISOString() });
    agentStatusCache[sid] = { projectId: pid, status: 'idle', task: 'tests', startedAt: new Date().toISOString() };
    agentStatusCache[sid2] = { projectId: pid, status: 'idle', task: 'other', startedAt: new Date(Date.now() - 5000).toISOString() };
    agentOutputBuffers[sid] = ['Run the suite.', ...ids.map((i) => `[test-run:${i}]`), `[test-run:${ids[0]}]`];
    agentOutputBuffers[sid2] = ['nothing here'];
    activeAgentTab[pid] = sid;
    window.openProjectModal(pid);
  }, { pid: PID, sid: SID, sid2: SID2, ids: [ID.unknown, ID.running, ID.failed, ID.evil] });
  await page.waitForSelector(`.test-run-card[data-test-run-id="${ID.failed}"]`, { timeout: 8000 }).catch(() => {});
  const inModal = async (id) => page.locator(`[data-modal-id="${PID}"] .test-run-card[data-test-run-id="${id}"]`).count();
  check(await inModal(ID.failed) === 1 && await inModal(ID.unknown) === 1, 'cold render through agentPanelHTML mounted the cards');
  check(await page.locator(`[data-modal-id="${PID}"] .test-run-card`).count() === 4, 'one card per distinct run (4 ids, one repeated in the log)');
  for (let i = 1; i <= 3; i++) {
    await page.evaluate((pid) => window.refreshModalById(pid), PID);
    await page.waitForSelector(`[data-modal-id="${PID}"] .test-run-card[data-test-run-id="${ID.failed}"]`, { timeout: 5000 }).catch(() => {});
    check(await page.locator(`[data-modal-id="${PID}"] .test-run-card`).count() === 4
          && await page.locator(`[data-modal-id="${PID}"] .test-run-card-mount`).count() === 4,
          `refreshModalById rebuild #${i}: still exactly 4 cards / 4 mounts`);
  }
  await page.evaluate(({ pid, sid2 }) => { activeAgentTab[pid] = sid2; window.refreshModalById(pid); }, { pid: PID, sid2: SID2 });
  check(await page.locator(`[data-modal-id="${PID}"] .test-run-card`).count() === 0, 'switching to another tab leaves no card behind in the DOM');
  await page.evaluate(({ pid, sid }) => { activeAgentTab[pid] = sid; window.refreshModalById(pid); }, { pid: PID, sid: SID });
  await page.waitForSelector(`[data-modal-id="${PID}"] .test-run-card[data-test-run-id="${ID.failed}"]`, { timeout: 5000 }).catch(() => {});
  check(await page.locator(`[data-modal-id="${PID}"] .test-run-card`).count() === 4, 'inactive-tab reopen: the 4 cards come back, no twins');
  const failedAgain = await page.locator(`[data-modal-id="${PID}"] .test-run-card[data-test-run-id="${ID.failed}"]`).innerText();
  check(/Failed/.test(failedAgain), 'the reopened card shows the terminal verdict straight from the cache');
  await page.evaluate((pid) => { const m = document.querySelector(`[data-modal-id="${pid}"]`); m.querySelectorAll('.test-run-card-mount').forEach((x) => { x.removeAttribute('data-trc-mounted'); x.innerHTML = ''; }); window.mountTestRunCards(m); window.mountTestRunCards(m); }, PID);
  check(await page.locator(`[data-modal-id="${PID}"] .test-run-card-mount`).count() === 4, 'calling mountTestRunCards repeatedly never adds a mount');
  if (process.env.MC_SMOKE_SHOT_DIR) await page.locator(`[data-modal-id="${PID}"] .agent-output`).first().screenshot({ path: resolve(process.env.MC_SMOKE_SHOT_DIR, 'test-run-card.png') }).catch(() => {});

  // ── live update without any rebuild ───────────────────────────────────────
  console.log('live update + display model');
  const idx = srv.runs.findIndex((x) => x.run_id === ID.running);
  srv.runs[idx] = row(ID.running, { state: 'passed', seq: 2, selected_total: 10, completed: 10, counts: counts({ pass: 10 }), exit_code: 0, finished_at: 1200 });
  await page.waitForFunction((id) => /Passed/.test(document.querySelector(`[data-modal-id="smoke_trc"] .test-run-card[data-test-run-id="${id}"]`)?.innerText || ''), ID.running, { timeout: 8000 }).catch(() => {});
  check(/Passed/.test(await page.locator(`[data-modal-id="${PID}"] .test-run-card[data-test-run-id="${ID.running}"]`).innerText()),
        'a running card became Passed from the poll, with no rebuild');
  check(await page.locator(`[data-modal-id="${PID}"] .test-run-card`).count() === 4, 'the live update did not add a card');
  const model = await page.evaluate(() => {
    const m = window.TestRunCards.cardModel;
    const base = { run_id: 'x', state: 'running', capture: 'complete', selected_total: 10, completed: 5, counts: {} };
    return {
      eta: m({ ...base, eta_seconds: 90 }).eta,
      etaNone: m(base).eta,
      etaRecalc: m({ ...base, eta_seconds: 90, total_recalculated: true }).eta,
      overBar: m({ ...base, completed: 12 }).bar.mode,
      partialBar: m({ ...base, capture: 'partial' }).bar.mode,
      zeroBar: m({ ...base, selected_total: 0, completed: 0 }).bar.mode,
      unknownState: m({ ...base, state: 'bogus' }).verdict,
      lostTerminal: m({ ...base, state: 'lost', capture: 'lost' }).verdict,
      stale: m(base, 'stale').notes.join(' '),
    };
  });
  check(model.eta === 'about 1m 30s left' && model.etaNone === null && model.etaRecalc === null, `ETA only when the server supplies one and the total is stable (${JSON.stringify([model.eta, model.etaNone, model.etaRecalc])})`);
  check(model.overBar === 'indeterminate' && model.partialBar === 'indeterminate' && model.zeroBar === 'indeterminate',
        `completed>total, partial capture and zero total never get a percentage (${model.overBar}/${model.partialBar}/${model.zeroBar})`);
  check(model.unknownState === 'Status unknown' && /no exit record/.test(model.lostTerminal), 'an unrecognised state reads "Status unknown"; a lost run says no exit record');
  check(/delayed/.test(model.stale), 'stale telemetry is disclosed on the card');

  // ── subscription hygiene ──────────────────────────────────────────────────
  console.log('hygiene');
  await page.evaluate((pid) => { window.closeModal && window.closeModal(pid); document.querySelector(`[data-modal-id="${pid}"]`)?.remove(); }, PID);
  await page.waitForTimeout(100);
  await page.evaluate(() => window.mountTestRunCards(document));    // sweeps detached cards
  const stats = await page.evaluate(() => window.TestRunCards.cardStats());
  check(stats.cards === 0 && stats.subscriptions === 0 && stats.sweeping === false,
        `no card left -> subscription dropped and sweep timer cleared (${JSON.stringify(stats)})`);
  const es = await page.evaluate(() => performance.getEntriesByType('resource').filter((e) => /EventSource|\/stream/.test(e.name)).length);
  check(es === 0, 'the card opened no stream');
  const realErrors = errors.filter((e) => !/cdn\.jsdelivr\.net/.test(e));   // offline harness: the mermaid CDN is unreachable by design
  check(realErrors.length === 0, `no page errors (${realErrors.join(' | ') || 'none'})`);
  await ctx.close();

  // ── flag off ──────────────────────────────────────────────────────────────
  console.log('flag off — bare 404 from the read API');
  srv.mode = 'off';
  const off = await newPage(browser);
  await off.page.evaluate(({ sid, id }) => {
    const out = document.createElement('div');
    out.id = `agent-output-${sid}`;
    out.className = 'agent-output';
    document.body.prepend(out);
    agentStatusCache[sid] = { projectId: 'smoke_trc', status: 'idle' };
    window.appendAgentLine(sid, `[test-run:${id}]`);
  }, { sid: SID, id: ID.running });
  await off.page.waitForTimeout(800);
  check(await off.page.locator('.test-run-card').count() === 0, 'flag off: no card DOM');
  check(/\[test-run:/.test(await off.page.locator('#agent-output-trc_sid').innerText()), 'flag off: the marker stays an ordinary visible line');
  check(await off.page.locator('#agent-output-trc_sid .test-run-card-mount').count() === 0 || await off.page.locator('#agent-output-trc_sid .test-run-card-mount .agent-line').count() === 1,
        'flag off: the placeholder resolves to a plain line');
  check(off.errors.length === 0, `flag off: no page errors (${off.errors.join(' | ') || 'none'})`);
  await off.ctx.close();

  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error('HARNESS ERROR', e);
  bad++;
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(exitCode);

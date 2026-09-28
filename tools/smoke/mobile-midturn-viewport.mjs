#!/usr/bin/env node
/**
 * MC-988 — mobile viewport/keyboard recovery must be decoupled from the send
 * path, and the header label must not go stale under a mid-turn interjection.
 *
 * WHY THIS EXISTS
 * ----------------
 * Ron's report (screenshot: agent_6227bada48.jpg): on a mid-turn send (typing
 * a follow-up to an agent that is ALREADY running), the chat pane stayed
 * wedged at the keyboard-reduced height after the keyboard closed, AND the
 * header status kept reading COMPLETED while the typing dots showed the turn
 * genuinely running.
 *
 * ROOT CAUSE (static/index.html, updateAgentStatusUI + static/js/mobile.js):
 *
 *   1. mobile.js's viewport-height recovery
 *      (`window.mcRecoverViewportOnStatusSettle` = `_recoverStaleFocusedInset`)
 *      was invoked from ONE place that mattered for a stale FOCUSED inset
 *      (a down-button/back-gesture keyboard dismiss, which some Android
 *      WebViews report via neither a focusout nor a visualViewport 'resize' —
 *      see mobile.js's own "Second watchdog" comment, which explicitly stands
 *      down whenever a field is focused): `updateAgentStatusUI`, gated on
 *      `status !== 'running'`. A genuine mid-turn interjection re-enters that
 *      function with status 'running' both BEFORE and AFTER — there is no
 *      settle transition to hang the recovery off — so a stale focused inset
 *      that predates the turn was NEVER re-validated for the entire span of
 *      an already-running turn. Recovery was tied to a STATUS TRANSITION,
 *      not to "the viewport actually changed", which is exactly the kind of
 *      send-path coupling Ron's design explicitly rejects.
 *   2. sendFollowup (static/js/conversation.js) painted eager typing dots
 *      (MC-973) but never touched the status cache/label — the header only
 *      flipped off a terminal status once the server's `turn_start` SSE
 *      event arrived. That event can't arrive before the round trip
 *      resolves (revive/queued/slow-network all delay it further), so the
 *      header sat on a stale terminal label (COMPLETED/IDLE) underneath dots
 *      that already showed a turn in flight.
 *
 * THE FIX:
 *   - updateAgentStatusUI's viewport-recovery call is now UNCONDITIONAL —
 *     every status patch (turn_start/turn_complete/status/the freshness
 *     reconciler, and sendFollowup's own new eager patch) re-validates the
 *     height, regardless of the status value carried. One function is the
 *     single owner; no send/turn path can bypass it.
 *   - sendFollowup now calls updateAgentStatusUI(sid, 'running') synchronously,
 *     in the same tick as the eager dots — fixing the stale label immediately,
 *     and (via the point above) triggering the viewport recovery on every
 *     send, including one where the status value never changes (mid-turn).
 *     This is a pure DOM paint: it deliberately does NOT write
 *     agentStatusCache[sid].status, because that write would land before the
 *     MC-985 `_preSendStatus` capture and permanently disarm the
 *     stale-idle-echo guard in resume-preview.js (caught in review, db3389d).
 *
 * Hermetic: real index.html + real static/js/*.js served verbatim, fake
 * visualViewport installed via addInitScript (same technique as the other
 * mobile smokes). No real server, no network — /agent/send never resolves,
 * modelling a slow/pending round trip so a pass here proves neither check
 * depends on it.
 *
 * RUN   node tools/smoke/mobile-midturn-viewport.mjs
 * Exit  0 = height recovers within one frame and the label never goes
 *           stale; 1 = a regression.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_midturn_viewport';
const VW = 412, VH = 883;   // Galaxy Z Fold-class narrow viewport — the ticket's repro device
const KB = 380;             // a real soft-keyboard-sized bite (> mobile.js's MIN_KB=120)

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

function fixtureProject(id, name) {
  return {
    id, name, status: 'active', domain: 'general', emoji: '🧪',
    description: '', summary: '', current_task: 'Idle', next_action: '',
    blocked: false, blocked_reason: null, activity_log: [], backlog: [],
    project_path: '/smoke/' + id, last_updated: '2026-09-02T00:00:00Z',
    last_updated_relative: 'today', last_completed: null, live_agent: null,
    display_order: 0, provider: 'claude', use_streaming_agent: true,
    distiller_mode: 'proposed', distiller_min_recurrence: 3,
    distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
    distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
    distiller_skip_errors: true,
  };
}
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Midturn Viewport Smoke')]);

// A fake visualViewport we can drive by hand — real devices differ on
// whether a keyboard dismiss ever fires 'resize' (mobile.js's own "Second
// watchdog" comment documents Android WebViews that never do), so the smoke
// controls this directly instead of trusting a headless browser's real one.
const INSTALL_FAKE_VV = `
  (() => {
    const t = new EventTarget();
    t.height = window.innerHeight;
    t.width = window.innerWidth;
    t.offsetTop = 0;
    t.scale = 1;
    Object.defineProperty(window, 'visualViewport', { value: t, configurable: true });
    window.__vv = t;
  })();
`;

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

async function withPage(run) {
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ viewport: { width: VW, height: VH }, hasTouch: true });
    const page = await ctx.newPage();
    await page.addInitScript(INSTALL_FAKE_VV);
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

    await page.route('**/*', (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
      const hit = STATIC[path];
      if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
      if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
      if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      // Models a slow/pending round trip (revive, queued, network slow) —
      // never resolves. Both checks below must not depend on it.
      if (path === `/api/project/${PID}/agent/send`) return new Promise(() => {});
      return route.abort();
    });

    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
    await run(page);

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource|mermaid/i.test(e));
    uncaught.forEach((e) => fail('uncaught page error: ' + e));
    await ctx.close();
  } finally {
    await browser.close();
  }
}

function seedSession(page, sid, status) {
  return page.evaluate(({ pid, sid, status }) => {
    agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Midturn Viewport Smoke', task: 'a turn', status, startedAt: new Date().toISOString() });
    agentStatusCache[sid] = { status, task: 'a turn', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: 'csid-' + sid, character: null };
    agentOutputBuffers[sid] = ['> earlier message'];
    conversationsCache[pid] = [{ claude_session_id: 'csid-' + sid, mc_session_id: sid, character: null, mtime: 1000, ts_relative: 'just now', status, turns: 1, label: 'a turn', first_user: 'earlier message', last_user: 'earlier message' }];
    openProjectModal(pid);
    openConversation(pid, 'csid-' + sid, sid, true);
  }, { pid: PID, sid, status });
}

const appVh = (page) => page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--mc-app-vh')) || 0);
const label = (page, sid) => page.evaluate(sid => document.querySelector(`#session-metrics-${sid}`)?.closest('.agent-panel')?.querySelector('.agent-status-label')?.textContent, sid);

// ── Check 1: mid-turn status settle must recover a stale FOCUSED inset ─────
// (the down-button/back-gesture dismiss mobile.js's own comments say some
// Android WebViews report via neither focusout nor a vv resize) EVEN THOUGH
// the status value never changes across the patch. This is the exact shape
// of a mid-turn interjection: the session was already 'running' and stays
// 'running' — no settle TRANSITION for the old code to hang a recovery off.
async function checkMidTurnStatusSettleRecovers(page) {
  const SID = 'sess-midturn-a';
  await seedSession(page, SID, 'running');
  await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });

  const layout = await page.evaluate(() => document.documentElement.clientHeight);

  // Open the keyboard for real (focus + vv shrink)...
  await page.evaluate(({ sid, kb }) => {
    const ta = document.getElementById(`agent-followup-${sid}`);
    ta.focus();
    window.__vv.height = window.innerHeight - kb;
    window.__vv.dispatchEvent(new Event('resize'));
  }, { sid: SID, kb: KB });
  await page.waitForTimeout(200);
  const shrunk = await appVh(page);
  (shrunk < layout - 100)
    ? ok(`baseline: pane shrinks for the open keyboard (--mc-app-vh=${shrunk}, layout=${layout})`)
    : fail(`baseline never shrunk for the open keyboard (--mc-app-vh=${shrunk}) — test setup is wrong`);

  // ...then dismiss it WITHOUT blurring and WITHOUT a vv resize — the exact
  // WebView quirk mobile.js documents (focus stays on the field, vv.height
  // never updates). Nothing in the app has told the client the keyboard is
  // gone; only a real status patch can still catch it.
  // (vv deliberately left untouched here — modelling the stale-forever case.)

  // A mid-turn status patch arrives (turn_start / activity tick / the
  // freshness reconciler) — status is 'running' before AND after.
  await page.evaluate(sid => window.updateAgentStatusUI(sid, 'running'), SID);
  await page.evaluate(() => new Promise(requestAnimationFrame));

  const recovered = await appVh(page);
  (Math.abs(recovered - layout) <= 2)
    ? ok(`mid-turn status settle recovers the stale focused inset within one frame (--mc-app-vh=${recovered})`)
    : fail(`pane still wedged at keyboard height after a mid-turn status settle (--mc-app-vh=${recovered}, want ~${layout})`);
}

// ── Check 2: idle send — same guarantee, from the send path itself ─────────
async function checkIdleSendRecoversHeight(page) {
  const SID = 'sess-midturn-b';
  await seedSession(page, SID, 'idle');
  await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });
  const layout = await page.evaluate(() => document.documentElement.clientHeight);

  await page.evaluate(({ sid, kb }) => {
    const ta = document.getElementById(`agent-followup-${sid}`);
    ta.focus();
    ta.value = 'a follow-up';
    window.__vv.height = window.innerHeight - kb;
    window.__vv.dispatchEvent(new Event('resize'));
  }, { sid: SID, kb: KB });
  await page.waitForTimeout(200);

  const sendBtn = page.locator(`#agent-followup-${SID}`).locator('xpath=ancestor::div[contains(@class,"agent-chat-input")]').locator('.btn-dispatch, .btn-send-arrow').first();
  await sendBtn.tap();
  await page.evaluate(() => new Promise(requestAnimationFrame));

  const recovered = await appVh(page);
  (Math.abs(recovered - layout) <= 2)
    ? ok(`idle send recovers full height within one frame (--mc-app-vh=${recovered})`)
    : fail(`idle send left the pane at keyboard height (--mc-app-vh=${recovered}, want ~${layout})`);

  // Regression guard (Dave's MC-988 review, db3389d): the eager header paint
  // must be a pure DOM update, not a write to agentStatusCache[sid].status —
  // an optimistic write there lands BEFORE the MC-985 `_preSendStatus`
  // capture a few lines into sendFollowup and would make it always read
  // 'running', permanently disarming the resume-preview.js stale-idle-echo
  // guard (`_preSendStatus[sessionId] === 'idle'`) on every idle send.
  const preSend = await page.evaluate(sid => _preSendStatus[sid], SID);
  (preSend === 'idle')
    ? ok(`_preSendStatus captured the true pre-send status ("${preSend}"), not an optimistic overwrite`)
    : fail(`_preSendStatus[sid] is "${preSend}", want "idle" — the eager header paint is leaking into agentStatusCache`);
}

// ── Check 3: header label must show running during a mid-turn send, not a
// stale terminal status left over from before the send. ───────────────────
async function checkLabelDuringMidTurnSend(page) {
  const SID = 'sess-midturn-c';
  // Stale-cached terminal status (e.g. a revived/parked chat opened while
  // the server-side session had already moved on) — the client believes
  // 'idle'/COMPLETED right up until the send.
  await seedSession(page, SID, 'idle');
  await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });

  (await label(page, SID))
    ? ok(`baseline label present before send: "${await label(page, SID)}"`)
    : fail('no label element found — test setup is wrong');

  await page.evaluate(sid => { document.getElementById(`agent-followup-${sid}`).value = 'confirmed, keep going'; }, SID);
  await page.evaluate(({ pid, sid }) => { window.sendFollowup(pid, sid); }, { pid: PID, sid: SID });
  await page.evaluate(() => new Promise(requestAnimationFrame));

  const lbl = await label(page, SID);
  const stillStale = /completed|idle/i.test(lbl || '');
  (!stillStale)
    ? ok(`header label flips off the stale terminal status in the same tick as send ("${lbl}")`)
    : fail(`header label still reads a stale terminal status ("${lbl}") while the turn is in flight`);
}

try {
  await withPage(checkMidTurnStatusSettleRecovers);
  await withPage(checkIdleSendRecoversHeight);
  await withPage(checkLabelDuringMidTurnSend);
} catch (e) {
  console.error(e);
  bad++;
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(bad ? 1 : 0);

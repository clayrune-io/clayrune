#!/usr/bin/env node
/**
 * Mobile modal stuck at ~60% height — 4th recurrence (2026-09-24).
 *
 * WHY THIS EXISTS
 * ----------------
 * Ron, 2026-09-24 22:41, Android: a project modal stuck at ~60% height, project
 * list visible underneath, composer squeezed — the "split screen" class fixed
 * three times before (911ba86, 2c7e42a, 931449b) for three different triggers.
 * git show 931449b covered app backgrounding/resuming with the keyboard open
 * (visibilitychange/pageshow → forceFull). This is a FOURTH trigger it didn't
 * cover, reproduced here against the real app before being fixed — not assumed.
 *
 * THE TRIGGER (confirmed against real index.html + static/js/*.js, not the
 * isolated mobile.js fixture): the composer holds DOM focus from a stale-vv
 * keyboard dismiss (down-button or the Android back gesture — same WebView
 * quirk 2c7e42a/931449b already document: neither a focusout nor a
 * visualViewport 'resize' fires, so mobile.js's own watchdogs never see
 * anything change), and the agent's turn then completes with NO tap on the
 * transcript and NO backgrounding in between. `updateAgentStatusUI` (MC-940)
 * patches the status dot/label in place and deliberately skips the full modal
 * rebuild (rebuilding the composer every turn cost ~205ms/keystroke on
 * mobile) — so nothing about the turn ending ever touches focus or the
 * keyboard inset, and the layout watchdog explicitly stands down while a
 * field is focused. The modal is stuck with no keyboard on screen and nothing
 * left that could ever prove it. Matches Ron's screenshot exactly: the header
 * said COMPLETED while the composer still showed the RUNNING placeholder
 * ("Interrupt and redirect agent...") — direct evidence the DOM never got a
 * rebuild when the turn ended.
 *
 * (Ruled out, not assumed: the new mobile header Back button (e1a2940)
 * closing the modal DOES blur the composer — closing and reopening the
 * project modal already recovers full height on unpatched code. Not this bug.)
 *
 * THE FIX (static/js/mobile.js + static/index.html): `updateAgentStatusUI`
 * now calls mobile.js's `mcRecoverViewportOnStatusSettle` (== forceFull, the
 * same assume-no-keyboard-then-let-a-fresh-vv-reading-correct-it recovery
 * 931449b already trusts for app backgrounding) whenever a session's status
 * patch lands on anything other than 'running' — covering every current and
 * future caller of the lightweight status patch, not just this one
 * reproduction. Scenario C below proves this doesn't fight a GENUINELY
 * still-open keyboard: it may flash full for one frame, but the next real vv
 * reading re-shrinks it correctly, exactly like the existing bg-resume case.
 *
 * Secondary (Ron): Stop stayed visible on a COMPLETED session — read
 * conversation.js: `stopBtn = (isRunning || st === 'idle' || st === 'error')`
 * and `consoleStatusLabel`: status 'idle' maps to the SAME "Completed" label
 * as status 'completed'. Showing Stop for 'idle' is deliberate (Mode B keeps
 * the CLI process resident after a turn ends; Stop kills that lingering
 * process) — not a bug, not touched here.
 *
 * Hermetic: real index.html + every real static/js/*.js served verbatim (same
 * technique as mobile-send-height-restore.mjs). No server, no network.
 *
 * RUN   node tools/smoke/mobile-status-settle-viewport.mjs
 * Exit  0 = all checks pass; 1 = a regression.
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
const PID = 'smoke_mobile_status_settle';
const SID = 'sess-status-settle';
const VW = 412, VH = 883;   // a typical Android phone, CSS px
const KB = 380;             // a typical soft keyboard

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
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Mobile Status Settle Smoke')]);

// Same fake-visualViewport technique as mobile-keyboard-viewport.mjs /
// mobile-send-height-restore.mjs: a plain writable EventTarget so a scenario
// can drive keyboard show/hide (and leave it STALE) by hand.
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

let failures = 0;
function check(name, actual, expected, tol = 4) {
  const ok = Math.abs(actual - expected) <= tol;
  if (!ok) failures++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}\n        got ${actual}, want ${expected} (±${tol})`);
}

async function openRunningSession(browser) {
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
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(({ pid, sid }) => {
    agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Mobile Status Settle Smoke', task: 'a running turn', status: 'running', startedAt: new Date().toISOString() });
    agentStatusCache[sid] = { status: 'running', task: 'a running turn', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: 'csid-status-settle', character: null };
    agentOutputBuffers[sid] = ['> get started'];
    conversationsCache[pid] = [{ claude_session_id: 'csid-status-settle', mc_session_id: sid, character: null, mtime: 1000, ts_relative: 'just now', status: 'running', turns: 1, label: 'a running turn', first_user: 'a running turn', last_user: 'a running turn' }];
    openProjectModal(pid);
    openConversation(pid, 'csid-status-settle', sid, true);
  }, { pid: PID, sid: SID });
  await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });
  return { ctx, page, pageErrors };
}

const appVh = (page) => page.evaluate(() =>
  parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--mc-app-vh')) || 0);

const focusAndShrink = (page) => page.evaluate(({ sid, kb }) => {
  document.getElementById(`agent-followup-${sid}`).focus();
  window.__vv.height = window.innerHeight - kb;
  window.__vv.dispatchEvent(new Event('resize'));
}, { sid: SID, kb: KB });

// Exactly what resume-preview.js's real turn_complete-equivalent path does:
// patch status in place via updateAgentStatusUI, WITHOUT a refreshModal.
const settleTurn = (page) => page.evaluate((sid) => {
  agentStatusCache[sid].status = 'idle';
  if (typeof updateHistoryStatus === 'function') updateHistoryStatus(sid, 'idle');
  updateAgentStatusUI(sid, 'idle');
}, SID);

let browser;
let exitCode = 1;
try {
  browser = await chromium.launch();

  // ── A: the reproduced bug — stale-vv dismiss, turn settles, no tap/no bg ──
  {
    const { ctx, page, pageErrors } = await openRunningSession(browser);
    const layout = await page.evaluate(() => document.documentElement.clientHeight);
    await focusAndShrink(page);
    await page.waitForTimeout(900);
    check('setup: keyboard-open baseline (stale-vv about to be simulated)', await appVh(page), layout - KB);

    // stale-vv dismiss: down-button / back-gesture. Nothing to simulate beyond
    // NOT touching vv/focus again — that omission IS the quirk (2c7e42a/931449b).
    await settleTurn(page);
    await page.waitForTimeout(1500);   // several watchdog ticks, no tap, no bg

    check('turn settles with composer still focused, no tap, no background: modal self-heals to full height',
          await appVh(page), layout);
    const stillFocused = await page.evaluate(sid => document.activeElement === document.getElementById(`agent-followup-${sid}`), SID);
    if (stillFocused) {
      console.log('  (composer legitimately stayed focused throughout — recovery did not require a blur)');
    } else {
      failures++;
      console.log('FAIL  composer lost focus unexpectedly');
    }

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    uncaught.forEach((e) => { failures++; console.log('FAIL  uncaught page error: ' + e); });
    await ctx.close();
  }

  // ── C: no-regression — a GENUINELY still-open keyboard must still shrink
  // the app after a fresh vv reading, even though settle() now runs on every
  // non-running status patch ──────────────────────────────────────────────
  {
    const { ctx, page, pageErrors } = await openRunningSession(browser);
    const layout = await page.evaluate(() => document.documentElement.clientHeight);
    await focusAndShrink(page);
    await page.waitForTimeout(900);

    await settleTurn(page);
    await page.waitForTimeout(50);
    // A real fresh vv reading arrives shortly after — the keyboard never
    // actually closed (e.g. the user is about to type a follow-up).
    await page.evaluate(kb => {
      window.__vv.height = window.innerHeight - kb;
      window.__vv.dispatchEvent(new Event('resize'));
    }, KB);
    await page.waitForTimeout(900);
    check('a genuinely still-open keyboard re-shrinks the app after settle (no permanent full-height lock-in)',
          await appVh(page), layout - KB);

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    uncaught.forEach((e) => { failures++; console.log('FAIL  uncaught page error: ' + e); });
    await ctx.close();
  }

  // ── B: no-regression — the mobile header Back button (e1a2940), the other
  // candidate, was already fine before this fix (closing blurs the composer);
  // confirm it still is ────────────────────────────────────────────────────
  {
    const { ctx, page, pageErrors } = await openRunningSession(browser);
    const layout = await page.evaluate(() => document.documentElement.clientHeight);
    await focusAndShrink(page);
    await page.waitForTimeout(900);

    await page.evaluate((pid) => {
      document.querySelector(`#modal-layer .modal-window[data-modal-id="${pid}"] .modal-header .modal-minimize`).click();
    }, PID);
    await page.waitForTimeout(600);
    check('header Back button closes the modal and clears the stuck inset', await appVh(page), layout);

    await page.evaluate((pid) => { openProjectModal(pid); }, PID);
    await page.waitForTimeout(600);
    check('reopening the project modal afterward comes up full height', await appVh(page), layout);

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    uncaught.forEach((e) => { failures++; console.log('FAIL  uncaught page error: ' + e); });
    await ctx.close();
  }

  exitCode = failures ? 1 : 0;
} catch (e) {
  console.error(e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(failures ? `\n${failures} check(s) failed` : '\nall checks passed');
process.exit(exitCode);

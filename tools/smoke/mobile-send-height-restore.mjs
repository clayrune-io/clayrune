#!/usr/bin/env node
/**
 * Mobile Send → full-height restore ordering (2026-09-14).
 *
 * WHY THIS EXISTS
 * ----------------
 * Ron's report: on mobile, tapping Send shrinks the keyboard but the chat
 * takes a variable — sometimes long — time to expand back to full screen,
 * and the delay tracks how long the agent takes to respond. Reading
 * conversation.js's sendFollowup() confirmed why: it never blurred the
 * composer or restored height itself, so the layout only recovered whenever
 * mobile.js's own rAF/timeout/watchdog machinery next happened to fire on a
 * main thread that sendFollowup had just loaded with an image upload, an SSE
 * reconnect and a full modal rebuild.
 *
 * The fix moves three things to the very front of sendFollowup(), before any
 * await/network/render: blur the field (mobile-only), call the newly exposed
 * window.mcRestoreFullHeight() (mobile.js's apply(), which forces the
 * keyboard inset to 0 once nothing is focused), then yield one frame.
 *
 * This test proves the restore is now independent of the agent's reply speed
 * by holding the /agent/send POST open indefinitely (never resolving it)
 * until after the height assertion has already passed — a heavy/slow turn
 * modelled as literally never finishing during the check.
 *
 * Hermetic: real index.html + real static/js/*.js served verbatim (same
 * shape as subagent-visibility.mjs), a hand-driven fake visualViewport (same
 * technique as mobile-keyboard-viewport.mjs). No server, no network.
 *
 * RUN   node tools/smoke/mobile-send-height-restore.mjs
 * Exit  0 = restore proven reply-independent; 1 = a regression.
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
const PID = 'smoke_mobile_send';
const SID = 'sess-mobile-send';
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
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Mobile Send Smoke')]);

// Same fake-visualViewport technique as mobile-keyboard-viewport.mjs: a plain
// writable EventTarget so a scenario can drive keyboard show/hide by hand.
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

let browser;
let exitCode = 1;
try {
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: VW, height: VH }, hasTouch: true });
  const page = await ctx.newPage();
  await page.addInitScript(INSTALL_FAKE_VV);
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

  let sendPosted = false;
  let sendResolve = null;
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    // Never resolve on its own — modelling a heavy/slow agent turn. If the
    // height restore secretly depended on this POST settling (the bug),
    // it would never restore for as long as this test runs.
    if (path === `/api/project/${PID}/agent/send`) {
      sendPosted = true;
      return new Promise((res) => {
        sendResolve = () => res(route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) }));
      });
    }
    return route.abort();
  });

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  // At this mobile width #projects-col renders the WhatsApp-style chat-row
  // list (.mc-chat-row), not the desktop .card grid — see floor.js's own
  // HIRE_TILE_SEL comment for the same split.
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });

  await page.evaluate(({ pid, sid }) => {
    agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Mobile Send Smoke', task: 'a running turn', status: 'running', startedAt: new Date().toISOString() });
    agentStatusCache[sid] = { status: 'running', task: 'a running turn', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: 'csid-mobile-send', character: null };
    agentOutputBuffers[sid] = ['> get started'];
    conversationsCache[pid] = [{ claude_session_id: 'csid-mobile-send', mc_session_id: sid, character: null, mtime: 1000, ts_relative: 'just now', status: 'running', turns: 1, label: 'a running turn', first_user: 'a running turn', last_user: 'a running turn' }];
    openProjectModal(pid);
    // Mobile NEVER auto-selects a tab (agentPanelHTML: "mobile → NEVER
    // auto-select") — it lands on the Layer-2 conversation list even for a
    // single chat. Drill in the same way a real tap on the row does
    // (openConversation, wired to the row's onclick) to reach the thread +
    // followup composer. The seeded agentStatusCache entry's claudeSessionId
    // already matches, so this takes the fast switchAgentTab path with no
    // network round trip.
    openConversation(pid, 'csid-mobile-send', sid, true);
  }, { pid: PID, sid: SID });

  await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });
  const layout = await page.evaluate(() => document.documentElement.clientHeight);
  const appVh = () => page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--mc-app-vh')) || 0);

  const restoreFnType = await page.evaluate(() => typeof window.mcRestoreFullHeight);
  (restoreFnType === 'function')
    ? ok('window.mcRestoreFullHeight is exposed by mobile.js')
    : fail(`window.mcRestoreFullHeight is not exposed by mobile.js (typeof === '${restoreFnType}') — sendFollowup has nothing to call`);

  // ── Baseline: focus the composer, keyboard opens, app shrinks ────────────
  await page.evaluate(({ sid, kb }) => {
    const ta = document.getElementById(`agent-followup-${sid}`);
    ta.focus();
    ta.value = 'ping while a heavy reply is pending';
    window.__vv.height = window.innerHeight - kb;
    window.__vv.dispatchEvent(new Event('resize'));
  }, { sid: SID, kb: KB });
  await page.waitForTimeout(900);   // same settle window as mobile-keyboard-viewport.mjs
  const shrunk = await appVh();
  (Math.abs(shrunk - (layout - KB)) <= 4)
    ? ok(`keyboard-open baseline: app shrank to keyboard height (${shrunk} ~= ${layout - KB})`)
    : fail(`keyboard-open baseline never shrank (got ${shrunk}, want ~${layout - KB}) — can't test the restore from here`);

  // ── Fire the send (fire-and-forget, exactly like a real tap) and measure
  // how fast the layout comes back, BEFORE the withheld POST ever settles ──
  await page.evaluate(({ pid, sid }) => { window.sendFollowup(pid, sid); }, { pid: PID, sid: SID });
  await page.evaluate(() => new Promise(requestAnimationFrame));
  const afterFrame = await appVh();
  const stillFocused = await page.evaluate(sid => document.activeElement === document.getElementById(`agent-followup-${sid}`), SID);

  (Math.abs(afterFrame - layout) <= 4)
    ? ok(`full height restored within one frame of Send (got ${afterFrame}, want ~${layout})`)
    : fail(`height NOT restored within one frame of Send (got ${afterFrame}, want ~${layout})`);
  (!stillFocused)
    ? ok('composer field was blurred by the send (keyboard dismiss is deterministic)')
    : fail('composer field is still focused after Send — blur did not happen');

  // ── The rest of the send pipeline still runs, just AFTER the restore, and
  // the POST is confirmed still pending at the moment the restore was proven
  await page.waitForTimeout(500);
  sendPosted
    ? ok('the /agent/send POST fired after the restore (send path continued normally)')
    : fail('/agent/send was never called — sendFollowup did not run to completion');
  (typeof sendResolve === 'function')
    ? ok('/agent/send was still unresolved when the height check ran — restore is reply-independent')
    : fail('the send POST route never armed — cannot confirm reply-independence');

  if (typeof sendResolve === 'function') sendResolve();   // let the page tear down cleanly
  await page.waitForTimeout(200);

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail('uncaught page error: ' + e));

  await ctx.close();
  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error(e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(exitCode);

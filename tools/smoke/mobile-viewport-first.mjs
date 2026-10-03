#!/usr/bin/env node
/**
 * MC-988 (REOPENED 2026-10-02) — viewport recovery comes FIRST.
 *
 * Ron's rule, binding: when the keyboard is reduced or dismissed, the pane
 * returns to FULL height IMMEDIATELY — in the same frame as the
 * visualViewport/window resize — before and independent of any send, queue,
 * respawn, turn status or server round-trip. Typing with the keyboard open
 * must still keep the composer above the keyboard.
 *
 * WHAT THE EARLIER SMOKES MISSED
 * ------------------------------
 * They all `waitForTimeout(200..900)` before reading `--mc-app-vh`, so a
 * recovery that took 500ms (the old 500ms watchdogs) or a frame (the old
 * rAF-scheduled apply) or never-until-a-tap passed or was waved through. This
 * one reads the CSS var and the composer's real on-screen position IN THE SAME
 * TASK as the resize event, and measures worst-case latency for the
 * no-event cases. Every "dismiss" below is checked in four contexts: a
 * RUNNING agent (send POST still pending), an IDLE agent, the browser pane
 * open, and the Learn bubble open.
 *
 * Hermetic: real index.html + real static/js/*.js, fake visualViewport we drive
 * by hand (the same technique as mobile-keyboard-viewport / mobile-midturn).
 *
 * RUN   node tools/smoke/mobile-viewport-first.mjs
 * Exit  0 = every dismissal recovers in-frame; 1 = a regression.
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
const CLAYDO_IDLE = readFileSync(resolve(REPO_ROOT, 'assets', 'claydo-idle.webp'));
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_viewport_first';
const VW = 412, VH = 883;   // Galaxy Z Fold-class
const KB = 380;

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
// learn.js imports its lessons from static/js/learn-lessons/ (one module each).
for (const f of readdirSync(resolve(JS_DIR, 'learn-lessons'))) if (f.endsWith('.js')) STATIC[`/static/js/learn-lessons/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, 'learn-lessons', f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

function fixtureProject(id, name, extra = {}) {
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
    distiller_skip_errors: true, roster: [], ...extra,
  };
}
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Viewport First Smoke'), fixtureProject('clayrune', 'Clayrune', { _is_onboarding_project: true })]);
const FLOOR_JSON = JSON.stringify({
  rooms: [{ id: PID, name: 'Viewport First Smoke', emoji: '🧪', color: '', figures: [{
    session_id: 'sess-fenn', claude_session_id: 'csid-fenn', state: 'idle', reason: null, activity: '', task: 'x',
    character: { name: 'code-reviewer', display: 'Fenn', scope: 'global' }, name: 'Fenn', name_from: 'character',
    avatar: 'fig:scholar', provider: 'claude', model: '', model_from: '', started_at: '', age: '5m',
    trigger_type: 'manual', hivemind_id: '', subagents: [] }] }],
  quiet: [], bench: [], counts: { rooms: 1, figures: 1, quiet: 0, bench: 0 }, activity_states: false, poll_seconds: 5,
});

// A fake visualViewport driven by hand (a headless Chromium's own never moves).
const INSTALL_FAKE_VV = `
  (() => {
    const t = new EventTarget();
    t.height = window.innerHeight; t.width = window.innerWidth; t.offsetTop = 0; t.scale = 1;
    Object.defineProperty(window, 'visualViewport', { value: t, configurable: true });
    window.__vv = t;
    if (!sessionStorage.getItem('__seeded')) { sessionStorage.setItem('__seeded', '1'); localStorage.setItem('walkthrough_done', '1'); }
  })();
`;

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, pass, failMsg) => (cond ? ok(pass) : fail(failMsg || pass));

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
      if (path === '/assets/claydo-idle.webp') return route.fulfill({ status: 200, contentType: 'image/webp', body: CLAYDO_IDLE });
      const json = (o, s = 200) => route.fulfill({ status: s, contentType: 'application/json', body: typeof o === 'string' ? o : JSON.stringify(o) });
      if (path === '/api/projects') return json(PROJECTS_JSON);
      if (path === '/api/config') return json('{}');
      if (path === '/api/characters') return json('[]');
      if (path === '/api/floor') return json(FLOOR_JSON);
      if (path === '/api/diag/viewport') return route.fulfill({ status: 204, body: '' });
      // The send never resolves: models a queued / respawning / slow round trip.
      // Nothing below may depend on it.
      if (path === `/api/project/${PID}/agent/send`) return new Promise(() => {});
      if (path === '/api/browser/launch') return json({ session_id: 'sid-1', url: 'about:blank', profile: 'main', view: { w: 412, h: 700 } }, 201);
      if (path.startsWith('/api/browser/')) return json({ ok: true, sessions: [], profiles: [] });
      if (path.endsWith('/browser/status')) return json({ sessions: [] });
      return route.abort();
    });
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
    await run(page);
    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource|mermaid|dynamically imported/i.test(e));
    uncaught.forEach((e) => fail('uncaught page error: ' + e));
    await ctx.close();
  } finally {
    await browser.close();
  }
}

function seedSession(page, sid, status) {
  return page.evaluate(({ pid, sid, status }) => {
    agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Viewport First Smoke', task: 'a turn', status, startedAt: new Date().toISOString() });
    agentStatusCache[sid] = { status, task: 'a turn', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: 'csid-' + sid, character: null };
    agentOutputBuffers[sid] = ['> earlier message', 'agent: a long-ish reply line'];
    conversationsCache[pid] = [{ claude_session_id: 'csid-' + sid, mc_session_id: sid, character: null, mtime: 1000, ts_relative: 'just now', status, turns: 1, label: 'a turn', first_user: 'earlier', last_user: 'earlier' }];
    openProjectModal(pid);
    openConversation(pid, 'csid-' + sid, sid, true);
  }, { pid: PID, sid, status });
}

const appVh = (page) => page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--mc-app-vh')) || 0);
// Where the composer's bottom edge sits on screen, as a real number.
const composerBottom = (page, sid) => page.evaluate((sid) => {
  const el = document.getElementById(`agent-followup-${sid}`);
  return el ? Math.round(el.getBoundingClientRect().bottom) : -1;
}, sid);

// Open the keyboard for real: focus + type + vv shrink with an event.
async function keyboardUp(page, sid, text = 'a follow-up') {
  await page.evaluate(({ sid, kb, text }) => {
    const ta = document.getElementById(`agent-followup-${sid}`);
    ta.focus(); ta.value = text; ta.dispatchEvent(new Event('input', { bubbles: true }));
    window.__vv.height = window.innerHeight - kb;
    window.__vv.dispatchEvent(new Event('resize'));
  }, { sid, kb: KB, text });
  await page.waitForTimeout(250);
}

// Dismiss the keyboard the way the platform does — focus STAYS on the composer
// (down button / back gesture), only vv grows and fires 'resize' — then read
// the pane height IN THE SAME TASK, before any rAF, timer or microtask turn.
const dismissSync = (page) => page.evaluate(() => {
  window.__vv.height = window.innerHeight;
  window.__vv.dispatchEvent(new Event('resize'));
  return {
    appVh: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--mc-app-vh')) || 0,
    focusKept: /TEXTAREA|INPUT/.test((document.activeElement || {}).tagName || ''),
  };
});

async function assertDismissInFrame(page, sid, layout, label) {
  const before = await composerBottom(page, sid);
  const r = await dismissSync(page);
  const after = await composerBottom(page, sid);
  check(r.focusKept, `${label}: focus stayed on the field (the hard case — no focusout to lean on)`);
  check(Math.abs(r.appVh - layout) <= 2, `${label}: --mc-app-vh=${r.appVh} in the SAME TASK as the vv resize (want ${layout})`,
        `${label}: pane NOT full in the same task as the resize (--mc-app-vh=${r.appVh}, want ${layout})`);
  check(after > before + 150 && after >= layout - 140, `${label}: composer bottom edge ${before}px -> ${after}px (screen ${layout}px)`,
        `${label}: composer stayed up at ${after}px (was ${before}px, screen ${layout}px) — dead band under the pane`);
}

// ── 1 + 2. Running and idle agent: send, reopen keyboard, dismiss ──────────
async function checkAfterSend(status) {
  await withPage(async (page) => {
    const SID = 'sess-' + status;
    await seedSession(page, SID, status);
    await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });
    const layout = await page.evaluate(() => document.documentElement.clientHeight);
    await keyboardUp(page, SID, 'first message');
    check((await appVh(page)) < layout - 100, `[${status}] baseline: pane shrank for the open keyboard`);
    await page.locator(`#agent-followup-${SID}`).locator('xpath=ancestor::div[contains(@class,"agent-chat-input")]').locator('.btn-dispatch, .btn-send-arrow').first().tap();
    await page.evaluate(() => new Promise(requestAnimationFrame));
    check(Math.abs((await appVh(page)) - layout) <= 2, `[${status}] send itself restores full height`);
    // The POST is still pending (never resolves). The user taps the composer
    // again to add a line; the keyboard returns; then it is dismissed.
    await keyboardUp(page, SID, 'and a second line');
    check((await appVh(page)) < layout - 100, `[${status}] keyboard back up after the send (pane shrank again)`);
    await assertDismissInFrame(page, SID, layout, `[${status}] dismiss with the send still pending`);
  });
}

// ── 3. No event at all: vv grows silently (some WebViews) — watchdog latency ─
async function checkNoEventLatency() {
  await withPage(async (page) => {
    const SID = 'sess-noevent';
    await seedSession(page, SID, 'running');
    await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });
    const layout = await page.evaluate(() => document.documentElement.clientHeight);
    await keyboardUp(page, SID);
    const ms = await page.evaluate(() => new Promise((res) => {
      const t0 = performance.now();
      window.__vv.height = window.innerHeight;           // grows, NO event
      const tick = () => {
        const v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--mc-app-vh')) || 0;
        if (v >= document.documentElement.clientHeight - 2) return res(Math.round(performance.now() - t0));
        if (performance.now() - t0 > 3000) return res(-1);
        requestAnimationFrame(tick);
      };
      tick();
    }));
    check(ms >= 0 && ms <= 250, `vv grew with NO event, field still focused: full height after ${ms}ms (budget 250ms; the old watchdog was 500ms)`,
          `vv grew with NO event: recovery took ${ms}ms (budget 250ms)`);
    check(Math.abs((await appVh(page)) - layout) <= 2, 'and it stayed there');
  });
}

// ── 4. Window resized back up while vv stays STALE (resizes-content WebView) ─
async function checkLayoutGrowthBeatsStaleVv() {
  await withPage(async (page) => {
    const SID = 'sess-stalevv';
    await seedSession(page, SID, 'running');
    await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });
    const layout = await page.evaluate(() => document.documentElement.clientHeight);
    // Keyboard up in resizes-content mode: the whole window shrinks.
    await page.setViewportSize({ width: VW, height: VH - KB });
    await page.evaluate((sid) => {
      document.getElementById(`agent-followup-${sid}`).focus();
      window.__vv.height = window.innerHeight; window.__vv.dispatchEvent(new Event('resize'));
      // Observer registered AFTER mobile.js's own handler: sees the var as that
      // handler left it, at the moment of the resize event.
      window.__atResize = [];
      window.addEventListener('resize', () => window.__atResize.push({ ih: window.innerHeight, vh: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--mc-app-vh')) }));
    }, SID);
    await page.waitForTimeout(250);
    const shrunk = await appVh(page);
    check(shrunk < layout - 100, `[stale-vv] window shrank with the keyboard (--mc-app-vh=${shrunk})`);
    // Keyboard goes: window grows back, focus stays, vv NEVER updates or fires.
    await page.setViewportSize({ width: VW, height: VH });
    await page.waitForFunction((full) => window.__atResize.some((r) => r.ih >= full - 2), layout, { timeout: 2000 });
    const atResize = await page.evaluate((full) => window.__atResize.find((r) => r.ih >= full - 2).vh, layout);
    check(Math.abs(atResize - layout) <= 2, `[stale-vv] at the window 'resize' event itself the pane is already full (${atResize}), vv still stale at ${await page.evaluate(() => window.__vv.height)}`,
          `[stale-vv] pane NOT full at the window resize (${atResize}, want ${layout}) — a lagging vv.height is deciding the height`);
    await page.waitForTimeout(700);
    check(Math.abs((await appVh(page)) - layout) <= 2, '[stale-vv] still full 700ms later with no tap and no event (no vv, no send, no status patch needed)');
  });
}

// ── 5. A live keyboard is NOT yanked down (c90513b1's invariant) ────────────
async function checkOpenKeyboardSurvivesQuietPause() {
  await withPage(async (page) => {
    const SID = 'sess-quiet';
    await seedSession(page, SID, 'running');
    await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });
    const layout = await page.evaluate(() => document.documentElement.clientHeight);
    await keyboardUp(page, SID);
    const open = await appVh(page);
    await page.waitForTimeout(4500);   // a reading pause; the 100ms poll runs ~45 times
    check(Math.abs((await appVh(page)) - open) <= 2 && open < layout - 100,
          `open keyboard left quiet for 4.5s keeps the composer above it (--mc-app-vh ${open} -> ${await appVh(page)})`);
    const bottom = await composerBottom(page, SID);
    check(bottom <= layout - KB + 4, `composer bottom ${bottom}px stays above the keyboard top (${layout - KB}px)`);
    // a status patch under a live typist must not drop it either
    await page.evaluate((sid) => { const ta = document.getElementById(`agent-followup-${sid}`); ta.dispatchEvent(new Event('input', { bubbles: true })); window.updateAgentStatusUI(sid, 'running'); }, SID);
    await page.waitForTimeout(150);
    check((await appVh(page)) < layout - 100, 'a status patch while typing leaves the keyboard-height pane alone');
  });
}

// ── 6. Browser pane open ────────────────────────────────────────────────────
async function checkBrowserPaneOpen() {
  await withPage(async (page) => {
    const SID = 'sess-pane';
    await seedSession(page, SID, 'running');
    await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });
    const layout = await page.evaluate(() => document.documentElement.clientHeight);
    await page.waitForFunction(() => typeof window.openBrowserPane === 'function');
    await page.evaluate(() => window.openBrowserPane('about:blank', 'p1'));
    await page.locator('#mc-browser-pane [data-bp="ime-shadow"]').waitFor({ state: 'attached' });
    await page.waitForTimeout(500);
    // Keyboard up with focus INSIDE the pane (its hidden typing input).
    await page.evaluate((kb) => {
      document.querySelector('#mc-browser-pane [data-bp="ime-shadow"]').focus();
      window.__vv.height = window.innerHeight - kb; window.__vv.dispatchEvent(new Event('resize'));
    }, KB);
    await page.waitForTimeout(250);
    const r = await dismissSync(page);
    check(r.focusKept, '[browser pane] focus stayed on the pane typing input');
    check(Math.abs(r.appVh - layout) <= 2, `[browser pane] --mc-app-vh=${r.appVh} in the same task as the dismiss (want ${layout})`,
          `[browser pane] pane open: --mc-app-vh=${r.appVh} after dismiss, want ${layout}`);
    await page.waitForTimeout(120);
    const paneH = await page.evaluate(() => Math.round(document.querySelector('#mc-browser-pane').getBoundingClientRect().height));
    check(Math.abs(paneH - layout) <= 4, `[browser pane] the pane itself is full height (${paneH}px of ${layout}px)`,
          `[browser pane] pane is ${paneH}px of ${layout}px after the dismiss`);
    // The chat underneath must have been re-laid out too (it is what the user
    // sees the moment they hide the pane).
    const bottom = await composerBottom(page, SID);
    check(bottom >= layout - 140, `[browser pane] the chat under it is full height too (composer bottom ${bottom}px of ${layout}px)`,
          `[browser pane] the chat under the pane stayed short (composer bottom ${bottom}px of ${layout}px)`);
  });
}

// ── 7. Learn bubble open ────────────────────────────────────────────────────
async function checkLearnBubbleOpen() {
  await withPage(async (page) => {
    const SID = 'sess-learn';
    await seedSession(page, SID, 'running');
    await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });
    const layout = await page.evaluate(() => document.documentElement.clientHeight);
    await page.waitForFunction(() => window.LearnEngine && typeof window.LearnEngine.start === 'function');
    await page.evaluate(() => window.LearnEngine.start('floor-v1', 'test'));
    await page.waitForSelector('#lrn-bubble', { state: 'attached', timeout: 8000 });
    await page.waitForTimeout(400);
    const bubbleUp = await page.evaluate(() => { const b = document.getElementById('lrn-bubble'); return !!b && b.getBoundingClientRect().height > 0; });
    check(bubbleUp, '[learn] the Learn bubble is on screen');
    // Make sure the chat is still the open modal under the lesson.
    const hasField = await page.evaluate((sid) => !!document.getElementById(`agent-followup-${sid}`), SID);
    if (!hasField) { fail('[learn] the lesson tore down the chat under it — cannot test dismiss in this context'); return; }
    await keyboardUp(page, SID);
    check((await appVh(page)) < layout - 100, '[learn] baseline: pane shrank for the open keyboard with the bubble up');
    await assertDismissInFrame(page, SID, layout, '[learn] dismiss with the bubble open');
  });
}

try {
  await checkAfterSend('running');
  await checkAfterSend('idle');
  await checkNoEventLatency();
  await checkLayoutGrowthBeatsStaleVv();
  await checkOpenKeyboardSurvivesQuietPause();
  await checkBrowserPaneOpen();
  await checkLearnBubbleOpen();
} catch (e) {
  console.error(e);
  bad++;
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(bad ? 1 : 0);

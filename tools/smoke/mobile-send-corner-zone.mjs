#!/usr/bin/env node
/**
 * "Split screen is back" + "Send needs two taps" — the invisible corner-resize zone.
 *
 * ROOT CAUSE (4th recurrence, 2026-10-02, Galaxy Z Fold cover screen)
 * ------------------------------------------------------------------
 * interactions.js claimed any single-finger touchstart within 40px of
 * .modal-content's bottom-right corner as a "corner resize" (e.preventDefault()).
 * On the phone layout that grip is invisible (`::after {display:none}`,
 * `resize:none`) but the Send button lives exactly there. Two consequences:
 *   1. preventDefault() on touchstart cancels the synthesized click — a tap on the
 *      low/right part of Send did nothing ("Send needs two taps").
 *   2. a thumb that rolled ~15px while pressing it fired the touchmove branch,
 *      which writes an INLINE px height on .modal-content. Inline beats the
 *      `height: calc(var(--mc-app-vh) - 52px)` rule, so the sheet froze at the
 *      keyboard-open height for good while --mc-app-vh recovered to full — the
 *      "split screen". Every earlier fix chased --mc-app-vh and so could not see it.
 *
 * Verified first on a real Android WebView (tools/mobile-test/split4.js
 * sendZoneTap / sendZoneDrift); this is the hermetic regression guard.
 *
 * RUN   node tools/smoke/mobile-send-corner-zone.mjs
 * Exit  0 = fixed; 1 = regression.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const CLAYDO_IDLE = readFileSync(resolve(REPO_ROOT, 'assets', 'claydo-idle.webp'));
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_corner_zone';
const SID = 'sess-corner';
const KB = 380;

const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));

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
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Corner Zone Smoke'), fixtureProject('clayrune', 'Clayrune', { _is_onboarding_project: true })]);
const FLOOR_JSON = JSON.stringify({ rooms: [], quiet: [], bench: [], counts: { rooms: 0, figures: 0, quiet: 0, bench: 0 }, activity_states: false, poll_seconds: 5 });

const INSTALL_FAKE_VV = `
  (() => {
    const t = new EventTarget();
    t.height = window.innerHeight; t.width = window.innerWidth; t.offsetTop = 0; t.scale = 1;
    Object.defineProperty(window, 'visualViewport', { value: t, configurable: true });
    window.__vv = t;
    if (!sessionStorage.getItem('__seeded')) { sessionStorage.setItem('__seeded', '1'); localStorage.setItem('walkthrough_done', '1'); }
    // Last listener in the bubble phase: sees defaultPrevented after every handler ran.
    window.__ts = [];
    window.addEventListener('touchstart', e => window.__ts.push({ dp: e.defaultPrevented, tgt: (e.target.className || e.target.tagName || '') + '' }), { capture: false, passive: true });
  })();
`;

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, pass, failMsg) => (cond ? ok(pass) : fail(failMsg || pass));

let sendPosts = 0;
async function withPage(viewport, run) {
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ viewport, hasTouch: true });
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
      if (path === `/api/project/${PID}/agent/send`) { sendPosts++; return new Promise(() => {}); }
      if (path.startsWith('/api/browser/')) return json({ ok: true, sessions: [], profiles: [] });
      return route.abort();
    });
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
    await run(page, ctx);
    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource|mermaid|dynamically imported/i.test(e));
    uncaught.forEach((e) => fail('uncaught page error: ' + e));
    await ctx.close();
  } finally {
    await browser.close();
  }
}

function seedSession(page) {
  return page.evaluate(({ pid, sid }) => {
    const status = 'idle';
    agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Corner Zone Smoke', task: 'a turn', status, startedAt: new Date().toISOString() });
    agentStatusCache[sid] = { status, task: 'a turn', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: 'csid-' + sid, character: null };
    agentOutputBuffers[sid] = ['> earlier message', 'agent: a reply'];
    conversationsCache[pid] = [{ claude_session_id: 'csid-' + sid, mc_session_id: sid, character: null, mtime: 1000, ts_relative: 'just now', status, turns: 1, label: 'a turn', first_user: 'earlier', last_user: 'earlier' }];
    openProjectModal(pid);
    openConversation(pid, 'csid-' + sid, sid, true);
  }, { pid: PID, sid: SID });
}

// Keyboard up the way the platform does it: focus + text + vv shrink.
function keyboardUp(page) {
  return page.evaluate(({ sid, kb }) => {
    const ta = document.getElementById(`agent-followup-${sid}`);
    ta.focus(); ta.value = 'a follow-up'; ta.dispatchEvent(new Event('input', { bubbles: true }));
    window.__vv.height = window.innerHeight - kb;
    window.__vv.dispatchEvent(new Event('resize'));
  }, { sid: SID, kb: KB });
}

const geom = (page) => page.evaluate(() => {
  const c = document.querySelector('.modal-content').getBoundingClientRect();
  const s = document.querySelector('.composer-action .btn-send-arrow, .btn-send-arrow').getBoundingClientRect();
  return { cr: c.right, cb: c.bottom, sl: s.left, sr: s.right, st: s.top, sb: s.bottom };
});

// A touch with real input events (CDP), optionally drifting — a thumb rolls.
async function touch(ctx, page, x, y, drift = 0) {
  const cdp = await ctx.newCDPSession(page);
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
  if (drift) {
    for (let i = 1; i <= 6; i++) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y: y - (drift * i) / 6 }] });
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await page.waitForTimeout(150);
  await cdp.detach();
}

// ── Phone layout ───────────────────────────────────────────────────────────
async function phone() {
  console.log('phone layout (412x883)');
  await withPage({ width: 412, height: 883 }, async (page, ctx) => {
    await seedSession(page);
    await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });
    const layout = await page.evaluate(() => document.documentElement.clientHeight);
    await keyboardUp(page);
    await page.waitForTimeout(300);

    const g = await geom(page);
    // The tap point: lower-right of Send, but still ON the button.
    const x = Math.min(g.sr - 4, g.cr - 30), y = Math.min(g.sb - 3, g.cb - 30);
    check(x >= g.sl && y >= g.st && x > g.cr - 40 && y > g.cb - 40,
      `premise: (${Math.round(x)},${Math.round(y)}) is on Send AND inside the 40px corner zone (Send ${Math.round(g.sl)}-${Math.round(g.sr)} x ${Math.round(g.st)}-${Math.round(g.sb)}, corner ${Math.round(g.cr)},${Math.round(g.cb)})`);

    // 1. a tap there must send.
    sendPosts = 0;
    await page.evaluate(() => { window.__ts.length = 0; });
    await page.touchscreen.tap(x, y);
    await page.waitForTimeout(500);
    const ts = await page.evaluate(() => window.__ts.slice());
    check(ts.length > 0 && ts.every((t) => t.dp === false), 'tap on Send inside the corner zone: touchstart is NOT preventDefault()ed',
      'touchstart was preventDefault()ed over Send — the click is cancelled (Send needs two taps)');
    check(sendPosts === 1, 'tap on Send inside the corner zone POSTs once', `tap on Send inside the corner zone sent ${sendPosts} POSTs (want 1)`);

    // 2. a drifting thumb there must not pin the sheet's height inline.
    await page.evaluate(() => { document.querySelector('.modal-content').style.removeProperty('height'); });
    await keyboardUp(page);                       // composer was cleared by the send
    await page.waitForTimeout(300);
    const g2 = await geom(page);
    await touch(ctx, page, Math.min(g2.sr - 4, g2.cr - 30), Math.min(g2.sb - 3, g2.cb - 30), 24);
    const inline = await page.evaluate(() => document.querySelector('.modal-content').style.height || '');
    check(inline === '', 'drifting thumb in the corner zone leaves no inline height on .modal-content',
      `drifting thumb wrote inline height "${inline}" on .modal-content — outranks --mc-app-vh and pins the sheet`);

    // 3. keyboard dismissed: the sheet is full height (the screenshot: ends 2/3 down).
    await page.evaluate(() => { window.__vv.height = window.innerHeight; window.__vv.dispatchEvent(new Event('resize')); });
    await page.waitForTimeout(400);
    const after = await page.evaluate(() => Math.round(document.querySelector('.modal-content').getBoundingClientRect().bottom));
    check(Math.abs(after - layout) <= 2, `keyboard dismissed: sheet bottom ${after}px = screen ${layout}px`,
      `keyboard dismissed but the sheet ends at ${after}px of a ${layout}px screen (split screen)`);
  });
}

// ── Wider coarse-pointer window (tablet): the zone may stay, controls win ───
async function tablet() {
  console.log('wide touch window (1100x800)');
  await withPage({ width: 1100, height: 800 }, async (page, ctx) => {
    await seedSession(page);
    await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });
    const probe = await page.evaluate(() => {
      // The Claydo FAB floats over this corner; it is not what is under test.
      document.querySelectorAll('.claydo-fab').forEach((f) => { f.style.display = 'none'; });
      const c = document.querySelector('.modal-content');
      const r = c.getBoundingClientRect();
      const b = document.createElement('button');
      b.id = '__zone_btn'; b.textContent = 'x';
      b.style.cssText = 'position:absolute;right:6px;bottom:6px;width:28px;height:28px;z-index:99';
      c.appendChild(b);
      const br = b.getBoundingClientRect();
      b.remove();
      // A bare spot inside the 40px zone: nothing interactive under it.
      const ctl = 'input, textarea, select, button, a, label, [role="button"], [contenteditable]';
      let zx = null, zy = null;
      for (let dy = 3; dy < 38 && zx === null; dy += 3) {
        for (let dx = 3; dx < 38; dx += 3) {
          const px = r.right - dx, py = r.bottom - dy;
          const el = document.elementFromPoint(px, py);
          if (el && c.contains(el) && !el.closest(ctl)) { zx = px; zy = py; break; }
        }
      }
      c.appendChild(b);
      return { bx: br.left + br.width / 2, by: br.top + br.height / 2, zx, zy };
    });
    await page.evaluate(() => { window.__ts.length = 0; });
    await touch(ctx, page, probe.bx, probe.by);
    let ts = await page.evaluate(() => window.__ts.slice());
    check(ts.length > 0 && ts.every((t) => t.dp === false), 'wide window: a button inside the corner zone is not claimed by it',
      'wide window: touchstart on a button inside the corner zone was preventDefault()ed');
    await page.evaluate(() => { const b = document.getElementById('__zone_btn'); if (b) b.remove(); });
    check(probe.zx !== null, 'premise: a bare (control-free) spot exists inside the corner zone');
    await page.evaluate(() => { window.__ts.length = 0; });
    await touch(ctx, page, probe.zx, probe.zy);
    ts = await page.evaluate(() => window.__ts.slice());
    if (process.env.DEBUG_ZONE) console.log('corner ts', JSON.stringify(ts), JSON.stringify(probe), await page.evaluate((p) => { const e = document.elementFromPoint(p.zx, p.zy); return e ? e.tagName + '.' + e.className + '#' + e.id + ' | ' + (e.parentElement && e.parentElement.className) : null; }, probe));
    check(ts.length > 0 && ts.some((t) => t.dp === true), 'wide window: the bare corner still starts a resize (feature kept where the grip exists)',
      'wide window: the bare corner no longer starts a resize — the fix over-reached');
  });
}

await phone();
await tablet();
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(bad ? 1 : 0);

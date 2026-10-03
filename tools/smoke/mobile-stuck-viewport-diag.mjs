#!/usr/bin/env node
/**
 * MC-988 part 3 — self-triggering stuck-viewport diagnostic (backlog 40ff4ab5).
 *
 * WHY THIS EXISTS
 * ----------------
 * Ron's "half-height pane after Send" report (Galaxy Z Fold, OneUI, the
 * Clayrune Capacitor WebView) never reproduced in desktop emulation or on a
 * stock Pixel 6 AVD (docs/_journal/40ff4ab5-mc988-part2-mobile-viewport-repro.md
 * — both send paths recovered in 39ms/433ms there). It can only be caught
 * live, on Ron's device, so static/js/mobile.js now carries a self-triggering
 * diagnostic (no toggle, nothing for Ron to switch on): a 30-event ring
 * buffer of every viewport-affecting event, and a stuck detector riding the
 * existing 500ms watchdog that fires ONE POST to /api/diag/viewport if,
 * with no text field focused, --mc-app-vh stays short relative to the
 * layout viewport OR the layout viewport itself stays short relative to the
 * screen — for >=1.5s straight.
 *
 * This smoke forces the "outside the WebView" half of that OR: a layout
 * viewport (window.innerHeight) that is short relative to screen.availHeight
 * from the very first paint (no keyboard involved, nothing to recover from —
 * modelling a native WebView that was never resized back). It proves the
 * detector fires exactly once, with the fields the diagnosis needs, and never
 * fires again for the rest of the page's life once it has.
 *
 * Hermetic: real index.html + real static/js/*.js served verbatim, a real
 * (small) viewport plus a patched screen.availHeight via addInitScript — same
 * technique mobile-midturn-viewport.mjs uses for its fake visualViewport. No
 * real server, no network beyond the intercepted /api/diag/viewport POST.
 *
 * 2026-10-02 (MC-988 reopened): Ron reproduced the bug and data/diag/ stayed
 * empty. Two defects, both pinned here now:
 *   - TRIGGER: the detector reset whenever a field was focused, yet the known
 *     stuck shape is a FOCUSED composer. Scenario B drives that shape.
 *   - DELIVERY: sendBeacon bypasses the Capacitor shell's fetch wrapper (the
 *     one that adds the Cloudflare Access service-token headers). The client
 *     now uses fetch, posts a once-a-day 'armed' line so an empty file can no
 *     longer mean "pipeline broken", and replays a failed payload next load.
 *     Scenarios A (no sendBeacon, armed line) and C (failure + replay).
 *
 * RUN   node tools/smoke/mobile-stuck-viewport-diag.mjs
 * Exit  0 = every scenario behaves; 1 = a regression.
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
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_stuck_viewport_diag';
// A layout viewport short enough (vs. the patched screen.availHeight below)
// to trip the "outside" stuck condition (layoutH() < 0.75*availH) from the
// very first paint, with no keyboard/focus involved at all.
const VW = 412, VH = 400;
const FAKE_AVAIL_HEIGHT = 900; // 400 < 0.75*900=675 -> stuck

const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));

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
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Stuck Viewport Diag Smoke')]);

// Patches screen.availHeight (read-only on a real device) so the "outside"
// stuck condition — layoutH() short vs. the screen, independent of any
// keyboard/vv reading — is reproducible headless. visualViewport is left
// alone: this smoke models the native-WebView-never-resized-back case, not
// a keyboard-inset bug, so vv should behave like there is no keyboard at all.
const INSTALL_FAKE_SCREEN = `
  (() => {
    try {
      Object.defineProperty(window.screen, 'availHeight', { value: ${FAKE_AVAIL_HEIGHT}, configurable: true });
    } catch (e) { /* best-effort */ }
  })();
`;

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

// Same fake visualViewport the other mobile smokes use, plus a sendBeacon spy
// (the delivery fix is "never sendBeacon").
const INSTALL_FAKE_VV = `
  (() => {
    const t = new EventTarget();
    t.height = window.innerHeight; t.width = window.innerWidth; t.offsetTop = 0; t.scale = 1;
    Object.defineProperty(window, 'visualViewport', { value: t, configurable: true });
    window.__vv = t;
  })();
`;
const INSTALL_BEACON_SPY = `
  window.__beacons = 0;
  try { navigator.sendBeacon = function () { window.__beacons++; return true; }; } catch (e) {}
`;

// One page. `diagStatus` is a function so a scenario can flip delivery between
// loads (a failing tunnel, then a healthy one). Returns the recorded POSTs.
async function openSession(browser, { height, availHeight = 0, fakeVv = false, diagStatus = () => 204, ctx = null, page = null }) {
  const c = ctx || await browser.newContext({ viewport: { width: VW, height }, hasTouch: true });
  const pg = page || await c.newPage();
  const posts = [];
  const pageErrors = [];
  if (!page) {
    pg.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
    if (availHeight) await pg.addInitScript(`try { Object.defineProperty(window.screen, 'availHeight', { value: ${availHeight}, configurable: true }); } catch (e) {}`);
    if (fakeVv) await pg.addInitScript(INSTALL_FAKE_VV);
    await pg.addInitScript(INSTALL_BEACON_SPY);
    await pg.route('**/*', (route) => {
      const req = route.request();
      const path = new URL(req.url()).pathname;
      if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
      const hit = STATIC[path];
      if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
      if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
      if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      if (path === '/api/diag/viewport') {
        try { posts.push(JSON.parse(req.postData() || '{}')); } catch (e) { posts.push({ __parseError: String(e) }); }
        const st = diagStatus();
        return route.fulfill({ status: st, contentType: 'application/json', body: st === 204 ? '' : '{"ok":false}' });
      }
      return route.abort();
    });
  }
  await pg.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await pg.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  return { ctx: c, page: pg, posts, pageErrors };
}
const stuckPosts = (posts) => posts.filter((b) => b.kind === 'stuck' && !b.replayed);
const armedPosts = (posts) => posts.filter((b) => b.kind === 'armed');
const noRealErrors = (errs) => errs.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource|mermaid/i.test(e));

// ── A. The "outside the WebView" case — and the delivery contract ───────────
async function scenarioA(browser) {
  console.log('A. layout short vs. the screen (native shell never resized back)');
  const s = await openSession(browser, { height: VH, availHeight: FAKE_AVAIL_HEIGHT });
  const { page, posts } = s;
  const precheck = await page.evaluate(() => ({
    layout: Math.max(window.innerHeight || 0, document.documentElement.clientHeight || 0),
    availHeight: (window.screen && window.screen.availHeight) || 0,
    activeIsField: !!document.activeElement && (document.activeElement.tagName === 'TEXTAREA' || document.activeElement.tagName === 'INPUT'),
  }));
  (precheck.availHeight === FAKE_AVAIL_HEIGHT && precheck.layout < 0.75 * precheck.availHeight && !precheck.activeIsField)
    ? ok(`precondition holds: layout=${precheck.layout} < 0.75*availHeight(${precheck.availHeight}), no field focused`)
    : fail(`precondition does not hold (layout=${precheck.layout}, availHeight=${precheck.availHeight}) — test setup is wrong`);

  // 1.5s of continuous stuck readings, then the 3s boot ping for 'armed'.
  await page.waitForTimeout(4500);

  const stuck = stuckPosts(posts);
  (stuck.length === 1)
    ? ok(`exactly one stuck snapshot fired (got ${stuck.length})`)
    : fail(`expected exactly one stuck snapshot, got ${stuck.length}`);
  if (stuck.length >= 1) {
    const body = stuck[0];
    const hasEvents = Array.isArray(body.events) && body.events.length > 0;
    hasEvents ? ok(`payload carries a non-empty ring buffer (${body.events.length} event(s))`) : fail(`payload.events missing or empty: ${JSON.stringify(body.events)}`);
    const ev0 = hasEvents ? body.events[0] : {};
    const evFields = ['ts', 'source', 'innerHeight', 'clientHeight', 'outerHeight', 'screenHeight', 'screenAvailHeight', 'vvHeight', 'vvOffsetTop', 'appVh', 'lastApplied'];
    const missingEv = evFields.filter((f) => !(f in ev0));
    missingEv.length === 0 ? ok('ring buffer entries carry all expected fields') : fail(`ring buffer entry missing fields: ${missingEv.join(', ')}`);
    const cur = body.current || {};
    const missingCur = ['innerHeight', 'clientHeight', 'outerHeight', 'screenHeight', 'screenAvailHeight', 'vvHeight', 'vvOffsetTop', 'appVh', 'lastApplied'].filter((f) => !(f in cur));
    missingCur.length === 0 ? ok('current snapshot carries all expected fields') : fail(`current snapshot missing fields: ${missingCur.join(', ')}`);
    (cur.screenAvailHeight === FAKE_AVAIL_HEIGHT) ? ok(`current.screenAvailHeight reflects the patched screen (${cur.screenAvailHeight})`) : fail(`current.screenAvailHeight is ${cur.screenAvailHeight}`);
    ('modalHeight' in body && 'devicePixelRatio' in body && typeof body.ua === 'string' && body.ua.length > 0 && typeof body.capacitor === 'boolean')
      ? ok('payload carries modalHeight, devicePixelRatio, ua, capacitor') : fail('payload missing modalHeight/devicePixelRatio/ua/capacitor');
    (body.reason === 'stuck' && typeof body.fieldFocused === 'boolean' && 'vvTrusted' in body && 'sinceFieldActivityMs' in body)
      ? ok(`payload names its trigger and the focus/trust state (reason=${body.reason}, fieldFocused=${body.fieldFocused}, vvTrusted=${body.vvTrusted})`)
      : fail(`payload missing reason/fieldFocused/vvTrusted/sinceFieldActivityMs: ${JSON.stringify({ r: body.reason, f: body.fieldFocused, t: body.vvTrusted })}`);
  }
  const armed = armedPosts(posts);
  (armed.length === 1 && armed[0].capacitor === false && typeof armed[0].ua === 'string')
    ? ok('exactly one "armed" line posted (the client proves the pipeline works from THIS client)')
    : fail(`expected exactly one armed line, got ${armed.length}`);
  const beacons = await page.evaluate(() => window.__beacons);
  (beacons === 0) ? ok('sendBeacon was never used (fetch is the path the native shell wraps)') : fail(`sendBeacon was called ${beacons} time(s) — it bypasses the shell's credential-injecting fetch wrapper`);

  await page.waitForTimeout(1500);
  (stuckPosts(posts).length === 1)
    ? ok('still exactly one stuck snapshot after the condition persists (cooldown holds)')
    : fail(`cooldown did not hold, got ${stuckPosts(posts).length} stuck snapshots`);
  noRealErrors(s.pageErrors).forEach((e) => fail('uncaught page error: ' + e));
  await s.ctx.close();
}

// ── B. A FOCUSED composer that never takes the room it is offered ───────────
// The exact shape the first detector excluded by construction. A backgrounded
// resume leaves vv untrusted (forceFull); a vv reading taller than the layout
// is "room" the pane will not take, so it stays unclaimed with the field focused.
async function scenarioB(browser) {
  console.log('B. focused field, room offered but not taken');
  const s = await openSession(browser, { height: 883, fakeVv: true });
  const { page, posts } = s;
  await page.evaluate(() => {
    const ta = document.createElement('textarea'); ta.id = 'diag-field'; document.body.appendChild(ta); ta.focus();
    document.dispatchEvent(new Event('visibilitychange'));      // forceFull -> vv untrusted
    window.__vv.height = window.innerHeight + 300;               // vv claims more than the layout; NO event
  });
  const focused = await page.evaluate(() => document.activeElement && document.activeElement.id === 'diag-field');
  focused ? ok('precondition: a text field holds focus') : fail('precondition: field did not take focus');
  await page.waitForTimeout(2800);
  const stuck = stuckPosts(posts);
  (stuck.length === 1 && stuck[0].fieldFocused === true)
    ? ok(`the stuck snapshot fires WITH a field focused (fieldFocused=${stuck[0].fieldFocused}) — the old detector could not`)
    : fail(`expected one stuck snapshot with fieldFocused=true, got ${stuck.length} (${stuck.map((b) => b.fieldFocused)})`);
  noRealErrors(s.pageErrors).forEach((e) => fail('uncaught page error: ' + e));
  await s.ctx.close();
}

// ── C. Delivery fails (tunnel bounce), recovers next load ───────────────────
async function scenarioC(browser) {
  console.log('C. delivery failure keeps the evidence and replays it');
  let status = 502;
  const s = await openSession(browser, { height: VH, availHeight: FAKE_AVAIL_HEIGHT, diagStatus: () => status });
  const { page, posts, ctx } = s;
  await page.waitForTimeout(4500);
  const pend = await page.evaluate(() => ({ pending: !!localStorage.getItem('mc_vp_diag_pending'), armed: !!localStorage.getItem('mc_vp_diag_armed') }));
  (posts.length >= 2 && pend.pending)
    ? ok(`both posts were attempted and refused; the stuck payload is kept for replay (attempts=${posts.length})`)
    : fail(`expected >=2 refused attempts and a stored pending payload, got attempts=${posts.length} pending=${pend.pending}`);
  (!pend.armed)
    ? ok('a refused "armed" ping does not mark the device armed (it will retry)')
    : fail('a refused armed ping was recorded as delivered');
  status = 204;
  posts.length = 0;
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.waitForTimeout(4500);
  const replayed = posts.filter((b) => b.replayed === true && b.kind === 'stuck');
  (replayed.length === 1)
    ? ok('the next load replays the stored snapshot (replayed=true) once the endpoint answers')
    : fail(`expected one replayed snapshot, got ${replayed.length}`);
  (armedPosts(posts).length === 1) ? ok('and the armed ping lands this time') : fail(`expected the armed ping on the healthy load, got ${armedPosts(posts).length}`);
  const left = await page.evaluate(() => !!localStorage.getItem('mc_vp_diag_pending'));
  (!left) ? ok('the pending payload is cleared after replay') : fail('pending payload still stored after replay');
  noRealErrors(s.pageErrors).forEach((e) => fail('uncaught page error: ' + e));
  await ctx.close();
}

async function main() {
  const browser = await chromium.launch();
  try {
    await scenarioA(browser);
    await scenarioB(browser);
    await scenarioC(browser);
  } finally {
    await browser.close();
  }
}

try {
  await main();
} catch (e) {
  console.error(e);
  bad++;
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(bad ? 1 : 0);

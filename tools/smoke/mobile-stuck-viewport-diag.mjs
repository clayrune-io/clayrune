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
 * RUN   node tools/smoke/mobile-stuck-viewport-diag.mjs
 * Exit  0 = exactly one POST fires, with the expected fields; 1 = a regression.
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
const PID = 'smoke_stuck_viewport_diag';
// A layout viewport short enough (vs. the patched screen.availHeight below)
// to trip the "outside" stuck condition (layoutH() < 0.75*availH) from the
// very first paint, with no keyboard/focus involved at all.
const VW = 412, VH = 400;
const FAKE_AVAIL_HEIGHT = 900; // 400 < 0.75*900=675 -> stuck

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

async function main() {
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ viewport: { width: VW, height: VH }, hasTouch: true });
    const page = await ctx.newPage();
    await page.addInitScript(INSTALL_FAKE_SCREEN);
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

    const diagPosts = [];
    await page.route('**/*', (route) => {
      const req = route.request();
      const path = new URL(req.url()).pathname;
      if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
      const hit = STATIC[path];
      if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
      if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
      if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      if (path === '/api/diag/viewport') {
        try { diagPosts.push(JSON.parse(req.postData() || '{}')); } catch (e) { diagPosts.push({ __parseError: String(e) }); }
        return route.fulfill({ status: 204, contentType: 'application/json', body: '' });
      }
      return route.abort();
    });

    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });

    // Sanity: the stuck precondition actually holds as the test intends
    // (layout short vs. patched screen, no field focused) before waiting on
    // the detector — otherwise a false pass would tell us nothing.
    const precheck = await page.evaluate(() => ({
      layout: Math.max(window.innerHeight || 0, document.documentElement.clientHeight || 0),
      availHeight: (window.screen && window.screen.availHeight) || 0,
      activeIsField: !!document.activeElement && (document.activeElement.tagName === 'TEXTAREA' || document.activeElement.tagName === 'INPUT'),
    }));
    (precheck.availHeight === FAKE_AVAIL_HEIGHT && precheck.layout < 0.75 * precheck.availHeight && !precheck.activeIsField)
      ? ok(`precondition holds: layout=${precheck.layout} < 0.75*availHeight(${precheck.availHeight}), no field focused`)
      : fail(`precondition does not hold (layout=${precheck.layout}, availHeight=${precheck.availHeight}, activeIsField=${precheck.activeIsField}) — test setup is wrong`);

    // The detector rides the existing 500ms watchdog and requires >=1.5s of
    // continuous stuck readings before it fires — wait comfortably past that.
    await page.waitForTimeout(2500);

    (diagPosts.length === 1)
      ? ok(`exactly one /api/diag/viewport POST fired (got ${diagPosts.length})`)
      : fail(`expected exactly one /api/diag/viewport POST, got ${diagPosts.length}`);

    if (diagPosts.length >= 1) {
      const body = diagPosts[0];
      const hasEvents = Array.isArray(body.events) && body.events.length > 0;
      hasEvents
        ? ok(`payload carries a non-empty ring buffer (${body.events.length} event(s))`)
        : fail(`payload.events missing or empty: ${JSON.stringify(body.events)}`);

      const ev0 = hasEvents ? body.events[0] : {};
      const evFields = ['ts', 'source', 'innerHeight', 'clientHeight', 'outerHeight', 'screenHeight', 'screenAvailHeight', 'vvHeight', 'vvOffsetTop', 'appVh', 'lastApplied'];
      const missingEvFields = evFields.filter(f => !(f in ev0));
      missingEvFields.length === 0
        ? ok(`ring buffer entries carry all expected fields (${evFields.join(', ')})`)
        : fail(`ring buffer entry missing fields: ${missingEvFields.join(', ')}`);

      const cur = body.current || {};
      const curFields = ['innerHeight', 'clientHeight', 'outerHeight', 'screenHeight', 'screenAvailHeight', 'vvHeight', 'vvOffsetTop', 'appVh', 'lastApplied'];
      const missingCurFields = curFields.filter(f => !(f in cur));
      missingCurFields.length === 0
        ? ok(`current snapshot carries all expected fields (${curFields.join(', ')})`)
        : fail(`current snapshot missing fields: ${missingCurFields.join(', ')}`);

      (cur.screenAvailHeight === FAKE_AVAIL_HEIGHT)
        ? ok(`current.screenAvailHeight reflects the patched screen (${cur.screenAvailHeight})`)
        : fail(`current.screenAvailHeight is ${cur.screenAvailHeight}, want ${FAKE_AVAIL_HEIGHT}`);

      ('modalHeight' in body)
        ? ok(`payload carries modalHeight (${JSON.stringify(body.modalHeight)})`)
        : fail('payload missing modalHeight');
      ('devicePixelRatio' in body)
        ? ok(`payload carries devicePixelRatio (${JSON.stringify(body.devicePixelRatio)})`)
        : fail('payload missing devicePixelRatio');
      (typeof body.ua === 'string' && body.ua.length > 0)
        ? ok(`payload carries a non-empty UA string`)
        : fail(`payload.ua missing or empty: ${JSON.stringify(body.ua)}`);
      (typeof body.capacitor === 'boolean')
        ? ok(`payload carries capacitor flag (${body.capacitor})`)
        : fail(`payload.capacitor is not a boolean: ${JSON.stringify(body.capacitor)}`);
    }

    // The stuck condition is still true (nothing changed since); the once-
    // per-page-load latch must hold anyway.
    await page.waitForTimeout(1500);
    (diagPosts.length === 1)
      ? ok(`still exactly one POST after the stuck condition persists further (latched, not re-fired)`)
      : fail(`expected the once-per-page-load latch to hold, got ${diagPosts.length} POST(s)`);

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource|mermaid/i.test(e));
    uncaught.forEach((e) => fail('uncaught page error: ' + e));
    await ctx.close();
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

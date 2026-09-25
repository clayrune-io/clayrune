#!/usr/bin/env node
/**
 * Mobile modal header: Minimize -> Back, everywhere (2026-09-24, Ron's call).
 *
 * WHY THIS EXISTS
 * ----------------
 * Commit 78d10eb gave Claydo's mobile header a Back button (closeModalById)
 * mirroring the Android hardware-back key, hand-built as a Claydo-only
 * special case (`_claydoSecondBtn` in claydo.js). Ron: every modal's header
 * button next to X should do this, not just Claydo's.
 *
 * The fix is centralized, not 20 template edits:
 *  - mobile.js: `_mcApplyMobileModalHeader(win)` finds the `.modal-minimize`
 *    button inside a newly-mounted `.modal-window`, and on mobile swaps its
 *    icon/title to Back and rewires its onclick to `mcModalHeaderBack`, which
 *    walks the SAME sentinel stack the popstate handler reads
 *    (index.html ~1391) in the same priority order: history.back() if a
 *    sentinel is live (so popstate does the exact partial-unwind hardware
 *    back would), closeModalById as the safe fallback otherwise. It also
 *    pushes a surface-history sentinel (mcPushSurfaceHistory) for any
 *    `__`-prefixed modal that doesn't already register one of its own
 *    (project modals push their own via openProjectModal; __settings owns
 *    its dedicated nav-depth stack) — before this, hardware back did nothing
 *    for most `__`-surfaces (Skills/MCP/Backlog/Floor/...), only Claydo and
 *    the new-project form opted in by hand.
 *  - interactions.js: the existing `_mcInitModalResize` MutationObserver on
 *    #modal-layer (already there to attach resize handles to every mounted
 *    modal regardless of which of the ~25 modules built it) is the one hook
 *    that calls `_mcApplyMobileModalHeader` — so every call site is covered
 *    without editing any of them.
 *  - claydo.js: the special case is gone; Claydo renders a plain Minimize
 *    button and gets Back from the same mechanism as everyone else.
 *  - modal-manager.js: closing a `__`-surface via the button/hardware-back
 *    now genuinely goes through closeModalById directly (popstate's
 *    _mcSurfaceOpen branch always did), which surfaced a real gap for the
 *    Floor (closeFloor's clearInterval lived only in its own X handler,
 *    never in closeModalById) — fixed by exposing `_floorTeardown` and
 *    calling it from closeModalById, same pattern as the existing Beacon
 *    SSE teardown.
 *
 * Covers: Claydo, a project modal, the Floor, and one cross-* modal
 * (All Backlog). The Inbox is NOT a `.modal-window` (its own `.mib-back`
 * button + `_mcInboxOpen` sentinel predate this work) — checked only for a
 * no-regression smoke, not converted.
 *
 * Also asserts desktop (1280px) is untouched: Minimize stays Minimize and
 * still minimizes instead of closing, for both a `__`-surface and a project
 * modal.
 *
 * Hermetic: real index.html + every real static/js/*.js + static/css/*.css
 * served verbatim (same technique as claydo-mobile.mjs / mobile-send-height-
 * restore.mjs). No server, no network, no MC state.
 *
 * RUN   node tools/smoke/mobile-modal-back.mjs
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
const PID = 'smoke_mobile_modal_back';
const VW_MOBILE = 390, VH_MOBILE = 844;   // Android UA-ish, CSS px (task spec: 390x844)
const VW_DESKTOP = 1280, VH_DESKTOP = 800;

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
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Mobile Modal Back Smoke')]);

const ANDROID_UA = 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36';

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

async function routeCommon(page) {
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/floor') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    return route.abort();
  });
}

function readHeaderBtn(modalId) {
  const b = document.querySelector(`#modal-layer .modal-window[data-modal-id="${modalId}"] .modal-header .modal-minimize`);
  return b ? { title: b.title, text: b.textContent.trim() } : null;
}

function clickHeaderBtn(modalId) {
  document.querySelector(`#modal-layer .modal-window[data-modal-id="${modalId}"] .modal-header .modal-minimize`).click();
}

// Generic scenario: open a modal, assert the header button, click it, assert
// the SAME effect a hardware back press would have (mobile), or that it
// still just minimizes (desktop).
async function scenario(browser, { label, mobile, openExpr, modalId, expectHistoryEntry }) {
  const vp = mobile ? { width: VW_MOBILE, height: VH_MOBILE } : { width: VW_DESKTOP, height: VH_DESKTOP };
  const ctx = await browser.newContext({ viewport: vp, hasTouch: mobile, userAgent: mobile ? ANDROID_UA : undefined });
  const page = await ctx.newPage();
  await routeCommon(page);
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

  console.log(`\n[${label}]`);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector(mobile ? '#bottom-tab-bar' : '#claydo-fab', { timeout: 15000 });

  const startUrl = await page.evaluate(() => location.href);
  const startLen = await page.evaluate(() => history.length);

  await page.evaluate(openExpr, modalId);
  await page.waitForSelector(`#modal-layer .modal-window[data-modal-id="${modalId}"]`, { timeout: 5000 });

  const btn = await page.evaluate(readHeaderBtn, modalId);
  if (mobile) {
    (btn && btn.title === 'Back' && btn.text === '←')
      ? ok(`header shows Back (title="${btn?.title}", icon="${btn?.text}")`)
      : fail(`header did not show Back (got ${JSON.stringify(btn)})`);
  } else {
    (btn && btn.title === 'Minimize' && btn.text === '―')
      ? ok(`header still shows Minimize (title="${btn?.title}", icon="${btn?.text}")`)
      : fail(`header lost Minimize (got ${JSON.stringify(btn)})`);
  }

  if (mobile && expectHistoryEntry) {
    const pushed = await page.evaluate(() => history.length) > startLen;
    pushed
      ? ok('opening pushed a mobile back-stack entry (hardware back has something to consume)')
      : fail('opening did NOT push a back-stack entry — hardware back would do nothing');
  }

  await page.evaluate(clickHeaderBtn, modalId);
  await page.waitForTimeout(150);

  const stillOpen = await page.evaluate((id) => typeof openModals !== 'undefined' && openModals.has(id), modalId);
  if (mobile) {
    (!stillOpen)
      ? ok('clicking Back closed the modal (matches hardware back)')
      : fail('clicking Back left the modal open — openModals still has it');
    const backAtStart = await page.evaluate((u) => location.href === u, startUrl);
    backAtStart
      ? ok('back landed on the dashboard URL — no stray forward/blank navigation')
      : fail('URL after Back is not the pre-open URL — history got left in a weird spot');
  } else {
    const entry = await page.evaluate((id) => {
      const e = typeof openModals !== 'undefined' && openModals.get(id);
      return e ? { minimized: !!e.minimized } : null;
    }, modalId);
    (entry && entry.minimized)
      ? ok('clicking Minimize on desktop still minimizes (does not close)')
      : fail(`desktop Minimize did not minimize (got ${JSON.stringify(entry)})`);
  }

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail('uncaught page error: ' + e));

  await ctx.close();
}

// The Floor specifically: assert its 30s poll interval is torn down when
// closed via the (now-shared) Back button, not just via its own X/closeFloor.
async function testFloorTeardown(browser) {
  console.log('\n[Floor: poll teardown on Back]');
  const ctx = await browser.newContext({ viewport: { width: VW_MOBILE, height: VH_MOBILE }, hasTouch: true, userAgent: ANDROID_UA });
  const page = await ctx.newPage();
  await routeCommon(page);
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#bottom-tab-bar', { timeout: 15000 });
  await page.evaluate(() => openFloor());
  await page.waitForSelector('#modal-layer .modal-window[data-modal-id="__floor"]', { timeout: 5000 });

  const armedBefore = await page.evaluate(() => window._floorTimerArmed && window._floorTimerArmed());
  armedBefore
    ? ok('Floor poll timer is armed after open')
    : fail('Floor poll timer never armed — cannot test teardown');

  await page.evaluate(() => document.querySelector('#modal-layer .modal-window[data-modal-id="__floor"] .modal-header .modal-minimize').click());
  await page.waitForTimeout(150);

  const armedAfter = await page.evaluate(() => window._floorTimerArmed && window._floorTimerArmed());
  (!armedAfter)
    ? ok('Floor poll timer cleared after clicking Back (no leaked /api/floor poll)')
    : fail('Floor poll timer still set after Back — closeModalById skipped _floorTeardown');

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail('uncaught page error: ' + e));

  await ctx.close();
}

// Inbox regression check: NOT part of this mechanism (its own .mib-back +
// _mcInboxOpen predate it) — just confirm it still opens/closes cleanly
// alongside the new modal-layer observer logic.
async function testInboxRegression(browser) {
  console.log('\n[Inbox: no-regression check]');
  const ctx = await browser.newContext({ viewport: { width: VW_MOBILE, height: VH_MOBILE }, hasTouch: true, userAgent: ANDROID_UA });
  const page = await ctx.newPage();
  await routeCommon(page);
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#bottom-tab-bar', { timeout: 15000 });
  await page.evaluate(() => openInbox());
  await page.waitForSelector('#mobile-inbox.open', { timeout: 5000 });

  const back = await page.evaluate(() => {
    const b = document.querySelector('#mobile-inbox .mib-back');
    return b ? { title: b.getAttribute('aria-label'), text: b.textContent.trim() } : null;
  });
  back ? ok(`Inbox still has its own back affordance (${JSON.stringify(back)})`) : fail('Inbox back button missing');

  await page.evaluate(() => document.querySelector('#mobile-inbox .mib-back').click());
  await page.waitForTimeout(150);
  const closed = await page.evaluate(() => !document.getElementById('mobile-inbox').classList.contains('open'));
  closed ? ok('Inbox closes normally via its own back button') : fail('Inbox did not close');

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail('uncaught page error: ' + e));

  await ctx.close();
}

let browser;
let exitCode = 1;
try {
  browser = await chromium.launch();

  await scenario(browser, {
    label: 'Claydo (mobile): header is Back, closes on click',
    mobile: true, modalId: '__claydo', expectHistoryEntry: true,
    openExpr: () => openClaydo(),
  });
  await scenario(browser, {
    label: 'Claydo (desktop): header stays Minimize',
    mobile: false, modalId: '__claydo',
    openExpr: () => openClaydo(),
  });

  await scenario(browser, {
    label: 'Project modal (mobile): header is Back, closes on click',
    mobile: true, modalId: PID, expectHistoryEntry: true,
    openExpr: (id) => openProjectModal(id),
  });
  await scenario(browser, {
    label: 'Project modal (desktop): header stays Minimize',
    mobile: false, modalId: PID,
    openExpr: (id) => openProjectModal(id),
  });

  await scenario(browser, {
    label: 'The Floor (mobile): header is Back, closes on click',
    mobile: true, modalId: '__floor', expectHistoryEntry: true,
    openExpr: () => openFloor(),
  });
  await scenario(browser, {
    label: 'The Floor (desktop): header stays Minimize',
    mobile: false, modalId: '__floor',
    openExpr: () => openFloor(),
  });

  await scenario(browser, {
    label: 'Cross-* modal, All Backlog (mobile): header is Back, closes on click',
    mobile: true, modalId: '__all_backlog', expectHistoryEntry: true,
    openExpr: () => openAllBacklog(),
  });
  await scenario(browser, {
    label: 'Cross-* modal, All Backlog (desktop): header stays Minimize',
    mobile: false, modalId: '__all_backlog',
    openExpr: () => openAllBacklog(),
  });

  await testFloorTeardown(browser);
  await testInboxRegression(browser);

  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error(e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(exitCode);

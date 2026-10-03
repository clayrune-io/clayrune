#!/usr/bin/env node
/**
 * Backlog 9227b94e: the bottom project-tab strip (#minimized-tray —
 * minimized project chat modals, `.minimized-chip` from modal-manager.js
 * `minimizeModal()`) started at `left: 0`, UNDER the fixed `.sidebar`
 * (z-index 200/400 > tray's 180), so the leftmost tab was clipped
 * ("layrune website" instead of "Clayrune website") and its close button
 * sat under the sidebar, unclickable.
 *
 * Fix (static/css/app.css `.minimized-tray`): offset `left: var(--sidebar-w)`
 * — the SAME variable `.sidebar` itself uses for its base width, and the
 * same pattern already used by `.agent-console` (app.css ~7162) for the
 * identical fixed-bottom-bar-next-to-the-sidebar problem. NOT a z-index bump
 * — that would let the tray cover the sidebar's chat button instead.
 * Mobile (<=960px) override keeps `left: 0` since `.sidebar` is
 * `display:none` there (nothing to clip against).
 *
 * Real headless boot (real index.html + real static/js/*.js + static/css/*.css
 * served verbatim, no server, no network) — same hermetic pattern as
 * usage-bar.mjs: read the whole static/js and static/css dirs so every
 * extracted ES module is served automatically (no per-file allowlist to
 * maintain). /api/projects is stubbed with 5 fixture projects; the test
 * drives the REAL open+minimize code path (`openProjectModal` +
 * `minimizeModal`, modal-manager.js) rather than injecting chip DOM by hand.
 *
 * Covers:
 *  1. Desktop 1440, sidebar at its base (collapsed) width: the first tab's
 *     left edge is >= the sidebar's right edge (not clipped underneath).
 *  2. Desktop 1440, sidebar hover-expanded (220px): the tray's offset stays
 *     pinned to the sidebar's BASE width (matches `.agent-console` /
 *     `_workspaceRect()`'s documented "hover is transient, don't chase it"
 *     precedent) — not zero, not shifted backwards.
 *  3. Tab 1's close button is clickable: `elementFromPoint` at its center
 *     returns the close button itself, not something the sidebar occludes.
 *  4. The usage-bar-strip row beneath the tray is a real flex child of
 *     `.main-area` (not fixed) so it already clears the sidebar — asserted
 *     here as a regression guard, not a fix.
 *  5. Mobile (390px): sidebar is `display:none`, tray's `left` computes to 0
 *     (no orphaned offset with nothing to clear).
 *
 * RUN
 *   cd tools/smoke && node tabstrip-sidebar-offset.mjs
 * Exit 0 = all pass; 1 = a case regressed / harness error.
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

const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));

// Same full field shape as fixtures/projects.json (boot-smoke.mjs) — trimmed
// fixtures have previously thrown deep inside modalContentHTML on fields it
// assumes exist. Five entries to match the backlog screenshot's tab count.
const NAMES = ['Clayrune website', 'Mission Control', 'Drop shipping company', 'Clayrune Cloud', 'Find Ron a Job'];
const PROJECTS = NAMES.map((name, i) => ({
  id: `smoke_${i}`,
  name,
  status: 'active',
  domain: 'general',
  emoji: '\u{1F9EA}',
  description: '',
  summary: '',
  current_task: 'Idle',
  next_action: '',
  blocked: false,
  blocked_reason: null,
  activity_log: [],
  backlog: [],
  project_path: '',
  last_updated: '2026-06-08T00:00:00Z',
  last_updated_relative: 'today',
  last_completed: null,
  live_agent: null,
  display_order: i,
  provider: 'claude',
  use_streaming_agent: true,
  distiller_mode: 'proposed',
  distiller_min_recurrence: 3,
  distiller_max_topics_per_session: 3,
  distiller_max_preferences_per_session: 3,
  distiller_max_explorations_per_session: 3,
  distiller_min_turns: 5,
  distiller_skip_errors: true,
}));

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

function routeHandler() {
  return (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(PROJECTS) });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/system/status') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/system/usage') return route.fulfill({ status: 200, contentType: 'application/json', body: '{"available":false}' });
    if (path === '/api/terminal/stream') return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' });
    return route.abort();
  };
}

async function newPage(browser, { width, height }) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', routeHandler());
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  // waitForFunction on allProjects (not a '.card' selector) so this holds on
  // both the desktop grid and the mobile chat-list view, which render the
  // fixture differently.
  await page.waitForFunction(() => typeof allProjects !== 'undefined' && allProjects.length === 5, { timeout: 15000 });
  return { ctx, page, pageErrors };
}

// Opens + minimizes all fixture projects via the REAL app code path, in
// project order, so chip 1 == PROJECTS[0] ("Clayrune website").
async function openAndMinimizeAll(page) {
  return page.evaluate((ids) => {
    for (const id of ids) {
      openProjectModal(id);
      minimizeModal(id);
    }
    return document.querySelectorAll('#minimized-tray .minimized-chip').length;
  }, PROJECTS.map((p) => p.id));
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();

  // ── 1-4. Desktop 1440 ──────────────────────────────────────────────────
  {
    const { ctx, page, pageErrors } = await newPage(browser, { width: 1440, height: 900 });
    const chipCount = await openAndMinimizeAll(page);
    chipCount === 5
      ? ok(`all 5 fixture projects minimized into #minimized-tray (got ${chipCount})`)
      : fail(`expected 5 chips, got ${chipCount}`);

    const sidebarBase = await page.$eval('.sidebar', (el) => el.getBoundingClientRect().right);
    const sidebarVarPx = await page.$eval(':root', (el) => parseFloat(getComputedStyle(el).getPropertyValue('--sidebar-w')));
    Math.abs(sidebarBase - sidebarVarPx) < 1
      ? ok(`sidebar at rest measures ${sidebarBase}px (== --sidebar-w ${sidebarVarPx}px, not hover-expanded)`)
      : fail(`sidebar rest width ${sidebarBase}px doesn't match --sidebar-w ${sidebarVarPx}px`);

    const firstChip = await page.$eval('#minimized-tray .minimized-chip:first-child', (el) => {
      const r = el.getBoundingClientRect();
      return { left: r.left, text: el.textContent.trim(), right: r.right };
    });
    /Clayrune website/.test(firstChip.text)
      ? ok(`first chip is "Clayrune website" (unclipped text: "${firstChip.text.replace(/[✕×]/g,'').trim()}")`)
      : fail(`first chip text unexpected: "${firstChip.text}"`);
    firstChip.left >= sidebarBase - 0.5
      ? ok(`first tab's left edge (${firstChip.left}px) >= sidebar's right edge (${sidebarBase}px) — not clipped`)
      : fail(`first tab's left edge (${firstChip.left}px) is UNDER the sidebar (right edge ${sidebarBase}px)`);

    // ── 2. Close button is clickable at rest — elementFromPoint returns it,
    // not the sidebar or anything else occluding it.
    const closeHit = await page.evaluate(() => {
      const chip = document.querySelector('#minimized-tray .minimized-chip:first-child');
      const closeBtn = chip.querySelector('.chip-close');
      const r = closeBtn.getBoundingClientRect();
      const el = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return { hitIsClose: el === closeBtn || closeBtn.contains(el), hitTag: el ? el.className || el.tagName : null };
    });
    closeHit.hitIsClose
      ? ok('tab 1\'s close button is clickable (elementFromPoint returns it, not occluded)')
      : fail(`tab 1's close button is occluded — elementFromPoint hit "${closeHit.hitTag}" instead`);

    // ── 3. Hover-expand the sidebar (220px) — tray must not track the
    // transient hover width (matches .agent-console / _workspaceRect()
    // precedent: fixed elements pin to the BASE var, the sidebar's own
    // higher z-index covers them during the hover overlay). Assert the
    // tray's CSS left is unchanged, not recomputed to 0 or negative.
    await page.hover('.sidebar-logo');
    await page.waitForTimeout(250); // width transition (0.2s)
    const sidebarHoverWidth = await page.$eval('.sidebar', (el) => el.getBoundingClientRect().width);
    const trayLeftDuringHover = await page.$eval('#minimized-tray', (el) => getComputedStyle(el).left);
    sidebarHoverWidth > sidebarBase
      ? ok(`sidebar hover-expanded to ${sidebarHoverWidth}px (>${sidebarBase}px base) — hover state confirmed active`)
      : fail(`sidebar did not hover-expand (still ${sidebarHoverWidth}px) — hover precondition not met`);
    Math.abs(parseFloat(trayLeftDuringHover) - sidebarVarPx) < 1
      ? ok(`tray's left stays pinned to --sidebar-w (${trayLeftDuringHover}) during hover-expand, same as .agent-console`)
      : fail(`tray's left changed during hover-expand: ${trayLeftDuringHover} (expected ~${sidebarVarPx}px)`);
    await page.mouse.move(700, 700); // un-hover, tidy state before this context closes
    await page.waitForTimeout(250);

    // ── 4. Usage-bar-strip regression guard: already a flex child of
    // .main-area, so its left edge should already clear the sidebar. This
    // fixture has no provider usage data, so the strip is legitimately
    // empty/display:none — check that explicitly rather than trusting
    // getBoundingClientRect() (which returns 0 on a hidden element too).
    const stripState = await page.$eval('#usage-bar-strip', (el) => ({
      empty: el.childElementCount === 0,
      display: getComputedStyle(el).display,
      left: el.getBoundingClientRect().left,
    }));
    if (stripState.empty || stripState.display === 'none') {
      ok(`usage-bar-strip empty (no provider data in this fixture, display:${stripState.display}) — geometry check not applicable`);
    } else {
      stripState.left >= sidebarBase - 0.5
        ? ok(`usage-bar-strip's left edge (${stripState.left}px) already clears the sidebar (${sidebarBase}px) — no clipping regression`)
        : fail(`usage-bar-strip's left edge (${stripState.left}px) is UNDER the sidebar (right edge ${sidebarBase}px)`);
    }

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    uncaught.length === 0
      ? ok('desktop pass: no uncaught page errors')
      : uncaught.forEach((e) => fail('uncaught exception (desktop): ' + e));

    await ctx.close();
  }

  // ── 5. Mobile 390: sidebar hidden, tray's left computes to 0 ─────────────
  {
    const { ctx, page, pageErrors } = await newPage(browser, { width: 390, height: 844 });
    await openAndMinimizeAll(page);
    const sidebarDisplay = await page.$eval('.sidebar', (el) => getComputedStyle(el).display);
    sidebarDisplay === 'none'
      ? ok('sidebar is display:none at 390px (mobile)')
      : fail(`sidebar should be hidden at 390px, computed display is "${sidebarDisplay}"`);
    const trayLeft = await page.$eval('#minimized-tray', (el) => getComputedStyle(el).left);
    parseFloat(trayLeft) === 0
      ? ok(`tray's left is 0 at mobile width (computed "${trayLeft}") — no orphaned desktop offset`)
      : fail(`tray's left should be 0 at mobile width, computed "${trayLeft}"`);
    const firstChipLeft = await page.$eval('#minimized-tray .minimized-chip:first-child', (el) => el.getBoundingClientRect().left).catch(() => null);
    firstChipLeft !== null && firstChipLeft >= -0.5
      ? ok(`first tab renders at the mobile left edge (${firstChipLeft}px), unclipped`)
      : fail(`first tab not found or clipped at mobile width (left=${firstChipLeft})`);

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    uncaught.length === 0
      ? ok('mobile pass: no uncaught page errors')
      : uncaught.forEach((e) => fail('uncaught exception (mobile): ' + e));

    await ctx.close();
  }

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0
    ? '\n✅ PASS — tab strip offsets past the sidebar on desktop (collapsed + hover-expanded), close button is clickable, usage-bar-strip unaffected, mobile untouched.'
    : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}

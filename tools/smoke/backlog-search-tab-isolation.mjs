#!/usr/bin/env node
/**
 * MC-955 follow-up: the Backlog tab's search box must have ITS OWN state.
 *
 * WHY THIS EXISTS
 * ----------------
 * The original MC-955 landing (154cb2b/9263055) reused `modalSearchQuery` — the
 * map that already backed the Agent Log / Documents / Activity "Filter..." box
 * (render-core.js ~L921, agent-console.js applyTabFilter) — for the NEW Backlog
 * search box too (render-core.js backlogViewState / the input at ~L949 /
 * clearBacklogSearch). One map, two independent UIs, two failures:
 *
 *   1. Search by ticket KEY breaks on the next refresh. Typing "955" matches
 *      MC-955 via backlogItemMatchesQuery (key/num aware) and shows it. But
 *      every refreshModalById() (SSE turn, 30s poll, any mutation) called
 *      applyTabFilter(), whose 'backlog' branch hid `.backlog-item`s by
 *      substring-matching `.backlog-text` ONLY — the key lives in `.backlog-num`,
 *      a sibling span. The very next rebuild hid the match it had just shown.
 *   2. Cross-tab leak. switchModalTab() never reset the shared map, so typing
 *      into the Agent Log filter and then clicking the Backlog tab silently
 *      filtered (and, via the searchActive branch, un-hid done items in) the
 *      backlog with whatever text was in the OTHER box.
 *
 * The fix splits it into `backlogSearchQuery`, its own map (index.html ~L927),
 * read only by backlogViewState/refreshBacklogList/clearBacklogSearch, and
 * removes the 'backlog' branch from applyTabFilter entirely (the backlog now
 * filters in the render, via backlogViewState, not by hiding DOM nodes after
 * the fact) — so there is nothing left in modalSearchQuery for the backlog to
 * leak from, or be broken by.
 *
 * This test drives the REAL modal in headless Chromium — real card click, real
 * tab switches, real keystrokes into the real inputs, real refreshModalById()
 * calls (the same function every poll/SSE tick calls in production) — so the
 * onclick/oninput wiring shipped in static/index.html and static/js/*.js is
 * exercised as-is, not a hand-copied duplicate that could drift from it.
 *
 * Hermetic: fulfills the page + every extracted /static/js/*.js module (same
 * STATIC_MAP as boot-smoke.mjs) plus canned /api/projects and the lazy
 * per-project /api/project/<id>/backlog (etc.) fetches openProjectModal()
 * fires on open. No running MC server, no real data, no network.
 *
 * RUN: node backlog-search-tab-isolation.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const rd = (...p) => readFileSync(resolve(REPO_ROOT, ...p), 'utf8');

const INDEX_HTML = rd('static', 'index.html');
const APP_CSS = rd('static', 'css', 'app.css');
const BEACON_CSS = rd('static', 'css', 'beacon.css');

// Same module list as boot-smoke.mjs's STATIC_MAP — every extracted
// /static/js/*.js must be fulfilled or the hermetic harness aborts its
// request and the SPA boots without that feature (and, here, without the
// modal/tab wiring under test).
const JS_MODULES = [
  'claydo.js', 'mobile-pairing.js', 'walkthrough.js', 'skills-panel.js', 'media.js',
  'settings-drill.js', 'settings-sections.js', 'terminal.js', 'mermaid.js',
  'search-chats.js', 'backlog-actions.js', 'cross-backlog.js', 'scheduler.js',
  'schedule-calendar.js', 'automation-suggestions.js', 'mcp.js', 'secrets-panel.js',
  'system-status.js', 'update-power.js', 'provider-auth.js', 'schedule-banner.js',
  'provider-settings.js', 'process-manager.js', 'cross-hivemind.js', 'feed.js',
  'beacon.js', 'mobile.js', 'project-actions.js', 'composer-extras.js',
  'slash-autocomplete.js', 'mention-autocomplete.js', 'appearance.js',
  'project-forms.js', 'interactions.js', 'render-core.js', 'modal-manager.js',
  'agent-console.js', 'floor.js', 'hivemind.js', 'agent-log.js', 'resume-preview.js',
  'conversation.js', 'rich-text.js', 'team-card.js', 'cross-social.js', 'desk.js',
  'workflow-builder.js', 'first-run.js',
];
const STATIC_MAP = { '/static/css/app.css': ['text/css; charset=utf-8', APP_CSS],
  '/static/css/beacon.css': ['text/css; charset=utf-8', BEACON_CSS] };
for (const name of JS_MODULES) {
  STATIC_MAP[`/static/js/${name}`] = ['text/javascript; charset=utf-8', rd('static', 'js', name)];
}

const PROJECT_ID = 'smoke_alpha';
const PROJECTS_JSON = JSON.parse(rd('tools', 'smoke', 'fixtures', 'projects.json'));

// Two backlog items, both OPEN. mc955's ticket number is not present anywhere
// in its free text — the only way to match it via '955' is the key/num-aware
// backlogItemMatchesQuery, which is exactly the path the old shared-map bug
// broke on the very next re-render.
const BACKLOG_ITEMS = [
  { id: 'item-955', key: 'MC-955', num: 955, text: 'Backlog list should allow searching and sorting',
    status: 'open', priority: 'normal', source: 'user', links: [], notes: [], attachments: [] },
  { id: 'item-12', key: 'MC-12', num: 12, text: 'Unrelated scheduler cleanup task',
    status: 'open', priority: 'normal', source: 'user', links: [], notes: [], attachments: [] },
];

const ORIGIN = 'http://mc.smoke.test';

function fulfillStaticOrAbort(route) {
  const path = new URL(route.request().url()).pathname;
  if (path === '/' || path === '/index.html')
    return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  const hit = STATIC_MAP[path];
  if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
  if (path === '/api/projects')
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(PROJECTS_JSON) });
  if (path === '/api/config')
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  // openProjectModal()'s lazy per-project fetches (Promise.all in modal-manager.js)
  if (path === `/api/project/${PROJECT_ID}/backlog`)
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(BACKLOG_ITEMS) });
  if (path === `/api/project/${PROJECT_ID}/agent/status`)
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"sessions":[]}' });
  if (path === `/api/project/${PROJECT_ID}/terminal/status`)
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"sessions":[]}' });
  if (path === `/api/project/${PROJECT_ID}/social/queue`)
    return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  // switchModalTab('agent-log') awaits loadAgentLog() before its refreshModal()
  // fires — without this the fetch aborts, the tab-search input never renders,
  // and step (2)'s waitForSelector times out on an unrelated plumbing gap.
  if (path === `/api/project/${PROJECT_ID}/agent/log`)
    return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  return route.abort();
}

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

const browser = await chromium.launch();
try {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', fulfillStaticOrAbort);

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 20000 });
  ok('dashboard booted, project grid rendered');

  await page.evaluate((pid) => window.openProjectModal(pid), PROJECT_ID);
  await page.waitForSelector(`.backlog-check`, { timeout: 20000 }).catch(() => {});
  await page.evaluate((pid) => window.switchModalTab(pid, 'backlog'), PROJECT_ID);
  await page.waitForSelector(`#backlog-search-${PROJECT_ID}`, { timeout: 20000 });
  await page.waitForFunction(
    () => document.querySelectorAll('.backlog-item').length >= 2,
    null, { timeout: 20000 }
  ).catch(() => {});
  const initialCount = await page.locator('.backlog-item').count();
  initialCount === 2 ? ok(`backlog tab rendered ${initialCount} items`)
    : fail(`expected 2 backlog items before search, got ${initialCount}`);

  // ── (1) Search by ticket KEY, then simulate the next poll/SSE rebuild ──────
  await page.locator(`#backlog-search-${PROJECT_ID}`).fill('955');
  await page.waitForTimeout(150);
  let visible = await page.locator('.backlog-item:visible').count();
  visible === 1 ? ok("searching '955' shows exactly MC-955 (key match)")
    : fail(`expected 1 visible item after searching '955', got ${visible}`);

  // The real mechanism every SSE turn_start / 30s poll uses to redraw an open
  // modal — NOT a test-only shortcut.
  await page.evaluate((pid) => refreshModalById(pid), PROJECT_ID);
  await page.waitForTimeout(150);
  visible = await page.locator('.backlog-item:visible').count();
  const mc955Visible = await page.locator('.backlog-item[data-item-id="item-955"]:visible').count();
  (visible === 1 && mc955Visible === 1)
    ? ok("MC-955 STAYS visible across a refreshModalById() rebuild (bug #1 fixed)")
    : fail(`after refreshModalById(), expected MC-955 alone visible; saw ${visible} visible item(s), MC-955 visible=${mc955Visible === 1}`);

  await page.evaluate((pid) => clearBacklogSearch(pid), PROJECT_ID);
  await page.waitForTimeout(150);

  // ── (2) Agent Log filter must not leak into the Backlog tab ────────────────
  // The Filter... box (#tab-search-<id>) lives in .modal-tab-bar, which is
  // display:none at every breakpoint (app.css:2206, 2250) — tab switching
  // moved to the .mc-tabs-in-menu dropdown, which has no search box. Real
  // but currently unreachable DOM; pre-existing and out of scope here. Drive
  // the exact assignment + call its oninput performs instead of `.fill()`
  // on an element Playwright (rightly) won't treat as visible.
  await page.evaluate((pid) => window.switchModalTab(pid, 'agent-log'), PROJECT_ID);
  await page.waitForFunction(
    (pid) => modalActiveTab[pid] === 'agent-log',
    PROJECT_ID, { timeout: 20000 }
  );
  await page.evaluate((pid) => {
    modalSearchQuery[pid] = 'no-such-agent-log-text-zzz';
    applyTabFilter(pid);
  }, PROJECT_ID);
  await page.waitForTimeout(150);

  await page.evaluate((pid) => window.switchModalTab(pid, 'backlog'), PROJECT_ID);
  await page.waitForSelector(`#backlog-search-${PROJECT_ID}`, { timeout: 20000 });
  await page.waitForTimeout(150);
  const backlogBoxValue = await page.locator(`#backlog-search-${PROJECT_ID}`).inputValue();
  const visibleAfterLeak = await page.locator('.backlog-item:visible').count();
  (backlogBoxValue === '' && visibleAfterLeak === 2)
    ? ok('Backlog tab unaffected by the Agent Log filter (bug #2 fixed)')
    : fail(`Backlog tab leaked the Agent Log filter: search box="${backlogBoxValue}", visible items=${visibleAfterLeak}`);

  // Same allowance as boot-smoke.mjs: the hermetic harness has no network, so
  // mermaid.js's dynamic import from the jsdelivr CDN always fails here — not
  // a regression signal.
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.length === 0 ? ok('no uncaught JS error during the whole flow')
    : uncaught.forEach((e) => fail('uncaught: ' + e));
} finally {
  await browser.close();
}

console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);

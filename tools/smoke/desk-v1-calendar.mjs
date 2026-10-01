#!/usr/bin/env node
/**
 * Desk v1 (MC-977, R0 plan T4) — Calendar view smoke (frame 12f).
 *
 * Closes: A14 (UI half — grid of channel rows x day columns, drag to
 * reschedule, phone falls back to an agenda list), A3 (chips carry per-
 * version state, not per-family).
 *
 * T2a's List/Calendar toggle doesn't exist yet, so this mounts the same way
 * T3's review smoke mounted review before T2a existed: through the shell's
 * own pre-registered route (`window.deskV1Nav('calendar', {campaignId})`,
 * T0a's contract) — the ticket brief's required "way to mount it directly".
 *
 * Real headless boot (real index.html + real static/js|css, no network), same
 * hermetic shape as desk-v1-harness.mjs / desk-v1-review.mjs.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-calendar.mjs
 * Exit 0 = every case holds (render checks in all three tones, interaction
 * checks once, phone agenda once); 1 = a case regressed / harness error.
 * Also writes docs/desk_v1/screens/t4_calendar_{desktop_1440,phone_390}.png
 * (default tone only).
 */
import { readFileSync, readdirSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { seedDeskV1Fixtures } from './desk-v1-fixture-api.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const ASSETS_DIR = resolve(REPO_ROOT, 'assets');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const SHOT_DIR = resolve(REPO_ROOT, 'docs', 'desk_v1', 'screens');
mkdirSync(SHOT_DIR, { recursive: true });

const MIME = { '.webp': 'image/webp', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml' };

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];
// See desk-v1-review.mjs for why: the Claydo FAB (app chrome) requests
// /assets/claydo-*.webp; without this every screenshot near it shows a
// broken-image glyph instead of the real mascot icon.
for (const f of readdirSync(ASSETS_DIR)) {
  const ext = f.slice(f.lastIndexOf('.'));
  if (MIME[ext]) STATIC[`/assets/${f}`] = [MIME[ext], readFileSync(resolve(ASSETS_DIR, f))];
}

const SMOKE_TZ = 'America/Los_Angeles';
const SMOKE_NOW = new Date('2026-09-30T12:00:00-07:00');
const PID = 'smoke_deskv1calendar';
const PROJECTS = [{
  id: PID, name: 'Desk v1 calendar smoke', status: 'active', domain: 'general', emoji: '🧪',
  description: '', summary: '', current_task: 'Idle', next_action: '',
  blocked: false, blocked_reason: null, activity_log: [], backlog: [],
  project_path: '/smoke/' + PID, last_updated: '2026-09-09T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null,
  display_order: 0, provider: 'claude', use_streaming_agent: true,
  distiller_mode: 'proposed', distiller_min_recurrence: 3,
  distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
  distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
  distiller_skip_errors: true, roster: [],
}];

const TONES = [
  { name: 'default/dark', ls: {} },
  { name: 'tone-warm', ls: { mc_tone: 'warm' } },
  { name: 'tone-editorial', ls: { mc_tone: 'editorial' } },
];

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

async function fulfillOrAbort(route) {
  const req = route.request();
  const path = new URL(req.url()).pathname;
  const J = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  const hit = STATIC[path];
  if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
  if (path === '/api/projects') return J(PROJECTS);
  if (path === '/api/config') return J({ desk_v1: true, user_timezone: '' });
  if (path === '/api/characters') return J([]);
  return route.abort();
}

async function newBootedPage(browser, tone, viewport) {
  // PIN THE WALL CLOCK AND THE ZONE. The fixtures (desk-v1-fixtures.js:
  // CALENDAR_SCHEDULE, publishedAt/publishAt) are dated late Sep / 1 Oct 2026 and
  // authored in -07:00; the calendar anchors on the page's own `new Date()`. Run
  // against the real clock this smoke passed 2026-09-30 and failed from 2026-10-01
  // (month view moved to October, so only 'Thursday, Oct 1' had chips). The product
  // is right on that boundary; the fixtures simply aren't "today" any more. The
  // clock keeps ticking from the pinned instant (install without pauseAt), so
  // Date.now()-derived ids and timers still behave.
  const ctx = await browser.newContext({
    viewport: viewport || { width: 1440, height: 950 },
    timezoneId: SMOKE_TZ,
  });
  await ctx.clock.install({ time: SMOKE_NOW });
  const page = await ctx.newPage();
  await page.addInitScript((ls) => {
    try { for (const k of Object.keys(ls)) localStorage.setItem(k, ls[k]); } catch (e) {}
  }, (tone && tone.ls) || {});
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', fulfillOrAbort);

  await seedDeskV1Fixtures(page);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors };
}

// Through the campaign page first (matches the real navigation path, and the
// fix reported against T3's smoke for the same reason), then straight to
// calendar via the shell route T0a already registers.
async function navToCalendar(page) {
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.evaluate(() => window.deskV1Nav('calendar', { campaignId: 'camp-1' }));
}

function reportUncaught(pageErrors, tag) {
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail(`${tag} uncaught page error: ${e}`));
}

// ── render checks, one per tone: toolbar, row headers (channel badges, held
// notice), month view surfaces the fixtures' known dated chips with the
// right state labels, footnote copy is the section-9 constant. ─────────────
async function runToneRenderChecks(browser, tone) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, tone);
  await navToCalendar(page);
  await page.waitForSelector('.desk-v1-calendar', { timeout: 8000 });

  const toolbar = await page.$('.desk-v1-cal-toolbar');
  toolbar ? ok(`[${tone.name}] toolbar renders`) : fail(`[${tone.name}] toolbar missing`);

  // Dave's review point 6: one row (§3.3: "This campaign ▾ | All campaigns,
  // ‹ week ›, Week ▾ | Month"), scope as a dropdown, T2a's toggle slot
  // marked with a placeholder since T2a doesn't exist yet.
  const toolbarHeight = await page.$eval('.desk-v1-cal-toolbar', (el) => el.getBoundingClientRect().height).catch(() => 0);
  toolbarHeight > 0 && toolbarHeight < 50
    ? ok(`[${tone.name}] toolbar is a single row (${toolbarHeight.toFixed(0)}px tall)`)
    : fail(`[${tone.name}] toolbar taller than one row: ${toolbarHeight}px`);
  const togglePlaceholder = await page.$('.desk-v1-cal-viewtoggle-placeholder');
  togglePlaceholder
    ? ok(`[${tone.name}] T2a's List/Calendar toggle slot has a placeholder`)
    : fail(`[${tone.name}] toggle placeholder missing`);

  const scopeOn = await page.$eval('[data-cal-scope-select]', (el) => el.value).catch(() => '');
  scopeOn === 'campaign'
    ? ok(`[${tone.name}] default scope is "This campaign"`)
    : fail(`[${tone.name}] default scope wrong: ${JSON.stringify(scopeOn)}`);

  // Row headers reuse the shared channel badge (kit component, not a local
  // label) — the held LinkedIn page channel must show its hold line.
  const rowLabels = await page.$$eval('.desk-v1-cal-rowhead:not(.desk-v1-cal-rowhead-corner) .desk-v1-channel-badge', (els) => els.map((e) => e.textContent.trim()));
  const hasX = rowLabels.some((l) => l.includes('@ron'));
  const hasLi = rowLabels.some((l) => l.includes('Clayrune page'));
  const hasBlog = rowLabels.some((l) => l.includes('Clayrune blog'));
  hasX && hasLi && hasBlog
    ? ok(`[${tone.name}] row headers show all 3 campaign channels via the shared channel badge: ${JSON.stringify(rowLabels)}`)
    : fail(`[${tone.name}] row headers missing a channel: ${JSON.stringify(rowLabels)}`);

  const holdLine = (await page.textContent('.desk-v1-cal-rowhead-held .desk-v1-cal-row-hold').catch(() => '') || '');
  holdLine.length > 0
    ? ok(`[${tone.name}] held channel's row shows the hold reason: "${holdLine.trim()}"`)
    : fail(`[${tone.name}] held channel's row missing its hold reason`);

  // Dave's review point 8: the shared channel badge has no nowrap of its
  // own — narrow enough in this 150px rowhead column that "in · Clayrune
  // page ⚠" used to wrap onto two lines. Line count, not height, is the
  // real signal: the badge's border-box height for a genuine single line
  // is ~25px (17.25px line-height + 6px padding + 2px border), not <22 —
  // a wrapped badge instead produces >1 client rect.
  const badgeLines = await page.$eval('.desk-v1-cal-rowhead-held .desk-v1-channel-badge', (el) => el.getClientRects().length).catch(() => 0);
  badgeLines === 1
    ? ok(`[${tone.name}] the held channel badge stays one line`)
    : fail(`[${tone.name}] held channel badge wrapped or missing: ${badgeLines} line(s)`);

  // Switch to month view (select), which for "today" in this fixture set's
  // window always covers v-install-x (Sep 22, published) and v-testers-li +
  // v-restore-blog (Sep 30, needs_review) in the same grid.
  await page.selectOption('[data-cal-view]', 'month');
  await page.waitForTimeout(50);
  const chipStates = await page.$$eval('.desk-v1-cal-chip', (els) => els.map((e) => e.dataset.state));
  const wantStates = ['verified_published', 'held', 'needs_review'];
  wantStates.every((s) => chipStates.includes(s))
    ? ok(`[${tone.name}] month view surfaces the 3 dated September chips with their real per-version states: ${JSON.stringify(chipStates)}`)
    : fail(`[${tone.name}] month view chip states wrong: ${JSON.stringify(chipStates)}`);

  // Dave's review point 1: a chip on a HELD channel must display Held, not
  // its own underlying state — v-testers-li's real version.state stays
  // 'scheduled' (never mutated), only the chip's DISPLAY is overridden.
  const testersState = await page.$eval('[data-chip-version="v-testers-li"]', (el) => el.dataset.state).catch(() => null);
  testersState === 'held'
    ? ok(`[${tone.name}] a chip on the held LinkedIn channel displays "Held", not "Scheduled"`)
    : fail(`[${tone.name}] held-channel chip should read data-state="held", got ${JSON.stringify(testersState)}`);
  const testersRealState = await page.evaluate(() => {
    const fam = (window.DeskV1Fixtures.families || []).find((f) => (f.versions || []).some((v) => v.id === 'v-testers-li'));
    return fam.versions.find((v) => v.id === 'v-testers-li').state;
  });
  testersRealState === 'scheduled'
    ? ok(`[${tone.name}] the underlying version state is untouched by the display override ("scheduled")`)
    : fail(`[${tone.name}] held display must not mutate the real state, got ${JSON.stringify(testersRealState)}`);

  // Dave's review point 2: a fixture authored for 10:00 local must display
  // 10:00, not a `Z`-suffixed UTC instant shifted into the small hours.
  const testersTime = (await page.textContent('[data-chip-version="v-testers-li"] .desk-v1-cal-chip-time').catch(() => '') || '').trim();
  /^10:00/.test(testersTime)
    ? ok(`[${tone.name}] a fixture authored for 10:00 local displays 10:00: "${testersTime}"`)
    : fail(`[${tone.name}] expected the chip time to read 10:00 local, got ${JSON.stringify(testersTime)}`);

  // Dave's review point 5: the grid's own hairlines are solid — only a
  // Planned CHIP is dashed (checked with October's fixtures below).
  const cellBorderStyle = await page.$eval('.desk-v1-cal-cell.pd-drop-target', (el) => getComputedStyle(el).borderRightStyle).catch(() => '');
  cellBorderStyle === 'solid'
    ? ok(`[${tone.name}] grid cells use solid hairlines, not dashed`)
    : fail(`[${tone.name}] grid cell border should be solid, got ${JSON.stringify(cellBorderStyle)}`);

  const undatedChip = await page.$('[data-chip-version="v-install-li"]');
  !undatedChip
    ? ok(`[${tone.name}] A14/MET-01: a version with no date anywhere (v-install-li) gets no chip, never an invented one`)
    : fail(`[${tone.name}] v-install-li should not have rendered a chip — no date exists for it`);

  // Section-9 footnote copy is a literal from the doc, not invented text.
  const footnote = (await page.textContent('.desk-v1-cal-footnote').catch(() => '') || '');
  /Calendar is a view of Content, not a separate place/.test(footnote)
    ? ok(`[${tone.name}] footnote uses the section-9 "Calendar is a view of Content" copy`)
    : fail(`[${tone.name}] footnote copy wrong/missing: ${JSON.stringify(footnote)}`);

  reportUncaught(pageErrors, `[${tone.name}]`);
  await ctx.close();
}

// ── scope dropdown: "All campaigns" option switches which families/channels
// populate the grid (Dave's review point 6: a dropdown, not two pills).
// camp-1 is currently the only campaign in fixtures, so this checks the
// select's own value flips and re-renders rather than a row-count change
// (that needs a second campaign fixture, out of scope). ────────────────────
async function runScopeToggle(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCalendar(page);
  await page.waitForSelector('.desk-v1-calendar', { timeout: 8000 });

  await page.selectOption('[data-cal-scope-select]', 'all');
  const val = await page.$eval('[data-cal-scope-select]', (el) => el.value).catch(() => '');
  val === 'all'
    ? ok('scope dropdown: selecting "All campaigns" flips the state')
    : fail(`scope dropdown did not flip: ${JSON.stringify(val)}`);

  reportUncaught(pageErrors, '[scope]');
  await ctx.close();
}

// ── week layout (Dave's review point 4): Monday-start, today highlighted,
// weekends muted. Runs on the default (unnavigated) week so "today" is
// always whatever day this actually runs on. ───────────────────────────────
async function runWeekLayout(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCalendar(page);
  await page.waitForSelector('.desk-v1-calendar', { timeout: 8000 });

  const dayNames = await page.$$eval('.desk-v1-cal-daycol .desk-v1-cal-dayname', (els) => els.map((e) => e.textContent.trim()));
  dayNames[0] === 'Mon' && dayNames[dayNames.length - 1] === 'Sun'
    ? ok(`week starts Monday, ends Sunday: ${JSON.stringify(dayNames)}`)
    : fail(`week should run Mon..Sun, got ${JSON.stringify(dayNames)}`);

  const todayCol = await page.$('.desk-v1-cal-daycol-today');
  todayCol ? ok('the real today column is highlighted') : fail('no column carries the today highlight');

  const weekendCols = await page.$$eval('.desk-v1-cal-daycol-weekend', (els) => els.length);
  weekendCols >= 1
    ? ok(`${weekendCols} weekend day column(s) are muted`)
    : fail('no weekend columns muted');

  reportUncaught(pageErrors, '[week-layout]');
  await ctx.close();
}

// ── fixture coverage (Dave's review point 3): every §9 state in frame 12f
// appears somewhere on the grid, plus the ▶ video glyph (point 7). The
// T4-added Follow-up post (planned) and the new Blocked article live in
// October, alongside September's Published/Held/Needs-review already
// covered in runToneRenderChecks. Also re-checks point 5 (only a Planned
// CHIP is dashed) against a real Blocked neighbor. ──────────────────────────
async function runFixtureCoverage(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCalendar(page);
  await page.waitForSelector('.desk-v1-calendar', { timeout: 8000 });
  await page.selectOption('[data-cal-view]', 'month');
  await page.waitForTimeout(50);

  const videoChip = await page.$('.desk-v1-cal-chip .desk-v1-cal-chip-video');
  videoChip ? ok('a video-kind version (v-install-x) shows the ▶ glyph on its title line') : fail('no ▶ video glyph found on the video chip');

  await page.click('[data-cal-shift="1"]'); // September -> October
  await page.waitForTimeout(50);

  const chips = await page.$$eval('.desk-v1-cal-chip', (els) => els.map((e) => ({
    state: e.dataset.state,
    borderStyle: getComputedStyle(e).borderStyle,
  })));
  const states = chips.map((c) => c.state);
  ['planned', 'blocked'].every((s) => states.includes(s))
    ? ok(`October carries the Planned + Blocked chips: ${JSON.stringify(states)}`)
    : fail(`October missing expected states: ${JSON.stringify(states)}`);

  const plannedChip = chips.find((c) => c.state === 'planned');
  const blockedChip = chips.find((c) => c.state === 'blocked');
  plannedChip && plannedChip.borderStyle === 'dashed'
    ? ok('the Planned chip is dashed')
    : fail(`Planned chip should be dashed: ${JSON.stringify(plannedChip)}`);
  blockedChip && blockedChip.borderStyle !== 'dashed'
    ? ok('the Blocked chip is solid (only Planned is dashed)')
    : fail(`Blocked chip should not be dashed: ${JSON.stringify(blockedChip)}`);

  reportUncaught(pageErrors, '[fixture-coverage]');
  await ctx.close();
}

// ── keyboard reschedule (§10 parity: every drag also has a non-drag path).
// Enter on a focused chip opens the inline popover; Move commits through the
// same commandBus as a drag drop, so Undo/toast/announce all apply. ─────────
async function runKeyboardReschedule(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCalendar(page);
  await page.waitForSelector('.desk-v1-calendar', { timeout: 8000 });
  await page.selectOption('[data-cal-view]', 'month');
  await page.waitForTimeout(50);

  // v-install-blog's fixture date (2026-10-01) sits one calendar month past
  // most of the other fixtures' dates; step forward with the real Next
  // button (never assume which month is "current") until it's on-grid.
  let chip = await page.$('[data-chip-version="v-install-blog"]');
  for (let i = 0; i < 3 && !chip; i++) {
    await page.click('[data-cal-shift="1"]');
    await page.waitForTimeout(30);
    chip = await page.$('[data-chip-version="v-install-blog"]');
  }
  chip ? ok('found the planned chip (v-install-blog) to reschedule') : fail('v-install-blog chip not found within 3 months of today');
  await chip.focus();
  await page.keyboard.press('Enter');
  await page.waitForSelector('.desk-v1-cal-kbd-reschedule', { timeout: 2000 });
  ok('Enter on a focused chip opens the keyboard reschedule popover');

  await page.fill('[data-kbd-when]', '2026-10-05T09:00');
  await page.click('[data-kbd-move]');
  await page.waitForSelector('.toast', { timeout: 2000 }).catch(() => {});
  const toastText = (await page.textContent('.toast').catch(() => '') || '');
  /Moved/.test(toastText) && /Install in two minutes/.test(toastText)
    ? ok(`commandBus toast confirms the move: "${toastText.trim()}"`)
    : fail(`toast missing/wrong after keyboard move: ${JSON.stringify(toastText)}`);

  const movedKey = await page.evaluate(() => window.DeskV1Fixtures.calendarSchedule['v-install-blog']);
  movedKey && movedKey.startsWith('2026-10-05')
    ? ok(`fixture mutated in place: calendarSchedule['v-install-blog'] = ${movedKey}`)
    : fail(`fixture not mutated as expected: ${JSON.stringify(movedKey)}`);

  await page.click('.toast .toast-btn.primary');
  await page.waitForTimeout(50);
  const revertedKey = await page.evaluate(() => window.DeskV1Fixtures.calendarSchedule['v-install-blog']);
  revertedKey === '2026-10-01T15:00:00-07:00'
    ? ok('Undo restores the original date on the fixture')
    : fail(`Undo did not restore the original date: ${JSON.stringify(revertedKey)}`);

  reportUncaught(pageErrors, '[keyboard-reschedule]');
  await ctx.close();
}

// ── drag-to-reschedule (§10, §3.3) — a real mouse gesture through
// PointerDrag (T0c), same technique as pointer-drag.mjs/drag-to-hire.mjs:
// page.mouse, not synthetic PointerEvents. Drags a SCHEDULED version
// (v-testers-li) to a day outside its approval window, which must ask via
// window.confirm before committing and flips it to needs_review. ───────────
async function runDragReschedule(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCalendar(page);
  await page.waitForSelector('.desk-v1-calendar', { timeout: 8000 });
  // Week view, not month: month's ~30 day-columns (rows=channels,
  // columns=days per §3.3, unchanged for either view) scroll horizontally
  // past the viewport, and a real mouse drag can't reach an off-screen drop
  // target any more than a real user's could — week's 7 columns always fit.
  await page.waitForTimeout(50);

  await page.evaluate(() => { window.__confirmCalls = []; window.confirm = (msg) => { window.__confirmCalls.push(msg); return true; }; });

  // v-testers-li is fixed at 2026-09-30; step week-forward with the real
  // Next button (never assume which week is "current") until it's on-grid.
  let chip = await page.$('[data-chip-version="v-testers-li"]');
  for (let i = 0; i < 6 && !chip; i++) {
    await page.click('[data-cal-shift="1"]');
    await page.waitForTimeout(30);
    chip = await page.$('[data-chip-version="v-testers-li"]');
  }
  chip ? ok('found the scheduled chip (v-testers-li) to drag') : fail('v-testers-li chip not found within 6 weeks of today');
  const chipBox = await chip.boundingBox();
  const cx = chipBox.x + chipBox.width / 2, cy = chipBox.y + chipBox.height / 2;

  // Target a cell in the SAME row (same channel, ch-li-page) a day over —
  // same-row keeps this a pure day-move, not a channel reassignment (which
  // this view does not support; a drop only ever changes `data-day-key`).
  const chipRow = await chip.evaluateHandle((el) => el.closest('.desk-v1-cal-row'));
  const targetCell = (await chipRow.$('.desk-v1-cal-cell:not(:has(.desk-v1-cal-chip))')) || (await chipRow.$('.desk-v1-cal-cell'));
  const targetBox = await targetCell.boundingBox();
  const tx = targetBox.x + targetBox.width / 2, ty = targetBox.y + targetBox.height / 2;

  await page.mouse.move(cx, cy);
  await page.mouse.down();
  await page.mouse.move(cx + 15, cy + 5, { steps: 3 }); // cross the slop
  await page.waitForSelector('.desk-v1-cal-chip-dragging', { timeout: 2000 }).catch(() => {});
  await page.mouse.move(tx, ty, { steps: 8 });
  await page.waitForSelector('.pd-drop-hover .desk-v1-cal-cell-preview:not(:empty)', { timeout: 2000 }).catch(() => {});
  const previewShown = await page.$eval('.pd-drop-hover .desk-v1-cal-cell-preview', (e) => e.textContent).catch(() => '');
  if (previewShown && /Move to/.test(previewShown)) {
    ok(`drag hover preview shows the destination: "${previewShown}"`);
  } else {
    const underPointer = await page.evaluate(([x, y]) => {
      const el = document.elementFromPoint(x, y);
      return el ? el.outerHTML.slice(0, 160) : null;
    }, [tx, ty]);
    fail(`drop-hover preview missing/wrong: ${JSON.stringify(previewShown)} — elementFromPoint(target): ${underPointer}`);
  }
  await page.mouse.up();
  await page.waitForTimeout(50);

  const confirmCalls = await page.evaluate(() => window.__confirmCalls.length);
  confirmCalls > 0
    ? ok('moving a scheduled version to a different day asks for approval again (window.confirm)')
    : fail('expected window.confirm to fire for a scheduled version moved outside its approval day');

  const newState = await page.evaluate(() => {
    const fam = (window.DeskV1Fixtures.families || []).find((f) => (f.versions || []).some((v) => v.id === 'v-testers-li'));
    return fam.versions.find((v) => v.id === 'v-testers-li').state;
  });
  newState === 'needs_review'
    ? ok('A confirmed out-of-window move flips the version to needs_review (approval invalidated)')
    : fail(`expected needs_review after the confirmed move, got ${JSON.stringify(newState)}`);

  const teardown = await page.evaluate(() => ({
    ghosts: document.querySelectorAll('.pd-ghost').length,
    hovering: document.querySelectorAll('.pd-drop-hover').length,
  }));
  teardown.ghosts === 0 && teardown.hovering === 0
    ? ok('drag teardown clears the ghost and any leftover hover state')
    : fail(`drag teardown incomplete: ${JSON.stringify(teardown)}`);

  reportUncaught(pageErrors, '[drag-reschedule]');
  await ctx.close();
}

// ── R2-9 (§8 row / §11), reshaped when the Presence screen was retired
// (MC-977 2026-10-01): the When stop only READS the campaign's own limits
// (cadence, min gap, term, post cap) and links back to the Brief, where they
// are edited — one place per setting. Plus the legend's literal "your own
// slot"/"agent-suggested" words and the Your-slots rowhead. ───────────────
async function runR29FieldsAndLegend(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCalendar(page);
  await page.waitForSelector('.desk-v1-calendar', { timeout: 8000 });

  const cadence = (await page.textContent('[data-cal-field-cadence]').catch(() => '') || '').trim();
  cadence === '3/wk'
    ? ok(`R2-9: Cadence field reads the campaign's own cadence: "${cadence}"`)
    : fail(`R2-9: Cadence field wrong: ${JSON.stringify(cadence)}`);

  const minGap = (await page.textContent('[data-cal-field-mingap]').catch(() => '') || '').trim();
  minGap === '12h'
    ? ok(`R2-9: Min gap field reads the campaign's own min gap: "${minGap}"`)
    : fail(`R2-9: Min gap field wrong: ${JSON.stringify(minGap)}`);

  const term = (await page.textContent('[data-cal-field-term]').catch(() => '') || '').trim();
  /Oct 20$/.test(term)
    ? ok(`R2-9: Term field shows the campaign's end date: "${term}"`)
    : fail(`R2-9: Term field wrong: ${JSON.stringify(term)}`);

  const noInputs = await page.$$eval('.desk-v1-cal-fields input', (els) => els.length);
  noInputs === 0
    ? ok('R2-9: the When stop has no limit inputs of its own (limits are edited on the Brief only)')
    : fail(`R2-9: the When stop still carries ${noInputs} limit input(s)`);

  await page.click('[data-cal-edit-limits]');
  await page.waitForSelector('[data-how-limits-card] [data-how-limit="per_week"]', { timeout: 4000 });
  const cap = await page.$eval('[data-how-limits-card] [data-how-limit="per_week"]', (el) => el.value);
  cap === '3'
    ? ok('R2-9: "Edit limits in Brief" opens the Brief limits card, showing the same 3/wk')
    : fail(`R2-9: Brief limits card wrong: per_week=${JSON.stringify(cap)}`);
  await navToCalendar(page);
  await page.waitForSelector('.desk-v1-calendar', { timeout: 8000 });

  const legendText = await page.$$eval('.desk-v1-cal-legend-item', (els) => els.map((e) => e.textContent.trim()));
  legendText.some((t) => /your own slot/.test(t)) && legendText.some((t) => /agent-suggested/.test(t))
    ? ok(`R2-9: legend spells out the literal words, not colour/border alone: ${JSON.stringify(legendText)}`)
    : fail(`R2-9: legend text missing/wrong: ${JSON.stringify(legendText)}`);

  const rowLabel = (await page.textContent('.desk-v1-cal-slot-rowlabel').catch(() => '') || '').trim();
  const handle = await page.$('[data-slot-handle]');
  rowLabel === 'Your slots' && handle
    ? ok('R2-9: the Your-slots band and its drag handle render')
    : fail(`R2-9: Your-slots band missing (label=${JSON.stringify(rowLabel)}, handle=${!!handle})`);

  reportUncaught(pageErrors, '[R2-9 fields/legend]');
  await ctx.close();
}

// ── R2-9 drag-to-create helper: drags the "+ New slot" rowhead handle onto
// a `.desk-v1-cal-slotcell` (same PointerDrag mechanics as runDragReschedule
// above), scoped to the slots band only so it never lands on a channel row.
async function dragSlotHandleToDay(page, dayKey) {
  const handle = await page.$('[data-slot-handle]');
  const handleBox = await handle.boundingBox();
  const hx = handleBox.x + handleBox.width / 2, hy = handleBox.y + handleBox.height / 2;
  const cellSel = `.desk-v1-cal-row-slots .desk-v1-cal-slotcell[data-day-key="${dayKey}"]`;
  const cell = await page.$(cellSel);
  const cellBox = await cell.boundingBox();
  const tx = cellBox.x + cellBox.width / 2, ty = cellBox.y + cellBox.height / 2;

  await page.mouse.move(hx, hy);
  await page.mouse.down();
  await page.mouse.move(hx + 15, hy + 5, { steps: 3 });
  await page.waitForSelector('.desk-v1-cal-slot-handle-dragging', { timeout: 2000 }).catch(() => {});
  await page.mouse.move(tx, ty, { steps: 8 });
  await page.waitForSelector(`${cellSel}.pd-drop-hover .desk-v1-cal-cell-preview:not(:empty)`, { timeout: 2000 }).catch(() => {});
  const previewShown = await page.$eval(`${cellSel}.pd-drop-hover .desk-v1-cal-cell-preview`, (e) => e.textContent).catch(() => '');
  await page.mouse.up();
  await page.waitForTimeout(50);
  return previewShown;
}

// ── R2-9 (§8 row): drag-to-create own slots, the cadence/min-gap refusal
// gate (banner shows the reason, no slot inserted), and Undo on a successful
// add. Uses camp-1's real fixture bounds — cadence 3/wk, min gap 12h across
// all 3 accounts (desk-v1-fixtures.js `presence.ceilings`) — not invented
// numbers, so a refusal message asserted here is the real gate firing. ─────
async function runR29SlotCreateAndRefusal(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCalendar(page);
  await page.waitForSelector('.desk-v1-calendar', { timeout: 8000 });

  const dayKeys = await page.$$eval('.desk-v1-cal-row-slots .desk-v1-cal-slotcell', (els) => els.map((e) => e.dataset.dayKey));
  dayKeys.length === 7
    ? ok(`R2-9: the slots band has 7 day cells for week view: ${JSON.stringify(dayKeys)}`)
    : fail(`R2-9: expected 7 slot cells, got ${dayKeys.length}`);

  // 1) Monday: first slot succeeds.
  const preview1 = await dragSlotHandleToDay(page, dayKeys[0]);
  /Add your slot/.test(preview1)
    ? ok(`R2-9: slot-create drag hover shows a ghost + drop preview: "${preview1}"`)
    : fail(`R2-9: slot-create hover preview missing/wrong: ${JSON.stringify(preview1)}`);
  let chip = await page.$(`.desk-v1-cal-row-slots .desk-v1-cal-slotcell[data-day-key="${dayKeys[0]}"] .desk-v1-cal-slotchip-user`);
  const ariaLabel = chip ? await chip.getAttribute('aria-label') : '';
  chip && /your own slot/.test(ariaLabel)
    ? ok(`R2-9: dragged slot renders solid "Your slot", aria-label carries "your own slot": "${ariaLabel}"`)
    : fail(`R2-9: first slot drag did not create a user chip (aria-label=${JSON.stringify(ariaLabel)})`);
  const dashed1 = chip ? await chip.evaluate((el) => el.classList.contains('desk-v1-cal-chip-dashed')) : true;
  !dashed1
    ? ok('R2-9: a user slot chip is solid, not dashed (mine vs suggested distinct by border too)')
    : fail('R2-9: user slot chip should not be dashed');

  // 2) Monday again (same 14:00 default time, 0h apart): min-gap refusal —
  // count (2) would stay under the 3/wk cap, so this isolates the gap check.
  await dragSlotHandleToDay(page, dayKeys[0]);
  const refusalMsg = (await page.textContent('[data-slot-refusal]').catch(() => '') || '').trim();
  /within 12h of another slot/.test(refusalMsg)
    ? ok(`R2-9: dropping a 2nd slot on the same default time is refused for min gap: "${refusalMsg}"`)
    : fail(`R2-9: expected a min-gap refusal, got ${JSON.stringify(refusalMsg)}`);
  const mondayChips = await page.$$eval(`.desk-v1-cal-row-slots .desk-v1-cal-slotcell[data-day-key="${dayKeys[0]}"] .desk-v1-cal-slotchip`, (els) => els.length);
  mondayChips === 1
    ? ok('R2-9: a refused slot is not inserted (Monday still carries exactly 1 chip)')
    : fail(`R2-9: refused slot should not persist, Monday has ${mondayChips} chip(s)`);

  // 3) Tuesday: succeeds and clears the refusal banner (2nd real slot).
  await dragSlotHandleToDay(page, dayKeys[1]);
  const bannerAfterSuccess = await page.$eval('[data-slot-refusal]', (el) => el.classList.contains('desk-v1-cal-slot-refusal-show')).catch(() => true);
  !bannerAfterSuccess
    ? ok('R2-9: a subsequent successful add clears the refusal banner')
    : fail('R2-9: refusal banner should clear after a successful add');

  // 4) Wednesday: 3rd slot, at the 3/wk cap.
  await dragSlotHandleToDay(page, dayKeys[2]);
  const wedChip = await page.$(`.desk-v1-cal-row-slots .desk-v1-cal-slotcell[data-day-key="${dayKeys[2]}"] .desk-v1-cal-slotchip-user`);
  wedChip
    ? ok('R2-9: 3rd slot (at cap) succeeds')
    : fail('R2-9: 3rd slot should have been created');

  // 5) Thursday: 4th slot would exceed the 3/wk cadence cap -> refused.
  await dragSlotHandleToDay(page, dayKeys[3]);
  const capRefusal = (await page.textContent('[data-slot-refusal]').catch(() => '') || '').trim();
  /over 3\/wk/.test(capRefusal)
    ? ok(`R2-9: a 4th slot in the same week is refused over the campaign's cadence cap: "${capRefusal}"`)
    : fail(`R2-9: expected an over-cadence refusal, got ${JSON.stringify(capRefusal)}`);
  const thuChips = await page.$$eval(`.desk-v1-cal-row-slots .desk-v1-cal-slotcell[data-day-key="${dayKeys[3]}"] .desk-v1-cal-slotchip`, (els) => els.length);
  thuChips === 0
    ? ok('R2-9: the refused 4th slot leaves Thursday empty')
    : fail(`R2-9: Thursday should have no slot, has ${thuChips}`);

  // 6) Undo the last successful add (Wednesday) via the commandBus toast —
  // same Undo contract every other Desk v1 drop already gets. Toasts stack
  // (no `opts.key`, so each add appends a new one rather than replacing the
  // last) — target the LAST toast, not the first, or this undoes Monday's.
  const toastText = (await page.textContent('.toast:last-of-type').catch(() => '') || '');
  /Added your slot/.test(toastText)
    ? ok(`R2-9: commandBus toast confirms the slot add: "${toastText.trim()}"`)
    : fail(`R2-9: toast missing/wrong after slot add: ${JSON.stringify(toastText)}`);
  await page.click('.toast:last-of-type .toast-btn.primary');
  await page.waitForTimeout(50);
  const wedChipsAfterUndo = await page.$$eval(`.desk-v1-cal-row-slots .desk-v1-cal-slotcell[data-day-key="${dayKeys[2]}"] .desk-v1-cal-slotchip`, (els) => els.length);
  wedChipsAfterUndo === 0
    ? ok('R2-9: Undo removes the slot from the grid')
    : fail(`R2-9: Wednesday should be empty after Undo, has ${wedChipsAfterUndo}`);

  reportUncaught(pageErrors, '[R2-9 slot-create]');
  await ctx.close();
}

// ── R2-9 (§8 row): an agent-suggested slot renders dashed with the literal
// "Agent suggested" words (mine vs suggested is never colour/border alone),
// and `deskV1CalendarSuggestFill` — the seam desk-v1-how.js's Suggest task
// will call — fills a user slot without touching its id/at/origin. ─────────
async function runR29AgentSuggestedAndFill(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCalendar(page);
  await page.waitForSelector('.desk-v1-calendar', { timeout: 8000 });

  // Seeded at "today 14:00" so it always falls inside the default week view
  // regardless of which real day this runs on (no hardcoded date to step to).
  await page.evaluate(() => {
    const camp = (window.DeskV1Fixtures.campaigns || []).find((c) => c.id === 'camp-1');
    camp.when = camp.when || {};
    camp.when.slots = camp.when.slots || [];
    const at = new Date(); at.setHours(14, 0, 0, 0);
    camp.when.slots.push({ id: 'slot-agent-test', at: at.toISOString(), origin: 'agent' });
    window.deskV1Nav('calendar', { campaignId: 'camp-1' });
  });
  await page.waitForTimeout(50);

  const agentChip = await page.$('[data-slot-id="slot-agent-test"]');
  const isDashed = agentChip ? await agentChip.evaluate((el) => el.classList.contains('desk-v1-cal-chip-dashed') && el.classList.contains('desk-v1-cal-slotchip-agent')) : false;
  const agentAria = agentChip ? await agentChip.getAttribute('aria-label') : '';
  agentChip && isDashed && /agent-suggested/.test(agentAria)
    ? ok(`R2-9: an agent-origin slot renders dashed with "agent-suggested" in its aria-label: "${agentAria}"`)
    : fail(`R2-9: agent-suggested slot chip wrong (dashed=${isDashed}, aria=${JSON.stringify(agentAria)})`);
  const originWord = (await page.textContent('[data-slot-id="slot-agent-test"] .desk-v1-cal-slotchip-origin').catch(() => '') || '').trim();
  originWord === 'Agent suggested'
    ? ok('R2-9: the agent chip\'s own line reads "Agent suggested" literally')
    : fail(`R2-9: agent chip origin word wrong: ${JSON.stringify(originWord)}`);

  // Now exercise the fill seam on a fresh USER slot: fills without moving it.
  await page.evaluate(() => {
    const camp = (window.DeskV1Fixtures.campaigns || []).find((c) => c.id === 'camp-1');
    const at = new Date(); at.setHours(9, 0, 0, 0);
    camp.when.slots.push({ id: 'slot-fill-test', at: at.toISOString(), origin: 'user' });
    // R2-19: the fill takes a version Where already placed (title / platform
    // come off it, never off the caller) — v-home-x is @ron's version of
    // "Home status table".
    window.deskV1CalendarSuggestFill('camp-1', { slotId: 'slot-fill-test', versionId: 'v-home-x', title: 'Ignored caller title', platform: 'linkedin' });
  });
  await page.waitForTimeout(50);
  // R2-9b (Dave review pass 4): the title line no longer carries a
  // "· platform" text suffix — platform is the line-1 glyph now, and the
  // suffix was pushing "⚠ held" past the ellipsis at month-column widths.
  const filledTitle = (await page.textContent('[data-slot-id="slot-fill-test"] .desk-v1-cal-slotchip-title').catch(() => '') || '').trim();
  filledTitle === 'Home status table'
    ? ok(`R2-9b: deskV1CalendarSuggestFill fills a user slot with the placed version's title, no platform-text suffix: "${filledTitle}"`)
    : fail(`R2-9b: fill did not render on the slot chip: ${JSON.stringify(filledTitle)}`);
  const filledGlyph = (await page.textContent('[data-slot-id="slot-fill-test"] .desk-v1-cal-slotchip-glyph').catch(() => '') || '').trim();
  filledGlyph === '𝕏'
    ? ok(`R2-9b: the fill's platform (the version's own account, x) renders as the line-1 glyph: "${filledGlyph}"`)
    : fail(`R2-9b: fill's platform glyph wrong: ${JSON.stringify(filledGlyph)}`);
  const stillUser = await page.$eval('[data-slot-id="slot-fill-test"]', (el) => el.dataset.slotOrigin);
  stillUser === 'user'
    ? ok('R2-9: filling a slot never changes its origin away from user')
    : fail(`R2-9: fill must not mutate origin, got ${JSON.stringify(stillUser)}`);

  reportUncaught(pageErrors, '[R2-9 agent+fill]');
  await ctx.close();
}

// ── R2-19 (Ron 2026-09-30): When owns TIME only, for versions Where already
// placed. (a) Suggest never creates a version and never moves one across
// accounts — a full snapshot of every version's id + account + state is
// identical before and after; (b) Suggest takes only placed versions: an
// unplaced version (no account, or an account the campaign does not use) and
// an unknown id fill nothing; (c) with nothing placed, When shows an empty
// state pointing back to Where (and the Go to Where button lands there). ────
async function runR219WhenTimeOnly(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCalendar(page);
  await page.waitForSelector('.desk-v1-calendar', { timeout: 8000 });

  const res = await page.evaluate(() => {
    const fx = window.DeskV1Fixtures;
    const camp = fx.campaigns.find((c) => c.id === 'camp-1');
    const snapNow = () => JSON.stringify((fx.families || []).map((f) => [f.id, (f.versions || []).map((v) => [v.id, v.channelId, v.state])]));
    // Two unplaced versions on camp-1: no account, and an account camp-1 does not use.
    const fam = fx.families.find((f) => f.campaignId === 'camp-1');
    fam.versions.push({ id: 'v-r219-noacct', channelId: null, state: 'planned', revision: 0 });
    fam.versions.push({ id: 'v-r219-otheracct', channelId: 'ch-not-in-plan', state: 'planned', revision: 0 });
    camp.when = camp.when || {};
    camp.when.slots = camp.when.slots || [];
    const at = new Date();
    at.setHours(16, 0, 0, 0);
    ['a', 'b', 'c'].forEach((k) => camp.when.slots.push({ id: 'slot-r219-' + k, at: at.toISOString(), origin: 'user' }));
    const before = snapNow();
    const unplaced1 = window.deskV1CalendarSuggestFill('camp-1', { slotId: 'slot-r219-a', versionId: 'v-r219-noacct' });
    const unplaced2 = window.deskV1CalendarSuggestFill('camp-1', { slotId: 'slot-r219-a', versionId: 'v-r219-otheracct' });
    const unknown = window.deskV1CalendarSuggestFill('camp-1', { slotId: 'slot-r219-a', versionId: 'no-such-version', title: 'Invented', platform: 'x', channelId: 'ch-x-ron' });
    const invented = window.deskV1CalendarSuggestFill('camp-1', { slotId: 'slot-r219-b', title: 'Invented piece', platform: 'reddit', channelId: 'ch-blog' });
    const afterRefusals = snapNow();
    const auto = window.deskV1CalendarSuggestFill('camp-1', { slotId: 'slot-r219-c' });
    const after = snapNow();
    return {
      refusedUnplaced: unplaced1 === null && unplaced2 === null && unknown === null,
      slotAStillEmpty: !camp.when.slots.find((s) => s.id === 'slot-r219-a').filled,
      inventedTitle: invented && invented.filled && invented.filled.title,
      autoVersion: auto && auto.filled && auto.filled.versionId,
      autoChannel: auto && auto.filled && auto.filled.channelId,
      sameAfterRefusals: before === afterRefusals,
      sameAfterFill: before === after,
    };
  });
  res.refusedUnplaced && res.slotAStillEmpty
    ? ok('R2-19: Suggest fills nothing for a version with no account, one on an account the campaign does not use, or an unknown id')
    : fail(`R2-19: Suggest filled an unplaced version: ${JSON.stringify(res)}`);
  res.inventedTitle && res.inventedTitle !== 'Invented piece'
    ? ok(`R2-19: a caller-supplied title/platform/channel cannot invent a placement (filled with the placed version's own "${res.inventedTitle}")`)
    : fail(`R2-19: Suggest honoured a caller-invented piece: ${JSON.stringify(res)}`);
  res.sameAfterRefusals && res.sameAfterFill
    ? ok('R2-19: When Suggest left every version (count, id, account, state) unchanged, before and after')
    : fail(`R2-19: When Suggest changed version placement: ${JSON.stringify(res)}`);
  res.autoVersion && res.autoChannel
    ? ok(`R2-19: an unnamed fill picks a placed version (${res.autoVersion} on ${res.autoChannel}) and keeps its own account`)
    : fail(`R2-19: unnamed fill picked nothing: ${JSON.stringify(res)}`);

  // (c) empty state: a campaign whose Where has placed nothing.
  await page.evaluate(() => {
    const fx = window.DeskV1Fixtures;
    fx.campaigns.push({
      id: 'camp-r219-empty', projectId: 'clayrune', state: 'draft', subject: { label: 'R2-19 empty' },
      plan: { title: 'R2-19 empty', accounts: ['ch-x-ron'], cadence: { per_week: 2 } }, term: {}, map: { stop: 'when', done: [] },
    });
    window.deskV1Nav('campaign', { campaignId: 'camp-r219-empty' });
    window.deskV1Nav('calendar', { campaignId: 'camp-r219-empty' });
  });
  await page.waitForSelector('[data-cal-empty-where]', { timeout: 4000 }).catch(() => {});
  const empty = await page.evaluate(() => ({
    hasEmpty: !!document.querySelector('[data-cal-empty-where]'),
    hasGrid: !!document.querySelector('.desk-v1-cal-grid, .desk-v1-cal-monthgrid'),
    hasSlotsBand: !!document.querySelector('.desk-v1-cal-row-slots'),
    hasFields: !!document.querySelector('.desk-v1-cal-fields'),
    text: (document.querySelector('[data-cal-empty-where]') || {}).textContent || '',
  }));
  empty.hasEmpty && /Where/.test(empty.text) && !empty.hasGrid && !empty.hasSlotsBand && empty.hasFields
    ? ok(`R2-19: a campaign with nothing placed shows the When empty state pointing at Where (no grid, cadence/term fields kept): "${empty.text.trim().replace(/\s+/g, ' ')}"`)
    : fail(`R2-19: When empty state wrong: ${JSON.stringify(empty)}`);
  await page.click('[data-cal-to-where]').catch(() => {});
  await page.waitForSelector('[data-where]', { timeout: 4000 }).catch(() => {});
  const landed = await page.evaluate(() => {
    const here = document.querySelector('.desk-v1-map-stop[data-state="here"]');
    return { where: !!document.querySelector('[data-where]'), here: here ? here.dataset.stop : null };
  });
  landed.where && landed.here === 'where'
    ? ok('R2-19: "Go to Where ›" from the When empty state lands on the Where stop')
    : fail(`R2-19: Go to Where did not land on Where: ${JSON.stringify(landed)}`);

  reportUncaught(pageErrors, '[R2-19 when]');
  await ctx.close();
}

// ── R2-9 (§4.1 row 141 carried acceptance): the Unscheduled tray stays, and
// dragging a card onto its own channel row's day cell reuses `_reschedule`
// wholesale (same Undo/toast/approval-gate contract every other drop gets).
// Taller-than-shipped viewport (1440x1400, not the suite's usual 950) — at a
// real modal's ~900px height the campaign chrome above the calendar plus
// R2-9's own Fields/legend/Your-slots rows push the Unscheduled tray below
// the fold, below a channel-row drop target too, and PointerDrag has no
// auto-scroll-during-drag (see pointer-drag.js — a real user scrolls first,
// then drags, same two-step any native OS drag needs). This test exercises
// the drag MECHANICS (same code path either way); the below-the-fold
// reachability at shipped viewport sizes is reported as a deviation, not
// silently worked around.
async function runR29UnscheduledDrag(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} }, { width: 1440, height: 1400 });
  await navToCalendar(page);
  await page.waitForSelector('.desk-v1-calendar', { timeout: 8000 });

  const card = await page.$('.desk-v1-cal-unscheduled-card[data-unsched-version="v-install-li"]');
  card
    ? ok('R2-9: v-install-li (no date anywhere, A14/MET-01) shows in the Unscheduled tray')
    : fail('R2-9: v-install-li missing from the Unscheduled tray');

  const targetCell = await page.$('.desk-v1-cal-cell[data-channel-id="ch-li-page"]');
  const cardBox = await card.boundingBox();
  const cellBox = await targetCell.boundingBox();
  const cx = cardBox.x + cardBox.width / 2, cy = cardBox.y + cardBox.height / 2;
  const tx = cellBox.x + cellBox.width / 2, ty = cellBox.y + cellBox.height / 2;

  await page.mouse.move(cx, cy);
  await page.mouse.down();
  await page.mouse.move(cx + 15, cy + 5, { steps: 3 });
  await page.waitForSelector('.desk-v1-cal-unscheduled-dragging', { timeout: 2000 }).catch(() => {});
  await page.mouse.move(tx, ty, { steps: 8 });
  await page.waitForSelector('.pd-drop-hover .desk-v1-cal-cell-preview:not(:empty)', { timeout: 2000 }).catch(() => {});
  const preview = await page.$eval('.pd-drop-hover .desk-v1-cal-cell-preview', (e) => e.textContent).catch(() => '');
  /Schedule/.test(preview)
    ? ok(`R2-9: dragging an unscheduled card shows a "Schedule <day>" preview: "${preview}"`)
    : fail(`R2-9: unscheduled drag preview missing/wrong: ${JSON.stringify(preview)}`);
  await page.mouse.up();
  await page.waitForTimeout(50);

  const chipNow = await page.$('[data-chip-version="v-install-li"]');
  chipNow
    ? ok('R2-9: dropping an unscheduled card gives its version a real date (a chip now renders)')
    : fail('R2-9: v-install-li should now render a chip after being dropped');
  const stillInTray = await page.$('.desk-v1-cal-unscheduled-card[data-unsched-version="v-install-li"]');
  !stillInTray
    ? ok('R2-9: a now-dated version leaves the Unscheduled tray')
    : fail('R2-9: v-install-li should have left the Unscheduled tray once dated');

  reportUncaught(pageErrors, '[R2-9 unscheduled-drag]');
  await ctx.close();
}

// ── R2-9b (Dave review pass 4): the Month grid's own three defects, pinned
// so they can't regress silently — (1) the weekday header used to inherit
// the body grid's `grid-auto-rows: minmax(72px, 1fr)`, leaving a blank band
// under 12px of label text; (2) week rows were ~110px, not the mockup's
// ~75px, clipping the modal to ~1.3 visible rows; (3) a slot chip's line 1
// (glyph + time) must never ellipsize — only line 2 (the label) may. ───────
async function runR29bMonthGridDimensions(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCalendar(page);
  await page.waitForSelector('.desk-v1-calendar', { timeout: 8000 });

  await page.evaluate(() => {
    const camp = (window.DeskV1Fixtures.campaigns || []).find((c) => c.id === 'camp-1');
    camp.when = camp.when || {};
    camp.when.slots = camp.when.slots || [];
    const at = new Date(); at.setHours(14, 0, 0, 0);
    camp.when.slots.push({ id: 'slot-month-dim-test', at: at.toISOString(), origin: 'user' });
    window.deskV1Nav('calendar', { campaignId: 'camp-1' });
  });
  await page.waitForTimeout(50);
  await page.selectOption('[data-cal-view]', 'month');
  await page.waitForTimeout(50);

  const headHeight = await page.$eval('.desk-v1-cal-monthgrid-head', (el) => el.getBoundingClientRect().height);
  headHeight < 40
    ? ok(`R2-9b: month header row is short, no reserved blank band: ${headHeight.toFixed(1)}px`)
    : fail(`R2-9b: month header row too tall (blank band under labels): ${headHeight.toFixed(1)}px`);

  const rowHeight = await page.$eval('.desk-v1-cal-monthcell', (el) => el.getBoundingClientRect().height);
  rowHeight <= 90
    ? ok(`R2-9b: month week row is <=90px: ${rowHeight.toFixed(1)}px`)
    : fail(`R2-9b: month week row too tall: ${rowHeight.toFixed(1)}px`);

  const timeEl = await page.$('[data-slot-id="slot-month-dim-test"] .desk-v1-cal-slotchip-time');
  const dims = timeEl ? await timeEl.evaluate((el) => ({
    text: el.textContent.trim(),
    scrollWidth: el.scrollWidth,
    clientWidth: el.clientWidth,
  })) : null;
  dims && /\d{1,2}:\d{2}/.test(dims.text) && dims.scrollWidth <= dims.clientWidth + 1
    ? ok(`R2-9b: own slot chip's line 1 carries the full time, un-ellipsized: "${dims.text}"`)
    : fail(`R2-9b: slot chip line 1 time missing/truncated: ${JSON.stringify(dims)}`);

  reportUncaught(pageErrors, '[R2-9b month-dims]');
  await ctx.close();
}

// ── MC-1021 weekend overlap: at every desktop width the desk window is ~1026px
// wide, so the 7-day week grid beside the Posy column used to overflow its
// wrapper — Sat + Sun scrolled out of view and the Posy box sat where they
// were, so a drop on a weekend slot landed on the Posy input. Every width:
// each weekend cell is fully inside the grid wrapper, the point at its centre
// resolves to the cell (not the Posy box), the wrapper has no horizontal
// scroll, and a real drag hovers the Sunday cell as a drop target.
async function runWeekendNotCovered(browser) {
  for (const w of [1280, 1440, 1920]) {
    const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} }, { width: w, height: 950 });
    await navToCalendar(page);
    await page.waitForSelector('.desk-v1-calendar', { timeout: 8000 });
    await page.waitForTimeout(100);
    const r = await page.evaluate(() => {
      const wrap = document.querySelector('.desk-v1-cal-grid-wrap').getBoundingClientRect();
      const rightcol = document.querySelector('.desk-v1-camp-rightcol').getBoundingClientRect();
      const cells = [...document.querySelectorAll('.desk-v1-cal-row:not(.desk-v1-cal-row-header) .desk-v1-cal-cell-weekend')];
      const bad = [];
      for (const c of cells) {
        // Horizontal geometry is measured BEFORE any scroll (a cell that is
        // only reachable by scrolling sideways is the bug); the row may sit
        // below the modal's fold, so scroll vertically only, then hit-test.
        const b0 = c.getBoundingClientRect();
        const inWrap = b0.left >= wrap.left - 0.5 && b0.right <= wrap.right + 0.5;
        const clearOfRightcol = b0.right <= rightcol.left + 0.5;
        c.scrollIntoView({ block: 'center', inline: 'nearest' });
        const b = c.getBoundingClientRect();
        const x = b.left + b.width / 2, y = b.top + b.height / 2;
        const hit = document.elementFromPoint(x, y);
        if (!(hit && (hit === c || c.contains(hit))) || !inWrap || !clearOfRightcol) {
          bad.push({ l: Math.round(b.left), r: Math.round(b.right), inWrap, clearOfRightcol, hit: hit ? hit.className : null });
        }
      }
      const wrapEl = document.querySelector('.desk-v1-cal-grid-wrap');
      return { n: cells.length, bad, hscroll: wrapEl.scrollWidth - wrapEl.clientWidth };
    });
    r.n >= 2 && r.bad.length === 0
      ? ok(`[${w}px] all ${r.n} weekend cells are fully visible, clear of the Posy column, and are what elementFromPoint returns at their centres`)
      : fail(`[${w}px] weekend cells covered/clipped (n=${r.n}): ${JSON.stringify(r.bad)}`);
    r.hscroll <= 1
      ? ok(`[${w}px] week grid has no horizontal scroll (all 7 day columns fit)`)
      : fail(`[${w}px] week grid overflows its wrapper by ${r.hscroll}px`);

    // Real drag: a scheduled chip hovered over the Sunday cell of its own row
    // must light that cell's drop target (not the Posy box).
    await page.evaluate(() => { window.confirm = () => false; });
    let chip = await page.$('[data-chip-version="v-testers-li"]');
    for (let i = 0; i < 6 && !chip; i++) {
      await page.click('[data-cal-shift="1"]');
      await page.waitForTimeout(30);
      chip = await page.$('[data-chip-version="v-testers-li"]');
    }
    if (!chip) { fail(`[${w}px] no chip to drag onto the weekend`); await ctx.close(); continue; }
    const cb = await chip.boundingBox();
    const row = await chip.evaluateHandle((el) => el.closest('.desk-v1-cal-row'));
    const weekendCells = await row.$$('.desk-v1-cal-cell-weekend');
    const sunday = weekendCells[weekendCells.length - 1];
    const sb = await sunday.boundingBox();
    await page.mouse.move(cb.x + cb.width / 2, cb.y + cb.height / 2);
    await page.mouse.down();
    await page.mouse.move(cb.x + cb.width / 2 + 15, cb.y + cb.height / 2 + 5, { steps: 3 });
    await page.mouse.move(sb.x + sb.width / 2, sb.y + sb.height / 2, { steps: 8 });
    await page.waitForTimeout(60);
    const hoverIsSunday = await sunday.evaluate((el) => el.classList.contains('pd-drop-hover'));
    hoverIsSunday
      ? ok(`[${w}px] dragging a chip over Sunday lights the Sunday cell as the drop target`)
      : fail(`[${w}px] Sunday cell did not receive the drag hover`);
    await page.mouse.up();
    reportUncaught(pageErrors, `[${w}px weekend]`);
    await ctx.close();
  }
}

// ── Phone (§11): "Calendar on phone defaults to an agenda list grouped by
// day" — the grid is hidden, the agenda (day, then channel) is shown. ───────
async function runPhoneLayout(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} }, { width: 390, height: 844 });
  await navToCalendar(page);
  await page.waitForSelector('.desk-v1-calendar', { timeout: 8000 });
  await page.selectOption('[data-cal-view]', 'month');
  await page.waitForTimeout(50);

  const visibility = await page.evaluate(() => ({
    grid: getComputedStyle(document.querySelector('.desk-v1-cal-grid-wrap')).display,
    agenda: getComputedStyle(document.querySelector('.desk-v1-cal-agenda')).display,
  }));
  visibility.grid === 'none' && visibility.agenda !== 'none'
    ? ok(`§11: phone shows the agenda list, not the grid (grid: ${visibility.grid}, agenda: ${visibility.agenda})`)
    : fail(`§11: phone visibility wrong: ${JSON.stringify(visibility)}`);

  const dayLabels = await page.$$eval('.desk-v1-cal-agenda-daylabel', (els) => els.map((e) => e.textContent.trim()));
  dayLabels.length >= 2
    ? ok(`§11: agenda groups by day: ${JSON.stringify(dayLabels)}`)
    : fail(`§11: expected >=2 day groups in month range, got ${JSON.stringify(dayLabels)}`);

  const itemHit = await page.$eval('.desk-v1-cal-agenda-item', (el) => el.getBoundingClientRect().height).catch(() => 0);
  itemHit >= 40
    ? ok(`§11: agenda item hit target ${itemHit.toFixed(0)}px is touch-sized`)
    : fail(`§11: agenda item too short for touch: ${itemHit}px`);

  reportUncaught(pageErrors, '[phone]');
  await ctx.close();
}

// ── screenshots (default tone only, per the ticket brief). Week view, not
// month: it's both the real default and the shape frame 12f actually draws
// (Month's ~30 day-columns need horizontal scroll past 1440px — see the
// deviation note in the final report). Stepped forward once so the shot
// lands on the week actually carrying fixture chips (v-testers-li,
// v-restore-blog on Sep 30) instead of the near-empty week containing today.
async function captureScreenshots(browser) {
  {
    const { ctx, page } = await newBootedPage(browser, { ls: {} }, { width: 1440, height: 950 });
    await navToCalendar(page);
    await page.waitForSelector('.desk-v1-calendar', { timeout: 8000 });
    await page.click('[data-cal-shift="1"]');
    await page.waitForTimeout(80);
    await page.screenshot({ path: resolve(SHOT_DIR, 't4_calendar_desktop_1440.png') });
    ok('desktop screenshot saved: t4_calendar_desktop_1440.png');
    await ctx.close();
  }
  {
    const { ctx, page } = await newBootedPage(browser, { ls: {} }, { width: 390, height: 844 });
    await navToCalendar(page);
    await page.waitForSelector('.desk-v1-calendar', { timeout: 8000 });
    await page.click('[data-cal-shift="1"]');
    await page.waitForTimeout(80);
    await page.screenshot({ path: resolve(SHOT_DIR, 't4_calendar_phone_390.png') });
    ok('phone screenshot saved: t4_calendar_phone_390.png');
    await ctx.close();
  }
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  for (const tone of TONES) await runToneRenderChecks(browser, tone);
  await runScopeToggle(browser);
  await runKeyboardReschedule(browser);
  await runDragReschedule(browser);
  await runR29FieldsAndLegend(browser);
  await runR29SlotCreateAndRefusal(browser);
  await runR29AgentSuggestedAndFill(browser);
  await runR219WhenTimeOnly(browser);
  await runR29UnscheduledDrag(browser);
  await runR29bMonthGridDimensions(browser);
  await runWeekendNotCovered(browser);
  await runPhoneLayout(browser);
  await captureScreenshots(browser);
  exitCode = bad === 0 ? 0 : 1;
} catch (e) {
  console.error('harness error:', e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad === 0 ? `\nAll checks passed.` : `\n${bad} check(s) failed.`);
process.exit(exitCode);

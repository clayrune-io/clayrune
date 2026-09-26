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
  const ctx = await browser.newContext({ viewport: viewport || { width: 1440, height: 950 } });
  const page = await ctx.newPage();
  await page.addInitScript((ls) => {
    try { for (const k of Object.keys(ls)) localStorage.setItem(k, ls[k]); } catch (e) {}
  }, (tone && tone.ls) || {});
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', fulfillOrAbort);
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

  const scopeOn = (await page.textContent('.desk-v1-cal-pill.on').catch(() => '') || '').trim();
  scopeOn === 'This campaign'
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

  // Switch to month view (select), which for "today" in this fixture set's
  // window always covers v-install-x (Sep 22, published) and v-testers-li +
  // v-restore-blog (Sep 30, scheduled / needs_review) in the same grid.
  await page.selectOption('[data-cal-view]', 'month');
  await page.waitForTimeout(50);
  const chipStates = await page.$$eval('.desk-v1-cal-chip', (els) => els.map((e) => e.dataset.state));
  const wantStates = ['verified_published', 'scheduled', 'needs_review'];
  wantStates.every((s) => chipStates.includes(s))
    ? ok(`[${tone.name}] month view surfaces the 3 dated fixture chips with their real per-version states: ${JSON.stringify(chipStates)}`)
    : fail(`[${tone.name}] month view chip states wrong: ${JSON.stringify(chipStates)}`);

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

// ── scope toggle: "All campaigns" pill switches which families/channels
// populate the grid. camp-1 is currently the only campaign in fixtures, so
// this checks the pill's own on/off state flips and re-renders rather than
// a row-count change (that needs a second campaign fixture, out of scope). ──
async function runScopeToggle(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCalendar(page);
  await page.waitForSelector('.desk-v1-calendar', { timeout: 8000 });

  await page.click('[data-cal-scope="all"]');
  const onLabel = (await page.textContent('.desk-v1-cal-pill.on').catch(() => '') || '').trim();
  onLabel === 'All campaigns'
    ? ok('scope pill: clicking "All campaigns" flips the on-state')
    : fail(`scope pill did not flip: ${JSON.stringify(onLabel)}`);

  reportUncaught(pageErrors, '[scope]');
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

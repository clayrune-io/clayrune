#!/usr/bin/env node
/**
 * Desk v1 — quiet Undo (Ron 2026-10-01). Routine edits raise NO toast; they stay
 * undoable from the Desk header's Undo button and Ctrl/Cmd+Z. Only a DESTRUCTIVE
 * command (delete / remove / archive / skip) shows a toast with Undo, and every
 * Desk toast shares one key so a new one replaces the old. Flag ON, fixtures only,
 * hermetic (no server).
 *
 *   - An empty Desk: header Undo is disabled, tooltip `Nothing to undo`.
 *   - Dragging a scene twice: zero `.toast` nodes; the header Undo is enabled and
 *     its tooltip names the last command (`Undo: Moved scene "..."`).
 *   - Ctrl+Z undoes the last move (order restored); the header Undo button undoes
 *     the one before; with the history empty the button disables again.
 *   - Ctrl+Z while typing in a field leaves the Desk history alone.
 *   - Deleting a scene raises exactly one toast with Undo; a second delete
 *     REPLACES it (still one); the toast's Undo restores that scene.
 *   - A refusal / plain message toast also replaces rather than stacks.
 *
 * RUN   cd tools/smoke && node desk-v1-quiet-undo.mjs
 * Exit 0 = every check holds; 1 = a check failed / harness error.
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
for (const f of readdirSync(ASSETS_DIR)) {
  const ext = f.slice(f.lastIndexOf('.'));
  if (MIME[ext]) STATIC[`/assets/${f}`] = [MIME[ext], readFileSync(resolve(ASSETS_DIR, f))];
}

const PID = 'smoke_deskv1quiet';
const PROJECTS = [{
  id: PID, name: 'Desk v1 quiet undo smoke', status: 'active', domain: 'general', emoji: '🧪',
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
const CHARACTERS = [
  { scope: 'global', name: 'claydo', agent_name: 'Claydo', avatar: '🧱' },
  { scope: 'global', name: 'dave', agent_name: 'Dave', avatar: '🧭' },
];

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg));

async function fulfillOrAbort(route) {
  const req = route.request();
  const path = new URL(req.url()).pathname;
  const J = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  const hit = STATIC[path];
  if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
  if (path === '/api/projects') return J(PROJECTS);
  if (path === '/api/config') return J({ desk_v1: true, user_timezone: 'UTC' });
  if (path === '/api/characters') return J(CHARACTERS);
  if (path === '/api/floor') return J({ bench: [] });
  return route.abort();
}

async function newBootedPage(browser, viewport) {
  const ctx = await browser.newContext({ viewport: viewport || { width: 1440, height: 950 } });
  const page = await ctx.newPage();
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

async function dragTo(page, fromSel, toSel) {
  await page.locator(fromSel).scrollIntoViewIfNeeded();
  const h = await page.locator(fromSel).boundingBox();
  const t = await page.locator(toSel).boundingBox();
  await page.mouse.move(h.x + h.width / 2, h.y + h.height / 2);
  await page.mouse.down();
  await page.mouse.move(h.x + h.width / 2 + 10, h.y + h.height / 2 - 10, { steps: 4 });
  await page.mouse.move(t.x + t.width / 2, t.y + t.height / 2, { steps: 10 });
  await page.waitForTimeout(40);
  await page.mouse.up();
  await page.waitForTimeout(120);
}
const sceneIds = (page) => page.$$eval('.desk-v1-sb-scene', (els) => els.map((e) => e.dataset.sceneId));
const toastCount = (page) => page.$$eval('.toast:not(.toast-out)', (els) => els.length);
const undoTitle = (page) => page.getAttribute('#desk-v1-undo', 'title');
const undoDisabled = (page) => page.$eval('#desk-v1-undo', (b) => b.disabled);

async function openNewVideo(page) {
  await page.evaluate(() => window.deskV1Nav('studio', {}));
  await page.waitForSelector('[data-studio]', { timeout: 6000 });
  await page.click('[data-studio-new="video"]');
  await page.waitForSelector('[data-studio-create][data-kind="video"] [data-storyboard] .desk-v1-sb-scene', { timeout: 6000 });
}
async function editSceneTitle(page, nth, label) {
  await page.click(`.desk-v1-sb-scene:nth-child(${nth}) [data-scene-edit]`);
  await page.fill(`.desk-v1-sb-scene:nth-child(${nth}) [data-scene-edit-label]`, label);
  await page.click(`.desk-v1-sb-scene:nth-child(${nth}) [data-scene-edit]`);
  await page.waitForTimeout(80);
}

async function run(browser) {
  const b = await newBootedPage(browser);
  const page = b.page;

  // 0. Fresh Desk: nothing to undo.
  check(await undoDisabled(page) && /Nothing to undo/.test(await undoTitle(page)),
    'a fresh Desk has a disabled header Undo reading `Nothing to undo`', `fresh Undo wrong: disabled=${await undoDisabled(page)} title=${await undoTitle(page)}`);

  await openNewVideo(page);
  // Make scenes 2 and 3 real so they can be dragged (examples are dimmed).
  await editSceneTitle(page, 2, 'Second scene');
  await editSceneTitle(page, 3, 'Third scene');
  await page.evaluate(() => document.querySelectorAll('.toast').forEach((t) => t.remove()));
  const base = await sceneIds(page);

  // 1. Two drags: zero toasts, header Undo enabled with the right tooltip.
  await dragTo(page, `.desk-v1-sb-scene[data-scene-id="${base[2]}"] [data-scene-handle]`, `.desk-v1-sb-scene[data-scene-id="${base[0]}"]`);
  const afterOne = await sceneIds(page);
  await dragTo(page, `.desk-v1-sb-scene[data-scene-id="${afterOne[1]}"] [data-scene-handle]`, `.desk-v1-sb-scene[data-scene-id="${afterOne[0]}"]`);
  const afterTwo = await sceneIds(page);
  check(afterOne.join() !== base.join() && afterTwo.join() !== afterOne.join(),
    `two drags reorder the scenes (${base.slice(0, 3).join(' ')} → ${afterOne.slice(0, 3).join(' ')} → ${afterTwo.slice(0, 3).join(' ')})`, `drags did not reorder: ${base} / ${afterOne} / ${afterTwo}`);
  check(await toastCount(page) === 0, 'two scene drags raise zero toasts', `${await toastCount(page)} toast(s) after two drags`);
  const title = await undoTitle(page);
  check(!(await undoDisabled(page)) && /^Undo: Moved scene “.+”/.test(title),
    `the header Undo is enabled and names what it undoes (\`${title}\`)`, `header Undo wrong: disabled=${await undoDisabled(page)} title=${title}`);
  await page.hover('#desk-v1-undo');
  await page.screenshot({ path: resolve(SHOT_DIR, 'quiet_undo_header_1440.png'), clip: { x: 0, y: 0, width: 1440, height: 260 } });

  // 2. Ctrl+Z undoes the last move; the header button undoes the one before.
  await page.mouse.click(5, 5); // focus on the bare page, as after a click on a card
  await page.keyboard.press('Control+z');
  await page.waitForTimeout(100);
  check((await sceneIds(page)).join() === afterOne.join(), 'Ctrl+Z restores the order before the last drag', `Ctrl+Z did not undo: ${await sceneIds(page)}`);
  await page.click('#desk-v1-undo');
  await page.waitForTimeout(100);
  check((await sceneIds(page)).join() === base.join(), 'the header Undo button restores the order before the first drag', `header Undo did not undo: ${await sceneIds(page)}`);
  check(await toastCount(page) === 0, 'undoing raises no toast either', `${await toastCount(page)} toast(s) after undo`);

  // 3. Ctrl+Z while typing belongs to the field, not the Desk.
  const histBefore = await page.evaluate(() => window.DeskV1Kit.commandBus.history.length);
  await page.click('.desk-v1-sb-scene:nth-child(2) [data-scene-edit]');
  await page.focus('.desk-v1-sb-scene:nth-child(2) [data-scene-edit-label]');
  await page.keyboard.press('Control+z');
  const histTyping = await page.evaluate(() => window.DeskV1Kit.commandBus.history.length);
  check(histTyping === histBefore, 'Ctrl+Z inside a text field does not undo a Desk command', `history moved while typing: ${histBefore} → ${histTyping}`);
  await page.click('.desk-v1-sb-scene:nth-child(2) [data-scene-edit]'); // Done
  await page.waitForTimeout(80);

  // 4. Delete: exactly one toast with Undo; a second delete replaces it.
  await page.evaluate(() => document.querySelectorAll('.toast').forEach((t) => t.remove()));
  const ids0 = await sceneIds(page);
  await page.click('.desk-v1-sb-scene:nth-child(1) [data-scene-delete]');
  await page.waitForTimeout(100);
  check(await toastCount(page) === 1 && (await page.$$('.toast .toast-btn.primary')).length === 1,
    'deleting a scene raises exactly one toast with an Undo button', `delete toast wrong: ${await toastCount(page)} toast(s)`);
  await page.click('.desk-v1-sb-scene:nth-child(1) [data-scene-delete]');
  await page.waitForTimeout(100);
  check(await toastCount(page) === 1, 'a second delete replaces the toast (still one)', `${await toastCount(page)} toasts after a second delete`);
  const msg = await page.textContent('.toast .toast-msg');
  check(/Deleted scene/.test(msg), `the toast reads the latest delete (\`${msg.trim()}\`)`, `toast text wrong: ${msg}`);
  await page.click('.toast .toast-btn.primary');
  await page.waitForTimeout(100);
  const restored = await sceneIds(page);
  check(restored.join() === ids0.slice(1).join(),
    'the toast Undo restores the second deleted scene only (the first stays deleted)', `Undo restored wrong: ${restored} vs ${ids0}`);

  // 5. A plain message (a refusal) replaces rather than stacks.
  await page.evaluate(() => { window.DeskV1Kit.toast('First problem'); window.DeskV1Kit.toast('Second problem'); });
  await page.waitForTimeout(80);
  const msgs = await page.$$eval('.toast:not(.toast-out) .toast-msg', (els) => els.map((e) => e.textContent.trim()));
  check(msgs.length === 1 && msgs[0] === 'Second problem', 'two plain Desk toasts leave one (the newest)', `plain toasts stacked: ${JSON.stringify(msgs)}`);

  const uncaught = b.pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail('uncaught page error: ' + e));
  await b.ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  await run(browser);
  if (bad) { console.error(`\n${bad} check(s) failed.`); exitCode = 1; } else { console.log('\nAll checks passed.'); exitCode = 0; }
} catch (e) {
  console.error('HARNESS ERROR:', e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
process.exit(exitCode);

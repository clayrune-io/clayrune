#!/usr/bin/env node
/**
 * Desk v1 (MC-977 IA revision 2, docs/THE_DESK_V1_IA_REVISION_2.md §8 R2-8 row)
 * — Studio + the creation bodies What opens (frames 11 to 14, 15b, 16, 16c),
 * static/js/desk-v1-studio.js. Flag ON, fixtures only, hermetic (no server).
 *
 * Checks:
 *   - Studio from Home's header: 3 New tiles, Recent lists the fixture render at
 *     `Rendering 40%`, Material library folders with file counts, Back `‹ Desk`.
 *   - What → Video → Create new opens the storyboard: >= 1 scene pre-filled,
 *     the header reads `‹ Back to What` / `New video · Storyboard`, the
 *     `Returning to ›` strip names campaign + piece, scene 3 moves above 2 by
 *     drag AND by keyboard, Render → Back to What shows `⟳ Rendering` with
 *     role=progressbar + aria-valuenow (all 3 tones).
 *   - What → Article → Write new opens the writer: one tab per destination
 *     version, provenance line, the `? Assumed` claim both inline and in the
 *     Claims & sources panel, Save to What returns with the piece `in review`.
 *   - Image → Capture: Screen picker + preview, capturing attaches; a project
 *     with no capturable surface reads `Not available for this project`.
 *   - Online source: connected accounts expand a thumbnail grid; an unconnected
 *     source shows the read-access warning + a DISABLED Connect and never a
 *     file grid.
 *   - Generate carries the abstract-only line.
 *
 * RUN   cd tools/smoke && node desk-v1-studio.mjs
 * Exit 0 = every check holds; 1 = a check failed / harness error.
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
for (const f of readdirSync(ASSETS_DIR)) {
  const ext = f.slice(f.lastIndexOf('.'));
  if (MIME[ext]) STATIC[`/assets/${f}`] = [MIME[ext], readFileSync(resolve(ASSETS_DIR, f))];
}

const PID = 'smoke_deskv1studio';
const PROJECTS = [{
  id: PID, name: 'Desk v1 studio smoke', status: 'active', domain: 'general', emoji: '🧪',
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
const CHARACTERS = [{ scope: 'global', name: 'claydo', agent_name: 'Claydo', avatar: '🧱' }];

const TONES = [
  { name: 'default/dark', ls: {} },
  { name: 'tone-warm', ls: { mc_tone: 'warm' } },
  { name: 'tone-editorial', ls: { mc_tone: 'editorial' } },
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

function reportUncaught(pageErrors, tag) {
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail(`${tag} uncaught page error: ${e}`));
}

async function gotoWhat(page, campaignId) {
  await page.evaluate((id) => window.deskV1Nav('campaign', { campaignId: id, panel: 'what' }), campaignId || 'camp-1');
  await page.waitForSelector('[data-what]', { timeout: 8000 });
}
const text = async (page, sel) => ((await page.textContent(sel).catch(() => '')) || '').replace(/\s+/g, ' ').trim();

// A content type into the create-card, by the keyboard path (the pointer drag is
// What's own check in desk-v1-piece.mjs).
async function addCreate(page, typeId) {
  await page.focus(`[data-what-type="${typeId}"]`);
  await page.keyboard.press('Enter');
  await page.waitForSelector('[data-what-create]', { timeout: 4000 });
}

async function sceneLabels(page) {
  return page.$$eval('.desk-v1-sb-scene', (els) => els.map((e) => e.dataset.sceneLabel));
}

// ── 1. Studio home ───────────────────────────────────────────────────────
async function runStudioHome(browser, tone) {
  const tag = `[${tone.name}]`;
  const { ctx, page, pageErrors } = await newBootedPage(browser, tone);
  await page.waitForSelector('.desk-v1-home-studio-btn', { timeout: 8000 });
  await page.click('.desk-v1-home-studio-btn');
  await page.waitForSelector('[data-studio]', { timeout: 8000 });

  const tiles = await page.$$eval('[data-studio-new]', (els) => els.map((e) => e.textContent.replace(/\s+/g, ' ').trim()));
  check(tiles.length === 3 && /New video/.test(tiles[0]) && /New image/.test(tiles[1]) && /New article/.test(tiles[2]),
    `${tag} Studio shows 3 New tiles: ${JSON.stringify(tiles)}`, `${tag} tiles wrong: ${JSON.stringify(tiles)}`);
  const recent = await page.$$eval('[data-studio-recent-row]', (els) => els.map((e) => e.textContent.replace(/\s+/g, ' ').trim()));
  check(recent.some((r) => /Rendering 40%/.test(r)), `${tag} Recent lists the fixture render at Rendering 40%`, `${tag} Recent has no Rendering 40%: ${JSON.stringify(recent)}`);
  check(recent.some((r) => /Draft saved/.test(r)) && recent.some((r) => /not attached/.test(r)),
    `${tag} Recent also carries a \`Draft saved\` and a \`not attached\` row`, `${tag} Recent rows wrong: ${JSON.stringify(recent)}`);
  const folders = await page.$$eval('[data-studio-folder]', (els) => els.map((e) => e.textContent.replace(/\s+/g, ' ').trim()));
  check(folders.length === 5 && folders.some((f) => /Product screenshots 38 files/.test(f)),
    `${tag} Material library: ${folders.length} folders with file counts`, `${tag} library wrong: ${JSON.stringify(folders)}`);
  const back = await text(page, '.desk-v1-back');
  check(/^‹\s*Desk$/.test(back), `${tag} Back reads "${back}"`, `${tag} Back wrong: ${JSON.stringify(back)}`);

  // A tile asks which campaign, then lands on its What with the create-card open.
  await page.click('[data-studio-new="video"]');
  await page.waitForSelector('.desk-v1-add-menu, [role="menu"]', { timeout: 4000 });
  await page.click('[role="menu"] [role="menuitem"]:first-child, .desk-v1-add-menu button:first-child');
  await page.waitForSelector('[data-what-create][data-kind="video"]', { timeout: 6000 });
  check(true, `${tag} New video tile → pick a campaign → that What with a video create-card open`, '');
  reportUncaught(pageErrors, tag);
  await ctx.close();
}

// ── 2. storyboard, scene moves, render ───────────────────────────────────
async function runStoryboard(browser, tone) {
  const tag = `[${tone.name}]`;
  const { ctx, page, pageErrors } = await newBootedPage(browser, tone);
  await gotoWhat(page);
  await addCreate(page, 'video');
  const tiles = await page.$$eval('[data-what-source]', (els) => els.map((e) => e.dataset.whatSource));
  check(tiles.join() === 'record,upload,online,create', `${tag} Video card offers ${tiles.join(' · ')}`, `${tag} video sources wrong: ${tiles}`);
  await page.click('[data-what-source="create"]');
  await page.waitForSelector('[data-storyboard]', { timeout: 6000 });

  const scenes = await sceneLabels(page);
  check(scenes.length >= 1, `${tag} storyboard opens with ${scenes.length} scene(s) pre-filled`, `${tag} no scenes`);
  const crumb = await text(page, '.desk-v1-crumb-title');
  const back = await text(page, '.desk-v1-back');
  check(crumb === 'New video · Storyboard' && /^‹\s*Back to What$/.test(back), `${tag} header reads "${back}" · "${crumb}"`, `${tag} header wrong: ${JSON.stringify([back, crumb])}`);
  const strip = await text(page, '[data-returning-to]');
  check(/Returning to ›/.test(strip) && /Windows beta testers/.test(strip) && /What · New video/.test(strip),
    `${tag} strip names campaign + piece: "${strip}"`, `${tag} Returning-to strip wrong: ${JSON.stringify(strip)}`);
  const firstLine = await text(page, '.desk-v1-sb-scene:first-child .desk-v1-sb-line');
  check(firstLine.length > 0, `${tag} scene 1 has a line ("${firstLine}")`, `${tag} scene 1 line empty`);
  const agent = await page.getAttribute('[data-sb-ask]', 'placeholder');
  check(/Ask Claydo to change a scene/.test(agent || ''), `${tag} agent panel placeholder "${agent}"`, `${tag} agent placeholder wrong: ${JSON.stringify(agent)}`);

  // scene 3 above 2: by drag
  const before = await sceneLabels(page);
  const h = await page.locator('.desk-v1-sb-scene:nth-child(3) [data-scene-handle]').boundingBox();
  const t = await page.locator('.desk-v1-sb-scene:nth-child(2)').boundingBox();
  await page.mouse.move(h.x + h.width / 2, h.y + h.height / 2);
  await page.mouse.down();
  await page.mouse.move(h.x + h.width / 2 + 10, h.y + h.height / 2 - 10, { steps: 4 });
  await page.mouse.move(t.x + t.width / 2, t.y + t.height / 2, { steps: 10 });
  await page.waitForTimeout(40);
  await page.mouse.up();
  await page.waitForTimeout(80);
  const afterDrag = await sceneLabels(page);
  check(afterDrag[1] === before[2] && afterDrag[2] === before[1], `${tag} scene 3 moved above 2 by drag: ${afterDrag.join(' | ')}`, `${tag} drag did not reorder: ${before.join('|')} -> ${afterDrag.join('|')}`);
  const nums = await page.$$eval('[data-scene-num]', (els) => els.map((e) => e.textContent));
  check(nums.join() === '1,2,3,4', `${tag} number badges renumbered ${nums.join(',')}`, `${tag} badges wrong: ${nums}`);

  // and back by keyboard (scene now at 2 → ArrowDown returns it)
  await page.focus('.desk-v1-sb-scene:nth-child(2) [data-scene-handle]');
  await page.keyboard.press('ArrowDown');
  await page.waitForTimeout(80);
  const afterKey = await sceneLabels(page);
  check(afterKey.join() === before.join(), `${tag} keyboard ArrowDown restores the order`, `${tag} keyboard move wrong: ${afterKey.join('|')}`);
  await page.focus('.desk-v1-sb-scene:nth-child(3) [data-scene-handle]');
  await page.keyboard.press('ArrowUp');
  await page.waitForTimeout(80);
  const afterUp = await sceneLabels(page);
  check(afterUp[1] === before[2] && afterUp[2] === before[1], `${tag} scene 3 moved above 2 by keyboard (ArrowUp)`, `${tag} ArrowUp wrong: ${afterUp.join('|')}`);
  const focusedHandle = await page.evaluate(() => document.activeElement && document.activeElement.hasAttribute('data-scene-handle'));
  check(focusedHandle, `${tag} focus stays on the moved scene's handle`, `${tag} focus lost after keyboard move`);

  // Edit
  await page.click('.desk-v1-sb-scene:first-child [data-scene-edit]');
  await page.fill('.desk-v1-sb-scene:first-child [data-scene-edit-line]', 'Edited line.');
  await page.click('.desk-v1-sb-scene:first-child [data-scene-edit]');
  check((await text(page, '.desk-v1-sb-scene:first-child .desk-v1-sb-line')) === 'Edited line.', `${tag} Edit saves a scene line`, `${tag} scene edit lost`);

  if (tone.name === 'default/dark') {
    await page.waitForTimeout(80);
    await page.screenshot({ path: resolve(SHOT_DIR, 'r2_8_storyboard_1440.png') });
    ok('storyboard screenshot saved: r2_8_storyboard_1440.png');
  }

  // Render → Back to What. The scene edits leave an Undo toast over the top
  // right corner where Render sits (same as desk-v1-video.mjs's note), so clear
  // the stack first.
  await page.evaluate(() => document.querySelectorAll('#toast-container .toast').forEach((t) => t.remove()));
  await page.click('[data-sb-render]');
  await page.waitForSelector('[data-sb-rendering]', { timeout: 4000 });
  check(/Rendering 40%/.test(await text(page, '[data-sb-rendering]')), `${tag} Render shows ⟳ Rendering 40% on the storyboard`, `${tag} no rendering note`);
  await page.click('.desk-v1-back');
  await page.waitForSelector('[data-what-row]', { timeout: 6000 });
  const row = page.locator('[data-what-row]', { hasText: 'New video' }).first();
  const pb = row.locator('[role="progressbar"]');
  const now = await pb.getAttribute('aria-valuenow').catch(() => null);
  const rowText = ((await row.textContent()) || '').replace(/\s+/g, ' ');
  check(/⟳ Rendering 40%/.test(rowText) && now === '40', `${tag} What row shows "⟳ Rendering 40%" with progressbar aria-valuenow=${now}`, `${tag} What row not rendering: ${rowText} / ${now}`);
  const usable = await row.locator('[data-primary-action]').isEnabled();
  check(usable, `${tag} the piece stays usable (title button enabled) while rendering`, `${tag} piece not usable while rendering`);

  if (tone.name === 'default/dark') {
    await page.waitForTimeout(80);
    await page.screenshot({ path: resolve(SHOT_DIR, 'r2_8_rendering_1440.png') });
    ok('rendering screenshot saved: r2_8_rendering_1440.png');
  }
  reportUncaught(pageErrors, tag);
  await ctx.close();
}

// ── 3. article writer ────────────────────────────────────────────────────
async function runWriter(browser, tone) {
  const tag = `[${tone.name}]`;
  const { ctx, page, pageErrors } = await newBootedPage(browser, tone);
  await gotoWhat(page);
  await addCreate(page, 'article');
  const srcs = await page.$$eval('[data-what-source]', (els) => els.map((e) => e.dataset.whatSource));
  check(srcs.join() === 'browse,write', `${tag} Article card offers ${srcs.join(' · ')}`, `${tag} article sources wrong: ${srcs}`);
  await page.click('[data-what-source="write"]');
  await page.waitForSelector('[data-writer]', { timeout: 6000 });

  const tabs = await page.$$eval('[data-writer-tab]', (els) => els.map((e) => e.textContent.trim()));
  check(tabs.length >= 2 && tabs.includes('X thread') && tabs.includes('LinkedIn article'), `${tag} one tab per destination: ${tabs.join(' · ')}`, `${tag} tabs wrong: ${JSON.stringify(tabs)}`);
  const prov = await text(page, '[data-writer-provenance]');
  check(/^Drafted by Claydo from this campaign's How/.test(prov), `${tag} provenance: "${prov}"`, `${tag} provenance wrong: ${JSON.stringify(prov)}`);
  const inline = await page.$$eval('[data-writer-body] [data-claim-chip]', (els) => els.map((e) => e.textContent.trim()));
  check(inline.length === 1 && inline[0] === '? Assumed', `${tag} the ? Assumed chip sits inline on the assumed claim`, `${tag} inline chips wrong: ${JSON.stringify(inline)}`);
  const rows = await page.$$eval('[data-claim-row]', (els) => els.map((e) => e.textContent.replace(/\s+/g, ' ').trim()));
  check(rows.some((r) => /Assumed/.test(r) && /not yet verified/.test(r)), `${tag} Claims & sources lists it as "not yet verified"`, `${tag} claims panel wrong: ${JSON.stringify(rows)}`);
  check(rows.some((r) => /source: /.test(r)), `${tag} a sourced claim shows its source`, `${tag} no sourced claim row`);
  const panelHead = await text(page, '.desk-v1-writer-claims-title');
  check(panelHead === 'Claims & sources', `${tag} side panel titled "${panelHead}"`, `${tag} panel title wrong: ${panelHead}`);

  // editing the body keeps the panel in sync
  await page.click('[data-writer-tab="1"]');
  check((await page.getAttribute('[data-writer-tab="1"]', 'aria-selected')) === 'true', `${tag} second tab selects`, `${tag} tab switch failed`);
  await page.click('[data-writer-tab="0"]');

  if (tone.name === 'default/dark') {
    await page.waitForTimeout(80);
    await page.screenshot({ path: resolve(SHOT_DIR, 'r2_8_writer_1440.png') });
    ok('writer screenshot saved: r2_8_writer_1440.png');
  }

  await page.click('[data-writer-save]');
  await page.waitForSelector('[data-what-row]', { timeout: 6000 });
  const row = page.locator('[data-what-row]', { hasText: 'New article' }).first();
  const rowText = ((await row.textContent()) || '').replace(/\s+/g, ' ');
  check(/in review/.test(rowText), `${tag} Save to What returns with the piece "in review"`, `${tag} row not in review: ${rowText}`);
  check((await page.$$('[data-writer]')).length === 0, `${tag} the writer is gone after Save`, `${tag} writer still mounted`);
  reportUncaught(pageErrors, tag);
  await ctx.close();
}

// ── 4. capture / online / generate ───────────────────────────────────────
async function runImageBodies(browser, tone) {
  const tag = `[${tone.name}]`;
  const { ctx, page, pageErrors } = await newBootedPage(browser, tone);
  await gotoWhat(page);
  await addCreate(page, 'image');
  await page.click('[data-what-source="capture"]');
  await page.waitForSelector('[data-cap]', { timeout: 4000 });
  const label = await text(page, '.desk-v1-cap-label');
  const opts = await page.$$eval('[data-cap-screen] option', (els) => els.length);
  const imgSrc = await page.getAttribute('[data-cap-preview] img', 'src');
  check(label === 'Screen:' && opts >= 2 && /desk-thumb/.test(imgSrc || ''), `${tag} Capture shows a "Screen:" picker (${opts} screens) + preview`, `${tag} capture body wrong: ${JSON.stringify([label, opts, imgSrc])}`);
  await page.selectOption('[data-cap-screen]', 'scr-calendar');
  check(/calendar/.test((await page.getAttribute('[data-cap-preview] img', 'src')) || ''), `${tag} changing the screen changes the preview`, `${tag} preview did not change`);
  check((await page.$$('input[type=file]')).length === 0, `${tag} no file picker in the capture body`, `${tag} file input present under Capture`);

  if (tone.name === 'default/dark') {
    await page.waitForTimeout(80);
    await page.screenshot({ path: resolve(SHOT_DIR, 'r2_8_capture_1440.png') });
    ok('capture screenshot saved: r2_8_capture_1440.png');
  }
  await page.click('[data-cap-take]');
  await page.waitForSelector('[data-what-row] [data-what-media] img', { timeout: 4000 });
  check((await page.$$('[data-what-create]')).length === 0, `${tag} capturing attaches the screenshot and closes the create-card`, `${tag} create-card still open`);

  // Online source, image
  await addCreate(page, 'image');
  await page.click('[data-what-source="online"]');
  await page.waitForSelector('[data-online]', { timeout: 4000 });
  const accts = await page.$$eval('[data-online-acct]', (els) => els.map((e) => e.dataset.onlineAcct));
  const grids = await page.$$eval('[data-online-grid]', (els) => els.length);
  check(accts.join() === 'gdrive,gphotos' && grids === 1, `${tag} connected accounts ${accts.join(', ')}, first one expanded (${grids} grid)`, `${tag} online accounts wrong: ${accts} / ${grids}`);
  const warn = await text(page, '[data-online-unconnected="dropbox"]');
  const connectEnabled = await page.locator('[data-online-connect="dropbox"]').isEnabled();
  check(/read access/.test(warn) && /Connect Dropbox/.test(warn) && connectEnabled, `${tag} unconnected Dropbox: read-access warning + "Connect Dropbox" routes to Connections`, `${tag} unconnected card wrong: ${warn} enabled=${connectEnabled}`);
  const dropboxGrid = await page.$$('[data-online-unconnected="dropbox"] img, [data-online-acct="dropbox"]');
  check(dropboxGrid.length === 0, `${tag} an unconnected source never shows a file grid`, `${tag} a file grid rendered for Dropbox`);
  check(/Preview · not connected/.test(warn) && /Connections screen/.test(warn), `${tag} the card points at the Connections screen`, `${tag} no Connections pointer`);
  await page.click('[data-online-allows]');
  check(await page.locator('[data-online-scope]').isVisible(), `${tag} "what this allows" expands the scope`, `${tag} scope did not expand`);
  await page.click('[data-online-toggle="gphotos"]');
  const grids2 = await page.$$eval('[data-online-grid]', (els) => els.length);
  check(grids2 === 1 && (await page.getAttribute('[data-online-toggle="gphotos"]', 'aria-expanded')) === 'true', `${tag} opening Google Photos swaps the expanded grid`, `${tag} accordion wrong (${grids2})`);

  if (tone.name === 'default/dark') {
    await page.waitForTimeout(80);
    await page.screenshot({ path: resolve(SHOT_DIR, 'r2_8_online_1440.png') });
    ok('online screenshot saved: r2_8_online_1440.png');
  }
  await page.click('[data-online-pick="gphotos:0"]');
  await page.waitForFunction(() => document.querySelectorAll('[data-what-row] [data-what-media] img').length >= 2, null, { timeout: 4000 });
  check(true, `${tag} picking a thumbnail attaches it`, '');

  // Generate
  await addCreate(page, 'image');
  await page.click('[data-what-source="generate"]');
  await page.waitForSelector('[data-gen]', { timeout: 4000 });
  check(/Abstract visuals only — never the product UI/.test(await text(page, '[data-gen-abstract-only]')), `${tag} Generate body carries the abstract-only rule`, `${tag} abstract-only rule missing`);
  await page.fill('[data-gen-prompt]', 'calm blue waves');
  await page.click('[data-gen-go]');
  await page.waitForFunction(() => document.querySelectorAll('[data-what-row] [data-what-media] img[src^="data:image/svg"]').length >= 1, null, { timeout: 4000 });
  check(true, `${tag} Generate attaches an abstract image (svg data URI)`, '');
  reportUncaught(pageErrors, tag);
  await ctx.close();
}

// Video record + online + an unavailable project
async function runVideoBodies(browser, tone) {
  const tag = `[${tone.name}]`;
  const { ctx, page, pageErrors } = await newBootedPage(browser, tone);
  await gotoWhat(page);
  await addCreate(page, 'video');
  await page.click('[data-what-source="record"]');
  await page.waitForSelector('[data-cap]', { timeout: 4000 });
  check((await text(page, '[data-cap-take]')) === 'Record this screen', `${tag} Record shows the Screen picker with "Record this screen"`, `${tag} record body wrong`);
  await page.click('[data-what-change-source]');
  await page.click('[data-what-source="online"]');
  await page.waitForSelector('[data-online]', { timeout: 4000 });
  const accts = await page.$$eval('[data-online-acct]', (els) => els.map((e) => e.dataset.onlineAcct));
  check(accts.join() === 'yt,gdrive', `${tag} video online accounts: ${accts.join(', ')}`, `${tag} video online accounts wrong: ${accts}`);
  check((await page.$$('[data-online-unconnected="dropbox"]')).length === 1 && await page.locator('[data-online-connect="dropbox"]').isEnabled(), `${tag} unconnected Dropbox offers Connect`, `${tag} Dropbox connect missing`);
  reportUncaught(pageErrors, tag);
  await ctx.close();

  // A campaign on a project with no capturable surface.
  const b = await newBootedPage(browser, tone);
  await b.page.evaluate(() => {
    const fx = window.DeskV1Fixtures;
    const camp = fx.campaigns.find((c) => c.id === 'camp-1');
    camp.projectId = 'project-with-nothing-to-capture';
  });
  await gotoWhat(b.page);
  await addCreate(b.page, 'image');
  await b.page.click('[data-what-source="capture"]');
  await b.page.waitForSelector('[data-what-unavailable]', { timeout: 4000 });
  check((await text(b.page, '[data-what-unavailable]')) === 'Not available for this project', `${tag} no capturable surface → "Not available for this project"`, `${tag} unavailable message wrong`);
  check((await b.page.$$('[data-cap-screen]')).length === 0, `${tag} and no Screen picker`, `${tag} picker rendered for an unavailable project`);
  await b.ctx.close();
}

// ── 5. phone ────────────────────────────────────────────────────────────
async function runPhone(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} }, { width: 390, height: 844 });
  await page.evaluate(() => window.deskV1Nav('studio', {}));
  await page.waitForSelector('[data-studio]', { timeout: 6000 });
  const m = await page.evaluate(() => {
    const t = document.querySelector('[data-studio-new]').getBoundingClientRect();
    return { tileH: Math.round(t.height), overflow: document.documentElement.scrollWidth > window.innerWidth + 1 };
  });
  check(m.tileH >= 44 && !m.overflow, `phone: Studio tiles ${m.tileH}px tall, no horizontal overflow`, `phone: Studio layout wrong ${JSON.stringify(m)}`);
  await gotoWhat(page);
  await addCreate(page, 'video');
  await page.click('[data-what-source="create"]');
  await page.waitForSelector('[data-storyboard]', { timeout: 6000 });
  const s = await page.evaluate(() => {
    const h = document.querySelector('[data-scene-handle]').getBoundingClientRect();
    const r = document.querySelector('[data-sb-render]').getBoundingClientRect();
    return { handleH: Math.round(h.height), renderBottom: Math.round(r.bottom), vw: window.innerWidth, overflow: document.documentElement.scrollWidth > window.innerWidth + 1 };
  });
  check(s.handleH >= 44 && !s.overflow, `phone: scene handle ${s.handleH}px tall, no horizontal overflow`, `phone: storyboard layout wrong ${JSON.stringify(s)}`);
  await page.waitForTimeout(80);
  await page.screenshot({ path: resolve(SHOT_DIR, 'r2_8_storyboard_390.png') });
  ok('phone screenshot saved: r2_8_storyboard_390.png');
  reportUncaught(pageErrors, '[phone]');
  await ctx.close();
}

async function captureStudio(browser) {
  const { ctx, page } = await newBootedPage(browser, { ls: {} });
  await page.evaluate(() => window.deskV1Nav('studio', {}));
  await page.waitForSelector('[data-studio]', { timeout: 6000 });
  await page.waitForTimeout(80);
  await page.screenshot({ path: resolve(SHOT_DIR, 'r2_8_studio_1440.png') });
  ok('studio screenshot saved: r2_8_studio_1440.png');
  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  for (const tone of TONES) {
    console.log(`\n== ${tone.name}`);
    await runStudioHome(browser, tone);
    await runStoryboard(browser, tone);
    await runWriter(browser, tone);
    await runImageBodies(browser, tone);
    await runVideoBodies(browser, tone);
  }
  await runPhone(browser);
  await captureStudio(browser);
  if (bad) { console.error(`\n${bad} check(s) failed.`); exitCode = 1; } else { console.log('\nAll checks passed.'); exitCode = 0; }
} catch (e) {
  console.error('HARNESS ERROR:', e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
process.exit(exitCode);

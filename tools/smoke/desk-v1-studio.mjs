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
 *   - Standalone (MC-1024): the New video / New image tiles open creation with no
 *     campaign picker; a capture is saved to the Material library, listed in
 *     Recent and in a campaign's What library picker; a no-campaign Recent row
 *     opens the item; `Use in a campaign` attaches it as a piece.
 *   - Studio review fixes (MC-1024 follow-up): a library folder opens and lists
 *     its files with a way back; a new video's scenes 2 to 4 are dimmed
 *     examples left out of Render and the saved item until edited; a scene is
 *     deleted by its trash button or by dragging it onto the trash zone (Undo
 *     restores); the Your agent box is a picker of global agents only and the
 *     pick persists on the item; the timeline bar reorders the list by drag.
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
import { seedDeskV1Fixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

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
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));
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
// Two global agents and one project-only one: the Studio picker lists only the
// agents provisioned on ALL projects (scope global).
const CHARACTERS = [
  { scope: 'global', name: 'claydo', agent_name: 'Claydo', avatar: '🧱' },
  { scope: 'global', name: 'dave', agent_name: 'Dave', avatar: '🧭' },
  { scope: 'project', name: 'projonly', agent_name: 'ProjOnly', avatar: '🔒' },
];

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

  await seedDeskV1Fixtures(page);
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

  // The article tile opens the standalone article page (Topic, Project, Campaign)
  // like New video: no campaign menu. desk-v1-studio-article.mjs covers the page.
  await page.click('[data-studio-new="article"]');
  await page.waitForSelector('[data-studio-create][data-kind="article"] [data-sa-topic]', { timeout: 6000 });
  check((await page.$$('.desk-v1-add-menu, [role="menu"]')).length === 0, `${tag} New article tile opens its page with no campaign menu`, `${tag} a menu opened for the article tile`);
  reportUncaught(pageErrors, tag);
  await ctx.close();
}

// ── 1b. Studio is a standalone workshop (MC-1024) ────────────────────────
async function openStudio(page) {
  await page.evaluate(() => window.deskV1Nav('studio', {}));
  await page.waitForSelector('[data-studio]', { timeout: 6000 });
}

async function runStandalone(browser, tone) {
  const tag = `[${tone.name}]`;

  // Video tile: the creation page opens at once, no campaign picker, no campaign.
  let b = await newBootedPage(browser, tone);
  let page = b.page;
  await openStudio(page);
  await page.click('[data-studio-new="video"]');
  await page.waitForSelector('[data-studio-create][data-kind="video"] [data-storyboard]', { timeout: 6000 });
  check((await page.$$('.desk-v1-add-menu, [role="menu"]')).length === 0, `${tag} New video tile opens creation with no campaign picker`, `${tag} a menu opened for the video tile`);
  const strip = await text(page, '[data-sb-standalone]');
  check(/not attached to a campaign/.test(strip) && (await page.$$('[data-returning-to]')).length === 0,
    `${tag} the video page names no campaign: "${strip}"`, `${tag} video page still tied to a campaign: ${JSON.stringify(strip)}`);
  check((await page.$$('[data-sc-product]')).length === 1 && (await page.$$('[data-what]')).length === 0,
    `${tag} optional Product selector present, not on a campaign's What`, `${tag} product selector / What wrong`);
  const back = await text(page, '.desk-v1-back');
  check(/^‹\s*Studio$/.test(back), `${tag} Back reads "${back}"`, `${tag} Back wrong: ${JSON.stringify(back)}`);
  reportUncaught(b.pageErrors, tag);
  await b.ctx.close();

  // Image tile → capture → saved to the library → Studio's Recent → What's library.
  b = await newBootedPage(browser, tone);
  page = b.page;
  await openStudio(page);
  await page.click('[data-studio-new="image"]');
  await page.waitForSelector('[data-studio-create][data-kind="image"] [data-sc-source]', { timeout: 6000 });
  check((await page.$$('.desk-v1-add-menu, [role="menu"]')).length === 0, `${tag} New image tile opens creation with no campaign picker`, `${tag} a menu opened for the image tile`);
  const prodOpts = await page.$$eval('[data-sc-product] option', (els) => els.map((e) => e.value));
  const prod = prodOpts.find((v) => v);
  if (prod) await page.selectOption('[data-sc-product]', prod);
  await page.click('[data-sc-source="capture"]');
  await page.waitForSelector('[data-cap]', { timeout: 4000 });
  await page.click('[data-cap-take]');
  await page.waitForSelector('[data-studio-create][data-view="item"] [data-sc-item]', { timeout: 4000 });
  const saved = await text(page, '[data-sc-saved]');
  check(/Saved to the Material library/.test(saved), `${tag} the capture is saved to the library: "${saved}"`, `${tag} no library confirmation: ${JSON.stringify(saved)}`);
  check((await page.$$('[data-sc-use]')).length === 1, `${tag} a finished item offers "Use in a campaign"`, `${tag} no Use in a campaign`);
  const title = await text(page, '[data-sc-item-title]');

  await openStudio(page);
  const recent = await page.$$eval('[data-studio-recent-row]', (els) => els.map((e) => ({ t: e.textContent.replace(/\s+/g, ' ').trim(), c: e.dataset.campaignId || null })));
  check(recent.some((r) => r.t.includes(title) && !r.c), `${tag} Recent lists "${title}" with no campaign`, `${tag} Recent missing the item: ${JSON.stringify(recent)}`);

  // It is in the What source picker's library for any campaign.
  await gotoWhat(page, 'camp-1');
  await addCreate(page, 'image');
  await page.click('[data-what-source="upload"]');
  await page.waitForSelector('[data-what-lib]', { timeout: 4000 });
  const libNames = await page.$$eval('[data-what-lib] [data-what-folder] .desk-v1-what-folder-name', (els) => els.map((e) => e.textContent.trim()));
  check(libNames.includes(title), `${tag} the created item appears in a campaign's What library picker (${libNames.length} tiles)`, `${tag} library picker lacks "${title}": ${JSON.stringify(libNames)}`);
  reportUncaught(b.pageErrors, tag);
  await b.ctx.close();

  // A Recent row with no campaign opens the item, not a campaign; Use in a
  // campaign attaches it as a piece in that campaign's What.
  b = await newBootedPage(browser, tone);
  page = b.page;
  await openStudio(page);
  await page.click('[data-studio-recent-row="rec-dashboard-hero"]');
  await page.waitForSelector('[data-studio-create][data-view="item"]', { timeout: 4000 });
  check((await text(page, '[data-sc-item-title]')) === 'Dashboard hero' && (await page.$$('[data-what]')).length === 0,
    `${tag} a Recent row with no campaign opens the item, not a campaign`, `${tag} Recent row did not open the item`);
  await page.click('[data-sc-use]');
  await page.waitForSelector('.desk-v1-add-menu, [role="menu"]', { timeout: 4000 });
  const rowsBefore = await page.evaluate(() => window.DeskV1Fixtures.families.length);
  await page.click('[role="menu"] [role="menuitem"]:first-child, .desk-v1-add-menu button:first-child');
  await page.waitForSelector('[data-what]', { timeout: 6000 });
  const attached = await page.evaluate(() => window.DeskV1Fixtures.families.filter((f) => f.title === 'Dashboard hero' && f.assets && f.assets.length).length);
  const rowsAfter = await page.evaluate(() => window.DeskV1Fixtures.families.length);
  check(attached === 1 && rowsAfter === rowsBefore + 1, `${tag} Use in a campaign attaches it as a piece in that What`, `${tag} attach wrong: attached=${attached} families ${rowsBefore}→${rowsAfter}`);
  reportUncaught(b.pageErrors, tag);
  await b.ctx.close();
}

// ── 1c. Studio review fixes (MC-1024 follow-up, Ron 2026-10-01) ──────────
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
  await page.waitForTimeout(100);
}
const sceneIds = (page) => page.$$eval('.desk-v1-sb-scene', (els) => els.map((e) => e.dataset.sceneId));
async function openNewVideo(page) {
  await openStudio(page);
  await page.click('[data-studio-new="video"]');
  await page.waitForSelector('[data-studio-create][data-kind="video"] [data-storyboard] .desk-v1-sb-scene', { timeout: 6000 });
}
// Make an example scene real the way a user does: Edit, change the title, Done.
async function editSceneTitle(page, nth, label) {
  await page.click(`.desk-v1-sb-scene:nth-child(${nth}) [data-scene-edit]`);
  await page.fill(`.desk-v1-sb-scene:nth-child(${nth}) [data-scene-edit-label]`, label);
  await page.click(`.desk-v1-sb-scene:nth-child(${nth}) [data-scene-edit]`);
  await page.waitForTimeout(60);
}
const clearToasts = (page) => page.evaluate(() => document.querySelectorAll('.toast').forEach((t) => t.remove()));
const agentReady = (page) => page.waitForFunction(() => { const s = document.querySelector('[data-sb-agent-pick]'); return s && !s.disabled; }, null, { timeout: 4000 });

async function runStudioReview(browser, tone) {
  const tag = `[${tone.name}]`;
  const shot = tone.name === 'default/dark';

  // 1. A library folder opens, lists its files, and has a way back.
  let b = await newBootedPage(browser, tone);
  let page = b.page;
  await openStudio(page);
  await page.click('[data-studio-folder="lib-screenshots"]');
  await page.waitForSelector('[data-studio-folderview="lib-screenshots"]', { timeout: 4000 });
  const files = await page.$$eval('[data-studio-file]', (els) => els.map((e) => e.textContent.replace(/\s+/g, ' ').trim()));
  check(files.length === 38 && /screenshot-01\.png/.test(files[0]) && (await page.$$('[data-studio-folder]')).length === 0,
    `${tag} a library folder opens in place and lists its ${files.length} files`, `${tag} folder view wrong: ${files.length} files ${JSON.stringify(files.slice(0, 2))}`);
  check(/Product screenshots · 38 files/.test(await text(page, '.desk-v1-studio-folderbar-name')),
    `${tag} the open folder is named with its file count`, `${tag} folder bar wrong`);
  await page.click('[data-studio-lib-back]');
  await page.waitForSelector('[data-studio-folder]', { timeout: 4000 });
  check((await page.$$('[data-studio-folder]')).length === 5 && (await page.$$('[data-studio-folderview]')).length === 0,
    `${tag} the back button returns to the 5 folders`, `${tag} back did not restore the shelf`);
  if (shot) {
    await page.click('[data-studio-folder="lib-screenshots"]');
    await page.waitForSelector('[data-studio-file]');
    await page.waitForTimeout(150);
    await page.screenshot({ path: resolve(SHOT_DIR, 'studio_review_1_folder_1440.png') });
  }
  reportUncaught(b.pageErrors, tag);
  await b.ctx.close();

  // 2. Scenes 2 to 4 are dimmed examples: not rendered, not saved, real once edited.
  b = await newBootedPage(browser, tone);
  page = b.page;
  await openNewVideo(page);
  const ph = await page.$$eval('.desk-v1-sb-scene', (els) => els.map((e) => ({ ph: e.hasAttribute('data-scene-placeholder'), op: Number(getComputedStyle(e).opacity) })));
  check(ph.length === 4 && !ph[0].ph && ph.slice(1).every((x) => x.ph) && ph[0].op === 1 && ph.slice(1).every((x) => x.op < 1),
    `${tag} a new video opens with 4 scenes: 1 real, 3 dimmed examples (opacity ${ph.slice(1).map((x) => x.op).join('/')})`, `${tag} scene states wrong: ${JSON.stringify(ph)}`);
  if (shot) { await page.waitForTimeout(150); await page.screenshot({ path: resolve(SHOT_DIR, 'studio_review_2_examples_1440.png') }); }
  await page.click('[data-sb-render]');
  await page.waitForSelector('[data-sb-rendering]', { timeout: 4000 });
  let item = await page.evaluate(() => window.DeskV1Store.state().studio.items[0]);
  check(item.render.scenes.length === 1 && item.scenes.length === 1,
    `${tag} Render and the saved item carry 1 scene, not the 3 examples`, `${tag} payload wrong: render ${item.render.scenes.length}, item ${item.scenes.length}`);
  await clearToasts(page);
  await editSceneTitle(page, 2, 'My own scene');
  const after = await page.$$eval('.desk-v1-sb-scene', (els) => els.map((e) => e.hasAttribute('data-scene-placeholder')));
  item = await page.evaluate(() => window.DeskV1Store.state().studio.items[0]);
  check(after.join() === 'false,false,true,true' && item.scenes.length === 2 && item.scenes[1].label === 'My own scene',
    `${tag} editing an example makes it a real scene (then 2 are saved)`, `${tag} edit did not promote the scene: ${after} / ${item.scenes.length}`);
  reportUncaught(b.pageErrors, tag);
  await b.ctx.close();

  // 3. Delete: the trash button, then dragging onto the trash zone; both Undo.
  b = await newBootedPage(browser, tone);
  page = b.page;
  await openNewVideo(page);
  const ids0 = await sceneIds(page);
  await page.click('.desk-v1-sb-scene:nth-child(1) [data-scene-delete]');
  await page.waitForTimeout(80);
  let ids = await sceneIds(page);
  check(ids.length === 3 && !ids.includes(ids0[0]), `${tag} the trash button deletes a scene (4 → ${ids.length})`, `${tag} trash button did not delete: ${ids}`);
  await page.click('#desk-v1-undo'); // Ron 2026-10-02: no destructive popup; the header Undo
  await page.waitForTimeout(80);
  check((await sceneIds(page)).join() === ids0.join(), `${tag} Undo restores the deleted scene in its place`, `${tag} Undo did not restore: ${await sceneIds(page)}`);
  await clearToasts(page);
  await dragTo(page, '.desk-v1-sb-scene:nth-child(2) [data-scene-handle]', '[data-sb-trash]');
  ids = await sceneIds(page);
  check(ids.length === 3 && !ids.includes(ids0[1]), `${tag} dragging a scene onto the trash zone deletes it (4 → ${ids.length})`, `${tag} drag-to-trash failed: ${ids}`);
  await page.click('#desk-v1-undo'); // Ron 2026-10-02: no destructive popup; the header Undo
  await page.waitForTimeout(80);
  check((await sceneIds(page)).join() === ids0.join(), `${tag} Undo restores the scene dragged to the trash`, `${tag} Undo after drag-to-trash failed: ${await sceneIds(page)}`);
  if (shot) { await clearToasts(page); await page.waitForTimeout(150); await page.screenshot({ path: resolve(SHOT_DIR, 'studio_review_3_trash_1440.png') }); }
  reportUncaught(b.pageErrors, tag);
  await b.ctx.close();

  // 4. The agent box is a picker of global agents; the pick persists on the item.
  b = await newBootedPage(browser, tone);
  page = b.page;
  await openNewVideo(page);
  await agentReady(page);
  const opts = await page.$$eval('[data-sb-agent-pick] option', (els) => els.map((e) => e.value));
  check(opts.join() === ',global:claydo,global:dave', `${tag} the picker lists the default + global agents only: ${opts.join(' | ')}`, `${tag} picker options wrong: ${JSON.stringify(opts)}`);
  await page.selectOption('[data-sb-agent-pick]', 'global:dave');
  await page.waitForTimeout(60);
  check((await text(page, '[data-sb-agent-head] strong')) === 'Dave' && /Ask Dave/.test((await page.getAttribute('[data-sb-ask]', 'placeholder')) || ''),
    `${tag} the box takes the picked agent's name`, `${tag} agent box did not follow the pick: ${await text(page, '[data-sb-agent-head]')}`);
  if (shot) { await clearToasts(page); await page.waitForTimeout(150); await page.screenshot({ path: resolve(SHOT_DIR, 'studio_review_4_agent_1440.png') }); }
  const itemId = await page.evaluate(() => window.DeskV1Store.state().studio.items[0].id);
  const stored = await page.evaluate(() => window.DeskV1Store.state().studio.items[0].agent);
  await openStudio(page);
  await page.click(`[data-studio-recent-row="${itemId}"]`);
  await page.waitForSelector('[data-storyboard]', { timeout: 4000 });
  await agentReady(page);
  check(stored === 'global:dave' && (await page.inputValue('[data-sb-agent-pick]')) === 'global:dave',
    `${tag} the pick is stored on the item and survives reopening it`, `${tag} pick not persisted: stored=${stored}`);
  reportUncaught(b.pageErrors, tag);
  await b.ctx.close();

  // 5. The timeline bar: one tile per real scene, widths by duration, drag reorders the list.
  b = await newBootedPage(browser, tone);
  page = b.page;
  await openNewVideo(page);
  check((await page.$$('[data-sb-timeline] .desk-v1-video-scene')).length === 1, `${tag} the timeline starts with 1 tile (only scene 1 is real)`, `${tag} timeline tile count wrong`);
  await editSceneTitle(page, 2, 'Second');
  await editSceneTitle(page, 3, 'Third');
  const tiles = await page.$$eval('[data-sb-timeline] .desk-v1-video-scene', (els) => els.map((e) => ({ id: e.dataset.sceneId, w: e.getBoundingClientRect().width })));
  const durs = await page.$$eval('.desk-v1-sb-scene:not([data-scene-placeholder]) .desk-v1-sb-dur', (els) => els.map((e) => e.textContent));
  check(tiles.length === 3 && (await page.$$('[data-sb-timeline] [data-trim-edge]')).length === 0,
    `${tag} the timeline has one tile per real scene (${tiles.length}), no trim handles`, `${tag} timeline wrong: ${JSON.stringify(tiles)}`);
  const secs = durs.map((d) => { const [m, sec] = d.split(':').map(Number); return m * 60 + sec; });
  const wSum = tiles.reduce((a, x) => a + x.w, 0);
  const sSum = secs.reduce((a, x) => a + x, 0);
  check(tiles.every((t, i) => Math.abs(t.w / wSum - secs[i] / sSum) < 0.03),
    `${tag} tile widths follow scene durations (${durs.join(' / ')})`, `${tag} widths not proportional: ${JSON.stringify(tiles)} vs ${durs}`);
  if (shot) { await clearToasts(page); await page.locator('[data-sb-timeline]').scrollIntoViewIfNeeded(); await page.waitForTimeout(150); await page.screenshot({ path: resolve(SHOT_DIR, 'studio_review_5_timeline_1440.png') }); }
  const listBefore = (await sceneIds(page)).slice(0, 3);
  await clearToasts(page);
  await dragTo(page, `[data-sb-timeline] [data-scene-id="${listBefore[2]}"]`, `[data-sb-timeline] [data-scene-id="${listBefore[0]}"]`);
  const listAfter = (await sceneIds(page)).slice(0, 3);
  const tlAfter = await page.$$eval('[data-sb-timeline] .desk-v1-video-scene', (els) => els.map((e) => e.dataset.sceneId));
  check(listAfter[0] === listBefore[2] && listAfter.join() === tlAfter.join(),
    `${tag} dragging a timeline tile reorders the list and the bar stays in sync`, `${tag} timeline drag wrong: list ${listBefore} -> ${listAfter}, bar ${tlAfter}`);
  await dragTo(page, `.desk-v1-sb-scene[data-scene-id="${listAfter[1]}"] [data-scene-handle]`, `.desk-v1-sb-scene[data-scene-id="${listAfter[0]}"]`);
  const tl2 = await page.$$eval('[data-sb-timeline] .desk-v1-video-scene', (els) => els.map((e) => e.dataset.sceneId));
  check(tl2[0] === listAfter[1], `${tag} reordering the list updates the timeline`, `${tag} list drag did not update the bar: ${tl2}`);
  reportUncaught(b.pageErrors, tag);
  await b.ctx.close();

  // 6. Layout (Ron 2026-10-01): the timeline tops the scene column, the bin is a
  // small icon in its lower-right, timeline tiles carry the scene's thumbnail.
  // Desktop widths and a phone width: nothing clipped, bin reachable, no overlap
  // with the Your agent box.
  const VIEWPORTS = [{ w: 1280, h: 900 }, { w: 1440, h: 950 }, { w: 1920, h: 1000 }, { w: 390, h: 844 }];
  for (const vp of VIEWPORTS) {
    const vtag = `${tag} ${vp.w}px`;
    b = await newBootedPage(browser, tone, { width: vp.w, height: vp.h });
    page = b.page;
    await openNewVideo(page);
    await page.waitForTimeout(150);
    const g = await page.evaluate(() => {
      const r = (sel) => { const e = document.querySelector(sel); if (!e) return null; const bx = e.getBoundingClientRect(); return { x: bx.x, y: bx.y, r: bx.right, b: bx.bottom, w: bx.width, h: bx.height }; };
      const bin = document.querySelector('[data-sb-trash]');
      const img = document.querySelector('[data-sb-timeline] .desk-v1-video-scene-thumb img');
      const ib = img ? img.getBoundingClientRect() : null;
      return {
        main: r('.desk-v1-sb-main'), tl: r('[data-sb-timeline-wrap]'), first: r('.desk-v1-sb-scene'), lastScene: r('.desk-v1-sb-scene:last-child'),
        bin: r('[data-sb-trash]'), agent: r('[data-sb-agent]'),
        oldZone: !!document.querySelector('.desk-v1-sb-trashzone'),
        binText: bin ? bin.textContent.trim() : '',
        img: img ? { src: img.getAttribute('src'), done: img.complete, nw: img.naturalWidth, vis: ib.width > 0 && ib.height > 0 } : null,
        docW: document.documentElement.scrollWidth, winW: window.innerWidth,
      };
    });
    check(g.tl && g.first && g.tl.b <= g.first.y + 1, `${vtag} the timeline is above the first scene (${Math.round(g.tl.b)} <= ${Math.round(g.first.y)})`, `${vtag} timeline not above the scenes: ${JSON.stringify([g.tl, g.first])}`);
    check(!g.oldZone && g.bin && g.bin.w <= 64 && g.bin.h <= 64 && g.binText === '🗑',
      `${vtag} the full-width trash bar is gone; the bin is a ${Math.round(g.bin.w)}x${Math.round(g.bin.h)} icon`, `${vtag} bin wrong: ${JSON.stringify([g.oldZone, g.bin, g.binText])}`);
    check(g.bin && g.main && g.bin.r >= g.main.r - 4 && g.bin.r <= g.main.r + 1 && g.bin.y >= g.first.y,
      `${vtag} the bin sits at the column's right edge (${Math.round(g.bin.r)} vs ${Math.round(g.main.r)}), below the timeline`, `${vtag} bin not lower-right of the column: ${JSON.stringify([g.bin, g.main])}`);
    // The bin is sticky: it rides the screen's bottom edge while the column is taller
    // than the screen, and settles in the column's lower-right corner (own gutter beside
    // the scenes on desktop, below the last scene on a phone) once scrolled to the end.
    check(g.bin.y >= g.main.y && g.bin.b <= g.main.b + 1, `${vtag} the bin stays inside the scene column`, `${vtag} bin outside the column: ${JSON.stringify([g.bin, g.main])}`);
    const end = await page.evaluate(() => {
      for (let e = document.querySelector('[data-sb-trash]').parentElement; e; e = e.parentElement) if (e.scrollHeight > e.clientHeight) e.scrollTop = e.scrollHeight;
      const bn = document.querySelector('[data-sb-trash]').getBoundingClientRect();
      const ls = document.querySelector('.desk-v1-sb-scene:last-child').getBoundingClientRect();
      const mn = document.querySelector('.desk-v1-sb-main').getBoundingClientRect();
      return { binX: bn.x, binY: bn.y, binB: bn.bottom, lastR: ls.right, lastB: ls.bottom, mainB: mn.bottom };
    });
    check((end.binX >= end.lastR - 1 || end.binY >= end.lastB - 1) && end.binB >= end.lastB - 1 && end.binB <= end.mainB + 1, `${vtag} scrolled to the end, the bin sits in the column's lower-right corner, clear of the last scene`, `${vtag} bin not below the last scene at the end: ${JSON.stringify(end)}`);
    if (vp.w > 960) check(g.bin.x >= g.first.r - 1, `${vtag} the bin has its own gutter: it never covers a scene row's buttons (${Math.round(g.bin.x)} >= ${Math.round(g.first.r)})`, `${vtag} bin overlaps the scene rows: ${JSON.stringify([g.bin, g.first])}`);
    const overlapAgent = g.agent && g.bin.r > g.agent.x && g.bin.x < g.agent.r && g.bin.b > g.agent.y && g.bin.y < g.agent.b;
    check(!overlapAgent, `${vtag} the bin does not overlap the Your agent box`, `${vtag} bin overlaps the agent box: ${JSON.stringify([g.bin, g.agent])}`);
    check(g.docW <= g.winW + 1 && g.main.r <= g.winW + 1 && g.main.x >= -1, `${vtag} nothing clipped sideways (page ${g.docW} <= ${g.winW}, column ${Math.round(g.main.x)}..${Math.round(g.main.r)})`, `${vtag} horizontal clipping: ${JSON.stringify([g.docW, g.winW, g.main])}`);
    check(g.img && g.img.done && g.img.nw > 0 && g.img.vis, `${vtag} the timeline tile shows the scene thumbnail (${g.img && g.img.src})`, `${vtag} timeline tile has no loaded img: ${JSON.stringify(g.img)}`);
    await page.locator('[data-sb-trash]').scrollIntoViewIfNeeded();
    const binBox = await page.locator('[data-sb-trash]').boundingBox();
    const topEl = await page.evaluate((p) => { const e = document.elementFromPoint(p.x, p.y); return e && (e.closest('[data-sb-trash]') ? 'bin' : (e.className || e.tagName)); }, { x: binBox.x + binBox.width / 2, y: binBox.y + binBox.height / 2 });
    check(topEl === 'bin' && binBox.y >= 0 && binBox.y + binBox.height <= vp.h, `${vtag} the bin is reachable: on screen and the top element at its centre`, `${vtag} bin not reachable: ${topEl} ${JSON.stringify(binBox)}`);
    if (vp.w === 1440 || vp.w === 390) {
      // the bin highlights while a scene is dragged over it
      await page.locator('.desk-v1-sb-scene:nth-child(2) [data-scene-handle]').scrollIntoViewIfNeeded();
      const hb = await page.locator('.desk-v1-sb-scene:nth-child(2) [data-scene-handle]').boundingBox();
      const bb = await page.locator('[data-sb-trash]').boundingBox();
      await page.mouse.move(hb.x + hb.width / 2, hb.y + hb.height / 2);
      await page.mouse.down();
      await page.mouse.move(hb.x + hb.width / 2 + 10, hb.y + hb.height / 2 - 10, { steps: 4 });
      await page.mouse.move(bb.x + bb.width / 2, bb.y + bb.height / 2, { steps: 10 });
      await page.waitForTimeout(60);
      const hot = await page.evaluate(() => document.querySelector('[data-sb-trash]').classList.contains('pd-drop-hover'));
      if (vp.w === 1440 && shot) await page.screenshot({ path: resolve(SHOT_DIR, 'studio_timeline_top_bin_hover_1440.png') });
      await page.mouse.up();
      await page.waitForTimeout(100);
      check(hot, `${vtag} the bin highlights while a scene is dragged over it`, `${vtag} bin did not highlight on drag-over`);
      await clearToasts(page);
    }
    if (shot && (vp.w === 1440 || vp.w === 390)) {
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.waitForTimeout(150);
      await page.screenshot({ path: resolve(SHOT_DIR, `studio_timeline_top_${vp.w}.png`) });
    }
    reportUncaught(b.pageErrors, vtag);
    await b.ctx.close();
  }
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
  check(/Ask Claydo about the storyboard/.test(agent || ''), `${tag} agent panel placeholder "${agent}"`, `${tag} agent placeholder wrong: ${JSON.stringify(agent)}`);

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
    await runStandalone(browser, tone);
    await runStudioReview(browser, tone);
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

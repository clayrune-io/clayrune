#!/usr/bin/env node
/**
 * Desk v1 (MC-977, R0 plan T2a) — Campaign page + Content list smoke
 * (frame 12a, + 12e wording rules).
 *
 * Closes: A2 (content is grouped by family, versions independent per
 * channel), A3 (a family's versions carry independent state — one
 * published/one needs-review/one planned on the same card), A4 (moving a
 * version to another channel is a menu action, archives the old one), A5
 * (Skip/Archive act card-level through the shared commandBus, so Undo
 * applies), A12 (copy comes from the shared kit vocabulary/section-9
 * constants, never a local status string).
 *
 * Real headless boot (real index.html + real static/js|css, no network), same
 * hermetic shape as desk-v1-calendar.mjs / desk-v1-home.mjs.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-campaign.mjs
 * Exit 0 = every case holds (render checks in all three tones, filter/view
 * toggle, card menu actions, drag-drop from the Add tray, phone layout); 1 =
 * a case regressed / harness error. Also writes
 * docs/desk_v1/screens/t2a_campaign_{desktop_1440,phone_390}.png (default
 * tone only).
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

const PID = 'smoke_deskv1campaign';
const PROJECTS = [{
  id: PID, name: 'Desk v1 campaign smoke', status: 'active', domain: 'general', emoji: '🧪',
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

  await seedDeskV1Fixtures(page);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors };
}

async function navToCampaign(page) {
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.waitForSelector('.desk-v1-campaign', { timeout: 8000 });
}

function reportUncaught(pageErrors, tag) {
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail(`${tag} uncaught page error: ${e}`));
}

// ── render checks, one per tone: summary bar, tab strip badges, grouped
// content list, cards (preview/meta/versions/actions), Posy box, Add tray. ──
async function runToneRenderChecks(browser, tone) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, tone);
  await navToCampaign(page);

  const state = await page.textContent('.desk-v1-camp-state-pill .desk-v1-state-word').catch(() => '');
  state.trim() === 'Active'
    ? ok(`[${tone.name}] summary state pill reads "Active" (shared kit vocabulary)`)
    : fail(`[${tone.name}] state pill wrong: ${JSON.stringify(state)}`);

  const goalText = (await page.textContent('.desk-v1-camp-summary-goaltext').catch(() => '') || '');
  /11\/30 tester signups/.test(goalText)
    ? ok(`[${tone.name}] goal group reads the fixture's real progress: "${goalText.trim()}"`)
    : fail(`[${tone.name}] goal text wrong: ${JSON.stringify(goalText)}`);

  const chanLabels = await page.$$eval('.desk-v1-camp-summary-group .desk-v1-channel-badge', (els) => els.map((e) => e.textContent.trim()));
  chanLabels.some((l) => l.includes('@ron')) && chanLabels.some((l) => l.includes('Clayrune page')) && chanLabels.some((l) => l.includes('Clayrune blog'))
    ? ok(`[${tone.name}] all 3 campaign channels render via the shared channel badge: ${JSON.stringify(chanLabels)}`)
    : fail(`[${tone.name}] channel badges missing one: ${JSON.stringify(chanLabels)}`);

  // R2-3b retired the rule chips and the rules popover's Edit hook (the
  // accepted-bounds table lives on ⑥ Launch now): the old A12 "chips are real
  // rule values" + "Edit hook renders" checks became their absence.
  const ruleChips = await page.$$eval('.desk-v1-camp-rule-chip', (els) => els.map((e) => e.textContent.trim()));
  const editBtn = await page.$('[data-rules-edit]');
  const rulesGroup = await page.$('[data-summary-group="rules"]');
  !ruleChips.length && !editBtn && !rulesGroup
    ? ok(`[${tone.name}] R2-3b: no rule chips, no Rules group and no Edit hook on the summary bar`)
    : fail(`[${tone.name}] R2-3b: rules UI still on the summary bar: chips=${JSON.stringify(ruleChips)}, edit=${!!editBtn}, group=${!!rulesGroup}`);

  // R2-3: the old Content/Conversations/Results tab strip is now the ①-⑥ map
  // stepper (IA revision 2 §3/§4.1). navToCampaign() opens camp-1 (Active)
  // with no explicit panel, which defaults to ③ What (desk-v1-shell.js's
  // `_renderCampaignSkeleton`: a non-draft campaign's `map.stop` is NOT a
  // resume cursor — "here" tracks the actually-rendered `params.panel`).
  // camp-1's fixture `map.done` still marks Goal done from its own separate
  // progress history. Conversations is no longer one of the six stops (§6:
  // moves into Engagement at R2-12), so its old badge assertion is retired
  // with the tab it lived on, not rewritten onto a stop that doesn't exist.
  const stopWords = await page.$$eval('.desk-v1-map-stop .desk-v1-map-stop-word', (els) => els.map((e) => e.textContent.trim()));
  JSON.stringify(stopWords) === JSON.stringify(['Brief', 'Goal', 'What', 'Where', 'When', 'Launch'])
    ? ok(`[${tone.name}] map stepper shows all 6 stops in order: ${JSON.stringify(stopWords)}`)
    : fail(`[${tone.name}] map stepper stops wrong: ${JSON.stringify(stopWords)}`);
  const whatState = await page.$eval('.desk-v1-map-stop[data-stop="what"]', (el) => el.dataset.state).catch(() => null);
  const goalState = await page.$eval('.desk-v1-map-stop[data-stop="goal"]', (el) => el.dataset.state).catch(() => null);
  whatState === 'here' && goalState === 'done'
    ? ok(`[${tone.name}] map stepper reflects current panel + map.done (what=here, goal=done)`)
    : fail(`[${tone.name}] map stepper state wrong: what=${whatState} goal=${goalState}`);

  // R2-7 (frames 5a/7b): ③ What is a flat list of pieces — kind label, title,
  // `on N channels`, a per-version status summary — with filters on top and the
  // content-type tray below. The old grouped list (NEEDS YOU · n …) is retired.
  const rows = await page.$$eval('[data-what-row]', (els) => els.map((e) => ({
    id: e.dataset.familyId,
    kind: e.querySelector('.desk-v1-what-kind').textContent.trim(),
    title: e.querySelector('.desk-v1-what-title').textContent.trim(),
    on: e.querySelector('[data-what-on]').textContent.trim(),
    states: [...e.querySelectorAll('.desk-v1-what-status-item')].map((x) => x.dataset.state || ''),
  })));
  rows.length === 6
    ? ok(`[${tone.name}] What lists camp-1's 6 pieces (frame 7b)`)
    : fail(`[${tone.name}] What row count wrong: ${rows.length}`);
  const byId = Object.fromEntries(rows.map((r) => [r.id, r]));
  const vid = byId['fam-install-video'];
  vid && vid.kind === 'VIDEO' && vid.on === 'on 3 channels' && vid.states.length === 3 && new Set(vid.states).size >= 2
    ? ok(`[${tone.name}] A3: the video row reads "VIDEO · on 3 channels" with 3 independent version statuses: ${JSON.stringify(vid.states)}`)
    : fail(`[${tone.name}] A3: video row wrong: ${JSON.stringify(vid)}`);
  const art = byId['fam-restore-points'];
  art && art.kind === 'ARTICLE' && art.on === 'on 2 channels'
    ? ok(`[${tone.name}] the article row reads "ARTICLE · on 2 channels"`)
    : fail(`[${tone.name}] article row wrong: ${JSON.stringify(art)}`);
  const agentRow = await page.textContent('[data-family-id="fam-agent-live-run"] [data-what-status]').catch(() => '');
  /rendering/i.test(agentRow)
    ? ok(`[${tone.name}] a video still rendering says so in its status column: "${agentRow.trim()}"`)
    : fail(`[${tone.name}] rendering status missing: ${JSON.stringify(agentRow)}`);
  const multi = await page.$$eval('[data-family-id="fam-30-testers"] .desk-v1-what-thumb', (els) => els.length);
  const addMedia = await page.$('[data-family-id="fam-30-testers"] [data-add-media]');
  multi === 2 && addMedia
    ? ok(`[${tone.name}] a multi-asset piece shows its 2 thumbnails + ＋ Add media (frame 5a)`)
    : fail(`[${tone.name}] multi-asset row wrong: thumbs=${multi}, add=${!!addMedia}`);
  const filters = await page.$$eval('[data-what-filter]', (els) => els.map((e) => e.textContent.trim()));
  JSON.stringify(filters) === JSON.stringify(['All', 'Needs review', 'Scheduled', 'Blocked'])
    ? ok(`[${tone.name}] filters read ${JSON.stringify(filters)}`)
    : fail(`[${tone.name}] filters wrong: ${JSON.stringify(filters)}`);

  // Posy box scoped to the campaign by default (§3.4 INS-01).
  const posyScope = (await page.textContent('.desk-v1-camp-posy .desk-v1-posy-scope').catch(() => '') || '');
  /Windows beta testers/.test(posyScope)
    ? ok(`[${tone.name}] Posy box scope defaults to the campaign: "${posyScope.trim()}"`)
    : fail(`[${tone.name}] Posy scope wrong: ${JSON.stringify(posyScope)}`);
  const suggestion = (await page.textContent('.desk-v1-camp-posy .agent-question-chip, .desk-v1-camp-posy [class*="chip"]').catch(() => '') || '');

  // The content-type tray (frame 5a) replaces the Add tray.
  const types = await page.$$eval('[data-what-type] .desk-v1-what-type-name', (els) => els.map((e) => e.textContent.trim()));
  JSON.stringify(types) === JSON.stringify(['Post', 'Article', 'Video', 'Image', 'YouTube'])
    ? ok(`[${tone.name}] content-type tray offers ${JSON.stringify(types)}`)
    : fail(`[${tone.name}] content-type tray wrong: ${JSON.stringify(types)}`);
  const oldTray = await page.$('.desk-v1-camp-addtray-details, [data-view-btn], [data-group-show], [data-filter-trigger]');
  !oldTray
    ? ok(`[${tone.name}] the old Add tray, List/Calendar toggle, group headers and channel filter are gone`)
    : fail(`[${tone.name}] retired Content-tab chrome still renders`);

  reportUncaught(pageErrors, `[${tone.name}]`);
  await ctx.close();
}

// ── What filters (frame 5a): All · Needs review · Scheduled · Blocked. ──────
async function runFilterToggle(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCampaign(page);

  const expect = await page.evaluate(() => {
    const sched = new Set(['scheduled', 'approved', 'sending', 'submitted']);
    const fams = window.DeskV1Fixtures.families.filter((f) => f.campaignId === 'camp-1');
    return {
      scheduled: fams.filter((f) => f.versions.some((v) => sched.has(v.state))).map((f) => f.id).sort(),
      needs: fams.filter((f) => f.versions.some((v) => v.state === 'needs_review')).map((f) => f.id).sort(),
      blocked: fams.filter((f) => f.versions.some((v) => v.state === 'blocked')).map((f) => f.id).sort(),
    };
  });
  const rowsNow = () => page.$$eval('[data-what-row]', (els) => els.map((e) => e.dataset.familyId).sort());
  for (const [label, key] of [['Scheduled', 'scheduled'], ['Needs review', 'needs'], ['Blocked', 'blocked']]) {
    await page.click(`[data-what-filter]:has-text("${label}")`);
    await page.waitForTimeout(50);
    const got = await rowsNow();
    got.length > 0 && got.length < 6 && JSON.stringify(got) === JSON.stringify(expect[key])
      ? ok(`filter "${label}" narrows 6 pieces to ${got.length}: ${JSON.stringify(got)}`)
      : fail(`filter "${label}" wrong: got ${JSON.stringify(got)}, expected ${JSON.stringify(expect[key])}`);
  }
  const pressed = await page.$eval('[data-what-filter][aria-pressed="true"]', (el) => el.textContent.trim());
  pressed === 'Blocked' ? ok('the chosen filter reads as pressed') : fail(`pressed filter wrong: ${pressed}`);
  await page.click('[data-what-filter]:has-text("All")');
  await page.waitForTimeout(50);
  (await rowsNow()).length === 6 ? ok('All restores the full list') : fail('All did not restore the list');

  reportUncaught(pageErrors, '[filter]');
  await ctx.close();
}

// ── The List/Calendar toggle is gone: the calendar is ④ When (R2-3/R2-7). ──
async function runViewToggle(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCampaign(page);

  (await page.$('[data-view-btn]')) === null
    ? ok('What has no List/Calendar toggle (the calendar is the ④ When stop)')
    : fail('a List/Calendar toggle still renders on What');
  await page.click('[data-stop="when"]');
  await page.waitForSelector('.desk-v1-calendar', { timeout: 4000 });
  ok('④ When mounts the calendar component');
  await page.click('[data-stop="what"]');
  await page.waitForSelector('[data-what]', { timeout: 4000 });
  ok('back on ③ What the list re-renders');

  reportUncaught(pageErrors, '[view-toggle]');
  await ctx.close();
}

// ── card ⋯ menu (§3.2: Add a channel version / Move to another channel /
// Duplicate / Skip / Archive) — A4/A5. ──────────────────────────────────────
async function runCardMenu(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCampaign(page);

  // Duplicate (fam-30-testers, single-version scheduled post) — commandBus +
  // toast + Undo, same contract as every other mutation on this page. The
  await page.click('[data-family-id="fam-30-testers"] [data-more-btn]');
  await page.waitForSelector('.desk-v1-camp-cardmenu', { timeout: 2000 });
  await page.click('.desk-v1-camp-cardmenu [data-menu-dup]');
  await page.waitForSelector('.toast', { timeout: 2000 }).catch(() => {});
  const dupToast = (await page.textContent('.toast').catch(() => '') || '');
  /Duplicated/.test(dupToast)
    ? ok(`Duplicate shows a commandBus toast: "${dupToast.trim()}"`)
    : fail(`Duplicate toast missing/wrong: ${JSON.stringify(dupToast)}`);
  const dupExists = await page.evaluate(() => window.DeskV1Fixtures.families.some((f) => f.id.startsWith('fam-30-testers-copy-')));
  dupExists ? ok('the duplicated family exists in fixtures (a new What row)') : fail('duplicated family not found in fixtures');
  await page.click('.toast .toast-btn.primary');
  await page.waitForSelector('.toast', { state: 'detached', timeout: 2000 }).catch(() => {});
  const dupGone = await page.evaluate(() => !window.DeskV1Fixtures.families.some((f) => f.id.startsWith('fam-30-testers-copy-')));
  dupGone ? ok('Undo removes the duplicate') : fail('Undo did not remove the duplicated family');

  // A5: Archive acts card-level, on every non-terminal version, through the
  // same commandBus (Undo restores each version's PRIOR state, not a blanket
  // one — checked against the video family's 3 independent states).
  await page.click('[data-family-id="fam-install-video"] [data-more-btn]');
  await page.waitForSelector('.desk-v1-camp-cardmenu', { timeout: 2000 });
  const statesBefore = await page.evaluate(() => window.DeskV1Fixtures.families.find((f) => f.id === 'fam-install-video').versions.map((v) => v.state));
  await page.click('.desk-v1-camp-cardmenu [data-menu-archive]');
  await page.waitForSelector('.toast', { timeout: 2000 }).catch(() => {});
  const archiveToast = (await page.textContent('.toast').catch(() => '') || '');
  /Archived/.test(archiveToast)
    ? ok(`Archive shows a commandBus toast: "${archiveToast.trim()}"`)
    : fail(`Archive toast missing/wrong: ${JSON.stringify(archiveToast)}`);
  const statesAfter = await page.evaluate(() => {
    const fam = window.DeskV1Fixtures.families.find((f) => f.id === 'fam-install-video');
    return fam.versions.map((v) => v.state);
  });
  statesAfter.filter((s) => s === 'archived').length === 2 // the already-published one is left alone (terminal)
    ? ok(`A5: Archive only touched the non-terminal versions, verified_published left alone: ${JSON.stringify(statesAfter)}`)
    : fail(`Archive touched the wrong versions: ${JSON.stringify(statesAfter)}`);
  await page.click('.toast .toast-btn.primary');
  await page.waitForTimeout(50);
  const statesRestored = await page.evaluate(() => {
    const fam = window.DeskV1Fixtures.families.find((f) => f.id === 'fam-install-video');
    return fam.versions.map((v) => v.state);
  });
  JSON.stringify(statesRestored) === JSON.stringify(statesBefore)
    ? ok(`Undo restores each version's own prior state: ${JSON.stringify(statesRestored)}`)
    : fail(`Undo did not restore prior per-version states: ${JSON.stringify(statesRestored)}`);

  reportUncaught(pageErrors, '[card-menu]');
  await ctx.close();
}

// ── R2-7: the content-type tray (frames 5a, 15, 15a, 16a, 16b). A type is
// dragged (or focused + Enter) into the list; each drop is its OWN piece and
// opens a source-first create card — nothing preselected, no file picker in
// the DOM until Upload is chosen; Undo removes the piece. ─────────────────
async function runWhatTray(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCampaign(page);
  const famCount = () => page.evaluate(() => window.DeskV1Fixtures.families.filter((f) => f.campaignId === 'camp-1').length);
  const n0 = await famCount();

  // Real pointer drag: Video tile -> the list panel.
  const tile = await page.$('[data-what-type="video"]');
  const list = await page.$('[data-what-list]');
  await tile.scrollIntoViewIfNeeded();
  const t = await tile.boundingBox();
  const l = await list.boundingBox();
  const body = await page.locator('#desk-v1-camp-tabbody').boundingBox();
  await page.mouse.move(t.x + t.width / 2, t.y + t.height / 2);
  await page.mouse.down();
  await page.mouse.move(t.x + t.width / 2 + 14, t.y + t.height / 2 - 14, { steps: 4 });
  await page.mouse.move(l.x + l.width / 2, Math.max(l.y, body ? body.y : l.y) + 30, { steps: 8 });
  await page.waitForTimeout(50);
  const hovering = await page.$eval('[data-what-list]', (el) => el.classList.contains('pd-drop-hover'));
  hovering ? ok('dragging a content type over the list shows the drop ring') : fail('no drop ring while a type is dragged over the list');
  await page.mouse.up();
  await page.waitForSelector('[data-what-create][data-kind="video"]', { timeout: 2000 });
  ok('dropping Video opens a create card at the top of the list');
  (await famCount()) === n0 + 1 ? ok('the drop created ONE new piece') : fail('drop did not create exactly one piece');

  // Source-first: four tiles, none preselected, no file input yet.
  const sources = await page.$$eval('[data-what-create] [data-what-source]', (els) => els.map((e) => e.querySelector('.desk-v1-what-source-name').textContent.trim()));
  JSON.stringify(sources) === JSON.stringify(['Record from the product', 'Upload', 'Online source', 'Create new'])
    ? ok(`Video offers four sources, none preselected: ${JSON.stringify(sources)}`)
    : fail(`Video sources wrong: ${JSON.stringify(sources)}`);
  (await page.$('[data-what-create] input[type=file]')) === null
    ? ok('no file picker exists in the DOM until Upload is chosen')
    : fail('a file input is in the DOM before a source was chosen');

  await page.click('[data-what-create] [data-what-source="upload"]');
  await page.waitForSelector('[data-what-dropzone]', { timeout: 2000 });
  const chip = (await page.textContent('[data-what-chip]')).trim();
  const folders = await page.$$eval('[data-what-folder]', (els) => els.length);
  chip === 'Upload' && folders === 3 && (await page.$('[data-what-browse]')) && (await page.$('input[type=file]'))
    ? ok('Upload shows the chip, the drop zone, Browse this computer and the 3 Material library folders')
    : fail(`Upload body wrong: chip=${chip}, folders=${folders}`);
  await page.click('[data-what-change-source]');
  await page.waitForSelector('[data-what-source]', { timeout: 2000 });
  (await page.$('[data-what-create] input[type=file]')) === null
    ? ok('Change source returns to the four tiles and drops the file input')
    : fail('file input survived Change source');

  // Picking a Material folder attaches an asset and closes the card into a row.
  await page.click('[data-what-source="upload"]');
  await page.click('[data-what-folder]');
  await page.waitForSelector('[data-what-create]', { state: 'detached', timeout: 2000 });
  const assets = await page.evaluate(() => window.DeskV1Fixtures.families.filter((f) => f.campaignId === 'camp-1' && f.id.startsWith('fam-new-')).map((f) => f.assets.length));
  assets.length === 1 && assets[0] === 1 ? ok('a Material folder attaches one asset and the card finishes into a row') : fail(`folder attach wrong: ${JSON.stringify(assets)}`);

  // Undo (attach), Undo (create) — the new piece is gone again.
  for (let i = 0; i < 2; i++) {
    await page.locator('.toast .toast-btn.primary').last().click();
    await page.waitForTimeout(80);
  }
  (await famCount()) === n0 ? ok('Undo twice removes the piece again') : fail(`Undo left ${await famCount()} pieces, expected ${n0}`);

  // Several of a kind: two Images are two pieces. Keyboard path (focus + Enter).
  await page.focus('[data-what-type="image"]');
  await page.keyboard.press('Enter');
  await page.waitForSelector('[data-what-create][data-kind="image"]', { timeout: 2000 });
  await page.focus('[data-what-type="image"]');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.querySelectorAll('[data-what-create][data-kind="image"]').length === 2, null, { timeout: 2000 });
  ok('keyboard: Enter on the Image tile adds a piece; a second Image is a second piece');
  const imgSources = await page.$$eval('[data-what-create][data-kind="image"]:first-child [data-what-source] .desk-v1-what-source-name', (els) => els.map((e) => e.textContent.trim()));
  JSON.stringify(imgSources) === JSON.stringify(['Capture from the product', 'Upload', 'Online source', 'Generate'])
    ? ok(`Image offers ${JSON.stringify(imgSources)}`)
    : fail(`Image sources wrong: ${JSON.stringify(imgSources)}`);
  const genHint = (await page.textContent('[data-what-source="generate"] [data-what-abstract-only]')).trim();
  /never the product UI/i.test(genHint)
    ? ok(`Generate states the standing position: "${genHint}"`)
    : fail(`Generate hint wrong: ${JSON.stringify(genHint)}`);
  await page.click('[data-what-create]:first-child [data-what-remove]');
  await page.click('[data-what-create] [data-what-remove]');
  await page.waitForFunction(() => !document.querySelector('[data-what-create]'), null, { timeout: 2000 });
  (await famCount()) === n0 ? ok('✕ on both cards removes both pieces') : fail(`✕ left ${await famCount()} pieces`);

  // Article: Browse existing / Write new.
  await page.focus('[data-what-type="article"]');
  await page.keyboard.press('Enter');
  await page.waitForSelector('[data-what-create][data-kind="article"]', { timeout: 2000 });
  const artSources = await page.$$eval('[data-what-create] [data-what-source] .desk-v1-what-source-name', (els) => els.map((e) => e.textContent.trim()));
  JSON.stringify(artSources) === JSON.stringify(['Browse existing', 'Write new'])
    ? ok(`Article offers ${JSON.stringify(artSources)}`)
    : fail(`Article sources wrong: ${JSON.stringify(artSources)}`);
  await page.click('[data-what-source="browse"]');
  await page.click('[data-what-existing]');
  await page.waitForSelector('[data-what-create]', { state: 'detached', timeout: 2000 });
  ok('Browse existing -> picking an article finishes the card');

  // YouTube: a Video card carrying the Preview chip (§11.6 Q1 — nothing connects).
  await page.focus('[data-what-type="youtube"]');
  await page.keyboard.press('Enter');
  await page.waitForSelector('[data-what-create][data-kind="video"] [data-preview]', { timeout: 2000 });
  ok('YouTube opens a Video card labelled Preview (renders, connects nothing)');

  reportUncaught(pageErrors, '[what-tray]');
  await ctx.close();
}

// ── item 3 (MC-977 R0 UX pass) — campaign-page header "More" menu: camp-1
// (Active, has publication history) offers Archive via the shared in-page
// confirm sheet, Cancel leaves it untouched, and confirming archives with an
// Undo. Delete's own full path (Proposed campaign) is exercised on the Home
// card in desk-v1-home.mjs's runHomeCardMoreMenu — same shared menu/sheet
// code, no need to duplicate every assertion here too. ─────────────────────
async function runCampaignPageMoreMenu(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCampaign(page);

  const moreBtn = await page.$('.desk-v1-camp-summary-top [data-camp-more-btn]');
  moreBtn ? ok('item 3: campaign page header has a discoverable More trigger') : fail('item 3: no More trigger on the campaign page header');
  await moreBtn.click();
  await page.waitForSelector('.desk-v1-camp-cardmenu', { timeout: 2000 });
  const menuText = (await page.textContent('.desk-v1-camp-cardmenu').catch(() => '') || '');
  /Archive campaign/.test(menuText)
    ? ok('item 3: Active campaign\'s page-level menu offers Archive')
    : fail(`item 3: page-level menu wrong: ${JSON.stringify(menuText)}`);

  await page.click('.desk-v1-camp-cardmenu [data-menu-archive]');
  await page.waitForSelector('.desk-v1-rules-confirm-overlay', { timeout: 2000 });
  await page.click('[data-confirm-decline]');
  await page.waitForTimeout(50);
  const stillActive = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-1').state);
  stillActive === 'active' ? ok('item 3: Cancel on the confirm sheet leaves the campaign untouched') : fail(`item 3: Cancel still mutated state: ${stillActive}`);

  await moreBtn.click();
  await page.waitForSelector('.desk-v1-camp-cardmenu', { timeout: 2000 });
  await page.click('.desk-v1-camp-cardmenu [data-menu-archive]');
  await page.waitForSelector('.desk-v1-rules-confirm-overlay', { timeout: 2000 });
  await page.click('[data-confirm-accept]');
  await page.waitForSelector('.toast', { timeout: 2000 }).catch(() => {});
  const archivedState = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-1').state);
  archivedState === 'archived' ? ok('item 3: confirming Archive on the campaign page sets state to archived') : fail(`item 3: state after confirm: ${archivedState}`);
  await page.click('.toast .toast-btn.primary');
  await page.waitForTimeout(50);
  const restoredState = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-1').state);
  restoredState === 'active' ? ok('item 3: Undo restores the campaign to active') : fail(`item 3: state after Undo: ${restoredState}`);

  reportUncaught(pageErrors, '[campaign-more-menu]');
  await ctx.close();
}

// ── Pause / Resume (§6.2, Dave review pass 2 on T2) — desk-v1-campaign.js's
// commandBus mutation + confirm-sheet gate. T2 acceptance: Pause -> Undo ->
// Resume-sheet -> Active, and a Resume blocked by an expired plan lands on
// the interim (rules popover) instead of restarting silently. ch-li-page is
// permanently 'held' in the T0a base fixtures (camp-1's ONE Active fixture
// carries it as a plan destination), so the success-path resume below flips
// it to 'ok' just long enough to isolate that path from the expired-end case
// tested right after — restored implicitly by context teardown, not undone
// in-test (nothing after this function reads it). ──────────────────────────
async function runPauseResume(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCampaign(page);

  // Toasts have no `key` on this bus (see commandBus.run's `toast()` call in
  // desk-v1-kit.js), so they STACK rather than replace — and `.toast-out`'s
  // removal depends on an `animationend` that a headless run of this suite
  // does not reliably deliver in time, so an earlier toast can linger in the
  // DOM. `.toast` alone would then resolve to the OLDEST (first-child, per
  // `container.appendChild`) instead of the one the just-fired action
  // produced — always read/act on `.last()` so a lingering toast never
  // shadows the current one.
  const lastToast = () => page.locator('.toast').last();

  // (a) Pause -> Paused, Undo toast, Undo -> Active.
  await page.click('[data-pause-btn]');
  let state = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-1').state);
  state === 'paused' ? ok('T2/6.2: Pause sets camp-1 to paused') : fail(`T2/6.2: state after Pause: ${state}`);
  await page.waitForSelector('.toast', { timeout: 2000 }).catch(() => {});
  const pauseToast = (await lastToast().textContent().catch(() => '') || '');
  /Paused/.test(pauseToast) && /Undo/.test(pauseToast)
    ? ok(`T2/6.2: Pause shows an Undo toast: "${pauseToast.trim()}"`)
    : fail(`T2/6.2: pause toast missing/wrong: ${JSON.stringify(pauseToast)}`);
  await lastToast().locator('.toast-btn.primary').click();
  await page.waitForTimeout(50);
  state = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-1').state);
  state === 'active' ? ok('T2/6.2: Undo restores camp-1 to active') : fail(`T2/6.2: state after Undo: ${state}`);

  // (b) Resume opens the sheet naming the upcoming work ("N posts resume:
  // ...", missed slots skipped), confirm -> Active. Deliberately does NOT
  // dismiss the resulting toast via its Undo button — that button's onclick
  // IS the campaign's own undo (reverts the resume), not a plain toast
  // dismiss; clicking it here would silently fail the very assertion it
  // follows.
  await page.evaluate(() => { window.DeskV1Fixtures.channels.find((c) => c.id === 'ch-li-page').health = 'ok'; });
  await page.click('[data-pause-btn]');
  await page.waitForSelector('[data-resume-btn]', { timeout: 2000 });
  await page.click('[data-resume-btn]');
  await page.waitForSelector('.desk-v1-rules-confirm-overlay', { timeout: 2000 });
  const resumeBody = (await page.textContent('.desk-v1-rules-confirmtext').catch(() => '') || '');
  /^1 post resumes: .+\. Missed slots are skipped, never posted late\.$/.test(resumeBody.trim())
    ? ok(`T2/6.2: Resume sheet names the upcoming work: "${resumeBody.trim()}"`)
    : fail(`T2/6.2: resume sheet body wrong: ${JSON.stringify(resumeBody)}`);
  await page.click('[data-confirm-accept]');
  await page.waitForSelector('.toast', { timeout: 2000 }).catch(() => {});
  state = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-1').state);
  state === 'active' ? ok('T2/6.2: confirming Resume sets camp-1 back to active') : fail(`T2/6.2: state after Resume confirm: ${state}`);

  // (d) An expired end date isn't covered by validatePlan's own bound table
  // (§4) — Resume catches it separately (_planExpiryReason) and must not
  // silently restart. R2-3b: the rules popover that used to stand in for the
  // "Review + start" step is retired, so Resume lands on ⑥ Launch (where the
  // missing terms are listed) plus an explaining toast.
  await page.click('[data-pause-btn]');
  await page.waitForSelector('[data-resume-btn]', { timeout: 2000 });
  await page.evaluate(() => { window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-1').plan.end.date = '2020-01-01'; });
  await page.click('[data-resume-btn]');
  await page.waitForSelector('.desk-v1-rules-confirm-overlay', { timeout: 2000 });
  await page.click('[data-confirm-accept]');
  await page.waitForSelector('.desk-v1-launch', { timeout: 2000 }).catch(() => {});
  const onLaunch = await page.$eval('[data-stop="launch"]', (el) => el.dataset.state === 'here').catch(() => false);
  const popOpen = await page.$('.desk-v1-rules-pop-overlay');
  onLaunch && !popOpen ? ok('T2/6.2: expired-end Resume routes to ⑥ Launch (no rules popover any more)') : fail(`T2/6.2: expired-end Resume did not land on ⑥ Launch: onLaunch=${onLaunch}, popover=${!!popOpen}`);
  const expiredToast = (await lastToast().textContent().catch(() => '') || '');
  /its end date has passed/.test(expiredToast)
    ? ok(`T2/6.2: expired-end Resume explains why via toast: "${expiredToast.trim()}"`)
    : fail(`T2/6.2: expired-end toast missing/wrong: ${JSON.stringify(expiredToast)}`);
  state = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-1').state);
  state === 'paused' ? ok('T2/6.2: expired-end Resume leaves camp-1 paused, no silent restart') : fail(`T2/6.2: state after expired-end Resume: ${state}`);

  reportUncaught(pageErrors, '[pause-resume]');
  await ctx.close();
}

// ── §2's in-place panel switch must never push a route (T2 acceptance: "tab
// click keeps header + Posy DOM node, no route push"). runPosyDraftPersistence
// above proves DOM-node identity for the header/Posy box; this proves the
// STACK itself never grows — deskV1Back() after several tab clicks must land
// on Home in ONE pop, not on campaign's own previous panel (which is what a
// hidden per-tab push would produce). ──────────────────────────────────────
// ── IA6 (docs/THE_DESK_V1_IA_REVISION.md §5 row IA6, §6.1 empty states) —
// camp-4 is a genuinely fresh Active campaign (goal.current 0, no version
// has ever published): the goal bar reads as plain text with no `0/30`-style
// bar, and Results/Conversations show the UX_PASS §6.1 copy instead of the
// generic "nothing yet" stubs. ─────────────────────────────────────────────
async function runFreshEmptyStates(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-4' }));
  await page.waitForSelector('.desk-v1-campaign', { timeout: 8000 });

  const goalBar = await page.$('.desk-v1-camp-summary-goalbar');
  goalBar === null ? ok('IA6: fresh Active (camp-4) shows no 0/ progress bar') : fail('IA6: fresh Active still rendered a 0/ progress bar');
  const goalText = (await page.textContent('.desk-v1-camp-summary-goal').catch(() => '') || '');
  /40 Discord joins.*starts counting at the first post/.test(goalText)
    ? ok(`IA6: fresh Active goal reads as plain text: "${goalText.trim()}"`)
    : fail(`IA6: fresh Active goal text wrong: ${JSON.stringify(goalText)}`);

  // R2-3: 'results'/'conversations' aren't tab-strip buttons any more — ①
  // Goal is a map stop (`[data-stop="goal"]`); Conversations has no stop at
  // all (§6, moves to Engagement at R2-12) and stays reachable only through
  // the same deep-link call a Home/piece "Review" link would make.
  await page.click('[data-stop="goal"]');
  await page.waitForSelector('#desk-v1-camp-tabbody .desk-v1-goal-editor', { timeout: 2000 });
  const hintText = (await page.textContent('.desk-v1-goal-editor-hint').catch(() => '') || '').trim();
  hintText === '⚠ Not measured — pick a source to start tracking progress.'
    ? ok(`IA6: fresh Active Goal (R2-4) shows the untracked hint: "${hintText}"`)
    : fail(`IA6: fresh Active Goal editor hint wrong: ${JSON.stringify(hintText)}`);

  // R2-3b: the campaign's Conversations tab is gone; the old deep link lands
  // on Engagement filtered to this campaign, which mounts the same component
  // (and so the same §6.1 fresh-empty copy).
  await page.evaluate(() => window.deskV1GotoCampaignPanel('conversations', { campaignId: 'camp-4' }));
  await page.waitForSelector('.desk-v1-conv-empty', { timeout: 2000 });
  const convText = (await page.textContent('.desk-v1-conv-empty') || '');
  /Replies show up here once a post is live\./.test(convText)
    ? ok('IA6: fresh Active campaign\'s conversations (via Engagement) show UX_PASS §6.1 copy: "Replies show up here once a post is live."')
    : fail(`IA6: fresh Active Conversations copy wrong: ${JSON.stringify(convText)}`);

  reportUncaught(pageErrors, '[ia6-fresh-empty]');
  await ctx.close();
}

async function runRouteStackUnchanged(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCampaign(page);

  // R2-3b: Conversations is no longer a panel (it routes to Engagement, which
  // DOES push), so the in-place leg here is the ② How stop.
  await page.click('[data-stop="how"]');
  await page.waitForSelector('[data-stop="how"][data-state="here"]', { timeout: 2000 });
  await page.click('[data-stop="goal"]');
  await page.waitForSelector('.desk-v1-results', { timeout: 2000 });
  await page.click('[data-stop="what"]');
  await page.waitForSelector('.desk-v1-camp-card', { timeout: 2000 });

  await page.evaluate(() => window.deskV1Back());
  await page.waitForSelector('.desk-v1-home', { timeout: 2000 }).catch(() => {});
  const onHome = await page.$('.desk-v1-home');
  onHome
    ? ok('T2/§2: three in-place panel switches left the route stack at [home, campaign] — one deskV1Back() lands on Home')
    : fail('T2/§2: a panel switch pushed a route onto the stack — deskV1Back() did not land on Home');

  reportUncaught(pageErrors, '[route-stack]');
  await ctx.close();
}

// ── item 5 (MC-977 R0 UX pass, Ron 2026-09-28: "I sent an ask to Posy,
// switched to another tab and the existing data ... disappeared") — text
// typed but not sent into the campaign's Posy box must survive a tab-strip
// switch (Content -> Conversations -> back), and a full navigate-away/back
// (Home and back), both of which rebuild the Posy box's DOM from scratch. ──
async function runPosyDraftPersistence(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCampaign(page);

  const DRAFT = 'draft text that must survive a rebuild';
  await page.fill('#desk-v1-camp-posy-input', DRAFT);

  // T2 (§2): a panel switch is now an IN-PLACE swap on the SAME `campaign`
  // stack entry (desk-v1-shell.js's `_gotoCampaignPanel`) — it never pushes
  // a route or rebuilds the skeleton, so the Posy box is the SAME DOM node
  // across a What -> Conversations -> What round trip, not merely one
  // holding the same value. Capture the node identity via a marker property
  // (a fresh element from a rebuild would not carry it). R2-3b: Conversations
  // is no longer a panel, so the middle leg is the ② How stop.
  await page.evaluate(() => { document.getElementById('desk-v1-camp-posy-input')._deskv1SmokeMarker = 'same-node'; });
  await page.click('[data-stop="how"]');
  await page.waitForSelector('[data-stop="how"][data-state="here"]', { timeout: 2000 });
  await page.click('[data-stop="what"]');
  await page.waitForSelector('#desk-v1-camp-posy-input', { timeout: 2000 });
  await page.waitForTimeout(50);
  const afterTabSwitch = await page.$eval('#desk-v1-camp-posy-input', (ta) => ta.value).catch(() => '');
  const sameNode = await page.evaluate(() => document.getElementById('desk-v1-camp-posy-input')._deskv1SmokeMarker === 'same-node').catch(() => false);
  afterTabSwitch === DRAFT && sameNode
    ? ok('item 5/T2: Posy draft survives a What -> How -> What panel switch, same DOM node (no route push)')
    : fail(`item 5/T2: draft or node identity lost across panel switch: value=${JSON.stringify(afterTabSwitch)}, sameNode=${sameNode}`);

  // Navigate away to Home and back — a harder rebuild than the tab strip
  // (the whole route unmounts).
  await page.evaluate(() => window.deskV1Nav('home', {}));
  await page.waitForSelector('.desk-v1-home', { timeout: 8000 });
  await navToCampaign(page);
  const afterNavAway = await page.$eval('#desk-v1-camp-posy-input', (ta) => ta.value).catch(() => '');
  afterNavAway === DRAFT
    ? ok('item 5: Posy draft survives navigating to Home and back')
    : fail(`item 5: draft lost across Home nav: ${JSON.stringify(afterNavAway)}`);

  // Sending clears the draft (no stale text left behind for the next open).
  // T3: the draft clears on the Accepted transition (§5), 120ms after Send
  // (desk-v1-kit.js's `_startPosyTask`), not synchronously — wait past it.
  await page.click('[data-posy-send="desk-v1-camp-posy-input"]');
  await page.waitForTimeout(200);
  await page.evaluate(() => window.deskV1Nav('home', {}));
  await page.waitForSelector('.desk-v1-home', { timeout: 8000 });

  // T3/§5's "Home card shows ⟳ Posy working" targeted the per-campaign Home
  // card (`.desk-v1-home-camp-card`) that IA1 retires (§1: Home shows project
  // cards now, THE_DESK_V1_IA_REVISION.md §4 row "draft keys" fate table).
  // Re-homing this onto the project card needs the `project:<pid>:` draft-key
  // prefix, which the same row assigns to IA2 ("T3 draft keys prefixed +
  // stable ids") — not built yet, so there is no live DOM to assert against
  // here without reaching ahead into IA2's own scope. Left for IA2's smoke.

  await navToCampaign(page);
  const afterSend = await page.$eval('#desk-v1-camp-posy-input', (ta) => ta.value).catch(() => '');
  afterSend === ''
    ? ok('item 5: sending clears the draft (no stale leftover on reopen)')
    : fail(`item 5: draft not cleared after send: ${JSON.stringify(afterSend)}`);

  reportUncaught(pageErrors, '[posy-draft]');
  await ctx.close();
}

// ── Phone (§11): the toolbar/cards/Posy/Add tray stack single-column, hit
// targets stay touch-sized. ─────────────────────────────────────────────────
async function runPhoneLayout(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} }, { width: 390, height: 844 });
  await navToCampaign(page);

  const rowBox = await page.$eval('[data-what-row]', (el) => { const r = el.getBoundingClientRect(); return { w: r.width, right: r.right }; }).catch(() => null);
  rowBox && rowBox.right <= 390 + 1
    ? ok(`§11: What rows fit the phone width (${rowBox.w.toFixed(0)}px wide, right edge ${rowBox.right.toFixed(0)}px)`)
    : fail(`§11: What row overflows the phone: ${JSON.stringify(rowBox)}`);

  const hit = await page.$eval('[data-what-row] .desk-v1-what-title', (el) => el.getBoundingClientRect().height).catch(() => 0);
  hit >= 44
    ? ok(`§11: a row's title action is touch-sized (${hit.toFixed(0)}px)`)
    : fail(`§11: title action too short for touch: ${hit}px`);
  const typeHit = await page.$eval('[data-what-type]', (el) => el.getBoundingClientRect().height).catch(() => 0);
  typeHit >= 44
    ? ok(`§11: content-type tiles are touch-sized (${typeHit.toFixed(0)}px)`)
    : fail(`§11: type tile too short for touch: ${typeHit}px`);

  // Dave's review (T2a pass 2/4): the docked Posy composer used to be the
  // whole card (message + chips + input), tall enough to squeeze the tab
  // body down to a sliver with its own scroll pocket — no content card
  // was visible at rest. Only the input row docks now; this pins it down.
  const firstCardVisible = await page.$eval('.desk-v1-camp-card', (el) => {
    const r = el.getBoundingClientRect();
    return r.top < window.innerHeight && r.bottom > 0 && r.height > 0;
  }).catch(() => false);
  firstCardVisible
    ? ok('§11: first content card is visible at rest (not hidden behind the docked Posy composer)')
    : fail('§11: first content card is NOT visible at 390x844 at rest');

  reportUncaught(pageErrors, '[phone]');
  await ctx.close();
}

// ── screenshots (default tone only, per the ticket brief). ─────────────────
// ── R2-2g + R2-18 (Ron 2026-09-30): a project is REQUIRED TO START, not to
// proceed. A project-less draft opens on the ① Brief stop (R2-18), which
// carries the Project select (empty) and a disabled Agent picker; the Goal
// stop and the crumb carry no project control; the draft runs every stop
// without throwing or printing "undefined"/"null"; ⑥ Launch carries a Project
// select too (exactly one on the page), Start stays disabled until one is
// picked, a pick goes through the commandBus (Undo reverts), and the picked
// project imposes no limit of its own (Presence retired, MC-977 2026-10-01: the
// campaign's own limits are the only ones). A project with no agent does not
// block Start. (The Agent picker itself is covered in desk-v1-how.mjs.) ─────
async function runProjectSelect(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  const campId = await page.evaluate(() => {
    const camp = window.deskV1CreateDraftCampaign(null);
    window.DeskV1Fixtures.campaigns.push(camp);
    window.deskV1Nav('campaign', { campaignId: camp.id, projectId: null });
    return camp.id;
  });
  await page.waitForSelector('.desk-v1-map-stop[data-stop="how"][data-state="here"]', { timeout: 4000 });
  const camp = (fn, ...a) => page.evaluate(([id, src, args]) => new Function('c', 'args', 'return (' + src + ')(c, args)')(window.DeskV1Fixtures.campaigns.find((c) => c.id === id), args), [campId, fn.toString(), a]);
  const pid = () => camp((c) => c.projectId);
  const rerender = () => page.evaluate(() => window.deskV1Render());
  const selectCount = () => page.evaluate(() => document.querySelectorAll('select[data-setup-project]').length);
  const crumbPickers = () => page.evaluate(() => document.querySelectorAll('#desk-v1-crumb .desk-v1-projects-picker').length);

  // Brief stop (R2-18): ONE project select, empty, no crumb picker, no "Pick a
  // project" gate, no IA4 setup step; the Agent picker is there but disabled.
  const first = await page.evaluate(() => ({
    selects: document.querySelectorAll('select[data-setup-project]').length,
    value: (document.querySelector('select[data-setup-project]') || {}).value,
    crumb: document.querySelectorAll('#desk-v1-crumb .desk-v1-projects-picker').length,
    gate: /Setup\s*[—-]\s*Pick a project/.test(document.querySelector('.desk-v1-campaign').innerText),
    step1: !!document.querySelector('[data-setup-title]') || /Setup\s+\d\s+of\s+3/.test(document.querySelector('.desk-v1-campaign').innerText),
    agentDisabled: (document.querySelector('select[data-how-agent]') || {}).disabled,
  }));
  first.selects === 1 && first.value === '' && first.crumb === 0 && !first.gate && !first.step1 && first.agentDisabled === true
    ? ok('R2-18: a project-less draft lands on Brief with ONE empty project select, a disabled agent picker, NO crumb picker and no "Setup — Pick a project" gate')
    : fail(`R2-18: Brief stop wrong for a project-less draft: ${JSON.stringify(first)}`);

  // The neutral right-column note points at Brief; nothing is written.
  const agentBox = await page.evaluate(() => ({
    neutral: (document.querySelector('[data-no-agent]') || {}).innerText || '',
    posyBox: document.querySelectorAll('.desk-v1-posy-box').length,
  }));
  /No agent yet/.test(agentBox.neutral) && /on Brief/.test(agentBox.neutral) && !/Launch/.test(agentBox.neutral) && agentBox.posyBox === 0 && (await camp((c) => !(c.how && c.how.agent)))
    ? ok(`R2-18: project-less draft has a neutral agent note pointing at Brief ("${agentBox.neutral.replace(/\s+/g, ' ').trim().slice(0, 70)}…"), camp.how.agent unset`)
    : fail(`R2-18: project-less agent box wrong: ${JSON.stringify(agentBox)}`);

  // Goal stop carries no project control.
  await page.click('.desk-v1-map-stop[data-stop="goal"]');
  await page.waitForTimeout(40);
  (await selectCount()) === 0 ? ok('R2-18: the Goal stop has no project select') : fail('R2-18: Goal stop still carries a project select');

  // Every stop renders for a project-less draft: no page errors, no raw values, no crumb picker.
  for (const stop of ['how', 'what', 'when', 'where', 'launch', 'goal']) {
    await page.click(`.desk-v1-map-stop[data-stop="${stop}"]`);
    await page.waitForTimeout(40);
    const txt = await page.evaluate(() => document.querySelector('.desk-v1-campaign').innerText);
    /\bundefined\b|\bnull\b|\[object/.test(txt)
      ? fail(`R2-2g: project-less draft at ${stop} prints a raw value: ${JSON.stringify((txt.match(/.{0,30}(undefined|null|\[object).{0,30}/) || [''])[0])}`)
      : ok(`R2-2g: project-less draft renders ${stop} with no "undefined"/"null" text`);
  }
  (await crumbPickers()) === 0 ? ok('R2-2g: no crumb Projects picker on the campaign route at any stop') : fail('R2-2g: crumb picker shown on a campaign stop');
  (await camp((c) => !(c.how && c.how.agent))) ? ok('R2-2g: visiting every stop never wrote camp.how.agent') : fail('R2-2g: camp.how.agent was written');

  // Launch: exactly one project select (empty), Start disabled, "project" listed.
  await page.click('.desk-v1-map-stop[data-stop="launch"]');
  await page.waitForSelector('[data-setup-project]', { timeout: 4000 });
  const launch = await page.evaluate(() => ({
    selects: document.querySelectorAll('select[data-setup-project]').length,
    crumb: document.querySelectorAll('#desk-v1-crumb .desk-v1-projects-picker').length,
    value: document.querySelector('[data-setup-project]').value,
    prompt: document.querySelector('[data-setup-project]').selectedOptions[0].textContent,
    options: Array.from(document.querySelector('[data-setup-project]').options).filter((o) => o.value).map((o) => o.textContent),
    startDisabled: document.querySelector('[data-map-start-btn]').disabled,
    missing: Array.from(document.querySelectorAll('.desk-v1-map-launch-missing li, .desk-v1-launch-row[data-missing] .desk-v1-launch-val')).map((b) => b.textContent.trim()),
    launchState: document.querySelector('.desk-v1-map-stop[data-stop="launch"]').dataset.state,
  }));
  launch.selects === 1 && launch.crumb === 0 && launch.value === '' && /Pick a project/.test(launch.prompt) && launch.options.includes('Clayrune') && launch.options.includes('Engulfing scanner')
    ? ok(`R2-2g: Launch shows exactly ONE project selector anywhere on the page, empty, listing ${launch.options.join(', ')}`)
    : fail(`R2-2g: Launch project select wrong: ${JSON.stringify(launch)}`);
  launch.startDisabled && launch.missing.some((m) => /^Project/.test(m))
    ? ok(`R2-2g: Start is disabled until a project is picked ("${launch.missing.find((m) => /^Project/.test(m))}")`)
    : fail(`R2-2g: Launch not blocked on a missing project: ${JSON.stringify(launch)}`);

  // Fill the plan by hand (accounts, cadence 3/wk, post cap) so a project's old ceilings would have something to refuse.
  await camp((c) => { c.plan.accounts = ['ch-x-ron']; c.plan.cadence.per_week = 3; c.plan.end = { date: null, post_cap: 12 }; });
  await rerender();
  await page.waitForSelector('[data-setup-project]', { timeout: 4000 });

  // Pick Engulfing scanner (it once capped x at 2/wk; no desk agent): the pick adds no limit, Start is enabled.
  await page.selectOption('[data-setup-project]', 'engulfing_scanner');
  await page.waitForSelector('[data-setup-project]', { timeout: 4000 });
  const conflict = await page.evaluate(() => ({
    value: document.querySelector('[data-setup-project]').value,
    startDisabled: document.querySelector('[data-map-start-btn]').disabled,
    missing: Array.from(document.querySelectorAll('.desk-v1-map-launch-missing li, .desk-v1-launch-row[data-missing] .desk-v1-launch-val')).map((b) => b.textContent.trim()),
    toast: (document.querySelector('.toast') || {}).textContent || '',
  }));
  (await pid()) === 'engulfing_scanner' && conflict.value === 'engulfing_scanner' && /Set campaign project to Engulfing scanner/.test(conflict.toast)
    ? ok(`R2-2g: picking a project on Launch sets camp.projectId and is a commandBus toast ("${conflict.toast.trim().slice(0, 60)}")`)
    : fail(`R2-2g: pick did not stick: ${JSON.stringify({ pid: await pid(), conflict })}`);
  !conflict.startDisabled && conflict.missing.length === 0
    ? ok('R2-2g: after the pick the project imposes no cadence limit: the campaign\'s 3/wk stands, nothing missing, Start enabled')
    : fail(`R2-2g: a project limit still applies after the pick: ${JSON.stringify(conflict)}`);

  // Undo reverts the pick: back to no project, Project listed again.
  await page.locator('.toast .toast-btn.primary').last().click();
  await page.waitForSelector('[data-setup-project]', { timeout: 4000 });
  const undone = await page.evaluate(() => ({ value: document.querySelector('[data-setup-project]').value, startDisabled: document.querySelector('[data-map-start-btn]').disabled, missing: Array.from(document.querySelectorAll('.desk-v1-map-launch-missing li, .desk-v1-launch-row[data-missing] .desk-v1-launch-val')).map((b) => b.textContent.trim()) }));
  (await pid()) === null && undone.value === '' && undone.startDisabled && undone.missing.some((m) => /^Project/.test(m)) && !undone.missing.some((m) => /cadence/.test(m))
    ? ok('R2-2g: Undo reverts the Launch pick (camp.projectId null, select empty, conflict gone, project listed again)')
    : fail(`R2-2g: Undo did not revert: ${JSON.stringify({ pid: await pid(), undone })}`);

  // Clayrune: no conflict, Start enabled.
  await page.selectOption('[data-setup-project]', 'clayrune');
  await page.waitForSelector('[data-setup-project]', { timeout: 4000 });
  const okPick = await page.evaluate(() => ({ startDisabled: document.querySelector('[data-map-start-btn]').disabled, missing: Array.from(document.querySelectorAll('.desk-v1-map-launch-missing li, .desk-v1-launch-row[data-missing] .desk-v1-launch-val')).map((b) => b.textContent.trim()) }));
  (await pid()) === 'clayrune' && !okPick.startDisabled && okPick.missing.length === 0
    ? ok('R2-2g: picking Clayrune leaves nothing missing and enables Start')
    : fail(`R2-2g: Clayrune pick wrong: ${JSON.stringify({ pid: await pid(), okPick })}`);

  // A project with NO agent (Engulfing scanner) must not block Start; the project-level label stays, no per-campaign picker.
  await camp((c) => { c.plan.cadence.per_week = 2; });
  await page.selectOption('[data-setup-project]', 'engulfing_scanner');
  await page.waitForSelector('[data-setup-project]', { timeout: 4000 });
  const noAgent = await page.evaluate(() => ({
    startDisabled: document.querySelector('[data-map-start-btn]').disabled,
    missing: Array.from(document.querySelectorAll('.desk-v1-map-launch-missing li, .desk-v1-launch-row[data-missing] .desk-v1-launch-val')).map((b) => b.textContent.trim()),
    agentLabel: (document.querySelector('.desk-v1-camp-posy .desk-thread-name') || {}).textContent || '',
    picker: document.querySelectorAll('[data-agent-pick], .desk-v1-posy-agentpick').length,
  }));
  const engAgent = await page.evaluate(() => window.DeskV1Fixtures.projects.find((p) => p.id === 'engulfing_scanner').presence.desk_agent || null);
  engAgent === null && !noAgent.startDisabled && noAgent.missing.length === 0 && noAgent.picker === 0 && /Pick who plans for this project/.test(noAgent.agentLabel) && (await camp((c) => !(c.how && c.how.agent)))
    ? ok('R2-2g: a project with no agent does not block Start, keeps its project-level "Pick who plans for this project" label, no per-campaign picker, camp.how.agent unset')
    : fail(`R2-2g: missing agent blocks/changes Launch: ${JSON.stringify({ engAgent, noAgent })}`);
  await page.click('[data-map-start-btn]');
  await page.waitForSelector('[data-sheet-confirm]', { timeout: 4000 });
  !(await page.$eval('[data-sheet-confirm]', (b) => b.disabled)) ? ok('R2-2g: the Start sheet confirm is enabled with no agent assigned') : fail('R2-2g: Start sheet confirm disabled without an agent');
  await page.click('[data-sheet-confirm]');
  await page.waitForTimeout(80);

  // After launch the project is read-only on Launch.
  await page.click('.desk-v1-map-stop[data-stop="launch"]');
  await page.waitForTimeout(60);
  const ro = await page.evaluate(() => ({ state: null, selects: document.querySelectorAll('select[data-setup-project]').length, ro: (document.querySelector('[data-launch-project-ro]') || {}).textContent || '' }));
  ro.selects === 0 && /Project: Engulfing scanner/.test(ro.ro)
    ? ok(`R2-2g: once launched, Launch shows the project read-only ("${ro.ro.trim()}"), no select`)
    : fail(`R2-2g: launched campaign still editable/missing project line: ${JSON.stringify(ro)}`);

  reportUncaught(pageErrors, '[project-select]');
  await ctx.close();
}

// R2-3b retired `runProjectLessSetup`: IA4's setup steps 1 and 2 and the
// "Draft the plan" button it drove no longer exist. Its still-live claim — a
// project-less draft is blocked at Launch until a project is picked — is held
// by runProjectSelect above and desk-v1-map.mjs's "Launch missing list" case.

async function captureScreenshots(browser) {
  {
    const { ctx, page } = await newBootedPage(browser, { ls: {} }, { width: 1440, height: 950 });
    await navToCampaign(page);
    await page.waitForTimeout(80);
    await page.screenshot({ path: resolve(SHOT_DIR, 't2a_campaign_desktop_1440.png') });
    ok('desktop screenshot saved: t2a_campaign_desktop_1440.png');
    await ctx.close();
  }
  {
    const { ctx, page } = await newBootedPage(browser, { ls: {} }, { width: 390, height: 844 });
    await navToCampaign(page);
    await page.waitForTimeout(80);
    await page.screenshot({ path: resolve(SHOT_DIR, 't2a_campaign_phone_390.png') });
    ok('phone screenshot saved: t2a_campaign_phone_390.png');
    await ctx.close();
  }
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  for (const tone of TONES) await runToneRenderChecks(browser, tone);
  await runFilterToggle(browser);
  await runViewToggle(browser);
  await runCardMenu(browser);
  await runWhatTray(browser);
  await runCampaignPageMoreMenu(browser);
  await runPauseResume(browser);
  await runFreshEmptyStates(browser);
  await runRouteStackUnchanged(browser);
  await runPosyDraftPersistence(browser);
  await runPhoneLayout(browser);
  await runProjectSelect(browser);
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

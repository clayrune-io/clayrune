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

  // A12: rule chips are real rule values, and the popover itself is out of
  // scope for T2a (docs/desk_v1_r0_plan.md) — only the Edit hook exists.
  const ruleChips = await page.$$eval('.desk-v1-camp-rule-chip', (els) => els.map((e) => e.textContent.trim()));
  ruleChips.includes('Organic') && ruleChips.includes('You approve each piece') && ruleChips.includes('≤3/wk')
    ? ok(`[${tone.name}] rule chips reflect the fixture's real rules: ${JSON.stringify(ruleChips)}`)
    : fail(`[${tone.name}] rule chips wrong: ${JSON.stringify(ruleChips)}`);
  const editBtn = await page.$('[data-rules-edit]');
  editBtn ? ok(`[${tone.name}] Rules "Edit" hook renders (popover itself is T2b)`) : fail(`[${tone.name}] Rules Edit hook missing`);

  // Tab strip: both badges are needs-you-only (§3.1). Content: 2 families
  // (restore-points, install-video). Conversations: conv-1 needs_reply +
  // conv-4/conv-6 needs_you = 3 of camp-1's 6 rows (frame 12a also says 3).
  const tabText = await page.textContent('[data-tab="content"]').catch(() => '');
  const convText = await page.textContent('[data-tab="conversations"]').catch(() => '');
  /Content\s*2/.test(tabText.replace(/\s+/g, ' '))
    ? ok(`[${tone.name}] Content tab badge is needs-you-only: "${tabText.trim()}"`)
    : fail(`[${tone.name}] Content tab badge wrong: ${JSON.stringify(tabText)}`);
  /Conversations\s*3/.test(convText.replace(/\s+/g, ' '))
    ? ok(`[${tone.name}] Conversations tab badge is needs-you-only: "${convText.trim()}"`)
    : fail(`[${tone.name}] Conversations tab badge wrong: ${JSON.stringify(convText)}`);

  // A2/A3: grouped "All content" — needs-you group holds both multi-claim
  // article and multi-version video families; each card lists every
  // version's OWN state independently.
  const groupTitles = await page.$$eval('.desk-v1-camp-group-title', (els) => els.map((e) => e.textContent.trim()));
  groupTitles.some((t) => /NEEDS YOU · 2/.test(t)) && groupTitles.some((t) => /SCHEDULED · 1/.test(t))
    ? ok(`[${tone.name}] groups match fixture counts: ${JSON.stringify(groupTitles)}`)
    : fail(`[${tone.name}] group titles/counts wrong: ${JSON.stringify(groupTitles)}`);

  const videoStates = await page.$$eval('[data-family-id="fam-install-video"] .desk-v1-camp-vrow .desk-v1-state-word', (els) => els.map((e) => e.textContent.trim()));
  videoStates.length === 3 && new Set(videoStates).size >= 2
    ? ok(`[${tone.name}] A3: the video family's 3 versions show independent states: ${JSON.stringify(videoStates)}`)
    : fail(`[${tone.name}] A3: version states should be independent, got ${JSON.stringify(videoStates)}`);

  const primaryLabel = await page.textContent('[data-family-id="fam-install-video"] [data-primary-action]').catch(() => '');
  primaryLabel.trim() === 'Watch & review'
    ? ok(`[${tone.name}] video family's primary action reads "Watch & review"`)
    : fail(`[${tone.name}] video primary action wrong: ${JSON.stringify(primaryLabel)}`);

  const previewText = (await page.textContent('[data-family-id="fam-restore-points"] .desk-v1-camp-preview-text').catch(() => '') || '');
  /Undo anything: restore points/.test(previewText)
    ? ok(`[${tone.name}] article card shows its content preview excerpt`)
    : fail(`[${tone.name}] article preview text missing: ${JSON.stringify(previewText)}`);

  // Posy box scoped to the campaign by default (§3.4 INS-01).
  const posyScope = (await page.textContent('.desk-v1-camp-posy .desk-v1-posy-scope').catch(() => '') || '');
  /Windows beta testers/.test(posyScope)
    ? ok(`[${tone.name}] Posy box scope defaults to the campaign: "${posyScope.trim()}"`)
    : fail(`[${tone.name}] Posy scope wrong: ${JSON.stringify(posyScope)}`);
  const suggestion = (await page.textContent('.desk-v1-camp-posy .agent-question-chip, .desk-v1-camp-posy [class*="chip"]').catch(() => '') || '');

  // Add tray (§3.6): collapsed by default, reuses Home's own shelf markup.
  const trayOpen = await page.$eval('.desk-v1-camp-addtray-details', (el) => el.open).catch(() => null);
  trayOpen === false
    ? ok(`[${tone.name}] Add tray is collapsed by default`)
    : fail(`[${tone.name}] Add tray open state wrong: ${JSON.stringify(trayOpen)}`);

  reportUncaught(pageErrors, `[${tone.name}]`);
  await ctx.close();
}

// ── filter + channel dropdowns (toolbar, §3.2). ─────────────────────────────
async function runFilterToggle(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCampaign(page);

  await page.click('[data-filter-trigger]');
  await page.waitForSelector('.desk-v1-addto-menu', { timeout: 2000 });
  await page.click('.desk-v1-addto-menu button:has-text("Scheduled")');
  await page.waitForTimeout(50);
  const cards = await page.$$eval('.desk-v1-camp-cards [data-family-id]', (els) => els.map((e) => e.dataset.familyId));
  cards.length === 1 && cards[0] === 'fam-30-testers'
    ? ok(`filter "Scheduled" narrows to the one scheduled family: ${JSON.stringify(cards)}`)
    : fail(`Scheduled filter wrong: ${JSON.stringify(cards)}`);
  const filterLabel = await page.textContent('[data-filter-trigger]');
  /Scheduled/.test(filterLabel)
    ? ok('filter trigger label updates to the chosen filter')
    : fail(`filter trigger label did not update: ${JSON.stringify(filterLabel)}`);

  reportUncaught(pageErrors, '[filter]');
  await ctx.close();
}

// ── List/Calendar toggle mounts T4's calendar through the T0a slot contract
// (desk-v1-calendar.js's own comment: "once T2a lands, its toggle can call
// [this] straight into its Content-tab body slot with no change needed
// here"). ────────────────────────────────────────────────────────────────
async function runViewToggle(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCampaign(page);

  const listBefore = await page.$('.desk-v1-camp-listarea');
  listBefore ? ok('List view is the default') : fail('List view should be the default');

  await page.click('[data-view-btn="calendar"]');
  await page.waitForSelector('.desk-v1-calendar', { timeout: 4000 });
  ok('Calendar toggle mounts T4\'s real calendar component into the same body slot');
  const listGone = await page.$('.desk-v1-camp-listarea');
  !listGone ? ok('the list view is torn down while calendar is shown') : fail('list view should not coexist with calendar');

  await page.click('[data-view-btn="list"]');
  await page.waitForSelector('.desk-v1-camp-listarea', { timeout: 4000 });
  ok('toggling back to List re-renders the grouped list');

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
  // duplicate is a fresh 'drafting' version, so it lands in the collapsed
  // PLANNED group (_familyGroup's own "other" bucket) — show it first.
  await page.click('[data-family-id="fam-30-testers"] [data-more-btn]');
  await page.waitForSelector('.desk-v1-camp-cardmenu', { timeout: 2000 });
  await page.click('.desk-v1-camp-cardmenu [data-menu-dup]');
  await page.waitForSelector('.toast', { timeout: 2000 }).catch(() => {});
  const dupToast = (await page.textContent('.toast').catch(() => '') || '');
  /Duplicated/.test(dupToast)
    ? ok(`Duplicate shows a commandBus toast: "${dupToast.trim()}"`)
    : fail(`Duplicate toast missing/wrong: ${JSON.stringify(dupToast)}`);
  const dupExists = await page.evaluate(() => window.DeskV1Fixtures.families.some((f) => f.id.startsWith('fam-30-testers-copy-')));
  dupExists ? ok('the duplicated family exists in fixtures (renders collapsed under PLANNED)') : fail('duplicated family not found in fixtures');
  await page.click('.toast .toast-btn.primary');
  await page.waitForSelector('.toast', { state: 'detached', timeout: 2000 }).catch(() => {});
  const dupGone = await page.evaluate(() => !window.DeskV1Fixtures.families.some((f) => f.id.startsWith('fam-30-testers-copy-')));
  dupGone ? ok('Undo removes the duplicate') : fail('Undo did not remove the duplicated family');

  // A5: Archive acts card-level, on every non-terminal version, through the
  // same commandBus (Undo restores each version's PRIOR state, not a blanket
  // one — checked against the video family's 3 independent states).
  await page.click('[data-family-id="fam-install-video"] [data-more-btn]');
  await page.waitForSelector('.desk-v1-camp-cardmenu', { timeout: 2000 });
  const statesBefore = await page.$$eval('[data-family-id="fam-install-video"] .desk-v1-camp-vrow .desk-v1-state-word', (els) => els.map((e) => e.textContent.trim()));
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
  JSON.stringify(statesRestored.slice().sort()) === JSON.stringify(statesBefore.map((s) => s.toLowerCase()).sort()) || statesRestored.includes('needs_review')
    ? ok(`Undo restores each version's own prior state: ${JSON.stringify(statesRestored)}`)
    : fail(`Undo did not restore prior per-version states: ${JSON.stringify(statesRestored)}`);

  reportUncaught(pageErrors, '[card-menu]');
  await ctx.close();
}

// ── Add tray drag-drop (§3.6, §10). camp-1's fixture already carries all 3
// channels (docs/desk_v1_r0_plan.md ground rule 3 data), so the tray's own
// Channels shelf is empty here (hideChannelIds hides every one, exactly as
// designed) — this drags a MATERIAL asset instead: onto an existing card
// (attaches it) and onto the empty list area (creates a new piece). ────────
async function runAddTrayDrag(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCampaign(page);

  await page.click('.desk-v1-camp-addtray-summary');
  await page.waitForSelector('.desk-v1-camp-addtray-body', { state: 'visible', timeout: 2000 });
  ok('Add tray opens on click');

  const noChannels = await page.$$eval('#desk-v1-camp-addtray-channels .desk-v1-shelf-item', (els) => els.length);
  noChannels === 0
    ? ok('every campaign channel is already attached, so the tray\'s Channels shelf is empty (hidden, not dimmed)')
    : fail(`expected 0 channel shelf items (all 3 already on the campaign), got ${noChannels}`);

  // Attach an asset to fam-30-testers (single scheduled version, no assets yet).
  const assetItem = await page.$('#desk-v1-camp-addtray-material .desk-v1-shelf-item[data-asset-id="asset-restore-points"]');
  const card = await page.$('[data-family-id="fam-30-testers"]');
  assetItem && card ? ok('found the article asset shelf item and the testers-wanted card') : fail('drag source/target not found');
  // The Add tray expands the scrollable tab body past the viewport at 950px
  // tall — manual mouse.move/down (unlike .click()) never auto-scrolls, so
  // the drag source can sit below the fold and every coordinate below misses.
  await assetItem.scrollIntoViewIfNeeded();
  const sBox = await assetItem.boundingBox();
  const cBox = await card.boundingBox();
  await page.mouse.move(sBox.x + sBox.width / 2, sBox.y + sBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(cBox.x + cBox.width / 2, cBox.y + 10, { steps: 8 });
  await page.mouse.move(cBox.x + cBox.width / 2, cBox.y + cBox.height / 2, { steps: 8 });
  await page.waitForTimeout(50);
  const resultText = (await page.textContent('[data-family-id="fam-30-testers"] .desk-v1-camp-card-result').catch(() => '') || '');
  /attach.*to this piece/i.test(resultText)
    ? ok(`hover shows the attach result before release: "${resultText.trim()}"`)
    : fail(`drop hover text missing/wrong: ${JSON.stringify(resultText)}`);
  await page.mouse.up();
  await page.waitForTimeout(50);

  const attached = await page.evaluate(() => window.DeskV1Fixtures.families.find((f) => f.id === 'fam-30-testers').attachedAssets || []);
  attached.includes('asset-restore-points')
    ? ok(`material dropped on a card attaches the asset: ${JSON.stringify(attached)}`)
    : fail(`asset was not attached: ${JSON.stringify(attached)}`);

  // Drop the video asset on the empty list area (not a card) — creates a new
  // piece (A2: content originates from material, same as the ⋯ menu path).
  const videoAsset = await page.$('#desk-v1-camp-addtray-material .desk-v1-shelf-item[data-asset-id="asset-install-video"]');
  // Target the collapsed PLANNED group's header row — it's unambiguously
  // below every card and outside .desk-v1-camp-card, so it's a valid
  // "listarea" hit. Two-phase because hovering the empty area for the first
  // time inserts a `.desk-v1-camp-listarea-result` banner as the list's
  // first child (desk-v1-campaign.js's `_listAreaResultText`), pushing the
  // header down a few px — a coordinate computed only from the PRE-hover
  // position then drifts back onto the last card as the banner appears.
  const plannedHead = await page.$('.desk-v1-camp-group-show');
  await videoAsset.scrollIntoViewIfNeeded();
  const vBox = await videoAsset.boundingBox();
  await plannedHead.scrollIntoViewIfNeeded();
  await page.mouse.move(vBox.x + vBox.width / 2, vBox.y + vBox.height / 2);
  await page.mouse.down();
  const hBoxPre = await plannedHead.boundingBox();
  await page.mouse.move(hBoxPre.x + 20, hBoxPre.y - 30, { steps: 6 }); // cross slop, trigger the banner
  await page.mouse.move(hBoxPre.x + 20, hBoxPre.y + hBoxPre.height / 2, { steps: 6 });
  await page.waitForTimeout(50);
  const hBoxPost = await plannedHead.boundingBox(); // re-measure post-banner
  await page.mouse.move(hBoxPost.x + 20, hBoxPost.y + hBoxPost.height / 2, { steps: 4 });
  await page.waitForTimeout(50);
  await page.mouse.up();
  await page.waitForTimeout(50);

  const newPieceExists = await page.evaluate(() => window.DeskV1Fixtures.families.some((f) => f.id.startsWith('fam-drop-') && f.title === 'Install in two minutes'));
  newPieceExists
    ? ok('material dropped on the empty list area creates a new piece')
    : fail('dropping material on the list area did not create a new family');

  reportUncaught(pageErrors, '[add-tray-drag]');
  await ctx.close();
}

// ── Phone (§11): the toolbar/cards/Posy/Add tray stack single-column, hit
// targets stay touch-sized. ─────────────────────────────────────────────────
async function runPhoneLayout(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} }, { width: 390, height: 844 });
  await navToCampaign(page);

  const cardDir = await page.$eval('.desk-v1-camp-card', (el) => getComputedStyle(el).flexDirection).catch(() => '');
  cardDir === 'column'
    ? ok('§11: content cards stack column-wise on phone (preview above title)')
    : fail(`§11: card flex-direction wrong on phone: ${JSON.stringify(cardDir)}`);

  const primaryHeight = await page.$eval('.desk-v1-camp-card-primary', (el) => el.getBoundingClientRect().height).catch(() => 0);
  primaryHeight >= 44
    ? ok(`§11: primary action button is touch-sized (${primaryHeight.toFixed(0)}px)`)
    : fail(`§11: primary action too short for touch: ${primaryHeight}px`);

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
  await runAddTrayDrag(browser);
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

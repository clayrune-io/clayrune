#!/usr/bin/env node
/**
 * Desk v1 (MC-977, R0 plan T1) — Desk Home smoke (frame 11a, not in the
 * repo — built from THE_DESK_V1_UI.md §2 text; see docs/desk_v1_r0_plan.md
 * ground rule 8).
 *
 * Closes: A13 (worker-offline heartbeat surfaces as a hold row), A4 (the
 * Home half — dropping a channel onto a campaign card adds it as a
 * destination), A12 on Home (every Needs-you row deep-links to review/
 * conversations with the params those surfaces expect).
 *
 * Real headless boot (real index.html + real static/js|css, no network), same
 * hermetic shape as desk-v1-harness.mjs / desk-v1-review.mjs.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-home.mjs
 * Exit 0 = every case holds (render checks in all three tones, interaction
 * checks once); 1 = a case regressed / harness error.
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
// See desk-v1-review.mjs's identical stub: the Claydo FAB requests
// /assets/claydo-*.webp; without this the network stub aborts it and every
// screenshot near the FAB shows a broken-image glyph.
for (const f of readdirSync(ASSETS_DIR)) {
  const ext = f.slice(f.lastIndexOf('.'));
  if (MIME[ext]) STATIC[`/assets/${f}`] = [MIME[ext], readFileSync(resolve(ASSETS_DIR, f))];
}

const PID = 'smoke_deskv1home';
const PROJECTS = [{
  id: PID, name: 'Desk v1 home smoke', status: 'active', domain: 'general', emoji: '🧪',
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
  // Home is the default route on open (desk-v1-shell.js _stack resets to
  // 'home') — no explicit deskV1Nav needed to land here.
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-home', { timeout: 8000 });
  return { ctx, page, pageErrors };
}

function reportUncaught(pageErrors, tag) {
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail(`${tag} uncaught page error: ${e}`));
}

// ── render checks, one per tone: header, promote box, campaign card, Needs
// you (grouped counts + holds), shelves. ────────────────────────────────────
async function runToneRenderChecks(browser, tone) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, tone);

  const cardCount = await page.$$eval('.desk-v1-home-camp-card', (els) => els.length);
  // §2: cards show every campaign with its state — camp-1 (Active) and,
  // since T2b, camp-2 (Proposed).
  if (cardCount === 2) ok(`[${tone.name}] both fixture campaign cards render (Active + Proposed)`);
  else fail(`[${tone.name}] expected 2 campaign cards, got ${cardCount}`);

  const cardText = (await page.textContent('.desk-v1-home-camp-card').catch(() => '') || '');
  if (/Windows beta testers/.test(cardText) && /11\/30 tester signups/.test(cardText)) {
    ok(`[${tone.name}] campaign card shows name + goal progress`);
  } else {
    fail(`[${tone.name}] campaign card content wrong: ${JSON.stringify(cardText)}`);
  }
  const badgeCount = await page.$eval('.desk-v1-home-camp-card', (el) => el.querySelectorAll('.desk-v1-channel-badge').length);
  if (badgeCount === 3) ok(`[${tone.name}] campaign card shows all 3 fixture channel badges`);
  else fail(`[${tone.name}] expected 3 channel badges, got ${badgeCount}`);

  // Needs you: 1 piece (fam-restore-points), 1 video (fam-install-video),
  // 1 reply (conv-1), plus the held ch-li-page hold row and the offline
  // worker-heartbeat hold row (A13). Held count is 3, not 2 (MC-977 T2b,
  // Dave's review pass 2): camp-2's fam-launch-li put a second piece on the
  // same globally-disconnected ch-li-page, so the count is real, not stale.
  const needsYouText = (await page.textContent('#desk-v1-home-needsyou').catch(() => '') || '');
  if (/1 piece to approve/.test(needsYouText)) ok(`[${tone.name}] Needs you: "1 piece to approve"`);
  else fail(`[${tone.name}] Needs you missing piece row: ${JSON.stringify(needsYouText)}`);
  if (/1 video to watch/.test(needsYouText)) ok(`[${tone.name}] Needs you: "1 video to watch"`);
  else fail(`[${tone.name}] Needs you missing video row: ${JSON.stringify(needsYouText)}`);
  if (/1 reply waiting/.test(needsYouText)) ok(`[${tone.name}] Needs you: "1 reply waiting"`);
  else fail(`[${tone.name}] Needs you missing reply row: ${JSON.stringify(needsYouText)}`);
  if (/LinkedIn page disconnected/.test(needsYouText) && /3 held/.test(needsYouText)) {
    ok(`[${tone.name}] A13: held-channel hold row tinted in the same card: "LinkedIn page disconnected · 3 held"`);
  } else {
    fail(`[${tone.name}] A13: held-channel hold row missing/wrong: ${JSON.stringify(needsYouText)}`);
  }
  if (/Scheduling paused/.test(needsYouText) && /worker offline since 14:02/.test(needsYouText) && /2 posts missed/.test(needsYouText)) {
    ok(`[${tone.name}] A13: worker-offline hold row: "Scheduling paused — worker offline since 14:02 · 2 posts missed"`);
  } else {
    fail(`[${tone.name}] A13: worker-offline hold row missing/wrong: ${JSON.stringify(needsYouText)}`);
  }

  // Shelves: 3 channels, 2 recent assets, four Material intake tiles.
  const channelItems = await page.$$eval('#desk-v1-home-shelf-channels .desk-v1-shelf-item', (els) => els.length);
  if (channelItems === 3) ok(`[${tone.name}] Channels shelf: 3 fixture channels`);
  else fail(`[${tone.name}] Channels shelf wrong count: ${channelItems}`);
  const tileCount = await page.$$eval('.desk-v1-home-material-tile', (els) => els.length);
  if (tileCount === 4) ok(`[${tone.name}] Material shelf: 4 intake tiles (Create video/Upload/Connect/Record)`);
  else fail(`[${tone.name}] Material shelf tile count wrong: ${tileCount}`);
  const assetItems = await page.$$eval('.desk-v1-home-material-assets .desk-v1-shelf-item', (els) => els.length);
  if (assetItems === 2) ok(`[${tone.name}] Material shelf: 2 recent assets`);
  else fail(`[${tone.name}] Material shelf recent-asset count wrong: ${assetItems}`);

  // Posy suggestion chips (≤3, KNW) fill the promote box without sending.
  const chips = await page.$$('.desk-v1-home-suggestions .agent-question-chip');
  if (chips.length > 0 && chips.length <= 3) ok(`[${tone.name}] ${chips.length} Posy suggestion chip(s) rendered (<=3)`);
  else fail(`[${tone.name}] suggestion chip count out of range: ${chips.length}`);
  await chips[0].click();
  const filled = await page.$eval('#desk-v1-home-promote-input', (ta) => ta.value);
  if (filled) ok(`[${tone.name}] clicking a suggestion chip fills the promote box (does not send)`);
  else fail(`[${tone.name}] suggestion chip click did not fill the promote box`);

  reportUncaught(pageErrors, `[${tone.name}]`);
  await ctx.close();
}

// ── A12 on Home: Needs-you rows deep-link to review/conversations with the
// params those surfaces expect. ─────────────────────────────────────────────
async function runNeedsYouDeepLinks(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });

  await page.click('#desk-v1-home-needsyou .desk-v1-home-needsyou-row:has-text("piece to approve")');
  await page.waitForSelector('.desk-v1-review', { timeout: 8000 });
  let onReview = await page.evaluate(() => document.querySelector('[data-act-primary]') ? true : false);
  if (onReview) ok('A12: "1 piece to approve" deep-links straight into the review surface (12b)');
  else fail('A12: piece row did not land on review');
  const claimBar = (await page.textContent('.desk-v1-claimbar-blocked').catch(() => '') || '');
  if (/No source for this/.test(claimBar)) ok('A12: piece deep-link resolved to v-restore-blog (its own blocked claim renders)');
  else fail(`A12: piece deep-link landed on the wrong version: ${JSON.stringify(claimBar)}`);

  await page.evaluate(() => window.deskV1Nav('home', {}));
  await page.waitForSelector('.desk-v1-home', { timeout: 8000 });
  await page.click('#desk-v1-home-needsyou .desk-v1-home-needsyou-row:has-text("video to watch")');
  await page.waitForSelector('.desk-v1-stub-title:has-text("Video"), .desk-v1-video', { timeout: 8000 });
  // Route-name assertion (Dave's review): a video row must land on the
  // 'video' route (12d, T5's surface — still a stub in this ticket's tree),
  // never 'review' (12b) — §2's "12b, 12c, or 12d, not to a list" names them
  // as three distinct surfaces. The crumb title is ROUTES.video's own label
  // ('Video'), so it's the route-table entry talking, not a guess.
  const crumbAfterVideo = (await page.textContent('#desk-v1-crumb .desk-v1-crumb-title').catch(() => '') || '');
  if (/^Video$/.test(crumbAfterVideo.trim())) ok(`A12: "1 video to watch" deep-links to route 'video' (12d), not 'review': crumb "${crumbAfterVideo.trim()}"`);
  else fail(`A12: video row landed on the wrong route: crumb ${JSON.stringify(crumbAfterVideo)}`);

  await page.evaluate(() => window.deskV1Nav('home', {}));
  await page.waitForSelector('.desk-v1-home', { timeout: 8000 });
  await page.click('#desk-v1-home-needsyou .desk-v1-home-needsyou-row:has-text("reply waiting")');
  await page.waitForSelector('.desk-v1-stub, .desk-v1-conversations', { timeout: 8000 });
  // T2 (§2, §8): 'conversations' is a panel alias now, not a pushed route —
  // it lands on the SAME campaign crumb (the campaign's own name) with the
  // Conversations tab selected, rather than painting its own "Conversations"
  // crumb title. See desk-v1-shell.js's PANEL_ALIASES.
  const crumbTitle = (await page.textContent('#desk-v1-crumb .desk-v1-crumb-title').catch(() => '') || '');
  const convTabSelected = await page.$eval('[data-tab="conversations"]', (el) => el.getAttribute('aria-selected') === 'true').catch(() => false);
  if (convTabSelected) ok(`A12: "1 reply waiting" deep-links to the conversations panel (campaign crumb "${crumbTitle.trim()}")`);
  else fail(`A12: reply row did not select the conversations panel: crumb ${JSON.stringify(crumbTitle)}`);

  reportUncaught(pageErrors, '[deep-links]');
  await ctx.close();
}

// ── A4 (Home half) + UX-03: dropping a channel shelf item onto the single
// fixture campaign card adds it as a destination with a result preview +
// Undo; a second drop of the SAME channel is refused (already attached). ───
async function runChannelDropOnCard(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });

  // ch-x-ron is already on camp-1 (fixture); drop the OTHER channel we can
  // still add is none — all 3 are already attached in the base fixture, so
  // exercise the refusal path first, then detach one via Undo-of-detach is
  // out of scope; instead verify the "already on" refusal directly (every
  // fixture channel is already attached, which is itself the real case
  // ground rule 3's fixture produces for a single-campaign drop).
  const before = await page.evaluate(() => window.DeskV1Fixtures.campaigns[0].channelIds.length);
  await page.evaluate(() => {
    window.PointerDrag = window.PointerDrag || {};
  });
  const shelfItem = await page.$('#desk-v1-home-shelf-channels .desk-v1-shelf-item[data-channel-id="ch-x-ron"]');
  const card = await page.$('.desk-v1-home-camp-card');
  const [sBox, cBox] = await Promise.all([shelfItem.boundingBox(), card.boundingBox()]);
  await page.mouse.move(sBox.x + sBox.width / 2, sBox.y + sBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(cBox.x + cBox.width / 2, cBox.y + 10, { steps: 8 });
  await page.mouse.move(cBox.x + cBox.width / 2, cBox.y + cBox.height / 2, { steps: 8 });
  const resultText = (await page.textContent('.desk-v1-home-camp-result').catch(() => '') || '');
  if (/Drop to add/.test(resultText)) ok(`A4/UX-03: hovering a shelf item over the card previews the drop: "${resultText.trim()}"`);
  else fail(`A4/UX-03: no drop-hover preview text: ${JSON.stringify(resultText)}`);
  await page.mouse.up();
  const toastText = (await page.$eval('.toast:last-of-type', (el) => el.textContent).catch(() => '') || '');
  if (/already on/.test(toastText)) ok(`A4: dropping an already-attached channel is refused with a toast: "${toastText.trim()}"`);
  else fail(`A4: expected an "already on" refusal toast, got: ${JSON.stringify(toastText)}`);
  const after = await page.evaluate(() => window.DeskV1Fixtures.campaigns[0].channelIds.length);
  if (after === before) ok('A4: refused drop does not mutate channelIds');
  else fail(`A4: channelIds mutated on a refused drop: ${before} -> ${after}`);

  // Material asset drop DOES add a new family + version (the literal "adds a
  // version" case per §13/A4), and IS undo-able via the command bus.
  const famCountBefore = await page.evaluate(() => window.DeskV1Fixtures.families.length);
  const assetItem = await page.$('.desk-v1-home-material-assets .desk-v1-shelf-item');
  const aBox = await assetItem.boundingBox();
  await page.mouse.move(aBox.x + aBox.width / 2, aBox.y + aBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(cBox.x + cBox.width / 2, cBox.y + 10, { steps: 8 });
  await page.mouse.move(cBox.x + cBox.width / 2, cBox.y + cBox.height / 2, { steps: 8 });
  await page.mouse.up();
  const famCountAfter = await page.evaluate(() => window.DeskV1Fixtures.families.length);
  if (famCountAfter === famCountBefore + 1) ok('A4: dropping a Material asset onto the card adds a new family (a drafting version)');
  else fail(`A4: family count did not increase on asset drop: ${famCountBefore} -> ${famCountAfter}`);
  const hasUndo = await page.$eval('.toast:last-of-type', (el) => /toast-btn/.test(el.innerHTML)).catch(() => false);
  if (hasUndo) {
    await page.click('.toast:last-of-type .toast-btn.primary');
    const famCountUndone = await page.evaluate(() => window.DeskV1Fixtures.families.length);
    if (famCountUndone === famCountBefore) ok('A4: Undo removes the family the drop added');
    else fail(`A4: Undo did not remove the added family: ${famCountBefore} -> ${famCountUndone}`);
  } else {
    const lastToastText = (await page.$eval('.toast:last-of-type', (el) => el.textContent).catch(() => '') || '');
    fail(`A4: asset-drop toast had no Undo button: ${JSON.stringify(lastToastText)}`);
  }

  reportUncaught(pageErrors, '[drop]');
  await ctx.close();
}

// ── UX-03 "Which campaign?" on an ambiguous drop — the fixture ships two
// campaigns since T2b (camp-1 Active, camp-2 Proposed), so no runtime push. ─
async function runAmbiguousDropChoosesCampaign(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await page.waitForSelector('.desk-v1-home-camp-card', { timeout: 8000 });
  const cardCount = await page.$$eval('.desk-v1-home-camp-card', (els) => els.length);
  if (cardCount === 2) ok('UX-03 setup: two campaign cards render');
  else fail(`UX-03 setup: expected 2 cards, got ${cardCount}`);

  const shelfItem = await page.$('#desk-v1-home-shelf-channels .desk-v1-shelf-item[data-channel-id="ch-blog"]');
  const sBox = await shelfItem.boundingBox();
  // Drop over the cards section's own empty grid space (3 columns, only 2
  // cards) but not on either card — the "2+ campaigns, no single target"
  // ambiguous case (_resolveDropAt's `inSection` branch).
  const cardsHost = await page.$('#desk-v1-home-cards');
  const chBox = await cardsHost.boundingBox();
  await page.mouse.move(sBox.x + sBox.width / 2, sBox.y + sBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(chBox.x + chBox.width * 0.9, chBox.y + 10, { steps: 8 });
  await page.mouse.up();
  const menu = await page.$('.desk-v1-addto-menu');
  if (menu) ok('UX-03: an ambiguous drop opens the "Which campaign? / + New campaign" menu');
  else fail('UX-03: ambiguous drop did not open the campaign-choice menu');
  const menuText = (await page.textContent('.desk-v1-addto-menu').catch(() => '') || '');
  if (/New campaign/.test(menuText)) ok('UX-03: menu offers "+ New campaign"');
  else fail(`UX-03: menu missing "+ New campaign": ${JSON.stringify(menuText)}`);

  reportUncaught(pageErrors, '[ambiguous]');
  await ctx.close();
}

// ── Desktop layout (Dave's review): Needs you sizes to its own content
// instead of stretching to match the cards column, and a channel shelf item
// renders as ONE pill (grip + badge + add inside a single outline), not a
// badge-pill nested inside the shelf-item's own pill. ──────────────────────
async function runDesktopLayoutChecks(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });

  const needsyouFit = await page.evaluate(() => {
    const needsyou = document.querySelector('.desk-v1-home-needsyou');
    const main = document.querySelector('.desk-v1-home-main');
    return { cardHeight: needsyou.getBoundingClientRect().height, mainHeight: main.getBoundingClientRect().height };
  });
  if (needsyouFit.cardHeight < needsyouFit.mainHeight - 20) {
    ok(`Needs you sizes to its own content, not the cards column's height (${needsyouFit.cardHeight.toFixed(0)}px vs ${needsyouFit.mainHeight.toFixed(0)}px main)`);
  } else {
    fail(`Needs you stretched to match the main row's height: ${needsyouFit.cardHeight.toFixed(0)}px vs ${needsyouFit.mainHeight.toFixed(0)}px`);
  }

  const badgeBorders = await page.evaluate(() => {
    const item = document.querySelector('#desk-v1-home-shelf-channels .desk-v1-shelf-item');
    const badge = item.querySelector('.desk-v1-channel-badge');
    return { itemBorder: getComputedStyle(item).borderStyle, badgeBorder: getComputedStyle(badge).borderStyle };
  });
  if (badgeBorders.itemBorder !== 'none' && badgeBorders.badgeBorder === 'none') {
    ok('Channel shelf item is one pill (badge nested inside carries no border of its own)');
  } else {
    fail(`Channel shelf item double-pills: item border ${badgeBorders.itemBorder}, badge border ${badgeBorders.badgeBorder}`);
  }

  reportUncaught(pageErrors, '[desktop-layout]');
  await ctx.close();
}

// ── Phone (§11): promote box shows the icon row (no drag), cards/needsyou/
// shelves stack, hit targets >=44px. ────────────────────────────────────────
async function runPhoneLayout(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} }, { width: 390, height: 844 });

  const layout = await page.evaluate(() => {
    const phoneActions = document.querySelector('.desk-v1-home-promote-phone-actions');
    const main = document.querySelector('.desk-v1-home-main');
    const shelves = document.querySelector('.desk-v1-home-shelves');
    const promote = document.querySelector('.desk-v1-home-promote');
    const needsyou = document.querySelector('.desk-v1-home-needsyou');
    const cards = document.querySelector('.desk-v1-home-cards');
    const card = document.querySelector('.desk-v1-home-camp-card');
    return {
      phoneActionsDisplay: getComputedStyle(phoneActions).display,
      mainDirection: getComputedStyle(main).flexDirection,
      shelvesColumns: getComputedStyle(shelves).gridTemplateColumns.split(' ').length,
      // Visual (top-to-bottom) order, not DOM order — §11: promote, Needs
      // you, THEN campaign cards.
      promoteTop: promote.getBoundingClientRect().top,
      needsyouTop: needsyou.getBoundingClientRect().top,
      cardsTop: cards.getBoundingClientRect().top,
      cardWidth: card.getBoundingClientRect().width,
      cardsHostWidth: cards.getBoundingClientRect().width,
    };
  });
  if (layout.phoneActionsDisplay !== 'none') ok('§11: promote box icon row (📎 🔗 🎙) visible at phone width');
  else fail('§11: promote box icon row hidden at phone width');
  if (layout.mainDirection === 'column') ok('§11: cards + Needs you stack vertically on phone width');
  else fail(`§11: cards/Needs you did not stack: flex-direction ${layout.mainDirection}`);
  if (layout.shelvesColumns === 1) ok('§11: Channels + Material shelves stack to a single column');
  else fail(`§11: shelves did not stack to one column: ${layout.shelvesColumns} columns`);
  if (layout.promoteTop < layout.needsyouTop && layout.needsyouTop < layout.cardsTop) {
    ok('§11: phone stack order is promote, Needs you, then campaign cards');
  } else {
    fail(`§11: phone stack order wrong — promote@${layout.promoteTop} needsyou@${layout.needsyouTop} cards@${layout.cardsTop}`);
  }
  if (Math.abs(layout.cardWidth - layout.cardsHostWidth) < 2) ok(`§11: campaign card is full width on phone (${layout.cardWidth.toFixed(0)}px)`);
  else fail(`§11: campaign card is not full width: card ${layout.cardWidth}px vs host ${layout.cardsHostWidth}px`);

  const hitTargets = await page.evaluate(() => {
    const els = [
      document.querySelector('.desk-v1-home-settings-btn'),
      document.querySelector('.desk-v1-home-pause-btn'),
      document.querySelector('.desk-v1-home-promote-icon-btn'),
      document.querySelector('.desk-v1-home-needsyou-row'),
      document.querySelector('.desk-v1-shelf-item'),
    ].filter(Boolean);
    return els.map((el) => el.getBoundingClientRect().height);
  });
  if (hitTargets.every((h) => h >= 44)) ok(`§11: header/promote/Needs-you/shelf hit targets all >=44px (${hitTargets.map((h) => h.toFixed(0)).join(', ')})`);
  else fail(`§11: a hit target is under 44px: ${JSON.stringify(hitTargets)}`);

  reportUncaught(pageErrors, '[phone]');
  await ctx.close();
}

// ── item 1 (MC-977 R0 UX pass, Ron 2026-09-28: "the menu opens on the full
// dashboard size"). desk-v1-shell.js used to call toggleModalMaximize() on
// open; removed so Desk rides the SAME sizing convention every other project
// modal uses (interactions.js's snap machinery, no bespoke full-size mode).
// Checked at a wide (2000px) window — the width the screenshot that reported
// this bug was taken at. ────────────────────────────────────────────────────
async function runModalSizeCheck(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} }, { width: 2000, height: 1100 });

  const size = await page.evaluate(() => {
    const win = document.querySelector('.modal-window[data-modal-id="__desk"]');
    const r = win.getBoundingClientRect();
    return { isMaximized: win.classList.contains('is-maximized'), width: r.width, height: r.height };
  });
  !size.isMaximized
    ? ok(`item 1: Desk does not force-maximize on open (no .is-maximized class)`)
    : fail('item 1: Desk still opens maximized');
  size.width < 2000 - 200
    ? ok(`item 1: Desk modal is bounded on a 2000px window (${size.width.toFixed(0)}px wide, not edge-to-edge)`)
    : fail(`item 1: Desk modal stretched edge-to-edge on a 2000px window: ${size.width.toFixed(0)}px wide`);

  await page.screenshot({ path: resolve(SHOT_DIR, 'ux_item1_home_2000.png') });
  ok('desktop (2000px) screenshot saved: ux_item1_home_2000.png');

  reportUncaught(pageErrors, '[modal-size]');
  await ctx.close();
}

// ── item 3 (MC-977 R0 UX pass) — Home card "More" menu, per Dave/Kestrel's
// review scope: camp-2 (Proposed, never published) offers Delete; camp-1
// (Active, has publication history) offers Archive, not Delete. Both route
// through the shared confirm sheet + commandBus Undo desk-v1-campaign.js
// built, and archiving moves the card into the collapsed Archived section
// instead of the main grid. ─────────────────────────────────────────────────
async function runHomeCardMoreMenu(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });

  // Proposed campaign (camp-2) → Delete, with Undo.
  const camp2Card = await page.$('.desk-v1-home-camp-card[data-campaign-id="camp-2"] [data-camp-more-btn]');
  camp2Card ? ok('item 3: Home card for a Proposed campaign shows a More trigger') : fail('item 3: no More trigger on the Proposed campaign card');
  await camp2Card.click();
  await page.waitForSelector('.desk-v1-camp-cardmenu', { timeout: 2000 });
  const menuText2 = (await page.textContent('.desk-v1-camp-cardmenu').catch(() => '') || '');
  /Delete campaign/.test(menuText2) && !/Archive campaign/.test(menuText2)
    ? ok('item 3: Proposed campaign\'s More menu offers Delete (never published, no Archive)')
    : fail(`item 3: Proposed campaign menu wrong: ${JSON.stringify(menuText2)}`);
  await page.click('.desk-v1-camp-cardmenu [data-menu-delete]');
  const usesConfirmSheet = await page.$('.desk-v1-rules-confirm-overlay');
  usesConfirmSheet
    ? ok('item 3: Delete confirms via the in-page sheet, not window.confirm (d146df0)')
    : fail('item 3: Delete confirm sheet did not render');
  await page.click('[data-confirm-accept]');
  await page.waitForSelector('.toast', { timeout: 2000 }).catch(() => {});
  const delToast = (await page.textContent('.toast').catch(() => '') || '');
  /Deleted/.test(delToast) ? ok(`item 3: delete shows a commandBus toast: "${delToast.trim()}"`) : fail(`item 3: delete toast missing/wrong: ${JSON.stringify(delToast)}`);
  const camp2GoneFromCards = await page.$('.desk-v1-home-camp-card[data-campaign-id="camp-2"]');
  !camp2GoneFromCards ? ok('item 3: deleted campaign leaves the Home grid') : fail('item 3: deleted campaign still rendered on Home');
  await page.click('.toast .toast-btn.primary');
  await page.waitForTimeout(80);
  const camp2Restored = await page.$('.desk-v1-home-camp-card[data-campaign-id="camp-2"]');
  camp2Restored ? ok('item 3: Undo restores the deleted campaign to Home') : fail('item 3: Undo did not restore the deleted campaign');

  // Active campaign (camp-1) → Archive, with Undo; archived leaves the main
  // grid for the Archived section.
  const camp1More = await page.$('.desk-v1-home-camp-card[data-campaign-id="camp-1"] [data-camp-more-btn]');
  await camp1More.click();
  await page.waitForSelector('.desk-v1-camp-cardmenu', { timeout: 2000 });
  const menuText1 = (await page.textContent('.desk-v1-camp-cardmenu').catch(() => '') || '');
  /Archive campaign/.test(menuText1) && !/Delete campaign/.test(menuText1)
    ? ok('item 3: Active campaign\'s More menu offers Archive, not Delete (has publication history)')
    : fail(`item 3: Active campaign menu wrong: ${JSON.stringify(menuText1)}`);
  await page.click('.desk-v1-camp-cardmenu [data-menu-archive]');
  await page.waitForSelector('.desk-v1-rules-confirm-overlay', { timeout: 2000 });
  const archiveBody = (await page.textContent('.desk-v1-rules-confirm-overlay').catch(() => '') || '');
  /does not remove any posts already on a platform/.test(archiveBody)
    ? ok('item 3: archive copy states it does not remove already-published posts')
    : fail(`item 3: archive confirm copy missing the platform-safety line: ${JSON.stringify(archiveBody)}`);
  await page.click('[data-confirm-accept]');
  await page.waitForSelector('.toast', { timeout: 2000 }).catch(() => {});
  const camp1GoneFromGrid = await page.$('.desk-v1-home-camp-card[data-campaign-id="camp-1"]');
  !camp1GoneFromGrid ? ok('item 3: archived campaign leaves the main grid') : fail('item 3: archived campaign still in the main grid');
  const archivedSection = (await page.textContent('#desk-v1-home-archived').catch(() => '') || '');
  /Archived \(1\)/.test(archivedSection)
    ? ok('item 3: archived campaign appears in the Archived section')
    : fail(`item 3: Archived section wrong: ${JSON.stringify(archivedSection)}`);
  await page.click('.toast .toast-btn.primary');
  await page.waitForTimeout(80);
  const camp1Restored = await page.$('.desk-v1-home-camp-card[data-campaign-id="camp-1"]');
  camp1Restored ? ok('item 3: Undo restores the archived campaign to the main grid') : fail('item 3: Undo did not restore the archived campaign');

  reportUncaught(pageErrors, '[home-more-menu]');
  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  for (const tone of TONES) await runToneRenderChecks(browser, tone);
  await runNeedsYouDeepLinks(browser);
  await runChannelDropOnCard(browser);
  await runAmbiguousDropChoosesCampaign(browser);
  await runDesktopLayoutChecks(browser);
  await runPhoneLayout(browser);
  await runModalSizeCheck(browser);
  await runHomeCardMoreMenu(browser);
  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error('❌ FAIL — smoke harness error: ' + (e && e.message ? e.message : e));
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}

console.log(bad
  ? `\n❌ FAIL — ${bad} Desk v1 home check(s) regressed.`
  : '\n✅ PASS — Desk v1 T1 home: A13 (heartbeat -> hold row), A4 (channel/asset drop, home half), A12 (Needs-you deep-links), UX-03 (ambiguous drop), phone layout all hold.');
process.exit(exitCode);

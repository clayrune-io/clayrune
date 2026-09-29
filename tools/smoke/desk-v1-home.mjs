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

  // IA1 (Dave's review pass 3): Home's own "at a glance" row is now one
  // card per project, not a flat campaign grid — the grid + Archived section
  // are retired from Home entirely (only reachable via a project's own page,
  // covered by desk-v1-project.mjs). Both fixture projects render here.
  const projectCardCount = await page.$$eval('.desk-v1-home-project-card', (els) => els.length);
  if (projectCardCount === 2) ok(`[${tone.name}] both fixture project cards render`);
  else fail(`[${tone.name}] expected 2 project cards, got ${projectCardCount}`);

  const projectsText = (await page.textContent('#desk-v1-home-projects').catch(() => '') || '');
  if (/Clayrune/.test(projectsText) && /2 campaigns/.test(projectsText) && /1 active/.test(projectsText)) {
    ok(`[${tone.name}] Clayrune project card shows campaign count + active count`);
  } else {
    fail(`[${tone.name}] Clayrune project card content wrong: ${JSON.stringify(projectsText)}`);
  }
  if (/Engulfing scanner/.test(projectsText) && /1 campaign/.test(projectsText)) {
    ok(`[${tone.name}] Engulfing scanner project card shows its own campaign count`);
  } else {
    fail(`[${tone.name}] Engulfing scanner project card content wrong: ${JSON.stringify(projectsText)}`);
  }
  const campCardsGone = await page.$('.desk-v1-home-camp-card');
  !campCardsGone ? ok(`[${tone.name}] no flat campaign-card grid on Home`) : fail(`[${tone.name}] a retired .desk-v1-home-camp-card still rendered`);

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

// ── A4 + UX-03 (Dave's review pass 3): Home no longer has a campaign grid to
// drag/drop onto — _homeShelfAdapter.onDrop always misses — so a shelf item's
// click/keyboard "Add to…" path (UX-05) is now the ONLY way Home attaches a
// channel/asset to a campaign, across every project at once (no "ambiguous
// drop" case left: the picker always lists every campaign, suffixed with its
// project name since the fixture ships two). ───────────────────────────────
async function runAddToMenuAttach(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });

  const before = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-1').channelIds.length);
  const channelItem = await page.$('#desk-v1-home-shelf-channels .desk-v1-shelf-item[data-channel-id="ch-x-ron"]');
  await channelItem.click();
  await page.waitForSelector('.desk-v1-addto-menu', { timeout: 2000 });
  const menuText = (await page.textContent('.desk-v1-addto-menu').catch(() => '') || '');
  if (/Windows beta testers — Clayrune/.test(menuText) && /Restore points launch — Clayrune/.test(menuText)
    && /Signal alerts for day traders — Engulfing scanner/.test(menuText) && /New campaign/.test(menuText)) {
    ok('Add to…: menu lists every campaign across both fixture projects, suffixed with its project name, plus "+ New campaign"');
  } else {
    fail(`Add to… menu wrong: ${JSON.stringify(menuText)}`);
  }
  // ch-x-ron is already on camp-1 (fixture) — exercise the "already on"
  // refusal, the same case the old drag-onto-card test covered.
  await page.click('.desk-v1-addto-menu button:has-text("Windows beta testers")');
  const toastText = (await page.$eval('.toast:last-of-type', (el) => el.textContent).catch(() => '') || '');
  if (/already on/.test(toastText)) ok(`Add to…: attaching a channel already on the campaign is refused: "${toastText.trim()}"`);
  else fail(`Add to…: expected an "already on" refusal toast, got: ${JSON.stringify(toastText)}`);
  const after = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-1').channelIds.length);
  if (after === before) ok('Add to…: refused attach does not mutate channelIds');
  else fail(`Add to…: channelIds mutated on a refused attach: ${before} -> ${after}`);

  // Material asset -> a real campaign DOES add a new family (the literal
  // "adds a version" case per §13/A4), and IS undo-able via the command bus.
  const famCountBefore = await page.evaluate(() => window.DeskV1Fixtures.families.length);
  const assetItem = await page.$('.desk-v1-home-material-assets .desk-v1-shelf-item');
  await assetItem.click();
  await page.waitForSelector('.desk-v1-addto-menu', { timeout: 2000 });
  await page.click('.desk-v1-addto-menu button:has-text("Signal alerts for day traders")');
  const famCountAfter = await page.evaluate(() => window.DeskV1Fixtures.families.length);
  if (famCountAfter === famCountBefore + 1) ok('Add to…: attaching a Material asset to a campaign adds a new family (a drafting version)');
  else fail(`Add to…: family count did not increase: ${famCountBefore} -> ${famCountAfter}`);
  const hasUndo = await page.$eval('.toast:last-of-type', (el) => /toast-btn/.test(el.innerHTML)).catch(() => false);
  if (hasUndo) {
    await page.click('.toast:last-of-type .toast-btn.primary');
    const famCountUndone = await page.evaluate(() => window.DeskV1Fixtures.families.length);
    if (famCountUndone === famCountBefore) ok('Add to…: Undo removes the family the attach added');
    else fail(`Add to…: Undo did not remove the added family: ${famCountBefore} -> ${famCountUndone}`);
  } else {
    const lastToastText = (await page.$eval('.toast:last-of-type', (el) => el.textContent).catch(() => '') || '');
    fail(`Add to…: asset-attach toast had no Undo button: ${JSON.stringify(lastToastText)}`);
  }

  // "+ New campaign" still creates one and navigates straight to it (UX-03's
  // old "ambiguous drop -> + New campaign" case, now reached via the picker).
  const campCountBefore = await page.evaluate(() => window.DeskV1Fixtures.campaigns.length);
  const channelItem2 = await page.$('#desk-v1-home-shelf-channels .desk-v1-shelf-item[data-channel-id="ch-blog"]');
  await channelItem2.click();
  await page.waitForSelector('.desk-v1-addto-menu', { timeout: 2000 });
  await page.click('.desk-v1-addto-menu button:has-text("New campaign")');
  await page.waitForSelector('#desk-v1-crumb .desk-v1-crumb-title:has-text("New campaign")', { timeout: 4000 }).catch(() => {});
  const campCountAfter = await page.evaluate(() => window.DeskV1Fixtures.campaigns.length);
  const crumbAfterNew = (await page.textContent('#desk-v1-crumb .desk-v1-crumb-title').catch(() => '') || '');
  if (campCountAfter === campCountBefore + 1) ok('Add to…: "+ New campaign" creates a campaign');
  else fail(`Add to…: "+ New campaign" did not create a campaign: ${campCountBefore} -> ${campCountAfter}`);
  if (/New campaign/.test(crumbAfterNew)) ok(`Add to…: "+ New campaign" navigates straight to the new campaign: crumb "${crumbAfterNew.trim()}"`);
  else fail(`Add to…: did not land on the new campaign: crumb ${JSON.stringify(crumbAfterNew)}`);

  reportUncaught(pageErrors, '[addto]');
  await ctx.close();
}

// ── Desktop layout (Dave's review): with the campaign grid retired, Needs
// you is the only content row left in the .desk-v1-home column — it must
// still size to its own content (flex:0 0 auto), not stretch to fill
// whatever height the column has free, and a channel shelf item renders as
// ONE pill (grip + badge + add inside a single outline), not a badge-pill
// nested inside the shelf-item's own pill. ─────────────────────────────────
async function runDesktopLayoutChecks(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });

  const needsyouFit = await page.evaluate(() => {
    const needsyou = document.querySelector('.desk-v1-home-needsyou');
    const home = document.querySelector('.desk-v1-home');
    return { needsyouHeight: needsyou.getBoundingClientRect().height, homeHeight: home.getBoundingClientRect().height };
  });
  if (needsyouFit.needsyouHeight < needsyouFit.homeHeight - 40) {
    ok(`Needs you sizes to its own content, not stretched to fill the Home column (${needsyouFit.needsyouHeight.toFixed(0)}px vs ${needsyouFit.homeHeight.toFixed(0)}px column)`);
  } else {
    fail(`Needs you stretched to fill the Home column: ${needsyouFit.needsyouHeight.toFixed(0)}px vs ${needsyouFit.homeHeight.toFixed(0)}px`);
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

// ── Phone (§11): promote box shows the icon row (no drag), Home's content
// rows stack (they always do now — the campaign grid's rail layout that used
// to need a desktop/phone split is retired), hit targets >=44px. ───────────
async function runPhoneLayout(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} }, { width: 390, height: 844 });

  const layout = await page.evaluate(() => {
    const phoneActions = document.querySelector('.desk-v1-home-promote-phone-actions');
    const shelves = document.querySelector('.desk-v1-home-shelves');
    const promote = document.querySelector('.desk-v1-home-promote');
    const projects = document.querySelector('.desk-v1-home-projects');
    const needsyou = document.querySelector('.desk-v1-home-needsyou');
    return {
      phoneActionsDisplay: getComputedStyle(phoneActions).display,
      shelvesColumns: getComputedStyle(shelves).gridTemplateColumns.split(' ').length,
      promoteTop: promote.getBoundingClientRect().top,
      projectsTop: projects.getBoundingClientRect().top,
      needsyouTop: needsyou.getBoundingClientRect().top,
      needsyouWidth: needsyou.getBoundingClientRect().width,
      homeWidth: document.querySelector('.desk-v1-home').getBoundingClientRect().width,
    };
  });
  if (layout.phoneActionsDisplay !== 'none') ok('§11: promote box icon row (📎 🔗 🎙) visible at phone width');
  else fail('§11: promote box icon row hidden at phone width');
  if (layout.shelvesColumns === 1) ok('§11: Channels + Material shelves stack to a single column');
  else fail(`§11: shelves did not stack to one column: ${layout.shelvesColumns} columns`);
  if (layout.promoteTop < layout.projectsTop && layout.projectsTop < layout.needsyouTop) {
    ok('§11: phone stack order is promote, project cards, then Needs you');
  } else {
    fail(`§11: phone stack order wrong — promote@${layout.promoteTop} projects@${layout.projectsTop} needsyou@${layout.needsyouTop}`);
  }
  if (Math.abs(layout.needsyouWidth - layout.homeWidth) < 2) ok(`§11: Needs you is full width on phone, no fixed rail (${layout.needsyouWidth.toFixed(0)}px)`);
  else fail(`§11: Needs you is not full width: ${layout.needsyouWidth}px vs ${layout.homeWidth}px column`);

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

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  for (const tone of TONES) await runToneRenderChecks(browser, tone);
  await runNeedsYouDeepLinks(browser);
  await runAddToMenuAttach(browser);
  await runDesktopLayoutChecks(browser);
  await runPhoneLayout(browser);
  await runModalSizeCheck(browser);
  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error('❌ FAIL — smoke harness error: ' + (e && e.message ? e.message : e));
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}

console.log(bad
  ? `\n❌ FAIL — ${bad} Desk v1 home check(s) regressed.`
  : '\n✅ PASS — Desk v1 T1 home: A13 (heartbeat -> hold row), A4/UX-03 (Add to… menu attach), A12 (Needs-you deep-links), phone layout all hold.');
process.exit(exitCode);

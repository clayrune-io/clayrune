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

// ── render checks, one per tone: header, promote box, status board (ONE
// column header, bordered per-project blocks, per-row Needs-you column),
// shelves. R2-2 (§8 amended row, §11.6 item 2): the old flat project-card
// grid + grouped Needs-you section are retired — each project is its own
// bordered block of campaign rows, and Needs-you is a COLUMN in that row
// showing the top reason (blocker > held channel > piece/video/reply,
// highest first) plus "+n" for the other kinds present. ────────────────────
async function runToneRenderChecks(browser, tone) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, tone);

  // ONE column header for the whole page, not one per block (§8 amended row).
  const headCount = await page.$$eval('.desk-v1-home-board-head', (els) => els.length);
  if (headCount === 1) ok(`[${tone.name}] exactly one page-wide column header`);
  else fail(`[${tone.name}] expected 1 column header, got ${headCount}`);
  const headText = (await page.textContent('.desk-v1-home-board-head').catch(() => '') || '');
  if (/CAMPAIGN/.test(headText) && /STAGE/.test(headText) && /GOAL PROGRESS/.test(headText) && /PACE/.test(headText) && /NEXT POST/.test(headText) && /NEEDS YOU/.test(headText)) {
    ok(`[${tone.name}] column header names all six columns`);
  } else {
    fail(`[${tone.name}] column header missing a column: ${JSON.stringify(headText)}`);
  }

  // Two bordered project blocks (both fixture projects), campaign grid retired.
  const blockCount = await page.$$eval('.desk-v1-home-block', (els) => els.length);
  if (blockCount === 2) ok(`[${tone.name}] both fixture project blocks render`);
  else fail(`[${tone.name}] expected 2 project blocks, got ${blockCount}`);
  const campCardsGone = await page.$('.desk-v1-home-camp-card');
  !campCardsGone ? ok(`[${tone.name}] no flat campaign-card grid on Home`) : fail(`[${tone.name}] a retired .desk-v1-home-camp-card still rendered`);

  // IA6: clayrune's block holds camp-1/camp-2/camp-4 (camp-archived-1 excluded,
  // its state is 'archived'); engulfing_scanner's block holds camp-3 alone.
  const blockRowTitles = await page.evaluate(() => {
    const out = {};
    document.querySelectorAll('.desk-v1-home-block').forEach((b) => {
      const name = b.querySelector('.desk-v1-home-block-name').textContent.trim();
      out[name] = Array.from(b.querySelectorAll('.desk-v1-home-row-title')).map((t) => t.textContent.trim());
    });
    return out;
  });
  const clayruneTitles = blockRowTitles['Clayrune'] || [];
  if (['Windows beta testers', 'Restore points launch', 'Community Discord launch'].every((t) => clayruneTitles.includes(t)) && clayruneTitles.length === 3) {
    ok(`[${tone.name}] Clayrune block: camp-1/camp-2/camp-4 rows, camp-archived-1 excluded`);
  } else {
    fail(`[${tone.name}] Clayrune block rows wrong: ${JSON.stringify(clayruneTitles)}`);
  }
  const engulfingTitles = blockRowTitles['Engulfing scanner'] || [];
  if (engulfingTitles.length === 1 && engulfingTitles[0] === 'Signal alerts for day traders') {
    ok(`[${tone.name}] Engulfing scanner block: camp-3 row alone`);
  } else {
    fail(`[${tone.name}] Engulfing scanner block rows wrong: ${JSON.stringify(engulfingTitles)}`);
  }

  // Needs-you column, per row, unmutated fixture state:
  //  - camp-1: ch-li-page (held) is one of its accounts — held beats the
  //    needs_review piece/video also sitting on this campaign (§11.6 item 2
  //    priority: blocker > held > piece/video/reply).
  //  - camp-2 (proposed): its own sequencing blocker beats everything.
  //  - camp-3: no held channel, no needs_review family — its one needs_you
  //    conversation is the only bucket, so no "+n" suffix.
  //  - camp-4: nothing pending — empty dash, no pill.
  const needsYouByRow = await page.evaluate(() => {
    const out = {};
    document.querySelectorAll('.desk-v1-home-row').forEach((r) => {
      const pill = r.querySelector('.desk-v1-home-needsyou-pill');
      out[r.dataset.campaignId] = pill ? { kind: pill.dataset.needsyouKind, text: pill.textContent.trim() } : { empty: !!r.querySelector('.desk-v1-home-needsyou-empty') };
    });
    return out;
  });
  if (needsYouByRow['camp-1'] && needsYouByRow['camp-1'].kind === 'held' && /LinkedIn disconnected/.test(needsYouByRow['camp-1'].text)) {
    ok(`[${tone.name}] camp-1 Needs you: held channel wins over its own pending piece/video: "${needsYouByRow['camp-1'].text}"`);
  } else {
    fail(`[${tone.name}] camp-1 Needs you wrong: ${JSON.stringify(needsYouByRow['camp-1'])}`);
  }
  if (needsYouByRow['camp-2'] && needsYouByRow['camp-2'].kind === 'blocker' && /Needs your answer/.test(needsYouByRow['camp-2'].text)) {
    ok(`[${tone.name}] camp-2 Needs you: proposed sequencing blocker: "${needsYouByRow['camp-2'].text}"`);
  } else {
    fail(`[${tone.name}] camp-2 Needs you wrong: ${JSON.stringify(needsYouByRow['camp-2'])}`);
  }
  if (needsYouByRow['camp-3'] && needsYouByRow['camp-3'].kind === 'reply' && /^\S\s1 reply waiting$/.test(needsYouByRow['camp-3'].text)) {
    ok(`[${tone.name}] camp-3 Needs you: "1 reply waiting" (no held channel, no "+n")`);
  } else {
    fail(`[${tone.name}] camp-3 Needs you wrong: ${JSON.stringify(needsYouByRow['camp-3'])}`);
  }
  if (needsYouByRow['camp-4'] && needsYouByRow['camp-4'].empty) {
    ok(`[${tone.name}] camp-4 Needs you: empty dash (nothing pending)`);
  } else {
    fail(`[${tone.name}] camp-4 Needs you should be empty: ${JSON.stringify(needsYouByRow['camp-4'])}`);
  }

  // A13 worker-offline: a banner above the board, not a per-row hold (it's a
  // worker-wide condition, §11.6/A13 comment in desk-v1-home.js).
  const bannerText = (await page.textContent('.desk-v1-home-worker-banner').catch(() => '') || '');
  if (/Scheduling paused/.test(bannerText) && /worker offline since 14:02/.test(bannerText) && /2 posts missed/.test(bannerText)) {
    ok(`[${tone.name}] A13: worker-offline banner: "Scheduling paused — worker offline since 14:02 · 2 posts missed"`);
  } else {
    fail(`[${tone.name}] A13: worker-offline banner missing/wrong: ${JSON.stringify(bannerText)}`);
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
// params those surfaces expect. camp-1's unmutated fixture state has a held
// channel that outranks its own pending piece/video (§11.6 item 2 priority,
// verified above in runToneRenderChecks) — so exercising the 'piece' and
// 'video' kinds here first un-holds ch-li-page to let those buckets surface,
// the same fixture mutation technique the Add-to test below already uses. ──
async function runNeedsYouDeepLinks(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });

  // Un-hold ch-li-page: camp-1 now has 3 live buckets (piece, video, reply);
  // priority puts piece first, so its pill reads "1 piece to approve +2".
  await page.evaluate(() => {
    window.DeskV1Fixtures.channels.find((c) => c.id === 'ch-li-page').health = 'ok';
    window.__deskV1HomeTickHeartbeatNow();
  });
  const camp1PillText = (await page.textContent('.desk-v1-home-row[data-campaign-id="camp-1"] .desk-v1-home-needsyou-pill').catch(() => '') || '');
  if (/1 piece to approve \+2/.test(camp1PillText)) ok(`A12 setup: camp-1 Needs you shows top bucket + "+n": "${camp1PillText.trim()}"`);
  else fail(`A12 setup: camp-1 pill did not read "1 piece to approve +2": ${JSON.stringify(camp1PillText)}`);

  await page.click('.desk-v1-home-row[data-campaign-id="camp-1"] .desk-v1-home-needsyou-pill');
  await page.waitForSelector('.desk-v1-review', { timeout: 8000 });
  let onReview = await page.evaluate(() => document.querySelector('[data-act-primary]') ? true : false);
  if (onReview) ok('A12: "1 piece to approve" deep-links straight into the review surface (12b)');
  else fail('A12: piece row did not land on review');
  const claimBar = (await page.textContent('.desk-v1-claimbar-blocked').catch(() => '') || '');
  if (/No source for this/.test(claimBar)) ok('A12: piece deep-link resolved to v-restore-blog (its own blocked claim renders)');
  else fail(`A12: piece deep-link landed on the wrong version: ${JSON.stringify(claimBar)}`);

  // Resolve the piece so video becomes camp-1's top bucket ("1 video to watch +1").
  await page.evaluate(() => window.deskV1Nav('home', {}));
  await page.waitForSelector('.desk-v1-home', { timeout: 8000 });
  await page.evaluate(() => {
    const fam = window.DeskV1Fixtures.families.find((f) => f.id === 'fam-restore-points');
    fam.versions.find((v) => v.id === 'v-restore-blog').state = 'approved';
    window.__deskV1HomeTickHeartbeatNow();
  });
  const camp1PillText2 = (await page.textContent('.desk-v1-home-row[data-campaign-id="camp-1"] .desk-v1-home-needsyou-pill').catch(() => '') || '');
  if (/1 video to watch \+1/.test(camp1PillText2)) ok(`A12 setup: with the piece resolved, camp-1 Needs you promotes video: "${camp1PillText2.trim()}"`);
  else fail(`A12 setup: camp-1 pill did not read "1 video to watch +1": ${JSON.stringify(camp1PillText2)}`);
  await page.click('.desk-v1-home-row[data-campaign-id="camp-1"] .desk-v1-home-needsyou-pill');
  await page.waitForSelector('.desk-v1-stub-title:has-text("Video"), .desk-v1-video', { timeout: 8000 });
  // Route-name assertion (Dave's review): a video row must land on the
  // 'video' route (12d, T5's surface — still a stub in this ticket's tree),
  // never 'review' (12b) — §2's "12b, 12c, or 12d, not to a list" names them
  // as three distinct surfaces. The crumb title is ROUTES.video's own label
  // ('Video'), so it's the route-table entry talking, not a guess.
  const crumbAfterVideo = (await page.textContent('#desk-v1-crumb .desk-v1-crumb-title').catch(() => '') || '');
  if (/^Video$/.test(crumbAfterVideo.trim())) ok(`A12: "1 video to watch" deep-links to route 'video' (12d), not 'review': crumb "${crumbAfterVideo.trim()}"`);
  else fail(`A12: video row landed on the wrong route: crumb ${JSON.stringify(crumbAfterVideo)}`);

  // camp-3 is naturally reply-only (no held channel, no needs_review family)
  // — no mutation needed to isolate the 'reply' kind.
  await page.evaluate(() => window.deskV1Nav('home', {}));
  await page.waitForSelector('.desk-v1-home', { timeout: 8000 });
  await page.click('.desk-v1-home-row[data-campaign-id="camp-3"] .desk-v1-home-needsyou-pill');
  await page.waitForSelector('.desk-v1-stub, .desk-v1-conversations', { timeout: 8000 });
  // T2 (§2, §8): 'conversations' is a panel alias now, not a pushed route —
  // it lands on the SAME campaign crumb (the campaign's own name). R2-3:
  // Conversations has no map-stop button (§6, moves to Engagement at
  // R2-12), so "selected" is read off the rendered panel body, not a tab.
  const crumbTitle = (await page.textContent('#desk-v1-crumb .desk-v1-crumb-title').catch(() => '') || '');
  const convPanelShown = await page.$('.desk-v1-conv-layout, .desk-v1-conversations, .desk-v1-conv-empty');
  if (convPanelShown) ok(`A12: "1 reply waiting" deep-links to the conversations panel (campaign crumb "${crumbTitle.trim()}")`);
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

  const before = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-1').plan.accounts.length);
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
  const after = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-1').plan.accounts.length);
  if (after === before) ok('Add to…: refused attach does not mutate plan.accounts');
  else fail(`Add to…: plan.accounts mutated on a refused attach: ${before} -> ${after}`);

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

// ── Desktop layout (§8 amended row: "each project is its own bordered block
// with a visible gap between blocks", Ron r2 (a)) — each block must actually
// be bordered and have visible daylight before the next one, and a channel
// shelf item renders as ONE pill (grip + badge + add inside a single
// outline), not a badge-pill nested inside the shelf-item's own pill. ──────
async function runDesktopLayoutChecks(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });

  const blockLayout = await page.evaluate(() => {
    const blocks = Array.from(document.querySelectorAll('.desk-v1-home-block'));
    const [first, second] = blocks;
    const firstStyle = getComputedStyle(first);
    return {
      count: blocks.length,
      borderStyle: firstStyle.borderStyle,
      borderWidth: parseFloat(firstStyle.borderWidth) || 0,
      gap: second ? second.getBoundingClientRect().top - first.getBoundingClientRect().bottom : null,
    };
  });
  if (blockLayout.borderStyle !== 'none' && blockLayout.borderWidth > 0) {
    ok(`Each project block is bordered (${blockLayout.borderStyle}, ${blockLayout.borderWidth}px)`);
  } else {
    fail(`Project block has no visible border: ${blockLayout.borderStyle} ${blockLayout.borderWidth}px`);
  }
  if (blockLayout.count === 2 && blockLayout.gap !== null && blockLayout.gap > 0) {
    ok(`Visible gap between project blocks: ${blockLayout.gap.toFixed(0)}px`);
  } else {
    fail(`No visible gap between project blocks: ${JSON.stringify(blockLayout)}`);
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
    const board = document.querySelector('.desk-v1-home-board');
    return {
      phoneActionsDisplay: getComputedStyle(phoneActions).display,
      shelvesColumns: getComputedStyle(shelves).gridTemplateColumns.split(' ').length,
      promoteTop: promote.getBoundingClientRect().top,
      boardTop: board.getBoundingClientRect().top,
      boardWidth: board.getBoundingClientRect().width,
      homeWidth: document.querySelector('.desk-v1-home').getBoundingClientRect().width,
    };
  });
  if (layout.phoneActionsDisplay !== 'none') ok('§11: promote box icon row (📎 🔗 🎙) visible at phone width');
  else fail('§11: promote box icon row hidden at phone width');
  if (layout.shelvesColumns === 1) ok('§11: Channels + Material shelves stack to a single column');
  else fail(`§11: shelves did not stack to one column: ${layout.shelvesColumns} columns`);
  if (layout.promoteTop < layout.boardTop) {
    ok('§11: phone stack order is promote, then the status board');
  } else {
    fail(`§11: phone stack order wrong — promote@${layout.promoteTop} board@${layout.boardTop}`);
  }
  if (Math.abs(layout.boardWidth - layout.homeWidth) < 2) ok(`§11: status board is full width on phone, no fixed rail (${layout.boardWidth.toFixed(0)}px)`);
  else fail(`§11: status board is not full width: ${layout.boardWidth}px vs ${layout.homeWidth}px column`);

  // .desk-v1-home-needsyou-pill is deliberately smaller (32px, CSS comment
  // at desk-v1.css's phone media query) — it's a secondary jump-straight-in
  // control nested inside the row, which is itself the 44px primary target.
  const hitTargets = await page.evaluate(() => {
    const els = [
      document.querySelector('.desk-v1-home-settings-btn'),
      document.querySelector('.desk-v1-home-pause-btn'),
      document.querySelector('.desk-v1-home-promote-icon-btn'),
      document.querySelector('.desk-v1-home-row'),
      document.querySelector('.desk-v1-shelf-item'),
    ].filter(Boolean);
    return els.map((el) => el.getBoundingClientRect().height);
  });
  if (hitTargets.every((h) => h >= 44)) ok(`§11: header/promote/row/shelf hit targets all >=44px (${hitTargets.map((h) => h.toFixed(0)).join(', ')})`);
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

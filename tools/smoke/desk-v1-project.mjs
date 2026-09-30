#!/usr/bin/env node
/**
 * Desk v1 (MC-977 IA revision, docs/THE_DESK_V1_IA_REVISION.md §5 row IA1)
 * — Project page smoke.
 *
 * Closes IA1's acceptance: Home shows project cards; Home -> project card
 * -> campaign -> Back reads '‹ Clayrune' then '‹ Desk'; a Needs-you deep
 * link into a review builds the 5-deep stack (home, project, campaign,
 * piece, review); the presence/engagement/piece stub routes render an
 * honest placeholder instead of a blank pane.
 *
 * Real headless boot (real index.html + real static/js|css, no network), same
 * hermetic shape as desk-v1-campaign.mjs / desk-v1-home.mjs.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-project.mjs
 * Exit 0 = every case holds; 1 = a case regressed / harness error.
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

const MIME = { '.webp': 'image/webp', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml' };

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];
for (const f of readdirSync(ASSETS_DIR)) {
  const ext = f.slice(f.lastIndexOf('.'));
  if (MIME[ext]) STATIC[`/assets/${f}`] = [MIME[ext], readFileSync(resolve(ASSETS_DIR, f))];
}

const PID = 'smoke_deskv1project';
const PROJECTS = [{
  id: PID, name: 'Desk v1 project smoke', status: 'active', domain: 'general', emoji: '🧪',
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

async function newBootedPage(browser, viewport) {
  const ctx = await browser.newContext({ viewport: viewport || { width: 1440, height: 950 } });
  const page = await ctx.newPage();
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
  if (uncaught.length) uncaught.forEach((e) => fail(`${tag} uncaught page error: ${e}`));
}

async function crumb(page) {
  return {
    title: (await page.textContent('.desk-v1-crumb-title').catch(() => '') || '').trim(),
    back: (await page.textContent('.desk-v1-back').catch(() => null) || '').trim() || null,
  };
}

// ── Home shows project cards (§5 row IA1: "Home shows project cards"). ─────
async function runHomeProjectCards(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  const cards = await page.$$eval('#desk-v1-home-board .desk-v1-home-block', (els) =>
    els.map((e) => ({
      id: e.dataset.projectId,
      name: e.querySelector('.desk-v1-home-block-name').textContent.trim(),
    })));
  cards.length === 2 && cards.some((c) => c.id === 'clayrune') && cards.some((c) => c.id === 'engulfing_scanner')
    ? ok(`Home shows both fixture projects as blocks: ${JSON.stringify(cards)}`)
    : fail(`Home project cards wrong: ${JSON.stringify(cards)}`);

  reportUncaught(pageErrors, '[home-projects]');
  await ctx.close();
}

// ── Home -> project card -> campaign -> Back reads '‹ Clayrune' then
// '‹ Desk' (§5 row IA1's own acceptance wording, verbatim). ────────────────
async function runProjectToCampaignAndBack(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  await page.click('.desk-v1-home-block-name[data-project-id="clayrune"]');
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });
  let c = await crumb(page);
  c.title === 'Clayrune' && c.back === '‹ Desk'
    ? ok(`Home -> project: crumb reads "Clayrune", back reads "${c.back}"`)
    : fail(`project crumb wrong: ${JSON.stringify(c)}`);

  // camp-1 (audience subject) and camp-2 (feature subject) both belong to
  // clayrune and are not archived — both render as cards with the right
  // subject glyph (§1's "◉ project · ▣ product · ✦ feature · ◎ audience").
  const camps = await page.$$eval('#desk-v1-project-camps .desk-v1-project-camp-card', (els) =>
    els.map((e) => ({
      id: e.dataset.campaignId,
      name: e.querySelector('.desk-v1-project-camp-name').textContent.trim(),
      glyph: e.querySelector('.desk-v1-project-camp-subject').textContent.trim(),
      subjectLabel: (e.querySelector('.desk-v1-project-camp-subject-label') || {}).textContent || '',
    })));
  const camp1 = camps.find((c2) => c2.id === 'camp-1');
  const camp2 = camps.find((c2) => c2.id === 'camp-2');
  camp1 && camp1.glyph === '◎' && /Windows users trying Claude Code/.test(camp1.subjectLabel)
    ? ok(`camp-1 card shows the audience glyph + subject label: ${JSON.stringify(camp1)}`)
    : fail(`camp-1 card wrong: ${JSON.stringify(camp1)}`);
  camp2 && camp2.glyph === '✦' && /Restore points/.test(camp2.subjectLabel)
    ? ok(`camp-2 card shows the feature glyph + subject label: ${JSON.stringify(camp2)}`)
    : fail(`camp-2 card wrong: ${JSON.stringify(camp2)}`);

  await page.click('.desk-v1-project-camp-card[data-campaign-id="camp-1"]');
  await page.waitForSelector('.desk-v1-campaign', { timeout: 4000 });
  c = await crumb(page);
  c.title === 'Windows beta testers' && c.back === '‹ Clayrune'
    ? ok(`project -> campaign: crumb reads "Windows beta testers", back reads "${c.back}"`)
    : fail(`campaign crumb wrong: ${JSON.stringify(c)}`);

  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });
  c = await crumb(page);
  c.title === 'Clayrune' && c.back === '‹ Desk'
    ? ok(`campaign -> Back: lands on project, crumb "Clayrune", back reads "${c.back}" (the ticket's own worked example)`)
    : fail(`back-to-project crumb wrong: ${JSON.stringify(c)}`);

  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-home', { timeout: 4000 });
  ok('project -> Back: lands on Home');

  reportUncaught(pageErrors, '[project-campaign-back]');
  await ctx.close();
}

// ── engulfing_scanner: 1 Active campaign + 1 Needs-you item (§5 row IA1's
// own fixture ask), scoped separately from clayrune's. ─────────────────────
async function runSecondProject(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  await page.click('.desk-v1-home-block-name[data-project-id="engulfing_scanner"]');
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });
  const c = await crumb(page);
  c.title === 'Engulfing scanner'
    ? ok('Home -> engulfing_scanner project: crumb reads "Engulfing scanner"')
    : fail(`engulfing_scanner crumb wrong: ${JSON.stringify(c)}`);

  const camps = await page.$$eval('#desk-v1-project-camps .desk-v1-project-camp-card', (els) =>
    els.map((e) => e.querySelector('.desk-v1-project-camp-name').textContent.trim()));
  camps.length === 1 && camps[0] === 'Signal alerts for day traders'
    ? ok(`engulfing_scanner shows its one Active campaign: ${JSON.stringify(camps)}`)
    : fail(`engulfing_scanner campaign list wrong: ${JSON.stringify(camps)}`);

  const needsYouText = (await page.textContent('#desk-v1-project-needsyou').catch(() => '') || '');
  /futures/.test(needsYouText)
    ? ok('engulfing_scanner\'s own Needs-you list shows conv-7 (not clayrune\'s items)')
    : fail(`engulfing_scanner Needs-you wrong: ${JSON.stringify(needsYouText)}`);

  reportUncaught(pageErrors, '[second-project]');
  await ctx.close();
}

// ── A Needs-you deep link into a review builds the 5-deep stack (§5 row
// IA1's own acceptance wording): home, project, campaign, piece, review —
// verified by walking Back up through every label in turn. ────────────────
async function runNeedsYouDeepStack(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  // camp-1's unmutated fixture state has a held channel (ch-li-page) that
  // outranks its pending piece (§11.6 item 2 priority: blocker > held >
  // piece/video/reply) — un-hold it first so the piece bucket surfaces,
  // same technique desk-v1-home.mjs's A12 deep-link test already uses.
  await page.evaluate(() => {
    window.DeskV1Fixtures.channels.find((c) => c.id === 'ch-li-page').health = 'ok';
    window.__deskV1HomeTickHeartbeatNow();
  });
  await page.click('.desk-v1-home-row[data-campaign-id="camp-1"] .desk-v1-home-needsyou-pill');
  await page.waitForSelector('.desk-v1-review', { timeout: 4000 });
  let c = await crumb(page);
  c.back === '‹ Undo anything: restore points in Clayrune 2.1'
    ? ok(`review's Back label is the piece entry's title: "${c.back}"`)
    : fail(`review Back label wrong (piece not on the stack?): ${JSON.stringify(c)}`);

  await page.click('.desk-v1-back');
  await page.waitForTimeout(50);
  c = await crumb(page);
  c.back === '‹ Windows beta testers'
    ? ok(`piece's Back label is the campaign entry's title: "${c.back}"`)
    : fail(`piece Back label wrong: ${JSON.stringify(c)}`);

  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-campaign', { timeout: 4000 });
  c = await crumb(page);
  c.title === 'Windows beta testers' && c.back === '‹ Clayrune'
    ? ok(`campaign's Back label is the project entry's title: "${c.back}"`)
    : fail(`campaign Back label wrong: ${JSON.stringify(c)}`);

  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });
  c = await crumb(page);
  c.title === 'Clayrune' && c.back === '‹ Desk'
    ? ok(`project's Back label is Home ("Desk"): "${c.back}"`)
    : fail(`project Back label wrong: ${JSON.stringify(c)}`);

  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-home', { timeout: 4000 });
  const backGone = await page.$('.desk-v1-back');
  !backGone
    ? ok('5-deep stack (home, project, campaign, piece, review) exhausted in exactly 4 Backs, landing on root Home with no Back button')
    : fail('Home unexpectedly still shows a Back button — stack deeper than 5');

  reportUncaught(pageErrors, '[needsyou-deep-stack]');
  await ctx.close();
}

// ── presence/engagement/piece routes reached directly rather than via the
// review chain above (§5 row IA1: "presence (stub), engagement (stub), piece
// (stub)"). Presence (IA3) and piece (IA5) are real pages now; engagement
// remains an honest placeholder, never a blank pane. ──────────────────────
async function runStubRoutes(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  await page.click('.desk-v1-home-block-name[data-project-id="clayrune"]');
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });

  // IA3 landed the real Presence page — no longer a stub route (was
  // asserted as one in IA1; re-check on master before calling this a
  // regression if it ever fails).
  await page.click('.desk-v1-project-presence-btn');
  await page.waitForSelector('.desk-v1-presence', { timeout: 4000 });
  const accountCount = await page.$$eval('.desk-v1-presence-account-row', (els) => els.length);
  accountCount === 3
    ? ok(`presence route: real page renders Clayrune's 3 bound accounts`)
    : fail(`presence route wrong account count: ${accountCount}`);
  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });

  // IA7 landed the real Engagement dashboard — no longer a stub route (was
  // asserted as one in IA1; re-check on master before calling this a
  // regression if it ever fails). Full lane/filter behavior belongs to
  // desk-v1-engagement.mjs; this just confirms the stub is gone.
  await page.evaluate(() => window.deskV1Nav('engagement', {}));
  await page.waitForSelector('.desk-v1-engagement', { timeout: 4000 });
  const stubGone = await page.$('.desk-v1-stub');
  !stubGone
    ? ok('engagement route: real dashboard renders, no longer the IA1 stub')
    : fail('engagement route still shows the IA1 stub placeholder');

  // IA5 landed the real piece page — no longer a stub route (was asserted as
  // one in IA1/IA3; re-check on master before calling this a regression if
  // it ever fails). Reached directly (not via deskV1HomeGotoReview's chain
  // or a campaign card, which both carry familyId) with ONLY a versionId —
  // the IA1-era deep-link shape Home's deskV1HomeGotoReview still uses —
  // so this exercises _pieceLabel's/desk-v1-piece.js's own versionId
  // fallback resolution, not just the familyId-first common path.
  await page.evaluate(() => window.deskV1Nav('piece', { versionId: 'v-restore-blog' }));
  await page.waitForSelector('.desk-v1-piece', { timeout: 4000 });
  const pieceTitle = (await page.textContent('.desk-v1-piece-title').catch(() => '') || '').trim();
  pieceTitle === 'Undo anything: restore points in Clayrune 2.1'
    ? ok(`piece route: real page renders, titled from the fixture family via versionId fallback — "${pieceTitle}"`)
    : fail(`piece route wrong: ${JSON.stringify(pieceTitle)}`);

  reportUncaught(pageErrors, '[stub-routes]');
  await ctx.close();
}

// ── IA3 acceptance row: lowering a Presence ceiling narrows at once (no
// confirm sheet — narrowing needs none) and clamps camp-1's effective-cadence
// chip on the campaign page, with a log line left on the Presence page
// itself (§2.1: "narrowing... clamps that campaign at once and logs it"). ──
async function runPresenceCeilingClamp(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  await page.click('.desk-v1-home-block-name[data-project-id="clayrune"]');
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });
  await page.click('.desk-v1-project-presence-btn');
  await page.waitForSelector('.desk-v1-presence', { timeout: 4000 });

  // Leave the input focused after fill() and the ceiling still fires a
  // real blur-driven 'change' when the Apply click below steals focus —
  // re-running the handler and replacing the preview mid-click. Tab off
  // first so the one real 'change' happens here, not during the click.
  const ceilInput = page.locator('[data-ceiling-input="ch-x-ron"]');
  await ceilInput.fill('2');
  await ceilInput.press('Tab');
  await page.waitForSelector('[data-ceiling-preview="ch-x-ron"] [data-preview-apply]', { timeout: 2000 });
  await page.click('[data-ceiling-preview="ch-x-ron"] [data-preview-apply]');
  await page.waitForTimeout(50);

  const logLine = (await page.textContent('#desk-v1-presence-log-list').catch(() => '') || '');
  /lowered to 2\/wk — was 3/.test(logLine)
    ? ok(`presence: narrowing ch-x-ron to 2/wk applies at once and logs it: "${logLine.trim().slice(0, 80)}"`)
    : fail(`presence ceiling-narrow log wrong: ${JSON.stringify(logLine)}`);

  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.waitForSelector('.desk-v1-campaign', { timeout: 4000 });
  const chipsText = (await page.textContent('.desk-v1-camp-summary').catch(() => '') || '');
  /≤2\/wk/.test(chipsText)
    ? ok('presence: camp-1 chip clamps to "≤2/wk" from the lowered project ceiling')
    : fail(`camp-1 chip not clamped: ${JSON.stringify(chipsText)}`);

  reportUncaught(pageErrors, '[presence-ceiling-clamp]');
  await ctx.close();
}

// ── IA3 acceptance row: adding a Presence account widens what the project
// can publish, so it requires the same confirm-sheet pattern as the Rules
// popover's widening path (desk-v1-kit.js's openConfirmSheet). ─────────────
async function runPresenceAddAccountConfirm(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  await page.click('.desk-v1-home-block-name[data-project-id="engulfing_scanner"]');
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });
  await page.click('.desk-v1-project-presence-btn');
  await page.waitForSelector('.desk-v1-presence', { timeout: 4000 });

  const before = await page.$$eval('.desk-v1-presence-account-row', (els) => els.length);
  await page.click('[data-addacct-trigger]');
  await page.waitForSelector('.desk-v1-addto-menu button', { timeout: 2000 });
  await page.click('.desk-v1-addto-menu button');

  await page.waitForSelector('.desk-v1-rules-confirm-overlay', { timeout: 2000 });
  const sheetTitle = (await page.textContent('.desk-v1-rules-confirm-overlay').catch(() => '') || '');
  /widens what/.test(sheetTitle)
    ? ok('presence: adding an account opens the widening confirm sheet')
    : fail(`add-account confirm sheet wrong: ${JSON.stringify(sheetTitle)}`);

  await page.click('[data-confirm-accept]');
  await page.waitForSelector('.desk-v1-rules-confirm-overlay', { state: 'detached', timeout: 2000 });
  const after = await page.$$eval('.desk-v1-presence-account-row', (els) => els.length);
  after === before + 1
    ? ok(`presence: confirming the sheet actually adds the account (${before} -> ${after})`)
    : fail(`add-account count wrong after confirm: ${before} -> ${after}`);

  reportUncaught(pageErrors, '[presence-addaccount-confirm]');
  await ctx.close();
}

// ── IA3 acceptance row: project Pause pauses both clayrune campaigns;
// Resume runs each through validatePlan (not a blind flip back). ──────────
async function runProjectPauseResume(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  await page.click('.desk-v1-home-block-name[data-project-id="clayrune"]');
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });

  await page.click('[data-pause-project-btn]');
  await page.waitForTimeout(50);
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.waitForSelector('.desk-v1-campaign', { timeout: 4000 });
  let pillText = (await page.textContent('.desk-v1-camp-state-pill').catch(() => '') || '');
  /Paused/i.test(pillText)
    ? ok('project Pause: camp-1 (was active) shows Paused')
    : fail(`camp-1 not paused: ${JSON.stringify(pillText)}`);

  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-2' }));
  await page.waitForSelector('.desk-v1-campaign', { timeout: 4000 });
  pillText = (await page.textContent('.desk-v1-camp-state-pill').catch(() => '') || '');
  /Paused/i.test(pillText)
    ? ok('project Pause: camp-2 (was proposed) shows Paused too')
    : fail(`camp-2 not paused: ${JSON.stringify(pillText)}`);

  await page.evaluate(() => window.deskV1Nav('project', { projectId: 'clayrune' }));
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });
  await page.click('[data-resume-project-btn]');
  await page.waitForTimeout(50);

  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.waitForSelector('.desk-v1-campaign', { timeout: 4000 });
  pillText = (await page.textContent('.desk-v1-camp-state-pill').catch(() => '') || '');
  /Active/i.test(pillText)
    ? ok('project Resume: camp-1 goes through validatePlan and returns to Active (it has a full plan)')
    : fail(`camp-1 not resumed: ${JSON.stringify(pillText)}`);

  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-2' }));
  await page.waitForSelector('.desk-v1-campaign', { timeout: 4000 });
  pillText = (await page.textContent('.desk-v1-camp-state-pill').catch(() => '') || '');
  /Proposed/i.test(pillText)
    ? ok('project Resume: camp-2 goes through validatePlan and returns to its pre-pause Proposed state')
    : fail(`camp-2 not resumed: ${JSON.stringify(pillText)}`);

  reportUncaught(pageErrors, '[project-pause-resume]');
  await ctx.close();
}

// ── IA6 (docs/THE_DESK_V1_IA_REVISION.md §5 row IA6) — project Next post is
// the EARLIEST scheduled version across the project's own campaigns, not
// just the newest/freshest one. camp-1's "30 Windows testers wanted" is
// scheduled 2026-09-30; the fresh camp-4 fixture (added for the campaign-page
// empty-state case) schedules later, 2026-10-06 — so this only passes if the
// project page actually compares across campaigns instead of picking one. ──
async function runNextPostAcrossCampaigns(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  await page.evaluate(() => window.deskV1Nav('project', { projectId: 'clayrune' }));
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });
  const nextText = (await page.textContent('.desk-v1-project-nextpost').catch(() => '') || '');
  // camp-1's "30 Windows testers wanted" piece is scheduled 2026-09-30, on
  // camp-1 (title "Windows beta testers"); camp-4's own piece is scheduled
  // later, 2026-10-06 (title "Community Discord launch") — the earliest one
  // wins only if the project page actually compares across campaigns.
  /Windows beta testers/.test(nextText) && !/Community Discord/.test(nextText)
    ? ok(`IA6: project Next post = earliest across campaigns: "${nextText.trim().replace(/\s+/g, ' ')}"`)
    : fail(`IA6: project Next post picked the wrong campaign: ${JSON.stringify(nextText)}`);

  reportUncaught(pageErrors, '[next-post]');
  await ctx.close();
}

// ── IA6 — archived campaigns, unreachable since IA1 removed Home's archived
// section, are reachable again via a project-page toggle; `More › Restore`
// puts one back on the live campaign grid. ─────────────────────────────────
async function runArchivedToggleRestore(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  await page.evaluate(() => window.deskV1Nav('project', { projectId: 'clayrune' }));
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });

  const beforeLive = await page.$('.desk-v1-project-camps [data-campaign-id="camp-archived-1"]');
  beforeLive === null ? ok('IA6: archived campaign is not on the live grid before Restore') : fail('IA6: archived campaign already on the live grid');
  const beforeToggle = await page.$('.desk-v1-project-archived-list');
  beforeToggle === null ? ok('IA6: archived list is collapsed by default') : fail('IA6: archived list rendered expanded by default');

  await page.click('[data-archived-toggle]');
  await page.waitForSelector('.desk-v1-project-archived-list [data-campaign-id="camp-archived-1"]', { timeout: 2000 });
  ok('IA6: archived toggle reveals the archived campaign (camp-archived-1)');

  await page.click('.desk-v1-project-archived-list [data-archived-more-btn]');
  await page.waitForSelector('.desk-v1-camp-cardmenu', { timeout: 2000 });
  const menuText = (await page.textContent('.desk-v1-camp-cardmenu').catch(() => '') || '');
  /Restore/.test(menuText) ? ok('IA6: archived card\'s More menu offers Restore') : fail(`IA6: More menu wrong: ${JSON.stringify(menuText)}`);

  await page.click('.desk-v1-camp-cardmenu [data-restore-btn]');
  await page.waitForTimeout(80);
  const afterLive = await page.$('.desk-v1-project-camps [data-campaign-id="camp-archived-1"]');
  afterLive !== null
    ? ok('IA6: More › Restore brings the campaign back to the live list')
    : fail('IA6: campaign missing from the live list after Restore');
  const state = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-archived-1').state);
  state === 'completed'
    ? ok('IA6: Restore returns the campaign to its pre-archive state ("completed"), not a bare "active"')
    : fail(`IA6: restored state wrong: ${state}`);

  reportUncaught(pageErrors, '[archived-restore]');
  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  await runHomeProjectCards(browser);
  await runProjectToCampaignAndBack(browser);
  await runSecondProject(browser);
  await runNeedsYouDeepStack(browser);
  await runStubRoutes(browser);
  await runPresenceCeilingClamp(browser);
  await runPresenceAddAccountConfirm(browser);
  await runProjectPauseResume(browser);
  await runNextPostAcrossCampaigns(browser);
  await runArchivedToggleRestore(browser);
  exitCode = bad === 0 ? 0 : 1;
} catch (e) {
  console.error('harness error:', e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad === 0 ? `\nAll checks passed.` : `\n${bad} check(s) failed.`);
process.exit(exitCode);

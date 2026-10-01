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
  c.back === '‹ Windows beta testers · What'
    ? ok(`piece's Back label is the campaign title + its ③ What stop: "${c.back}"`)
    : fail(`piece Back label wrong: ${JSON.stringify(c)}`);

  // MC-977 G-4: Home -> campaign no longer pushes the project page, so the stack a
  // Needs-you deep link builds is Home, campaign, piece, review: Back from the
  // campaign goes straight to Home ("Desk"), where the user came from.
  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-campaign', { timeout: 4000 });
  c = await crumb(page);
  c.title === 'Windows beta testers' && c.back === '‹ Desk'
    ? ok(`campaign's Back label is Home ("Desk"), where the deep link came from: "${c.back}"`)
    : fail(`campaign Back label wrong: ${JSON.stringify(c)}`);

  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-home', { timeout: 4000 });
  const backGone = await page.$('.desk-v1-back');
  !backGone
    ? ok('4-deep stack (home, campaign, piece, review) exhausted in exactly 3 Backs, landing on root Home with no Back button')
    : fail('Home unexpectedly still shows a Back button — stack deeper than 4');

  reportUncaught(pageErrors, '[needsyou-deep-stack]');
  await ctx.close();
}

// ── engagement/piece routes reached directly rather than via the review
// chain above (§5 row IA1: "engagement (stub), piece (stub)"), plus the project
// page's own settings (Presence retired, MC-977 2026-10-01: the one project-level
// setting left is the default planner, picked from the header chip). ────────
async function runStubRoutes(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  await page.click('.desk-v1-home-block-name[data-project-id="clayrune"]');
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });

  // The Presence page is gone: no button, no route, and the default planner is a
  // header chip that opens the agent picker instead.
  const gone = await page.$('.desk-v1-project-presence-btn');
  const chip = (await page.textContent('[data-project-agent]').catch(() => '') || '').trim();
  !gone && chip.length > 0 && !(await page.evaluate(() => typeof window.deskV1RenderPresence !== 'undefined'))
    ? ok(`project page: no Presence button or route, default planner chip reads "${chip}"`)
    : fail(`project page still has Presence (button=${!!gone}, chip=${JSON.stringify(chip)})`);
  await page.evaluate(() => window.deskV1Nav('project', { projectId: 'clayrune' }));
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

// ── Batch 2 (Ron, phone: "I still don't see an easy way to delete a draft
// campaign"): Draft project cards carry a visible ⋯ › Delete draft; the
// campaign page shows a plain 'Discard draft' beside the state chip. Both reuse
// deskV1DeleteDraftCampaign + its Undo; non-draft campaigns get neither. ─────
async function runDraftDiscoverability(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);
  const pick = await page.evaluate(() => {
    const cs = window.DeskV1Fixtures.campaigns;
    const mine = cs.filter((c) => c.projectId === 'clayrune' && c.state !== 'archived');
    const d = mine.find((c) => c.state === 'active') || mine[0];
    d.state = 'draft';
    const other = mine.find((c) => c !== d);
    return { draftId: d.id, otherId: other && other.id, otherState: other && other.state, n: cs.length };
  });

  await page.evaluate(() => window.deskV1Nav('project', { projectId: 'clayrune' }));
  await page.waitForSelector('.desk-v1-project-camp-card', { timeout: 4000 });
  const cards = await page.evaluate((p) => ({
    draftHas: !!document.querySelector(`.desk-v1-project-camp-card[data-campaign-id="${p.draftId}"] [data-draft-more]`),
    otherHas: p.otherId ? !!document.querySelector(`.desk-v1-project-camp-card[data-campaign-id="${p.otherId}"] [data-draft-more]`) : false,
  }), pick);
  if (cards.draftHas && !cards.otherHas) ok('Batch 2: only the Draft project card carries a ⋯');
  else fail(`Batch 2: project-card ⋯ wrong: ${JSON.stringify(cards)}`);

  await page.click(`.desk-v1-project-camp-card[data-campaign-id="${pick.draftId}"] [data-draft-more]`);
  await page.waitForSelector('.desk-v1-camp-cardmenu [data-menu-delete-draft]', { timeout: 4000 });
  const stillProject = await page.evaluate(() => !!document.querySelector('.desk-v1-project') && !document.querySelector('.desk-v1-campaign'));
  if (stillProject) ok('Batch 2: ⋯ on a project card opens the menu without opening the campaign');
  else fail('Batch 2: ⋯ click navigated into the campaign');
  await page.click('.desk-v1-camp-cardmenu [data-menu-delete-draft]');
  const gone = await page.evaluate((p) => ({ card: !!document.querySelector(`.desk-v1-project-camp-card[data-campaign-id="${p.draftId}"]`), n: window.DeskV1Fixtures.campaigns.length, onProject: !!document.querySelector('.desk-v1-project') }), pick);
  if (!gone.card && gone.n === pick.n - 1 && gone.onProject) ok('Batch 2: project card ⋯ › Delete draft removes the card and the campaign, stays on the project');
  else fail(`Batch 2: project-card delete wrong: ${JSON.stringify(gone)}`);
  await page.locator('.toast-action').last().locator('.toast-btn.primary').click();
  await page.waitForSelector(`.desk-v1-project-camp-card[data-campaign-id="${pick.draftId}"]`, { timeout: 4000 });
  const restored = await page.evaluate(() => window.DeskV1Fixtures.campaigns.length);
  if (restored === pick.n) ok('Batch 2: Undo restores the deleted draft card');
  else fail(`Batch 2: Undo did not restore: ${restored} vs ${pick.n}`);

  // Campaign page: 'Discard draft' beside the state chip.
  await page.evaluate((id) => window.deskV1Nav('campaign', { campaignId: id, projectId: 'clayrune', panel: 'what' }), pick.draftId);
  await page.waitForSelector('[data-discard-draft]', { timeout: 4000 });
  const hdr = await page.evaluate(() => { const b = document.querySelector('[data-discard-draft]'); const pill = document.querySelector('.desk-v1-map-pill'); const br = b.getBoundingClientRect(); const pr = pill.getBoundingClientRect(); return { text: b.textContent.trim(), sameRow: Math.abs((br.top + br.bottom) / 2 - (pr.top + pr.bottom) / 2) < 30, visible: br.width > 0 && br.height > 0 }; });
  if (hdr.text === 'Discard draft' && hdr.sameRow && hdr.visible) ok('Batch 2: campaign page shows a plain "Discard draft" beside the state chip');
  else fail(`Batch 2: Discard draft wrong: ${JSON.stringify(hdr)}`);
  await page.click('[data-discard-draft]');
  await page.waitForSelector('.desk-v1-home-board', { timeout: 4000 });
  const dn = await page.evaluate(() => window.DeskV1Fixtures.campaigns.length);
  if (dn === pick.n - 1) ok('Batch 2: Discard draft deletes the campaign and returns Home');
  else fail(`Batch 2: Discard draft left ${dn} campaigns (want ${pick.n - 1})`);
  await page.locator('.toast-action').last().locator('.toast-btn.primary').click();
  await page.waitForFunction((n) => window.DeskV1Fixtures.campaigns.length === n, pick.n, { timeout: 4000 }).then(() => ok('Batch 2: Undo after Discard draft restores the campaign'), () => fail('Batch 2: Undo after Discard draft did not restore'));

  // Non-draft campaign page: no Discard action (Archive/Delete stay behind their confirm).
  if (pick.otherId) {
    await page.evaluate((id) => window.deskV1Nav('campaign', { campaignId: id, projectId: 'clayrune', panel: 'what' }), pick.otherId);
    await page.waitForSelector('.desk-v1-map-tabs', { timeout: 4000 });
    const nd = await page.evaluate(() => !!document.querySelector('[data-discard-draft]'));
    if (!nd) ok(`Batch 2: a ${pick.otherState} campaign has no Discard draft (Archive/Delete keep their confirm)`);
    else fail(`Batch 2: a ${pick.otherState} campaign shows Discard draft`);
  }

  reportUncaught(pageErrors, '[draft-discoverability]');
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

// ── R2-16 (docs/THE_DESK_V1_IA_REVISION_2.md §8 row R2-16, §10.4): project
// page Playbook — 2 confirmed (F2 slot, F6 format) + 1 stale (F4) + 1 rejected
// (F3) fixture findings render in their groups; a campaign link lands on that
// campaign's first stop; Undo reject moves the finding back to `proposed`;
// Home has no playbook line. ────────────────────────────────────────────────
async function gotoClayruneProject(page) {
  await page.evaluate(() => window.deskV1Nav('project', { projectId: 'clayrune' }));
  await page.waitForSelector('#desk-v1-project-playbook .desk-v1-playbook-group, #desk-v1-project-playbook .desk-v1-home-needsyou-empty', { timeout: 4000 });
}

async function runPlaybookGroups(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);
  await gotoClayruneProject(page);

  const groups = await page.$$eval('#desk-v1-project-playbook [data-playbook-dimension]', (els) =>
    els.map((g) => ({ dim: g.dataset.playbookDimension, head: g.querySelector('.desk-v1-playbook-group-head').textContent.trim(),
      ids: [...g.querySelectorAll('[data-finding-id]')].map((r) => r.dataset.findingId) })));
  groups.length === 2 && groups.some((g) => g.dim === 'slot' && g.head === 'Posting day / time slot' && g.ids.join() === 'F2')
    && groups.some((g) => g.dim === 'format' && g.head === 'Piece format' && g.ids.join() === 'F6')
    ? ok(`Playbook: 2 confirmed findings in their dimension groups: ${JSON.stringify(groups)}`)
    : fail(`Playbook confirmed groups wrong: ${JSON.stringify(groups)}`);

  const f2 = (await page.textContent('[data-finding-id="F2"] .desk-v1-playbook-sentence')).replace(/\s+/g, ' ').trim();
  /Tue\/Thu 08-10 got 2\.1× the clicks per post of other slots \(1 campaign, n=41, medium\)/.test(f2)
    ? ok(`Playbook: F2 renders its sentence from structure: ${f2}`) : fail(`F2 sentence wrong: ${f2}`);
  const f2meta = await page.$$eval('[data-finding-id="F2"] .desk-v1-playbook-chip', (els) => els.map((e) => e.textContent.trim()));
  f2meta.join('|') === 'medium|n=41' ? ok('Playbook: F2 shows confidence + n chips') : fail(`F2 chips wrong: ${JSON.stringify(f2meta)}`);

  const stale = await page.$$eval('#desk-v1-project-playbook [data-playbook-stale] [data-finding-id]', (els) =>
    els.map((r) => ({ id: r.dataset.findingId, btns: [...r.querySelectorAll('.desk-v1-retro-btn')].map((b) => b.textContent.trim()),
      text: r.querySelector('.desk-v1-playbook-sentence').textContent.replace(/\s+/g, ' ').trim() })));
  stale.length === 1 && stale[0].id === 'F4' && stale[0].btns.join('|') === 'Re-confirm|Retire' && /Video clips out-clicked plain posts early on\./.test(stale[0].text)
    ? ok('Playbook: Stale sub-list holds F4 (Ron\'s edited wording) with Re-confirm / Retire')
    : fail(`Playbook stale list wrong: ${JSON.stringify(stale)}`);
  const staleInConfirmed = await page.$('[data-playbook-dimension] [data-finding-id="F4"]');
  staleInConfirmed === null ? ok('Playbook: stale F4 is not in the confirmed groups') : fail('stale F4 leaked into a confirmed group');

  const collapsedToggle = (await page.textContent('[data-rejected-toggle]')).replace(/\s+/g, ' ').trim();
  const collapsedRows = await page.$('[data-playbook-rejected] [data-finding-id]');
  /Rejected \(1\)/.test(collapsedToggle) && collapsedRows === null
    ? ok(`Playbook: rejected is collapsed by default (${collapsedToggle})`) : fail(`rejected not collapsed: ${collapsedToggle} / row=${collapsedRows !== null}`);
  await page.click('[data-rejected-toggle]');
  await page.waitForSelector('[data-playbook-rejected] [data-finding-id="F3"]', { timeout: 2000 });
  const undoBtn = await page.textContent('[data-playbook-rejected] [data-finding-id="F3"] [data-finding-undo-reject]');
  undoBtn.trim() === 'Undo reject' ? ok('Playbook: expanding rejected shows F3 with Undo reject') : fail(`F3 action wrong: ${undoBtn}`);

  reportUncaught(pageErrors, '[playbook-groups]');
  await ctx.close();
}

async function runPlaybookCampaignLink(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);
  await gotoClayruneProject(page);

  // F6 cites camp-archived-1 and camp-1; camp-1 is Active, so a plain campaign
  // nav would land on What. The link must land on the first stop instead.
  const links = await page.$$eval('[data-finding-id="F6"] [data-playbook-campaign]', (els) => els.map((e) => e.dataset.playbookCampaign));
  links.join() === 'camp-archived-1,camp-1' ? ok(`Playbook: F6 links both evidence campaigns: ${links}`) : fail(`F6 campaign links wrong: ${links}`);
  await page.click('[data-finding-id="F6"] [data-playbook-campaign="camp-1"]');
  await page.waitForSelector('.desk-v1-map-stop', { timeout: 4000 });
  const here = await page.$$eval('.desk-v1-map-stop[data-state="here"]', (els) => els.map((e) => e.dataset.stop));
  const first = await page.evaluate(() => window.DeskV1Kit.MAP_STOPS[0]);
  const title = (await crumb(page)).title;
  here.join() === first && first === 'how' && title === 'Windows beta testers'
    ? ok(`Playbook: campaign link lands on camp-1's first stop (${first} = Brief), crumb "${title}"`)
    : fail(`campaign link landed wrong: here=${JSON.stringify(here)} first=${first} title=${title}`);

  reportUncaught(pageErrors, '[playbook-link]');
  await ctx.close();
}

async function runPlaybookUndoReject(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);
  await gotoClayruneProject(page);
  await page.click('[data-rejected-toggle]');
  await page.waitForSelector('[data-finding-id="F3"] [data-finding-undo-reject]', { timeout: 2000 });
  const snap = () => page.evaluate(() => {
    const fx = window.DeskV1Fixtures;
    const f = fx.playbook.findings.find((x) => x.id === 'F3');
    return { state: f.state, decided_by: f.decided_by,
      rejections: fx.playbook.rejections.filter((r) => r.dimension === 'platform_voice').length,
      listed: fx.retros['camp-archived-1:1'].findings.includes('F3'),
      toConfirm: window.deskV1RetroFindingsToConfirm('camp-archived-1') };
  });
  const before = await snap();
  await page.click('[data-finding-id="F3"] [data-finding-undo-reject]');
  await page.waitForTimeout(80);
  const after = await snap();
  before.state === 'rejected' && before.rejections === 1 && !before.listed && before.toConfirm === 1
    && after.state === 'proposed' && after.decided_by === null && after.rejections === 0 && after.listed && after.toConfirm === 2
    ? ok(`Playbook: Undo reject moves F3 rejected -> proposed, drops its rejection record, lists it on the retro: ${JSON.stringify(after)}`)
    : fail(`Undo reject wrong: before=${JSON.stringify(before)} after=${JSON.stringify(after)}`);
  (await page.$('[data-playbook-rejected]')) === null
    ? ok('Playbook: rejected block disappears once nothing is rejected') : fail('rejected block still rendered');

  // The toast's Undo puts it back.
  await page.click('.toast-action .toast-btn.primary');
  await page.waitForTimeout(80);
  const undone = await snap();
  undone.state === 'rejected' && undone.rejections === 1 && !undone.listed
    ? ok('Playbook: undoing Undo reject restores the rejection and the retro list') : fail(`undo of undo wrong: ${JSON.stringify(undone)}`);

  reportUncaught(pageErrors, '[playbook-undo-reject]');
  await ctx.close();
}

async function runPlaybookStaleActions(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);
  await gotoClayruneProject(page);
  await page.click('[data-playbook-stale] [data-finding-reconfirm]');
  await page.waitForTimeout(80);
  const re = await page.evaluate(() => { const f = window.DeskV1Fixtures.playbook.findings.find((x) => x.id === 'F4'); return { state: f.state, origin: f.origin, by: f.decided_by, stale: f.stale_reason }; });
  const inGroup = await page.$('[data-playbook-dimension="format"] [data-finding-id="F4"]');
  const staleGone = await page.$('[data-playbook-stale]');
  re.state === 'confirmed' && re.origin === 'interactive' && re.by === 'ron' && re.stale === null && inGroup !== null && staleGone === null
    ? ok('Playbook: Re-confirm moves F4 stale -> confirmed (origin interactive) into its format group') : fail(`Re-confirm wrong: ${JSON.stringify(re)}`);
  reportUncaught(pageErrors, '[playbook-reconfirm]');
  await ctx.close();

  const p2 = await newBootedPage(browser);
  await gotoClayruneProject(p2.page);
  await p2.page.click('[data-playbook-stale] [data-finding-retire]');
  await p2.page.waitForTimeout(80);
  const rt = await p2.page.evaluate(() => ({
    state: window.DeskV1Fixtures.playbook.findings.find((x) => x.id === 'F4').state,
    rejections: window.DeskV1Fixtures.playbook.rejections.length,
  }));
  const anywhere = await p2.page.$('#desk-v1-project-playbook [data-finding-id="F4"]');
  rt.state === 'retired' && rt.rejections === 1 && anywhere === null
    ? ok('Playbook: Retire moves F4 stale -> retired, records no rejection, shows nowhere on the page') : fail(`Retire wrong: ${JSON.stringify(rt)}`);
  reportUncaught(p2.pageErrors, '[playbook-retire]');
  await p2.ctx.close();
}

async function runPlaybookEmptyAndHome(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  // Home: no playbook line anywhere on the board or in Needs-you (§10.4).
  const homeText = await page.evaluate(() => document.querySelector('.desk-v1-shell').innerText);
  !/playbook/i.test(homeText) && (await page.$('[data-playbook-dimension], #desk-v1-project-playbook')) === null
    ? ok('Home has no playbook line') : fail(`Home mentions the playbook: ${homeText.match(/.{0,40}playbook.{0,40}/i)}`);

  await page.evaluate(() => window.deskV1Nav('project', { projectId: 'engulfing_scanner' }));
  await page.waitForSelector('#desk-v1-project-playbook .desk-v1-home-needsyou-empty', { timeout: 4000 });
  const empty = (await page.textContent('#desk-v1-project-playbook')).replace(/\s+/g, ' ').trim();
  /No confirmed findings yet/.test(empty) && (await page.$('#desk-v1-project-playbook [data-finding-id]')) === null
    ? ok('Playbook: a project with no findings reads the empty line and shows none of clayrune\'s') : fail(`empty playbook wrong: ${empty}`);

  reportUncaught(pageErrors, '[playbook-empty]');
  await ctx.close();
}

async function runPlaybookShots(browser) {
  const a = await newBootedPage(browser);
  await gotoClayruneProject(a.page);
  await a.page.click('[data-rejected-toggle]');
  await a.page.click('[data-finding-id="F6"] [data-playbook-evidence-toggle]');
  await a.page.locator('#desk-v1-project-playbook').scrollIntoViewIfNeeded();
  await a.page.screenshot({ path: resolve(SHOT_DIR, 'r2_16_playbook_1440.png') });
  ok('desktop screenshot saved: r2_16_playbook_1440.png');
  reportUncaught(a.pageErrors, '[playbook-shot-1440]');
  await a.ctx.close();

  const b = await newBootedPage(browser, { width: 390, height: 844 });
  await gotoClayruneProject(b.page);
  await b.page.click('[data-rejected-toggle]');
  await b.page.locator('#desk-v1-project-playbook').scrollIntoViewIfNeeded();
  await b.page.screenshot({ path: resolve(SHOT_DIR, 'r2_16_playbook_390.png') });
  const overflow = await b.page.evaluate(() => { const h = document.getElementById('desk-v1-project-playbook'); return h.scrollWidth - h.clientWidth; });
  overflow <= 1 ? ok('phone screenshot saved: r2_16_playbook_390.png (no horizontal overflow)') : fail(`playbook overflows the 390 viewport by ${overflow}px`);
  reportUncaught(b.pageErrors, '[playbook-shot-390]');
  await b.ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  await runHomeProjectCards(browser);
  await runProjectToCampaignAndBack(browser);
  await runSecondProject(browser);
  await runNeedsYouDeepStack(browser);
  await runStubRoutes(browser);
  await runProjectPauseResume(browser);
  await runNextPostAcrossCampaigns(browser);
  await runDraftDiscoverability(browser);
  await runArchivedToggleRestore(browser);
  await runPlaybookGroups(browser);
  await runPlaybookCampaignLink(browser);
  await runPlaybookUndoReject(browser);
  await runPlaybookStaleActions(browser);
  await runPlaybookEmptyAndHome(browser);
  await runPlaybookShots(browser);
  exitCode = bad === 0 ? 0 : 1;
} catch (e) {
  console.error('harness error:', e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad === 0 ? `\nAll checks passed.` : `\n${bad} check(s) failed.`);
process.exit(exitCode);

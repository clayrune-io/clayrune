#!/usr/bin/env node
/**
 * Desk v1 (MC-977 IA revision, docs/THE_DESK_V1_IA_REVISION.md §5 row IA4)
 * — Campaign setup smoke (3 in-page steps + step 0 Presence gate).
 *
 * Closes IA4's acceptance: New campaign in engulfing_scanner -> accept
 * defaults -> Proposed with 3 Planned pieces -> step 3 (the Start sheet)
 * shows 0 '—' with its inherited rows labelled 'from Engulfing scanner' ->
 * Start -> Active; leaving setup at step 2 shows 'Setup 2 of 3' on the
 * project page's campaign card, and Continue lands back on step 2; a
 * campaign whose picked accounts are all manual-capability (no API-
 * connected account) completes via the '✋ You publish it' Replies copy
 * instead of the API-review default.
 *
 * Real headless boot (real index.html + real static/js|css, no network), same
 * hermetic shape as desk-v1-project.mjs / desk-v1-rules.mjs.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-setup.mjs
 * Exit 0 = every case holds; 1 = a case regressed / harness error.
 */
import { readFileSync, readdirSync } from 'node:fs';
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

const PID = 'smoke_deskv1setup';
const PROJECTS = [{
  id: PID, name: 'Desk v1 setup smoke', status: 'active', domain: 'general', emoji: '🧪',
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
  if (path === '/api/floor') return J({ bench: [] });
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

async function openProject(page, projectId) {
  await page.click(`.desk-v1-home-project-card[data-project-id="${projectId}"]`);
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });
}

// ── §5 IA4 acceptance row, part 1: engulfing_scanner "accept defaults" runs
// all three in-page steps straight through to Active, with 3 Planned pieces
// and a Start sheet that shows every bound resolved (0 '—') and names where
// the inherited ones came from. ─────────────────────────────────────────────
async function runAcceptDefaultsToActive(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  await openProject(page, 'engulfing_scanner');
  await page.click('.desk-v1-project-newcamp-btn');
  await page.waitForSelector('.desk-v1-campaign', { timeout: 4000 });

  // Step 1 (Subject + goal) — engulfing_scanner already has a bound account,
  // so step 0's Presence gate is skipped straight to step 1; every field
  // ships pre-filled (§5 IA4: "accept defaults" needs no typing).
  let stepText = (await page.textContent('.desk-v1-camp-state-pill').catch(() => '') || '');
  /Setup 1 of 3/.test(stepText)
    ? ok(`new campaign in engulfing_scanner opens straight to step 1: "${stepText.trim()}"`)
    : fail(`step 1 pill wrong: ${JSON.stringify(stepText)}`);
  await page.click('[data-setup-continue]');

  // Step 2 (Plan) — accept the pre-ticked account + default post cap.
  await page.waitForSelector('[data-setup-draftplan]', { timeout: 4000 });
  stepText = (await page.textContent('.desk-v1-camp-state-pill').catch(() => '') || '');
  /Setup 2 of 3/.test(stepText)
    ? ok(`step 2 (Plan) renders: "${stepText.trim()}"`)
    : fail(`step 2 pill wrong: ${JSON.stringify(stepText)}`);
  await page.click('[data-setup-draftplan]');

  // Draft the plan flips the campaign to Proposed with 3 Planned pieces.
  await page.waitForSelector('.desk-v1-camp-state-pill', { timeout: 4000 });
  const pillText = (await page.textContent('.desk-v1-camp-state-pill').catch(() => '') || '');
  /Proposed/.test(pillText)
    ? ok(`"Draft the plan" flips the campaign to Proposed: "${pillText.trim()}"`)
    : fail(`campaign not Proposed after step 2: ${JSON.stringify(pillText)}`);

  const pieceLabels = await page.$$eval('.desk-v1-rules-proposed-card .desk-v1-state-word', (els) => els.map((e) => e.textContent.trim()));
  pieceLabels.length === 3 && pieceLabels.every((t) => t === 'Planned')
    ? ok(`3 Planned pieces created: ${JSON.stringify(pieceLabels)}`)
    : fail(`planned pieces wrong: ${JSON.stringify(pieceLabels)}`);

  // Step 3 — the existing Proposed-state Start sheet; every bound resolved
  // (0 '—'), inherited ones (accounts/cadence/end, all defaulted from the
  // project) named "from Engulfing scanner".
  await page.click('[data-start-campaign]');
  await page.waitForSelector('.desk-v1-rules-sheet', { timeout: 4000 });
  const rowVals = await page.$$eval('.desk-v1-rules-authrow-val', (els) => els.map((e) => e.textContent.trim()));
  const dashCount = rowVals.filter((v) => v === '—').length;
  dashCount === 0
    ? ok(`step 3 Start sheet shows 0 '—' rows: ${JSON.stringify(rowVals)}`)
    : fail(`step 3 Start sheet still shows ${dashCount} '—' row(s): ${JSON.stringify(rowVals)}`);
  const fromCount = rowVals.filter((v) => /from Engulfing scanner/.test(v)).length;
  fromCount >= 2
    ? ok(`step 3 inherited rows labelled "from Engulfing scanner" (${fromCount} row(s)): ${JSON.stringify(rowVals)}`)
    : fail(`step 3 inherited-from labelling missing/short (${fromCount}): ${JSON.stringify(rowVals)}`);

  await page.click('[data-sheet-confirm]');
  await page.waitForSelector('.desk-v1-camp-state-pill', { timeout: 4000 });
  const activeText = (await page.textContent('.desk-v1-camp-state-pill').catch(() => '') || '');
  /Active/.test(activeText)
    ? ok(`Confirm — Start campaign takes it Active: "${activeText.trim()}"`)
    : fail(`campaign not Active after Start confirm: ${JSON.stringify(activeText)}`);

  reportUncaught(pageErrors, '[accept-defaults-to-active]');
  await ctx.close();
}

// ── §5 IA4 acceptance row, part 2: leaving setup mid-way (before "Draft the
// plan" commits step 2) keeps the campaign in Draft at that step — the
// project page's own card names the step, and re-opening it resumes there,
// not back at step 1 or forward into a half-drafted plan. ──────────────────
async function runLeaveAtStepTwoResumes(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  await openProject(page, 'engulfing_scanner');
  await page.click('.desk-v1-project-newcamp-btn');
  await page.waitForSelector('[data-setup-continue]', { timeout: 4000 });
  await page.click('[data-setup-continue]');
  await page.waitForSelector('[data-setup-draftplan]', { timeout: 4000 });

  // engulfing_scanner already has one Active campaign (camp-3) — resolve the
  // just-created draft by id (last pushed onto the fixture array) rather than
  // assuming card order, so this assertion targets the right card.
  const campaignId = await page.evaluate(() => {
    const camps = window.DeskV1Fixtures.campaigns;
    return camps[camps.length - 1].id;
  });
  const cardSel = `.desk-v1-project-camp-card[data-campaign-id="${campaignId}"]`;

  // Leave without drafting the plan — back to the project page.
  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });

  const cardLabel = (await page.textContent(`${cardSel} .desk-v1-state-word`).catch(() => '') || '');
  /Setup 2 of 3/.test(cardLabel)
    ? ok(`left setup at step 2: project card reads "${cardLabel.trim()}"`)
    : fail(`project card label wrong after leaving at step 2: ${JSON.stringify(cardLabel)}`);

  await page.click(cardSel);
  await page.waitForSelector('.desk-v1-campaign', { timeout: 4000 });
  const resumedPill = (await page.textContent('.desk-v1-camp-state-pill').catch(() => '') || '');
  const resumedHasDraftPlan = await page.$('[data-setup-draftplan]');
  /Setup 2 of 3/.test(resumedPill) && resumedHasDraftPlan
    ? ok(`Continue lands back on step 2 (Plan): "${resumedPill.trim()}"`)
    : fail(`resuming did not land on step 2: pill=${JSON.stringify(resumedPill)}, hasDraftPlanBtn=${!!resumedHasDraftPlan}`);

  reportUncaught(pageErrors, '[leave-at-step-two]');
  await ctx.close();
}

// ── §5 IA4 acceptance row, part 3: a campaign whose only picked accounts are
// manual-capability (clayrune's "Clayrune blog", never API-connected) has
// nothing for Posy to draft into review — the Start sheet's Replies row
// names the real mechanism, "✋ You publish it", not the API-review default.
// ────────────────────────────────────────────────────────────────────────
async function runZeroConnectedAccountsManualCopy(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  await openProject(page, 'clayrune');
  await page.click('.desk-v1-project-newcamp-btn');
  await page.waitForSelector('[data-setup-continue]', { timeout: 4000 });
  await page.click('[data-setup-continue]');
  await page.waitForSelector('[data-setup-draftplan]', { timeout: 4000 });

  // Uncheck every account except the manual-capability one (ch-blog);
  // clayrune's other two (ch-x-ron, ch-li-page) are both 'direct' capability.
  await page.uncheck('[data-setup-account="ch-x-ron"]');
  await page.uncheck('[data-setup-account="ch-li-page"]');
  const checkedNow = await page.$$eval('[data-setup-account]:checked', (els) => els.map((e) => e.dataset.setupAccount));
  checkedNow.length === 1 && checkedNow[0] === 'ch-blog'
    ? ok(`only the manual-capability account (ch-blog) stays picked: ${JSON.stringify(checkedNow)}`)
    : fail(`account picking wrong before draft: ${JSON.stringify(checkedNow)}`);

  await page.click('[data-setup-draftplan]');
  await page.waitForSelector('.desk-v1-camp-state-pill', { timeout: 4000 });
  await page.click('[data-start-campaign]');
  await page.waitForSelector('.desk-v1-rules-sheet', { timeout: 4000 });

  const repliesVal = await page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll('.desk-v1-rules-authrow'));
    const row = rows.find((r) => r.querySelector('.desk-v1-rules-authrow-label').textContent.trim() === 'Replies');
    return row ? row.querySelector('.desk-v1-rules-authrow-val').textContent.trim() : null;
  });
  repliesVal === '✋ You publish it'
    ? ok(`zero connected (all-manual) accounts: Replies row reads "${repliesVal}"`)
    : fail(`Replies row wrong for all-manual accounts: ${JSON.stringify(repliesVal)}`);

  reportUncaught(pageErrors, '[zero-connected-accounts-manual-copy]');
  await ctx.close();
}

// ── IA4 rework gap 1 (Dave's review): §2.3 row 3's "Required to leave:
// validatePlan ok" was never enforced — Confirm had to be gated on it, and
// the sheet has to name the missing bound with its step. Clear the end bound
// a normal "Draft the plan" always sets, then re-open the sheet on the same
// draft to force the gate. ──────────────────────────────────────────────────
async function runStep3GateMissingEndCannotStart(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  await openProject(page, 'engulfing_scanner');
  await page.click('.desk-v1-project-newcamp-btn');
  await page.waitForSelector('[data-setup-continue]', { timeout: 4000 });
  await page.click('[data-setup-continue]');
  await page.waitForSelector('[data-setup-draftplan]', { timeout: 4000 });
  await page.click('[data-setup-draftplan]');
  await page.waitForSelector('.desk-v1-camp-state-pill', { timeout: 4000 });

  await page.evaluate(() => {
    const camps = window.DeskV1Fixtures.campaigns;
    camps[camps.length - 1].plan.end = { date: null, post_cap: null };
  });

  await page.click('[data-start-campaign]');
  await page.waitForSelector('.desk-v1-rules-sheet', { timeout: 4000 });

  const confirmDisabled = await page.$eval('[data-sheet-confirm]', (b) => b.disabled);
  confirmDisabled
    ? ok('step 3 Confirm is disabled when validatePlan is not ok (missing end)')
    : fail('step 3 Confirm should be disabled when the plan is missing its end bound');

  const noteText = (await page.textContent('.desk-v1-rules-sheet-note').catch(() => '') || '');
  /end date/.test(noteText) && /step 2/.test(noteText)
    ? ok(`step 3 sheet names the missing bound with its step: "${noteText.trim()}"`)
    : fail(`step 3 sheet note wrong: ${JSON.stringify(noteText)}`);

  reportUncaught(pageErrors, '[step3-gate-missing-end]');
  await ctx.close();
}

// ── IA4 rework gap 2 (Dave's review): §2.3 row 3's "Change for the project
// ›" link on inherited rows didn't exist. Click it from step 3 and land on
// the campaign's own project's Presence page. ──────────────────────────────
async function runChangeForProjectLinkNavigatesToPresence(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  await openProject(page, 'engulfing_scanner');
  await page.click('.desk-v1-project-newcamp-btn');
  await page.waitForSelector('[data-setup-continue]', { timeout: 4000 });
  await page.click('[data-setup-continue]');
  await page.waitForSelector('[data-setup-draftplan]', { timeout: 4000 });
  await page.click('[data-setup-draftplan]');
  await page.waitForSelector('.desk-v1-camp-state-pill', { timeout: 4000 });
  await page.click('[data-start-campaign]');
  await page.waitForSelector('.desk-v1-rules-sheet', { timeout: 4000 });

  const linkCount = await page.$$eval('[data-change-project]', (els) => els.length);
  linkCount > 0
    ? ok(`step 3 sheet renders ${linkCount} "Change for the project ›" link(s) on inherited rows`)
    : fail('step 3 sheet has no "Change for the project ›" link on any inherited row');

  await page.click('[data-change-project]');
  await page.waitForSelector('.desk-v1-presence', { timeout: 4000 });
  // engulfing_scanner's own presence fixture binds exactly 1 account
  // (ch-x-ron); clayrune's binds 3 — a distinct row count is a stronger
  // proof of "the right project's Presence page" than the crumb text, which
  // reads the campaign's own title (the actual previous stack entry, per
  // shell.js's own back-label rule), not the project name.
  const accountRowCount = await page.$$eval('.desk-v1-presence-account-row', (els) => els.length);
  accountRowCount === 1
    ? ok(`"Change for the project ›" lands on Engulfing scanner's Presence page (${accountRowCount} bound account)`)
    : fail(`"Change for the project ›" landed on the wrong project's Presence page (${accountRowCount} bound accounts, expected Engulfing scanner's 1)`);

  reportUncaught(pageErrors, '[change-for-project-link]');
  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  await runAcceptDefaultsToActive(browser);
  await runLeaveAtStepTwoResumes(browser);
  await runZeroConnectedAccountsManualCopy(browser);
  await runStep3GateMissingEndCannotStart(browser);
  await runChangeForProjectLinkNavigatesToPresence(browser);
  exitCode = bad === 0 ? 0 : 1;
} catch (e) {
  console.error('harness error:', e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad === 0 ? `\nAll checks passed.` : `\n${bad} check(s) failed.`);
process.exit(exitCode);

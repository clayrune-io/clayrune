#!/usr/bin/env node
/**
 * Desk v1 (MC-977, R0 plan T2b) — Proposed state, Start sheet, rules
 * popover, Posy instructions smoke (no frame drawn — gap map C6; built from
 * THE_DESK_V1_UI.md §3.5, §8, §9, §11).
 *
 * Closes: A12 (channel badge copy flips between "Publishes after approval"
 * and "Publishes automatically" with the review-mode rule — actually owned
 * by T0b's kit, re-verified here end-to-end through a live rule change).
 *
 * Real headless boot (real index.html + real static/js|css, no network), same
 * hermetic shape as desk-v1-calendar.mjs / desk-v1-review.mjs.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-rules.mjs
 * Exit 0 = every case holds; 1 = a case regressed / harness error.
 * Also writes docs/desk_v1/screens/t2b_{proposed,start_sheet,rules}_
 * {desktop_1440,phone_390}.png (default tone only).
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

const PID = 'smoke_deskv1rules';
const PROJECTS = [{
  id: PID, name: 'Desk v1 rules smoke', status: 'active', domain: 'general', emoji: '🧪',
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

async function navToCampaign(page, campaignId) {
  await page.evaluate((id) => window.deskV1Nav('campaign', { campaignId: id }), campaignId);
  await page.waitForSelector('.desk-v1-camp-summary', { timeout: 8000 });
}

function reportUncaught(pageErrors, tag) {
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail(`${tag} uncaught page error: ${e}`));
}

// ── §3.5 Proposed state render checks, one per tone: state pill, editable
// goal sentence, untracked-goal warning (CMP-03), the blocker card, and
// Posy's proposed pieces with "? Assumed" popovers. ─────────────────────────
async function runProposedRenderChecks(browser, tone) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, tone);
  await navToCampaign(page, 'camp-2');

  const stateWord = (await page.textContent('.desk-v1-camp-state-pill .desk-v1-state-word').catch(() => '') || '').trim();
  stateWord === 'Proposed'
    ? ok(`[${tone.name}] state pill reads "Proposed"`)
    : fail(`[${tone.name}] state pill wrong: ${JSON.stringify(stateWord)}`);

  const startBtn = await page.$('[data-start-campaign]');
  startBtn ? ok(`[${tone.name}] "Start campaign" primary button renders`) : fail(`[${tone.name}] Start campaign button missing`);

  const dashedFields = await page.$$eval('[data-goal-field]', (els) => els.map((e) => e.dataset.goalField));
  ['target', 'dateLabel', 'audience'].every((f) => dashedFields.includes(f))
    ? ok(`[${tone.name}] goal sentence has 3 editable dashed fields: ${JSON.stringify(dashedFields)}`)
    : fail(`[${tone.name}] goal sentence fields wrong: ${JSON.stringify(dashedFields)}`);

  const warning = await page.$('.desk-v1-rules-goal-warning');
  warning ? ok(`[${tone.name}] untracked goal shows "⚠ not tracked yet" (CMP-03), not a blocker`) : fail(`[${tone.name}] untracked-goal warning missing`);

  const badgeTitle = await page.$eval('.desk-v1-camp-summary-badges .desk-v1-channel-badge', (el) => el.title).catch(() => '');
  /Publishes after approval/.test(badgeTitle)
    ? ok(`[${tone.name}] A12: each-piece review mode → channel badge reads "Publishes after approval": ${JSON.stringify(badgeTitle)}`)
    : fail(`[${tone.name}] channel badge copy wrong for each_piece: ${JSON.stringify(badgeTitle)}`);

  const blockerQ = (await page.textContent('.desk-v1-rules-blocker-q').catch(() => '') || '').trim();
  /subreddit/i.test(blockerQ)
    ? ok(`[${tone.name}] "⛔ Posy's one question" blocker card renders: "${blockerQ}"`)
    : fail(`[${tone.name}] blocker card missing/wrong: ${JSON.stringify(blockerQ)}`);
  const answerCount = await page.$$eval('.desk-v1-rules-blocker-answer', (els) => els.length);
  answerCount === 2
    ? ok(`[${tone.name}] blocker card has 2 answer buttons`)
    : fail(`[${tone.name}] expected 2 blocker answers, got ${answerCount}`);

  const cardTitles = await page.$$eval('.desk-v1-rules-proposed-card .desk-v1-camp-card-title', (els) => els.map((e) => e.textContent.trim()));
  cardTitles.length === 2
    ? ok(`[${tone.name}] Posy's 2 proposed pieces render: ${JSON.stringify(cardTitles)}`)
    : fail(`[${tone.name}] expected 2 proposed cards, got ${JSON.stringify(cardTitles)}`);

  const assumedCount = await page.$$eval('.desk-v1-rules-assumed', (els) => els.length);
  assumedCount === 2
    ? ok(`[${tone.name}] both proposed cards carry a "? Assumed" popover`)
    : fail(`[${tone.name}] expected 2 "? Assumed" popovers, got ${assumedCount}`);

  // §3.5: "the same page... shows the same chips" as T2a's Active summary —
  // the Proposed variant must carry its own Rules group + Edit hook, not
  // just Goal/Channels.
  const proposedChips = await page.$$eval('[data-summary-group="rules"] .desk-v1-camp-rule-chip', (els) => els.map((e) => e.textContent));
  const proposedEditBtn = await page.$('[data-summary-group="rules"] [data-rules-edit]');
  proposedChips.length > 0 && proposedEditBtn
    ? ok(`[${tone.name}] §3.5: Proposed summary carries the same Rules chips + Edit hook: ${JSON.stringify(proposedChips)}`)
    : fail(`[${tone.name}] Proposed summary missing Rules chips/Edit hook: chips=${JSON.stringify(proposedChips)}, editBtn=${!!proposedEditBtn}`);

  reportUncaught(pageErrors, `[${tone.name}]`);
  await ctx.close();
}

// ── goal-sentence editing (§3.5: "dashed underlines on the editable
// parts") — blur commits the value into both the sentence fixture and the
// campaign's own tracked goal. ──────────────────────────────────────────────
async function runGoalEdit(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCampaign(page, 'camp-2');

  await page.click('[data-goal-field="target"]');
  await page.keyboard.press('Control+A');
  await page.keyboard.type('75');
  await page.click('.desk-v1-rules-goal-sentence'); // blur

  const target = await page.evaluate(() => {
    const camp = window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-2');
    return camp.goal.target;
  });
  target === 75
    ? ok('editing the goal sentence\'s target field commits to camp.goal.target on blur')
    : fail(`goal target did not commit: ${JSON.stringify(target)}`);

  reportUncaught(pageErrors, '[goal-edit]');
  await ctx.close();
}

// ── the blocker card's answer buttons (§3.5) — a command with Undo, like
// every other Desk drop/action (§10). ───────────────────────────────────────
async function runBlockerAnswer(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCampaign(page, 'camp-2');
  await page.waitForSelector('.desk-v1-rules-blocker', { timeout: 4000 });

  await page.click('[data-answer-id="a-sideproject"]');
  const goneCard = await page.$('.desk-v1-rules-blocker');
  !goneCard ? ok('answering the blocker removes its card') : fail('blocker card still present after answering');

  await page.waitForSelector('.toast .toast-btn.primary', { timeout: 2000 }).catch(() => {});
  await page.click('.toast .toast-btn.primary');
  await page.waitForTimeout(50);
  const backCard = await page.$('.desk-v1-rules-blocker');
  backCard ? ok('Undo restores the blocker card') : fail('Undo did not restore the blocker card');

  reportUncaught(pageErrors, '[blocker-answer]');
  await ctx.close();
}

// ── Start sheet (CMP-05) — plain-language authority list, "Starting doesn't
// approve any piece", Cancel/Escape leave state untouched, Confirm → Active
// + a policy record, with Undo reverting both. ──────────────────────────────
async function runStartSheet(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCampaign(page, 'camp-2');

  await page.click('[data-start-campaign]');
  await page.waitForSelector('.desk-v1-rules-sheet', { timeout: 2000 });
  ok('"Start campaign" opens the review sheet');

  const title = (await page.textContent('.desk-v1-rules-sheet-title').catch(() => '') || '');
  /Reddit AMA push/.test(title)
    ? ok(`sheet title names the campaign: "${title.trim()}"`)
    : fail(`sheet title wrong: ${JSON.stringify(title)}`);

  const rowLabels = await page.$$eval('.desk-v1-rules-authrow-label', (els) => els.map((e) => e.textContent.trim()));
  ['Accounts', 'Frequency ceiling', 'Dates', 'Review mode', 'Replies', 'Paid', 'Generation limits', 'Stop conditions']
    .every((l) => rowLabels.includes(l))
    ? ok(`all 8 CMP-05 authority rows render: ${JSON.stringify(rowLabels)}`)
    : fail(`authority rows missing/wrong: ${JSON.stringify(rowLabels)}`);

  const note = (await page.textContent('.desk-v1-rules-sheet-note').catch(() => '') || '');
  /Starting doesn.t approve any piece/.test(note)
    ? ok('sheet states "Starting doesn\'t approve any piece" verbatim')
    : fail(`sheet note wrong: ${JSON.stringify(note)}`);

  // Escape closes without changing state.
  await page.keyboard.press('Escape');
  const closedByEsc = !(await page.$('.desk-v1-rules-sheet'));
  closedByEsc ? ok('Escape closes the sheet') : fail('Escape did not close the sheet');

  // Cancel closes without changing state.
  await page.click('[data-start-campaign]');
  await page.waitForSelector('.desk-v1-rules-sheet', { timeout: 2000 });
  await page.click('[data-sheet-cancel]');
  const closedByCancel = !(await page.$('.desk-v1-rules-sheet'));
  const stateAfterCancel = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-2').state);
  closedByCancel && stateAfterCancel === 'proposed'
    ? ok('Cancel closes the sheet and leaves the campaign Proposed')
    : fail(`Cancel misbehaved: closed=${closedByCancel}, state=${JSON.stringify(stateAfterCancel)}`);

  // Confirm → Active + policy record; Undo reverts both.
  await page.click('[data-start-campaign]');
  await page.waitForSelector('.desk-v1-rules-sheet', { timeout: 2000 });
  await page.click('[data-sheet-confirm]');
  await page.waitForTimeout(80);
  const afterConfirm = await page.evaluate(() => {
    const camp = window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-2');
    return { state: camp.state, hasPolicy: !!camp.policyRecord };
  });
  afterConfirm.state === 'active' && afterConfirm.hasPolicy
    ? ok('Confirm sets the campaign Active and creates a policy record')
    : fail(`Confirm did not start the campaign correctly: ${JSON.stringify(afterConfirm)}`);

  await page.waitForSelector('.toast .toast-btn.primary', { timeout: 2000 }).catch(() => {});
  await page.click('.toast .toast-btn.primary');
  await page.waitForTimeout(50);
  const afterUndo = await page.evaluate(() => {
    const camp = window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-2');
    return { state: camp.state, hasPolicy: !!camp.policyRecord };
  });
  afterUndo.state === 'proposed' && !afterUndo.hasPolicy
    ? ok('Undo reverts the campaign back to Proposed and drops the policy record')
    : fail(`Undo did not revert correctly: ${JSON.stringify(afterUndo)}`);

  reportUncaught(pageErrors, '[start-sheet]');
  await ctx.close();
}

// ── §8 Rules popover (C1: both review modes) via the REAL entry point —
// T2a's Edit hook ([data-rules-edit]) on camp-1's (Active) summary, matching
// how a user actually gets here rather than a direct nav shortcut. Verifies
// it opens a floating popover (not a `‹ <campaign>` page/breadcrumb), that
// changing a control shows a visible effect preview + Apply step (no
// mutation until Apply — the fixture must still read the OLD value right
// after `change`), that Apply on a widening choice still confirms, and that
// the applied change round-trips into the campaign summary's rule chip AND
// the A12 channel-badge copy. ────────────────────────────────────────────────
async function runReviewModeToggle(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCampaign(page, 'camp-1');

  await page.click('[data-rules-edit]');
  await page.waitForSelector('.desk-v1-rules-pop', { timeout: 4000 });
  ok('the campaign summary\'s Edit link (T2a hook) opens the rules POPOVER, not a page');

  const breadcrumbHasRules = await page.$$eval('.desk-v1-crumb, .modal-crumb', (els) => els.some((e) => /Rules/i.test(e.textContent))).catch(() => false);
  !breadcrumbHasRules
    ? ok('opening the popover does not add a "Rules" breadcrumb — the campaign page stays underneath')
    : fail('a "Rules" breadcrumb appeared — the popover regressed to a full-page route');

  const checkedDefault = await page.$eval('input[name="reviewMode"]:checked', (el) => el.value);
  checkedDefault === 'each_piece'
    ? ok('C1: fixture default review mode is "You approve each piece"')
    : fail(`default review mode wrong: ${JSON.stringify(checkedDefault)}`);

  await page.check('input[name="reviewMode"][value="themes"]');
  await page.waitForTimeout(30);

  const previewText = (await page.textContent('.desk-v1-rules-pop-previewtext').catch(() => '') || '');
  /themes/i.test(previewText)
    ? ok(`§8: changing a control shows a visible effect preview before applying: "${previewText.trim()}"`)
    : fail(`effect preview missing/wrong: ${JSON.stringify(previewText)}`);

  const unappliedYet = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-1').rules.reviewMode);
  unappliedYet !== 'themes'
    ? ok('the fixture is NOT mutated until Apply is clicked')
    : fail(`fixture mutated before Apply: reviewMode=${JSON.stringify(unappliedYet)}`);

  await page.evaluate(() => { window.__confirms = []; window.confirm = (msg) => { window.__confirms.push(msg); return true; }; });
  await page.click('[data-pop-preview-apply]');
  await page.waitForTimeout(30);
  const confirms = await page.evaluate(() => window.__confirms);
  confirms.length > 0
    ? ok(`Apply on "Approve themes, then run" (widening) asks for confirmation: "${confirms[0]}"`)
    : fail('Apply on a widening choice should have called window.confirm');

  const appliedNow = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-1').rules.reviewMode);
  appliedNow === 'themes'
    ? ok('confirming Apply commits the mutation')
    : fail(`Apply did not commit: reviewMode=${JSON.stringify(appliedNow)}`);

  await page.keyboard.press('Escape');
  await navToCampaign(page, 'camp-1');
  const chipsAfterWiden = await page.$$eval('.desk-v1-camp-rule-chip', (els) => els.map((e) => e.textContent));
  chipsAfterWiden.some((c) => /Approve themes, then run/.test(c))
    ? ok(`campaign summary's rule chip reflects the new review mode: ${JSON.stringify(chipsAfterWiden)}`)
    : fail(`rule chip did not update: ${JSON.stringify(chipsAfterWiden)}`);

  const badgeAfterWiden = await page.$eval('.desk-v1-camp-summary-badges .desk-v1-channel-badge', (el) => el.title).catch(() => '');
  /Publishes automatically/.test(badgeAfterWiden)
    ? ok(`A12: themes review mode → channel badge flips to "Publishes automatically": ${JSON.stringify(badgeAfterWiden)}`)
    : fail(`channel badge did not flip for themes mode: ${JSON.stringify(badgeAfterWiden)}`);

  // Narrowing back applies with no confirmation.
  await page.click('[data-rules-edit]');
  await page.waitForSelector('.desk-v1-rules-pop', { timeout: 4000 });
  await page.evaluate(() => { window.__confirms = []; });
  await page.check('input[name="reviewMode"][value="each_piece"]');
  await page.waitForTimeout(30);
  await page.click('[data-pop-preview-apply]');
  await page.waitForTimeout(30);
  const confirmsOnNarrow = await page.evaluate(() => window.__confirms.length);
  confirmsOnNarrow === 0
    ? ok('narrowing back to "each piece" applies without confirmation')
    : fail(`narrowing should not confirm, got ${confirmsOnNarrow} call(s)`);

  await page.keyboard.press('Escape');
  reportUncaught(pageErrors, '[review-mode]');
  await ctx.close();
}

// ── declining a widening confirm reverts the UI to the real (unchanged)
// value by re-rendering, rather than tracking each control's prior state. ──
async function runDeclineWidening(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCampaign(page, 'camp-1');
  await page.click('[data-rules-edit]');
  await page.waitForSelector('.desk-v1-rules-pop', { timeout: 4000 });

  // check(), not a plain click: staging a pending change (the popover's
  // preview/Apply step) leaves the radio checked until Apply runs — only
  // declining inside the confirm() prompt reverts it back to Off.
  await page.evaluate(() => { window.confirm = () => false; });
  await page.check('input[name="paid"][value="on"]');
  await page.waitForTimeout(30);
  await page.click('[data-pop-preview-apply]');
  await page.waitForTimeout(30);

  const stillOff = await page.$eval('input[name="paid"][value="off"]', (el) => el.checked);
  const fixtureStillOff = await page.evaluate(() => !window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-1').rules.paid);
  stillOff && fixtureStillOff
    ? ok('declining the Paid-on widening confirm reverts the radio to Off, and the fixture is untouched')
    : fail(`decline did not revert cleanly: radioOff=${stillOff}, fixtureOff=${fixtureStillOff}`);

  await page.keyboard.press('Escape');
  reportUncaught(pageErrors, '[decline-widening]');
  await ctx.close();
}

// ── Channels row (§8: "included / excluded accounts, e.g. 'in · Ron
// (personal) excluded'") — camp-2's channelIds is only ['ch-x-ron'], so
// ch-li-page ("in · Clayrune page") is a pre-existing channel that's
// naturally unattached to it; no 4th global channel needed (see the fixture
// comment — one broke desk-v1-campaign.mjs's channel-shelf assumption for
// camp-1). Via [data-rules-edit]: the Proposed summary carries the same Edit
// hook as T2a's Active one (deskV1FillProposedSummary), so camp-2 opens the
// popover the same real way a user would. ──────────────────────────────────
async function runChannelsExcluded(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCampaign(page, 'camp-2');
  await page.click('[data-rules-edit]');
  await page.waitForSelector('.desk-v1-rules-pop', { timeout: 4000 });

  const pageRow = await page.$('[data-channel-toggle="ch-li-page"]');
  pageRow ? ok('the unattached channel (Clayrune LinkedIn page) appears in the Channels list') : fail('ch-li-page row missing');
  const pageChecked = await page.$eval('[data-channel-toggle="ch-li-page"]', (el) => el.checked).catch(() => true);
  const excludedLabel = await page.$eval('[data-channel-toggle="ch-li-page"]', (el) => el.closest('label').textContent).catch(() => '');
  !pageChecked && /excluded/.test(excludedLabel)
    ? ok(`§8's worked example: "in · Clayrune page" shows unchecked + "excluded": "${excludedLabel.trim()}"`)
    : fail(`ch-li-page row wrong: checked=${pageChecked}, label=${JSON.stringify(excludedLabel)}`);

  await page.evaluate(() => { window.__confirms = []; window.confirm = (m) => { window.__confirms.push(m); return true; }; });
  await page.check('[data-channel-toggle="ch-li-page"]');
  await page.waitForTimeout(30);
  await page.click('[data-pop-preview-apply]');
  await page.waitForTimeout(30);
  const includingConfirmed = await page.evaluate(() => window.__confirms.length);
  includingConfirmed > 0
    ? ok('attaching a new channel (widening) asks for confirmation')
    : fail('attaching a channel should have confirmed');
  const nowIncluded = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-2').channelIds.includes('ch-li-page'));
  nowIncluded ? ok('confirmed attach adds the channel to campaign.channelIds') : fail('channel not added after confirm');

  await page.keyboard.press('Escape');
  reportUncaught(pageErrors, '[channels]');
  await ctx.close();
}

// ── §3.4 Posy instructions (INS-01..04): before/after, a durable
// instruction becomes a visible rule chip (INS-02), a widening one confirms
// first, Undo reverses a durable chip. ──────────────────────────────────────
async function runPosyInstructions(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToCampaign(page, 'camp-1');

  const input = '#desk-v1-camp-posy-input';
  await page.waitForSelector(input, { timeout: 4000 });

  // Plain (non-widening, non-durable) instruction.
  await page.fill(input, 'Focus this week on the video piece');
  await page.keyboard.press('Enter');
  await page.waitForSelector('.desk-v1-rules-posyreply', { timeout: 2000 });
  const reply = (await page.textContent('.desk-v1-rules-posyreply').catch(() => '') || '');
  /Before/.test(reply) && /After/.test(reply)
    ? ok(`plain instruction renders Before/After: "${reply.replace(/\s+/g, ' ').trim().slice(0, 90)}..."`)
    : fail(`Before/After reply missing: ${JSON.stringify(reply)}`);

  // Durable instruction → visible rule chip (INS-02).
  await page.fill(input, 'Always keep replies short from now on');
  await page.keyboard.press('Enter');
  await page.waitForTimeout(50);
  const chips = await page.$$eval('.desk-v1-camp-rule-chip', (els) => els.map((e) => e.textContent));
  chips.some((c) => /keep replies short/.test(c))
    ? ok(`INS-02: a durable instruction becomes a new rule chip: ${JSON.stringify(chips)}`)
    : fail(`durable instruction did not add a rule chip: ${JSON.stringify(chips)}`);

  // Undo removes the chip again. `.last()`, not the bare selector: the
  // plain instruction above left its own un-dismissed toast in the stack
  // (no auto-dismiss, no toast `key`, so commandBus never replaces one
  // in place) — the durable instruction's toast is the newest one.
  await page.waitForSelector('.toast .toast-btn.primary', { timeout: 2000 }).catch(() => {});
  await page.locator('.toast .toast-btn.primary').last().click();
  await page.waitForTimeout(50);
  const chipsAfterUndo = await page.$$eval('.desk-v1-camp-rule-chip', (els) => els.map((e) => e.textContent));
  chipsAfterUndo.some((c) => /keep replies short/.test(c))
    ? fail(`Undo should have removed the durable chip: ${JSON.stringify(chipsAfterUndo)}`)
    : ok('Undo removes the durable rule chip again');

  // Widening instruction → confirm first.
  await page.evaluate(() => { window.__confirms = []; window.confirm = (m) => { window.__confirms.push(m); return true; }; });
  await page.fill(input, 'Turn on paid promotion for this');
  await page.keyboard.press('Enter');
  await page.waitForTimeout(30);
  const widenConfirms = await page.evaluate(() => window.__confirms.length);
  widenConfirms > 0
    ? ok('a widening Posy instruction asks for confirmation before applying')
    : fail('widening instruction should have confirmed');

  reportUncaught(pageErrors, '[posy-instructions]');
  await ctx.close();
}

// ── Phone (§11): Proposed summary collapses (reuses T2a's phone rule since
// this variant shares the same data-summary-group markup), the Start sheet
// docks to the bottom edge full width, and the rules page's controls stay
// touch-sized. ───────────────────────────────────────────────────────────────
async function runPhoneLayout(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} }, { width: 390, height: 844 });
  await navToCampaign(page, 'camp-2');

  const channelsHidden = await page.$eval('.desk-v1-camp-summary-group[data-summary-group="channels"]', (el) => getComputedStyle(el).display).catch(() => '');
  channelsHidden === 'none'
    ? ok('§11: phone collapses the Proposed summary\'s Channels group')
    : fail(`§11: Channels group should be hidden on phone, got ${JSON.stringify(channelsHidden)}`);

  await page.click('[data-start-campaign]');
  await page.waitForSelector('.desk-v1-rules-sheet', { timeout: 2000 });
  const sheetBox = await page.$eval('.desk-v1-rules-sheet', (el) => el.getBoundingClientRect());
  Math.abs(sheetBox.width - 390) < 2
    ? ok(`§11: Start sheet docks full-width on phone (${sheetBox.width.toFixed(0)}px)`)
    : fail(`§11: Start sheet not full-width on phone: ${sheetBox.width}px`);
  const confirmHeight = await page.$eval('[data-sheet-confirm]', (el) => el.getBoundingClientRect().height);
  confirmHeight >= 44
    ? ok(`§11: Confirm button is touch-sized (${confirmHeight.toFixed(0)}px)`)
    : fail(`§11: Confirm button too short for touch: ${confirmHeight}px`);
  await page.keyboard.press('Escape');

  // Direct nav, not the [data-rules-edit] hook: §11's own "summary collapses"
  // rule (T2a's existing phone CSS) hides the whole Rules summary group —
  // Edit included — on a 390px viewport, same as the Channels group above.
  // `deskV1RenderRules`'s kept-for-compat shim still resolves this to the
  // real popover, now re-docked full-width bottom sheet by the §11 media
  // query (`.desk-v1-rules-pop` in the 960px block).
  await navToCampaign(page, 'camp-1');
  await page.evaluate(() => window.deskV1Nav('rules', { campaignId: 'camp-1' }));
  await page.waitForSelector('.desk-v1-rules-pop', { timeout: 4000 });
  const popBox = await page.$eval('.desk-v1-rules-pop', (el) => el.getBoundingClientRect());
  Math.abs(popBox.width - 390) < 2
    ? ok(`§11: rules popover docks full-width bottom sheet on phone (${popBox.width.toFixed(0)}px)`)
    : fail(`§11: rules popover not full-width on phone: ${popBox.width}px`);
  await page.keyboard.press('Escape');

  reportUncaught(pageErrors, '[phone]');
  await ctx.close();
}

// ── screenshots (default tone only, per the ticket brief): Proposed page,
// Start sheet, rules page, each at desktop 1440 and phone 390. ─────────────
async function captureScreenshots(browser) {
  for (const [w, h, tag] of [[1440, 950, 'desktop_1440'], [390, 844, 'phone_390']]) {
    const { ctx, page } = await newBootedPage(browser, { ls: {} }, { width: w, height: h });
    await navToCampaign(page, 'camp-2');
    await page.waitForSelector('.desk-v1-rules-proposed', { timeout: 4000 });
    await page.screenshot({ path: resolve(SHOT_DIR, `t2b_proposed_${tag}.png`) });
    ok(`screenshot saved: t2b_proposed_${tag}.png`);

    await page.click('[data-start-campaign]');
    await page.waitForSelector('.desk-v1-rules-sheet', { timeout: 2000 });
    await page.screenshot({ path: resolve(SHOT_DIR, `t2b_start_sheet_${tag}.png`) });
    ok(`screenshot saved: t2b_start_sheet_${tag}.png`);
    await ctx.close();
  }
  for (const [w, h, tag] of [[1440, 950, 'desktop_1440'], [390, 844, 'phone_390']]) {
    const { ctx, page } = await newBootedPage(browser, { ls: {} }, { width: w, height: h });
    // camp-2, direct nav: shows §8's included/excluded worked example for
    // real (camp-1's 3 channels are all attached, so it never has an
    // excluded row — see the fixture comment on why camp-2 carries that demo).
    await navToCampaign(page, 'camp-2');
    await page.evaluate(() => window.deskV1Nav('rules', { campaignId: 'camp-2' }));
    await page.waitForSelector('.desk-v1-rules-pop', { timeout: 4000 });
    await page.screenshot({ path: resolve(SHOT_DIR, `t2b_rules_${tag}.png`) });
    ok(`screenshot saved: t2b_rules_${tag}.png`);
    await ctx.close();
  }
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  for (const tone of TONES) await runProposedRenderChecks(browser, tone);
  await runGoalEdit(browser);
  await runBlockerAnswer(browser);
  await runStartSheet(browser);
  await runReviewModeToggle(browser);
  await runDeclineWidening(browser);
  await runChannelsExcluded(browser);
  await runPosyInstructions(browser);
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

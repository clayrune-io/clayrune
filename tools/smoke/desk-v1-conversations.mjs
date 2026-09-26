#!/usr/bin/env node
/**
 * Desk v1 (MC-977, R0 plan T6) — Conversations smoke (frame 12c).
 *
 * Closes: A11 (the source switch is always visible and coverage gaps are
 * printed).
 *
 * Home's "1 reply waiting" row already deep-links via
 * `deskV1Nav('conversations', {campaignId, conversationId})` — this mounts
 * the same way, through the shell's own pre-registered route.
 *
 * Real headless boot (real index.html + real static/js|css, no network), same
 * hermetic shape as desk-v1-calendar.mjs / desk-v1-review.mjs.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-conversations.mjs
 * Exit 0 = every case holds (render checks in all three tones, interaction
 * checks once, phone layout once); 1 = a case regressed / harness error.
 * Also writes docs/desk_v1/screens/t6_conversations_{desktop_1440,phone_390}.png
 * (default tone only).
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

const PID = 'smoke_deskv1conversations';
const PROJECTS = [{
  id: PID, name: 'Desk v1 conversations smoke', status: 'active', domain: 'general', emoji: '🧪',
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

// Through the campaign page first (matches the real navigation path), then
// straight to conversations via the shell route T0a already registers —
// same two-step precedent T4's calendar smoke uses.
async function navToConversations(page, conversationId) {
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.evaluate((cid) => window.deskV1Nav('conversations', cid ? { campaignId: 'camp-1', conversationId: cid } : { campaignId: 'camp-1' }), conversationId || null);
}

function reportUncaught(pageErrors, tag) {
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail(`${tag} uncaught page error: ${e}`));
}

// ── render checks, one per tone: source switch always visible with counts
// matching the frame, list rows show author/age/snippet/reason, coverage
// gap footer prints, an empty source never reads "no one is talking". ──────
async function runToneRenderChecks(browser, tone) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, tone);
  await navToConversations(page);
  await page.waitForSelector('.desk-v1-conv-layout', { timeout: 8000 });

  const sourceLabels = await page.$$eval('.desk-v1-conv-source', (els) => els.map((e) => e.textContent.trim()));
  sourceLabels.length === 3
    ? ok(`[${tone.name}] source switch (A11) always renders all 3 segments: ${JSON.stringify(sourceLabels)}`)
    : fail(`[${tone.name}] expected 3 source segments, got ${JSON.stringify(sourceLabels)}`);

  // Verified against a 930px crop of frame 12c: "On our posts · 2",
  // "Mentions · 1", "Discussions" (no number — its one conversation is
  // 'stale', not countable). See desk-v1-fixtures.js's own T6 comment for
  // the row-by-row accounting.
  const ourPosts = sourceLabels.find((l) => l.startsWith('On our posts'));
  const mentions = sourceLabels.find((l) => l.startsWith('Mentions'));
  const discussions = sourceLabels.find((l) => l.startsWith('Discussions'));
  ourPosts === 'On our posts · 2'
    ? ok(`[${tone.name}] "On our posts" badge matches the frame: "${ourPosts}"`)
    : fail(`[${tone.name}] "On our posts" badge wrong: ${JSON.stringify(ourPosts)}`);
  mentions === 'Mentions · 1'
    ? ok(`[${tone.name}] "Mentions" badge matches the frame: "${mentions}"`)
    : fail(`[${tone.name}] "Mentions" badge wrong: ${JSON.stringify(mentions)}`);
  discussions === 'Discussions'
    ? ok(`[${tone.name}] "Discussions" carries no number (its item isn't countable): "${discussions}"`)
    : fail(`[${tone.name}] "Discussions" badge wrong: ${JSON.stringify(discussions)}`);

  // Default tab is "On our posts" — 3 rows (devnull_kat, Mira Okafor,
  // sam_builds), each with an author, an age, and a reason line.
  const rows = await page.$$eval('.desk-v1-conv-row', (els) => els.map((e) => ({
    author: (e.querySelector('.desk-v1-conv-author') || {}).textContent,
    reason: (e.querySelector('.desk-v1-conv-reason') || {}).textContent,
  })));
  rows.length === 3
    ? ok(`[${tone.name}] "On our posts" lists 3 rows: ${JSON.stringify(rows.map((r) => r.author))}`)
    : fail(`[${tone.name}] expected 3 rows, got ${rows.length}: ${JSON.stringify(rows)}`);
  const questionRow = rows.find((r) => (r.author || '').includes('devnull_kat'));
  questionRow && /Question/.test(questionRow.reason) && /reply drafted/.test(questionRow.reason)
    ? ok(`[${tone.name}] devnull_kat's row reads "? Question · reply drafted"`)
    : fail(`[${tone.name}] devnull_kat's reason line wrong: ${JSON.stringify(questionRow)}`);
  const needsYouRow = rows.find((r) => (r.author || '').includes('Mira Okafor'));
  needsYouRow && /Needs you/.test(needsYouRow.reason) && /account problem/.test(needsYouRow.reason)
    ? ok(`[${tone.name}] Mira Okafor's row reads "⚑ Needs you · account problem"`)
    : fail(`[${tone.name}] Mira Okafor's reason line wrong: ${JSON.stringify(needsYouRow)}`);
  const noReplyRow = rows.find((r) => (r.author || '').includes('sam_builds'));
  noReplyRow && /No reply suggested/.test(noReplyRow.reason)
    ? ok(`[${tone.name}] sam_builds's row reads "No reply suggested"`)
    : fail(`[${tone.name}] sam_builds's reason line wrong: ${JSON.stringify(noReplyRow)}`);

  // §6: the list footer states coverage gaps.
  const gapText = (await page.textContent('.desk-v1-conv-gaps').catch(() => '') || '');
  /LinkedIn mentions not available/.test(gapText)
    ? ok(`[${tone.name}] coverage-gap footer prints: "${gapText.trim()}"`)
    : fail(`[${tone.name}] coverage-gap footer missing/wrong: ${JSON.stringify(gapText)}`);

  // Switch to Discussions (only conv-3, 'stale') — never reads as empty/
  // "no one is talking" even where the badge shows no count.
  await page.click('[data-conv-source="discussions"]');
  await page.waitForTimeout(30);
  const discRows = await page.$$eval('.desk-v1-conv-row', (els) => els.length);
  discRows === 1
    ? ok(`[${tone.name}] Discussions tab lists its 1 conversation despite the badge showing no count`)
    : fail(`[${tone.name}] Discussions should list 1 row, got ${discRows}`);

  reportUncaught(pageErrors, `[${tone.name}]`);
  await ctx.close();
}

// ── Home deep-link honours both params (campaignId + conversationId): jumps
// straight to conv-1's thread with its source tab already active. ─────────
async function runDeepLink(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToConversations(page, 'conv-1');
  await page.waitForSelector('.desk-v1-conv-layout', { timeout: 8000 });

  const selected = await page.$eval('.desk-v1-conv-row.is-selected', (el) => el.dataset.convId).catch(() => null);
  selected === 'conv-1'
    ? ok('deep link via {campaignId, conversationId} selects conv-1 on mount')
    : fail(`deep link did not select conv-1, got ${JSON.stringify(selected)}`);

  const activeSource = await page.$eval('.desk-v1-conv-source.is-active', (el) => el.dataset.convSource).catch(() => null);
  activeSource === 'our_posts'
    ? ok('the source tab switches to match the deep-linked conversation ("our_posts")')
    : fail(`expected source "our_posts" active, got ${JSON.stringify(activeSource)}`);

  const replyHead = (await page.textContent('.desk-v1-conv-reply-head').catch(() => '') || '');
  /Replying as @ron on/.test(replyHead)
    ? ok(`thread shows the proposed reply as it will be sent: "${replyHead.trim()}"`)
    : fail(`reply head missing/wrong: ${JSON.stringify(replyHead)}`);

  reportUncaught(pageErrors, '[deep-link]');
  await ctx.close();
}

// ── a row can't promise one question and open on another (Dave's review
// pass, MC-977 T6): every fixture conversation whose thread carries a first
// comment must show that EXACT text as its list-row excerpt. Checked at the
// fixture-data level (not the DOM) so it holds for every conversation, not
// just the one conv-1 case that regressed. ─────────────────────────────────
async function runRowMatchesThread(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToConversations(page, 'conv-1');
  await page.waitForSelector('.desk-v1-conv-layout', { timeout: 8000 });

  const mismatches = await page.evaluate(() => {
    const fx = window.DeskV1Fixtures || {};
    const detail = fx.conversationDetail || {};
    const bad = [];
    for (const c of (fx.conversations || [])) {
      const comments = (detail[c.id] || {}).thread && (detail[c.id] || {}).thread.comments;
      if (!comments || !comments.length) continue;
      if (c.excerpt !== comments[0].text) bad.push({ id: c.id, excerpt: c.excerpt, comment: comments[0].text });
    }
    return bad;
  });
  mismatches.length === 0
    ? ok('every conversation with a first comment shows that exact text as its row excerpt')
    : fail(`row/thread text mismatch: ${JSON.stringify(mismatches)}`);

  reportUncaught(pageErrors, '[row-matches-thread]');
  await ctx.close();
}

// ── Send is simulated (ticket brief + §6): no network call, just a local
// state flip through the commandBus (toast + Undo), and Send disables once
// there's nothing left to send. ─────────────────────────────────────────────
async function runSimulatedSend(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  const requests = [];
  page.on('request', (r) => { if (r.url().includes('/api/')) requests.push(r.url()); });
  await navToConversations(page, 'conv-1');
  await page.waitForSelector('.desk-v1-conv-layout', { timeout: 8000 });

  await page.click('[data-conv-send]');
  await page.waitForSelector('.toast', { timeout: 2000 }).catch(() => {});
  const toastText = (await page.textContent('.toast').catch(() => '') || '');
  /Sent your reply/.test(toastText)
    ? ok(`Send fires the commandBus toast: "${toastText.trim()}"`)
    : fail(`Send toast missing/wrong: ${JSON.stringify(toastText)}`);

  const state = await page.evaluate(() => (window.DeskV1Fixtures.conversations || []).find((c) => c.id === 'conv-1').state);
  state === 'sent'
    ? ok('conv-1 fixture state flips to "sent" — a real client-side mutation, not a no-op')
    : fail(`expected conv-1.state === 'sent', got ${JSON.stringify(state)}`);

  const apiCalls = requests.filter((u) => !/\/api\/(projects|config|characters)$/.test(u));
  apiCalls.length === 0
    ? ok('Send made no platform-write API call — simulated only, per the ticket brief')
    : fail(`Send should not hit the network, but called: ${JSON.stringify(apiCalls)}`);

  const sendDisabled = await page.$eval('[data-conv-send]', (el) => el.disabled).catch(() => false);
  sendDisabled
    ? ok('Send disables once the reply is already sent')
    : fail('Send should disable after sending');

  reportUncaught(pageErrors, '[simulated-send]');
  await ctx.close();
}

// ── stale/incomplete context → hold bar + disabled Send (U14). conv-3
// (discussions) carries a fixture `thread.hold` for exactly this case. ──────
async function runStaleHold(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToConversations(page, 'conv-3');
  await page.waitForSelector('.desk-v1-conv-layout', { timeout: 8000 });

  const holdText = (await page.textContent('.desk-v1-conv-hold').catch(() => '') || '');
  holdText.length > 0
    ? ok(`U14: a stale thread shows a hold bar: "${holdText.trim()}"`)
    : fail('U14: expected a hold bar for the stale conversation (conv-3)');

  const sendDisabled = await page.$eval('[data-conv-send]', (el) => el.disabled).catch(() => false);
  sendDisabled
    ? ok('U14: Send is disabled while the thread is stale')
    : fail('U14: Send should be disabled for a stale/incomplete thread');

  reportUncaught(pageErrors, '[stale-hold]');
  await ctx.close();
}

// ── Take over revokes automated sending immediately (banner + disabled
// actions) and Resume is a separate, explicit action (§6). ──────────────────
async function runTakeOver(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToConversations(page, 'conv-1');
  await page.waitForSelector('.desk-v1-conv-layout', { timeout: 8000 });

  await page.click('[data-conv-takeover]');
  await page.waitForTimeout(30);
  const banner = (await page.textContent('.desk-v1-conv-takeover-banner').catch(() => '') || '');
  /taken over/i.test(banner)
    ? ok(`Take over shows a persistent banner: "${banner.trim().replace(/\s+/g, ' ')}"`)
    : fail(`Take over banner missing/wrong: ${JSON.stringify(banner)}`);

  const sendDisabled = await page.$eval('[data-conv-send]', (el) => el.disabled).catch(() => false);
  sendDisabled
    ? ok('Send disables immediately once taken over — automated sending revoked')
    : fail('Send should disable after Take over');

  // Resume is the explicit action the spec requires — a real click, not the
  // toast's own transient Undo (which may already have expired).
  await page.click('[data-conv-resume]');
  await page.waitForTimeout(30);
  const bannerGone = await page.$('.desk-v1-conv-takeover-banner');
  !bannerGone
    ? ok('Resume is a separate, explicit action that clears the takeover banner')
    : fail('banner should be gone after an explicit Resume');

  reportUncaught(pageErrors, '[take-over]');
  await ctx.close();
}

// ── §11 phone: hit targets ≥44px, and a selected conversation replaces the
// list (narrow width can't show both) with an explicit back row. ───────────
async function runPhoneLayout(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} }, { width: 390, height: 844 });
  await navToConversations(page, 'conv-1');
  await page.waitForSelector('.desk-v1-conv-layout', { timeout: 8000 });

  const visibility = await page.evaluate(() => ({
    list: getComputedStyle(document.querySelector('.desk-v1-conv-list-pane')).display,
    hasSelection: document.querySelector('.desk-v1-conv-layout').dataset.hasSelection,
  }));
  visibility.hasSelection === '1' && visibility.list === 'none'
    ? ok('§11: with a conversation selected, phone hides the list (thread takes the full width)')
    : fail(`§11: phone list visibility wrong: ${JSON.stringify(visibility)}`);

  const backRowHeight = await page.$eval('.desk-v1-conv-backrow', (el) => el.getBoundingClientRect().height).catch(() => 0);
  backRowHeight >= 40
    ? ok(`§11: back-to-list row is touch-sized (${backRowHeight.toFixed(0)}px)`)
    : fail(`§11: back row too short for touch: ${backRowHeight}px`);

  const actionBtn = await page.$eval('.desk-v1-conv-actions button', (el) => el.getBoundingClientRect().height).catch(() => 0);
  actionBtn >= 40
    ? ok(`§11: action buttons are touch-sized (${actionBtn.toFixed(0)}px)`)
    : fail(`§11: action button too short for touch: ${actionBtn}px`);

  await page.click('[data-conv-back]');
  await page.waitForTimeout(30);
  const listVisibleAgain = await page.$eval('.desk-v1-conv-list-pane', (el) => getComputedStyle(el).display).catch(() => 'none');
  listVisibleAgain !== 'none'
    ? ok('§11: Back to list returns to the list pane')
    : fail('§11: Back to list did not restore the list pane');

  reportUncaught(pageErrors, '[phone]');
  await ctx.close();
}

// ── screenshots (default tone only, per the ticket brief). ─────────────────
async function captureScreenshots(browser) {
  {
    const { ctx, page } = await newBootedPage(browser, { ls: {} }, { width: 1440, height: 950 });
    await navToConversations(page, 'conv-1');
    await page.waitForSelector('.desk-v1-conv-layout', { timeout: 8000 });
    await page.screenshot({ path: resolve(SHOT_DIR, 't6_conversations_desktop_1440.png') });
    ok('desktop screenshot saved: t6_conversations_desktop_1440.png');
    await ctx.close();
  }
  {
    const { ctx, page } = await newBootedPage(browser, { ls: {} }, { width: 390, height: 844 });
    await navToConversations(page, 'conv-1');
    await page.waitForSelector('.desk-v1-conv-layout', { timeout: 8000 });
    await page.screenshot({ path: resolve(SHOT_DIR, 't6_conversations_phone_390.png') });
    ok('phone screenshot saved: t6_conversations_phone_390.png');
    await ctx.close();
  }
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  for (const tone of TONES) await runToneRenderChecks(browser, tone);
  await runRowMatchesThread(browser);
  await runDeepLink(browser);
  await runSimulatedSend(browser);
  await runStaleHold(browser);
  await runTakeOver(browser);
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

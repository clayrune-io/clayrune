#!/usr/bin/env node
/**
 * Desk v1 (MC-977 IA revision) — IA7: Engagement dashboard smoke.
 * docs/THE_DESK_V1_IA_REVISION.md §5 row IA7, §7.
 *
 * Reached three ways, all through the shell's `engagement` route
 * (desk-v1-shell.js): Home's own header count (no params — every project),
 * a project page's Engagement strip "Open ›" (`{projectId}`, IA6), or a
 * campaign's Conversations tab (`{campaignId}`) — which hands off straight
 * to the existing deskV1RenderConversations rather than re-implementing it.
 *
 * Real headless boot (real index.html + real static/js|css, no network),
 * same hermetic shape as desk-v1-conversations.mjs.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-engagement.mjs
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

const PID = 'smoke_deskv1engagement';
const PROJECTS = [{
  id: PID, name: 'Desk v1 engagement smoke', status: 'active', domain: 'general', emoji: '🧪',
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

async function navToEngagement(page, params) {
  await page.evaluate((p) => window.deskV1Nav('engagement', p), params || {});
}

function reportUncaught(pageErrors, tag) {
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail(`${tag} uncaught page error: ${e}`));
}

async function rowIds(page) {
  return page.$$eval('.desk-v1-eng-row', (els) => els.map((e) => e.dataset.engConv));
}

// ── Home header count (§1 "💬 Engagement · n") — sum of Suggested
// (needs_reply) + Needs you (needs_you) across BOTH fixture projects.
// Fixture accounting: conv-1 needs_reply; conv-4, conv-6 (clayrune),
// conv-7 (engulfing_scanner), conv-8 (clayrune, no campaign) needs_you.
// 1 + 4 = 5.
async function runHomeCount(browser, tone) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, tone);
  const btnText = (await page.textContent('.desk-v1-home-engagement-btn').catch(() => '') || '').trim();
  /Engagement\s*(·|·)\s*5/.test(btnText)
    ? ok(`[${tone.name}] Home header reads "${btnText}" (Suggested 1 + Needs you 4, across both projects)`)
    : fail(`[${tone.name}] Home header engagement count wrong: ${JSON.stringify(btnText)}`);

  await page.click('.desk-v1-home-engagement-btn');
  await page.waitForSelector('.desk-v1-engagement', { timeout: 4000 });
  ok(`[${tone.name}] Home header click lands on the real Engagement route`);

  reportUncaught(pageErrors, `[${tone.name}]`);
  await ctx.close();
}

// ── project filter hides the other project's rows. ─────────────────────────
async function runProjectFilter(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToEngagement(page, {});
  await page.waitForSelector('.desk-v1-engagement', { timeout: 4000 });

  const allIds = await rowIds(page);
  allIds.includes('conv-7') && (allIds.includes('conv-4') || allIds.includes('conv-6'))
    ? ok(`all-projects Incoming lane mixes both projects: ${JSON.stringify(allIds)}`)
    : fail(`expected both projects mixed in the unfiltered lane, got ${JSON.stringify(allIds)}`);

  await page.selectOption('[data-eng-filter="project"]', 'clayrune');
  const clayruneIds = await rowIds(page);
  !clayruneIds.includes('conv-7') && clayruneIds.length > 0
    ? ok(`project filter "Clayrune" hides engulfing_scanner's conv-7: ${JSON.stringify(clayruneIds)}`)
    : fail(`project filter "Clayrune" should hide conv-7, got ${JSON.stringify(clayruneIds)}`);

  await page.selectOption('[data-eng-filter="project"]', 'engulfing_scanner');
  const scannerIds = await rowIds(page);
  scannerIds.length === 1 && scannerIds[0] === 'conv-7'
    ? ok(`project filter "Engulfing scanner" shows only conv-7: ${JSON.stringify(scannerIds)}`)
    : fail(`project filter "Engulfing scanner" should show only conv-7, got ${JSON.stringify(scannerIds)}`);

  reportUncaught(pageErrors, '[project-filter]');
  await ctx.close();
}

// ── a mention with no campaign (conv-8) shows under its project with
// "No campaign" — both via the campaign filter's own dedicated option and
// in the row's own meta line. ───────────────────────────────────────────────
async function runNoCampaignRow(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToEngagement(page, {});
  await page.waitForSelector('.desk-v1-engagement', { timeout: 4000 });

  await page.selectOption('[data-eng-filter="campaign"]', '__none__');
  const ids = await rowIds(page);
  ids.length === 1 && ids[0] === 'conv-8'
    ? ok(`"No campaign" filter shows exactly conv-8: ${JSON.stringify(ids)}`)
    : fail(`"No campaign" filter should show only conv-8, got ${JSON.stringify(ids)}`);

  const metaText = (await page.$eval('[data-eng-conv="conv-8"] .desk-v1-eng-row-meta', (el) => el.textContent).catch(() => '') || '');
  /Clayrune/.test(metaText) && /No campaign/.test(metaText)
    ? ok(`conv-8's row meta names its project and reads "No campaign": "${metaText.trim()}"`)
    : fail(`conv-8's row meta wrong: ${JSON.stringify(metaText)}`);

  reportUncaught(pageErrors, '[no-campaign]');
  await ctx.close();
}

// ── the campaign Conversations tab embeds the SAME component, not a copy:
// deep-linking `engagement` with a campaignId hands off to the exact
// `.desk-v1-conv-layout` class the campaign page's own Conversations tab
// renders — never `.desk-v1-engagement`'s own row list. ────────────────────
async function runCampaignHandoff(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });

  // Path 1: campaign page's own Conversations tab click.
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.waitForSelector('.desk-v1-camp-tab', { timeout: 4000 });
  await page.click('[data-tab="conversations"]');
  await page.waitForSelector('.desk-v1-conv-layout', { timeout: 4000 });
  const tabRowIds = await page.$$eval('.desk-v1-conv-row', (els) => els.map((e) => e.dataset.convId));

  // Path 2: the engagement route itself, given the same campaignId — the
  // component's own file banner says this is a hand-off, not a second copy.
  await navToEngagement(page, { campaignId: 'camp-1' });
  await page.waitForSelector('.desk-v1-conv-layout', { timeout: 4000 });
  const noEngagementRows = await page.$('.desk-v1-eng-row');
  !noEngagementRows
    ? ok('engagement route with a campaignId renders .desk-v1-conv-layout, not .desk-v1-eng-row — a hand-off, not a copy')
    : fail('engagement route with a campaignId should not render its own .desk-v1-eng-row list');
  const directRowIds = await page.$$eval('.desk-v1-conv-row', (els) => els.map((e) => e.dataset.convId));

  JSON.stringify(tabRowIds.sort()) === JSON.stringify(directRowIds.sort()) && tabRowIds.length > 0
    ? ok(`both paths render the identical camp-1 row set: ${JSON.stringify(tabRowIds)}`)
    : fail(`campaign-tab path and engagement-route path disagree: ${JSON.stringify(tabRowIds)} vs ${JSON.stringify(directRowIds)}`);

  reportUncaught(pageErrors, '[campaign-handoff]');
  await ctx.close();
}

// ── lane switching (Incoming/Suggested/Sent) and the empty-lane state. ─────
async function runLanes(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToEngagement(page, {});
  await page.waitForSelector('.desk-v1-engagement', { timeout: 4000 });

  const incomingIds = await rowIds(page);
  incomingIds.length === 4
    ? ok(`Incoming lane (default) lists 4 rows across both projects: ${JSON.stringify(incomingIds)}`)
    : fail(`expected 4 Incoming rows, got ${incomingIds.length}: ${JSON.stringify(incomingIds)}`);

  await page.click('[data-eng-lane="suggested"]');
  const suggestedIds = await rowIds(page);
  suggestedIds.length === 1 && suggestedIds[0] === 'conv-1'
    ? ok(`Suggested lane lists conv-1 only: ${JSON.stringify(suggestedIds)}`)
    : fail(`expected Suggested lane = [conv-1], got ${JSON.stringify(suggestedIds)}`);

  await page.click('[data-eng-lane="sent"]');
  const sentIds = await rowIds(page);
  const emptyText = (await page.textContent('.desk-v1-eng-empty').catch(() => '') || '');
  sentIds.length === 0 && emptyText.length > 0
    ? ok(`Sent lane is empty and shows an explicit empty-state message: "${emptyText.trim()}"`)
    : fail(`Sent lane should be empty with an empty-state message, got ${sentIds.length} rows / "${emptyText}"`);

  reportUncaught(pageErrors, '[lanes]');
  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  for (const tone of TONES) await runHomeCount(browser, tone);
  await runProjectFilter(browser);
  await runNoCampaignRow(browser);
  await runCampaignHandoff(browser);
  await runLanes(browser);
  exitCode = bad === 0 ? 0 : 1;
} catch (e) {
  console.error('harness error:', e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad === 0 ? `\nAll checks passed.` : `\n${bad} check(s) failed.`);
process.exit(exitCode);

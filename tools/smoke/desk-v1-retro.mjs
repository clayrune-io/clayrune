#!/usr/bin/env node
/**
 * Desk v1 (MC-977, R2-15) — Retro section smoke: the Retro section at the
 * foot of the ① Goal stop's effectiveness panel (docs/THE_DESK_V1_IA_REVISION_2.md
 * §8 row "R2-15", §10 outcome learning loop in full). Covers the panel's own
 * three states — interim (numbers only, no findings), closed with proposed
 * findings (Confirm/Edit/Reject/Don't suggest again), and closed after a
 * decision (proposed count drops to 0) — plus the per-post paste-from-CSV
 * grid (§10.7) and the Home/Playbook seam `deskV1RetroFindingsToConfirm`.
 *
 * Real headless boot (real index.html + real static/js|css, no network), same
 * hermetic shape as desk-v1-results.mjs.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-retro.mjs
 * Exit 0 = every case holds; 1 = a case regressed / harness error.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { seedDeskV1Fixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const ASSETS_DIR = resolve(REPO_ROOT, 'assets');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';

const MIME = { '.webp': 'image/webp', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml' };

const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));
for (const f of readdirSync(ASSETS_DIR)) {
  const ext = f.slice(f.lastIndexOf('.'));
  if (MIME[ext]) STATIC[`/assets/${f}`] = [MIME[ext], readFileSync(resolve(ASSETS_DIR, f))];
}

const PID = 'smoke_deskv1retro';
const PROJECTS = [{
  id: PID, name: 'Desk v1 retro smoke', status: 'active', domain: 'general', emoji: '🧪',
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
  const ctx = await browser.newContext({ viewport: viewport || { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.addInitScript((ls) => {
    try { for (const k of Object.keys(ls)) localStorage.setItem(k, ls[k]); } catch (e) {}
  }, (tone && tone.ls) || {});
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', fulfillOrAbort);

  await seedDeskV1Fixtures(page);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors };
}

// Straight to ① goal via the shell route (same as desk-v1-results.mjs's own
// navToResults) — works for any campaign state, deskV1RenderResults has no
// state guard (static/js/desk-v1-results.js:228).
async function navToResults(page, campaignId) {
  await page.evaluate((id) => window.deskV1Nav('results', { campaignId: id }), campaignId);
}

function reportUncaught(pageErrors, tag) {
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail(`${tag} uncaught page error: ${e}`));
}

// ── render checks, one per tone: camp-1's interim retro (term not closed)
// starts collapsed behind "Run retro now", and after clicking it shows the
// Interim status + dimension table + the numbers-only stub, never a
// findings list (§10.1: "Interim ... never proposes findings"). ────────────
async function runToneInterimChecks(browser, tone) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, tone);
  await navToResults(page, 'camp-1');
  await page.waitForSelector('.desk-v1-retro-mount', { timeout: 8000 });

  const runBtn = await page.$('[data-retro-run]');
  runBtn
    ? ok(`[${tone.name}] interim retro starts collapsed behind "Run retro now"`)
    : fail(`[${tone.name}] "Run retro now" button missing for an unclosed term`);
  const runBtnRadius = runBtn ? parseFloat(await runBtn.evaluate((e) => getComputedStyle(e).borderRadius)) || 0 : 0;
  runBtnRadius > 0
    ? ok(`[${tone.name}] "Run retro now" computes a non-default border-radius (${runBtnRadius}px)`)
    : fail(`[${tone.name}] "Run retro now" renders as a bare unstyled <button>`);
  if (runBtn) await runBtn.click();
  await page.waitForTimeout(30);

  const status = (await page.textContent('.desk-v1-retro-status').catch(() => '') || '').trim();
  status === 'Interim'
    ? ok(`[${tone.name}] status reads "Interim" after running`)
    : fail(`[${tone.name}] status wrong: ${JSON.stringify(status)}`);

  const dimRows = await page.$$('.desk-v1-retro-dim-row');
  dimRows.length > 0
    ? ok(`[${tone.name}] dimension table renders ${dimRows.length} rows`)
    : fail(`[${tone.name}] dimension table empty`);

  const findingsSection = await page.$('.desk-v1-retro-findings');
  const stub = (await page.textContent('.desk-v1-retro-mount .desk-v1-stub-inline').catch(() => '') || '').trim();
  !findingsSection && /findings are proposed once this term closes/.test(stub)
    ? ok(`[${tone.name}] interim never proposes findings: "${stub}"`)
    : fail(`[${tone.name}] interim wrongly shows a findings list or wrong stub: ${JSON.stringify(stub)}`);

  reportUncaught(pageErrors, `[${tone.name}]`);
  await ctx.close();
}

// ── closed term, proposed findings (camp-archived-1, RETRO_CLOSED_1/F1):
// Closed status, the full §10.1 dimension table (5 rows), the per-post grid
// (12 ledger rows), and F1's Confirm/Edit/Reject/Don't-suggest-again card. ──
async function runClosedWithFindings(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToResults(page, 'camp-archived-1');
  await page.waitForSelector('.desk-v1-retro', { timeout: 8000 });

  const status = (await page.textContent('.desk-v1-retro-status').catch(() => '') || '').trim();
  status === 'Closed'
    ? ok(`closed term status reads "Closed"`)
    : fail(`closed term status wrong: ${JSON.stringify(status)}`);

  const dimRows = await page.$$('.desk-v1-retro-dim-row');
  dimRows.length === 5
    ? ok(`closed dimension table shows all 5 §10.1 rows`)
    : fail(`closed dimension table row count wrong: ${dimRows.length}`);

  const dimLabels = await page.$$eval('.desk-v1-retro-dim-label', (els) => els.map((e) => e.textContent));
  ['Piece format', 'Platform + voice', 'Posting day / time slot', 'Angle / strategy', 'Spend kind'].every((l) => dimLabels.includes(l))
    ? ok(`dimension labels match RETRO_DIMENSIONS: ${JSON.stringify(dimLabels)}`)
    : fail(`dimension labels wrong: ${JSON.stringify(dimLabels)}`);

  const gridRows = await page.$$('.desk-v1-retro-grid-row');
  gridRows.length === 12
    ? ok(`per-post grid shows all 12 ledger rows`)
    : fail(`per-post grid row count wrong: ${gridRows.length}`);

  const findingsHead = (await page.textContent('.desk-v1-retro-findings-head').catch(() => '') || '').trim();
  findingsHead === '1 proposed finding'
    ? ok(`findings header reads "${findingsHead}"`)
    : fail(`findings header wrong: ${JSON.stringify(findingsHead)}`);

  const findingText = (await page.textContent('.desk-v1-retro-finding-text').catch(() => '') || '').trim();
  /^On 𝕏 \(Ron\), post got 1\.8× the clicks per post of image \(1 campaign, n=25, low\)\.$/.test(findingText)
    ? ok(`F1's sentence renders from the structured finding: "${findingText}"`)
    : fail(`F1's sentence wrong: ${JSON.stringify(findingText)}`);

  const actionLabels = await page.$$eval('.desk-v1-retro-finding-actions button', (els) => els.map((e) => e.textContent));
  ['Confirm', 'Edit', 'Reject', 'Don’t suggest again'].every((l) => actionLabels.includes(l))
    ? ok(`finding card has all four actions: ${JSON.stringify(actionLabels)}`)
    : fail(`finding card actions wrong: ${JSON.stringify(actionLabels)}`);

  // Dave's review of 89827bad: every Retro button rendered as a bare browser
  // <button> (`.btn-secondary` has no CSS rule anywhere). Pin it so a future
  // edit can't quietly drop the `.desk-v1-retro-btn` class and regress back
  // to unstyled buttons — a real button rule always computes a non-zero
  // border-radius, a bare UA default never does.
  const radii = await page.$$eval('.desk-v1-retro-finding-actions button, [data-retro-paste-fill]',
    (els) => els.map((e) => parseFloat(getComputedStyle(e).borderRadius) || 0));
  radii.every((r) => r > 0)
    ? ok(`all ${radii.length} Retro action buttons compute a non-default border-radius: ${JSON.stringify(radii)}`)
    : fail(`some Retro action buttons render as bare unstyled <button>: ${JSON.stringify(radii)}`);

  reportUncaught(pageErrors, '[closed-findings]');
  await ctx.close();
}

// ── Confirm drops the proposed count to 0 and flips the seam other panels
// read (§10.4's Home row: "Retro ready: n findings to confirm"). ────────────
async function runConfirmFinding(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToResults(page, 'camp-archived-1');
  await page.waitForSelector('[data-finding-confirm]', { timeout: 8000 });

  const before = await page.evaluate(() => window.deskV1RetroFindingsToConfirm('camp-archived-1'));
  before === 1
    ? ok(`seam reads 1 finding to confirm before Confirm`)
    : fail(`seam wrong before Confirm: ${before}`);

  await page.click('[data-finding-confirm]');
  await page.waitForTimeout(30);

  const stub = (await page.textContent('.desk-v1-retro-findings .desk-v1-stub-inline').catch(() => '') || '').trim();
  stub === '0 proposed findings.'
    ? ok(`after Confirm, findings list reads "${stub}"`)
    : fail(`after Confirm, findings list wrong: ${JSON.stringify(stub)}`);

  const after = await page.evaluate(() => window.deskV1RetroFindingsToConfirm('camp-archived-1'));
  after === 0
    ? ok(`seam reads 0 findings to confirm after Confirm`)
    : fail(`seam wrong after Confirm: ${after}`);

  const f1State = await page.evaluate(() => window.DeskV1Fixtures.playbook.findings.find((f) => f.id === 'F1').state);
  f1State === 'confirmed'
    ? ok(`F1's fixture state flips to "confirmed"`)
    : fail(`F1's fixture state wrong: ${JSON.stringify(f1State)}`);

  reportUncaught(pageErrors, '[confirm]');
  await ctx.close();
}

// ── Don't suggest again records a permanent rejection (§10.5.3), distinct
// from a plain Reject. ───────────────────────────────────────────────────
async function runDontSuggestAgain(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToResults(page, 'camp-archived-1');
  await page.waitForSelector('[data-finding-dontsuggest]', { timeout: 8000 });

  const before = await page.evaluate(() => window.DeskV1Fixtures.playbook.rejections.length);
  await page.click('[data-finding-dontsuggest]');
  await page.waitForTimeout(30);

  const after = await page.evaluate(() => window.DeskV1Fixtures.playbook.rejections);
  after.length === before + 1 && after[after.length - 1].permanent === true
    ? ok(`Don't suggest again records a permanent rejection (${after.length} total)`)
    : fail(`Don't suggest again rejection wrong: ${JSON.stringify(after)}`);

  const f1State = await page.evaluate(() => window.DeskV1Fixtures.playbook.findings.find((f) => f.id === 'F1').state);
  f1State === 'rejected'
    ? ok(`F1's fixture state flips to "rejected"`)
    : fail(`F1's fixture state wrong: ${JSON.stringify(f1State)}`);

  reportUncaught(pageErrors, '[dont-suggest-again]');
  await ctx.close();
}

// ── Edit opens a textarea seeded with the rendered sentence; Save & Confirm
// commits the edited wording and confirms in one step (§10.4). ─────────────
async function runEditFinding(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToResults(page, 'camp-archived-1');
  await page.waitForSelector('[data-finding-edit]', { timeout: 8000 });

  await page.click('[data-finding-edit]');
  await page.waitForTimeout(30);
  const seeded = (await page.inputValue('[data-finding-editarea]').catch(() => '') || '').trim();
  seeded.startsWith('On \u{1D54F} (Ron), post got 1.8')
    ? ok(`Edit seeds the textarea with the rendered sentence: "${seeded}"`)
    : fail(`Edit textarea seed wrong: ${JSON.stringify(seeded)}`);

  await page.fill('[data-finding-editarea]', 'Custom edited wording for F1.');
  await page.click('[data-finding-edit-save]');
  await page.waitForTimeout(30);

  const f1 = await page.evaluate(() => window.DeskV1Fixtures.playbook.findings.find((f) => f.id === 'F1'));
  f1.state === 'confirmed' && f1.edited_text === 'Custom edited wording for F1.'
    ? ok(`Save & Confirm commits edited wording and confirms: ${JSON.stringify(f1.edited_text)}`)
    : fail(`Save & Confirm result wrong: ${JSON.stringify(f1)}`);

  reportUncaught(pageErrors, '[edit-finding]');
  await ctx.close();
}

// ── §10.7 per-post grid: paste-from-CSV fills outcomes in ledger order,
// short input leaves the rest at "No per-post numbers yet" (never a fake 0,
// MET-01's same rule). ───────────────────────────────────────────────────
async function runPasteGrid(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToResults(page, 'camp-archived-1');
  await page.waitForSelector('[data-retro-paste]', { timeout: 8000 });

  const before = await page.$$eval('.desk-v1-retro-grid-value', (els) => els.map((e) => e.textContent.trim()));
  before.every((v) => v === 'No per-post numbers yet')
    ? ok(`grid starts with no per-post numbers (${before.length} rows)`)
    : fail(`grid did not start empty: ${JSON.stringify(before)}`);

  const pasted = Array.from({ length: 5 }, (_, i) => 10 + i).join('\n');
  await page.fill('[data-retro-paste]', pasted);
  await page.click('[data-retro-paste-fill]');
  await page.waitForTimeout(30);

  const after = await page.$$eval('.desk-v1-retro-grid-value', (els) => els.map((e) => e.textContent.trim()));
  const filled = after.slice(0, 5);
  const rest = after.slice(5);
  filled.join(',') === '10,11,12,13,14' && rest.every((v) => v === 'No per-post numbers yet')
    ? ok(`paste-from-CSV fills the first 5 rows in order, leaves the rest unfilled: ${JSON.stringify(filled)}`)
    : fail(`paste-fill result wrong: ${JSON.stringify(after)}`);

  reportUncaught(pageErrors, '[paste-grid]');
  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  for (const tone of TONES) await runToneInterimChecks(browser, tone);
  await runClosedWithFindings(browser);
  await runConfirmFinding(browser);
  await runDontSuggestAgain(browser);
  await runEditFinding(browser);
  await runPasteGrid(browser);
  exitCode = bad === 0 ? 0 : 1;
} catch (e) {
  console.error('harness error:', e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad === 0 ? `\nAll checks passed.` : `\n${bad} check(s) failed.`);
process.exit(exitCode);

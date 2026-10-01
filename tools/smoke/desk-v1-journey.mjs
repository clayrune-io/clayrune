#!/usr/bin/env node
/**
 * Desk v1 (MC-977 IA revision, docs/THE_DESK_V1_IA_REVISION.md §5 row IA8)
 * — the acceptance journey (old T8, new path).
 *
 * ONE continuous page, driven exactly the way a user would click it (no
 * deskV1Nav shortcuts except reaching Engagement, which the real UI itself
 * only reaches via Home's own button — see navHomeToEngagement below):
 *
 *   Home -> engulfing_scanner -> new campaign (lands on the map at ① Brief,
 *   R2-18; R2-3b: no IA4 setup steps) -> left at ② Goal -> resume -> back to
 *   Brief -> forced Posy failure + Retry on the Brief stop's Suggest -> Accept all on ③ What ->
 *   ⑥ Launch -> Start -> open a piece -> What/How/When/Where -> Pause project
 *   -> Resume -> Engagement filtered to that project -> Delete a never-started
 *   Draft from its ⋯ menu (Undo).
 *
 * Each hop asserts what the user SEES (pill text, facet body copy, filtered
 * row ids, toast text) — never just that a selector exists.
 *
 * Real headless boot (real index.html + real static/js|css, no network),
 * same hermetic shape as every other desk-v1-*.mjs smoke.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-journey.mjs
 * Exit 0 = the whole journey holds; 1 = a hop regressed / harness error.
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

const PID = 'smoke_deskv1journey';
const PROJECTS = [{
  id: PID, name: 'Desk v1 journey smoke', status: 'active', domain: 'general', emoji: '🧪',
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

// kit.js reads this at module load and again whenever the Brief's agent picker
// paints; the picker offers only the ones hired on the project's roster.
const CHARACTERS = [
  { scope: 'global', name: 'claydo', agent_name: 'Claydo', avatar: '🧱' },
  { scope: 'global', name: 'dave', agent_name: 'Dave', avatar: '🛡️' },
  { scope: 'global', name: 'not-hired', agent_name: 'Not Hired', avatar: '👻' },
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
  if (path === '/api/characters') return J(CHARACTERS);
  if (path === '/api/floor') return J({ bench: [] });
  return route.abort();
}

async function newBootedPage(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 } });
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

async function pillText(page) {
  return (await page.textContent('.desk-v1-camp-state-pill').catch(() => '') || '').trim();
}

// The last campaign fixture pushed — same technique desk-v1-setup.mjs uses
// to resolve a just-created draft's id without assuming card order.
async function lastCampaignId(page) {
  return page.evaluate(() => {
    const camps = window.DeskV1Fixtures.campaigns;
    return camps[camps.length - 1].id;
  });
}

const SHOT_DIR = resolve(REPO_ROOT, 'docs', 'desk_v1', 'screens');
const text = async (page, sel) => ((await page.textContent(sel).catch(() => '')) || '').replace(/\s+/g, ' ').trim();
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const DAY = 86400000;

// Real pointer drag (same shape as desk-v1-where.mjs's `drag`): mouse down on
// the source, cross the slop, release over the target's centre. Both ends are
// scrolled into view first — the drag engine has no auto-scroll.
async function drag(page, fromSel, toSel) {
  const from = page.locator(fromSel).first();
  const to = page.locator(toSel).first();
  await to.scrollIntoViewIfNeeded();
  await from.scrollIntoViewIfNeeded();
  const fb = await from.boundingBox();
  if (!fb) throw new Error(`drag source not visible: ${fromSel}`);
  await page.mouse.move(fb.x + fb.width / 2, fb.y + fb.height / 2);
  await page.mouse.down();
  await page.mouse.move(fb.x + fb.width / 2 + 14, fb.y + fb.height / 2 + 14, { steps: 4 });
  const tb = await to.boundingBox();
  if (!tb) throw new Error(`drag target not visible: ${toSel}`);
  const body = await page.locator('#desk-v1-camp-tabbody').boundingBox();
  const top = Math.max(tb.y, body ? body.y : tb.y);
  const bottom = Math.min(tb.y + tb.height, body ? body.y + body.height : tb.y + tb.height);
  await page.mouse.move(tb.x + tb.width / 2, top + Math.min((bottom - top) / 2, 120), { steps: 10 });
  await page.waitForTimeout(40);
  await page.mouse.up();
  await page.waitForTimeout(120);
}

// Reads one campaign off the fixture store; `fn` runs in the page.
const campOf = (page, id, fn) => page.evaluate(([cid, src]) => {
  const c = window.DeskV1Fixtures.campaigns.find((x) => x.id === cid);
  return (0, eval)(`(${src})`)(c, window.DeskV1Fixtures);
}, [id, fn.toString()]);

// The campaign page's stepper is the only way between stops a user has.
async function gotoStop(page, stop) {
  await page.click(`.desk-v1-map-stop[data-stop="${stop}"]`);
  await page.waitForSelector(`.desk-v1-map-stop[data-stop="${stop}"][data-state="here"]`, { timeout: 8000 });
}

async function run(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  // ── Home -> picker -> Engulfing scanner ──────────────────────────────────
  await page.waitForSelector('.desk-v1-home-block-name[data-project-id="engulfing_scanner"]', { timeout: 8000 });
  await page.click('#desk-v1-crumb .desk-v1-projects-picker');
  await page.waitForSelector('[role="menu"] [role="menuitem"]', { timeout: 4000 });
  const pickerItems = await page.$$eval('[role="menu"] [role="menuitem"]', (els) => els.map((e) => e.textContent.trim()));
  pickerItems.includes('Engulfing scanner')
    ? ok(`Home -> Projects picker lists ${JSON.stringify(pickerItems)}`)
    : fail(`Home -> Projects picker missing Engulfing scanner: ${JSON.stringify(pickerItems)}`);
  await page.click('[role="menu"] [role="menuitem"]:has-text("Engulfing scanner")');
  await page.waitForSelector('.desk-v1-project', { timeout: 8000 });
  const projectTitle = await text(page, '.desk-v1-crumb-title, .desk-v1-project-title');
  /Engulfing scanner/.test(projectTitle)
    ? ok(`picker -> project: landed on "${projectTitle}"`)
    : fail(`picker -> project: wrong project page: ${JSON.stringify(projectTitle)}`);

  // ── New campaign: lands on the map at ① Brief ────────────────────────────
  // The fixture project hires nobody on its floor yet; standing in for "Ron
  // hired Claydo and Dave there" so the Brief's agent picker has a roster.
  await page.evaluate(() => {
    window.DeskV1Fixtures.projects.find((p) => p.id === 'engulfing_scanner').roster = ['global:claydo', 'global:dave'];
  });
  await page.click('.desk-v1-project-newcamp-btn');
  await page.waitForSelector('.desk-v1-campaign', { timeout: 8000 });
  await page.waitForSelector('.desk-v1-map-stop[data-stop="how"][data-state="here"]', { timeout: 8000 });
  const stops = await page.$$eval('.desk-v1-map-stop', (els) => els.map((e) => e.dataset.stop));
  const bodyText = await page.evaluate(() => document.body.innerText);
  JSON.stringify(stops) === JSON.stringify(['how', 'goal', 'what', 'where', 'when', 'launch']) && !/Setup\s+\d\s+of\s+3/.test(bodyText)
    ? ok(`new campaign: opens on ① Brief of ${stops.length} stops, no "Setup n of 3"`)
    : fail(`new campaign: wrong stepper ${JSON.stringify(stops)}`);
  const campA = await lastCampaignId(page);

  // ── Brief: pick the agent (R2-18's "Change agent"); the agent box names it ─
  await page.waitForFunction(() => { const s = document.querySelector('[data-how-agent]'); return s && !s.disabled && s.options.length > 1; }, null, { timeout: 6000 });
  const projSel = await page.$eval('[data-setup-project]', (s) => s.value).catch(() => null);
  projSel === 'engulfing_scanner'
    ? ok('Brief: Project select is pre-filled from the project page')
    : fail(`Brief: project select not pre-filled: ${JSON.stringify(projSel)}`);
  const agentOpts = await page.$$eval('[data-how-agent] option', (os) => os.map((o) => o.textContent.trim()));
  agentOpts.length === 4 && agentOpts[3] === '+ Create new agent' && /Claydo/.test(agentOpts[1]) && /Dave/.test(agentOpts[2])
    ? ok(`Brief: agent picker offers the project's hired agents then "+ Create new agent": ${JSON.stringify(agentOpts)}`)
    : fail(`Brief: agent picker wrong: ${JSON.stringify(agentOpts)}`);
  await page.selectOption('[data-how-agent]', 'global:dave');
  await page.waitForTimeout(80);
  const agentName = await text(page, '.desk-v1-posy-box .desk-thread-name');
  const storedAgent = await campOf(page, campA, (c) => c.how.agent);
  storedAgent === 'global:dave' && /Dave/.test(agentName)
    ? ok(`Brief: picking Dave stores how.agent and the agent box reads "${agentName}"`)
    : fail(`Brief: agent pick wrong: stored=${storedAgent} box=${JSON.stringify(agentName)}`);
  await page.fill('[data-how-strategy]', 'Show the scanner catching real engulfing setups, with the misses.');
  await page.press('[data-how-strategy]', 'Tab');

  // ── left at ② Goal -> resume ─────────────────────────────────────────────
  await page.click('[data-map-next]');
  await page.waitForSelector('.desk-v1-map-stop[data-stop="goal"][data-state="here"]', { timeout: 8000 });
  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-project', { timeout: 8000 });
  const cardWordSel = `.desk-v1-project-camp-card[data-campaign-id="${campA}"] .desk-v1-state-word`;
  const cardLabel = await text(page, cardWordSel);
  /Draft · at Goal/.test(cardLabel)
    ? ok(`left at ② Goal: project card reads "${cardLabel}"`)
    : fail(`left-at-Goal card label wrong: ${JSON.stringify(cardLabel)}`);
  await page.click(`.desk-v1-project-camp-card[data-campaign-id="${campA}"]`);
  await page.waitForSelector('.desk-v1-map-stop[data-stop="goal"][data-state="here"]', { timeout: 8000 });
  ok('resume: back on ② Goal');

  // ── Goal: accept a long goal (R2-4) ──────────────────────────────────────
  // A long horizon renews in ≤90-day terms; the deadline is ~200 days out.
  const deadline = iso(new Date(Date.now() + 200 * DAY));
  await page.fill('[data-goal-field="metric"]', 'trial downloads');
  await page.press('[data-goal-field="metric"]', 'Tab');
  await page.fill('[data-goal-field="target"]', '500');
  await page.press('[data-goal-field="target"]', 'Tab');
  await page.fill('[data-goal-field="unit"]', 'downloads');
  await page.press('[data-goal-field="unit"]', 'Tab');
  await page.selectOption('[data-goal-field="horizon"]', 'long');
  await page.fill('[data-goal-field="deadline"]', deadline);
  await page.press('[data-goal-field="deadline"]', 'Tab');
  await page.selectOption('[data-goal-field="source"]', 'manual');
  const goal = await campOf(page, campA, (c) => ({ metric: c.goal.metric, target: c.goal.target, horizon: c.goal.horizon, deadline: c.goal.deadline, source: c.goal.source }));
  goal.metric === 'trial downloads' && goal.target === 500 && goal.horizon === 'long' && goal.source === 'manual' && String(goal.deadline).slice(0, 10) === deadline
    ? ok(`Goal: a long goal accepted — ${JSON.stringify(goal)}`)
    : fail(`Goal: long goal not stored: ${JSON.stringify(goal)}`);
  await page.screenshot({ path: resolve(SHOT_DIR, 'r2_13_journey_goal_1440.png') });

  // ── Brief: Suggest — forced failure + Retry ──────────────────────────────
  await gotoStop(page, 'how');
  await page.evaluate(() => { window.__deskV1PosyForce = 'fail'; });
  await page.click('[data-how-suggest]');
  await page.waitForSelector('.desk-v1-posy-failed', { timeout: 4000 });
  const failedText = await text(page, '.desk-v1-posy-failed');
  /couldn.t finish/.test(failedText)
    ? ok(`forced agent failure: shows "${failedText}"`)
    : fail(`forced agent failure: wrong copy: ${JSON.stringify(failedText)}`);
  (await campOf(page, campA, (c) => c.state)) === 'draft'
    ? ok('forced agent failure: campaign stays Draft, nothing changed')
    : fail('forced agent failure mutated the campaign');
  await page.evaluate(() => { window.__deskV1PosyForce = undefined; });
  await page.click('[data-posy-retry]');
  await page.waitForFunction(() => /suggested 3 pieces/.test(document.body.textContent || ''), null, { timeout: 6000 });
  ok('Retry: the Suggest task completes ("suggested 3 pieces, a cadence and a placement")');

  // ── What: the suggestion is 3 pieces — accept all ────────────────────────
  await gotoStop(page, 'what');
  await page.waitForSelector('.desk-v1-camp-suggested-banner', { timeout: 4000 });
  const banner = await text(page, '.desk-v1-camp-suggested-banner');
  /3 suggested/.test(banner)
    ? ok(`What: banner reads "${banner}"`)
    : fail(`What: suggestion banner wrong: ${JSON.stringify(banner)}`);
  const noPlaybookChips = await page.$$eval('.desk-v1-suggested-list [data-because-finding]', (els) => els.length);
  noPlaybookChips === 0
    ? ok('What: no `Based on` chip yet (this project has no confirmed finding)')
    : fail(`What: ${noPlaybookChips} Based-on chip(s) with nothing confirmed`);
  await page.click('[data-suggested-accept-all]');
  await page.waitForSelector('[data-what-row]', { timeout: 4000 });
  const rows0 = await page.$$eval('[data-what-row]', (els) => els.length);
  rows0 === 3 ? ok('What: Accept all -> 3 pieces') : fail(`What: expected 3 pieces after Accept all, got ${rows0}`);

  // ── What: drop Video -> Create new -> Studio storyboard -> Render -> back ─
  await drag(page, '[data-what-type="video"]', '[data-what-list]');
  await page.waitForSelector('[data-what-create][data-kind="video"]', { timeout: 4000 });
  const sources = await page.$$eval('[data-what-create] [data-what-source]', (els) => els.map((e) => e.dataset.whatSource));
  sources.join() === 'record,upload,online,create'
    ? ok(`What: dropping Video opens the source-first card: ${sources.join(' · ')}`)
    : fail(`What: video card sources wrong: ${sources}`);
  await page.click('[data-what-source="create"]');
  await page.waitForSelector('[data-storyboard]', { timeout: 6000 });
  const sbCrumb = await text(page, '.desk-v1-crumb-title');
  const strip = await text(page, '[data-returning-to]');
  sbCrumb === 'New video · Storyboard' && /Returning to ›/.test(strip) && /What · New video/.test(strip)
    ? ok(`Create new -> storyboard: "${sbCrumb}", strip "${strip}"`)
    : fail(`storyboard header wrong: ${JSON.stringify([sbCrumb, strip])}`);
  await page.evaluate(() => document.querySelectorAll('#toast-container .toast').forEach((t) => t.remove()));
  await page.click('[data-sb-render]');
  await page.waitForSelector('[data-sb-rendering]', { timeout: 4000 });
  await page.click('.desk-v1-back');
  await page.waitForSelector('[data-what-row]', { timeout: 6000 });
  const vrow = page.locator('[data-what-row]', { hasText: 'New video' }).first();
  const vtext = ((await vrow.textContent()) || '').replace(/\s+/g, ' ');
  const vnow = await vrow.locator('[role="progressbar"]').getAttribute('aria-valuenow').catch(() => null);
  /⟳ Rendering 40%/.test(vtext) && vnow === '40'
    ? ok(`Render -> back to What: the row shows "⟳ Rendering 40%" (aria-valuenow=${vnow})`)
    : fail(`Render -> What row wrong: ${vtext} / ${vnow}`);

  // ── Where: drag a source up, then a message into its column (R2-10/R2-19) ─
  // Where owns channel placement; When only times what Where already placed.
  await gotoStop(page, 'where');
  await page.waitForSelector('.desk-v1-where[data-where]', { timeout: 6000 });
  const nSources = await page.locator('[data-where-source]').count();
  nSources > 0 ? ok(`Where: SOURCES offers the project's ${nSources} connected account(s)`) : fail('Where: no sources offered');
  const srcChannel = await page.locator('[data-where-source]').first().getAttribute('data-channel-id');
  await drag(page, `[data-where-source][data-channel-id="${srcChannel}"]`, '[data-where-messages]');
  const colSel = `.desk-v1-where-col[data-channel-id="${srcChannel}"]`;
  (await page.$(colSel)) && !(await page.$('[data-where-awaiting]'))
    ? ok(`Where: a source dragged up opened the ${srcChannel} column (Draft: no approval state)`)
    : fail(`Where: source drag did not open ${srcChannel} column`);
  const famId = await page.locator('[data-where-message]').first().getAttribute('data-family-id');
  await drag(page, `[data-where-message][data-family-id="${famId}"]`, colSel);
  const placed = await page.locator(`${colSel} [data-where-version]`).count();
  placed >= 1
    ? ok(`Where: dragging message ${famId} into the column placed ${placed} version(s)`)
    : fail(`Where: message drag placed nothing in ${srcChannel}`);
  await page.screenshot({ path: resolve(SHOT_DIR, 'r2_13_journey_where_1440.png') });

  // ── When: set the term, drag an own slot ─────────────────────────────────
  await gotoStop(page, 'when');
  await page.waitForSelector('.desk-v1-calendar', { timeout: 8000 });
  await page.fill('[data-cal-field-end]', iso(new Date(Date.now() + 60 * DAY)));
  await page.$eval('[data-cal-field-end]', (el) => el.dispatchEvent(new Event('change', { bubbles: true })));
  const dayKeys = await page.$$eval('.desk-v1-cal-row-slots .desk-v1-cal-slotcell', (els) => els.map((e) => e.dataset.dayKey));
  const cellSel = `.desk-v1-cal-row-slots .desk-v1-cal-slotcell[data-day-key="${dayKeys[4]}"]`;
  await drag(page, '[data-slot-handle]', cellSel);
  const ownChip = await page.$(`${cellSel} .desk-v1-cal-slotchip-user`);
  const ownLabel = ownChip ? await ownChip.getAttribute('aria-label') : '';
  const ownSlots = await campOf(page, campA, (c) => ((c.when && c.when.slots) || []).filter((s) => s.origin === 'user').length);
  ownChip && /your own slot/.test(ownLabel) && ownSlots === 1
    ? ok(`When: dragged an own slot onto ${dayKeys[4]} — "${ownLabel}"`)
    : fail(`When: own slot drag did not create a user slot (chip=${!!ownChip}, slots=${ownSlots})`);
  await page.screenshot({ path: resolve(SHOT_DIR, 'r2_13_journey_when_1440.png') });
// __PART2__
  reportUncaught(pageErrors, '[journey]');
  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  await run(browser);
  exitCode = bad === 0 ? 0 : 1;
} catch (e) {
  console.error('journey smoke error:', e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad === 0 ? `\nAll checks passed.` : `\n${bad} check(s) failed.`);
process.exit(exitCode);

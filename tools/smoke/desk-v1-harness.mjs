#!/usr/bin/env node
/**
 * Desk v1 (MC-977, R0 plan T0a) — shell + router smoke, flag ON, fixtures only.
 *
 * WHY THIS EXISTS BEYOND desk.mjs / boot-smoke.mjs. Neither of those turns the
 * `desk_v1` flag on, so neither would ever catch a v1 regression:
 *   1. Every pre-registered route (home, campaign, rules, review, calendar,
 *      video, conversations, results) must render SOMETHING when navigated to
 *      directly — a stub file failing to load must show the shell's honest
 *      "not built yet" placeholder, never a blank pane.
 *   2. Back must always read the REAL previous stack entry's title (ticket's
 *      "Home -> campaign -> item with '<parent>' back" requirement), not a
 *      hardcoded label.
 *   3. A1 (docs/desk_v1_r0_plan.md §13): no SVG connector paths on ANY v1
 *      route, in ANY of the three tones (ground rule 5).
 *
 * Real headless boot (real index.html + real static/js|css, no network), same
 * hermetic shape as desk.mjs.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-harness.mjs
 * Exit 0 = every route + Back + the A1 check hold in all three tones; 1 = a
 * case regressed / harness error.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

const PID = 'smoke_deskv1';
const PROJECTS = [{
  id: PID, name: 'Desk v1 smoke', status: 'active', domain: 'general', emoji: '🧪',
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

// The 8 pre-registered routes (desk-v1-shell.js ROUTES) — home and campaign
// are reached through real navigation below; the rest are item-level surfaces
// normally reached FROM a campaign, so they are entered directly via
// `deskV1Nav`, which is exactly how a later ticket's own UI will reach them.
// 'rules' is deliberately excluded (Dave's review pass 3): ROUTES['rules']
// still exists in desk-v1-shell.js so old deep links resolve (T5's "Raise
// budget…"), but deskV1RenderRules immediately deskV1PopTo('campaign') and
// opens the real rules POPOVER instead of painting a stub under a
// "‹ <campaign>" crumb — it never has the page shape this loop asserts, so
// it gets its own check below instead.
const ITEM_ROUTES = ['review', 'calendar', 'video', 'conversations', 'results'];
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

async function runTone(browser, tone) {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();
  await page.addInitScript((ls) => {
    try { for (const k of Object.keys(ls)) localStorage.setItem(k, ls[k]); } catch (e) {}
  }, tone.ls);
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', fulfillOrAbort);

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });
  if (pageErrors.length) { pageErrors.forEach((e) => fail(`[${tone.name}] uncaught page error during boot: ${e}`)); await ctx.close(); return; }
  ok(`[${tone.name}] app booted clean with desk_v1=true`);

  await page.click('.sidebar-item[data-nav="social"]');
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  ok(`[${tone.name}] flag on: the Desk opens the v1 shell, not the legacy tabs`);

  const legacyTabs = await page.$('#desk-tabs .desk-tab');
  if (!legacyTabs) ok(`[${tone.name}] no legacy #desk-tabs rendered alongside v1`);
  else fail(`[${tone.name}] legacy desk-tabs rendered even though desk_v1 is on`);

  // ── Home: root of the stack, no Back ─────────────────────────────────────
  const homeTitle = await page.textContent('.desk-v1-crumb-title');
  const homeBack = await page.$('.desk-v1-back');
  if ((homeTitle || '').trim() === 'Home' && !homeBack) {
    ok(`[${tone.name}] Home is the stack root: no Back button`);
  } else {
    fail(`[${tone.name}] Home crumb wrong: title=${JSON.stringify(homeTitle)}, hasBack=${!!homeBack}`);
  }
  const homeToolsEmpty = await page.$eval('#desk-v1-crumb-tools', (el) => el.children.length === 0).catch(() => null);
  if (homeToolsEmpty) ok(`[${tone.name}] Home: #desk-v1-crumb-tools stays empty (one-row crumb, T3's optional slot unused)`);
  else fail(`[${tone.name}] Home: #desk-v1-crumb-tools is not empty: ${JSON.stringify(homeToolsEmpty)}`);
  const homeLink = await page.$('.desk-v1-stub-link');
  if (homeLink) ok(`[${tone.name}] Home renders the fixture campaign as a link`);
  else fail(`[${tone.name}] Home did not render the fixture campaign`);

  // ── Home -> campaign: skeleton's 5 slots + Back reads "‹ Home" ──────────
  await page.click('.desk-v1-stub-link');
  await page.waitForSelector('.desk-v1-campaign', { timeout: 8000 });
  const campSlots = await page.$$eval(
    '.desk-v1-camp-summary, .desk-v1-camp-tabstrip, .desk-v1-camp-tabbody, .desk-v1-camp-rightcol, .desk-v1-camp-addtray',
    els => els.length);
  if (campSlots === 5) ok(`[${tone.name}] campaign renders all 5 skeleton slots`);
  else fail(`[${tone.name}] expected 5 campaign slots, got ${campSlots}`);
  const campBack = await page.textContent('.desk-v1-back').catch(() => null);
  const campTitle = await page.textContent('.desk-v1-crumb-title');
  if ((campBack || '').includes('Home') && (campTitle || '').includes('Windows beta testers')) {
    ok(`[${tone.name}] campaign Back reads "${campBack.trim()}", title is the real campaign name`);
  } else {
    fail(`[${tone.name}] campaign crumb wrong: back=${JSON.stringify(campBack)}, title=${JSON.stringify(campTitle)}`);
  }
  // T2a (frame 12a): the name is not duplicated in the summary — it's
  // already the crumb title above — so resolve against the goal text
  // instead, which IS rendered there.
  const summaryText = await page.textContent('#desk-v1-camp-summary');
  if ((summaryText || '').includes('tester signups')) ok(`[${tone.name}] summary slot resolves the fixture campaign`);
  else fail(`[${tone.name}] summary slot did not resolve the campaign: ${JSON.stringify(summaryText)}`);
  const campToolsEmpty = await page.$eval('#desk-v1-crumb-tools', (el) => el.children.length === 0).catch(() => null);
  if (campToolsEmpty) ok(`[${tone.name}] campaign: #desk-v1-crumb-tools stays empty (one-row crumb)`);
  else fail(`[${tone.name}] campaign: #desk-v1-crumb-tools is not empty: ${JSON.stringify(campToolsEmpty)}`);

  // Dave's review (T2a pass 2/4): app.css's .agent-line is styled for
  // terminal-style agent transcripts (JetBrains Mono) — every Desk v1
  // caller that reuses it (kit's Posy box, T7's Results read) is product
  // copy, not a log line, so desk-v1.css's `.desk-v1-shell .agent-line`
  // rule must win. Checked once, here, rather than per-surface.
  const posyLineFont = await page.$eval('.desk-v1-shell .agent-line', (el) => getComputedStyle(el).fontFamily).catch(() => null);
  if (posyLineFont && !/mono/i.test(posyLineFont)) ok(`[${tone.name}] Desk v1 .agent-line uses the body font, not monospace: "${posyLineFont}"`);
  else fail(`[${tone.name}] Desk v1 .agent-line font-family: ${JSON.stringify(posyLineFont)}`);

  // Back from campaign returns to Home.
  await page.click('.desk-v1-back');
  await page.waitForFunction(() => (document.querySelector('.desk-v1-crumb-title') || {}).textContent === 'Home', null, { timeout: 5000 });
  ok(`[${tone.name}] Back from campaign lands on Home`);

  // ── Every item-level route: reached from the campaign that is its declared
  // parent (ROUTES[route].parent === 'campaign' in desk-v1-shell.js) — the
  // real "Home -> campaign -> item" stack the ticket specifies, not a bare
  // jump from Home. The shell builds Back from the ACTUAL previous stack
  // entry, not the route table's parent field, so skipping the campaign hop
  // here would assert a stack no real caller ever produces. ──
  for (const route of ITEM_ROUTES) {
    await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
    await page.waitForTimeout(30);
    await page.evaluate((r) => window.deskV1Nav(r, { campaignId: 'camp-1' }), route);
    await page.waitForTimeout(30);
    const title = (await page.textContent('.desk-v1-crumb-title').catch(() => '') || '').trim();
    const bodyText = await page.textContent('#desk-v1-body').catch(() => '');
    const notBuilt = /is not built yet/.test(bodyText || '');
    if (route === 'review') {
      // T3 shell polish: review's own crumb-title is intentionally empty —
      // its doc label/count + controls render into the shell's
      // #desk-v1-crumb-tools slot instead (frame 12b's one row). See the
      // ROUTES.review comment in desk-v1-shell.js.
      const toolsText = (await page.textContent('#desk-v1-crumb-tools').catch(() => '') || '');
      if (title === '' && /to review/.test(toolsText) && !notBuilt) {
        ok(`[${tone.name}] route "review" renders its own stub, crumb-title intentionally empty, doc label/count lives in #desk-v1-crumb-tools`);
      } else {
        fail(`[${tone.name}] route "review" did not render correctly: title=${JSON.stringify(title)}, toolsText=${JSON.stringify(toolsText)}, fellBackToShellStub=${notBuilt}`);
      }
    } else if (title.toLowerCase() === route && !notBuilt) {
      ok(`[${tone.name}] route "${route}" renders its own stub (not the shell's fallback)`);
      const toolsEmpty = await page.$eval('#desk-v1-crumb-tools', (el) => el.children.length === 0).catch(() => null);
      if (toolsEmpty) ok(`[${tone.name}] route "${route}": #desk-v1-crumb-tools stays empty (one-row crumb)`);
      else fail(`[${tone.name}] route "${route}": #desk-v1-crumb-tools is not empty: ${JSON.stringify(toolsEmpty)}`);
    } else {
      fail(`[${tone.name}] route "${route}" did not render correctly: title=${JSON.stringify(title)}, fellBackToShellStub=${notBuilt}`);
    }
    const back = (await page.textContent('.desk-v1-back').catch(() => '') || '').trim();
    if (back.includes('Windows beta testers')) {
      ok(`[${tone.name}] "${route}" Back reads "${back}"`);
    } else {
      fail(`[${tone.name}] "${route}" Back wrong: ${JSON.stringify(back)}`);
    }
    await page.click('.desk-v1-back'); // item -> campaign
    await page.waitForTimeout(30);
    await page.click('.desk-v1-back'); // campaign -> home, reset for the next route
    await page.waitForTimeout(30);
  }

  // ── 'rules' is not a page (Dave's review pass 3): navigating to it must
  // bounce straight back to the campaign page and open the real popover, with
  // Back reading the campaign's actual parent (Home) — never a "‹ Rules"
  // crumb or a bare stub. ───────────────────────────────────────────────────
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.waitForTimeout(30);
  await page.evaluate(() => window.deskV1Nav('rules', { campaignId: 'camp-1' }));
  await page.waitForSelector('.desk-v1-rules-pop', { timeout: 4000 }).catch(() => {});
  const rulesTitle = (await page.textContent('.desk-v1-crumb-title').catch(() => '') || '').trim();
  const rulesPopOpen = !!(await page.$('.desk-v1-rules-pop'));
  if (rulesPopOpen && rulesTitle.includes('Windows beta testers')) {
    ok(`[${tone.name}] "rules" bounces back to the campaign page and opens the popover, not a page`);
  } else {
    fail(`[${tone.name}] "rules" did not land on the campaign page + popover: popoverOpen=${rulesPopOpen}, title=${JSON.stringify(rulesTitle)}`);
  }
  const rulesBack = (await page.textContent('.desk-v1-back').catch(() => '') || '').trim();
  if (rulesBack.includes('Home')) {
    ok(`[${tone.name}] "rules" Back crumb names the campaign's real parent: "${rulesBack}"`);
  } else {
    fail(`[${tone.name}] "rules" Back crumb wrong: ${JSON.stringify(rulesBack)}`);
  }
  await page.keyboard.press('Escape');
  await page.click('.desk-v1-back'); // campaign -> home, reset for the A1 sweep below
  await page.waitForTimeout(30);

  // ── A1: no SVG connector paths on ANY v1 route, in this tone ─────────────
  // Re-visit every route (home already current after the loop's last Back)
  // and check the whole modal, not just the body, so a connector added to the
  // crumb or a future header would still be caught.
  const routesToCheck = ['home', 'campaign', ...ITEM_ROUTES];
  let svgFound = 0;
  for (const route of routesToCheck) {
    if (route === 'home') await page.evaluate(() => { while (true) { const btn = document.querySelector('.desk-v1-back'); if (!btn) break; btn.click(); } });
    else await page.evaluate((r) => window.deskV1Nav(r, { campaignId: 'camp-1' }), route);
    await page.waitForTimeout(20);
    svgFound += await page.$$eval('.modal-window[data-modal-id="__desk"] svg path', els => els.length);
  }
  if (svgFound === 0) ok(`[${tone.name}] A1 holds: no SVG connector paths on any of the ${routesToCheck.length} v1 routes`);
  else fail(`[${tone.name}] A1 violated: ${svgFound} SVG <path> element(s) found across v1 routes`);

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail(`[${tone.name}] uncaught page error: ${e}`));

  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  for (const tone of TONES) await runTone(browser, tone);
  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error('❌ FAIL — smoke harness error: ' + (e && e.message ? e.message : e));
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}

console.log(bad
  ? `\n❌ FAIL — ${bad} Desk v1 check(s) regressed.`
  : '\n✅ PASS — Desk v1 shell: every route renders its stub, Back always names the real parent, A1 holds in all three tones.');
process.exit(exitCode);

#!/usr/bin/env node
/**
 * Desk v1 (MC-977, R2-6) — the ② How stop of the campaign map
 * (docs/THE_DESK_V1_IA_REVISION_2.md §11 amended row R2-6, §11.3 item 4).
 *
 * Drives camp-1 (Active, `approval.bounds` snapshot already recorded) through:
 *   How stop renders (angle/strategy/never claim/budget), no agent picker and
 *   no /api/characters request from this panel -> Never claim survives a stop
 *   switch -> a $60 own budget reads as the campaign's cap, no project pool
 *   (Presence retired) -> Suggest What/When/Where -> Working ->
 *   Ready -> ③ shows "3 suggested" -> Accept all creates 3 Planned pieces ->
 *   ④ shows the suggested cadence -> ⑤ shows the suggested placement ->
 *   forced Posy failure -> Retry recovers -> budget own $50 on the Active
 *   campaign flips ⑥ Launch to "Awaiting approval" -> lowering to $40 does
 *   not clear it.
 *
 * R2-17 (§10.3, §8 row R2-17): Suggest results carry `because`. Default
 * fixtures: F6 confirmed -> `Based on F6 >`; F5 (rejected) + F99 (unknown) ->
 * no chip; `untested` -> `Trying`; the Tue 09:00 slot cites F3 which is
 * rejected -> no chip. Then F3 is confirmed: the slot shows `Based on F3 >`,
 * which opens F3's evidence on the project Playbook, and accepting that slot
 * on Active camp-1 (within bounds) keeps approval (no Awaiting approval).
 *
 * Real headless boot (real index.html + real static/js|css, no network), same
 * hermetic shape as every other desk-v1-*.mjs smoke.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-how.mjs
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

const PID = 'smoke_deskv1how';
const PROJECTS = [{
  id: PID, name: 'Desk v1 how smoke', status: 'active', domain: 'general', emoji: '🧪',
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
// desk-v1-kit.js fetches this at module load and again each time the Brief's
// agent picker paints (R2-18 `projectAgentChoices`). The fixture project's
// roster is ['global:claydo', 'global:dave'], desk_agent 'global:claydo'.
const CHARACTERS = [
  { scope: 'global', name: 'claydo', agent_name: 'Claydo', avatar: '🧱' },
  { scope: 'global', name: 'dave', agent_name: 'Dave', avatar: '🛡️' },
  { scope: 'global', name: 'not-hired', agent_name: 'Not Hired', avatar: '👻' },
];

let charReqsAfterHow_ = 0;
// The one Agent picker (Brief > Campaign card): rows of the open listbox.
const pickerRows = (page) => page.$$eval('.desk-v1-agentlist [role="option"]', (rs) => rs.map((r) => ({ id: r.dataset.agentId, t: r.textContent.replace(/\s+/g, ' ').trim(), sel: r.getAttribute('aria-selected') === 'true', fig: !!r.querySelector('.av') })));
const openAgentPicker = async (page) => { await page.click('[data-how-agent]'); await page.waitForSelector('.desk-v1-agentlist [role="option"]', { timeout: 3000 }); };

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
  // §11.3 item 4: "the How panel makes no /api/characters request" — tracked
  // for the WHOLE page (kit.js's own one-shot boot fetch is expected and
  // happens before any How-stop nav below), so callers diff a count taken
  // right before entering How against one taken right after.
  const charRequests = [];
  page.on('request', (req) => { if (new URL(req.url()).pathname === '/api/characters') charRequests.push(req.url()); });
  await page.route('**/*', fulfillOrAbort);

  await seedDeskV1Fixtures(page);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  // nextToastText below needs a call log, not a live-DOM count: a toast
  // self-removes ~5s after it fires, so a slow-resolving task (Retry's ~1.5-4s
  // Working phase, arriving well after the PREVIOUS ask's still-showing toast
  // from earlier in this same run) can let the old one dismiss before the new
  // one lands — the DOM count dips back through the "before" baseline instead
  // of ever exceeding it. Hooking the one function every toast already routes
  // through (DeskV1Kit.toast -> window.showToast) sidesteps the race: a call
  // log only grows.
  await page.evaluate(() => { window.__toastLog = []; const orig = window.showToast; window.showToast = (m, d) => { window.__toastLog.push(m); return orig ? orig(m, d) : undefined; }; });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors, charRequests };
}

function reportUncaught(pageErrors, tag) {
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail(`${tag} uncaught page error: ${e}`));
}

async function navToCampaign(page) {
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.waitForSelector('.desk-v1-campaign', { timeout: 8000 });
}

async function gotoStop(page, stop) {
  await page.click(`.desk-v1-map-stop[data-stop="${stop}"]`);
}

async function familyCountFor(page, campaignId) {
  return page.evaluate((cid) => window.DeskV1Fixtures.families.filter((f) => f.campaignId === cid).length, campaignId);
}

// A plain "does a toast with this text exist" check can't tell a fresh
// Suggest run's toast apart from the PREVIOUS run's — the Suggest task fires
// the identical message both times. Counting live `.toast` DOM nodes doesn't
// work either: a toast self-removes ~5s after it fires, so when the
// triggering action resolves slowly (Retry's ~1.5-4s Working phase) the
// PREVIOUS ask's still-showing toast can auto-dismiss before the new one
// lands — the DOM count dips back through the "before" baseline instead of
// ever exceeding it. `window.__toastLog` (newBootedPage's hook on
// window.showToast, the one function every toast already routes through) is
// a call log, not a live count — it only grows, immune to that dismiss race.
async function nextToastText(page, triggerFn, timeout) {
  const before = await page.evaluate(() => window.__toastLog.length);
  await triggerFn();
  await page.waitForFunction((n) => window.__toastLog.length > n, before, { timeout: timeout || 6000 });
  return (await page.evaluate(() => window.__toastLog[window.__toastLog.length - 1]) || '');
}

async function run(browser) {
  const { ctx, page, pageErrors, charRequests } = await newBootedPage(browser);
  await navToCampaign(page);

  // ── ② How stop renders angle/strategy/never-claim/budget, no agent picker ─
  const charReqsBeforeHow = charRequests.length;
  await gotoStop(page, 'how');
  await page.waitForSelector('.desk-v1-how', { timeout: 8000 });
  const strategyVal = await page.inputValue('[data-how-strategy]').catch(() => '');
  /Show the beta working end to end/.test(strategyVal)
    ? ok(`How stop: strategy textarea reads the fixture: "${strategyVal.trim()}"`)
    : fail(`How stop: strategy textarea wrong: ${JSON.stringify(strategyVal)}`);
  const neverClaimVal = await page.inputValue('[data-how-never-claim]').catch(() => '');
  /Feature completeness on ARM/.test(neverClaimVal)
    ? ok(`How stop: Never claim reads the fixture: "${neverClaimVal.trim()}"`)
    : fail(`How stop: Never claim wrong: ${JSON.stringify(neverClaimVal)}`);

  // R2-18: the agent belongs to the CAMPAIGN — the Brief offers the agents hired
  // on the project's floor (roster), never the whole /api/characters list, plus
  // "+ Create new agent" last; it defaults to the project's desk agent.
  await page.waitForFunction(() => { const s = document.querySelector('[data-how-agent]'); return s && !s.disabled; }, null, { timeout: 4000 }).catch(() => {});
  await openAgentPicker(page);
  const agentOpts = (await pickerRows(page)).map((o) => ({ v: o.id, t: o.t, sel: o.sel, fig: o.fig }));
  agentOpts.length === 4 && agentOpts[0].v === 'global:claydo' && agentOpts[1].v === 'global:dave' && agentOpts[2].v === '__hire__' && agentOpts[3].v === '__create__'
    ? ok(`Brief: agent picker lists the project's hired agents, "+ Hire an agent onto…", then "+ Create new agent": ${JSON.stringify(agentOpts.map((o) => o.t))}`)
    : fail(`Brief: agent picker options wrong: ${JSON.stringify(agentOpts)}`);
  agentOpts.some((o) => o.sel && o.v === 'global:claydo') && /project default/.test((agentOpts[0] || {}).t || '')
    ? ok('Brief: agent picker defaults to the project desk agent, labelled "(project default)"')
    : fail(`Brief: default not the desk agent: ${JSON.stringify(agentOpts)}`);
  agentOpts.slice(0, 2).every((o) => o.fig)
    ? ok('Brief: every agent row renders a figure (.av), not an avatar ref as text')
    : fail(`Brief: an agent row has no figure: ${JSON.stringify(agentOpts)}`);
  charReqsAfterHow_ = charRequests.length - charReqsBeforeHow;
  charReqsAfterHow_ >= 1
    ? ok(`Brief: agent roster is fetched live from /api/characters (${charReqsAfterHow_} request(s))`)
    : fail('Brief: no /api/characters request when painting the agent picker');

  // Picking Dave writes the campaign's own `how.agent`; the resolver now
  // prefers it over the project's presence.desk_agent; Undo restores it.
  await page.click('.desk-v1-agentlist [data-agent-id="global:dave"]');
  const resolvedDave = await page.evaluate(() => {
    const c = window.DeskV1Fixtures.campaigns.find((x) => x.id === 'camp-1');
    const p = window.DeskV1Fixtures.projects.find((x) => x.id === c.projectId);
    return { stored: c.how.agent, ref: window.DeskV1Kit.deskAgentRef({ project: p, campaign: c }), presence: p.presence.desk_agent };
  });
  resolvedDave.stored === 'global:dave' && resolvedDave.ref === 'global:dave' && resolvedDave.presence === 'global:claydo'
    ? ok('Brief: picking Dave stores how.agent and deskAgentRef resolves it BEFORE presence.desk_agent')
    : fail(`Brief: agent pick not campaign-scoped: ${JSON.stringify(resolvedDave)}`);

  const budgetNonePressed = await page.getAttribute('[data-how-budget-btn="none"]', 'aria-pressed').catch(() => '');
  budgetNonePressed === 'true'
    ? ok('How stop: budget starts at "None" (fixture default)')
    : fail(`How stop: budget source should start "none", got aria-pressed=${JSON.stringify(budgetNonePressed)}`);

  // ── Never claim survives a stop switch ───────────────────────────────────
  await page.fill('[data-how-never-claim]', 'Never claim: guaranteed uptime');
  await page.keyboard.press('Tab'); // blur fires the real 'change' exactly once
  await gotoStop(page, 'what');
  await page.waitForSelector('[data-what]', { timeout: 4000 });
  await gotoStop(page, 'how');
  await page.waitForSelector('[data-how-never-claim]', { timeout: 4000 });
  const neverClaimAfterSwitch = await page.inputValue('[data-how-never-claim]').catch(() => '');
  neverClaimAfterSwitch === 'Never claim: guaranteed uptime'
    ? ok('Never claim: survives a stop switch')
    : fail(`Never claim: lost after stop switch: ${JSON.stringify(neverClaimAfterSwitch)}`);

  // ── Presence retired (MC-977): a budget is the campaign's own amount, with no
  // project pool to read "remaining" from. $60 reads as this campaign's cap and
  // the card offers no 'project' source.
  const projectBtn = await page.$('[data-how-budget-btn="project"]');
  !projectBtn
    ? ok('How stop: no "project pool" budget source is offered')
    : fail('How stop: a project budget pool button is still rendered');
  await page.click('[data-how-budget-btn="own"]');
  await page.waitForSelector('[data-how-budget-amount]', { timeout: 4000 });
  await page.fill('[data-how-budget-amount]', '60');
  await page.keyboard.press('Tab');
  // Camp-1 is Active with an approved ceiling, so the raise keeps its confirm
  // sheet on the Brief edit (the only place the limit lives now).
  await page.waitForSelector('[data-confirm-accept]', { timeout: 3000 })
    .then(() => ok('How stop: raising an Active campaign budget still opens the widen-confirm sheet'))
    .catch(() => fail('How stop: raising an Active campaign budget showed no confirm sheet'));
  await page.click('[data-confirm-accept]').catch(() => {});
  await page.waitForSelector('[data-how-budget-hint]', { timeout: 3000 });
  const poolLine = (await page.textContent('[data-how-budget-hint]').catch(() => '') || '').trim();
  /^Up to \$60 for this campaign\./.test(poolLine) && !/remaining|earmark|\/month/.test(poolLine)
    ? ok(`How stop: $60 reads as the campaign's own cap, no pool arithmetic: "${poolLine.slice(0, 60)}"`)
    : fail(`How stop: budget line wrong: ${JSON.stringify(poolLine)}`);
  // Dave's review (2e24880e follow-up): the budget line must be reachable by
  // scrolling `.desk-v1-how-scroll` (fine) but never hidden behind the
  // Suggest footer (not fine) — scroll the inner region all the way and
  // assert the line's rect clears the footer's top edge.
  const overlapAtEnd = await page.evaluate(() => {
    const scroller = document.querySelector('.desk-v1-how-scroll');
    const hint = document.querySelector('[data-how-budget-hint]');
    const footer = document.querySelector('.desk-v1-how-suggest');
    scroller.scrollTop = scroller.scrollHeight;
    return hint.getBoundingClientRect().bottom > footer.getBoundingClientRect().top;
  });
  !overlapAtEnd
    ? ok('How stop: Budget line clears the Suggest footer once scrolled to the end')
    : fail('How stop: Budget line is still hidden behind the Suggest footer at max scroll');
  // Reset to 'none' so the later ⑥ own-budget section below starts clean.
  await page.click('[data-how-budget-btn="none"]');

  // ── Suggest What/When/Where -> Working -> Ready ──────────────────────────
  const famCountBefore = await familyCountFor(page, 'camp-1');
  const suggestToast = await nextToastText(page, async () => {
    await page.click('[data-how-suggest]');
    await page.waitForSelector('.desk-v1-posy-stage', { timeout: 3000 }).catch(() => {});
  }, 6000);
  /suggested 3 pieces, a cadence and a placement/.test(suggestToast)
    ? ok(`Suggest task: reaches Ready, toast reads: "${suggestToast.trim()}"`)
    : fail(`Suggest task: toast wrong: ${JSON.stringify(suggestToast)}`);

  // ── Dave's review (2e24880e follow-up): a Suggest task that resolves
  // while the user stayed parked on How must NOT clobber How with the
  // Content tab's HTML — `_runSuggestTask` used to guard its repaint on
  // "is `_st.el` still in the DOM", true even while How owns that same
  // shared node, so this used to fail. Still on How (no nav since the
  // click above) — Strategy + the Ready line must both still be showing.
  const stillOnHowStrategy = await page.inputValue('[data-how-strategy]').catch(() => '');
  /Show the beta working end to end/.test(stillOnHowStrategy)
    ? ok(`Suggest task (parked on How): Strategy textarea still showing: "${stillOnHowStrategy.trim()}"`)
    : fail(`Suggest task (parked on How): Strategy textarea lost/clobbered: ${JSON.stringify(stillOnHowStrategy)}`);
  const readyLine = (await page.textContent('.desk-v1-posy-status').catch(() => '') || '').trim();
  /answered; nothing changed/.test(readyLine)
    ? ok(`Suggest task (parked on How): Ready line reads "${readyLine}"`)
    : fail(`Suggest task (parked on How): Ready line missing/wrong: ${JSON.stringify(readyLine)}`);
  const contentTabLeaked = await page.$('.desk-v1-camp-content');
  !contentTabLeaked
    ? ok('Suggest task (parked on How): no Content-tab markup leaked into the How panel')
    : fail('Suggest task (parked on How): Content-tab markup clobbered the How panel');

  // ── ③ What: "3 suggested" banner + Accept all -> 3 Planned pieces ───────
  await gotoStop(page, 'what');
  await page.waitForSelector('.desk-v1-camp-suggested-banner', { timeout: 4000 });
  const bannerText = (await page.textContent('.desk-v1-camp-suggested-banner').catch(() => '') || '').trim();
  /3 suggested/.test(bannerText)
    ? ok(`③ What: suggested banner reads "${bannerText}"`)
    : fail(`③ What: suggested banner wrong: ${JSON.stringify(bannerText)}`);

  // R2-17: chips per suggested piece, default fixtures.
  const whatChips = await page.$$eval('[data-suggested-item]', (els) => els.map((e) => ({
    based: Array.from(e.querySelectorAll('[data-because-finding]')).map((b) => b.textContent.trim()),
    trying: Array.from(e.querySelectorAll('[data-because-trying]')).map((b) => b.textContent.trim()),
  })));
  whatChips.length === 3 && whatChips[0].based.length === 1 && /^Based on F6\s*›$/.test(whatChips[0].based[0])
    ? ok('R2-17 ③: piece 1 (confirmed F6) shows `Based on F6 ›`')
    : fail(`R2-17 ③: piece 1 chips wrong: ${JSON.stringify(whatChips)}`);
  whatChips[1] && !whatChips[1].based.length && !whatChips[1].trying.length
    ? ok('R2-17 ③: piece 2 cites nonexistent F5 + unknown F99 -> no chip')
    : fail(`R2-17 ③: piece 2 should show no chip: ${JSON.stringify(whatChips[1])}`);
  whatChips[2] && whatChips[2].trying.length === 1 && /Trying: untested/.test(whatChips[2].trying[0])
    ? ok('R2-17 ③: piece 3 (`untested`) shows `Trying: untested`')
    : fail(`R2-17 ③: piece 3 chips wrong: ${JSON.stringify(whatChips[2])}`);

  await page.click('[data-suggested-accept-all]');
  await page.waitForTimeout(50);
  const famCountAfter = await familyCountFor(page, 'camp-1');
  (famCountAfter - famCountBefore) === 3
    ? ok(`Accept all: 3 new pieces created (${famCountBefore} -> ${famCountAfter})`)
    : fail(`Accept all: expected +3 pieces, got ${famCountBefore} -> ${famCountAfter}`);
  const newIds = await page.evaluate(() => window.DeskV1Fixtures.families.filter((f) => f.id.startsWith('fam-suggest-')).map((f) => f.id));
  newIds.length === 3
    ? ok(`Accept all: all 3 new pieces have the "fam-suggest-" id prefix`)
    : fail(`Accept all: unexpected new-piece ids: ${JSON.stringify(newIds)}`);
  // R2-7: What is a flat list now (no collapsed PLANNED group to expand); each
  // row's right-hand column carries its per-version status.
  const stateWords = [];
  for (const id of newIds) {
    const w = (await page.textContent(`[data-family-id="${id}"] [data-what-status]`).catch(() => '') || '').trim();
    stateWords.push(w);
  }
  stateWords.every((w) => /◇\s*planned/.test(w))
    ? ok(`Accept all: every new piece row reads "◇ planned": ${JSON.stringify(stateWords)}`)
    : fail(`Accept all: a new piece row isn't "◇ planned": ${JSON.stringify(stateWords)}`);
  const bannerGone = await page.$('.desk-v1-camp-suggested-banner');
  !bannerGone
    ? ok('Accept all: the "N suggested" banner clears once accepted')
    : fail('Accept all: banner still showing after accept');

  // ── ④ When: cadence proposal ─────────────────────────────────────────────
  await gotoStop(page, 'when');
  await page.waitForSelector('.desk-v1-camp-suggested-banner', { timeout: 4000 });
  const whenBanner = (await page.textContent('.desk-v1-camp-suggested-banner').catch(() => '') || '').trim();
  /suggested.*3x\/week/.test(whenBanner)
    ? ok(`④ When: cadence proposal banner reads "${whenBanner}"`)
    : fail(`④ When: cadence proposal banner wrong: ${JSON.stringify(whenBanner)}`);

  // R2-17: the proposed slot cites F3, which is `rejected` in the default
  // fixtures -> the row exists (Tue, dashed on the calendar) with NO chip.
  const slotRow0 = await page.$eval('[data-suggested-slot]', (e) => ({
    text: e.textContent.replace(/\s+/g, ' ').trim(), chips: e.querySelectorAll('.desk-v1-because-chip').length, state: e.dataset.slotState,
  })).catch(() => null);
  slotRow0 && /^Tue 9:00/.test(slotRow0.text) && slotRow0.chips === 0 && slotRow0.state === 'suggested'
    ? ok(`R2-17 ④: suggested slot "${slotRow0.text}" cites rejected F3 -> no chip`)
    : fail(`R2-17 ④: suggested slot row wrong: ${JSON.stringify(slotRow0)}`);

  // ── ⑤ Where: placement suggestion ────────────────────────────────────────
  await gotoStop(page, 'where');
  await page.waitForSelector('.desk-v1-stub-inline', { timeout: 4000 });
  const whereChip = (await page.textContent('[data-where-suggest] [data-because-trying]').catch(() => '') || '').trim();
  /Trying: untested/.test(whereChip)
    ? ok('R2-17 ⑤: placement suggestion shows `Trying: untested`')
    : fail(`R2-17 ⑤: placement chip wrong: ${JSON.stringify(whereChip)}`);
  const whereText = (await page.textContent('.desk-v1-stub-inline').catch(() => '') || '').trim();
  /suggested/.test(whereText) && /@ron|ch-x-ron/.test(whereText)
    ? ok(`⑤ Where: placement suggestion reads "${whereText}"`)
    : fail(`⑤ Where: placement suggestion wrong: ${JSON.stringify(whereText)}`);

  // ── forced Posy failure -> Retry ─────────────────────────────────────────
  await gotoStop(page, 'how');
  await page.waitForSelector('.desk-v1-how', { timeout: 4000 });
  await page.evaluate(() => { window.__deskV1PosyForce = 'fail'; });
  await page.click('[data-how-suggest]');
  await page.waitForSelector('.desk-v1-posy-failed', { timeout: 6000 });
  const failedText = (await page.textContent('.desk-v1-posy-failed').catch(() => '') || '');
  /couldn.t finish/.test(failedText)
    ? ok(`forced Posy failure: shows "${failedText.trim()}"`)
    : fail(`forced Posy failure: wrong copy: ${JSON.stringify(failedText)}`);

  await page.evaluate(() => { window.__deskV1PosyForce = undefined; });
  const retryToast = await nextToastText(page, () => page.click('[data-posy-retry]'), 6000);
  /suggested 3 pieces, a cadence and a placement/.test(retryToast)
    ? ok(`Retry: recovers, reaches Ready again: "${retryToast.trim()}"`)
    : fail(`Retry: did not recover: ${JSON.stringify(retryToast)}`);

  // ── R2-17: F3 confirmed -> the Tue 09:00 slot shows `Based on F3 ›`, it
  // opens F3's evidence, accepting it keeps approval ─────────────────────────
  await page.evaluate(() => {
    const pb = window.DeskV1Fixtures.playbook;
    const f3 = pb.findings.find((f) => f.id === 'F3');
    f3.state = 'confirmed'; f3.origin = 'interactive';
    // the acceptance's rejected F5 (the agent cites it on piece 2)
    pb.findings.push({ id: 'F5', project_id: 'clayrune', scope: 'project', dimension: 'slot', arms: { a: 'Mon', b: 'Fri' },
      metric: 'clicks', effect: { ratio: 1.3, direction: 'a>b' }, evidence: [], n_total: 20, confidence: 'low',
      state: 'rejected', origin: 'interactive', decided_at: '2026-08-07T09:00:00Z', decided_by: 'ron' });
  });
  await gotoStop(page, 'how');
  await page.waitForSelector('.desk-v1-how', { timeout: 4000 });
  await nextToastText(page, () => page.click('[data-how-suggest]'), 8000);

  await gotoStop(page, 'what');
  await page.waitForSelector('[data-suggested-item]', { timeout: 4000 });
  const whatChips2 = await page.$$eval('[data-suggested-item]', (els) => els.map((e) => e.querySelectorAll('[data-because-finding]').length));
  whatChips2[1] === 0
    ? ok('R2-17 ③: a suggestion citing rejected F5 (now in the store) + unknown F99 still shows no chip')
    : fail(`R2-17 ③: rejected F5 leaked a chip: ${JSON.stringify(whatChips2)}`);

  await gotoStop(page, 'when');
  await page.waitForSelector('[data-suggested-slot]', { timeout: 4000 });
  const f3Chip = (await page.textContent('[data-suggested-slot] [data-because-finding="F3"]').catch(() => '') || '').trim();
  /^Based on F3\s*›$/.test(f3Chip)
    ? ok('R2-17 ④: with F3 confirmed, the Tue 09:00 suggestion shows `Based on F3 ›`')
    : fail(`R2-17 ④: expected "Based on F3 ›", got ${JSON.stringify(f3Chip)}`);

  await page.click('[data-suggested-slot-accept]');
  await page.waitForSelector('[data-suggested-slot][data-slot-state="accepted"]', { timeout: 3000 }).catch(() => {});
  const accState = await page.$eval('[data-suggested-slot]', (e) => e.dataset.slotState).catch(() => null);
  accState === 'accepted'
    ? ok('R2-17 ④: accepting the F3-based slot (within cadence/min-gap) marks it accepted')
    : fail(`R2-17 ④: slot not accepted: ${JSON.stringify(accState)}`);
  await gotoStop(page, 'launch');
  await page.waitForSelector('.desk-v1-launch', { timeout: 4000 });
  const launchAfterAccept = (await page.textContent('.desk-v1-launch').catch(() => '') || '').replace(/\s+/g, ' ');
  const approvalOnFile = (await page.textContent('[data-launch-approval]').catch(() => '') || '').trim();
  !/Awaiting approval/.test(launchAfterAccept) && /Approval on file/.test(approvalOnFile)
    ? ok(`R2-17 ⑥: accepting the F3-based slot keeps approval: "${approvalOnFile}"`)
    : fail(`R2-17 ⑥: accepting a within-bounds slot disturbed approval: awaiting=${/Awaiting approval/.test(launchAfterAccept)} line=${JSON.stringify(approvalOnFile)}`);

  await gotoStop(page, 'when');
  await page.waitForSelector('[data-suggested-slot] [data-because-finding="F3"]', { timeout: 4000 });
  await page.click('[data-suggested-slot] [data-because-finding="F3"]');
  await page.waitForSelector('.desk-v1-playbook-finding[data-finding-id="F3"] [data-finding-evidence]', { timeout: 4000 }).catch(() => {});
  const f3Evidence = await page.$eval('.desk-v1-playbook-finding[data-finding-id="F3"] [data-finding-evidence]', (e) => e.textContent.replace(/\s+/g, ' ').trim()).catch(() => null);
  f3Evidence && /term 1/.test(f3Evidence)
    ? ok(`R2-17: "Based on F3 ›" opens F3's evidence on the project Playbook: "${f3Evidence}"`)
    : fail(`R2-17: F3 evidence not open on the project page: ${JSON.stringify(f3Evidence)}`);
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1', projectId: 'clayrune', panel: 'how' }));
  await page.waitForSelector('.desk-v1-how', { timeout: 4000 });

  // ── ⑥ budget own $50 on an Active campaign -> Awaiting approval ─────────
  await gotoStop(page, 'how');
  await page.waitForSelector('.desk-v1-how', { timeout: 4000 });
  await page.click('[data-how-budget-btn="own"]');
  await page.waitForSelector('[data-how-budget-amount]', { timeout: 4000 });
  await page.fill('[data-how-budget-amount]', '50');
  await page.keyboard.press('Tab'); // blur fires the real 'change' exactly once
  // The raise widens the approved ceiling, so the Brief edit asks first.
  await page.waitForSelector('[data-confirm-accept]', { timeout: 3000 }).catch(() => {});
  await page.click('[data-confirm-accept]', { timeout: 1500 }).catch(() => {});

  await gotoStop(page, 'launch');
  await page.waitForSelector('.desk-v1-map-launch', { timeout: 4000 });
  let launchStatus = (await page.textContent('.desk-v1-map-launch-status').catch(() => '') || '').trim();
  /Awaiting approval/.test(launchStatus)
    ? ok(`⑥ Launch: budget own $50 flips the panel to "${launchStatus}"`)
    : fail(`⑥ Launch: expected Awaiting approval after $50 own budget, got: ${JSON.stringify(launchStatus)}`);

  // ── lowering to $40 does not clear the Awaiting-approval state ──────────
  await gotoStop(page, 'how');
  await page.waitForSelector('[data-how-budget-amount]', { timeout: 4000 });
  await page.fill('[data-how-budget-amount]', '40');
  await page.keyboard.press('Tab');
  // $40 is still above what was approved, so it asks again; confirming it must not clear the state.
  await page.waitForSelector('[data-confirm-accept]', { timeout: 3000 }).catch(() => {});
  await page.click('[data-confirm-accept]', { timeout: 1500 }).catch(() => {});

  await gotoStop(page, 'launch');
  await page.waitForSelector('.desk-v1-map-launch', { timeout: 4000 });
  launchStatus = (await page.textContent('.desk-v1-map-launch-status').catch(() => '') || '').trim();
  /Awaiting approval/.test(launchStatus)
    ? ok(`⑥ Launch: lowering the budget to $40 does NOT clear "Awaiting approval": "${launchStatus}"`)
    : fail(`⑥ Launch: lowering to $40 wrongly cleared Awaiting approval: ${JSON.stringify(launchStatus)}`);

  reportUncaught(pageErrors, '[how]');
  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  await run(browser);
  exitCode = bad === 0 ? 0 : 1;
} catch (e) {
  console.error('how smoke error:', e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad === 0 ? `\nAll checks passed.` : `\n${bad} check(s) failed.`);
process.exit(exitCode);

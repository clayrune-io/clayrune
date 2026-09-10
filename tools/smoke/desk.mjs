#!/usr/bin/env node
/**
 * The Desk (docs/THE_DESK_SPEC.md) end-to-end: the sidebar opens a four-surface
 * workspace, each surface renders its own thing, and the Queue surface hosts
 * cross-social.js's real rows.
 *
 * WHY THIS EXISTS BEYOND boot-smoke. Three of the Desk's failure modes are
 * silent — the page still boots, nothing throws, and the control just does
 * nothing:
 *
 *   1. desk.js is an ES MODULE, so `onclick="deskTab('queue')"` resolves against
 *      the GLOBAL object at click time. A missing `window.` bridge means the tab
 *      is dead with no exception. inline-handler-scope-check.mjs catches the
 *      static case; this catches the "it's bridged but wired to the wrong
 *      render" case by actually clicking every tab.
 *   2. The Queue surface REUSES cross-social.js's rows rather than forking them,
 *      which only works because `_hydrateAllSocial` is bridged onto window. If
 *      that bridge is dropped the Queue silently renders empty forever.
 *   3. `_preserveOpenSocial` in index.html had to learn about `__desk`, or every
 *      30s poll empties the Queue tab under the user. Asserted by re-rendering
 *      after a simulated refresh.
 *
 * Real headless boot (real index.html + real static/js/*.js, no network), same
 * hermetic shape as drag-to-hire.mjs.
 *
 * RUN
 *   cd tools/smoke && node desk.mjs
 * Exit 0 = all cases behave; 1 = a case regressed / harness error.
 */
import { readFileSync, readdirSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const SHOT_DIR = process.env.MC_SMOKE_SHOT_DIR || '';
if (SHOT_DIR) mkdirSync(SHOT_DIR, { recursive: true });

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

function fixtureProject(id, name, extra = {}) {
  return {
    id, name, status: 'active', domain: 'general', emoji: '🧪',
    description: '', summary: '', current_task: 'Idle', next_action: '',
    blocked: false, blocked_reason: null, activity_log: [], backlog: [],
    project_path: '/smoke/' + id, last_updated: '2026-09-09T00:00:00Z',
    last_updated_relative: 'today', last_completed: null, live_agent: null,
    display_order: 0, provider: 'claude', use_streaming_agent: true,
    distiller_mode: 'proposed', distiller_min_recurrence: 3,
    distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
    distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
    distiller_skip_errors: true, roster: [], ...extra,
  };
}

const PID = 'smoke_desk';
const PROJECTS = [
  fixtureProject(PID, 'Desk Project', { social_pending_count: 2 }),
  fixtureProject('smoke_quiet', 'Quiet Project'),
];

const QUEUE = [
  { id: 'd1', project_id: PID, platform: 'x', status: 'pending', body: 'Shipped drag-to-hire today.',
    originated: false, note: '', created_at: '2026-09-09T10:00:00Z',
    voice: 'ron', signal_id: 'sig-0',
    teaching: 'Ships-beat-promises angle. X rewards a concrete claim with a number; '
      + 'it is competing against launch threads with no artifact behind them.' },
  { id: 'd2', project_id: PID, platform: 'linkedin', status: 'pending', body: 'Restore points now keep ten snapshots.',
    originated: false, note: '', created_at: '2026-09-09T09:00:00Z' },
];

const OVERVIEW = {
  campaigns: [{
    id: 'camp-1', title: 'Agent persistence', thesis: 'Clayrune keeps agents alive between sessions',
    agenda: 'launch window opens in three weeks', voice: 'clayrune', state: 'running',
    project_ids: [PID], planned: [], created_at: '2026-09-01T00:00:00Z',
  }],
  running: 1,
  pending_drafts: 2,
  hot_signals: [],
  recent_posts: [{
    id: 'post-1', platform: 'x', voice: 'ron', body: 'Shipped the Desk. Took four days.',
    project_id: PID, published_at: '2026-09-08T12:00:00Z', outcome: null,
  }],
  voices: ['ron', 'clayrune'],
};

// A realistic feed, not two rows: the Board's density is part of what is being
// checked. Mixed kinds and scores, because the point of the meter is that a
// scannable eye finds the two stories among twenty chores.
const SIGNALS = [
  { kind: 'release',  score: 0.90, txt: 'Shipped drag-to-hire, now live' },
  { kind: 'backlog',  score: 0.85, txt: 'BACKUP PHASE 3 - restore points + checklist UI, shipped' },
  { kind: 'backlog',  score: 0.70, txt: 'Provider quota must be visible before a run, not after it dies' },
  { kind: 'journal',  score: 0.65, txt: 'Measured: an uncapped harvest pulled 899 items in one call' },
  { kind: 'commit',   score: 0.65, txt: 'fix(floor): the Bench is draggable - it is where hireable agents are' },
  { kind: 'commit',   score: 0.55, txt: 'fix(floor): a hire drag now outlives the board it started on' },
  { kind: 'backlog',  score: 0.50, txt: 'Scheduler double-dispatch: one fire spawns two sessions' },
  { kind: 'commit',   score: 0.45, txt: 'docs(desk): lock identity split and v1 platforms' },
  { kind: 'journal',  score: 0.40, txt: 'X API went pay-per-use in Feb 2026; links cost 13x a plain post' },
  { kind: 'commit',   score: 0.35, txt: 'feat(desk): harvest the feed from real project activity' },
  { kind: 'run',      score: 0.25, txt: 'Night review completed, no blockers raised' },
  { kind: 'commit',   score: 0.20, txt: 'refactor: extract _deskSectionHTML' },
  { kind: 'commit',   score: 0.15, txt: 'test: cover the undateable-backlog-item case' },
  { kind: 'commit',   score: 0.10, txt: 'chore: bump the linter' },
  { kind: 'commit',   score: 0.05, txt: 'chore: whitespace in app.css' },
].map((s, i) => ({
  id: `sig-${i}`, project_id: PID, kind: s.kind, summary: s.txt,
  ref: `ref-${i}`, story_score: s.score,
  occurred_at: `2026-09-0${(i % 9) + 1}T08:00:00Z`, consumed_by: null,
}));

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

  let harvestCalls = 0;
  const draftCalls = [];
  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const J = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return J(PROJECTS);
    if (path === '/api/config') return J({});
    if (path === '/api/characters') return J([]);
    if (path === `/api/project/${PID}/social/queue`) return J(QUEUE);
    if (path.endsWith('/social/queue')) return J([]);
    if (path === '/api/desk/overview') return J(OVERVIEW);
    if (path === '/api/desk/signals') return J(SIGNALS);
    if (path === '/api/desk/draft' && req.method() === 'POST') {
      draftCalls.push(JSON.parse(req.postData() || '{}'));
      return J({ ok: true, signal_id: 'sig-0', voice: 'ron', platform: 'x', session_id: 's1' });
    }
    if (path === '/api/desk/signals/harvest' && req.method() === 'POST') {
      harvestCalls++;
      return J({ projects: [{ project_id: PID, commits: 3, backlog: 1 }], commits: 3, backlog: 1 });
    }
    return route.abort();
  });

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });
  if (pageErrors.length) pageErrors.forEach((e) => fail('uncaught page error during boot: ' + e));
  else ok('app booted clean, no uncaught exceptions');

  // ── The sidebar opens the Desk, not the old standalone Social pane ────────
  await page.click('.sidebar-item[data-nav="social"]');
  await page.waitForSelector('.modal-window[data-modal-id="__desk"]', { timeout: 8000 });
  ok('sidebar "The Desk" opens the __desk workspace');

  const label = await page.textContent('.sidebar-item[data-nav="social"] .si-label');
  if ((label || '').trim() === 'The Desk') ok('sidebar reads "The Desk"');
  else fail(`sidebar label is "${label}", expected "The Desk"`);

  // ── Four surfaces, and only the Queue nags ───────────────────────────────
  const tabs = await page.$$eval('#desk-tabs .desk-tab', els => els.map(e => e.textContent.trim()));
  if (tabs.length === 4) ok(`four surfaces render: ${tabs.map(t => t.split(/\s+/)[0]).join(' / ')}`);
  else fail(`expected 4 tabs, got ${tabs.length}: ${JSON.stringify(tabs)}`);

  await page.waitForFunction(
    () => document.querySelectorAll('#desk-tabs .desk-tab-badge').length > 0,
    null, { timeout: 8000 }).catch(() => {});
  const badges = await page.$$eval('.desk-tab', els =>
    els.map(e => ({ label: e.textContent.trim().split(/\s+/)[0],
                    badge: (e.querySelector('.desk-tab-badge') || {}).textContent || null })));
  const nagging = badges.filter(b => b.badge);
  if (nagging.length === 1 && nagging[0].label === 'Queue') {
    ok(`only the Queue carries a count (${nagging[0].badge}) — the Board is to be read, not actioned`);
  } else {
    fail(`expected exactly one badge on Queue, got ${JSON.stringify(nagging)}`);
  }

  // ── BOARD: a campaign leads with its thesis and its reason to run now ─────
  const boardText = await page.textContent('#desk-body');
  if (boardText.includes('Clayrune keeps agents alive')) ok('Board renders the campaign THESIS, not just its title');
  else fail('Board did not render the campaign thesis');
  if (boardText.includes('launch window opens')) ok('Board renders "why now" (the agenda note)');
  else fail('Board did not render the agenda note');

  // The feed is sorted by story value, so the chore must not lead.
  const sigOrder = await page.$$eval('.desk-signal .desk-signal-text', els => els.map(e => e.textContent.trim()));
  if (sigOrder[0] && sigOrder[0].startsWith('Shipped drag-to-hire')
      && sigOrder[sigOrder.length - 1].startsWith('chore:')) {
    ok(`feed leads with story value across ${sigOrder.length} rows — chores sink to the bottom`);
  } else {
    fail(`feed ordering wrong: leads with ${JSON.stringify(sigOrder[0])}, ends with ${JSON.stringify(sigOrder[sigOrder.length - 1])}`);
  }

  // The meter must actually ENCODE the score. It is a <span>, so if it ever
  // loses `display:block` the width is silently ignored and every bar renders
  // identical — plausible enough to ship, invisible to every other assertion.
  const widths = await page.$$eval('.desk-meter-fill', els =>
    els.map(e => e.getBoundingClientRect().width));
  if (widths.length > 2 && Math.max(...widths) - Math.min(...widths) > 10) {
    ok(`meters encode score: widths span ${Math.min(...widths).toFixed(0)}-${Math.max(...widths).toFixed(0)}px`);
  } else {
    fail(`meter widths do not vary (${JSON.stringify(widths.slice(0, 4))}) — the fill is inline again`);
  }

  // Low scorers must DIM, not vanish — nothing is hidden from the feed.
  const cold = await page.$$eval('.desk-signal.cold', els => els.length);
  if (cold > 0) ok(`${cold} low-value signals recede rather than disappear`);
  else fail('no signal was dimmed; the feed hides nothing, it de-emphasises');

  // ── QUEUE: hosts cross-social.js's REAL rows (the window bridge works) ────
  await page.click('.desk-tab:has-text("Queue")');
  await page.waitForSelector('#asl-list .social-item', { timeout: 8000 });
  const rows = await page.$$eval('#asl-list .social-item .backlog-text', els => els.map(e => e.textContent.trim()));
  if (rows.length === 2 && rows.some(r => r.includes('Shipped drag-to-hire today'))) {
    ok(`Queue hosts cross-social.js's real rows (${rows.length}) — the _hydrateAllSocial bridge holds`);
  } else {
    fail(`Queue rows wrong: ${JSON.stringify(rows)}`);
  }
  const badge = await page.textContent('#asl-list .social-project-badge');
  if ((badge || '').includes('Desk Project')) ok('Queue rows name the project they belong to');
  else fail(`project badge did not resolve: ${JSON.stringify(badge)}`);

  const teaching = await page.textContent('#asl-list .social-teaching');
  if ((teaching || '').includes('Ships-beat-promises')) {
    ok('the teaching block renders on the draft, where the decision is made');
  } else {
    fail(`teaching block missing from the queue row: ${JSON.stringify(teaching)}`);
  }

  const hasActions = await page.$('#asl-list .btn-social-release');
  if (hasActions) ok('Queue rows keep their Release / Edit / Push-back actions');
  else fail('Queue rows lost their action buttons');

  // ── A poll must not empty the Queue under the user ───────────────────────
  // _preserveOpenSocial had to learn about `__desk`; without it the next
  // fetchProjects() drops _socialQueueFull and the tab goes blank.
  await page.evaluate(async () => {
    const fresh = await (await fetch('/api/projects')).json();
    allProjects = _preserveOpenSocial(fresh);
    render();
  });
  const afterPoll = await page.$$eval('#asl-list .social-item', els => els.length);
  if (afterPoll === 2) ok('a refresh poll does NOT empty the Queue — __desk preserves hydrated rows');
  else fail(`Queue emptied on refresh: ${afterPoll} rows left, expected 2`);

  // ── CALENDAR and LEDGER each render their own thing ──────────────────────
  await page.click('.desk-tab:has-text("Calendar")');
  await page.waitForSelector('#desk-body .desk-day', { timeout: 8000 });
  const calText = await page.textContent('#desk-body');
  if (calText.includes('2026-09-08') && calText.includes('Shipped the Desk')) {
    ok('Calendar groups what went out by day');
  } else {
    fail('Calendar did not render the published post by day');
  }

  await page.click('.desk-tab:has-text("Ledger")');
  await page.waitForSelector('#desk-body .desk-ledger-row', { timeout: 8000 });
  const ledText = await page.textContent('#desk-body');
  if (ledText.includes('Took four days')) ok('Ledger renders the full published body');
  else fail('Ledger did not render the post body');

  // ── Drafting: the Desk briefs the writer, it does not generate ───────────
  await page.click('.desk-tab:has-text("Board")');
  await page.waitForSelector('.desk-signal .desk-draft-btn', { timeout: 8000 });
  await page.click('.desk-signal:first-child .desk-draft-btn');
  await page.waitForTimeout(500);
  if (draftCalls.length === 1 && draftCalls[0].voice === 'ron') {
    ok(`a signal's draft button briefs the writer (voice=${draftCalls[0].voice})`);
  } else {
    fail(`draft button did not POST /api/desk/draft: ${JSON.stringify(draftCalls)}`);
  }

  // Both voices are offered per signal, because the platform follows the voice.
  const perRow = await page.$$eval('.desk-signal:first-child .desk-draft-btn',
    els => els.map(e => e.textContent.trim()));
  if (perRow.length === 2) ok(`both voices offered per signal: ${perRow.join(' / ')}`);
  else fail(`expected two voice buttons, got ${JSON.stringify(perRow)}`);

  // ── Harvest is the one button that reaches out to the projects ───────────
  await page.click('.desk-tab:has-text("Board")');
  await page.click('.desk-harvest-btn');
  await page.waitForFunction(() => true, null, { timeout: 500 }).catch(() => {});
  await page.waitForTimeout(600);
  if (harvestCalls >= 1) ok(`"Read the projects" POSTs to /api/desk/signals/harvest (${harvestCalls}x)`);
  else fail('harvest button did not call the harvest endpoint');

  // ── And nothing in the whole surface offers to publish ───────────────────
  const bodyAll = await page.textContent('.modal-window[data-modal-id="__desk"]');
  if (!/\bPublish\b/.test(bodyAll)) {
    ok('no Publish control anywhere on the Desk — the approval gate is structural');
  } else {
    fail('a Publish control appeared on the Desk; the gate is a platform TERM, not a stage to outgrow');
  }

  // One shot per surface — the four-way split is the product, so a single
  // screenshot of the Board would not show what shipped.
  if (SHOT_DIR) {
    for (const t of ['Board', 'Queue', 'Calendar', 'Ledger']) {
      await page.click(`.desk-tab:has-text("${t}")`);
      await page.waitForTimeout(250);
      const win = await page.$('.modal-window[data-modal-id="__desk"]');
      await win.screenshot({ path: resolve(SHOT_DIR, `desk-${t.toLowerCase()}.png`) });
    }
  }

  if (pageErrors.length) pageErrors.forEach((e) => fail('uncaught page error: ' + e));
  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error('❌ FAIL — smoke harness error: ' + (e && e.message ? e.message : e));
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}

console.log(bad
  ? `\n❌ FAIL — ${bad} Desk check(s) regressed.`
  : '\n✅ PASS — the Desk: four surfaces, only the Queue nags, the Board leads with a thesis, the Queue keeps real rows across a poll, and nothing offers to publish.');
process.exit(exitCode);

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
  // Released but not yet recorded as posted. It still owes Ron the receipt, so
  // it MUST stay visible in the queue — leaving it out was the hole that made
  // the story ledger unwritable from this surface.
  { id: 'd3', project_id: PID, platform: 'x', status: 'approved', body: 'The Desk reads your projects.',
    originated: false, note: '', created_at: '2026-09-08T09:00:00Z' },
];

const OVERVIEW = {
  campaigns: [{
    id: 'camp-1', title: 'Agent persistence', thesis: 'Clayrune keeps agents alive between sessions',
    agenda: 'launch window opens in three weeks', state: 'running',
    voices: ['personal', 'product'], voice: 'personal',
    project_ids: [PID], planned: [], created_at: '2026-09-01T00:00:00Z',
  }],
  running: 1,
  pending_drafts: 2,
  hot_signals: [],
  recent_posts: [{
    id: 'post-1', platform: 'x', voice: 'ron', body: 'Shipped the Desk. Took four days.',
    project_id: PID, published_at: '2026-09-08T12:00:00Z', outcome: null,
  }],
  voices: ['personal', 'product'],
};

// A realistic feed, not two rows: the Board's density is part of what is being
// checked. Mixed kinds and scores, because the point of the meter is that a
// scannable eye finds the two stories among twenty chores.
// One voice that has learned something and one that has not. The COLD one is
// the case the seeder exists for: `voice_brief` is built out of real edits, so a
// voice with zero of them hands the writer a single line of register.
const VOICES = [
  { name: 'personal', platform: 'x', register: 'First person.', rewrites: [] },
  { name: 'product', platform: 'linkedin', register: 'Never first person.',
    rewrites: [{ before: 'a', after: 'b', at: '2026-09-01T00:00:00Z' }] },
];

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

// Header cluster fixtures (UI brief §1) — a real Desk-linked workflow +
// schedule, so the cadence chip exercises the actual lookup (a workflow
// containing a `desk_harvest` action node, joined to the schedule whose
// workflow_id points at it) rather than always falling through to the
// unset "Set a cadence ›" state.
const WORKFLOWS = [{
  id: 'wf-desk1', name: 'The Desk — weekly', enabled: true,
  nodes: [{ type: 'action', action: 'desk_harvest', name: 'harvest', x: 60, y: 60, config: {} }],
  edges: [],
}];
const SCHEDULES = [{
  id: 'sch-desk1', workflow_id: 'wf-desk1', enabled: true,
  schedule_type: 'daily', time: '07:00', days: [1], project_id: PID,
}];
const FLOOR = {
  rooms: [], quiet: [], counts: {},
  bench: [{ scope: 'global', name: 'social-media-strategist', avatar: '🧵', display: 'Posy' }],
};
// A second ledger pull wider than the overview's 10-row preview (Board §2's
// "last 30 days" panel), with one row tagged to the running campaign so its
// progress bar has a real "out" count.
const LEDGER = [
  { id: 'post-1', platform: 'x', voice: 'ron', body: 'Shipped the Desk. Took four days.',
    project_id: PID, published_at: '2026-09-08T12:00:00Z', campaign_id: null, outcome: null },
  { id: 'post-2', platform: 'linkedin', voice: 'clayrune', body: 'Restore points now keep ten snapshots.',
    project_id: PID, published_at: '2026-09-07T12:00:00Z', campaign_id: 'camp-1', outcome: null },
];

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
  const seedCalls = [];
  const draftCalls = [];
  const postedCalls = [];
  const patchCalls = [];
  const approveCalls = [];
  const reworkCalls = [];
  const repeatCheckCalls = [];
  // Stateful, unlike the other fixtures: the Queue's soft-lock (UI brief §3)
  // is a round trip — edit, then Release unlocks — and a route that always
  // hands back the original static QUEUE array can never show that working.
  const queueState = QUEUE.map(q => ({ ...q }));
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
    if (path.endsWith('/posted') && req.method() === 'POST') {
      postedCalls.push(path);
      return J({ ok: true, item: { id: 'd3', status: 'posted' }, post_id: 'post-9',
                 ledger_written: true, reactions_readable: true });
    }
    if (/\/social\/queue\/[^/]+\/approve$/.test(path) && req.method() === 'POST') {
      const id = path.split('/').slice(-2, -1)[0];
      const item = queueState.find(q => q.id === id);
      approveCalls.push(path);
      if (item) {
        item.status = 'approved';
        item.released_unedited = !item.edited;
      }
      return J({ ok: true, item: item || { id } });
    }
    // Mirrors reject_social_queue_item + dispatch_rework closely enough to
    // exercise the Thread's chain reconstruction: note lands on THIS item,
    // status flips to needs_changes, and a rework is marked in flight — the
    // successor only appears once the test pushes one into queueState itself,
    // same as the real world's rework session landing a NEW draft later.
    if (/\/social\/queue\/[^/]+\/reject$/.test(path) && req.method() === 'POST') {
      const id = path.split('/').slice(-2, -1)[0];
      const body = JSON.parse(req.postData() || '{}');
      reworkCalls.push({ id, body });
      const item = queueState.find(q => q.id === id);
      if (item) {
        item.status = 'needs_changes';
        item.note = body.note;
        item.rework_session_id = 'rw1';
      }
      return J({ ok: true, item: item || { id }, rework_dispatched: true,
                 rework_session_id: 'rw1', rework_reason: null });
    }
    if (path === `/api/project/${PID}/social/queue`) return J(queueState);
    if (path.endsWith('/social/queue')) return J([]);
    if (/\/social\/queue\/[^/]+$/.test(path) && req.method() === 'PATCH') {
      const id = path.split('/').pop();
      const body = JSON.parse(req.postData() || '{}');
      patchCalls.push({ path, body });
      const item = queueState.find(q => q.id === id);
      // Mirrors the real update_social_queue_item (project_routes.py) closely
      // enough for the soft-lock to actually flip in this harness: a real
      // body change marks the item edited, same trigger as the server.
      if (item) {
        if ('body' in body) {
          const before = item.body || '';
          const after = body.body;
          if (before.trim() !== after) {
            item.edited = true;
            item.edit_count = (item.edit_count || 0) + 1;
            item.edited_lines = (item.edited_lines || 0) + 1;
          }
          item.body = after;
        }
        if ('status' in body) item.status = body.status;
        if ('media' in body) item.media = body.media;
      }
      return J({ ok: true, item: item || { id } });
    }
    if (path === '/api/desk/overview') return J(OVERVIEW);
    if (path === '/api/desk/signals') return J(SIGNALS);
    if (path === '/api/desk/draft' && req.method() === 'POST') {
      draftCalls.push(JSON.parse(req.postData() || '{}'));
      return J({ ok: true, signal_id: 'sig-0', voice: 'ron', platform: 'x', session_id: 's1' });
    }
    if (path === '/api/desk/voices') return J(VOICES);
    if (path.endsWith('/seed') && req.method() === 'POST') {
      seedCalls.push(path);
      return J({ ok: true, seeded: true, voice: 'personal', samples: 312, session_id: 's9' });
    }
    if (path === '/api/desk/signals/harvest' && req.method() === 'POST') {
      harvestCalls++;
      return J({ projects: [{ project_id: PID, commits: 3, backlog: 1 }], commits: 3, backlog: 1 });
    }
    if (/^\/api\/desk\/platforms\//.test(path)) {
      const name = decodeURIComponent(path.split('/').pop());
      if (name === 'x') return J({ name: 'x', char_limit: 280, text: 'costs $0.015, $0.20 with a link', configured: true });
      return J({ name, char_limit: null, text: '', configured: false });
    }
    if (path === '/api/desk/repeat-check' && req.method() === 'POST') {
      repeatCheckCalls.push(JSON.parse(req.postData() || '{}'));
      return J({ repeat: false, matches: [] });
    }
    if (path === '/api/workflows') return J(WORKFLOWS);
    if (path === '/api/schedules') return J(SCHEDULES);
    if (path === '/api/floor') return J(FLOOR);
    if (path === '/api/desk/ledger') return J(LEDGER);
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

  // The MOBILE drawer is a second, separate list in index.html — it kept saying
  // "Social" for as long as the desktop sidebar said "The Desk", because the
  // rename only touched one of them and only one had a check. Ron found it on
  // his phone. Two nav lists, two assertions.
  const mLabel = await page.textContent('.mobile-drawer-item[data-nav="social"] .mdi-label');
  if ((mLabel || '').trim() === 'The Desk') ok('mobile drawer reads "The Desk" too');
  else fail(`mobile drawer label is "${mLabel}", expected "The Desk"`);

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

  // ── Header cluster (UI brief §1): cadence chip, Voices, Accounts — and the
  // OLD "What's worth saying?" / "Read the projects" buttons must be gone;
  // harvest runs on the cadence now, never a second trigger in the header. ──
  const oldButtons = await page.$$('.desk-harvest-btn, .desk-triage-btn');
  if (oldButtons.length === 0) {
    ok('the old "Read the projects" / "What\'s worth saying?" header buttons are gone');
  } else {
    fail(`${oldButtons.length} old header trigger button(s) still render`);
  }
  const cadenceText = await page.textContent('.desk-cadence-chip').catch(() => null);
  const cadencePaused = await page.$('.desk-cadence-chip.paused');
  if (cadenceText && cadenceText.includes('Runs weekly') && cadenceText.includes('07:00') && !cadencePaused) {
    ok(`the cadence chip reads the schedule linked to the Desk workflow: "${cadenceText.trim()}"`);
  } else {
    fail(`cadence chip did not resolve the linked schedule: ${JSON.stringify(cadenceText)}`);
  }
  // MC-871 rehost: the builder is an INLINE host in the project modal's
  // Workflows tab now, not a floating `__workflow_builder` modal — the chip
  // must resolve the schedule's project_id, open THAT project's modal, land
  // on its Workflows tab, and mount the linked workflow there.
  await page.click('.desk-cadence-chip');
  await page.waitForSelector(`.modal-window[data-modal-id="${PID}"] .wfb-node[data-name="harvest"]`,
    { timeout: 8000 }).catch(() => {});
  const wfProjectModalOpen = await page.$(`.modal-window[data-modal-id="${PID}"]`);
  const wfTabActive = await page.textContent(`.modal-window[data-modal-id="${PID}"] .modal-tab.active`).catch(() => null);
  const wfNodeMounted = await page.$(`#wfb-inline-host-${PID} .wfb-node[data-name="harvest"]`);
  if (wfProjectModalOpen && (wfTabActive || '').trim() === 'Workflows' && wfNodeMounted) {
    ok('clicking the cadence chip opens the linked project\'s Workflows tab with that workflow mounted');
  } else {
    fail(`cadence chip did not open the workflow builder: modalOpen=${!!wfProjectModalOpen}, `
      + `activeTab=${JSON.stringify(wfTabActive)}, nodeMounted=${!!wfNodeMounted}`);
  }
  await page.click(`.modal-window[data-modal-id="${PID}"] .modal-close`, { timeout: 2000 }).catch(() => {});

  const voicesChip = await page.$('.desk-voices-chip');
  const accountsChip = await page.textContent('.desk-accounts-chip').catch(() => null);
  if (voicesChip) ok('the Voices entry point renders in the header cluster');
  else fail('no Voices chip in the header cluster');
  if (accountsChip && accountsChip.includes('—')) {
    ok('Accounts renders honestly (em dash) — no account-inventory store exists yet');
  } else {
    fail(`Accounts chip did not render the honest placeholder: ${JSON.stringify(accountsChip)}`);
  }

  // ── BOARD: Posy's note is a real chat bubble with her roster avatar ──────
  await page.waitForSelector('.desk-note-bubble', { timeout: 8000 });
  const noteText = await page.textContent('.desk-note-bubble');
  const noteAvatar = await page.$('.desk-note-avatar .av');
  if (/draft/i.test(noteText || '') && noteAvatar) {
    ok('Posy\'s note bubble renders with a real avatar and real pending-draft count');
  } else {
    fail(`Posy's note did not render as expected: text=${JSON.stringify(noteText)}, avatar=${!!noteAvatar}`);
  }

  // ── BOARD: a campaign leads with its thesis and its reason to run now ─────
  const boardText = await page.textContent('#desk-body');
  if (boardText.includes('Clayrune keeps agents alive')) ok('Board renders the campaign THESIS, not just its title');
  else fail('Board did not render the campaign thesis');
  if (boardText.includes('launch window opens')) ok('Board renders "why now" (the agenda note)');
  else fail('Board did not render the agenda note');

  // WORTH A STORY leads with the top few by score, not the whole 15-row feed —
  // the raw feed moved behind "See all N ›" (UI brief §2).
  const storyTitles = await page.$$eval('.desk-story .desk-story-title', els => els.map(e => e.textContent.trim()));
  if (storyTitles.length > 0 && storyTitles.length <= 5 && storyTitles[0].startsWith('Shipped drag-to-hire')) {
    ok(`Worth a story shows ${storyTitles.length} top picks, leading with the highest score`);
  } else {
    fail(`Worth a story cards wrong: ${JSON.stringify(storyTitles)}`);
  }
  const seeAllBtn = await page.textContent('.desk-see-all');
  if ((seeAllBtn || '').includes(`See all ${SIGNALS.length}`)) {
    ok(`"See all ${SIGNALS.length} ›" offers the full feed as a secondary view`);
  } else {
    fail(`"See all" control missing or wrong count: ${JSON.stringify(seeAllBtn)}`);
  }

  // Open it — the full feed is sorted by story value, so the chore must not
  // lead, and this is also what exercises the same .desk-signal row markup
  // the OLD Board used to render inline (now reused, not forked, by "See all").
  await page.click('.desk-see-all');
  await page.waitForSelector('.desk-signal', { timeout: 8000 });
  const sigOrder = await page.$$eval('.desk-signal .desk-signal-text', els => els.map(e => e.textContent.trim()));
  if (sigOrder.length === SIGNALS.length && sigOrder[0].startsWith('Shipped drag-to-hire')
      && sigOrder[sigOrder.length - 1].startsWith('chore:')) {
    ok(`"See all" leads with story value across ${sigOrder.length} rows — chores sink to the bottom`);
  } else {
    fail(`feed ordering wrong: ${sigOrder.length} rows, leads with ${JSON.stringify(sigOrder[0])}, ends with ${JSON.stringify(sigOrder[sigOrder.length - 1])}`);
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

  await page.click('.desk-see-all');  // collapse it back — later Board assertions target the top picks again

  // ── QUEUE (UI brief §3): 300px list | review pane ─────────────────────────
  // Reworked wholesale from the flat cross-social.js list this used to host —
  // the redesign is the ask, so the old accordion-row assertions below are
  // REPLACED, not merely adapted, by two-pane equivalents that check the same
  // underlying behaviours (hydration bridge, receipt chain, edit-teaches-the-
  // voice, survives-a-poll) against the new markup.
  await page.click('.desk-tab:has-text("Queue")');
  await page.waitForSelector('.desk-qrow', { timeout: 8000 });
  const qrows = await page.$$eval('.desk-qrow .desk-qrow-body', els => els.map(e => e.textContent.trim()));
  if (qrows.length === 3 && qrows.some(r => r.includes('Shipped drag-to-hire today'))) {
    ok(`Queue lists all 3 pending/approved drafts (${qrows.length}) — the _hydrateAllSocial bridge holds`);
  } else {
    fail(`Queue rows wrong: ${JSON.stringify(qrows)}`);
  }

  // Oldest first: d3 (2026-09-08) precedes d2 (09-09 09:00) precedes d1 (09-09 10:00).
  const rowOrder = await page.$$eval('.desk-qrow', els => els.map(e => e.dataset.itemId));
  if (JSON.stringify(rowOrder) === JSON.stringify(['d3', 'd2', 'd1'])) {
    ok(`the list is oldest-first: ${rowOrder.join(' -> ')}`);
  } else {
    fail(`expected oldest-first d3,d2,d1 — got ${JSON.stringify(rowOrder)}`);
  }

  const projLabel = await page.textContent('.desk-qrow[data-item-id="d1"] .desk-qrow-proj');
  if ((projLabel || '').includes('Desk Project')) ok('Queue rows name the project they belong to');
  else fail(`project label did not resolve: ${JSON.stringify(projLabel)}`);

  // The oldest draft (d3, already `approved`) is selected by default (UI
  // brief §8 acceptance step 2) — and an approved draft still owes a receipt,
  // so its rail offers "Mark posted" rather than Release.
  await page.waitForSelector('.desk-review-rail', { timeout: 8000 });
  const defaultSelected = await page.getAttribute('.desk-qrow.selected', 'data-item-id');
  if (defaultSelected === 'd3') ok('the oldest draft is selected by default');
  else fail(`expected d3 selected by default, got ${JSON.stringify(defaultSelected)}`);

  const postedBtnSel = '.desk-review-rail button:has-text("Mark posted")';
  const postedBtn = await page.$(postedBtnSel);
  if (postedBtn) ok('an approved draft\'s rail offers "Mark posted" — it still owes a receipt');
  else fail('approved draft\'s rail did not offer "Mark posted"');

  // A selector-based click (not the handle above) — an async platform-rules
  // fetch triggered on selection can re-render the rail before the click
  // lands, detaching a held ElementHandle; page.click() re-queries.
  page.once('dialog', d => d.accept('https://x.com/RanLevi15/status/1'));
  await page.click(postedBtnSel);
  await page.waitForTimeout(400);
  if (postedCalls.length === 1) ok('"Mark posted" POSTs the receipt that writes the ledger');
  else fail(`Mark posted did not call the receipt route: ${JSON.stringify(postedCalls)}`);

  // ── Select d1: teaching pane is a DELIBERATE hole (step 4), not built here ─
  await page.click('.desk-qrow[data-item-id="d1"]');
  await page.waitForSelector('.desk-post-body', { timeout: 8000 });
  const bodyText = await page.textContent('.desk-post-body');
  if ((bodyText || '').includes('Shipped drag-to-hire today')) {
    ok('selecting a row loads its body into the editable preview');
  } else {
    fail(`review pane did not carry the draft body: ${JSON.stringify(bodyText)}`);
  }
  const signalLink = await page.textContent('.desk-review-signal').catch(() => null);
  if ((signalLink || '').includes('Shipped drag-to-hire')) {
    ok('the meta line links back to the originating signal');
  } else {
    fail(`signal deep-link missing: ${JSON.stringify(signalLink)}`);
  }
  // ── Thread with Posy (step 4): teaching bubble, quick-reply chips, composer ─
  const teachingBubble = await page.textContent('.desk-thread-output .agent-line:not(.agent-line-prompt)').catch(() => null);
  if ((teachingBubble || '').includes('Ships-beat-promises')) {
    ok('the teaching block renders as the thread\'s first left bubble, off the existing `teaching` field');
  } else {
    fail(`teaching bubble missing/wrong: ${JSON.stringify(teachingBubble)}`);
  }
  const chipLabels = await page.$$eval('.desk-thread-chips .agent-question-chip', els => els.map(e => e.textContent.trim()));
  if (JSON.stringify(chipLabels) === JSON.stringify(['Shorter', 'Less technical', 'Add the cost', 'Try LinkedIn voice'])) {
    ok('quick-reply chips render the fixed set (no pushback-history store to learn from yet)');
  } else {
    fail(`quick-reply chips wrong: ${JSON.stringify(chipLabels)}`);
  }
  const threadComposer = await page.$('.desk-thread .agent-task-input');
  if (threadComposer) ok('the thread composer renders — same .agent-input-row/.agent-task-input as the conversation view');
  else fail('no thread composer rendered for a pending, non-superseded draft');

  // A chip fills the composer, it does not send — same pattern as the +New
  // screen's starter chips.
  await page.click('.desk-thread-chips .agent-question-chip:has-text("Shorter")');
  const chipFilled = await page.$eval('.desk-thread .agent-task-input', el => el.value);
  if (chipFilled === 'Shorter') ok('a quick-reply chip fills the composer rather than auto-sending');
  else fail(`chip did not fill the composer: ${JSON.stringify(chipFilled)}`);
  await page.fill('.desk-thread .agent-task-input', '');

  // ── Push back on d2 (no teaching/signal — exercises the "no bubble" case
  // too) and follow the chain through to a landed revision. ──
  await page.click('.desk-qrow[data-item-id="d2"]');
  await page.waitForSelector('.desk-thread', { timeout: 8000 });
  // Selecting a row kicks off `_deskLoadPlatformRules`, an async fetch that
  // unconditionally calls `renderDesk()` on completion (same pre-existing race
  // the "Mark posted" test above already comments on). If that lands WHILE
  // the composer below is focused, `deferRepaintWhileTyping` defers it to the
  // textarea's blur — which fires from the Send button's own mousedown, right
  // before its click, and can rebuild the DOM out from under the in-flight
  // click. Draining it here (nothing is focused yet) keeps the click below
  // deterministic; this is a test-timing concern, not product behavior this
  // pass changes.
  await page.waitForTimeout(250);
  const d2Teaching = await page.$('.desk-thread-output .agent-line:not(.agent-line-prompt)');
  const d2Empty = await page.$('.desk-thread-empty');
  if (!d2Teaching && d2Empty) {
    ok('a draft with no teaching renders no fake bubble — the honest empty state instead');
  } else {
    fail(`expected the empty-thread state for d2 (no teaching field), got teaching=${!!d2Teaching}, empty=${!!d2Empty}`);
  }
  await page.fill('.desk-thread .agent-task-input', 'Cut the middle two sentences.');
  await page.click('.desk-thread button.btn-dispatch');
  await page.waitForTimeout(400);
  if (reworkCalls.length === 1 && reworkCalls[0].id === 'd2' && reworkCalls[0].body.note === 'Cut the middle two sentences.') {
    ok('sending a pushback POSTs the existing /reject route (note + dispatch, not a new write path)');
  } else {
    fail(`pushback did not call /reject as expected: ${JSON.stringify(reworkCalls)}`);
  }
  await page.waitForSelector('.desk-thread-typing', { timeout: 8000 });
  const typingText = await page.textContent('.desk-thread-typing');
  if (/revising the draft/.test(typingText || '')) {
    ok('while the rework is in flight, the shared typing indicator shows "Posy is revising the draft…"');
  } else {
    fail(`typing indicator did not render: ${JSON.stringify(typingText)}`);
  }
  const composerGoneWhilePending = await page.$('.desk-thread .agent-task-input:not([disabled])');
  if (!composerGoneWhilePending) ok('the composer disables while a rework is already in flight for this draft');
  else fail('composer stayed enabled while a rework was in flight');

  // The rework's successor is an ORDINARY new queue item (reworked_from: d2),
  // not an in-place revision — land it and confirm the thread surfaces the
  // hand-off rather than claiming something happened in place that did not.
  queueState.push({
    id: 'd2r', project_id: PID, platform: 'linkedin', status: 'pending',
    body: 'Restore points now keep ten.', originated: false, note: '',
    created_at: '2026-09-09T09:05:00Z', reworked_from: 'd2', voice: 'clayrune',
    teaching: 'Cut the qualifier — the number carries the claim on its own.',
  });
  await page.evaluate((pid) => window.refreshProjectSocialQueue(pid), PID);
  await page.evaluate(() => window.renderDesk());
  await page.waitForSelector('.desk-thread-ready', { timeout: 8000 });
  const readyText = await page.textContent('.desk-thread-ready');
  if (/revision is ready/.test(readyText || '')) {
    ok('once the successor lands, the thread says the revision is ready rather than faking an in-place update');
  } else {
    fail(`ready banner missing/wrong: ${JSON.stringify(readyText)}`);
  }
  await page.click('.desk-thread-ready a');
  await page.waitForSelector('.desk-qrow[data-item-id="d2r"].selected', { timeout: 8000 });
  const revisedBody = await page.textContent('.desk-post-body');
  if ((revisedBody || '').includes('Restore points now keep ten')) {
    ok('"open it ›" switches the review pane to the actual revised draft');
  } else {
    fail(`did not land on the revised draft: ${JSON.stringify(revisedBody)}`);
  }
  const chainBubbles = await page.$$eval('.desk-thread-output .agent-line', els => els.map(e => e.textContent.trim()));
  if (chainBubbles.some(t => t.includes('Cut the middle two sentences.'))
      && chainBubbles.some(t => t.includes('Cut the qualifier'))) {
    ok('the new item\'s thread reconstructs the FULL chain — the old pushback note plus the new teaching, ' +
       'even though they live on two different queue items, not one');
  } else {
    fail(`chain did not reconstruct across the rework: ${JSON.stringify(chainBubbles)}`);
  }
  // The OLD item (d2) is superseded now — its composer must not still invite
  // a pushback that would dispatch a second, conflicting rework.
  await page.click('.desk-qrow[data-item-id="d2"]');
  await page.waitForSelector('.desk-thread', { timeout: 8000 });
  const supersededNotice = await page.$('.desk-thread-superseded');
  const supersededComposer = await page.$('.desk-thread .agent-task-input');
  if (supersededNotice && !supersededComposer) {
    ok('the superseded original hides the composer and points at the successor instead');
  } else {
    fail(`superseded item still offered a composer or no hand-off notice: notice=${!!supersededNotice}, composer=${!!supersededComposer}`);
  }

  // Back to d1 — the rest of this run exercises the edit/release flow the
  // thread section above deliberately left (it has its own draft, d2/d2r).
  await page.click('.desk-qrow[data-item-id="d1"]');
  await page.waitForSelector('.desk-post-body', { timeout: 8000 });

  // ── The soft-lock: dimmed until edited, unlocks after a real edit ─────────
  const lockedBefore = await page.$('.desk-release-btn.locked[disabled]');
  if (lockedBefore) ok('Release is soft-locked before any edit');
  else fail('Release was not locked on an unedited draft');
  const unlockLink = await page.$('.desk-release-unlock a');
  if (unlockLink) ok('the "release unedited anyway" override link is offered while locked');
  else fail('no unedited-override link while locked');

  if (SHOT_DIR) {
    const win = await page.$('.modal-window[data-modal-id="__desk"]');
    await win.screenshot({ path: resolve(SHOT_DIR, 'desk-queue-review.png') });
  }

  // Edit in place — NO modal, autosave on blur -> record_edit (the learning loop).
  await page.click('.desk-post-body');
  await page.keyboard.press('End');
  await page.keyboard.type(' EDITED');
  // Typing must survive a forced renderDesk() the same way the old flat list
  // protected a live cursor — deferRepaintWhileTyping now guards the whole
  // #desk-body, since the editable body lives inside it.
  await page.evaluate(() => window.renderDesk && window.renderDesk());
  await page.waitForTimeout(150);
  const survived = await page.$eval('.desk-post-body', el => el.innerText).catch(() => null);
  if (survived && survived.includes('EDITED')) {
    ok('typing in the review pane survives a forced renderDesk() mid-edit');
  } else {
    fail(`edit was lost on repaint: ${JSON.stringify(survived)}`);
  }
  await page.click('.desk-queue-list-head');  // blur the contenteditable
  await page.waitForTimeout(400);
  if (patchCalls.some(c => c.path.endsWith('/social/queue/d1') && c.body.body && c.body.body.includes('EDITED'))) {
    ok('autosave on blur PATCHes /api/project/<pid>/social/queue/<id> — no new route, this is desk.record_edit\'s trigger');
  } else {
    fail(`blur did not PATCH the existing route as expected: ${JSON.stringify(patchCalls)}`);
  }

  // The edit above must have unlocked Release in this same session — the
  // fixture's mock PATCH route mirrors update_social_queue_item's real
  // edited-flag trigger for exactly this reason.
  await page.waitForSelector('.desk-release-btn:not(.locked):not([disabled])', { timeout: 8000 });
  const releaseBtn = await page.textContent('.desk-release-btn');
  if (/Release to X/.test(releaseBtn || '') && /\$0\.015|\$0\.20/.test(releaseBtn || '')) {
    ok(`Release unlocked after the edit, priced: "${releaseBtn.trim()}"`);
  } else {
    fail(`Release did not unlock/price correctly: ${JSON.stringify(releaseBtn)}`);
  }

  const checksText = await page.textContent('.desk-checks-card');
  if (repeatCheckCalls.length && /Not said before|Checking the ledger/.test(checksText || '')) {
    ok('the Checks card runs the ledger repeat-check before release');
  } else {
    fail(`Checks card did not run/render the repeat-check: ${JSON.stringify(checksText)}`);
  }

  await page.click('.desk-release-btn');
  await page.waitForTimeout(400);
  if (approveCalls.length === 1) {
    ok('Release calls the existing (non-publishing) approve route — no outbound platform call exists to make');
  } else {
    fail(`Release did not call approve as expected: ${JSON.stringify(approveCalls)}`);
  }

  // ── A poll must not empty the Queue under the user ───────────────────────
  // _preserveOpenSocial had to learn about `__desk`; without it the next
  // fetchProjects() drops _socialQueueFull and the tab goes blank.
  await page.evaluate(async () => {
    const fresh = await (await fetch('/api/projects')).json();
    allProjects = _preserveOpenSocial(fresh);
    render();
  });
  // 4, not the original 3: the Thread section above landed d2r (a real new
  // queue row, reworked_from: d2) alongside d1/d2/d3.
  const afterPoll = await page.$$eval('.desk-qrow', els => els.length);
  if (afterPoll === 4) ok('a refresh poll does NOT empty the Queue — __desk preserves hydrated rows');
  else fail(`Queue emptied on refresh: ${afterPoll} rows left, expected 4`);

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

  // ── Campaigns are creatable from the Board, not only from the API ───────
  await page.click('.desk-tab:has-text("Board")');
  const newBtn = await page.$('.desk-new-campaign');
  if (newBtn) ok('the Board offers "+ New campaign" — it was API-only until now');
  else fail('no way to create a campaign from the Board');

  // A campaign carries state, and a proposed one needs a way to start.
  const campActions = await page.$$eval('.desk-campaign-actions button',
    els => els.map(e => e.textContent.trim()));
  if (campActions.length) ok(`a campaign can be moved from the Board: ${campActions.join(' / ')}`);
  else fail('a campaign has no state actions on the Board');

  // ── Drafting: the Desk briefs the writer, it does not generate ───────────
  // (WORTH A STORY card, not the raw .desk-signal feed row — that moved
  // behind "See all" above; both share _deskDraftButtons, so this exercises
  // the same dispatch either way.)
  await page.click('.desk-tab:has-text("Board")');
  await page.waitForSelector('.desk-story .desk-draft-btn', { timeout: 8000 });
  await page.click('.desk-story:first-child .desk-draft-btn');
  await page.waitForTimeout(500);
  if (draftCalls.length === 1 && draftCalls[0].campaign_id === 'camp-1') {
    ok(`a draft carries the RUNNING campaign (voice=${draftCalls[0].voice}, `
       + `campaign=${draftCalls[0].campaign_id}) — the thesis reaches the writer`);
  } else {
    fail(`draft button did not POST /api/desk/draft: ${JSON.stringify(draftCalls)}`);
  }

  // Both voices are offered per signal, because the platform follows the voice.
  const perRow = await page.$$eval('.desk-story:first-child .desk-draft-btn',
    els => els.map(e => e.textContent.trim()));
  if (perRow.length === 2) ok(`the campaign's voices are the buttons: ${perRow.join(' / ')}`);
  else fail(`expected one button per campaign voice, got ${JSON.stringify(perRow)}`);

  // ── Harvest no longer has a header trigger to click (asserted absent,
  // above) — it runs on the cadence workflow's own Run now, server-side, via
  // mc/workflows.py's `desk_harvest` action, outside this UI entirely. What
  // stays testable here is the negative: nothing on the Board fires it on its
  // own. ──
  if (harvestCalls === 0) {
    ok('no hidden auto-harvest fires from the Board — the trigger genuinely moved to the workflow');
  } else {
    fail(`harvest fired ${harvestCalls}x with no button to cause it`);
  }

  // ── And nothing in the whole surface offers to publish ───────────────────
  const bodyAll = await page.textContent('.modal-window[data-modal-id="__desk"]');
  if (!/\bPublish\b/.test(bodyAll)) {
    ok('no Publish control anywhere on the Desk — the approval gate is structural');
  } else {
    fail('a Publish control appeared on the Desk; the gate is a platform TERM, not a stage to outgrow');
  }

  // One shot per surface — the four-way split is the product, so a single
  // screenshot of the Board would not show what shipped.
  // ── Voices: the cold start is visible, and seeding is one click ──────────
  //
  // A voice with no learned edits is GUESSING, and the panel has to say so next
  // to the button that fixes it — otherwise the honest status lives only in a
  // brief nobody reads.
  await page.click('.desk-voices-chip');
  await page.waitForSelector('.modal-window[data-modal-id="__desk_voices"]', { timeout: 8000 });
  const voiceRows = await page.$$eval('.desk-voice-row', els => els.map(e => ({
    voice: e.dataset.voice,
    learned: (e.querySelector('.desk-voice-learned') || {}).textContent.trim(),
    cold: !!e.querySelector('.desk-voice-learned.cold'),
    seedable: !!e.querySelector('.desk-seed-btn'),
  })));
  if (voiceRows.length === 2 && voiceRows.every(r => r.seedable)) {
    ok('every voice offers to learn from how the user already writes');
  } else {
    fail(`expected 2 seedable voice rows, got ${JSON.stringify(voiceRows)}`);
  }
  const coldVoices = voiceRows.filter(r => r.cold);
  if (coldVoices.length === 1 && coldVoices[0].voice === 'personal' && coldVoices[0].learned.startsWith('0 edits')) {
    ok('the voice with nothing learned is marked cold, and says "0 edits learned"');
  } else {
    fail(`expected exactly the 0-rewrite voice marked cold, got ${JSON.stringify(voiceRows)}`);
  }

  await page.click('.desk-voice-row[data-voice="personal"] .desk-seed-btn');
  await page.waitForFunction(() => !document.querySelector('.modal-window[data-modal-id="__desk_voices"]'),
                             null, { timeout: 8000 }).catch(() => {});
  if (seedCalls.length === 1 && seedCalls[0].includes('/api/desk/voices/personal/seed')) {
    ok('seeding posts to the seed route of that specific voice');
  } else {
    fail(`expected one seed POST for "personal", got ${JSON.stringify(seedCalls)}`);
  }

  // ── Focus itself survives a refresh, not just the typed text ─────────────
  //
  // Ron: "I try to add notes to Posy, but the cursor keeps jumping off that
  // window." renderDesk() rebuilds #desk-body with innerHTML, so a refresh
  // while a field has focus destroys the element being typed into. The Queue
  // section above already checks the TEXT survives; this checks FOCUS itself
  // is never lost (deferRepaintWhileTyping must skip the repaint entirely,
  // not repaint-then-refocus, or a mid-edit poll would visibly steal the
  // cursor even if the content came back).
  await page.click('.desk-tab:has-text("Queue")');
  await page.waitForSelector('.desk-post-body', { timeout: 8000 });
  await page.click('.desk-post-body');
  await page.keyboard.type(' more');
  await page.evaluate(() => window.renderDesk && window.renderDesk());
  await page.waitForTimeout(150);
  const focusState = await page.evaluate(() => {
    const el = document.querySelector('.desk-post-body');
    return { focused: document.activeElement === el, hasText: el ? el.innerText.includes('more') : false };
  });
  if (focusState.focused && focusState.hasText) {
    ok('a refresh mid-typing keeps FOCUS (not just the text) on the editable post body');
  } else {
    fail(`refresh moved focus off the field being typed into: ${JSON.stringify(focusState)}`);
  }

  if (SHOT_DIR) {
    for (const t of ['Board', 'Queue', 'Calendar', 'Ledger']) {
      await page.click(`.desk-tab:has-text("${t}")`);
      await page.waitForTimeout(250);
      const win = await page.$('.modal-window[data-modal-id="__desk"]');
      await win.screenshot({ path: resolve(SHOT_DIR, `desk-${t.toLowerCase()}.png`) });
    }
  }

  // Same noise filter every other context in this suite (and boot-smoke.mjs,
  // workflow-builder.mjs) applies: the cadence-chip fix now opens a real
  // project modal, whose first refreshModal() lazy-loads mermaid.js -- a CDN
  // import this hermetic run always aborts (no network). Expected, not a
  // regression this file introduced.
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail('uncaught page error: ' + e));
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

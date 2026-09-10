#!/usr/bin/env node
/**
 * docs/DRAG_TO_HIRE_SPEC.md end-to-end: grabbing a Floor figure dims the
 * dashboard, drops onto a project tile, and the character is hired onto
 * that project (POST .../roster/hire) — then the modal opens in Channel
 * mode with that agent's row selected.
 *
 * Real headless boot (real index.html + real static/js/*.js, no network),
 * same hermetic shape as channel-mode-roster.mjs. The drag itself is
 * simulated with real Playwright pointer input (page.mouse), not a
 * synthetic DOM event dispatch — Chromium turns mouse input into real
 * PointerEvents, so this exercises floor.js's actual pointerdown/move/up
 * handlers, not a stand-in for them.
 *
 * RUN
 *   cd tools/smoke && node drag-to-hire.mjs
 * Screenshots (drag mid-flight + post-drop channel view) go to
 * MC_SMOKE_SHOT_DIR if set, else are skipped.
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
const PID_HOME = 'smoke_home';    // where Fenn's session currently lives
const PID_TARGET = 'smoke_target';  // where Ron drops her
const PROJECTS = [
  fixtureProject(PID_HOME, 'Home Project'),
  fixtureProject(PID_TARGET, 'Target Project'),
];

const FLOOR_PAYLOAD = {
  rooms: [{
    id: PID_HOME, name: 'Home Project', emoji: '🧪', color: '',
    figures: [{
      session_id: 'sess-fenn', claude_session_id: 'csid-fenn', state: 'idle', reason: null,
      activity: '', task: 'reviewing a diff',
      character: { name: 'code-reviewer', display: 'Fenn', scope: 'global' },
      name: 'Fenn', name_from: 'character', avatar: 'fig:scholar', provider: 'claude',
      model: '', model_from: '', started_at: '', age: '5m', trigger_type: 'manual',
      hivemind_id: '', subagents: [],
    }],
  }],
  quiet: [{ id: PID_TARGET, name: 'Target Project', emoji: '🧪', color: '' }],
  bench: [], counts: { rooms: 1, figures: 1, quiet: 1, bench: 0 },
  activity_states: false, poll_seconds: 5,
};

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

  const hireCalls = [];
  const unhireCalls = [];
  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(PROJECTS) });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    // Non-empty so the composer's Persona picker has a real option to select —
    // the post-drop "is the composer armed" assertion reads the select's value,
    // and an empty list would only ever render "None".
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([
      { name: 'code-reviewer', display_name: 'code-reviewer', agent_name: 'Fenn', scope: 'global',
        description: 'reviews a diff', engine: { provider: 'claude', model: 'claude-sonnet-5' } },
    ]) });
    if (path === '/api/floor') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(FLOOR_PAYLOAD) });
    if (path === '/api/project/smoke_target/roster/hire' && req.method() === 'POST') {
      hireCalls.push(JSON.parse(req.postData() || '{}'));
      const roster = [{ character: 'global:code-reviewer', hired_at: '2026-09-09T00:00:00Z', hired_by: 'drag', removed_at: null }];
      // Reflect the hire into the project record's own `roster` field so a
      // second /api/projects poll (refreshFloor -> refreshSilent, if any)
      // would see it too — mirrors the real server's persisted write.
      PROJECTS.find((p) => p.id === 'smoke_target').roster = roster;
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ roster, already_hired: hireCalls.length > 1 }) });
    }
    // encodeURIComponent turns the ref's ':' into '%3A' — .pathname keeps the
    // raw encoding (unlike Flask's route converter, which decodes it), so
    // this matches on the decoded form rather than a literal string.
    if (decodeURIComponent(path) === '/api/project/smoke_target/roster/global:code-reviewer' && req.method() === 'DELETE') {
      unhireCalls.push(true);
      const p = PROJECTS.find((x) => x.id === 'smoke_target');
      const roster = (p.roster || []).map((r) => ({ ...r, removed_at: '2026-09-09T01:00:00Z' }));
      p.roster = roster;
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, roster }) });
    }
    return route.abort();
  });

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });
  if (pageErrors.length) pageErrors.forEach((e) => fail('uncaught page error during boot: ' + e));
  else ok('app booted clean, no uncaught exceptions');

  // The Floor is a 1120px-wide window the real app CENTERS over the dashboard
  // grid, so in real use most tiles sit UNDERNEATH it. This run used to pin the
  // window and the drop target to disjoint corners, which is exactly why it
  // passed green while the real UI could not drop at all: `elementFromPoint`
  // (how a drop finds its tile) returns whatever paints on top, and the modal
  // does. So pin them OVERLAPPING instead — test-only inline styles for
  // deterministic geometry, with the occlusion itself asserted below rather
  // than assumed, so a future layout change can't quietly un-test it.
  await page.evaluate(() => { window.openFloor(); });
  await page.waitForSelector('.fl-fig.fl-draggable', { timeout: 5000 });
  await page.evaluate(() => {
    const win = document.querySelector('.modal-window[data-modal-id="__floor"]');
    win.style.left = '300px'; win.style.top = '60px';
    const content = win.querySelector('.modal-content');
    content.style.width = '700px'; content.style.maxWidth = '700px';
    content.style.maxHeight = '700px'; content.style.overflow = 'auto';
    const target = document.querySelector('#projects-col .card[data-id="smoke_target"]');
    target.style.position = 'fixed'; target.style.left = '520px'; target.style.top = '430px';
    target.style.width = '300px'; target.style.height = '160px'; target.style.zIndex = '1';
  });
  ok('Floor opened, Fenn\'s figure is marked draggable');

  const occlusion = await page.evaluate(() => {
    const t = document.querySelector('#projects-col .card[data-id="smoke_target"]').getBoundingClientRect();
    const el = document.elementFromPoint(t.x + t.width / 2, t.y + t.height / 2);
    return { inFloor: !!(el && el.closest('.modal-window[data-modal-id="__floor"]')), hit: el ? el.className.toString().slice(0, 40) : null };
  });
  occlusion.inFloor
    ? ok('premise holds: the drop target sits UNDER the Floor window (hit-tests to the modal at rest)')
    : fail(`this run is not testing occlusion — the target tile hit-tests to ${occlusion.hit}, not the Floor window`);

  const figBox = await page.$eval('.fl-fig.fl-draggable', (el) => {
    const r = el.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  });
  const tileBox = await page.$eval('#projects-col .card[data-id="smoke_target"]', (el) => {
    const r = el.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  });

  // ── Drag past the 8px activation slop and hold mid-flight ────────────────
  await page.mouse.move(figBox.x, figBox.y);
  await page.mouse.down();
  await page.mouse.move(figBox.x + 40, figBox.y + 10, { steps: 5 });
  await page.waitForSelector('body.hire-active', { timeout: 3000 }).then(
    () => ok('hire-active engaged after crossing the drag slop'),
    () => fail('body.hire-active never appeared'));
  await page.waitForTimeout(200);   // let the 0.15s opacity transition settle before reading it
  // Fenn is GLOBAL-scope, so every project is a legal drop — including her
  // own (spec §6 case 3, "upgrades a derived participant to explicit
  // membership"). Both tiles get .hire-target; the sidebar/chrome dims
  // instead, since it is never a target either way.
  const sidebarOpacity = await page.$eval('.sidebar', (el) => getComputedStyle(el).opacity);
  parseFloat(sidebarOpacity) < 0.5
    ? ok(`chrome (sidebar) dimmed to opacity ${sidebarOpacity}`)
    : fail(`sidebar should be dimmed during hire mode, opacity was ${sidebarOpacity}`);
  const [homeIsTarget, targetIsTarget] = await Promise.all([
    page.$eval('#projects-col .card[data-id="smoke_home"]', (el) => el.classList.contains('hire-target')),
    page.$eval('#projects-col .card[data-id="smoke_target"]', (el) => el.classList.contains('hire-target')),
  ]);
  (homeIsTarget && targetIsTarget)
    ? ok('both tiles are marked .hire-target — global-scope character, any project (including its own) is valid')
    : fail(`expected both tiles marked .hire-target, got home=${homeIsTarget} target=${targetIsTarget}`);
  const ghostVisible = await page.$eval('.hire-ghost', (el) => !!el).catch(() => false);
  ghostVisible ? ok('a ghost face follows the pointer') : fail('.hire-ghost did not appear');

  // The modal layer has to step aside for the whole drag — faded enough to aim
  // through, and out of the hit-test entirely.
  const layer = await page.evaluate(() => ({
    opacity: parseFloat(getComputedStyle(document.querySelector('.modal-layer')).opacity),
    winPE: getComputedStyle(document.querySelector('.modal-window[data-modal-id="__floor"]')).pointerEvents,
  }));
  (layer.opacity < 0.4 && layer.winPE === 'none')
    ? ok(`modal layer stepped aside during the drag (opacity ${layer.opacity}, pointer-events ${layer.winPE})`)
    : fail(`modal layer must fade AND stop hit-testing during a hire drag; got opacity ${layer.opacity}, pointer-events ${layer.winPE}`);

  // ── The drag must outlive the Floor's own refresh ────────────────────────
  // The Floor polls /api/floor and rewrites #floor-body wholesale. That used
  // to destroy the node under the pointer: the ghost froze, pointerup reached
  // nothing, and `hire-active` + the ghost were stranded on the board with
  // `_hireDrag` still set — which made every later drag a no-op too. Any drag
  // where Ron pauses to aim outlives one 5s tick, so this was the common case.
  await page.evaluate(() => { window.__dragNode = document.querySelector('.fl-fig.fl-draggable'); });
  await page.waitForTimeout(4200);   // fixture asks for a 5s poll; floor's floor is 3s
  const survived = await page.evaluate(() => ({
    stillMounted: document.contains(window.__dragNode),
    hireActive: document.body.classList.contains('hire-active'),
    ghost: !!document.querySelector('.hire-ghost'),
  }));
  (survived.stillMounted && survived.hireActive && survived.ghost)
    ? ok('a poll tick during the drag is skipped — the dragged node, the ghost and hire-active all survive')
    : fail(`the Floor poll disrupted an in-flight drag: ${JSON.stringify(survived)}`);

  // And if the board DOES re-render mid-drag by some other route (a forced
  // refresh, a modal close), the gesture still has to complete: the pointer
  // handlers live on `window`, not on the card, so losing the card is survivable.
  await page.evaluate(() => window.refreshFloor());
  await page.waitForTimeout(150);
  await page.mouse.move(figBox.x + 60, figBox.y + 40, { steps: 3 });
  await page.waitForTimeout(120);
  const afterRerender = await page.evaluate(({ x, y }) => {
    const g = document.querySelector('.hire-ghost');
    return { nodeGone: !document.contains(window.__dragNode), ghost: !!g,
             tracking: g ? Math.abs(parseFloat(g.style.left) - x) < 2 && Math.abs(parseFloat(g.style.top) - y) < 2 : false };
  }, { x: figBox.x + 60, y: figBox.y + 40 });
  (afterRerender.nodeGone && afterRerender.ghost && afterRerender.tracking)
    ? ok('a forced re-render replaced the dragged card and the ghost still tracks the pointer')
    : fail(`drag did not survive a mid-flight re-render: ${JSON.stringify(afterRerender)}`);

  if (SHOT_DIR) {
    await page.screenshot({ path: resolve(SHOT_DIR, 'drag-to-hire-mid-drag.png') });
    ok(`mid-drag screenshot written to ${resolve(SHOT_DIR, 'drag-to-hire-mid-drag.png')}`);
  }

  // ── Drop on the target tile ───────────────────────────────────────────────
  await page.mouse.move(tileBox.x, tileBox.y, { steps: 10 });
  await page.$eval('#projects-col .card[data-id="smoke_target"]', (el) => el.classList.contains('hire-hover'))
    .then((v) => v ? ok('hovered tile picks up .hire-hover — through the modal that covers it') : fail('hovered tile missing .hire-hover'));
  const hitThroughModal = await page.evaluate(({ x, y }) => {
    const el = document.elementFromPoint(x, y);
    const card = el && el.closest('#projects-col .card');
    return card ? card.dataset.id : (el ? el.className.toString().slice(0, 40) : null);
  }, tileBox);
  hitThroughModal === 'smoke_target'
    ? ok('the occluded tile hit-tests to itself mid-drag, so a drop can find it')
    : fail(`mid-drag hit-test should reach smoke_target, got ${hitThroughModal}`);
  await page.mouse.up();

  await page.waitForFunction(() => !document.body.classList.contains('hire-active'), { timeout: 3000 })
    .then(() => ok('hire-active cleared on drop'), () => fail('hire-active never cleared'));
  hireCalls.length === 1 ? ok(`POST .../roster/hire fired once with ${JSON.stringify(hireCalls[0])}`)
                         : fail(`expected exactly 1 hire call, got ${hireCalls.length}: ${JSON.stringify(hireCalls)}`);
  hireCalls[0] && hireCalls[0].character === 'global:code-reviewer'
    ? ok('hire payload names the dragged character')
    : fail(`hire payload should be global:code-reviewer, got ${JSON.stringify(hireCalls[0])}`);

  await page.waitForSelector(`.modal-window[data-modal-id="${PID_TARGET}"]`, { timeout: 5000 })
    .then(() => ok('the target project\'s modal opened'), () => fail('modal for the target project never opened'));
  // _hireOpenChannel waits 500ms for the modal to finish mounting before
  // touching the rail (same margin floorOpenFigure/floorPlace use) — the
  // rail starts on whatever mode localStorage last left it on (Chats here)
  // and only becomes Channel once that timeout fires.
  const modeScope = `.modal-window[data-modal-id="${PID_TARGET}"] `;
  await page.waitForFunction((sel) => {
    const el = document.querySelector(sel + '.rail-mode-btn.on');
    return el && el.textContent.trim() === 'Channel';
  }, modeScope, { timeout: 3000 }).then(
    () => ok('rail switched to Channel mode'),
    async () => fail(`rail mode should be Channel, was ${await page.$eval(modeScope + '.rail-mode-btn.on', (el) => el.textContent.trim()).catch(() => '(none)')}`));
  await page.waitForSelector(`.modal-window[data-modal-id="${PID_TARGET}"] .channel-back`, { timeout: 5000 })
    .then(() => ok('Fenn\'s row is selected (no history yet, so the filtered/empty view + back button show)'),
          () => fail('channel person filter never engaged for the hired character'));

  // ── The drop must ARM the composer, not just drill to the row ────────────
  // Landing on the agent's empty channel with PERSONA still on "None" sent the
  // next thing typed to the project default — correct-looking and wrong. The
  // pending value is what dispatchAgent puts in `body.character`, so assert the
  // state that actually reaches the wire, not only the select's displayed text.
  const armed = await page.evaluate((pid) => {
    const w = document.querySelector(`.modal-window[data-modal-id="${pid}"]`);
    const sel = w && w.querySelector('.composer-character-row .composer-provider-select');
    const header = w && w.querySelector('.conv-thread-header');
    const empty = w && w.querySelector('.composer-empty-state');
    return { pending: window.getPendingCharacter ? window.getPendingCharacter(pid) : '(no accessor)',
             selectValue: sel ? sel.value : '(no persona select on screen)',
             headerName: header ? (header.querySelector('.conv-thread-name') || {}).textContent : '(no thread header)',
             emptyHeading: empty ? (empty.querySelector('.ces-heading') || {}).textContent : '(no empty state)' };
  }, PID_TARGET);
  armed.pending === 'global:code-reviewer'
    ? ok('composer is armed with the dropped character — the next dispatch carries body.character')
    : fail(`pendingDispatchCharacter should be global:code-reviewer, got ${armed.pending}`);

  // ── No-history landing reads as a conversation shell, not the generic +New
  // screen (Ron, 2026-09-10: a drop with no history still landed on "What
  // should Claude work on?" with PERSONA sitting in a dropdown). A fresh hire
  // has no session to open, so the header + armed composer stand in for one —
  // the Persona dropdown is deliberately gone here (the header IS the persona
  // indicator); the empty-state heading names the thread, not the cold pitch.
  armed.selectValue === '(no persona select on screen)'
    ? ok('Persona dropdown is absent on the thread-shell landing — the header replaces it')
    : fail(`expected no Persona select on the thread-shell landing, got ${armed.selectValue}`);
  armed.headerName === 'Fenn'
    ? ok('thread-shell header names the hired agent (Fenn), standing in for a real conversation header')
    : fail(`thread-shell header should read Fenn, got ${armed.headerName}`);
  armed.emptyHeading === 'No conversations yet'
    ? ok('empty-state body reads "No conversations yet", not the generic cold-start pitch')
    : fail(`empty-state heading should read "No conversations yet", got ${armed.emptyHeading}`);

  if (SHOT_DIR) {
    await page.screenshot({ path: resolve(SHOT_DIR, 'drag-to-hire-channel-view.png') });
    ok(`post-drop channel-view screenshot written to ${resolve(SHOT_DIR, 'drag-to-hire-channel-view.png')}`);
  }

  // ── The OTHER case: an agent that already HAS history opens the real
  // thread, not the shell. Seed a session + conversation row for the same
  // character key, then re-select the row — agentStatusCache/agentHistory/
  // conversationsCache are classic-script globals declared in index.html
  // (`let` at top level), so a bare reference inside page.evaluate resolves
  // through the page's shared global scope, same as the existing
  // `allProjects` access above.
  await page.evaluate((pid) => {
    const sid = 'sess-fenn-hist';
    agentStatusCache[sid] = {
      status: 'idle', task: 'reviewed a diff', projectId: pid,
      claudeSessionId: 'csid-fenn-hist',
      character: { name: 'code-reviewer', scope: 'global', agent_name: 'Fenn', display_name: 'code-reviewer' },
    };
    agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Target Project',
      task: 'reviewed a diff', status: 'idle', startedAt: '2026-09-09T00:00:00Z', triggerType: 'manual' });
    conversationsCache[pid] = (conversationsCache[pid] || []).concat([{
      claude_session_id: 'csid-fenn-hist', mc_session_id: sid, mtime: Date.now() / 1000,
      character: { name: 'code-reviewer', scope: 'global', agent_name: 'Fenn', display_name: 'code-reviewer' },
      label: 'reviewed a diff', live: false,
    }]);
    openChannelPerson(pid, 'global:code-reviewer');
  }, PID_TARGET);
  // Desktop is the 3-pane view (no .agent-tab strip — that markup is mobile-only
  // dead code here), so "opened the real session" reads on the output pane
  // itself, keyed by the session id switchAgentTab armed.
  await page.waitForSelector(`.modal-window[data-modal-id="${PID_TARGET}"] #agent-output-sess-fenn-hist`, { timeout: 3000, state: 'attached' })
    .then(() => ok('a history hit opens the real session output pane (switchAgentTab), not the thread shell'),
          () => fail('an agent with a matching conversation should switch to its session tab'));
  const historyView = await page.evaluate((pid) => {
    const w = document.querySelector(`.modal-window[data-modal-id="${pid}"]`);
    return { hasShellHeader: !!w.querySelector('.conv-thread-header'),
             activeTab: activeAgentTab[pid] || null };
  }, PID_TARGET);
  historyView.activeTab === 'sess-fenn-hist'
    ? ok('activeAgentTab points at the resumed session, not the +New composer')
    : fail(`activeAgentTab should be sess-fenn-hist, got ${historyView.activeTab}`);
  !historyView.hasShellHeader
    ? ok('no thread-shell header on a real session view — it only stands in when there is nothing to open')
    : fail('thread-shell header leaked into a real, history-backed conversation view');
  if (SHOT_DIR) {
    await page.screenshot({ path: resolve(SHOT_DIR, 'drag-to-hire-history-view.png') });
    ok(`with-history landing screenshot written to ${resolve(SHOT_DIR, 'drag-to-hire-history-view.png')}`);
  }

  // ── Bench merge (spec §3.2) + un-hire (spec §5) ───────────────────────────
  // A real periodic /api/projects poll would carry the server's freshly
  // written `roster` back into `allProjects`; simulate that one field update
  // directly rather than re-plumbing the poll, then prove _channelRoster
  // actually reads it — a hired-but-never-talked-to character must appear on
  // the Bench, not stay invisible until its first conversation.
  await page.evaluate(({ pid, roster }) => {
    const p = allProjects.find((x) => x.id === pid);
    p.roster = roster;
    refreshModalById(pid);
  }, { pid: PID_TARGET, roster: [{ character: 'global:code-reviewer', hired_at: '2026-09-09T00:00:00Z', hired_by: 'drag', removed_at: null }] });
  await page.click(`${modeScope}.channel-back`);
  await page.waitForSelector(`${modeScope}.channel-row`, { timeout: 3000 });
  const benchRow = await page.$(`${modeScope}.channel-row[data-char-key="global:code-reviewer"] .conv-unhire`);
  benchRow ? ok('the hired-but-never-talked-to character shows on the Bench with a remove control')
           : fail('hired character with zero conversations never appeared on the Bench roster');
  if (benchRow) {
    await page.evaluate(() => { window.confirm = () => true; });   // same idiom as boot-smoke.mjs
    await benchRow.click();
    await page.waitForTimeout(200);   // let the fetch round-trip
    unhireCalls.length === 1 ? ok('DELETE .../roster/global:code-reviewer fired on remove-click + confirm')
                             : fail(`expected exactly 1 un-hire call, got ${unhireCalls.length}`);
  }

  // ── Cleanup is unconditional (spec §7) ──────────────────────────────────
  // Every way out of a drag has to leave the board spotless. A path that skips
  // teardown doesn't just litter one ghost: `_hireDrag` stays set, so the very
  // next pointerdown returns early at its own guard and the board is dead until
  // a reload. So each case here ends by proving another drag can still START.
  await page.evaluate(({ pid }) => { closeModalById ? closeModalById(pid) : null; }, { pid: PID_TARGET }).catch(() => {});
  const startDrag = async () => {
    const f = await (await page.$('.fl-fig.fl-draggable')).boundingBox();
    await page.mouse.move(f.x + f.width / 2, f.y + f.height / 2);
    await page.mouse.down();
    await page.mouse.move(f.x + f.width / 2 + 30, f.y + f.height / 2 + 20, { steps: 4 });
    await page.waitForSelector('body.hire-active', { timeout: 2000 });
  };
  const residue = () => page.evaluate(() => ({
    hireActive: document.body.classList.contains('hire-active'),
    ghosts: document.querySelectorAll('.hire-ghost').length,
    marks: document.querySelectorAll('#projects-col .card.hire-target,#projects-col .card.hire-refused,#projects-col .card.hire-hover').length,
  }));
  const assertClean = async (label) => {
    const r = await residue();
    (!r.hireActive && r.ghosts === 0 && r.marks === 0)
      ? ok(`${label}: board left spotless`)
      : fail(`${label}: left residue ${JSON.stringify(r)}`);
    // Esc/blur cancel the drag but leave the physical button DOWN — release it
    // before grabbing again, or the next mouse.down() is a no-op and this reads
    // as a dead board when the board is fine.
    await page.mouse.up();
    await startDrag().then(() => ok(`${label}: a fresh drag still starts afterwards`),
                           () => fail(`${label}: the board is dead — a later drag never activated`));
  };

  await startDrag();
  // A point with no tile under it, measured DURING the drag (the modal stops
  // hit-testing then, so a spot that looks empty at rest may not be).
  const deadSpot = await page.evaluate(() => {
    for (let y = window.innerHeight - 8; y > 40; y -= 16)
      for (let x = 8; x < window.innerWidth - 8; x += 32) {
        const el = document.elementFromPoint(x, y);
        if (el && !el.closest('#projects-col .card')) return [x, y];
      }
    return null;
  });
  const hiresBefore = hireCalls.length;
  if (!deadSpot) fail('could not find a tile-free point to test an invalid drop');
  else {
    await page.mouse.move(deadSpot[0], deadSpot[1], { steps: 6 });
    await page.mouse.up();
    await page.waitForTimeout(200);
    hireCalls.length === hiresBefore
      ? ok('a release off every tile hires nobody')
      : fail(`an off-target release fired ${hireCalls.length - hiresBefore} unwanted hire(s)`);
    await assertClean('release off-target');
  }
  await page.keyboard.press('Escape');
  await page.waitForTimeout(150);
  await assertClean('Escape mid-drag');
  await page.evaluate(() => window.dispatchEvent(new Event('blur')));
  await page.waitForTimeout(150);
  await assertClean('window blur mid-drag (release landed in another app)');
  // assertClean leaves a drag in flight; cancel it rather than releasing, so
  // the release can't land on a tile and hire somebody this case never asked for.
  await page.keyboard.press('Escape');
  await page.mouse.up();
  // Past floorOpenFigure's 300ms click-swallow window, so the plain-click case
  // below is testing the click and not this teardown's stamp.
  await page.waitForTimeout(450);

  // ── A click that never crossed the drag threshold still opens the chat ───
  await page.click('.fl-fig.fl-draggable');
  await page.waitForSelector(`.modal-window[data-modal-id="${PID_HOME}"]`, { timeout: 5000 })
    .then(() => ok('a plain click (no drag) still opens the figure\'s own chat, unaffected'),
          () => fail('a plain click on the figure should still open its chat'));

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail('uncaught exception during interaction: ' + e));

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0
    ? '\n✅ PASS — drag dims the board, marks/highlights valid targets, drops hire onto the target project, and opens Channel mode on that agent.'
    : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}

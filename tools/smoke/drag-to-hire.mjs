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
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
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

  // The Floor is a floating window the real app centers over the dashboard
  // grid (by design — Ron positions it beside the tiles he's dragging onto).
  // A headless run has no such positioning judgment, so pin both the Floor
  // window and the drop target to disjoint corners here — test-only inline
  // styles, so the drag lands on real, unobstructed screen coordinates
  // regardless of how wide this run's grid happens to lay out.
  await page.evaluate(() => { window.openFloor(); });
  await page.waitForSelector('.fl-fig.fl-draggable', { timeout: 5000 });
  await page.evaluate(() => {
    const win = document.querySelector('.modal-window[data-modal-id="__floor"]');
    win.style.left = '10px'; win.style.top = '10px';
    const content = win.querySelector('.modal-content');
    content.style.width = '340px'; content.style.maxWidth = '340px';
    content.style.maxHeight = '460px'; content.style.overflow = 'auto';
    const target = document.querySelector('#projects-col .card[data-id="smoke_target"]');
    target.style.position = 'fixed'; target.style.left = '1000px'; target.style.top = '650px';
    target.style.width = '340px'; target.style.zIndex = '1';
  });
  ok('Floor opened, Fenn\'s figure is marked draggable');

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

  if (SHOT_DIR) {
    await page.screenshot({ path: resolve(SHOT_DIR, 'drag-to-hire-mid-drag.png') });
    ok(`mid-drag screenshot written to ${resolve(SHOT_DIR, 'drag-to-hire-mid-drag.png')}`);
  }

  // ── Drop on the target tile ───────────────────────────────────────────────
  await page.mouse.move(tileBox.x, tileBox.y, { steps: 10 });
  await page.$eval('#projects-col .card[data-id="smoke_target"]', (el) => el.classList.contains('hire-hover'))
    .then((v) => v ? ok('hovered tile picks up .hire-hover') : fail('hovered tile missing .hire-hover'));
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

  if (SHOT_DIR) {
    await page.screenshot({ path: resolve(SHOT_DIR, 'drag-to-hire-channel-view.png') });
    ok(`post-drop channel-view screenshot written to ${resolve(SHOT_DIR, 'drag-to-hire-channel-view.png')}`);
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

  // ── A click that never crossed the drag threshold still opens the chat ───
  await page.evaluate(({ pid }) => { closeModalById ? closeModalById(pid) : null; }, { pid: PID_TARGET }).catch(() => {});
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

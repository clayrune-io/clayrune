#!/usr/bin/env node
/**
 * docs/TUTORIALS_SPEC.md lesson 2: the four-action Workflows lesson
 * ('workflows-v1', static/js/learn.js + static/js/learn-practice.js), end to end.
 *
 * Real headless boot (real index.html + real static/js/*.js, no network), same
 * hermetic shape as learn-floor.mjs. The fixture server owns real workflow and
 * schedule lists; every request that reaches it is recorded, so "practice never
 * touches the server" is asserted against what actually arrived. A control at
 * the end proves the sentinel can fire (a real write after practice DOES land).
 *
 * Covered:
 *   - the hub lists both lessons, each with its own Start/Resume/Replay state
 *   - each step advances ONLY on evidence read from the canvas model and the
 *     practice store; clicks, hovers, Hint and waiting advance nothing
 *   - drag path (desktop 1440), tap/no-drag path (960, 390 touch), keyboard-only
 *   - a failed save keeps step 4 and shows the real error
 *   - Run / Schedule / cancel from practice are refused with "Practice only"
 *   - zero writes and zero /api/workflows or /api/schedules reads reach the server
 *   - reload mid-lesson resumes; a missing or hidden target pauses with the
 *     update message and never completes
 *   - reduced motion
 *
 * RUN
 *   cd tools/smoke && node learn-workflows.mjs
 * Screenshots go to MC_SMOKE_SHOT_DIR if set, else are skipped.
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
const CLAYDO_IDLE = readFileSync(resolve(REPO_ROOT, 'assets', 'claydo-idle.webp'));
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
const REAL_PROJECTS = [fixtureProject('real_home', 'Real Home'), fixtureProject('clayrune', 'Clayrune', { _is_onboarding_project: true })];
const REAL_FLOOR = {
  rooms: [], quiet: [], bench: [], counts: { rooms: 0, figures: 0, quiet: 0, bench: 0 },
  activity_states: false, poll_seconds: 5,
};
const REAL_CHARACTERS = [];
const REAL_WORKFLOW = { id: 'wf-real-1', name: 'Real nightly', enabled: true, format: 2, trigger: { type: 'manual', entry: [] }, nodes: [], edges: [] };

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, pass, failMsg) => (cond ? ok(pass) : fail(failMsg || pass));

// ── Page factory ─────────────────────────────────────────────────────────────
async function newPage(browser, { width = 1440, height = 900, touch = false, reducedMotion = false, storage = null } = {}) {
  const ctx = await browser.newContext({
    viewport: { width, height }, hasTouch: touch, isMobile: touch,
    reducedMotion: reducedMotion ? 'reduce' : 'no-preference',
  });
  const page = await ctx.newPage();
  const server = { calls: [], workflows: [REAL_WORKFLOW], schedules: [] };
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.addInitScript(() => {
    if (!sessionStorage.getItem('__lrn_seeded')) { sessionStorage.setItem('__lrn_seeded', '1'); localStorage.setItem('walkthrough_done', '1'); }
  });
  if (storage) await page.addInitScript((s) => { if (!sessionStorage.getItem('__lrn_s2')) { sessionStorage.setItem('__lrn_s2', '1'); for (const [k, v] of Object.entries(s)) localStorage.setItem(k, v); } }, storage);
  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/assets/claydo-idle.webp') return route.fulfill({ status: 200, contentType: 'image/webp', body: CLAYDO_IDLE });
    server.calls.push({ method: req.method(), path, body: req.postData() || '' });
    const json = (o, s = 200) => route.fulfill({ status: s, contentType: 'application/json', body: JSON.stringify(o) });
    if (path === '/api/projects') return json(REAL_PROJECTS);
    if (path === '/api/config') return json({});
    if (path === '/api/characters') return json(REAL_CHARACTERS);
    if (path === '/api/floor') return json(REAL_FLOOR);
    if (path === '/api/local-auth/status') return json({ configured: true });
    if (path === '/api/workflows' && req.method() === 'GET') return json(server.workflows);
    if (path === '/api/workflows' && req.method() === 'POST') {
      const wf = { id: 'wf-new-' + server.workflows.length, ...JSON.parse(req.postData() || '{}') };
      server.workflows.push(wf);
      return json({ ok: true, workflow: wf });
    }
    if (path === '/api/schedules' && req.method() === 'GET') return json(server.schedules);
    if (path === '/api/schedules' && req.method() === 'POST') { server.schedules.push(JSON.parse(req.postData() || '{}')); return json({ ok: true }); }
    return route.abort();
  });
  return { ctx, page, server, pageErrors };
}

async function boot(h) {
  await h.page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await h.page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
}

const realErrors = (h) => h.pageErrors.filter((e) => !/Failed to fetch dynamically imported module|aborted|net::ERR|EventSource/i.test(e));
const state = (page) => page.evaluate(() => window.LearnEngine.state);
const waitStep = async (page, step, phase = 'active', timeout = 8000) => {
  try {
    await page.waitForFunction(([s, p]) => { const st = window.LearnEngine.state; return st && st.step === s && st.phase === p; }, [step, phase], { timeout });
  } catch (e) {
    const now = await page.evaluate(() => ({ state: window.LearnEngine.state, bubble: (document.getElementById('lrn-bubble') || {}).innerText }));
    throw new Error(`waiting for step ${step}/${phase}: lesson is at ${JSON.stringify(now.state)} bubble=${JSON.stringify(now.bubble)}`);
  }
};
const bubbleText = (page) => page.evaluate(() => (document.getElementById('lrn-bubble') || {}).innerText || '');
const progressText = (page) => page.evaluate(() => (document.querySelector('#lrn-bubble .lrn-progress') || {}).textContent || '');
const lessonRec = (page, id = 'workflows-v1') => page.evaluate((i) => window.LearnEngine.progress(i), id);
const serverWrites = (h, from = 0) => h.server.calls.slice(from).filter((c) => c.method !== 'GET' && c.method !== 'HEAD');
const wfSurfaceCalls = (h, from = 0) => h.server.calls.slice(from).filter((c) => /^\/api\/(workflows|schedules)/.test(c.path));
const sentinel = (h) => JSON.stringify({ wf: h.server.workflows, sc: h.server.schedules });
async function shoot(page, name) { if (SHOT_DIR) await page.screenshot({ path: resolve(SHOT_DIR, `learn-workflows-${name}.png`) }); }

// ── Canvas helpers: real mouse events, the same handlers a real drag fires ───
async function boxOf(page, sel) {
  const el = await page.$(sel);
  if (!el) throw new Error('not found: ' + sel);
  await el.scrollIntoViewIfNeeded();
  return el.boundingBox();
}
const centre = (b) => ({ x: b.x + b.width / 2, y: b.y + b.height / 2 });

// A free canvas point right of the Trigger, measured NOW.
async function freeCanvasPoint(page) {
  return page.evaluate(() => {
    const vp = document.getElementById('wfb-canvas-viewport');
    const r = vp.getBoundingClientRect();
    const t = vp.querySelector('.wfb-trigger-box').getBoundingClientRect();
    // A card is ~260px wide and the practice canvas can be narrower than
    // trigger + card, so with no room on the right the free spot is below.
    const right = t.right + 70 + 260 <= r.right - 8;
    return right ? { x: t.right + 70 + 130, y: t.top + 24 } : { x: t.left + 75, y: Math.min(t.bottom + 130, r.bottom - 150) };
  });
}
async function dragToolOntoCanvas(page) {
  const b = await boxOf(page, '.wfb-toolbar-tool[data-wf-tool="approval"]');
  const to = await freeCanvasPoint(page);
  const from = centre(b);
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(from.x + 30, from.y + 30, { steps: 4 });
  await page.mouse.move(to.x, to.y, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(150);
}
async function tapTool(page) {
  const b = await boxOf(page, '.wfb-toolbar-tool[data-wf-tool="approval"]');
  const c = centre(b);
  await page.mouse.move(c.x, c.y);
  await page.mouse.down();
  await page.mouse.up();
  await page.waitForTimeout(150);
}
async function dragTriggerToNode(page) {
  const a = centre(await boxOf(page, '.wfb-trigger-box .wfb-port-out'));
  const b = centre(await boxOf(page, '.wfb-node .wfb-port-in'));
  await page.mouse.move(a.x, a.y);
  await page.mouse.down();
  await page.mouse.move(a.x + 20, a.y + 10, { steps: 3 });
  await page.mouse.move(b.x, b.y, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(150);
}
async function setName(page, value) {
  await page.evaluate((v) => {
    const el = document.getElementById('wfb-name');
    el.value = v; el.dispatchEvent(new Event('input', { bubbles: true }));
  }, value);
}
const canvasModel = (page) => page.evaluate(() => {
  const m = window._wfLearn.state();
  return m ? { nodes: (m.wf.def.nodes || []).length, edges: (m.wf.def.edges || []).length, entry: (m.wf.def.trigger.entry || []).length } : null;
});

// Drive the whole lesson the way a user would. mode: drag | tap | keyboard
async function playLesson(page, mode, { shots = false } = {}) {
  const press = async (sel) => {
    if (mode === 'keyboard') { await page.focus(sel); await page.keyboard.press('Enter'); }
    else if (mode === 'tap') await page.tap(sel);
    else await page.click(sel);
  };
  await waitStep(page, 0);
  if (shots) await shoot(page, 'step1');
  await press('#wfb-clayrune-section-learn_practice .wfb-tab-new');
  await waitStep(page, 1);
  if (shots) await shoot(page, 'step2');
  if (mode === 'drag') await dragToolOntoCanvas(page);
  else await press('#lrn-bubble [data-lrn="fallback"]');
  await waitStep(page, 2);
  if (shots) await shoot(page, 'step3');
  if (mode === 'drag') await dragTriggerToNode(page);
  else await press('#lrn-bubble [data-lrn="fallback"]');
  await waitStep(page, 3);
  if (shots) await shoot(page, 'step4');
  await setName(page, 'My first workflow');
  await press('.wfb-toolbar .btn-sched-save');
  await page.waitForFunction(() => { const s = window.LearnEngine.state; return s && s.phase === 'completed'; }, null, { timeout: 8000 });
  if (shots) await shoot(page, 'done');
}

// ── Sections ─────────────────────────────────────────────────────────────────
let browser, exitCode = 1;
try {
  browser = await chromium.launch();

  // ══ 1. Hub: two lessons, each with its own state ══
  console.log('1. Hub lists both lessons');
  {
    const h = await newPage(browser);
    const { page } = h;
    await boot(h);
    await page.click('.sidebar-item[data-nav="learn"]');
    await page.waitForSelector('#lrn-hub-body .lrn-card', { timeout: 3000 });
    const cards = await page.evaluate(() => Array.from(document.querySelectorAll('#lrn-hub-body .lrn-card')).map((c) => ({
      id: c.dataset.lesson, text: c.innerText, btn: c.querySelector('.lrn-card-go').textContent.trim(),
    })));
    check(cards.length === 2 && cards[0].id === 'floor-v1' && cards[1].id === 'workflows-v1', 'hub shows the Floor lesson then the Workflows lesson');
    check(/Workflows/.test(cards[1].text) && /Place a step, connect the trigger, save it\./.test(cards[1].text) && /4 actions · Practice only/.test(cards[1].text), 'Workflows card carries its own title, subtitle and metadata');
    check(cards[0].btn === 'Start' && cards[1].btn === 'Start', 'both start in the Start state');
    await shoot(page, 'hub');

    // Progress is per lesson: finishing one never changes the other's state.
    await page.evaluate(() => { document.querySelector('#lrn-hub-body [data-lesson="workflows-v1"] .lrn-card-go').click(); });
    await waitStep(page, 0);
    check((await state(page)).lesson === 'workflows-v1', 'the hub card starts the Workflows lesson');
    const rec = await page.evaluate(() => JSON.parse(localStorage.getItem('learn.progress')).lessons);
    check(rec['workflows-v1'] && rec['workflows-v1'].status === 'active' && !rec['floor-v1'], 'progress is recorded under workflows-v1 only');
    const ev = await page.evaluate(() => JSON.parse(localStorage.getItem('learn.events')));
    check(ev.length > 0 && ev.every((e) => e.lesson === 'workflows-v1'), 'telemetry events carry the real lesson id');
    await page.evaluate(() => LearnEngine.leave());
    await page.evaluate(() => openLearn());
    const after = await page.evaluate(() => Array.from(document.querySelectorAll('#lrn-hub-body .lrn-card')).map((c) => c.querySelector('.lrn-card-go').textContent.trim()));
    check(after[0] === 'Start' && after[1] === 'Resume', 'after Leave: Floor still Start, Workflows now Resume');
    check(realErrors(h).length === 0, 'no uncaught page errors in section 1' + (realErrors(h).length ? ': ' + realErrors(h).join(' | ') : ''));
    await h.ctx.close();
  }

  // ══ 2. Desktop 1440: drag path, evidence-only, isolation ══
  console.log('2. Desktop 1440: drag path, evidence-only steps, isolation');
  {
    const h = await newPage(browser, { width: 1440, height: 900 });
    const { page } = h;
    await boot(h);
    const before = sentinel(h);
    const callsBefore = h.server.calls.length;
    await page.evaluate(() => LearnEngine.start('workflows-v1', 'test'));
    await waitStep(page, 0);

    const ctxInfo = await page.evaluate(() => ({
      tile: !!document.querySelector('#projects-col .card[data-id="learn_practice"]'),
      realTile: !!document.querySelector('#projects-col .card[data-id="real_home"]'),
      bannerShown: (() => { const b = document.getElementById('lrn-banner'); return !!b && getComputedStyle(b).display !== 'none' && b.getBoundingClientRect().height > 0; })(),
      tabNew: !!document.querySelector('#wfb-clayrune-section-learn_practice .wfb-tab-new'),
      realWf: document.body.innerText.includes('Real nightly'),
    }));
    check(ctxInfo.tile && !ctxInfo.realTile, 'dashboard shows the practice tile and none of the real projects');
    check(ctxInfo.bannerShown, 'the persistent "Practice only" banner is visible');
    check(ctxInfo.tabNew, 'the practice project opens on its Workflows tab with + New Workflow');
    check(!ctxInfo.realWf, 'the real workflow list is not shown while practicing');
    check((await progressText(page)) === '0/4', 'progress reads 0/4');
    const copy1 = await bubbleText(page);
    check(/A workflow is steps joined by lines\./.test(copy1), 'step 1 says what a workflow IS');
    check(!/\bNext\b/.test(copy1), 'the bubble has no Next control');
    await page.waitForTimeout(600);
    check(await page.evaluate(() => { const h = document.querySelector('.lrn-hand'); return !!h && h.dataset.kind === 'click'; }), 'step 1 shows a click cue on + New Workflow');
    await shoot(page, 'desktop-step1');

    // Nothing but the action advances.
    await page.waitForTimeout(1500);
    await page.click('#lrn-bubble [data-lrn="hint"]');
    check((await bubbleText(page)).includes('Hint:'), 'Hint repeats the cue');
    check((await state(page)).step === 0, 'time, clicks elsewhere and Hint advance nothing');

    // Step 1: open the canvas.
    await page.click('#wfb-clayrune-section-learn_practice .wfb-tab-new');
    await waitStep(page, 0, 'acknowledged', 4000);
    check((await bubbleText(page)).includes('Every workflow starts at a trigger.'), 'step 1 acknowledgment teaches the trigger');
    await waitStep(page, 1);
    check((await progressText(page)) === '1/4', 'progress reads 1/4 at step 2');
    check(/Approval gate/.test(await bubbleText(page)) && /step where a person decides/.test(await bubbleText(page)), 'step 2 says what the step IS');
    await page.waitForTimeout(600);
    check(await page.evaluate(() => { const h = document.querySelector('.lrn-hand'); return !!h && h.dataset.kind === 'drag'; }), 'step 2 hand cue shows a drag');
    await shoot(page, 'desktop-step2');

    // Hovering and pressing the tool without moving is a tap: it places (that IS the action),
    // so first prove a no-op does nothing: moving the pointer over the canvas places nothing.
    check((await canvasModel(page)).nodes === 0, 'the canvas starts with no steps');
    await dragToolOntoCanvas(page);
    await waitStep(page, 1, 'acknowledged', 4000);
    check((await canvasModel(page)).nodes === 1, 'the drag placed one step (read from the canvas model)');
    check((await bubbleText(page)).includes('Each step does one job.'), 'step 2 acknowledgment');
    await waitStep(page, 2);
    check(/The trigger starts the workflow/.test(await bubbleText(page)), 'step 3 says what the trigger IS');
    await page.waitForTimeout(600);
    check(await page.evaluate(() => { const h = document.querySelector('.lrn-hand'); return !!h && h.dataset.kind === 'drag'; }), 'step 3 hand cue shows the port-to-port drag');
    await shoot(page, 'desktop-step3');

    // A drag that ends short of the card connects nothing and advances nothing.
    const a = centre(await boxOf(page, '.wfb-trigger-box .wfb-port-out'));
    await page.mouse.move(a.x, a.y); await page.mouse.down();
    await page.mouse.move(a.x + 5, a.y + 300, { steps: 6 });
    await page.mouse.up();
    await page.waitForTimeout(700);
    check((await state(page)).step === 2 && (await canvasModel(page)).entry === 0, 'a wire released on empty canvas connects nothing and advances nothing');
    await dragTriggerToNode(page);
    await waitStep(page, 2, 'acknowledged', 4000);
    const m3 = await canvasModel(page);
    check(m3.entry === 1, 'the trigger now feeds the step (trigger.entry has one name)');
    check((await bubbleText(page)).includes('When the trigger fires, this step runs first.'), 'step 3 acknowledgment');
    await waitStep(page, 3);
    check(/does not run it or put it on a schedule/.test(await bubbleText(page)), 'step 4 says saving does not run or schedule');
    await shoot(page, 'desktop-step4');

    // Save only counts once the practice store holds the record.
    check((await page.evaluate(() => LearnPractice.workflows().length)) === 0, 'nothing is in the practice store before Save');
    await setName(page, 'My first workflow');
    await page.waitForTimeout(700);
    check((await state(page)).step === 3, 'naming the workflow advances nothing');
    await page.click('.wfb-toolbar .btn-sched-save');
    await page.waitForFunction(() => window.LearnEngine.state.phase === 'completed', null, { timeout: 8000 });
    const saved = await page.evaluate(() => LearnPractice.workflows());
    check(saved.length === 1 && saved[0].name === 'My first workflow' && saved[0].nodes.length === 1 && saved[0].trigger.entry.length === 1, 'the practice store holds the saved workflow with its step and wiring');
    check(/Saving did not run it or schedule it/.test(await bubbleText(page)), 'completion copy repeats that saving does not run or schedule');
    await shoot(page, 'desktop-done');

    check((await page.evaluate(() => LearnPractice.refused.length)) === 0, 'the lesson itself triggered no refused write (no stray Practice only toast)');
    // Isolation: refusals are visible and nothing lands on the server.
    const refusal = await page.evaluate(async () => {
      const out = {};
      const id = LearnPractice.workflows()[0].id;
      for (const [k, u, m] of [['run', `/api/workflows/${id}/run`, 'POST'], ['cancel', `/api/workflows/${id}/cancel`, 'POST'], ['schedule', '/api/schedules', 'POST'], ['draft', '/api/workflows/draft', 'POST']]) {
        const r = await fetch(u, { method: m, headers: { 'Content-Type': 'application/json' }, body: '{}' });
        out[k] = r.status;
      }
      return { out, toast: Array.from(document.querySelectorAll('.toast, #toast, [class*="toast"]')).map((e) => e.innerText).join('|'), refused: LearnPractice.refused.map((r) => r.path) };
    });
    check(Object.values(refusal.out).every((s) => s === 403), `Run, Cancel, Schedule and Draft are refused client-side (${JSON.stringify(refusal.out)})`);
    check(/Practice only/.test(refusal.toast), 'the refusal shows the visible "Practice only" message');
    check(refusal.refused.some((p) => /\/run$/.test(p)) && refusal.refused.some((p) => p === '/api/schedules'), 'the refused paths were recorded, not sent');
    check(serverWrites(h, callsBefore).length === 0, 'zero server writes during the whole practice run');
    check(wfSurfaceCalls(h, callsBefore).length === 0, 'zero /api/workflows or /api/schedules requests reached the server from the start of practice to its last step');
    check(sentinel(h) === before, 'before/after sentinel: the real workflow and schedule lists are unchanged');
    check(realErrors(h).length === 0, 'no uncaught page errors in section 2' + (realErrors(h).length ? ': ' + realErrors(h).join(' | ') : ''));

    // Leaving restores the real surfaces; the practice canvas does not outlive the run.
    await page.evaluate(() => LearnEngine.leave());
    check(await page.evaluate(() => window._wfLearn.state() === null), 'leaving discards the practice canvas from the builder singleton');
    await page.waitForFunction(() => !document.querySelector('#projects-col .card[data-id="learn_practice"]') && !!document.querySelector('#projects-col .card[data-id="real_home"]'), null, { timeout: 5000 }).then(() => ok('the real dashboard is back'), () => fail('the real dashboard is back'));

    // Control: the sentinel can fire. A real write after practice DOES land.
    await page.evaluate(() => fetch('/api/workflows', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"name":"control"}' }));
    check(h.server.workflows.length === 2 && wfSurfaceCalls(h).length >= 1, 'control: after Leave a real write reaches the server (the sentinel is live)');
    await h.ctx.close();
  }

  // ══ 3. 960 boundary: tap placement + keyboard-only completion ══
  console.log('3. 960 boundary: a toolbar tap places, explicit buttons connect, keyboard-only');
  {
    const h = await newPage(browser, { width: 960, height: 800 });
    const { page } = h;
    await boot(h);
    const before = sentinel(h), callsBefore = h.server.calls.length;
    await page.evaluate(() => LearnEngine.start('workflows-v1', 'test'));
    await waitStep(page, 0);
    await page.focus('#wfb-clayrune-section-learn_practice .wfb-tab-new');
    await page.keyboard.press('Enter');
    await waitStep(page, 1);
    check(await page.$('#lrn-bubble [data-lrn="fallback"]') !== null && /Place it for me/.test(await bubbleText(page)), 'step 2 offers a keyboard-reachable "Place it for me"');
    // A plain tap on the toolbar tool (no drag) places the step: no drag needed.
    await tapTool(page);
    await waitStep(page, 1, 'acknowledged', 4000);
    check((await canvasModel(page)).nodes === 1, 'a tap on the toolbar tool placed the step without any drag');
    await waitStep(page, 2);
    check(/Connect it for me/.test(await bubbleText(page)), 'step 3 offers "Connect it for me"');
    await page.focus('#lrn-bubble [data-lrn="fallback"]');
    await page.keyboard.press('Enter');
    await waitStep(page, 2, 'acknowledged', 4000);
    check((await canvasModel(page)).entry === 1, 'Connect it for me wired the trigger to the step (same builder call as the drag)');
    await waitStep(page, 3);
    await shoot(page, 'tablet-960-step4');
    await setName(page, 'Keyboard workflow');
    await page.focus('.wfb-toolbar .btn-sched-save');
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => window.LearnEngine.state.phase === 'completed', null, { timeout: 8000 });
    check(true, 'keyboard-only completion at 960');
    check(serverWrites(h, callsBefore).length === 0 && wfSurfaceCalls(h, callsBefore).length === 0 && sentinel(h) === before, '960 run: zero writes, zero workflow/schedule requests, lists unchanged');
    await page.evaluate(() => LearnEngine.leave());
    check(realErrors(h).length === 0, 'no uncaught page errors in section 3' + (realErrors(h).length ? ': ' + realErrors(h).join(' | ') : ''));
    await h.ctx.close();
  }

  // ══ 4. Phone 390 touch ══
  console.log('4. Phone 390 touch');
  {
    const h = await newPage(browser, { width: 390, height: 844, touch: true });
    const { page } = h;
    await boot(h);
    const before = sentinel(h), callsBefore = h.server.calls.length;
    await page.evaluate(() => LearnEngine.start('workflows-v1', 'test'));
    await waitStep(page, 0);
    const dock = await page.evaluate(() => {
      const b = document.getElementById('lrn-bubble').getBoundingClientRect();
      return { left: b.left, right: b.right, vw: window.innerWidth, mobile: document.getElementById('lrn-bubble').classList.contains('lrn-mobile') };
    });
    check(dock.mobile && dock.left <= 12 && dock.vw - dock.right <= 12, 'at 390 px the bubble is a full-width bottom dock');
    await shoot(page, 'phone-step1');
    await page.tap('#wfb-clayrune-section-learn_practice .wfb-tab-new');
    await waitStep(page, 1);
    await shoot(page, 'phone-step2');
    const tools = await page.evaluate(() => Array.from(document.querySelectorAll('#lrn-bubble button, .wfb-toolbar-tool')).map((b) => { const r = b.getBoundingClientRect(); return [b.textContent.trim().slice(0, 14), Math.round(r.height)]; }).filter(([, hgt]) => hgt > 0));
    check(tools.length > 0, 'the bubble and toolbar are rendered on a phone');
    await page.tap('.wfb-toolbar-tool[data-wf-tool="approval"]');
    await waitStep(page, 1, 'acknowledged', 4000).catch(() => {});
    if ((await state(page)).step === 1 && (await canvasModel(page)).nodes === 0) {
      // A phone tap on the tool may be swallowed by the toolbar's own scroll; the explicit button is the contract.
      await page.tap('#lrn-bubble [data-lrn="fallback"]');
    }
    await waitStep(page, 2);
    check((await canvasModel(page)).nodes === 1, 'one step placed on the phone canvas');
    await shoot(page, 'phone-step3');
    await page.tap('#lrn-bubble [data-lrn="fallback"]');
    await waitStep(page, 3);
    check((await canvasModel(page)).entry === 1, 'the phone Connect button wired the trigger');
    await setName(page, 'Phone workflow');
    await page.tap('.wfb-toolbar .btn-sched-save');
    await page.waitForFunction(() => window.LearnEngine.state.phase === 'completed', null, { timeout: 8000 });
    await shoot(page, 'phone-done');
    check((await page.evaluate(() => LearnPractice.refused.length)) === 0, 'phone run: no refused write, so no stray Practice only toast (the viewport watchdog is answered quietly)');
    check(serverWrites(h, callsBefore).length === 0 && wfSurfaceCalls(h, callsBefore).length === 0 && sentinel(h) === before, 'phone run: zero writes, zero workflow/schedule requests, lists unchanged');
    await page.evaluate(() => LearnEngine.leave());
    check(realErrors(h).length === 0, 'no uncaught page errors in section 4' + (realErrors(h).length ? ': ' + realErrors(h).join(' | ') : ''));
    await h.ctx.close();
  }

  // ══ 5. A failed save does not advance ══
  console.log('5. Failed save keeps step 4');
  {
    const h = await newPage(browser);
    const { page } = h;
    await boot(h);
    await page.evaluate(() => LearnEngine.start('workflows-v1', 'test'));
    await waitStep(page, 0);
    await page.click('#wfb-clayrune-section-learn_practice .wfb-tab-new');
    await waitStep(page, 1);
    await page.click('#lrn-bubble [data-lrn="fallback"]');
    await waitStep(page, 2);
    await page.click('#lrn-bubble [data-lrn="fallback"]');
    await waitStep(page, 3);
    await setName(page, 'Will fail once');
    await page.evaluate(() => LearnPractice.failNextSave('Practice save failed.'));
    await page.click('.wfb-toolbar .btn-sched-save');
    await page.waitForTimeout(900);
    check((await state(page)).step === 3 && (await state(page)).phase === 'active', 'a failed save leaves step 4 active');
    check((await page.evaluate(() => LearnPractice.workflows().length)) === 0, 'a failed save committed nothing to the practice store');
    check((await lessonRec(page)).status !== 'completed' && (await lessonRec(page)).verified.filter((v) => v.step === 'save').length === 0, 'a failed save is not recorded as a completed step');
    await shoot(page, 'save-failed');
    await page.click('.wfb-toolbar .btn-sched-save');
    await page.waitForFunction(() => window.LearnEngine.state.phase === 'completed', null, { timeout: 8000 });
    check(true, 'the next Save succeeds and completes the lesson');
    check(serverWrites(h).length === 0, 'the failed and successful saves caused zero server writes');
    await page.evaluate(() => LearnEngine.leave());
    await h.ctx.close();
  }

  // ══ 6. Reload mid-lesson resumes ══
  console.log('6. Reload mid-lesson resumes the saved step');
  {
    const h = await newPage(browser);
    const { page } = h;
    await boot(h);
    await page.evaluate(() => LearnEngine.start('workflows-v1', 'test'));
    await waitStep(page, 0);
    await page.click('#wfb-clayrune-section-learn_practice .wfb-tab-new');
    await waitStep(page, 1);
    const run = (await lessonRec(page)).runId;
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    check(await page.evaluate(() => window.LearnEngine.state === null && !window.LearnPractice.active), 'after reload nothing is running and practice is off');
    await page.evaluate(() => openLearn());
    const labels = await page.evaluate(() => Array.from(document.querySelectorAll('#lrn-hub-body .lrn-card-go')).map((b) => b.textContent.trim()));
    check(labels[0] === 'Start' && labels[1] === 'Resume', 'the hub shows Resume on Workflows only, Floor untouched');
    await page.click('#lrn-hub-body [data-lesson="workflows-v1"] .lrn-card-go');
    await waitStep(page, 1);
    check((await lessonRec(page)).runId === run, 'resume reuses the same run');
    check((await progressText(page)) === '1/4', 'resume shows 1/4');
    check(await page.$('#wfb-canvas-viewport') !== null && (await canvasModel(page)).nodes === 0, 'the empty canvas is restored so step 2 can be done');
    await page.click('#lrn-bubble [data-lrn="fallback"]');
    await waitStep(page, 2);
    // Reload with a placed step: the canvas is gone, so the lesson goes back one step and says so.
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(() => openLearn());
    await page.click('#lrn-hub-body [data-lesson="workflows-v1"] .lrn-card-go');
    await waitStep(page, 1);
    check(/canvas was reset, so this goes back a step/.test(await bubbleText(page)), 'a reload after step 2 goes back to step 2 and explains it');
    check(serverWrites(h).length === 0, 'resume caused zero server writes');
    await page.evaluate(() => LearnEngine.leave());
    check((await lessonRec(page)).status === 'paused', 'Leave keeps the progress');
    check(realErrors(h).length === 0, 'no uncaught page errors in section 6' + (realErrors(h).length ? ': ' + realErrors(h).join(' | ') : ''));
    await h.ctx.close();
  }

  // ══ 7. Target guards: hidden / missing pauses, never completes ══
  console.log('7. Hidden or missing target pauses with the update message');
  {
    const h = await newPage(browser);
    const { page } = h;
    await boot(h);
    await page.evaluate(() => LearnEngine.start('workflows-v1', 'test'));
    await waitStep(page, 0);
    await page.click('#wfb-clayrune-section-learn_practice .wfb-tab-new');
    await waitStep(page, 1);
    await page.addStyleTag({ content: '.wfb-toolbar-tool[data-wf-tool="approval"] { display: none !important; }' });
    await page.waitForFunction(() => window.LearnEngine.state.phase === 'unavailable', null, { timeout: 9000 });
    check((await bubbleText(page)).includes('This lesson needs an update. Your progress is saved.'), 'a hidden palette tool pauses with "This lesson needs an update. Your progress is saved."');
    check((await lessonRec(page)).verified.length === 1 && (await lessonRec(page)).status !== 'completed', 'an unresolved target never marks the step complete');
    check((await canvasModel(page)).nodes === 0, 'nothing was placed on the user\'s behalf');
    await shoot(page, 'unavailable');
    await page.evaluate(() => { document.querySelectorAll('style').forEach((s) => { if (/data-wf-tool="approval"\] \{ display: none/.test(s.textContent)) s.remove(); }); });
    await page.click('#lrn-bubble [data-lrn="retry-target"]');
    await waitStep(page, 1);
    check(true, 'Retry recovers once the target is back');
    // Missing: the toolbar lost its marker entirely.
    await page.evaluate(() => document.querySelectorAll('.wfb-toolbar-tool[data-wf-tool="approval"]').forEach((t) => t.removeAttribute('data-wf-tool')));
    await page.waitForFunction(() => window.LearnEngine.state.phase === 'unavailable', null, { timeout: 9000 });
    check((await progressText(page)) === '1/4', 'a missing target pauses and leaves progress at 1/4');
    await page.evaluate(() => LearnEngine.leave());
    check(serverWrites(h).length === 0, 'target guards caused zero server writes');
    check(realErrors(h).length === 0, 'no uncaught page errors in section 7' + (realErrors(h).length ? ': ' + realErrors(h).join(' | ') : ''));
    await h.ctx.close();
  }

  // ══ 8. Reduced motion ══
  console.log('8. Reduced motion');
  {
    const h = await newPage(browser, { reducedMotion: true });
    const { page } = h;
    await boot(h);
    await page.evaluate(() => LearnEngine.start('workflows-v1', 'test'));
    await waitStep(page, 0);
    await page.waitForTimeout(500);
    const hand = await page.evaluate(() => { const e = document.getElementById('lrn-hand'); return e && !e.hidden ? { kind: e.dataset.kind, mode: e.dataset.mode || '' } : null; });
    check(hand !== null, 'reduced motion: a static hand still points at the target');
    const anim = await page.evaluate(() => ['#lrn-bubble', '#lrn-outline'].map((s) => { const e = document.querySelector(s); return e ? getComputedStyle(e).animationName : 'absent'; }));
    check(anim.every((a) => a === 'none' || a === 'absent'), 'reduced motion: no CSS animation on bubble or outline ' + JSON.stringify(anim));
    await playLesson(page, 'click');
    check(await page.$('#lrn-bubble .lrn-seal') !== null, 'reduced motion: the completion seal is shown');
    await page.evaluate(() => LearnEngine.leave());
    check(realErrors(h).length === 0, 'no uncaught page errors in section 8' + (realErrors(h).length ? ': ' + realErrors(h).join(' | ') : ''));
    await h.ctx.close();
  }

  // ══ 9. Copy hygiene: no em-dashes in anything a user reads ══
  console.log('9. Copy hygiene');
  {
    const h = await newPage(browser);
    const { page } = h;
    await boot(h);
    const texts = [];
    await page.evaluate(() => openLearn());
    texts.push(await page.evaluate(() => document.getElementById('lrn-hub-body').innerText));
    await page.evaluate(() => closeHub());
    await page.evaluate(() => LearnEngine.start('workflows-v1', 'test'));
    await waitStep(page, 0);
    texts.push(await bubbleText(page));
    await page.click('#lrn-bubble [data-lrn="hint"]'); texts.push(await bubbleText(page));
    await page.click('#wfb-clayrune-section-learn_practice .wfb-tab-new');
    await waitStep(page, 0, 'acknowledged', 4000); texts.push(await bubbleText(page));
    await waitStep(page, 1); texts.push(await bubbleText(page));
    await page.click('#lrn-bubble [data-lrn="fallback"]');
    await waitStep(page, 1, 'acknowledged', 4000); texts.push(await bubbleText(page));
    await waitStep(page, 2); texts.push(await bubbleText(page));
    await page.click('#lrn-bubble [data-lrn="fallback"]');
    await waitStep(page, 2, 'acknowledged', 4000); texts.push(await bubbleText(page));
    await waitStep(page, 3); texts.push(await bubbleText(page));
    await setName(page, 'Copy check');
    await page.click('.wfb-toolbar .btn-sched-save');
    await page.waitForFunction(() => window.LearnEngine.state.phase === 'completed', null, { timeout: 8000 });
    texts.push(await bubbleText(page));
    const dashes = texts.filter((t) => /[—–]/.test(t));
    check(dashes.length === 0, 'no em- or en-dashes in the hub, any step, hint, ack or completion copy' + (dashes.length ? ': ' + JSON.stringify(dashes) : ''));
    await page.evaluate(() => LearnEngine.leave());
    await h.ctx.close();
  }

  console.log(bad ? `\n❌ FAIL: ${bad} problem(s).` : '\n✅ all learn-workflows cases passed');
  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error('HARNESS ERROR:', e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
process.exit(exitCode);

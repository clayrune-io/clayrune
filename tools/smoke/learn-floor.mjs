#!/usr/bin/env node
/**
 * docs/TUTORIALS_SPEC.md pilot: the Learn hub + the three-step Floor lesson
 * (static/js/learn.js, static/js/learn-practice.js), end to end.
 *
 * Real headless boot (real index.html + real static/js/*.js, no network), same
 * hermetic shape as walkthrough.mjs / drag-to-hire.mjs. The fixture server owns
 * ONE real project ("Real Home") and ONE real Floor figure ("Fenn"); every
 * request that reaches it is recorded, so "practice never touches the server"
 * is asserted against what actually arrived, not against what the code says.
 *
 * Covered (acceptance list, minus the cross-client items Dave dropped):
 *   - every entry point reaches the one lesson: sidebar, mobile drawer, Ctrl+K,
 *     hub card, first-open offer, Floor header, Ask Claydo chip
 *   - each step advances ONLY on evidence; no Next control; time/clicks elsewhere
 *     advance nothing; a failed hire keeps the step and shows the real error
 *   - fixture-only writes: zero server writes, zero practice ids on the wire,
 *     dispatch from practice is refused with "Practice only"
 *   - missing / duplicate / hidden target pauses with the update message
 *   - Floor poll replacement and a mid-drag refresh
 *   - reload resume, offer dismissal survives reload, replay isolation
 *   - desktop 1400, 1920 (tile exposed), 960 boundary, 390 touch; keyboard-only
 *     completion; reduced motion + Quiet effects
 *
 * RUN
 *   cd tools/smoke && node learn-floor.mjs
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
  rooms: [{
    id: 'real_home', name: 'Real Home', emoji: '🧪', color: '',
    figures: [{
      session_id: 'sess-fenn', claude_session_id: 'csid-fenn', state: 'idle', reason: null,
      activity: '', task: 'reviewing a diff',
      character: { name: 'code-reviewer', display: 'Fenn', scope: 'global' },
      name: 'Fenn', name_from: 'character', avatar: 'fig:scholar', provider: 'claude',
      model: '', model_from: '', started_at: '', age: '5m', trigger_type: 'manual',
      hivemind_id: '', subagents: [],
    }],
  }],
  quiet: [], bench: [], counts: { rooms: 1, figures: 1, quiet: 0, bench: 0 },
  activity_states: false, poll_seconds: 5,
};
const REAL_CHARACTERS = [
  { name: 'code-reviewer', display_name: 'code-reviewer', agent_name: 'Fenn', scope: 'global',
    description: 'reviews a diff', engine: { provider: 'claude', model: 'claude-sonnet-5' } },
];

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, pass, failMsg) => (cond ? ok(pass) : fail(failMsg || pass));

// ── Page factory ─────────────────────────────────────────────────────────────
// `server` records everything that reaches the (fake) real server. Practice must
// leave it with NO writes and no practice id on any request.
async function newPage(browser, { width = 1400, height = 900, touch = false, reducedMotion = false, storage = null } = {}) {
  const ctx = await browser.newContext({
    viewport: { width, height }, hasTouch: touch, isMobile: touch,
    reducedMotion: reducedMotion ? 'reduce' : 'no-preference',
  });
  const page = await ctx.newPage();
  const server = { calls: [], sseAnswer: '' };
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
    if (path === '/api/guide/stream') {
      const a = server.sseAnswer;
      return route.fulfill({ status: 200, contentType: 'text/event-stream',
        body: `data: ${JSON.stringify({ type: 'delta', text: a })}\n\ndata: ${JSON.stringify({ type: 'done', answer: a })}\n\n` });
    }
    return route.abort();
  });
  return { ctx, page, server, pageErrors };
}

async function boot(h) {
  await h.page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await h.page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
}

// The CDN-loaded mermaid module cannot load in a hermetic page; same filter the other smokes use.
const realErrors = (h) => h.pageErrors.filter((e) => !/Failed to fetch dynamically imported module|aborted|net::ERR|EventSource/i.test(e));
const state = (page) => page.evaluate(() => window.LearnEngine.state);
const waitStep = (page, step, phase = 'active', timeout = 8000) =>
  page.waitForFunction(([s, p]) => { const st = window.LearnEngine.state; return st && st.step === s && st.phase === p; }, [step, phase], { timeout });
const bubbleText = (page) => page.evaluate(() => (document.getElementById('lrn-bubble') || {}).innerText || '');
const progressText = (page) => page.evaluate(() => (document.querySelector('#lrn-bubble .lrn-progress') || {}).textContent || '');
const lessonRec = (page) => page.evaluate(() => window.LearnEngine.progress());
const serverWrites = (h) => h.server.calls.filter((c) => c.method !== 'GET' && c.method !== 'HEAD');
const practiceOnWire = (h) => h.server.calls.filter((c) => /learn_practice|learn-pip|learn\.practice/.test(c.path + c.body));
async function shoot(page, name) { if (SHOT_DIR) await page.screenshot({ path: resolve(SHOT_DIR, `learn-floor-${name}.png`) }); }

async function retryKeyboard(page, mode, act, done) {
  for (let i = 0; ; i++) {
    await act();
    try { await done(); return; } catch (e) { if (mode !== 'keyboard' || i >= 3) throw e; }
  }
}

// Drive the lesson to completion the way a user would. mode: click | keyboard | touch
async function playLesson(page, mode) {
  const press = async (sel) => {
    if (mode === 'keyboard') { await page.focus(sel); await page.keyboard.press('Enter'); }
    else if (mode === 'touch') await page.tap(sel);
    else await page.click(sel);
  };
  await waitStep(page, 0);
  // The Floor's own 5 s poll rebuilds #floor-body and drops keyboard focus, so a
  // focus-then-key can land on nothing; a keyboard user simply presses again.
  await retryKeyboard(page, mode, () => press('#floor-body .fl-fig[data-fl-session="learn-pip"]'), () => waitStep(page, 1, 'active', 2500));
  // Opening the chat covers the Floor; Back to Floor is the lesson's own navigation.
  await page.waitForSelector('#lrn-bubble [data-lrn="back-floor"]', { timeout: 5000 });
  await press('#lrn-bubble [data-lrn="back-floor"]');
  await page.waitForFunction(() => { const m = document.querySelector('.modal-window[data-modal-id="__floor"]'); return m && m.classList.contains('focused'); });
  await retryKeyboard(page, mode, async () => {
    const sel = '#floor-body .fl-bench-card[data-fl-type="project:guide"] .fl-bench-main';
    if (mode === 'keyboard') { await page.focus(sel); await page.keyboard.press('Space'); } else await press(sel);
  }, () => waitStep(page, 2, 'active', 2500));
  await page.waitForSelector('#lrn-bubble [data-lrn="hire"]', { timeout: 5000 });
  await press('#lrn-bubble [data-lrn="hire"]');
  await page.waitForFunction(() => { const s = window.LearnEngine.state; return s && s.phase === 'completed'; }, null, { timeout: 8000 });
}

// ── Sections ─────────────────────────────────────────────────────────────────
let browser, exitCode = 1;
try {
  browser = await chromium.launch();

  // ══ 1. Entry points, offer, hub (fresh browser) ══
  console.log('1. Entry points, first-open offer, hub');
  {
    const h = await newPage(browser);
    const { page } = h;
    await boot(h);

    // Sidebar + palette entries exist; hub has exactly one card with the specced copy.
    check(await page.$('.sidebar-item[data-nav="learn"]') !== null, 'sidebar has a Learn item');
    check(await page.$('.mobile-drawer-item[data-nav="learn"]') !== null, 'mobile drawer has a Learn item');
    await page.click('.sidebar-item[data-nav="learn"]');
    await page.waitForSelector('#lrn-hub-body .lrn-card', { timeout: 3000 });
    const hub = await page.evaluate(() => ({
      title: document.querySelector('.modal-window[data-modal-id="__learn"] .modal-header').innerText.trim(),
      cards: document.querySelectorAll('#lrn-hub-body .lrn-card').length,
      text: document.querySelector('#lrn-hub-body .lrn-card').innerText,
      btn: document.querySelector('#lrn-hub-body .lrn-card-go').innerText.trim(),
    }));
    check(hub.title.startsWith('Learn with Claydo'), 'hub titled "Learn with Claydo"');
    check(hub.cards === 1, 'hub shows exactly one card');
    check(/The Floor/.test(hub.text) && /Find a figure, open a chat, hire a type\./.test(hub.text) && /3 actions · Practice only/.test(hub.text),
      'card carries the specced title, subtitle and metadata');
    check(hub.btn === 'Start', 'new user sees the Start state');
    await shoot(page, 'hub');
    await page.evaluate(() => closeHub());

    // Ctrl+K palette entry.
    await page.keyboard.press('Control+k');
    await page.fill('#cmd-input', 'learn');
    await page.waitForTimeout(150);
    const palette = await page.evaluate(() => Array.from(document.querySelectorAll('#cmd-results *')).map((e) => e.textContent).join('|'));
    check(/Learn with Claydo/.test(palette), 'Ctrl+K palette lists "Learn with Claydo"');
    await page.keyboard.press('Escape');

    // First Floor opening offers once. The offer is non-blocking: no backdrop.
    await page.evaluate(() => window.openFloor());
    await page.waitForSelector('#lrn-offer', { timeout: 4000 });
    const offer = await page.evaluate(() => ({ text: document.getElementById('lrn-offer').innerText, backdrop: !!document.querySelector('.wt-backdrop, #wt-overlay') }));
    check(/Meet the Floor in three actions\. Want to try\?/.test(offer.text) && /Start practice/.test(offer.text) && /Not now/.test(offer.text),
      'first Floor opening shows the specced offer with Start practice / Not now');
    check(!offer.backdrop, 'the offer does not block the page (no backdrop)');
    await shoot(page, 'offer');
    await page.click('#lrn-offer [data-offer="no"]');
    check(await page.$('#lrn-offer') === null, 'Not now dismisses the offer');
    await page.evaluate(() => { closeModalById('__floor'); });
    await page.evaluate(() => window.openFloor());
    await page.waitForTimeout(500);
    check(await page.$('#lrn-offer') === null, 'reopening the Floor does not re-offer');
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(() => window.openFloor());
    await page.waitForTimeout(500);
    check(await page.$('#lrn-offer') === null, 'dismissal survives a reload (never re-offered)');

    // The header replay starts the same lesson id.
    await page.evaluate(() => { closeModalById('__floor'); window.openFloor(); });
    await page.waitForSelector('.modal-window[data-modal-id="__floor"] button:has-text("Learn the Floor")');
    await page.click('button:has-text("Learn the Floor")');
    await waitStep(page, 0);
    check((await state(page)).lesson === 'floor-v1', 'Floor header "Learn the Floor" starts floor-v1');
    await page.evaluate(() => LearnEngine.leave());

    // Hub Start button.
    await page.evaluate(() => openLearn());
    await page.click('#lrn-hub-body .lrn-card-go');
    await waitStep(page, 0);
    check((await state(page)).lesson === 'floor-v1', 'hub Start button starts floor-v1');
    check(await page.$('.modal-window[data-modal-id="__learn"]') === null, 'the hub closes when the lesson starts');
    await page.evaluate(() => LearnEngine.leave());

    // Ask Claydo chip: registered id yields the chip, a click starts the lesson;
    // an unregistered id yields no chip and nothing launches.
    h.server.sseAnswer = 'Here is a guided way in. [clayrune:lesson id="floor-v1"]';
    await page.evaluate(() => openClaydo());
    await page.waitForSelector('#claydo-input');
    await page.fill('#claydo-input', 'Teach me the Floor');
    await page.evaluate(() => submitClaydo());
    await page.waitForSelector('.claydo-lesson-chip', { timeout: 5000 });
    const chip = await page.evaluate(() => ({ t: document.querySelector('.claydo-lesson-chip').textContent, live: window.LearnEngine.state }));
    check(chip.t === 'Start Floor practice', 'a lesson marker renders the "Start Floor practice" chip');
    check(chip.live === null, 'model output alone launches nothing');
    await page.evaluate(() => document.querySelector('.claydo-lesson-chip').click());
    await waitStep(page, 0);
    check(true, 'clicking the chip starts the lesson');
    await page.evaluate(() => LearnEngine.leave());

    h.server.sseAnswer = 'Try this. [clayrune:lesson id="not-a-lesson"] [clayrune:lesson id="x\\" onclick=alert(1)"]';
    await page.fill('#claydo-input', 'Teach me something else');
    await page.evaluate(() => submitClaydo());
    await page.waitForTimeout(900);
    const chips = await page.evaluate(() => document.querySelectorAll('.claydo-lesson-chip').length);
    check(chips === 1, 'an unregistered lesson id adds no chip (only the earlier registered one remains)');
    check(await page.evaluate(() => LearnEngine.start('nope', 'x')) === false, 'LearnEngine.start refuses an unknown lesson id');

    check(realErrors(h).length === 0, 'no uncaught page errors in section 1' + (realErrors(h).length ? ': ' + realErrors(h).join(' | ') : ''));
    await h.ctx.close();
  }

  // ══ 2. Full lesson, desktop, mouse: evidence, fixture-only writes, replay ══
  console.log('2. Desktop lesson: evidence-only steps, fixture-only writes, replay isolation');
  {
    const h = await newPage(browser);
    const { page } = h;
    await boot(h);
    const sentinelBefore = await page.evaluate(() => JSON.stringify(allProjects.map((p) => [p.id, p.name, (p.roster || []).length])));
    const callsBefore = h.server.calls.length;
    await page.evaluate(() => LearnEngine.start('floor-v1', 'test'));
    await waitStep(page, 0);

    // Practice context: exactly one room, one figure, one Bench type; banner; tile.
    const ctxInfo = await page.evaluate(() => ({
      rooms: document.querySelectorAll('#floor-body .fl-room').length,
      figs: document.querySelectorAll('#floor-body .fl-fig').length,
      bench: document.querySelectorAll('#floor-body .fl-bench-card').length,
      roomName: (document.querySelector('#floor-body .fl-room-name') || {}).textContent,
      fig: (document.querySelector('#floor-body .fl-fig') || {}).innerText,
      benchName: (document.querySelector('#floor-body .fl-bench-card .fl-who') || {}).textContent,
      tile: !!document.querySelector('#projects-col .card[data-id="learn_practice"]'),
      realTile: !!document.querySelector('#projects-col .card[data-id="real_home"]'),
      banner: (document.getElementById('lrn-banner') || {}).innerText,
      bannerShown: (() => { const b = document.getElementById('lrn-banner'); return !!b && getComputedStyle(b).display !== 'none' && b.getBoundingClientRect().height > 0; })(),
    }));
    check(ctxInfo.rooms === 1 && ctxInfo.figs === 1 && ctxInfo.bench === 1, 'practice Floor holds exactly one room, one figure, one Bench type');
    check(/Learn practice/.test(ctxInfo.roomName) && /Pip/.test(ctxInfo.fig) && ctxInfo.benchName === 'Guide', 'fixture is Learn practice / Pip / Guide, drawn by the real renderers');
    check(ctxInfo.tile && !ctxInfo.realTile, 'dashboard shows the practice tile and none of the real projects');
    check(ctxInfo.bannerShown && /Practice only/.test(ctxInfo.banner), 'a persistent "Practice only" banner is visible');
    check(!/Fenn|Real Home/.test(await page.evaluate(() => document.getElementById('floor-body').innerText)), 'no real figure or project leaks into the practice Floor');
    check((await progressText(page)) === '0/3', 'progress reads 0/3');
    check(!(await bubbleText(page)).match(/\bNext\b/), 'the bubble has no Next control');
    const ctrl = await page.evaluate(() => Array.from(document.querySelectorAll('#lrn-bubble [data-lrn]')).map((b) => b.textContent.trim()));
    check(['Pause', 'Hint', 'Leave practice'].every((c) => ctrl.includes(c)), 'bubble offers Pause, Hint and Leave practice');
    await shoot(page, 'step1');

    // Nothing but the action advances: wait, click around, open the picker out of step.
    await page.waitForTimeout(1500);
    await page.click('#floor-body .fl-room-head', { trial: false }).catch(() => {});
    await page.click('#lrn-bubble [data-lrn="hint"]');
    check((await bubbleText(page)).includes('Hint:'), 'Hint repeats the cue');
    check((await state(page)).step === 0 && (await progressText(page)) === '0/3', 'time, clicks elsewhere and Hint advance nothing');

    // An action report with no rendered transcript behind it is not evidence.
    await page.evaluate(() => LearnEngine.notify('open-figure', {}));
    await page.waitForTimeout(700);
    check((await state(page)).step === 0 && (await progressText(page)) === '0/3', 'an open-chat report without the rendered transcript advances nothing');

    // Step 1: open the chat. The verdict is the rendered practice transcript.
    await page.click('#floor-body .fl-fig[data-fl-session="learn-pip"]');
    await page.waitForSelector('#agent-output-learn-pip', { timeout: 5000 });
    await waitStep(page, 0, 'acknowledged', 4000);
    check((await bubbleText(page)).includes('That’s Pip’s session. A figure opens a conversation.'), 'step 1 acknowledgment shown after the transcript renders');
    check((await progressText(page)) === '1/3', 'progress reads 1/3 after step 1');
    const transcript = await page.evaluate(() => document.getElementById('agent-output-learn-pip').innerText);
    check(transcript.includes('This is a practice conversation. No agent is running.'), 'Pip\'s chat reads the specced canned transcript');
    await shoot(page, 'step1-done');
    await waitStep(page, 1);
    check(await page.$('#agent-output-learn-pip') !== null, 'step 2 appears with the chat left open');
    check((await bubbleText(page)).includes('Back on the Floor, find Guide on the Bench.'), 'step 2 shows its specced copy');

    // Step 2: type selection is NOT a hire. Back to Floor is navigation only.
    await page.click('#lrn-bubble [data-lrn="back-floor"]');
    await page.waitForFunction(() => document.querySelector('.modal-window[data-modal-id="__floor"]').classList.contains('focused'));
    check((await state(page)).step === 1, 'Back to Floor navigates without completing the step');
    await page.waitForSelector('#lrn-outline:not([hidden])', { timeout: 3000 });
    const outline = await page.evaluate(() => {
      const o = document.getElementById('lrn-outline').getBoundingClientRect();
      const c = document.querySelector('#floor-body .fl-bench-card').getBoundingClientRect();
      return { near: Math.abs(o.left - c.left) < 12 && Math.abs(o.top - c.top) < 12, tail: document.getElementById('lrn-bubble').dataset.tail || '' };
    });
    check(outline.near, 'the soft outline sits on the Guide card');
    check(!!outline.tail, 'the bubble tail points at the target (' + outline.tail + ')');
    await shoot(page, 'step2');
    await page.click('#floor-body .fl-bench-card[data-fl-type="project:guide"] .fl-bench-main');
    await waitStep(page, 1, 'acknowledged', 4000);
    check((await bubbleText(page)).includes('Guide is a type. Now give it a project.'), 'step 2 acknowledgment shown once the picker is open');
    check(await page.evaluate(() => !!document.querySelector('.fl-bench-open .fl-pick-row')), 'the verdict state is real: card open with its pick row');
    check((await lessonRec(page)).verified.length === 2, 'two verified actions saved');
    check(h.server.calls.slice(callsBefore).filter((c) => c.method !== 'GET').length === 0, 'opening a type is not a hire: no write');
    await waitStep(page, 2);

    // Step 3 is a refused hire first (failure keeps the step), then the real one.
    check((await page.evaluate(() => (LearnPractice.roster().length))) === 0, 'practice roster is empty before the hire');
    await page.evaluate(() => LearnPractice.failNextHire('Practice hire failed on purpose.'));
    await page.click('#lrn-bubble [data-lrn="hire"]');
    await page.waitForSelector('#lrn-bubble .lrn-err', { timeout: 4000 });
    check((await bubbleText(page)).includes('Practice hire failed on purpose.'), 'a failed hire shows the actual error');
    check((await state(page)).step === 2 && (await progressText(page)) === '2/3', 'a failed hire keeps step 3 (2/3)');
    check(await page.$('#lrn-bubble [data-lrn="hire"]') !== null, 'Retry hire is offered');
    await shoot(page, 'step3-error');
    await page.click('#lrn-bubble [data-lrn="hire"]');
    await page.waitForFunction(() => window.LearnEngine.state && window.LearnEngine.state.phase === 'completed', null, { timeout: 6000 });
    check((await progressText(page)) === '3/3', 'progress reads 3/3 on completion');
    const done = await bubbleText(page);
    check(done.includes('You found a figure, opened its chat, and hired a type. Your real projects are untouched.'), 'completion copy matches the spec');
    check(['Return to Floor', 'Replay', 'Learn'].every((c) => done.includes(c)), 'completion offers Return to Floor, Replay, Learn');
    check(await page.$('#lrn-bubble .lrn-seal') !== null, 'the finish seal is shown');
    await shoot(page, 'complete');
    const rosterNow = await page.evaluate(() => LearnPractice.roster());
    check(rosterNow.length === 1 && rosterNow[0].character === 'project:guide' && rosterNow[0].hired_by === 'menu', 'the hire is committed to the practice roster (by the Hire control)');

    // Server-side silence.
    const after = h.server.calls.slice(callsBefore);
    check(serverWrites(h).length === 0, 'ZERO writes reached the server during practice');
    check(practiceOnWire(h).length === 0, 'no practice id appeared on any request to the server');
    check((await page.evaluate(() => LearnPractice.refused)).length === 0, 'nothing was refused on the happy path');

    // Dispatch / send / publish from practice is refused client-side, visibly.
    const dispatchRes = await page.evaluate(async () => {
      const r = await fetch('/api/project/learn_practice/agent/dispatch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ task: 'x' }) });
      return { status: r.status, body: await r.json() };
    });
    check(dispatchRes.status === 403 && dispatchRes.body.practice_only === true, 'dispatch from practice is refused (403, practice_only)');
    const realDispatch = await page.evaluate(async () => {
      const r = await fetch('/api/project/real_home/agent/dispatch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ task: 'x' }) });
      return r.status;
    });
    check(realDispatch === 403, 'a write aimed at a REAL project is refused while practicing too');
    check((await page.evaluate(() => document.body.innerText)).includes('Practice only. This would reach your real projects, so it was not sent.'), 'the refusal is visible as a "Practice only" message');
    check(serverWrites(h).length === 0, 'refused requests never reached the server');

    // Leave: routing + overlays gone, real data back, progress kept.
    await page.click('#lrn-bubble [data-lrn="return-floor"]');
    await page.evaluate(() => LearnEngine.leave());
    await page.waitForFunction(() => !document.querySelector('#projects-col .card[data-id="learn_practice"]'), null, { timeout: 5000 });
    check(await page.$('#lrn-root') === null, 'leaving removes the overlay');
    check(await page.$('#lrn-banner') === null, 'leaving removes the practice banner');
    const sentinelAfter = await page.evaluate(() => JSON.stringify(allProjects.map((p) => [p.id, p.name, (p.roster || []).length])));
    check(sentinelAfter === sentinelBefore, 'before/after sentinel of real projects and rosters is unchanged');
    check(await page.evaluate(() => !!document.querySelector('#projects-col .card[data-id="real_home"]')), 'the real dashboard is back');
    await page.waitForFunction(() => /Fenn/.test(document.getElementById('floor-body').innerText), null, { timeout: 7000 })
      .then(() => ok('the real Floor is back (Fenn)'), () => fail('the real Floor did not come back after leaving'));
    check(!(await page.evaluate(() => window.LearnPractice.active)), 'practice routing is off after leaving');
    const rec = await lessonRec(page);
    check(rec.status === 'completed' && rec.completions === 1, 'completion is recorded (completions=1)');

    // Replay: fresh run, empty practice roster, history preserved, hub says Replay.
    await page.evaluate(() => openLearn());
    check((await page.$eval('#lrn-hub-body .lrn-card-go', (b) => b.textContent.trim())) === 'Replay', 'hub shows Replay after completion');
    check(await page.$('#lrn-hub-body .lrn-card') !== null, 'the completed card is not hidden');
    const oldRun = rec.runId;
    await page.click('#lrn-hub-body .lrn-card-go');
    await waitStep(page, 0);
    const rec2 = await lessonRec(page);
    check(rec2.runId !== oldRun, 'Replay starts a fresh run id');
    check((await page.evaluate(() => LearnPractice.roster().length)) === 0, 'Replay starts with an empty practice roster');
    check(rec2.completions === 1 && rec2.verified.some((v) => v.run === oldRun), 'completion history survives the replay');
    check(await page.evaluate(() => !document.querySelector('.fl-bench-open') && !!document.querySelector('#floor-body .fl-bench-card')), 'replay Floor shows an un-hired, closed Guide card');
    check(await page.evaluate(() => !document.getElementById('agent-output-learn-pip')), 'replay does not carry the old open chat');
    check(serverWrites(h).length === 0 && practiceOnWire(h).length === 0, 'still zero server writes and no practice ids after the replay');
    await page.evaluate(() => LearnEngine.leave());

    check(realErrors(h).length === 0, 'no uncaught page errors in section 2' + (realErrors(h).length ? ': ' + realErrors(h).join(' | ') : ''));
    await h.ctx.close();
  }

  // ══ 3. Resume after reload, Pause/Escape, Leave ══
  console.log('3. Reload resume, Pause, Escape, Leave');
  {
    const h = await newPage(browser);
    const { page } = h;
    await boot(h);
    await page.evaluate(() => LearnEngine.start('floor-v1', 'test'));
    await waitStep(page, 0);
    await page.click('#floor-body .fl-fig[data-fl-session="learn-pip"]');
    await waitStep(page, 1);
    const run = (await lessonRec(page)).runId;

    // Escape inside the bubble pauses, never completes.
    await page.focus('#lrn-bubble');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => window.LearnEngine.state && window.LearnEngine.state.phase === 'paused');
    check((await bubbleText(page)).includes('Paused. Your progress is saved.'), 'Escape pauses with the saved-progress message');
    check((await lessonRec(page)).status === 'paused', 'paused is saved, not completed');
    await page.click('#lrn-bubble [data-lrn="resume"]');
    await waitStep(page, 1);
    check((await progressText(page)) === '1/3', 'Resume returns to step 2 with 1/3 kept');
    await page.click('#lrn-bubble [data-lrn="pause"]');
    await page.waitForFunction(() => window.LearnEngine.state.phase === 'paused');

    // Reload while paused at step 2.
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    check(await page.evaluate(() => window.LearnEngine.state === null && !window.LearnPractice.active), 'after reload nothing is running and practice is off');
    check(await page.evaluate(() => !document.querySelector('#projects-col .card[data-id="learn_practice"]')), 'the practice tile is gone after reload (real dashboard)');
    await page.evaluate(() => openLearn());
    check((await page.$eval('#lrn-hub-body .lrn-card-go', (b) => b.textContent.trim())) === 'Resume', 'hub shows Resume after a reload mid-lesson');
    await page.click('#lrn-hub-body .lrn-card-go');
    await waitStep(page, 1);
    const rec = await lessonRec(page);
    check(rec.runId === run, 'resume reuses the same run');
    check((await progressText(page)) === '1/3', 'resume shows 1/3');
    check(!(await bubbleText(page)).includes('Open this chat to see'), 'resume lands on step 2, not step 1');
    await page.evaluate(() => LearnEngine.leave());
    check((await lessonRec(page)).status === 'paused', 'Leave practice keeps the progress (status paused, step kept)');
    check(serverWrites(h).length === 0, 'resume/leave caused zero server writes');

    // A changed unfinished step contract: explained restart, never silently reused.
    await page.evaluate(() => {
      const p = JSON.parse(localStorage.getItem('learn.progress'));
      p.lessons['floor-v1'].fixture = 0;
      localStorage.setItem('learn.progress', JSON.stringify(p));
    });
    await page.evaluate(() => openLearn());
    await page.click('#lrn-hub-body .lrn-card-go');
    await page.waitForSelector('#lrn-bubble [data-lrn="restart"]', { timeout: 5000 });
    check(/Only practice state resets/.test(await bubbleText(page)), 'a stale fixture offers "Restart practice" and says only practice state resets');
    await page.click('#lrn-bubble [data-lrn="restart"]');
    await waitStep(page, 0);
    check((await lessonRec(page)).runId !== run, 'Restart practice starts a new run');
    await page.evaluate(() => LearnEngine.leave());

    check(realErrors(h).length === 0, 'no uncaught page errors in section 3' + (realErrors(h).length ? ': ' + realErrors(h).join(' | ') : ''));
    await h.ctx.close();
  }

  // ══ 4. Target guards (missing / duplicate / hidden), poll replacement ══
  console.log('4. Target guards and Floor poll replacement');
  {
    const h = await newPage(browser);
    const { page } = h;
    await boot(h);
    await page.evaluate(() => LearnEngine.start('floor-v1', 'test'));
    await waitStep(page, 0);

    // Poll replacement: the Floor rebuilds #floor-body; the outline must follow the NEW node.
    await page.evaluate(() => { document.querySelector('#floor-body .fl-fig').dataset.old = '1'; });
    await page.evaluate(() => window.refreshFloor());
    await page.waitForFunction(() => document.querySelector('#floor-body .fl-fig') && !document.querySelector('#floor-body .fl-fig').dataset.old);
    await page.waitForTimeout(450);
    const follow = await page.evaluate(() => {
      const o = document.getElementById('lrn-outline'), f = document.querySelector('#floor-body .fl-fig').getBoundingClientRect(), r = o.getBoundingClientRect();
      return { shown: !o.hidden, near: Math.abs(r.left - f.left) < 12 && Math.abs(r.top - f.top) < 12 };
    });
    check(follow.shown && follow.near, 'after a poll replaces the nodes, the outline reacquires the new figure');
    check((await state(page)).phase === 'active', 'a poll does not pause or reset the step');
    check((await progressText(page)) === '0/3', 'a poll does not change progress');

    // Hidden target: a persistent style survives the rebuilds.
    await page.addStyleTag({ content: '#floor-body .fl-fig { display: none !important; }' });
    await page.waitForFunction(() => window.LearnEngine.state.phase === 'unavailable', null, { timeout: 9000 });
    check((await bubbleText(page)).includes('This lesson needs an update. Your progress is saved.'), 'a hidden target pauses with "This lesson needs an update. Your progress is saved."');
    check(await page.$('#lrn-bubble [data-lrn="retry-target"]') !== null && (await bubbleText(page)).includes('Learn'), 'the unavailable state offers Retry and Learn');
    check((await lessonRec(page)).verified.length === 0 && (await lessonRec(page)).status !== 'completed', 'an unresolved target never marks the step complete');
    check(await page.evaluate(() => !document.querySelector('.wt-highlight, .wt-demo')), 'no demo markup is substituted for a missing target');
    await shoot(page, 'unavailable');
    await page.evaluate(() => { document.querySelectorAll('style').forEach((s) => { if (/fl-fig \{ display: none/.test(s.textContent)) s.remove(); }); });
    await page.click('#lrn-bubble [data-lrn="retry-target"]');
    await waitStep(page, 0);
    check(true, 'Retry recovers once the target is back');

    // Duplicate target: re-add a clone after every rebuild.
    await page.evaluate(() => {
      window.__dupObs = new MutationObserver(() => {
        const body = document.getElementById('floor-body');
        const figs = body ? body.querySelectorAll('.fl-room .fl-fig[data-fl-session="learn-pip"]') : [];
        if (figs.length === 1) figs[0].parentElement.appendChild(figs[0].cloneNode(true));
      });
      window.__dupObs.observe(document.getElementById('floor-body'), { childList: true, subtree: true });
      document.querySelector('#floor-body .fl-room .fl-fig').parentElement.appendChild(document.querySelector('#floor-body .fl-room .fl-fig').cloneNode(true));
    });
    await page.waitForFunction(() => window.LearnEngine.state.phase === 'unavailable', null, { timeout: 9000 });
    check(true, 'a duplicate target (two matches) pauses the step too');
    await page.evaluate(() => window.__dupObs.disconnect());

    // Missing target: remove it after every rebuild.
    await page.evaluate(() => { window.refreshFloor(); });
    await page.evaluate(() => {
      window.__missObs = new MutationObserver(() => { document.querySelectorAll('#floor-body .fl-fig').forEach((f) => f.remove()); });
      window.__missObs.observe(document.getElementById('floor-body'), { childList: true, subtree: true });
      document.querySelectorAll('#floor-body .fl-fig').forEach((f) => f.remove());
    });
    await page.click('#lrn-bubble [data-lrn="retry-target"]');
    await page.waitForFunction(() => window.LearnEngine.state.phase === 'unavailable', null, { timeout: 9000 });
    check((await progressText(page)) === '0/3', 'a missing target pauses and leaves progress at 0/3');
    await page.evaluate(() => window.__missObs.disconnect());
    await page.evaluate(() => LearnEngine.leave());
    check(serverWrites(h).length === 0, 'target guards caused zero server writes');
    check(realErrors(h).length === 0, 'no uncaught page errors in section 4' + (realErrors(h).length ? ': ' + realErrors(h).join(' | ') : ''));
    await h.ctx.close();
  }

  // ══ 5. Real drag hire, cancelled drag, mid-drag refresh, tile exposure ══
  console.log('5. Drag hire at 1920 (tile exposed), cancelled drag, mid-drag refresh');
  {
    const h = await newPage(browser, { width: 1920, height: 1000 });
    const { page } = h;
    await boot(h);
    await page.evaluate(() => LearnEngine.start('floor-v1', 'test'));
    await waitStep(page, 0);
    await page.click('#floor-body .fl-fig[data-fl-session="learn-pip"]');
    await waitStep(page, 1);
    await page.click('#lrn-bubble [data-lrn="back-floor"]');
    await page.waitForFunction(() => document.querySelector('.modal-window[data-modal-id="__floor"]').classList.contains('focused'));
    await page.click('#floor-body .fl-bench-card[data-fl-type="project:guide"] .fl-bench-main');
    await waitStep(page, 2);
    await page.waitForTimeout(500);

    const layout = await page.evaluate(() => {
      const t = document.querySelector('#projects-col .card[data-id="learn_practice"]').getBoundingClientRect();
      const w = document.querySelector('.modal-window[data-modal-id="__floor"] .modal-content').getBoundingClientRect();
      const hit = document.elementFromPoint(t.left + t.width / 2, t.top + t.height / 2);
      return { clear: t.right <= w.left || t.left >= w.right, hitTile: !!(hit && hit.closest('.card[data-id="learn_practice"]')) };
    });
    check(layout.clear && layout.hitTile, 'on a wide viewport the lesson moved the Floor so the practice tile is visible and reachable');
    await page.waitForSelector('#lrn-arrow:not([hidden])', { timeout: 3000 });
    check(true, 'the arrow shows from the Bench card toward the tile');
    await shoot(page, 'step3-wide');

    // Cancelled drag: grab the card, release over empty space. Nothing hired.
    const card = await page.$eval('#floor-body .fl-bench-card[data-fl-type="project:guide"]', (el) => { const r = el.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; });
    await page.mouse.move(card.x, card.y);
    await page.mouse.down();
    await page.mouse.move(card.x + 60, card.y + 20, { steps: 6 });
    await page.waitForSelector('body.hire-active', { timeout: 3000 });
    // Mid-drag refresh: the Floor poll must not rebuild under the held card.
    await page.evaluate(() => window.refreshFloor());
    await page.waitForTimeout(300);
    check(await page.evaluate(() => document.body.classList.contains('hire-active')), 'a refresh in the middle of a drag leaves the drag alive');
    await page.mouse.move(card.x + 60, card.y + 400, { steps: 6 });
    await page.mouse.up();
    await page.waitForTimeout(600);
    check((await state(page)).step === 2 && (await state(page)).phase === 'active', 'a cancelled drag leaves step 3 active');
    check(await page.evaluate(() => LearnPractice.roster().length) === 0, 'a cancelled drag hires nothing');

    // Real drop on the practice tile: hired_by drag, step verified by the roster.
    const card2 = await page.$eval('#floor-body .fl-bench-card[data-fl-type="project:guide"]', (el) => { const r = el.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; });
    const tile = await page.$eval('#projects-col .card[data-id="learn_practice"]', (el) => { const r = el.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; });
    await page.mouse.move(card2.x, card2.y);
    await page.mouse.down();
    await page.mouse.move(card2.x - 60, card2.y + 10, { steps: 6 });
    await page.waitForSelector('body.hire-active', { timeout: 3000 });
    await page.mouse.move(tile.x, tile.y, { steps: 12 });
    await page.mouse.up();
    await page.waitForFunction(() => window.LearnEngine.state && window.LearnEngine.state.phase === 'completed', null, { timeout: 8000 });
    const hire = await page.evaluate(() => LearnPractice.roster());
    check(hire.length === 1 && hire[0].hired_by === 'drag', 'the real drag-and-drop hire completes the lesson (hired_by=drag)');
    check(serverWrites(h).length === 0 && practiceOnWire(h).length === 0, 'drag hire caused zero server writes');
    await page.evaluate(() => LearnEngine.leave());
    check(realErrors(h).length === 0, 'no uncaught page errors in section 5' + (realErrors(h).length ? ': ' + realErrors(h).join(' | ') : ''));
    await h.ctx.close();
  }

  // ══ 6. Keyboard-only completion at the 960 boundary ══
  console.log('6. Keyboard-only completion (960 px)');
  {
    const h = await newPage(browser, { width: 960, height: 800 });
    const { page } = h;
    await boot(h);
    await page.evaluate(() => LearnEngine.start('floor-v1', 'test'));
    await playLesson(page, 'keyboard');
    check(true, 'all three steps completed with keyboard only, no drag');
    const roster = await page.evaluate(() => LearnPractice.roster());
    check(roster.length === 1 && roster[0].hired_by === 'menu', 'keyboard hire went through the Hire control');
    await page.evaluate(() => LearnEngine.leave());
    check(serverWrites(h).length === 0, 'keyboard run: zero server writes');
    check(realErrors(h).length === 0, 'no uncaught page errors in section 6' + (realErrors(h).length ? ': ' + realErrors(h).join(' | ') : ''));
    await h.ctx.close();
  }

  // ══ 7. Mobile 390: touch, bottom-docked bubble, drawer entry ══
  console.log('7. Mobile 390 touch');
  {
    const h = await newPage(browser, { width: 390, height: 844, touch: true });
    const { page } = h;
    await boot(h);
    await page.evaluate(() => window.openMobileDrawer ? window.openMobileDrawer() : null);
    check(await page.evaluate(() => typeof window.mobileDrawerNav === 'function'), 'the mobile drawer nav handler exists');
    await page.evaluate(() => mobileDrawerNav('learn'));
    await page.waitForSelector('#lrn-hub-body .lrn-card', { timeout: 4000 });
    check(true, 'the mobile drawer Learn entry opens the hub');
    await page.evaluate(() => closeHub());
    await page.evaluate(() => LearnEngine.start('floor-v1', 'test'));
    await waitStep(page, 0);
    const dock = await page.evaluate(() => {
      const b = document.getElementById('lrn-bubble').getBoundingClientRect();
      const tab = document.getElementById('bottom-tab-bar');
      const tr = tab && getComputedStyle(tab).display !== 'none' ? tab.getBoundingClientRect() : null;
      return { bottom: b.bottom, left: b.left, right: b.right, vh: window.innerHeight, vw: window.innerWidth, tabTop: tr ? tr.top : null, mobileClass: document.getElementById('lrn-bubble').classList.contains('lrn-mobile') };
    });
    check(dock.mobileClass && dock.left <= 12 && dock.vw - dock.right <= 12, 'at 390 px the bubble is a full-width bottom dock');
    check(dock.tabTop === null ? dock.bottom <= dock.vh : dock.bottom <= dock.tabTop + 1, 'the dock sits above the bottom navigation / safe area');
    const targets = await page.evaluate(() => Array.from(document.querySelectorAll('#lrn-bubble button')).map((b) => { const r = b.getBoundingClientRect(); return [b.textContent.trim().slice(0, 14), Math.round(r.height), Math.round(r.width)]; }).filter(([, hgt, wid]) => hgt > 0 || wid > 0));
    check(targets.every(([, hgt, wid]) => hgt >= 44 || wid >= 44 && hgt >= 40), 'tutorial controls are touch-sized (>=44 px)' + (targets.some(([, hgt]) => hgt < 44) ? ' ' + JSON.stringify(targets) : ''));
    const noTouchNone = await page.evaluate(() => !Array.from(document.querySelectorAll('#lrn-root, #lrn-root *')).some((e) => getComputedStyle(e).touchAction === 'none'));
    check(noTouchNone, 'no fixed touch-action: none on the tutorial layer');
    await shoot(page, 'mobile-step1');
    // Collapse leaves the task and progress visible.
    await page.tap('#lrn-bubble [data-lrn="collapse"]');
    const coll = await page.evaluate(() => { const b = document.getElementById('lrn-bubble'); return { collapsed: b.classList.contains('lrn-collapsed'), task: !!b.querySelector('.lrn-task') && b.querySelector('.lrn-task').getBoundingClientRect().height > 0, prog: b.querySelector('.lrn-progress').getBoundingClientRect().height > 0 }; });
    check(coll.collapsed && coll.task && coll.prog, 'collapsing keeps the task and progress visible');
    await page.tap('#lrn-bubble [data-lrn="collapse"]');
    await playLesson(page, 'touch');
    check(true, 'touch-only completion: tap figure, tap Back to Floor, tap Guide, tap Hire to practice');
    const mdest = await page.evaluate(() => !!document.querySelector('#projects-col .mc-chat-row[data-id="learn_practice"]'));
    check(mdest, 'the mobile practice destination is a real .mc-chat-row');
    await shoot(page, 'mobile-complete');
    await page.evaluate(() => LearnEngine.leave());
    check(serverWrites(h).length === 0 && practiceOnWire(h).length === 0, 'mobile run: zero server writes, no practice id on the wire');
    check(realErrors(h).length === 0, 'no uncaught page errors in section 7' + (realErrors(h).length ? ': ' + realErrors(h).join(' | ') : ''));
    await h.ctx.close();
  }

  // ══ 8. Reduced motion and Quiet effects ══
  console.log('8. Reduced motion + Quiet effects');
  {
    const probe = async (page) => page.evaluate(() => {
      const names = ['#lrn-bubble', '#lrn-arrow', '#lrn-arrow .lrn-arrow-in', '#lrn-outline'].map((s) => {
        const e = document.querySelector(s); return e ? [s, getComputedStyle(e).animationName, getComputedStyle(e).transitionDuration] : [s, 'absent', ''];
      });
      return names;
    });
    // 8a: OS-level reduced motion.
    const h = await newPage(browser, { reducedMotion: true });
    const { page } = h;
    await boot(h);
    await page.evaluate(() => LearnEngine.start('floor-v1', 'test'));
    await waitStep(page, 0);
    await page.waitForSelector('#lrn-arrow:not([hidden])', { timeout: 3000 });
    const rm = await probe(page);
    check(rm.every(([, a]) => a === 'none' || a === 'absent'), 'prefers-reduced-motion: no CSS animation on bubble, arrow or outline ' + JSON.stringify(rm.filter(([, a]) => a !== 'none' && a !== 'absent')));
    check((await bubbleText(page)).includes('Find Pip in Learn practice.'), 'reduced motion: the text is fully visible, no typewriter');
    await playLesson(page, 'click');
    check(await page.evaluate(() => { const d = document.getElementById('lrn-dots'); return !d || d.hidden || d.children.length === 0; }), 'reduced motion: no finish dots, static seal only');
    check(await page.$('#lrn-bubble .lrn-seal') !== null, 'reduced motion: the completion seal is shown');
    await page.evaluate(() => LearnEngine.leave());
    await h.ctx.close();

    // 8b: Quiet effects inside the lesson, on a no-preference browser.
    const h2 = await newPage(browser);
    const p2 = h2.page;
    await boot(h2);
    await p2.evaluate(() => LearnEngine.start('floor-v1', 'test'));
    await waitStep(p2, 0);
    await p2.waitForSelector('#lrn-arrow:not([hidden])', { timeout: 3000 });
    const loud = await probe(p2);
    check(loud.some(([, a]) => a !== 'none' && a !== 'absent'), 'with effects on, the arrow/bubble animate (the quiet toggle has something to turn off)');
    check((await p2.$eval('[data-lrn="quiet"]', (b) => b.getAttribute('aria-pressed'))) === 'false', 'Quiet effects starts off');
    await p2.click('[data-lrn="quiet"]');
    check((await p2.$eval('[data-lrn="quiet"]', (b) => b.getAttribute('aria-pressed'))) === 'true', 'Quiet effects toggles on');
    await p2.waitForTimeout(300);
    const quiet = await probe(p2);
    check(quiet.every(([, a]) => a === 'none' || a === 'absent'), 'Quiet effects: all lesson animation stops ' + JSON.stringify(quiet.filter(([, a]) => a !== 'none' && a !== 'absent')));
    check(await p2.evaluate(() => localStorage.getItem('learn.quiet')) === '1', 'the Quiet preference persists under learn.quiet');
    await p2.evaluate(() => LearnEngine.leave());
    check(realErrors(h2).length === 0 && realErrors(h).length === 0, 'no uncaught page errors in section 8');
    await h2.ctx.close();
  }

  // ══ 9. Offer never covers a user's editor; copy has no em-dashes ══
  console.log('9. Offer guard, copy hygiene');
  {
    const h = await newPage(browser);
    const { page } = h;
    await boot(h);
    await page.evaluate(() => {
      const i = document.createElement('input'); i.id = 'smoke-editor'; i.style.cssText = 'position:fixed;left:4px;bottom:4px;z-index:5';
      document.body.appendChild(i); i.focus();
    });
    await page.evaluate(() => window.openFloor());
    await page.waitForTimeout(600);
    check(await page.$('#lrn-offer') === null, 'no offer while an input is focused (unsaved editor)');
    check((await page.evaluate(() => (JSON.parse(localStorage.getItem('learn.progress') || '{"lessons":{}}').lessons['floor-v1'] || {}).offered)) !== true, 'a suppressed offer is not consumed (still available later)');
    await page.evaluate(() => { document.getElementById('smoke-editor').blur(); closeModalById('__floor'); window.openFloor(); });
    await page.waitForSelector('#lrn-offer', { timeout: 4000 });
    check(true, 'the offer appears once the editor is gone');

    // Em-dashes in anything a user can read from Learn.
    await page.evaluate(() => openLearn());
    const texts = [];
    texts.push(await page.evaluate(() => document.getElementById('lrn-hub-body').innerText));
    texts.push(await page.evaluate(() => document.getElementById('lrn-offer').innerText));
    await page.click('#lrn-offer [data-offer="go"]');
    await waitStep(page, 0);
    for (let i = 0; i < 3; i++) {
      texts.push(await bubbleText(page));
      if (i === 0) { await page.click('#lrn-bubble [data-lrn="hint"]'); texts.push(await bubbleText(page)); await page.click('#floor-body .fl-fig'); await waitStep(page, 0, 'acknowledged', 4000); texts.push(await bubbleText(page)); await waitStep(page, 1); }
      if (i === 1) { texts.push(await bubbleText(page)); await page.click('#lrn-bubble [data-lrn="back-floor"]'); await page.click('#floor-body .fl-bench-main'); await waitStep(page, 1, 'acknowledged', 4000); texts.push(await bubbleText(page)); await waitStep(page, 2); }
      if (i === 2) { await page.click('#lrn-bubble [data-lrn="hire"]'); await page.waitForFunction(() => window.LearnEngine.state.phase === 'completed'); texts.push(await bubbleText(page)); }
    }
    await page.click('#lrn-bubble [data-lrn="pause"]').catch(() => {});
    const dashes = texts.filter((t) => /[—–]/.test(t));
    check(dashes.length === 0, 'no em-dashes or en-dashes in the offer, hub, steps, acknowledgments or completion copy' + (dashes.length ? ': ' + JSON.stringify(dashes[0]) : ''));
    // The Claydo chip label and the lesson's metadata are checked as data too.
    const labels = await page.evaluate(() => [window.LearnEngine.lessonChip('floor-v1')]);
    check(labels.every((t) => !/[—–]/.test(t)), 'the Claydo chip label has no em-dash');
    await page.evaluate(() => LearnEngine.leave());
    check(realErrors(h).length === 0, 'no uncaught page errors in section 9' + (realErrors(h).length ? ': ' + realErrors(h).join(' | ') : ''));
    await h.ctx.close();
  }

  // ══ 10. Phone: a visible, tappable Leave in EVERY phase (MC-1031) ══
  console.log('10. Phone Leave control in every phase (390, 344)');
  for (const [w, hgt] of [[390, 844], [344, 882]]) {
    const h = await newPage(browser, { width: w, height: hgt, touch: true });
    const { page } = h;
    await boot(h);
    // The bubble's first Leave control must be reachable: laid out, fully inside the
    // viewport, >=44 px, and what a finger at its centre actually hits.
    const leaveReach = (label) => page.evaluate((l) => {
      const b = document.getElementById('lrn-bubble');
      const btn = b && Array.from(b.querySelectorAll('[data-lrn="leave"]')).find((e) => e.getBoundingClientRect().width > 0);
      if (!btn) return `${l}: no laid-out Leave control`;
      const r = btn.getBoundingClientRect(), br = b.getBoundingClientRect();
      if (r.width < 44 || r.height < 44) return `${l}: Leave is ${Math.round(r.width)}x${Math.round(r.height)}, under 44px`;
      if (r.left < 0 || r.top < 0 || r.right > innerWidth || r.bottom > innerHeight) return `${l}: Leave is outside the viewport`;
      if (br.left < 0 || br.top < 0 || br.right > innerWidth || br.bottom > innerHeight) return `${l}: the bubble is outside the viewport`;
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return hit && (hit === btn || btn.contains(hit)) ? '' : `${l}: something else covers Leave (${hit && (hit.id || hit.className)})`;
    }, label);
    const phoneLeave = async (label, setup, expectStatus) => {
      await page.evaluate(() => LearnEngine.start('floor-v1', 'test', { fresh: true }));
      await waitStep(page, 0);
      await setup();
      const why = await leaveReach(`${w}px ${label}`);
      check(why === '', `${w}px, ${label}: a visible, in-viewport, >=44px Leave that nothing covers` + (why ? ' (' + why + ')' : ''));
      await shoot(page, `phone-${w}-${label.replace(/\W+/g, '-')}`);
      const at = await page.evaluate(() => {
        const btn = Array.from(document.querySelectorAll('#lrn-bubble [data-lrn="leave"]')).find((e) => e.getBoundingClientRect().width > 0);
        const r = btn ? btn.getBoundingClientRect() : null;
        return r ? { x: r.left + r.width / 2, y: r.top + r.height / 2 } : null;
      });
      if (at) await page.touchscreen.tap(at.x, at.y);
      else await page.evaluate(() => LearnEngine.leave());   // keep going so every phase reports
      const gone = await page.evaluate(() => !document.getElementById('lrn-root') && !window.LearnEngine.state && !window.LearnPractice.active && !document.body.classList.contains('lrn-practicing'));
      check(gone, `${w}px, ${label}: tapping Leave ends the lesson and restores the real surface`);
      check(await page.waitForSelector('#projects-col .card[data-id="real_home"], #projects-col .mc-chat-row[data-id="real_home"]', { timeout: 3000 }).then(() => true, () => false), `${w}px, ${label}: the real dashboard is back after Leave`);
      if (expectStatus) check((await lessonRec(page)).status === expectStatus, `${w}px, ${label}: progress is ${expectStatus} after Leave`);
      await page.evaluate(() => { for (const id of ['__learn', '__floor']) if (typeof closeModalById === 'function' && openModals.has(id)) closeModalById(id); });
    };
    await phoneLeave('active step', async () => {}, 'paused');
    await phoneLeave('hint shown', async () => { await page.tap('#lrn-bubble [data-lrn="hint"]'); }, 'paused');
    await phoneLeave('collapsed', async () => { await page.tap('#lrn-bubble [data-lrn="collapse"]'); }, 'paused');
    await phoneLeave('paused', async () => { await page.tap('#lrn-bubble [data-lrn="pause"]'); await waitStep(page, 0, 'paused', 3000); }, 'paused');
    await phoneLeave('chat modal open', async () => {
      await page.tap('#floor-body .fl-fig[data-fl-session="learn-pip"]');
      await page.waitForSelector('#agent-output-learn-pip', { timeout: 5000 });
    }, 'paused');
    await phoneLeave('Learn hub open', async () => {
      await page.evaluate(() => openLearn());
      await page.waitForSelector('.modal-window[data-modal-id="__learn"]', { timeout: 4000 });
    }, 'paused');
    await phoneLeave('completed', async () => { await playLesson(page, 'touch'); }, 'completed');
    await phoneLeave('target unavailable', async () => {
      await page.addStyleTag({ content: '#floor-body .fl-fig { display: none !important; }' });
      await page.waitForFunction(() => window.LearnEngine.state.phase === 'unavailable', null, { timeout: 9000 });
    }, 'paused');
    check(realErrors(h).length === 0, `no uncaught page errors in section 10 at ${w}` + (realErrors(h).length ? ': ' + realErrors(h).join(' | ') : ''));
    await h.ctx.close();
  }
  {
    const h = await newPage(browser, { width: 1400, height: 900 });
    const { page } = h;
    await boot(h);
    await page.evaluate(() => LearnEngine.start('floor-v1', 'test'));
    await waitStep(page, 0);
    check(await page.evaluate(() => { const e = document.querySelector('#lrn-bubble .lrn-leave'); return !!e && getComputedStyle(e).display === 'none'; }), 'desktop 1400: the phone header Leave stays hidden');
    check(await page.evaluate(() => { const e = document.querySelector('#lrn-bubble .lrn-foot-leave'); return !!e && getComputedStyle(e).display !== 'none'; }), 'desktop 1400: the footer "Leave practice" is still shown');
    await page.evaluate(() => LearnEngine.leave());
    await h.ctx.close();
  }

  exitCode = bad === 0 ? 0 : 1;
  console.log(exitCode === 0 ? '\n✅ PASS: learn-floor, every case behaved.' : `\n❌ FAIL: ${bad} problem(s).`);
} catch (err) {
  console.error('❌ FAIL: smoke harness error:', err && err.stack ? err.stack : err);
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}

#!/usr/bin/env node
/**
 * MC-957 "Brainstorm this" — both entry points, desktop and mobile.
 *
 * Entry A (composer): the +New empty-state gets a ".ces-chip-brainstorm"
 * action (conversation.js startBrainstormThis) that switches the composer's
 * pending persona to 'global:brainstorm' via the SAME setComposerCharacter()
 * every other picker uses — it does not dispatch by itself. Section 1/2 prove
 * the click sets that state and that it survives to the real POST body.
 *
 * Entry B (message-level): a small "\u{1F4A1}" button rendered on the user's
 * own prompt bubble only (never on a slash-command bubble) opens a FRESH
 * composer (newAgentTab — a new tab, not a resume) seeded with that one
 * message quoted, persona pre-set to Brainstorm. Section 3/4 prove it does
 * not touch the live conversation and does not copy the whole transcript.
 *
 * Hermetic: real index.html + static/js/*.js + static/css/*.css served
 * verbatim, no server, no network — same shape as composer-persona-cards.mjs
 * and chat-date-dividers.mjs.
 *
 * RUN   node tools/smoke/brainstorm.mjs
 * Exit  0 = all checks pass; 1 = a regression / harness error.
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
const SHOT_DIR = resolve(__dirname, '_shots');
mkdirSync(SHOT_DIR, { recursive: true });

const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_brainstorm';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

function fixtureProject(id, name) {
  return {
    id, name, status: 'active', domain: 'general', emoji: '\u{1F4A1}',
    description: '', summary: '', current_task: 'Idle', next_action: '',
    blocked: false, blocked_reason: null, activity_log: [], backlog: [],
    project_path: '/smoke/' + id, last_updated: '2026-09-27T00:00:00Z',
    last_updated_relative: 'today', last_completed: null, live_agent: null,
    display_order: 0, provider: 'claude', use_streaming_agent: true,
    distiller_mode: 'proposed', distiller_min_recurrence: 3,
    distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
    distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
    distiller_skip_errors: true,
  };
}
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Brainstorm Smoke')]);
// Mirrors the real data/agents/builtin/brainstorm.md frontmatter — the
// picker/dispatch code never re-derives this, it just reads what /api/
// characters hands back, so a fixture with the real shape is enough here.
const CHARACTERS_JSON = JSON.stringify([
  { name: 'brainstorm', scope: 'global', agent_name: 'Brainstorm', display_name: 'brainstorm',
    description: 'x', file: 'brainstorm.md', size: 10, avatar: 'fig:alchemist',
    engine: { model: 'tier:best', effort: 'high' } },
]);
const SOURCE_SESSION_ID = 'sess-brainstorm-source';
const CONVERSATIONS_JSON = JSON.stringify([]);
const SEED_IDEA = "A subscription box for people who can't decide what to cook";

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

async function installRoutes(page) {
  const dispatchBodies = [];
  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (/\/conversations$/.test(path)) return route.fulfill({ status: 200, contentType: 'application/json', body: CONVERSATIONS_JSON });
    if (/\/agent-log$/.test(path)) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (/\/agent\/dispatch$/.test(path)) {
      dispatchBodies.push(JSON.parse(req.postData() || '{}'));
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, session_id: 'sess-brainstorm-new' }) });
    }
    return route.abort();
  });
  return dispatchBodies;
}

async function openFreshComposer(page, pid) {
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(({ pid }) => {
    window.agentConvNew = window.agentConvNew || {};
    openProjectModal(pid);
    agentConvNew[pid] = true;
    if (typeof refreshModal === 'function') refreshModal();
  }, { pid });
  await page.waitForSelector('.ces-chip-brainstorm', { timeout: 5000 });
}

// Seeds one existing, already-completed conversation with a single user
// prompt line (the shape agentLineCls renders as .agent-line-prompt) and
// opens it as the active tab — the precondition for Entry B.
async function openSourceConversation(page, pid) {
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  const sid = await page.evaluate(({ pid, sid, idea }) => {
    agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Brainstorm Smoke', task: idea, status: 'completed', startedAt: new Date().toISOString(), character: { name: 'fenn', scope: 'global', agent_name: 'Fenn', source: 'picked' } });
    agentStatusCache[sid] = { status: 'completed', task: idea, projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: 'csid-brainstorm-source', character: { name: 'fenn', scope: 'global', agent_name: 'Fenn', source: 'picked' } };
    agentOutputBuffers[sid] = [`> ${idea}`, 'A reasonable first pass, but have you checked who already does this?'];
    openProjectModal(pid);
    window.agentConvNew = window.agentConvNew || {};
    agentConvNew[pid] = false;
    activeAgentTab[pid] = sid;
    if (typeof refreshModal === 'function') refreshModal();
    return sid;
  }, { pid, sid: SOURCE_SESSION_ID, idea: SEED_IDEA });
  await page.waitForSelector(`.modal-window[data-modal-id="${pid}"] #agent-output-${sid}`, { timeout: 5000 });
  return sid;
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();

  // ── 1. Entry A, desktop: chip sets the persona, survives to the dispatch POST ──
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
    const dispatchBodies = await installRoutes(page);
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await openFreshComposer(page, PID);

    const before = await page.evaluate(({ pid }) => getPendingCharacter(pid), { pid: PID });
    (before === '') ? ok('desktop Entry A: no persona pending before the chip is clicked')
      : fail(`desktop Entry A: expected no pending persona, got "${before}"`);

    await page.click('.ces-chip-brainstorm');
    await page.waitForTimeout(50);
    const afterClick = await page.evaluate(({ pid }) => ({
      pending: getPendingCharacter(pid),
      heading: document.querySelector('.ces-heading')?.textContent || '',
      placeholder: document.getElementById('agent-task-' + pid)?.placeholder || '',
    }), { pid: PID });
    (afterClick.pending === 'global:brainstorm') ? ok('desktop Entry A: chip sets getPendingCharacter() to "global:brainstorm"')
      : fail(`desktop Entry A: pending character is "${afterClick.pending}", want "global:brainstorm"`);
    (afterClick.heading === 'What should Brainstorm work on?') ? ok(`desktop Entry A: headline follows the selection ("${afterClick.heading}")`)
      : fail(`desktop Entry A: headline is "${afterClick.heading}"`);
    (afterClick.placeholder.includes('Brainstorm')) ? ok('desktop Entry A: composer placeholder names Brainstorm')
      : fail(`desktop Entry A: placeholder "${afterClick.placeholder}" does not mention Brainstorm`);

    // The idea field is still editable and empty — dispatch happens only on submit.
    const ideaText = 'A meal-kit box that only ships what you already like';
    await page.fill('#agent-task-' + PID, ideaText);
    await page.evaluate(({ pid }) => dispatchAgent(pid), { pid: PID });
    await page.waitForTimeout(300);
    const uncaught1 = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    if (uncaught1.length) uncaught1.forEach((e) => fail('desktop Entry A: uncaught exception — ' + e));
    (dispatchBodies.length === 1 && dispatchBodies[0].character === 'global:brainstorm' && dispatchBodies[0].task === ideaText)
      ? ok('desktop Entry A: POST /agent/dispatch carries character="global:brainstorm" and the user-edited idea text')
      : fail(`desktop Entry A: dispatch body ${JSON.stringify(dispatchBodies[0])}`);

    await page.screenshot({ path: resolve(SHOT_DIR, 'brainstorm-entryA-desktop.png') });
    ok('desktop Entry A screenshot saved');
    await ctx.close();
  }

  // ── 2. Entry A, mobile (<=960px): same chip, same state, in the mobile compose view ──
  {
    const ctx = await browser.newContext({ viewport: { width: 412, height: 883 }, hasTouch: true });
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
    const dispatchBodies = await installRoutes(page);
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await openFreshComposer(page, PID);

    const visible = await page.evaluate(() => {
      const el = document.querySelector('.ces-chip-brainstorm');
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { top: r.top, bottom: r.bottom, vh: window.innerHeight };
    });
    visible ? ok(`mobile Entry A: ".ces-chip-brainstorm" renders in the mobile compose view (top=${visible.top.toFixed(0)})`)
      : fail('mobile Entry A: ".ces-chip-brainstorm" not found in the mobile compose view');

    await page.tap('.ces-chip-brainstorm');
    await page.waitForTimeout(50);
    const pending = await page.evaluate(({ pid }) => getPendingCharacter(pid), { pid: PID });
    (pending === 'global:brainstorm') ? ok('mobile Entry A: tap sets getPendingCharacter() to "global:brainstorm"')
      : fail(`mobile Entry A: pending character is "${pending}"`);

    await page.fill('#agent-task-' + PID, 'A meal-kit box, mobile edit');
    await page.evaluate(({ pid }) => dispatchAgent(pid), { pid: PID });
    await page.waitForTimeout(300);
    const uncaught2 = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    if (uncaught2.length) uncaught2.forEach((e) => fail('mobile Entry A: uncaught exception — ' + e));
    (dispatchBodies.length === 1 && dispatchBodies[0].character === 'global:brainstorm')
      ? ok('mobile Entry A: POST /agent/dispatch carries character="global:brainstorm"')
      : fail(`mobile Entry A: dispatch body ${JSON.stringify(dispatchBodies[0])}`);

    await page.screenshot({ path: resolve(SHOT_DIR, 'brainstorm-entryA-mobile.png') });
    ok('mobile Entry A screenshot saved');
    await ctx.close();
  }

  // ── 3. Entry B, desktop: message action opens a NEW seeded composer, source untouched ──
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
    const dispatchBodies = await installRoutes(page);
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    const sid = await openSourceConversation(page, PID);

    const btnCount = await page.evaluate(`document.querySelectorAll('.modal-window[data-modal-id="${PID}"] .msg-brainstorm-btn').length`);
    (btnCount === 1) ? ok(`desktop Entry B: exactly 1 ".msg-brainstorm-btn" rendered (only the real user bubble, not the agent's reply)`)
      : fail(`desktop Entry B: expected 1 message action button, got ${btnCount}`);

    await page.click(`.modal-window[data-modal-id="${PID}"] .msg-brainstorm-btn`);
    await page.waitForTimeout(50);
    const state = await page.evaluate(({ pid }) => ({
      isNewComposer: agentConvNew[pid] === true,
      activeTabCleared: !activeAgentTab[pid],
      pending: getPendingCharacter(pid),
      taskValue: document.getElementById('agent-task-' + pid)?.value || '',
    }), { pid: PID });
    state.isNewComposer ? ok('desktop Entry B: a NEW composer tab opened (agentConvNew === true), not a resume')
      : fail('desktop Entry B: did not land on the +New composer');
    state.activeTabCleared ? ok('desktop Entry B: the source conversation is no longer the active tab')
      : fail('desktop Entry B: source tab still active — looks like a live-persona swap, not a new thread');
    (state.pending === 'global:brainstorm') ? ok('desktop Entry B: new composer is pre-set to the Brainstorm persona')
      : fail(`desktop Entry B: pending character "${state.pending}"`);
    state.taskValue.includes(SEED_IDEA) ? ok('desktop Entry B: seeded textarea visibly quotes the source message')
      : fail(`desktop Entry B: textarea "${state.taskValue}" does not contain the quoted idea`);
    state.taskValue.includes('Fenn') ? ok('desktop Entry B: seeded text names the source conversation (linked back), not just the idea')
      : fail(`desktop Entry B: seeded text does not reference the source conversation: "${state.taskValue}"`);
    (!state.taskValue.includes('already does this')) ? ok("desktop Entry B: only the user's message was copied, not the agent's reply / whole transcript")
      : fail('desktop Entry B: the agent reply leaked into the seed — should only quote the one user message');

    // Still editable before dispatch, and the source conversation's own log is untouched.
    const editedText = state.taskValue + '\n\nAlso: subscription fatigue is real, need a differentiator.';
    await page.fill('#agent-task-' + PID, editedText);
    await page.evaluate(({ pid }) => dispatchAgent(pid), { pid: PID });
    await page.waitForTimeout(300);
    const uncaught3 = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    if (uncaught3.length) uncaught3.forEach((e) => fail('desktop Entry B: uncaught exception — ' + e));
    (dispatchBodies.length === 1 && dispatchBodies[0].character === 'global:brainstorm' && !dispatchBodies[0].resume_id)
      ? ok('desktop Entry B: dispatch is a FRESH session (no resume_id) carrying character="global:brainstorm"')
      : fail(`desktop Entry B: dispatch body ${JSON.stringify(dispatchBodies[0])}`);

    const sourceIntact = await page.evaluate(({ sid }) => agentOutputBuffers[sid].join('\n'), { sid });
    sourceIntact.includes(SEED_IDEA) && sourceIntact.includes('already does this')
      ? ok("desktop Entry B: the source conversation's own transcript is untouched")
      : fail(`desktop Entry B: source transcript changed unexpectedly: ${sourceIntact}`);

    await page.screenshot({ path: resolve(SHOT_DIR, 'brainstorm-entryB-desktop.png') });
    ok('desktop Entry B screenshot saved');
    await ctx.close();
  }

  // ── 4. Entry B, mobile (<=960px): message action reachable and works the same ──
  {
    const ctx = await browser.newContext({ viewport: { width: 412, height: 883 }, hasTouch: true });
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
    const dispatchBodies = await installRoutes(page);
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await openSourceConversation(page, PID);

    const btnCount = await page.evaluate(`document.querySelectorAll('.modal-window[data-modal-id="${PID}"] .msg-brainstorm-btn').length`);
    (btnCount === 1) ? ok('mobile Entry B: message action button renders in the mobile thread view')
      : fail(`mobile Entry B: expected 1 message action button, got ${btnCount}`);

    await page.tap(`.modal-window[data-modal-id="${PID}"] .msg-brainstorm-btn`);
    await page.waitForTimeout(50);
    const state = await page.evaluate(({ pid }) => ({
      isNewComposer: agentConvNew[pid] === true,
      pending: getPendingCharacter(pid),
      taskValue: document.getElementById('agent-task-' + pid)?.value || '',
    }), { pid: PID });
    state.isNewComposer ? ok('mobile Entry B: tap opens a new composer tab')
      : fail('mobile Entry B: did not open a new composer');
    (state.pending === 'global:brainstorm') ? ok('mobile Entry B: persona pre-set to Brainstorm')
      : fail(`mobile Entry B: pending character "${state.pending}"`);
    state.taskValue.includes(SEED_IDEA) ? ok('mobile Entry B: seeded textarea quotes the source message')
      : fail(`mobile Entry B: textarea does not contain the quoted idea: "${state.taskValue}"`);

    await page.evaluate(({ pid }) => dispatchAgent(pid), { pid: PID });
    await page.waitForTimeout(300);
    const uncaught4 = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    if (uncaught4.length) uncaught4.forEach((e) => fail('mobile Entry B: uncaught exception — ' + e));
    (dispatchBodies.length === 1 && dispatchBodies[0].character === 'global:brainstorm')
      ? ok('mobile Entry B: POST /agent/dispatch carries character="global:brainstorm"')
      : fail(`mobile Entry B: dispatch body ${JSON.stringify(dispatchBodies[0])}`);

    await page.screenshot({ path: resolve(SHOT_DIR, 'brainstorm-entryB-mobile.png') });
    ok('mobile Entry B screenshot saved');
    await ctx.close();
  }

  exitCode = bad === 0 ? 0 : 1;
} catch (e) {
  console.error('❌ harness error: ' + (e.stack || e));
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}

console.log('');
console.log(exitCode === 0
  ? '✅ PASS — Entry A (composer chip) and Entry B (message action) both set the Brainstorm persona, leave the idea editable, and dispatch a fresh session on submit; desktop and mobile.'
  : `❌ FAIL — ${bad} check(s) failed.`);
process.exit(exitCode);

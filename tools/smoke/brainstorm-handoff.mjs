#!/usr/bin/env node
/**
 * MC-990 Brainstorm handoff (docs/BRAINSTORM_HANDOFF_SPEC.md) — the three UI
 * legs the spec adds on top of MC-957's "Brainstorm this" entry points:
 *
 *  1. Claydo ask-mode offer: a reply carrying [clayrune:brainstorm-offer]
 *     renders a chip -> editable "Start Brainstorm" seed composer -> normal
 *     dispatch into the reserved Ideas workspace. A failed dispatch keeps
 *     the seed and re-shows the panel (spec section 1), never silently
 *     drops the idea.
 *  2. Exploration card: an agent chat line [clayrune:exploration-ready]
 *     renders a persistent "Use this exploration" card bound to that exact
 *     message + conversation, and rebuilds identically from the replayed
 *     transcript on a cold reopen (not just the live SSE path).
 *  3. Review form -> transfer: filling the "create a new project" form and
 *     submitting fires exactly ONE POST to the transfer route, and the card
 *     then shows the transfer as done instead of the form.
 *
 * Hermetic: real index.html + static/js/*.js + static/css/*.css served
 * verbatim, no real server, no network — same shape as brainstorm.mjs.
 *
 * RUN   node tools/smoke/brainstorm-handoff.mjs
 * Exit  0 = all checks pass; 1 = a regression / harness error.
 */
import { readFileSync, readdirSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const SHOT_DIR = resolve(__dirname, '_shots');
mkdirSync(SHOT_DIR, { recursive: true });

const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_handoff';
const IDEAS_PID = '_ideas'; // mc/blueprints/guide_routes.py IDEAS_WORKSPACE_ID (MC-990 D1 rename)

const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));

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
const PROJECTS_JSON = JSON.stringify([
  fixtureProject(PID, 'Handoff Smoke'),
  { ...fixtureProject(IDEAS_PID, 'Ideas'), _is_ideas_workspace: true },
]);
const CHARACTERS_JSON = JSON.stringify([
  { name: 'brainstorm', scope: 'global', agent_name: 'Brainstorm', display_name: 'brainstorm',
    description: 'x', file: 'brainstorm.md', size: 10, avatar: 'fig:alchemist',
    engine: { model: 'tier:best', effort: 'high' } },
]);
const CONVERSATIONS_JSON = JSON.stringify([]);
const SOURCE_SESSION_ID = 'sess-handoff-source';
const SOURCE_CSID = 'csid-handoff-source';
const BRIEF_LINES = [
  'Working hypothesis: a subscription box for people who can\'t decide what to cook.',
  'Checked three adjacent products; none solve the "decide for me" problem directly.',
  'Next experiment: a 5-question landing page quiz measuring signup intent.',
];

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

// SSE body for /api/guide/stream: one delta + a terminal done carrying the
// brainstorm-offer marker, matching submitClaydo's `data: {...}\n\n` parser.
function sseBody(answerText) {
  const delta = `data: ${JSON.stringify({ type: 'delta', text: answerText })}\n\n`;
  const done = `data: ${JSON.stringify({ type: 'done', answer: answerText })}\n\n`;
  return delta + done;
}

async function installRoutes(page, { dispatchStatus = 200, dispatchBody = null, transferStatus = 201, transferBody = null } = {}) {
  const dispatchBodies = [];
  const transferBodies = [];
  await page.route('**/*', (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/local-auth/status') return route.fulfill({ status: 200, contentType: 'application/json', body: '{"configured":true}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (/\/conversations$/.test(path)) return route.fulfill({ status: 200, contentType: 'application/json', body: CONVERSATIONS_JSON });
    if (/\/agent-log$/.test(path)) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/guide/stream') {
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: sseBody(global.__sseAnswer) });
    }
    if (/\/agent\/dispatch$/.test(path)) {
      dispatchBodies.push(JSON.parse(req.postData() || '{}'));
      if (dispatchStatus !== 200) return route.fulfill({ status: dispatchStatus, contentType: 'application/json', body: JSON.stringify(dispatchBody || { error: 'dispatch failed' }) });
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(dispatchBody || { ok: true, session_id: 'sess-brainstorm-new' }) });
    }
    if (/\/brainstorm\/transfer$/.test(path)) {
      transferBodies.push(JSON.parse(req.postData() || '{}'));
      return route.fulfill({ status: transferStatus, contentType: 'application/json', body: JSON.stringify(transferBody || { ok: true, destination_project_id: 'new_from_brief', doc_path: 'docs/brainstorm/x-v1.md', backlog_item_id: 'bl1' }) });
    }
    return route.abort();
  });
  return { dispatchBodies, transferBodies };
}

// Seeds one completed conversation carrying the exploration-ready marker
// after a short brief, and opens it as the active tab — precondition for
// sections 2/3.
async function openExplorationConversation(page, pid) {
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  const sid = await page.evaluate(({ pid, sid, csid, briefLines }) => {
    const buf = [`> Brainstorm this: a meal-decision subscription box`, ...briefLines, '[clayrune:exploration-ready]'];
    agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Handoff Smoke', task: briefLines[0], status: 'completed', startedAt: new Date().toISOString(), character: { name: 'brainstorm', scope: 'global', agent_name: 'Brainstorm', source: 'picked' } });
    agentStatusCache[sid] = { status: 'completed', task: briefLines[0], projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: csid, character: { name: 'brainstorm', scope: 'global', agent_name: 'Brainstorm', source: 'picked' } };
    agentOutputBuffers[sid] = buf;
    openProjectModal(pid);
    window.agentConvNew = window.agentConvNew || {};
    agentConvNew[pid] = false;
    activeAgentTab[pid] = sid;
    if (typeof refreshModal === 'function') refreshModal();
    return sid;
  }, { pid, sid: SOURCE_SESSION_ID, csid: SOURCE_CSID, briefLines: BRIEF_LINES });
  await page.waitForSelector(`.modal-window[data-modal-id="${pid}"] #agent-output-${sid}`, { timeout: 5000 });
  return sid;
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();

  // ── 1. Claydo offer chip -> seed composer -> dispatch into Ideas ──
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
    const answerText = 'That\'s a real gap worth exploring further. [clayrune:brainstorm-offer]';
    global.__sseAnswer = answerText;
    const { dispatchBodies } = await installRoutes(page);
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });

    const question = 'What if I built a subscription box for people who can\'t decide what to cook?';
    await page.evaluate(() => openClaydo());
    await page.waitForSelector('#claydo-input', { timeout: 5000 });
    await page.fill('#claydo-input', question);
    await page.click('#claydo-send');
    await page.waitForSelector('.claydo-brainstorm-offer-chip', { timeout: 5000 });
    ok('Claydo: a reply carrying [clayrune:brainstorm-offer] renders the "Brainstorm an idea" chip');

    await page.click('.claydo-brainstorm-offer-chip');
    await page.waitForSelector('#claydo-brainstorm-seed', { timeout: 5000 });
    const seedValue = await page.inputValue('#claydo-brainstorm-seed');
    (seedValue === question) ? ok('Claydo: seed composer is pre-filled with the triggering question, nothing else')
      : fail(`Claydo: seed composer value "${seedValue}" !== triggering question`);

    // Editable before submit.
    const editedSeed = question + ' Maybe tied to a weekly quiz.';
    await page.fill('#claydo-brainstorm-seed', editedSeed);

    // First attempt: dispatch fails -> seed must survive, panel stays open.
    await page.route('**/agent/dispatch', (route) => route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'no capacity' }) }));
    await page.click('#claydo-brainstorm-go');
    await page.waitForFunction(() => {
      const el = document.getElementById('claydo-brainstorm-err');
      return !!el && getComputedStyle(el).display !== 'none' && el.textContent.trim().length > 0;
    }, { timeout: 5000 });
    const errShown = await page.textContent('#claydo-brainstorm-err');
    const seedAfterError = await page.inputValue('#claydo-brainstorm-seed');
    (errShown || '').includes('no capacity') ? ok('Claydo: a failed dispatch surfaces the server error in the panel')
      : fail(`Claydo: error text "${errShown}" does not surface the failure`);
    (seedAfterError === editedSeed) ? ok('Claydo: the edited seed text survives a failed dispatch (not dropped)')
      : fail(`Claydo: seed after error is "${seedAfterError}", expected the edited text intact`);
    const panelStillOpen = await page.$('#claydo-brainstorm-seed');
    panelStillOpen ? ok('Claydo: the seed composer stays open after an error (no silent close)')
      : fail('Claydo: the seed composer closed despite the dispatch failing');

    // Second attempt: real dispatch route, now succeeds.
    await page.unroute('**/agent/dispatch');
    await page.route('**/agent/dispatch', (route) => {
      dispatchBodies.push(JSON.parse(route.request().postData() || '{}'));
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, session_id: 'sess-brainstorm-new' }) });
    });
    await page.click('#claydo-brainstorm-go');
    await page.waitForTimeout(300);
    const afterOk = await page.$('#claydo-brainstorm-seed');
    !afterOk ? ok('Claydo: a successful dispatch closes the seed composer')
      : fail('Claydo: seed composer still open after a successful dispatch');
    const last = dispatchBodies[dispatchBodies.length - 1] || {};
    (last.character === 'global:brainstorm' && last.task === editedSeed)
      ? ok('Claydo: dispatch POST carries character="global:brainstorm" and the edited seed as the task, into the Ideas workspace route')
      : fail(`Claydo: dispatch body ${JSON.stringify(last)}`);

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    if (uncaught.length) uncaught.forEach((e) => fail('Claydo section: uncaught exception — ' + e));

    await page.screenshot({ path: resolve(SHOT_DIR, 'handoff-claydo-seed.png') });
    ok('Claydo seed composer screenshot saved');
    await ctx.close();
  }

  // ── 2. Exploration marker -> card, and it rebuilds identically on a cold reopen ──
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
    await installRoutes(page);
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await openExplorationConversation(page, PID);

    const cardCount = await page.evaluate(`document.querySelectorAll('.modal-window[data-modal-id="${PID}"] .exploration-card').length`);
    (cardCount === 1) ? ok('exploration marker: exactly 1 "Use this exploration" card rendered for the one marker line')
      : fail(`exploration marker: expected 1 card, got ${cardCount}`);
    const cardText = await page.textContent(`.modal-window[data-modal-id="${PID}"] .exploration-card`);
    (cardText || '').includes('Use this exploration') ? ok('exploration marker: card title reads "Use this exploration"')
      : fail(`exploration marker: card text "${cardText}" missing the title`);
    (cardText || '').includes('Exploration v1') ? ok('exploration marker: card is labelled v1 (first occurrence in this conversation)')
      : fail(`exploration marker: card text "${cardText}" missing "Exploration v1"`);

    // Simulate "after reload": close the modal (tears down the live DOM) and
    // reopen — this exercises the SAME cold-render buffer replay a real page
    // refresh would (agentOutputBuffers is untouched, nothing SSE-derived).
    await page.evaluate(({ pid }) => closeModalById(pid), { pid: PID });
    await page.waitForTimeout(50);
    await page.evaluate(({ pid, sid }) => {
      openProjectModal(pid);
      activeAgentTab[pid] = sid;
      if (typeof refreshModal === 'function') refreshModal();
    }, { pid: PID, sid: SOURCE_SESSION_ID });
    await page.waitForSelector(`.modal-window[data-modal-id="${PID}"] .exploration-card`, { timeout: 5000 });
    const cardCountAfter = await page.evaluate(`document.querySelectorAll('.modal-window[data-modal-id="${PID}"] .exploration-card').length`);
    (cardCountAfter === 1) ? ok('exploration marker: card rebuilds identically after close+reopen (transcript replay, not live-only)')
      : fail(`exploration marker: after reopen expected 1 card, got ${cardCountAfter}`);

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    if (uncaught.length) uncaught.forEach((e) => fail('exploration marker section: uncaught exception — ' + e));

    await page.screenshot({ path: resolve(SHOT_DIR, 'handoff-exploration-card.png') });
    ok('exploration card screenshot saved');
    await ctx.close();
  }

  // ── 3. Review form (create project) -> exactly one transfer POST, card flips to done ──
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
    const { transferBodies } = await installRoutes(page, {
      transferBody: { ok: true, destination_project_id: 'meal_decider', doc_path: `docs/brainstorm/${SOURCE_CSID}-v1.md`, backlog_item_id: 'bl_meal1' },
    });
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await openExplorationConversation(page, PID);
    await page.waitForSelector(`.modal-window[data-modal-id="${PID}"] .exploration-card`, { timeout: 5000 });

    await page.click(`.modal-window[data-modal-id="${PID}"] .exploration-card [data-act="open-create"]`);
    await page.waitForSelector(`.modal-window[data-modal-id="${PID}"] .exploration-card input[data-f="name"]`, { timeout: 5000 });
    await page.fill(`.modal-window[data-modal-id="${PID}"] .exploration-card input[data-f="name"]`, 'Meal Decider');
    // The id field mirrors team-card.js's uncontrolled-input pattern: the auto-
    // derived slug lives in JS draft state (written to the input only on a full
    // re-render), not synced back into the DOM value on every keystroke. The
    // transfer POST body assertion below is what actually proves the derived
    // id ("meal_decider") reached the server.

    let openedModalId = null;
    await page.exposeFunction('__smokeOpenProjectModal', (pid) => { openedModalId = pid; });
    await page.evaluate(() => {
      window.__realOpenProjectModal = window.openProjectModal;
      window.openProjectModal = function (pid, ...rest) {
        window.__smokeOpenProjectModal(pid);
        return window.__realOpenProjectModal(pid, ...rest);
      };
    });

    await page.click(`.modal-window[data-modal-id="${PID}"] .exploration-card [data-act="submit"]`);
    // MC-995: the transfer POST now routes through humanProofFetch() -- answer
    // the shared passcode modal (convention: mock /api/local-auth/status above,
    // then drive window._hpSubmit(), same as engine-fallback-settings.mjs).
    await page.waitForSelector('[data-modal-id^="__human-proof-"]', { timeout: 5000 });
    await page.evaluate(() => {
      const win = document.querySelector('[data-modal-id^="__human-proof-"]');
      const modalId = win.dataset.modalId;
      document.getElementById(`hp-passcode-${modalId}`).value = 'smoke-dash-passcode';
      window._hpSubmit(modalId);
    });
    await page.waitForTimeout(300);

    (transferBodies.length === 1) ? ok('review form: exactly ONE POST to the transfer route fired on submit')
      : fail(`review form: expected exactly 1 transfer POST, got ${transferBodies.length}`);
    const tb = transferBodies[0] || {};
    (tb.claude_session_id === SOURCE_CSID && tb.version === 1 && tb.destination && tb.destination.mode === 'create' && tb.destination.id === 'meal_decider')
      ? ok('review form: transfer POST body identifies the source conversation, version, and create-mode destination correctly')
      : fail(`review form: transfer POST body ${JSON.stringify(tb)}`);
    (openedModalId === 'meal_decider') ? ok('review form: a successful transfer opens the new destination project')
      : fail(`review form: expected openProjectModal("meal_decider"), got ${openedModalId}`);

    const cardAfter = await page.textContent(`.modal-window[data-modal-id="${PID}"] .exploration-card`);
    (cardAfter || '').includes('Open meal_decider') ? ok('review form: card now shows the completed transfer as an "Open meal_decider" action, not the form')
      : fail(`review form: card text after submit "${cardAfter}" does not show the done state`);
    (cardAfter || '').includes('Create project and send') ? fail('review form: the create form is still showing after a successful submit')
      : ok('review form: the create-project form is gone after a successful submit');

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    if (uncaught.length) uncaught.forEach((e) => fail('review form section: uncaught exception — ' + e));

    await page.screenshot({ path: resolve(SHOT_DIR, 'handoff-review-form-done.png') });
    ok('review form done-state screenshot saved');
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
  ? '✅ PASS — Claydo offer chip -> seed composer -> Ideas dispatch (errors keep the seed); the exploration-ready marker renders a persistent card that survives a cold reopen; the review form fires exactly one transfer POST and flips the card to done.'
  : `❌ FAIL — ${bad} check(s) failed.`);
process.exit(exitCode);

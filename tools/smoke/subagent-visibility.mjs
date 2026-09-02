#!/usr/bin/env node
/**
 * MC-937 Phase 4 (frontend) regression: live subagent visibility.
 *
 * WHY THIS EXISTS
 * ----------------
 * The backend (agent_routes.py's _active_subagents_for_session, shipped and
 * LIVE) puts a per-session `active_subagents` array on GET
 * /api/project/<id>/agent/status — server-authoritative liveness, nothing
 * client-side re-derives it. Before this change nothing consumed the field:
 * Ron asked "is anyone working right now?" five times on a day when agents
 * WERE running and the app showed nothing, not on the Floor, not in the rail,
 * nowhere. This slice adds the two consuming surfaces:
 *   1. a nested card inside the spawning agent's message thread
 *      (conversation.js: _subagentCardsHTML / _renderSubagentCards, cold +
 *      warm render paths sharing _subagentCardRowHTML / _subagentCardMeta so
 *      they can't drift)
 *   2. a "+N helpers" badge on that agent's Channel-rail roster row
 *      (_channelRoster's helperCount, updateRailRowStatus's live patch)
 *
 * A regression here is silent in the exact way the bug report was silent: no
 * server round-trip breaks, a running subagent just renders as nothing.
 *
 * This is a real headless boot (real index.html + real static/js/*.js served
 * verbatim, no server, no network) — same hermetic shape as
 * channel-message-attribution.mjs and channel-mode-roster.mjs.
 * agentStatusCache / conversationsCache are seeded directly in-page (same
 * shortcut those two tests use), so no /agent/status response shape needs
 * mocking — the exact server field (`active_subagents`) is mirrored onto the
 * client-side cache key (`activeSubagents`) fetchAgentStatus itself writes.
 *
 * RUN
 *   cd tools/smoke && node subagent-visibility.mjs
 * Exit 0 = all cases behave; 1 = a case regressed / harness error.
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
const PID = 'smoke_subagent';        // running + finished + attribution + highlight cases
const PID_EMPTY = 'smoke_sub_empty'; // the empty-list "no chrome" case

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

function fixtureProject(id, name) {
  return {
    id, name, status: 'active', domain: 'general', emoji: '🧪',
    description: '', summary: '', current_task: 'Idle', next_action: '',
    blocked: false, blocked_reason: null, activity_log: [], backlog: [],
    project_path: '/smoke/' + id, last_updated: '2026-09-02T00:00:00Z',
    last_updated_relative: 'today', last_completed: null, live_agent: null,
    display_order: 0, provider: 'claude', use_streaming_agent: true,
    distiller_mode: 'proposed', distiller_min_recurrence: 3,
    distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
    distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
    distiller_skip_errors: true,
  };
}
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Subagent Smoke'), fixtureProject(PID_EMPTY, 'Empty Smoke')]);

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    return route.abort();  // includes the search "full-buffer" fetch — _csEnsureComplete tolerates the failure
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });

  if (pageErrors.length) {
    pageErrors.forEach((e) => fail('uncaught page error during boot: ' + e));
  } else {
    ok('app booted clean, no uncaught exceptions');
  }

  const FENN = { name: 'code-reviewer', scope: 'project', display_name: 'code-reviewer', agent_name: 'Fenn', avatar: 'fig:scholar' };

  // ── Seed a RUNNING session with a character (attribution) + a running
  // nested subagent, so the cold render exercises cards + headers together ──
  const seeded = await page.evaluate(({ pid, fenn }) => {
    const sid = 'sess-sub';
    agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Subagent Smoke', task: 'root-cause the flaky test', status: 'running', startedAt: new Date().toISOString() });
    agentStatusCache[sid] = {
      status: 'running', task: 'root-cause the flaky test', projectId: pid,
      startedAt: new Date().toISOString(), claudeSessionId: 'csid-sub', character: fenn,
      activeSubagents: [
        { agent_id: 'abc123', parent_claude_session_id: 'csid-sub', label: 'root-cause the flaky test', started_at: new Date().toISOString(), elapsed_seconds: 12.3, tool_calls: 4, running: true },
      ],
    };
    agentOutputBuffers[sid] = [
      '> Can you dig into the flaky test?',
      'On it — kicking off a subagent to isolate it.\nWill report back.',
    ];
    conversationsCache[pid] = [
      { claude_session_id: 'csid-sub', mc_session_id: sid, character: fenn, mtime: 1000, ts_relative: 'just now', status: 'running', turns: 1, label: 'root-cause the flaky test', first_user: 'root-cause the flaky test', last_user: 'root-cause the flaky test' },
    ];
    openProjectModal(pid);
    return sid;
  }, { pid: PID, fenn: FENN });

  await page.waitForSelector(`.modal-window[data-modal-id="${PID}"] #agent-output-${seeded}`, { timeout: 5000 });
  const scope = `.modal-window[data-modal-id="${PID}"] `;

  // ── 1. Cold render: a RUNNING subagent renders a card with label + elapsed ─
  const card1 = await page.$eval(`${scope}#subagent-card-${seeded}-abc123`, (el) => ({
    running: el.dataset.running,
    hasDoneClass: el.classList.contains('subagent-card-done'),
    label: el.querySelector('.subagent-card-label')?.textContent.trim(),
    meta: el.querySelector('.subagent-card-meta')?.textContent.trim(),
    hasDot: !!el.querySelector('.subagent-card-dot'),
    hasCheck: !!el.querySelector('.subagent-card-check'),
  })).catch((e) => null);
  card1
    ? ok(`cold render: running subagent card present (label="${card1.label}", meta="${card1.meta}")`)
    : fail('cold render: no #subagent-card-sess-sub-abc123 node found');
  if (card1) {
    (card1.running === 'true' && !card1.hasDoneClass && card1.hasDot && !card1.hasCheck)
      ? ok('running card shows the pulsing-dot state, not the finished state')
      : fail(`running card in wrong state: ${JSON.stringify(card1)}`);
    card1.label === 'root-cause the flaky test'
      ? ok('card label matches the server-supplied label verbatim')
      : fail(`card label wrong: "${card1.label}"`);
    (card1.meta.includes('12s') && card1.meta.includes('4') && card1.meta.toLowerCase().includes('tool call'))
      ? ok(`card meta carries elapsed time + tool-call count ("${card1.meta}")`)
      : fail(`card meta missing elapsed/tool-call info: "${card1.meta}"`);
  }

  // ── 2. Attribution is NOT desynced by the card's presence: same header
  // count as the baseline (channel-message-attribution.mjs) 2-run buffer ────
  const headers = await page.$$eval(`${scope}#agent-output-${seeded} .msg-attr`, (els) => els.length);
  headers === 1
    ? ok('attribution header count unaffected by the subagent card (1 header for the 1 narration run)')
    : fail(`expected exactly 1 .msg-attr header, got ${headers} — the card desynced _msgAttrPending`);
  // The card must sit OUTSIDE the attribution/narration bubble stream, not
  // consume the header slot meant for a narration bubble.
  const cardIsMsgAttr = await page.$eval(`${scope}#subagent-card-${seeded}-abc123`, (el) => el.classList.contains('msg-attr')).catch(() => false);
  !cardIsMsgAttr ? ok('the card itself never carries the .msg-attr class') : fail('the card was rendered AS an attribution header — desync');

  // ── 3. Warm path: the poll-driven updater flips running → finished IN
  // PLACE (same id, no duplicate card) ──────────────────────────────────────
  const finished = await page.evaluate(({ sid }) => {
    agentStatusCache[sid].activeSubagents = [
      { agent_id: 'abc123', parent_claude_session_id: 'csid-sub', label: 'root-cause the flaky test', started_at: new Date().toISOString(), elapsed_seconds: 47, tool_calls: 9, running: false },
    ];
    window._renderSubagentCards(sid);
    const cards = document.querySelectorAll(`#subagent-card-${sid}-abc123`);
    const el = cards[0];
    return {
      count: cards.length,
      running: el?.dataset.running,
      hasDoneClass: el?.classList.contains('subagent-card-done'),
      meta: el?.querySelector('.subagent-card-meta')?.textContent.trim(),
      hasCheck: !!el?.querySelector('.subagent-card-check'),
      hasDot: !!el?.querySelector('.subagent-card-dot'),
    };
  }, { sid: seeded });
  finished.count === 1
    ? ok('finishing a subagent updates the SAME card node, no duplicate')
    : fail(`expected exactly 1 card after the finish update, found ${finished.count}`);
  (finished.running === 'false' && finished.hasDoneClass && finished.hasCheck && !finished.hasDot)
    ? ok('finished card settles into the checkmark/done state (not removed, not still pulsing)')
    : fail(`finished card in wrong state: ${JSON.stringify(finished)}`);
  finished.meta && finished.meta.toLowerCase().includes('finished')
    ? ok(`finished card meta reads as a settled summary ("${finished.meta}")`)
    : fail(`finished card meta doesn't read as settled: "${finished.meta}"`);

  // ── 4. A highlight pass (chat-search) leaves the card intact ─────────────
  await page.evaluate(({ pid, sid }) => { openChatSearch(pid, sid); }, { pid: PID, sid: seeded });
  await page.waitForTimeout(50);  // _csEnsureComplete's fetch rejects (route.abort) and resolves; let the .then() land
  await page.evaluate(({ sid }) => { chatSearchOnInput(sid, 'flaky test'); }, { sid: seeded });
  const highlightResult = await page.evaluate(({ sid }) => {
    const card = document.getElementById(`subagent-card-${sid}-abc123`);
    return {
      hasMark: !!card?.querySelector('mark.mc-chat-search-hit'),
      labelText: card?.querySelector('.subagent-card-label')?.textContent.trim(),
      metaText: card?.querySelector('.subagent-card-meta')?.textContent.trim(),
      structureIntact: !!(card && card.querySelector('.subagent-card-head') && card.querySelector('.subagent-card-check')),
    };
  }, { sid: seeded });
  highlightResult.hasMark
    ? ok('chat-search found and wrapped a match INSIDE the subagent card label')
    : fail('chat-search did not find the match inside the card — card text is not searchable');
  highlightResult.labelText === 'root-cause the flaky test'
    ? ok('label text reconstructs correctly around the <mark> wrap')
    : fail(`label text corrupted by highlighting: "${highlightResult.labelText}"`);
  highlightResult.structureIntact
    ? ok('card structure (head/check) survives the highlight pass intact')
    : fail('highlight pass broke the card structure');
  await page.evaluate(({ sid }) => { closeChatSearch(sid); }, { sid: seeded });

  // ── 5. Rail: the roster row shows the helper count, and does NOT re-sort ─
  await page.click(`${scope}.rail-mode-btn >> text=Channel`);
  await page.waitForSelector(`${scope}.channel-row`, { timeout: 5000 });
  // Re-arm to a RUNNING helper for this part (test 3 above left it finished).
  await page.evaluate(({ sid }) => {
    agentStatusCache[sid].activeSubagents = [
      { agent_id: 'abc123', parent_claude_session_id: 'csid-sub', label: 'x', started_at: new Date().toISOString(), elapsed_seconds: 5, tool_calls: 1, running: true },
    ];
    window.updateRailRowStatus(sid);
  }, { sid: seeded });
  const helperBadge = await page.$eval(`${scope}.channel-row[data-char-key="project:code-reviewer"] .conv-helpers`, (el) => el.textContent.trim()).catch(() => null);
  helperBadge === '+1'
    ? ok('roster row shows the "+1" helper badge while a subagent is running')
    : fail(`expected a "+1" helper badge on Fenn's roster row, got: ${helperBadge}`);

  const orderBefore = await page.$$eval(`${scope}.channel-row`, (els) => els.map((el) => el.dataset.charKey));
  await page.evaluate(({ sid }) => {
    agentStatusCache[sid].activeSubagents = [];
    window.updateRailRowStatus(sid);
  }, { sid: seeded });
  const orderAfter = await page.$$eval(`${scope}.channel-row`, (els) => els.map((el) => el.dataset.charKey));
  const helperBadgeAfter = await page.$(`${scope}.channel-row[data-char-key="project:code-reviewer"] .conv-helpers`);
  !helperBadgeAfter ? ok('badge removed once no subagent is running any more')
                     : fail('helper badge persisted after the subagent list emptied');
  JSON.stringify(orderBefore) === JSON.stringify(orderAfter)
    ? ok('roster row order UNCHANGED across the helper-count update (no re-sort under the cursor)')
    : fail(`roster re-sorted: before=${JSON.stringify(orderBefore)} after=${JSON.stringify(orderAfter)}`);

  // ── 6. Empty active_subagents renders NOTHING — no empty-chrome container ─
  const seededEmpty = await page.evaluate(({ pid }) => {
    const sid = 'sess-empty';
    agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Empty Smoke', task: 'a normal turn', status: 'completed', startedAt: new Date().toISOString() });
    agentStatusCache[sid] = { status: 'completed', task: 'a normal turn', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: 'csid-empty', character: null, activeSubagents: [] };
    agentOutputBuffers[sid] = ['> Do a normal thing.', 'Done.'];
    openProjectModal(pid);
    return sid;
  }, { pid: PID_EMPTY });
  await page.waitForSelector(`.modal-window[data-modal-id="${PID_EMPTY}"] #agent-output-${seededEmpty}`, { timeout: 5000 });
  const emptyScope = `.modal-window[data-modal-id="${PID_EMPTY}"] `;
  const emptyContainer = await page.$(`${emptyScope}#subagent-cards-${seededEmpty}`);
  const emptyAnyCard = await page.$(`${emptyScope}.subagent-card`);
  (!emptyContainer && !emptyAnyCard)
    ? ok('empty active_subagents: no .subagent-cards container, no chrome at all')
    : fail('empty active_subagents still rendered a container/card');
  // Warm path too: explicitly re-invoke the updater with an empty list —
  // must stay a no-op, not create-then-immediately-hide a container.
  await page.evaluate(({ sid }) => { window._renderSubagentCards(sid); }, { sid: seededEmpty });
  const emptyContainerAfterWarm = await page.$(`${emptyScope}#subagent-cards-${seededEmpty}`);
  !emptyContainerAfterWarm
    ? ok('warm updater also stays a no-op for an empty list — no empty chrome ever created')
    : fail('warm updater created an empty .subagent-cards container');

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) {
    uncaught.forEach((e) => fail('uncaught exception during interaction: ' + e));
  }

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0
    ? '\n✅ PASS — nested subagent cards render running/finished states correctly, never desync attribution, survive a highlight pass, and the rail shows a stable (non-re-sorting) helper count.'
    : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}

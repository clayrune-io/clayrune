#!/usr/bin/env node
/**
 * MC-937 Phase 1 regression: the Channel rail mode (docs/CHANNEL_MODEL_SPEC.md).
 *
 * WHY THIS EXISTS
 * ----------------
 * Channel is a third rail mode (alongside Chats and Topics) that lists PEOPLE,
 * not chats: "In the room" (live, actively generating) / "Bench" (everyone
 * who's ever participated, idle) — roster rows derived from `character` on
 * conversationsCache rows + agentStatusCache liveness (conversation.js,
 * `_channelRoster` / `_railChannelHTML` / `_channelRowHTML`). Nothing here is
 * persisted — membership is a live grouping, so a regression is silent: no
 * server round-trip breaks, the rail just quietly renders zero people, one
 * row per chat instead of per person, or a broken empty state.
 *
 * This is a real headless boot (real index.html + real static/js/*.js served
 * verbatim, no server, no network) so a future edit to conversation.js is
 * exercised as shipped — same hermetic shape as conversation-persona-filter.mjs.
 * conversationsCache / agentStatusCache are seeded directly in-page (the same
 * shortcut boot-smoke.mjs's dispatch guard and conversation-persona-filter.mjs
 * both use), so no /conversations or /agent/status response shape needs
 * mocking.
 *
 * RUN
 *   cd tools/smoke && node channel-mode-roster.mjs
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
const PID = 'smoke_channel';       // project WITH history — grouping/live/click-filter cases
const PID_EMPTY = 'smoke_empty';   // vanilla project — the empty-state case

// Serve every real static/js/*.js and static/css/*.css verbatim (same
// approach as conversation-persona-filter.mjs) — a new module can never
// silently fall through to route.abort() the way a hand-maintained map would.
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
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Channel Smoke'), fixtureProject(PID_EMPTY, 'Empty Smoke')]);

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });  // desktop 3-pane rail
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
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });

  if (pageErrors.length) {
    pageErrors.forEach((e) => fail('uncaught page error during boot: ' + e));
  } else {
    ok('app booted clean, no uncaught exceptions');
  }

  const FENN = { name: 'code-reviewer', scope: 'project', display_name: 'code-reviewer', agent_name: 'Fenn', avatar: 'fig:scholar' };
  const TOBIN = { name: 'builder', scope: 'project', display_name: 'builder', agent_name: 'Tobin', avatar: 'fig:smith' };
  const MARLOW = { name: 'prd-writer', scope: 'global', display_name: 'prd-writer', agent_name: 'Marlow', avatar: 'fig:wizard' };

  // ── Seed history + a live run, open the modal, land in Channel mode ──────
  const seeded = await page.evaluate(({ pid, fenn, tobin, marlow }) => {
    // Two Fenn chats (must group into ONE roster row) + one Tobin chat (idle,
    // no live session — proves Bench membership doesn't require liveness) +
    // one unattributed chat (must NOT appear on the roster at all — spec §10,
    // no retroactive attribution) + one Marlow chat backing a WAITING session
    // (must land on the Bench with the badge, not "In the room" — a session
    // parked on the user isn't actively generating).
    conversationsCache[pid] = [
      { claude_session_id: 'fenn-1', mc_session_id: 'mc-fenn-1', character: fenn, mtime: 1000, ts_relative: '2h ago', status: 'completed', turns: 3, label: 'reviewed the auth PR', first_user: 'reviewed the auth PR', last_user: 'reviewed the auth PR' },
      { claude_session_id: 'fenn-2', mc_session_id: 'mc-fenn-2', character: fenn, mtime: 3000, ts_relative: '10m ago', status: 'completed', turns: 1, label: 'traced the timeout', first_user: 'traced the timeout', last_user: 'traced the timeout' },
      { claude_session_id: 'tobin-1', mc_session_id: 'mc-tobin-1', character: tobin, mtime: 2000, ts_relative: '1h ago', status: 'completed', turns: 2, label: 'fixed the export split', first_user: 'fixed the export split', last_user: 'fixed the export split' },
      { claude_session_id: 'plain-1', mc_session_id: 'mc-plain-1', character: null, mtime: 4000, ts_relative: '5m ago', status: 'completed', turns: 1, label: 'unattributed chat', first_user: 'unattributed chat', last_user: 'unattributed chat' },
      { claude_session_id: 'marlow-1', mc_session_id: 'mc-marlow-1', character: marlow, mtime: 500, ts_relative: '3h ago', status: 'idle', turns: 2, label: 'drafting the PRD', first_user: 'drafting the PRD', last_user: 'drafting the PRD', live: true, waiting_for_question: true },
    ];
    // Every row also gets a COMPLETED agentStatusCache entry keyed by its
    // mc_session_id — this puts a click on the row-open fast path (a direct
    // switchAgentTab, synchronous refreshModal) instead of the network
    // reconstruct-by-csid fallback, which is deliberately unmocked here (this
    // test is about the RAIL, not the reconstruct endpoints).
    for (const [csid, mcsid, ch] of [['fenn-1', 'mc-fenn-1', fenn], ['fenn-2', 'mc-fenn-2', fenn], ['tobin-1', 'mc-tobin-1', tobin], ['plain-1', 'mc-plain-1', null]]) {
      agentStatusCache[mcsid] = { status: 'completed', task: '', projectId: pid, startedAt: '', claudeSessionId: csid, character: ch };
    }
    // Tobin's SECOND, currently-running session — a distinct live entry (not
    // one of the completed rows above) is what puts her "In the room".
    agentStatusCache['live-tobin'] = { status: 'running', task: 'building', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: '', character: tobin };
    agentStatusCache['mc-marlow-1'] = { status: 'idle', task: 'drafting', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: 'marlow-1', character: marlow, waitingForQuestion: true };
    openProjectModal(pid);
    return true;
  }, { pid: PID, fenn: FENN, tobin: TOBIN, marlow: MARLOW });
  if (!seeded) fail('seeding evaluate() returned falsy');

  await page.waitForSelector(`.modal-window[data-modal-id="${PID}"] .agent-rail`, { timeout: 5000 });
  const scope = `.modal-window[data-modal-id="${PID}"] `;

  // ── 1. The three modes switch ─────────────────────────────────────────────
  const modeOn = async (label) => page.$eval(`${scope}.rail-mode-btn.on`, (el) => el.textContent.trim());
  ok(`starts on ${await modeOn()} by default`) ;  // whatever localStorage left it on — just prove .on exists
  await page.click(`${scope}.rail-mode-btn >> text=Topics`);
  (await modeOn()) === 'Topics' ? ok('switched to Topics') : fail('Topics tab did not activate');
  await page.click(`${scope}.rail-mode-btn >> text=Chats`);
  (await modeOn()) === 'Chats' ? ok('switched to Chats') : fail('Chats tab did not activate');
  await page.click(`${scope}.rail-mode-btn >> text=Channel`);
  (await modeOn()) === 'Channel' ? ok('switched to Channel') : fail('Channel tab did not activate');

  // ── 2. Channel groups real conversations into people ─────────────────────
  const rows = await page.$$eval(`${scope}.channel-row`, (els) => els.map((el) => ({
    key: el.dataset.charKey, name: el.querySelector('.conv-name')?.textContent.trim(),
  })));
  rows.length === 3 ? ok(`roster has 3 people (not 5 chats): ${rows.map(r => r.name).join(', ')}`)
                    : fail(`expected 3 roster rows (Fenn once, Tobin once, Marlow once), got ${rows.length}: ${JSON.stringify(rows)}`);
  rows.filter(r => r.name === 'Fenn').length === 1
    ? ok('Fenn — two chats collapsed into one roster row')
    : fail(`Fenn should appear exactly once, appeared ${rows.filter(r => r.name === 'Fenn').length} times`);
  rows.some(r => r.name === 'unattributed chat' || r.key === '')
    ? fail('an unattributed conversation leaked onto the roster — spec §10 forbids retroactive attribution')
    : ok('the unattributed chat did not leak onto the roster');

  // ── 3. A live session lands in "In the room"; the rest sit on the Bench ──
  const sectionOf = async (name) => page.evaluate(({ scopeSel, who }) => {
    const rowsEls = Array.from(document.querySelectorAll(scopeSel + '.channel-row'));
    const row = rowsEls.find((el) => el.querySelector('.conv-name')?.textContent.trim() === who);
    if (!row) return null;
    let el = row.previousElementSibling;
    while (el && !el.classList.contains('channel-section-header')) el = el.previousElementSibling;
    return el ? el.textContent.replace(/\d+$/, '').trim() : null;
  }, { scopeSel: scope, who: name });
  (await sectionOf('Tobin')) === 'In the room'
    ? ok('Tobin (live, running) sits under "In the room"')
    : fail(`Tobin should be under "In the room", section was: ${await sectionOf('Tobin')}`);
  (await sectionOf('Fenn')) === 'Bench'
    ? ok('Fenn (no live session) sits on the "Bench"')
    : fail(`Fenn should be on the "Bench", section was: ${await sectionOf('Fenn')}`);
  (await sectionOf('Marlow')) === 'Bench'
    ? ok('Marlow (waiting-for-you, not actively generating) sits on the "Bench", not "In the room"')
    : fail(`Marlow should be on the "Bench" (parked on the user isn't "in the room"), section was: ${await sectionOf('Marlow')}`);
  const tobinShimmer = await page.$eval(`${scope}.channel-row[data-char-key="project:builder"] .conv-time .act-word`, (el) => !!el).catch(() => false);
  tobinShimmer ? ok('Tobin\'s row shows the shimmer activity word (.act-word), not a static badge')
               : fail('Tobin\'s row is missing the .act-word shimmer treatment');
  const marlowBadge = await page.$eval(`${scope}.channel-row[data-char-key="global:prd-writer"] .conv-live-badge.waiting`, (el) => el.textContent.trim()).catch(() => null);
  marlowBadge === 'Waiting for you' ? ok('Marlow\'s row shows the "Waiting for you" badge in the timestamp slot')
                                    : fail(`Marlow's row should show the waiting badge, got: ${marlowBadge}`);

  // ── 4. Clicking a person narrows to their conversations, and nothing else ─
  await page.click(`${scope}.channel-row[data-char-key="project:code-reviewer"]`);
  await page.waitForSelector(`${scope}.channel-back`, { timeout: 3000 });
  const filteredIds = await page.$$eval(`${scope}.agent-rail-list .conv-row[data-csid]`, (els) => els.map((el) => el.dataset.csid).filter(Boolean));
  const filteredSet = new Set(filteredIds);
  (filteredSet.has('fenn-1') && filteredSet.has('fenn-2') && filteredSet.size === 2)
    ? ok('clicking Fenn narrowed the rail to exactly her 2 conversations')
    : fail(`clicking Fenn should show exactly [fenn-1, fenn-2], got: ${JSON.stringify([...filteredSet])}`);

  // ── 5. "← All people" returns to the full roster ─────────────────────────
  await page.click(`${scope}.channel-back`);
  await page.waitForSelector(`${scope}.channel-section-header`, { timeout: 3000 });
  const rosterBack = await page.$$eval(`${scope}.channel-row`, (els) => els.length);
  rosterBack === 3 ? ok('"← All people" restored the full 3-person roster')
                   : fail(`expected the roster back at 3 rows, got ${rosterBack}`);

  // ── 6. A vanilla / empty project shows the empty state, not a broken rail ─
  await page.evaluate(({ pid }) => { openProjectModal(pid); }, { pid: PID_EMPTY });
  await page.waitForSelector(`.modal-window[data-modal-id="${PID_EMPTY}"] .agent-rail`, { timeout: 5000 });
  const emptyScope = `.modal-window[data-modal-id="${PID_EMPTY}"] `;
  await page.click(`${emptyScope}.rail-mode-btn >> text=Channel`);
  await page.waitForSelector(`${emptyScope}.channel-empty`, { timeout: 3000 });
  const emptyRowCount = await page.$$eval(`${emptyScope}.channel-row`, (els) => els.length);
  const emptyTitle = await page.$eval(`${emptyScope}.channel-empty-title`, (el) => el.textContent.trim()).catch(() => null);
  (emptyRowCount === 0 && !!emptyTitle)
    ? ok(`vanilla project shows the empty state ("${emptyTitle}"), zero fake rows`)
    : fail(`vanilla project should show .channel-empty with 0 rows, got rows=${emptyRowCount} title=${emptyTitle}`);

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) {
    uncaught.forEach((e) => fail('uncaught exception during interaction: ' + e));
  }

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0
    ? '\n✅ PASS — Channel mode groups by person, live/waiting states place rows correctly, click-filter and empty state both hold.'
    : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}

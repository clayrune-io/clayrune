#!/usr/bin/env node
/**
 * Triggered chat messages collapse to a one-line expandable row
 * (static/js/triggered-collapse.js, static/css/triggered-collapse.css).
 *
 * Ron, 2026-10-06: turns a human did not type — a dispatched agent's
 * "[dispatched agent finished]" callback, a timer's scheduled prompt, a
 * <task-notification>, a rollover/handoff block — must not fill the chat with
 * full bubbles. Each renders as ONE line (icon + who/what + status + time) and
 * expands on click / Enter / Space to the original bubble.
 *
 * Checks, desktop (1400px) AND mobile (412px):
 *   1. cold render (history path, agentPanelHTML): every kind is a collapsed
 *      .trig-row, body hidden, row is a single line;
 *   2. a typed message — including one that merely MENTIONS a trigger phrase —
 *      and every agent reply render exactly as before (no row, no class);
 *   3. click, Enter and Space expand the original content; the body keeps the
 *      "> Ron:"-style bubble text;
 *   4. refreshModal rebuild: untouched rows come back collapsed, the one the
 *      reader expanded comes back expanded (state derives from the render,
 *      not from a node that no longer exists);
 *   5. live path (appendAgentLine): a callback arrives collapsed, a typed line
 *      does not.
 *
 * RUN: node tools/smoke/triggered-collapse.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_triggered';
const STATIC = loadStaticJsCss(REPO_ROOT);

const project = {
  id: PID, name: 'Triggered Smoke', status: 'active', domain: 'general', emoji: '\u{1f514}',
  description: '', summary: '', current_task: 'Idle', next_action: '',
  blocked: false, blocked_reason: null, activity_log: [], backlog: [],
  project_path: '/smoke/' + PID, last_updated: '2026-09-02T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null,
  display_order: 0, provider: 'claude', use_streaming_agent: true,
  distiller_mode: 'proposed', distiller_min_recurrence: 3,
  distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
  distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
  distiller_skip_errors: true,
};
const PROJECTS_JSON = JSON.stringify([project]);

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, pass, failMsg) => (cond ? ok(pass) : fail(failMsg));

// The buffer under test, in the exact line shapes the server writes.
const FINISHED = '> Ron: [dispatched agent finished] Tobin (session abc123def456) ended with status=completed.\n\nTask: fix the thing\n\nIts final message:\nAll done, merged.\n\nThis is the callback you asked for at dispatch. Continue the work it was part of -- do not re-dispatch it.';
const QUESTION = '> Ron: [dispatched agent asked a question] Posy (session 999aaa888bbb) is PAUSED waiting for an answer. It has NOT finished.';
const SCHED_CONT = '\n> [scheduled run]: [Scheduled run · 2026-10-06 09:00 EDT]\n\n[Backlog run] You are Dave, running the backlog on a timer.\n';
const SCHED_FIRST = '> [scheduled run]: [Backlog run] You are Dave, first fire.';
const SCHED_TRANSCRIPT = '\n> Ron: [Scheduled run · 2026-10-06 14:30 EDT]\n\n[Weekly review] look at the week.\n';
const NOTIF = '> Ron: <task-notification><task-id>t1</task-id><status>completed</status><summary>Background build finished</summary></task-notification>';
const HANDOFF = '> Ron: === Prior conversation, started on codex (session s1), handed off here ===\nuser: hi\n=== End of prior conversation. Continue from here, using the above as real context. ===';
const TYPED = '> Ron: please look at the failing test';
const TYPED_MENTION = '> Ron: why did you print [dispatched agent finished] in the last reply?';
const TYPED_SCHEDULE_WORD = '> Ron: schedule a run for tomorrow at 9';
const REPLY = 'Looking at the failing test now.';

const TRIG_LINES = [FINISHED, QUESTION, SCHED_CONT, SCHED_FIRST, SCHED_TRANSCRIPT, NOTIF, HANDOFF];
const BUFFER = [TYPED, REPLY, FINISHED, 'Agent reply after the callback.', QUESTION, SCHED_CONT, SCHED_FIRST,
  SCHED_TRANSCRIPT, NOTIF, HANDOFF, TYPED_MENTION, TYPED_SCHEDULE_WORD, 'Final agent line.'];
const TS = BUFFER.map((_, i) => new Date(2026, 9, 6, 10, 42 + i).toISOString());

async function newPage(browser, viewport, extraRoute) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message || String(e)));
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    const extra = extraRoute && extraRoute(path, route);
    if (extra) return extra;
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  return { page, errors };
}

// Real-world noise, not a regression: the hermetic harness aborts the mermaid CDN.
const realErrors = (errs) => errs.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));

async function run(browser, label, viewport, sid) {
  console.log(`
── ${label} (${viewport.width}px) ──`);
  const mobile = viewport.width <= 960;
  // Mobile has no inline agent pane: the chat opens from the conversation list
  // through the real /conversations + /reconstruct fetch path.
  const extraRoute = mobile ? (path, route) => {
    if (path === `/api/project/${PID}/conversations`) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{
        mc_session_id: sid, claude_session_id: 'csid-' + sid, provider: 'claude', live: false, status: 'completed',
        label: 'triggered smoke thread', first_user: 'triggered smoke thread', last_user: 'triggered smoke thread',
        ts: TS[TS.length - 1], ts_relative: '0m ago', turns: 1, waiting_for_question: false,
        waiting_for_plan_approval: false, resumable: true }]) });
    }
    if (path === `/api/project/${PID}/session/${sid}/reconstruct`) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        task: 'triggered smoke thread', started_at: TS[0], claude_session_id: 'csid-' + sid,
        log_lines: BUFFER, log_line_ts: TS }) });
    }
    return null;
  } : null;
  const { page, errors } = await newPage(browser, viewport, extraRoute);
  const outSel = `.modal-window[data-modal-id="${PID}"] #agent-output-${sid}`;
  if (mobile) {
    await page.evaluate((pid) => openProjectModal(pid), PID);
    const rowSel = `.modal-window[data-modal-id="${PID}"] .conv-row[data-mcsid="${sid}"]`;
    await page.waitForSelector(rowSel, { timeout: 5000 });
    await page.click(rowSel);
    await page.waitForSelector(outSel, { timeout: 5000 });
  } else {
    await page.evaluate(({ pid, sid, buf, ts }) => {
      agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Triggered Smoke', task: 'a thread', status: 'completed', startedAt: ts[0] });
      agentStatusCache[sid] = { status: 'completed', task: 'a thread', projectId: pid, startedAt: ts[0], claudeSessionId: 'csid-' + sid };
      agentOutputBuffers[sid] = buf;
      agentOutputTimestamps[sid] = ts;
      openProjectModal(pid);
    }, { pid: PID, sid, buf: BUFFER, ts: TS });
    await page.waitForSelector(outSel, { timeout: 5000 });
  }
  realErrors(errors).length ? realErrors(errors).forEach((e) => fail('page error during boot/open: ' + e)) : ok('app booted and chat opened clean');

  // 1. every triggered kind is a collapsed one-line row
  const rows = await page.evaluate((sel) => [...document.querySelectorAll(sel + ' .trig-row')].map((r) => ({
    kind: r.dataset.trigKind, text: r.textContent.replace(/\s+/g, ' ').trim(),
    expanded: r.getAttribute('aria-expanded'),
    bodyDisplay: getComputedStyle(r.parentElement.querySelector('.trig-body')).display,
    h: Math.round(r.getBoundingClientRect().height),
    overflowX: r.scrollWidth > r.clientWidth + 1,
  })), outSel);
  check(rows.length === TRIG_LINES.length, `${rows.length} triggered rows rendered`,
    `expected ${TRIG_LINES.length} rows, got ${rows.length}: ${JSON.stringify(rows)}`);
  check(rows.every((r) => r.expanded === 'false' && r.bodyDisplay === 'none'), 'all rows collapsed by default, bodies display:none',
    `a row is not collapsed: ${JSON.stringify(rows)}`);
  check(rows.every((r) => r.h <= 40), `every row is a single line (heights ${rows.map((r) => r.h).join(',')}px)`,
    `a row is taller than one line: ${JSON.stringify(rows.map((r) => r.h))}`);
  check(rows.every((r) => !r.overflowX), 'no row overflows horizontally', 'a row overflows horizontally');
  const want = [
    ['dispatch', 'Tobin finished: completed'], ['question', 'Posy asked a question'],
    ['schedule', 'Scheduled: Backlog run'], ['schedule', 'Scheduled: Backlog run'],
    ['schedule', 'Scheduled: Weekly review'], ['notification', 'Background task completed'],
    ['handoff', 'Handoff: continued from codex'],
  ];
  want.forEach(([kind, text], i) => check(rows[i] && rows[i].kind === kind && rows[i].text.includes(text),
    `row ${i + 1}: ${kind} — "${rows[i] && rows[i].text}"`, `row ${i + 1}: wanted ${kind} containing "${text}", got ${JSON.stringify(rows[i])}`));
  check(/10:4\d/.test(rows[0].text), `dispatch row carries its line time (${rows[0].text})`, `no HH:MM on the dispatch row: ${rows[0].text}`);
  check(rows[2].text.includes('09:00'), 'continued-schedule row takes its time from the [Scheduled run · …] header', `schedule time wrong: ${rows[2].text}`);

  // 2. typed messages and agent replies are untouched
  const typed = await page.evaluate((sel) => [...document.querySelectorAll(sel + ' .agent-line-prompt')]
    .filter((d) => !d.classList.contains('agent-line-triggered'))
    .map((d) => ({ text: d.textContent.replace(/\s+/g, ' ').trim(), hasRow: !!d.querySelector('.trig-row') })), outSel);
  check(typed.length === 3 && typed.every((t) => !t.hasRow), `${typed.length} typed messages left as normal bubbles (incl. one that merely mentions a trigger phrase)`,
    `typed messages wrong: ${JSON.stringify(typed)}`);
  check(typed.some((t) => t.text.includes('why did you print [dispatched agent finished]')), 'the typed message mentioning "[dispatched agent finished]" is NOT collapsed', 'typed mention was collapsed');
  const replies = await page.evaluate((sel) => [...document.querySelectorAll(sel + ' .agent-line')]
    .filter((d) => !d.classList.contains('agent-line-prompt')).map((d) => d.textContent.trim()), outSel);
  check(['Looking at the failing test now.', 'Agent reply after the callback.', 'Final agent line.'].every((r) => replies.includes(r)),
    'agent replies stay fully visible', `agent replies missing: ${JSON.stringify(replies)}`);

  // 3. click / Enter / Space expand to the original bubble
  const row0 = `${outSel} .trig-row >> nth=0`;
  await page.click(row0);
  let st = await page.evaluate((sel) => {
    const r = document.querySelector(sel + ' .trig-row'); const b = r.parentElement.querySelector('.trig-body');
    return { exp: r.getAttribute('aria-expanded'), disp: getComputedStyle(b).display, text: b.textContent };
  }, outSel);
  check(st.exp === 'true' && st.disp !== 'none' && st.text.includes('Its final message:') && st.text.includes('All done, merged.'),
    'click expands to the full original content', `click did not expand: ${JSON.stringify(st)}`);
  check(st.text.trimStart().startsWith('> Ron:'), 'expanded body is the original bubble text, as before', `expanded body changed: ${st.text.slice(0, 60)}`);
  await page.click(row0);
  st = await page.evaluate((sel) => document.querySelector(sel + ' .trig-row').getAttribute('aria-expanded'), outSel);
  check(st === 'false', 'second click collapses again', `still expanded: ${st}`);
  await page.focus(row0);
  await page.keyboard.press('Enter');
  st = await page.evaluate((sel) => document.querySelector(sel + ' .trig-row').getAttribute('aria-expanded'), outSel);
  check(st === 'true', 'Enter toggles the focused row open', `Enter: aria-expanded=${st}`);
  await page.keyboard.press('Space');
  st = await page.evaluate((sel) => document.querySelector(sel + ' .trig-row').getAttribute('aria-expanded'), outSel);
  check(st === 'false', 'Space toggles it closed', `Space: aria-expanded=${st}`);
  await page.keyboard.press('Enter');  // leave row 0 expanded for the rebuild check

  // 4. a full rebuild keeps collapsed-by-default AND the reader's open row
  await page.evaluate((pid) => refreshModalById(pid), PID);
  await page.waitForSelector(outSel, { timeout: 5000 });
  const after = await page.evaluate((sel) => [...document.querySelectorAll(sel + ' .trig-row')].map((r) => r.getAttribute('aria-expanded')), outSel);
  check(after.length === TRIG_LINES.length && after[0] === 'true' && after.slice(1).every((x) => x === 'false'),
    'after refreshModal: row 1 still open, the other six collapsed', `after rebuild: ${JSON.stringify(after)}`);

  // 5. live path
  await page.evaluate(({ sid, fin, typed }) => { appendAgentLine(sid, fin); appendAgentLine(sid, typed); }, { sid, fin: FINISHED.replace('Tobin', 'Vector'), typed: '> Ron: live typed message' });
  const live = await page.evaluate((sel) => {
    const kids = [...document.querySelectorAll(sel + ' > .agent-line-prompt')];
    const cb = kids[kids.length - 2], ty = kids[kids.length - 1];
    return { cbRow: cb.querySelector('.trig-row') && cb.querySelector('.trig-row').textContent.replace(/\s+/g, ' ').trim(),
      cbOpen: cb.querySelector('.trig-row') && cb.querySelector('.trig-row').getAttribute('aria-expanded'),
      tyRow: !!ty.querySelector('.trig-row'), tyTrig: ty.classList.contains('agent-line-triggered') };
  }, outSel);
  check(live.cbRow && live.cbRow.includes('Vector finished: completed') && live.cbOpen === 'false', `live callback arrives collapsed (${live.cbRow})`, `live callback wrong: ${JSON.stringify(live)}`);
  check(!live.tyRow && !live.tyTrig, 'live typed line is a normal bubble', `live typed line was collapsed: ${JSON.stringify(live)}`);

  realErrors(errors).length === 0 ? ok('no uncaught page errors') : realErrors(errors).forEach((e) => fail('page error: ' + e));
  await page.context().close();
}

let browser;
try {
  browser = await chromium.launch();
  await run(browser, 'desktop', { width: 1400, height: 900 }, 'sess-trig-desktop');
  await run(browser, 'mobile', { width: 412, height: 915 }, 'sess-trig-mobile');
} finally {
  if (browser) await browser.close();
}
if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('\nall triggered-collapse checks passed');

#!/usr/bin/env node
/**
 * MC-985 — Stop an agent, send a follow-up: chat must not hang forever on
 * "Thinking" with the pill stuck on STOPPED.
 *
 * WHY THIS EXISTS
 * ----------------
 * Ron's report ("once again", mobile): Stop a running agent, type a
 * follow-up, send. The server is fine — it revives the session, runs the
 * turn, and writes a full reply (verified live on clayrune_cloud session
 * 177e7fdabf5b: reply present, status idle) — but the client shows the echo
 * + "Thinking" forever and the pill never leaves STOPPED, until the chat is
 * reopened.
 *
 * ROOT CAUSE (two-part race, both in static/js/resume-preview.js):
 *
 *   1. sendFollowup() (conversation.js) opens an EAGER SSE before its POST
 *      to /agent/send resolves, so a fast turn_start isn't missed. For a
 *      session that was STOPPED, that eager stream can connect while the
 *      server still reports the OLD 'stopped' status (revival hasn't run
 *      yet) — `agent_stream`'s Mode-B branch (agent_routes.py) immediately
 *      emits `{type:'status', status:'stopped'}` and closes. The client's
 *      old rule ("'stopped' is always authoritative, even mid-send") let
 *      this THROUGH unconditionally — closing the eager stream and leaving
 *      `_sendInFlight` set with nothing left to clear it.
 *   2. Because the eager stream died before ever seeing the revival, the
 *      POST's `.then()` opens a SECOND stream once it resolves. If the
 *      revive + turn finish fast (exactly what's reported), server status
 *      is back to 'idle' by the time this second stream connects — so the
 *      'running' transition, and therefore `turn_start`, is never observed.
 *      The `turn_complete` that DOES arrive is then swallowed by the old
 *      "stale event, _sendInFlight is set" guard, which assumed anything
 *      arriving before turn_start must be an echo of the PRIOR turn. It
 *      isn't, here — it's the only word the client will ever get about the
 *      real one. Nothing else ever reconnects (the stream isn't a zombie —
 *      it just received its last real event), so the chat hangs until the
 *      user force-reopens it.
 *
 * THE FIX: track the status the session was in BEFORE the send
 * (`_preSendStatus`, static/index.html). A `status`/`turn_complete` event
 * during an in-flight send is stale ONLY when it reports that SAME status
 * with no turn_start yet — not merely because a send is in flight. A
 * different terminal status (idle/completed/error/a fresh stop) always
 * passes through and clears the gate, however it arrives.
 *
 * Hermetic: real index.html + real static/js/*.js served verbatim (same
 * technique as parked-chat-send-dots.mjs). No real server, no network. Two
 * scripted SSE connections model the race exactly: the first returns the
 * stale 'stopped' status and closes; the second (after the held POST
 * resolves) delivers the revived turn's output + turn_complete with NO
 * turn_start in between — modelling the fast-revive race the report hit.
 *
 * RUN   node tools/smoke/stop-then-send.mjs
 * Exit  0 = the chat recovers (pill flips, dots clear, reply visible);
 *        1 = a regression (this is what today's code fails on).
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
const PID = 'smoke_stop_then_send';
const SID = 'sess-stop-then-send';

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
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Stop Then Send Smoke')]);

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

async function runScenario(label, VW, VH) {
  console.log(`\n-- ${label} (${VW}x${VH}) --`);
  let browser;
  try {
    browser = await chromium.launch();
    const ctx = await browser.newContext({ viewport: { width: VW, height: VH }, hasTouch: VW < 960 });
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

    let sendPosted = false;
    let sendResolve = null;
    let streamConnections = 0;

    await page.route('**/*', (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
      const hit = STATIC[path];
      if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
      if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
      if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      // Held open like a real revive-from-stopped: rebuild context, replay
      // transcript, spawn `claude -r` — all synchronous inside the request.
      if (path === `/api/project/${PID}/agent/send`) {
        sendPosted = true;
        return new Promise((res) => {
          sendResolve = (body) => res(route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }));
        });
      }
      if (path === `/api/project/${PID}/agent/stream`) {
        streamConnections++;
        if (streamConnections === 1) {
          // The EAGER stream (opened before the POST above resolves) connects
          // while the server still reports the session's OLD status — the
          // revival hasn't run yet. Matches agent_routes.py's Mode-B branch
          // for a non-running/idle status: one status event, then close.
          return route.fulfill({
            status: 200, contentType: 'text/event-stream',
            body: 'data: ' + JSON.stringify({ type: 'status', status: 'stopped' }) + '\n\n',
          });
        }
        // The SECOND stream (opened once the POST resolves, because the
        // first was already closed). Models a FAST revive: by the time this
        // connects, the whole turn already finished server-side, so the
        // 'running' transition — and therefore turn_start — is never
        // observed. Only the output lines and the terminal turn_complete
        // arrive, exactly like the live repro (status idle, reply written).
        const events = [
          { type: 'output', text: '> resume after stop' },
          { type: 'output', text: 'Here is the answer you asked for.' },
          { type: 'turn_complete', status: 'idle' },
        ];
        const body = events.map((e) => 'data: ' + JSON.stringify(e) + '\n\n').join('');
        return route.fulfill({ status: 200, contentType: 'text/event-stream', body });
      }
      return route.abort();
    });

    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });

    await page.evaluate(({ pid, sid }) => {
      // A session the user just hit Stop on: status 'stopped', SSE already
      // closed — exactly the state sendFollowup() sees when the user then
      // types a follow-up and hits Send.
      agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Stop Then Send Smoke', task: 'an interrupted turn', status: 'stopped', startedAt: new Date().toISOString() });
      agentStatusCache[sid] = { status: 'stopped', task: 'an interrupted turn', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: 'csid-stop-then-send', character: null };
      agentOutputBuffers[sid] = ['> earlier message', '[Agent stopped by user]'];
      conversationsCache[pid] = [{ claude_session_id: 'csid-stop-then-send', mc_session_id: sid, character: null, mtime: 1000, ts_relative: '2h ago', status: 'stopped', turns: 1, label: 'an interrupted turn', first_user: 'earlier message', last_user: 'earlier message' }];
      openProjectModal(pid);
      openConversation(pid, 'csid-stop-then-send', sid, true);
    }, { pid: PID, sid: SID });

    await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });

    const pillText = () => page.evaluate(sid => {
      const el = document.getElementById(`agent-status-${sid}`) || document.querySelector(`[data-session-id="${sid}"] .agent-status-pill`);
      return el ? el.textContent.trim() : null;
    }, SID);
    const cachedStatus = () => page.evaluate(sid => (agentStatusCache[sid] || {}).status, SID);
    const dotsPresent = () => page.evaluate(sid => !!document.getElementById(`typing-${sid}`), SID);
    const echoPresent = () => page.evaluate(sid => {
      const el = document.getElementById(`agent-output-${sid}`);
      return !!(el && el.querySelector('.agent-echo'));
    }, SID);
    const replyVisible = () => page.evaluate(sid => {
      const el = document.getElementById(`agent-output-${sid}`);
      return !!(el && el.textContent.includes('Here is the answer you asked for.'));
    }, SID);

    ('stopped' === (await cachedStatus()))
      ? ok('baseline: session cached as stopped before Send')
      : fail('test setup wrong — session was not stopped before Send');

    // ── Type + fire the send, exactly like tapping Send on a stopped chat ──
    await page.evaluate(sid => { document.getElementById(`agent-followup-${sid}`).value = 'keep going'; }, SID);
    await page.evaluate(({ pid, sid }) => { window.sendFollowup(pid, sid); }, { pid: PID, sid: SID });

    // Let the eager (1st) SSE connect, receive the stale 'stopped' status,
    // and close — this is the moment the old code mis-fired the pill back
    // to STOPPED and discarded the stream with nothing left to recover it.
    await page.waitForTimeout(400);

    sendPosted
      ? ok('the /agent/send POST fired')
      : fail('/agent/send was never called — sendFollowup did not run to completion');
    (await echoPresent())
      ? ok('local echo of the follow-up is shown')
      : fail('local echo missing after send');

    // Now let the held POST resolve — same session id, matching a real
    // revive (no fresh dispatch). This is what finally lets the client open
    // its SECOND stream, which (per the race above) delivers the whole
    // finished turn with no turn_start in between.
    if (typeof sendResolve === 'function') sendResolve({ ok: true, session_id: SID });
    await page.waitForTimeout(600);

    (await replyVisible())
      ? ok('the revived turn\'s reply text is rendered')
      : fail('reply text never appeared in the transcript');
    (!(await dotsPresent()))
      ? ok('the "Thinking" indicator is cleared once the turn is known complete')
      : fail('stuck on "Thinking" — turn_complete was swallowed (the MC-985 hang)');
    ('idle' === (await cachedStatus()))
      ? ok('session status recovered to idle (pill is not stuck on STOPPED)')
      : fail(`session status never left "${await cachedStatus()}" — pill would still read STOPPED`);

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    uncaught.forEach((e) => fail('uncaught page error: ' + e));

    await ctx.close();
  } finally {
    if (browser) await browser.close();
  }
}

let exitCode = 1;
try {
  await runScenario('Desktop', 1280, 800);
  await runScenario('Mobile 390px', 390, 844);
  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error(e);
  exitCode = 1;
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(exitCode);

#!/usr/bin/env node
/**
 * MC-987 — Stop button must be visible from the moment a turn is RUNNING,
 * including the first turn after a break/recovery (server restart, a
 * revived/parked chat, an auto-fresh rollover) — not only once that turn
 * completes.
 *
 * WHY THIS EXISTS
 * ----------------
 * Ron's report (phone): open a revived/parked chat and send a follow-up.
 * No Stop control appears while the turn is running; it only shows up once
 * the turn has already finished.
 *
 * ROOT CAUSE: sendFollowup() deliberately never writes an optimistic
 * 'running' status into agentStatusCache (Phase-2 race consolidation — the
 * server picks the route, the UI waits for an SSE event). When `turn_start`
 * DOES arrive, resume-preview.js patches the status dot/label/rail row IN
 * PLACE (updateAgentStatusUI / updateHistoryStatus / updateRailRowStatus)
 * but never touched the Stop button — that control only existed inside the
 * full agentPanelHTML() string a refreshModal() rebuild produces, and
 * turn_start/turn_complete deliberately SKIP that rebuild (it recreates the
 * chat textarea every turn — the measured cause of the mobile IME-death
 * bug). A revived session's cached status sits on a terminal value
 * ('completed'/'stopped') that renders no Stop button at all, so the button
 * never appears for the whole running turn — only once some unrelated full
 * rebuild happens to fire afterward.
 *
 * THE FIX: a targeted in-place patch (updateStopButtonUI, static/index.html)
 * mirroring updateAgentStatusUI's "touch only the nodes that carry status"
 * discipline — it fills/clears a stable `#stop-btn-<sid>` (and
 * `#stop-btn-split-<sid>`) slot the header/split-pane templates now always
 * render, called from turn_start, turn_complete, the `status` SSE event, and
 * the freshness reconciler's status-change path.
 *
 * Hermetic: real index.html + real static/js/*.js served verbatim (same
 * technique as parked-chat-send-dots.mjs / stop-then-send.mjs). No real
 * server, no network. The /agent/send POST is held open (models the
 * synchronous revival window) and the FIRST SSE connection delivers
 * `turn_start` on its own — proving the Stop button does not depend on the
 * POST ever resolving, only on turn_start.
 *
 * RUN   node tools/smoke/revived-chat-stop-button.mjs
 * Exit  0 = Stop is visible the instant turn_start arrives, and stays
 *           visible through turn_complete; 1 = a regression.
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
const PID = 'smoke_revived_chat_stop';
const SID = 'sess-revived-chat-stop';

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
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Revived Chat Stop Smoke')]);

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
      // Held open like a real revive-from-completed: rebuild context, replay
      // transcript, spawn `claude -r` — all synchronous inside the request.
      // Never resolved until after the turn_start assertions below, so a
      // pass here proves the Stop button does NOT depend on this settling.
      if (path === `/api/project/${PID}/agent/send`) {
        sendPosted = true;
        return new Promise((res) => {
          sendResolve = (body) => res(route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }));
        });
      }
      if (path === `/api/project/${PID}/agent/stream`) {
        streamConnections++;
        if (streamConnections === 1) {
          // Eager stream (opened before the POST above resolves). A fast
          // revive reaches turn_start before the request itself returns —
          // deliver it here, alone, then close.
          return route.fulfill({
            status: 200, contentType: 'text/event-stream',
            body: 'data: ' + JSON.stringify({ type: 'turn_start' }) + '\n\n',
          });
        }
        // Reconnect after the first stream closes: deliver the reply and
        // the terminal turn_complete.
        const events = [
          { type: 'output', text: '> keep going' },
          { type: 'output', text: 'Picking up where we left off.' },
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
      // A revived/parked chat: server-forgotten session reconstructed
      // read-only from its transcript — exactly openConversation()'s
      // reconstruct branch (conversation.js). Cached status is a TERMINAL
      // value that renders no Stop button on its own, and no live SSE yet.
      agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Revived Chat Stop Smoke', task: 'a finished turn', status: 'completed', startedAt: new Date().toISOString() });
      agentStatusCache[sid] = { status: 'completed', task: 'a finished turn', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: 'csid-revived-chat-stop', character: null, _readOnlyRevived: true };
      agentOutputBuffers[sid] = ['> earlier message', 'earlier reply'];
      conversationsCache[pid] = [{ claude_session_id: 'csid-revived-chat-stop', mc_session_id: sid, character: null, mtime: 1000, ts_relative: '2h ago', status: 'completed', turns: 1, label: 'a finished turn', first_user: 'earlier message', last_user: 'earlier message' }];
      openProjectModal(pid);
      openConversation(pid, 'csid-revived-chat-stop', sid, true);
    }, { pid: PID, sid: SID });

    await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });

    const stopVisible = () => page.evaluate(sid => !!document.querySelector(`#stop-btn-${sid} .btn-stop, .btn-stop[onclick*="${sid}"]`), SID);
    const cachedStatus = () => page.evaluate(sid => (agentStatusCache[sid] || {}).status, SID);

    (!(await stopVisible()))
      ? ok('baseline: no Stop button while the revived chat sits completed')
      : fail('Stop button already present before Send — test setup is wrong');

    // ── Type + fire the send, exactly like tapping Send on a revived chat ──
    await page.evaluate(sid => { document.getElementById(`agent-followup-${sid}`).value = 'keep going'; }, SID);
    await page.evaluate(({ pid, sid }) => { window.sendFollowup(pid, sid); }, { pid: PID, sid: SID });

    // Let the eager (1st) SSE connect and deliver turn_start — the POST is
    // still held open, unresolved, the whole time.
    await page.waitForTimeout(400);

    sendPosted
      ? ok('the /agent/send POST fired')
      : fail('/agent/send was never called — sendFollowup did not run to completion');
    ('running' === (await cachedStatus()))
      ? ok('turn_start flipped the cached status to running')
      : fail(`cached status is "${await cachedStatus()}", not running — turn_start was not processed`);
    (typeof sendResolve === 'function')
      ? ok('/agent/send POST is still unresolved — the revival round trip has not settled yet')
      : fail('the send POST route never armed — cannot confirm reply-independence');
    (await stopVisible())
      ? ok('Stop button is visible the instant turn_start arrives — before the POST resolves, before turn_complete')
      : fail('Stop button is MISSING while the turn is running (MC-987)');

    // Now let the held POST resolve — same session id, matching a real
    // in-place revive (no fresh dispatch).
    if (typeof sendResolve === 'function') sendResolve({ ok: true, session_id: SID });
    await page.waitForTimeout(600);

    (await stopVisible())
      ? ok('Stop button is still visible once the POST resolves')
      : fail('Stop button disappeared once the POST resolved');

    // The second stream delivers turn_complete → idle. Stop stays visible
    // (idle is resumable, same as the pre-send terminal-render contract).
    (await page.evaluate(sid => document.getElementById(`agent-output-${sid}`)?.textContent.includes('Picking up where we left off.'), SID))
      ? ok('the revived turn\'s reply text is rendered')
      : fail('reply text never appeared in the transcript');
    ('idle' === (await cachedStatus()))
      ? ok('session settled to idle after turn_complete')
      : fail(`session status is "${await cachedStatus()}" after turn_complete, expected idle`);
    (await stopVisible())
      ? ok('Stop button remains visible after turn_complete (idle stays resumable)')
      : fail('Stop button vanished after turn_complete');

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

#!/usr/bin/env node
/**
 * MC-973 — dots must appear immediately on send, even to a PARKED chat.
 *
 * WHY THIS EXISTS
 * ----------------
 * Ron's report: sending to a parked (server-forgotten) project chat on mobile
 * lags badly before anything happens on screen. Measuring against a throwaway
 * project (docs/_journal/0063b3fc-mc973.md) traced it to two things stacking:
 *
 *   1. `/agent/send`'s fresh_or_revive path (`_revive_from_agent_log`,
 *      mc/blueprints/agent_routes.py) runs entirely SYNCHRONOUSLY inside the
 *      request — context rebuild, transcript-history replay, and the
 *      `claude -r` spawn all happen before the POST resolves at all.
 *   2. The client's eager SSE connect (`connectAgentStream`, opened BEFORE
 *      the POST so a fast turn's `turn_start` isn't missed) hits a session
 *      that doesn't exist yet server-side, gets `{type:'error', msg:'no
 *      active session'}` immediately, and — correctly, to avoid a false
 *      "Blocked" pill — closes SILENTLY when `_sendInFlight` is set
 *      (resume-preview.js's `msg.type === 'error'` handler).
 *
 * Net effect measured: a LIVE follow-up painted dots ~0.1s after send; a
 * PARKED one painted NOTHING for the entire revival window (which scales
 * with a real project's context + transcript size, not just the ~0.25s seen
 * against an empty throwaway project) until the POST itself finally resolved.
 *
 * THE FIX (static/js/conversation.js, sendFollowup): paint the typing
 * indicator eagerly, synchronously, the moment Send is pressed — a pure DOM
 * op with no status-cache write, so it doesn't touch the Phase-2 "server
 * picks the route" invariant. Cleaned up if the send itself errors, so a
 * rejected send doesn't leave the dots spinning forever.
 *
 * Hermetic: real index.html + real static/js/*.js served verbatim (same
 * technique as mobile-send-height-restore.mjs). No real server, no network.
 * The /agent/send POST is held open indefinitely — modelling a parked-chat
 * revival that hasn't resolved yet — so a pass here proves the dots do NOT
 * depend on that round trip completing.
 *
 * RUN   node tools/smoke/parked-chat-send-dots.mjs
 * Exit  0 = dots are eager and self-cleaning; 1 = a regression.
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
const PID = 'smoke_parked_chat';
const SID = 'sess-parked-chat';
const VW = 390, VH = 844;   // iPhone 12/13/14-class viewport — the exact width the ticket asks for

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
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Parked Chat Smoke')]);

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

let browser;
let exitCode = 1;
try {
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: VW, height: VH }, hasTouch: true });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

  let sendPosted = false;
  let sendResolve = null;
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    // Models the server-side revival window (agent_send -> _revive_from_agent_log)
    // that never resolves during this check: dots must not depend on it.
    if (path === `/api/project/${PID}/agent/send`) {
      sendPosted = true;
      return new Promise((res) => {
        sendResolve = (body) => res(route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }));
      });
    }
    // The eager SSE connect hits a session the (simulated) server has
    // forgotten — same "no active session" immediate error a real parked
    // chat's first connect gets, before the revive above ever runs.
    if (path === `/api/project/${PID}/agent/stream`) {
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: 'data: {"type":"error","msg":"no active session"}\n\n' });
    }
    return route.abort();
  });

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });

  await page.evaluate(({ pid, sid }) => {
    // A PARKED chat: idle, not running — exactly the state a revived
    // session starts a send from. No live process, server-side.
    agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Parked Chat Smoke', task: 'a finished turn', status: 'idle', startedAt: new Date().toISOString() });
    agentStatusCache[sid] = { status: 'idle', task: 'a finished turn', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: 'csid-parked-chat', character: null };
    agentOutputBuffers[sid] = ['> earlier message', 'earlier reply'];
    conversationsCache[pid] = [{ claude_session_id: 'csid-parked-chat', mc_session_id: sid, character: null, mtime: 1000, ts_relative: '2h ago', status: 'idle', turns: 1, label: 'a finished turn', first_user: 'earlier message', last_user: 'earlier message' }];
    openProjectModal(pid);
    openConversation(pid, 'csid-parked-chat', sid, true);
  }, { pid: PID, sid: SID });

  await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });

  const dotsPresent = () => page.evaluate(sid => !!document.getElementById(`typing-${sid}`), SID);

  (!(await dotsPresent()))
    ? ok('baseline: no typing indicator before Send (parked chat is idle)')
    : fail('typing indicator already present before Send — test setup is wrong');

  // ── Type + fire the send exactly like a real tap, and check the dots
  // WITHIN ONE FRAME — long before the withheld POST could ever settle ──
  await page.evaluate(sid => { document.getElementById(`agent-followup-${sid}`).value = 'wake up'; }, SID);
  await page.evaluate(({ pid, sid }) => { window.sendFollowup(pid, sid); }, { pid: PID, sid: SID });
  await page.evaluate(() => new Promise(requestAnimationFrame));

  (await dotsPresent())
    ? ok('dots appear within one frame of Send, before the revival POST resolves')
    : fail('dots did NOT appear immediately — send is still gated on the server round trip');

  await page.waitForTimeout(300);
  sendPosted
    ? ok('the /agent/send POST fired (send path continued normally after the eager paint)')
    : fail('/agent/send was never called — sendFollowup did not run to completion');
  (typeof sendResolve === 'function')
    ? ok('/agent/send was still unresolved when the dots check ran — eager paint is reply-independent')
    : fail('the send POST route never armed — cannot confirm reply-independence');
  (await dotsPresent())
    ? ok('dots are still showing while the revival is still in flight (no premature hide)')
    : fail('dots disappeared while the send was still pending — something hid them too early');

  // ── Server finally answers with a hard failure (revive AND fresh-dispatch
  // both failed) — the dots must not spin forever with nothing left to wait for.
  if (typeof sendResolve === 'function') sendResolve({ ok: false, error: 'simulated revive failure' });
  await page.waitForTimeout(300);

  (!(await dotsPresent()))
    ? ok('dots are cleared once the server reports the send failed (no stuck spinner)')
    : fail('dots kept spinning after a hard send failure — needs cleanup on the !data.ok path');

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail('uncaught page error: ' + e));

  await ctx.close();
  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error(e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(exitCode);

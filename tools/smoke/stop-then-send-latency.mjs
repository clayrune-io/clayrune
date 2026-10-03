#!/usr/bin/env node
/**
 * MC-986 — after Stop, the mobile pane stays wedged at keyboard-inset height
 * and the eager "Thinking" dots paint into that clipped region invisibly,
 * so Send *looks* like it does nothing for a few seconds until something
 * else (the 500ms layout watchdog, or the next turn's re-render) finally
 * recovers `--mc-app-vh`.
 *
 * ROOT CAUSE (static/js/conversation.js stopAgent()): the client-initiated
 * Stop path never calls the mobile viewport-height recovery hook
 * (mobile.js's mcRecoverViewportOnStatusSettle, exposed as
 * window.mcRecoverViewportOnStatusSettle). Every OTHER place a session
 * settles into a non-running status calls it from updateAgentStatusUI
 * (static/index.html ~1977) — turn_complete (idle), the SSE terminal
 * 'status' branch, fetchAgentStatus's reconciler. stopAgent() bypasses all
 * of those: it closes its own SSE synchronously (so the terminal 'status'
 * event this ticket's sibling code path relies on never arrives) and never
 * calls updateAgentStatusUI itself, so nothing ever re-validates the
 * viewport height at the one moment Stop is actually pressed. If the
 * composer had a real keyboard inset applied (the ordinary case — the user
 * was just typing when they hit Stop), `--mc-app-vh` stays short from then
 * on, through the entire STOPPED state, and into the eager dots painted by
 * a subsequent Send.
 *
 * This harness sets `--mc-app-vh` to a keyboard-open height (as mobile.js
 * itself would have left it mid-conversation), calls the real stopAgent(),
 * and measures whether the pane recovers on its own. Then it simulates a
 * real Send (a real DOM click, not a JS call, so native button-steals-focus
 * blur behavior applies) and measures (a) time from click to the "Thinking"
 * dots being actually IN VIEW (not just present in the DOM — a node
 * appended into a clipped, zero-visible-height container is a false
 * negative for "the user can see it"), and (b) time from click to the pane
 * reaching full height. Compared against an idle-live and a parked chat,
 * which never touch stopAgent() and are expected to already be fast
 * (MC-973).
 *
 * Hermetic: real index.html + real static/js/*.js served verbatim, same
 * technique as stop-then-send.mjs / parked-chat-send-dots.mjs. No real
 * server, no network.
 *
 * RUN   node tools/smoke/stop-then-send-latency.mjs
 * Exit  0 = dots become visible and the pane reaches full height within
 *           ~300ms of Send in all three scenarios; 1 = a regression.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_stop_latency';

const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));

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
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Stop Latency Smoke')]);

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

// Keyboard-open inset mobile.js would have applied while the user was typing
// the message that then got interrupted — matches its own MIN_KB=120 floor.
const KEYBOARD_INSET_PX = 320;

async function runScenario(kind, VW, VH) {
  const isMobile = VW <= 960;
  const label = `${kind} (${VW}x${VH})`;
  console.log(`\n-- ${label} --`);
  let browser;
  try {
    browser = await chromium.launch();
    const ctx = await browser.newContext({ viewport: { width: VW, height: VH }, hasTouch: isMobile });
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

    const SID = `sess-${kind}`;
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
      if (path === `/api/project/${PID}/agent/stop`) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
      }
      if (path === `/api/project/${PID}/agent/send`) {
        sendPosted = true;
        // Real server acks fast, then the turn progresses over SSE — don't
        // hold this open until after the measurement window like the first
        // draft did (that made the app's real reconnect-on-ack path never
        // run during polling, so pane recovery looked broken in ALL three
        // scenarios, not just 'stopped').
        return new Promise((res) => {
          sendResolve = (body) => res(route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }));
          setTimeout(() => sendResolve({ ok: true, session_id: SID }), 20);
        });
      }
      if (path === `/api/project/${PID}/agent/stream`) {
        streamConnections++;
        if (kind === 'stopped') {
          if (streamConnections === 1) {
            // Matches agent_routes.py's Mode-B branch: eager SSE connects
            // while the server still reports the OLD 'stopped' status.
            return route.fulfill({
              status: 200, contentType: 'text/event-stream',
              body: 'data: ' + JSON.stringify({ type: 'status', status: 'stopped' }) + '\n\n',
            });
          }
          const events = [
            { type: 'output', text: '> resume after stop' },
            { type: 'output', text: 'Here is the answer you asked for.' },
            { type: 'turn_complete', status: 'idle' },
          ];
          return route.fulfill({ status: 200, contentType: 'text/event-stream', body: events.map((e) => 'data: ' + JSON.stringify(e) + '\n\n').join('') });
        }
        if (kind === 'idle-live') {
          // Session already idle server-side (not stopped, not purged) —
          // the eager connect observes the Mode-B idle_sent turn_complete,
          // which the FE's own MC-985 guard treats as a stale echo of the
          // pre-send state (_preSendStatus === 'idle').
          if (streamConnections === 1) {
            return route.fulfill({ status: 200, contentType: 'text/event-stream', body: 'data: ' + JSON.stringify({ type: 'turn_complete', status: 'idle' }) + '\n\n' });
          }
          // A genuine new turn on a real server always opens with turn_start
          // (that's what flips the MC-985 _turnStartAcked gate so the REAL
          // turn_complete below isn't swallowed by the same stale-echo guard
          // that correctly ate the connection-1 echo above). Omitting it here
          // was a test-fixture bug: it made this scenario indistinguishable
          // from the swallowed echo and hid genuine 'idle-live' behavior.
          const events = [
            { type: 'turn_start' },
            { type: 'output', text: '> keep going' },
            { type: 'output', text: 'Sure, continuing.' },
            { type: 'turn_complete', status: 'idle' },
          ];
          return route.fulfill({ status: 200, contentType: 'text/event-stream', body: events.map((e) => 'data: ' + JSON.stringify(e) + '\n\n').join('') });
        }
        // parked: server has forgotten the session entirely — the eager
        // connect (before /agent/send resolves) genuinely has nothing to
        // observe. Once send revives it (connection 2, same mc session id),
        // a real turn_start/output/turn_complete sequence follows exactly
        // like idle-live's.
        if (streamConnections === 1) {
          return route.fulfill({ status: 200, contentType: 'text/event-stream', body: 'data: ' + JSON.stringify({ type: 'error', msg: 'no active session' }) + '\n\n' });
        }
        const events = [
          { type: 'turn_start' },
          { type: 'output', text: '> wake up' },
          { type: 'output', text: 'Back online.' },
          { type: 'turn_complete', status: 'idle' },
        ];
        return route.fulfill({ status: 200, contentType: 'text/event-stream', body: events.map((e) => 'data: ' + JSON.stringify(e) + '\n\n').join('') });
      }
      return route.abort();
    });

    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });

    const initialStatus = kind === 'idle-live' ? 'idle' : (kind === 'parked' ? 'idle' : 'stopped');
    await page.evaluate(({ pid, sid, status }) => {
      agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Stop Latency Smoke', task: 'earlier turn', status, startedAt: new Date().toISOString() });
      agentStatusCache[sid] = { status, task: 'earlier turn', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: 'csid-' + sid, character: null };
      agentOutputBuffers[sid] = ['> earlier message', status === 'stopped' ? '[Agent stopped by user]' : 'earlier reply'];
      conversationsCache[pid] = [{ claude_session_id: 'csid-' + sid, mc_session_id: sid, character: null, mtime: 1000, ts_relative: '2h ago', status, turns: 1, label: 'earlier turn', first_user: 'earlier message', last_user: 'earlier message' }];
      openProjectModal(pid);
      openConversation(pid, 'csid-' + sid, sid, true);
    }, { pid: PID, sid: SID, status: initialStatus });

    await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });

    // Simulate the residual keyboard inset mobile.js would have left from
    // typing the message that preceded Stop/the prior turn. mobile.js tracks
    // --mc-app-vh unconditionally (only the CSS *consumption* of it is
    // scoped to the mobile media query), so exercising the same recovery
    // path on desktop too is valid and gives a like-for-like number.
    await page.evaluate((inset) => {
      document.documentElement.style.setProperty('--mc-app-vh', (window.innerHeight - inset) + 'px');
    }, KEYBOARD_INSET_PX);
    await page.waitForTimeout(50);

    const fullHeightPx = await page.evaluate(() => window.innerHeight);
    // Read the CSS custom property mobile.js's apply()/forceFull() write to,
    // rather than a rendered element's boundingClientRect: the modal has its
    // own chrome (a `calc(var(--mc-app-vh) - 52px)` header allowance, and on
    // desktop no full-viewport sizing at all), so the property itself is the
    // only signal that is meaningful on both viewport classes.
    const paneHeight = () => page.evaluate(() => {
      const v = getComputedStyle(document.documentElement).getPropertyValue('--mc-app-vh').trim();
      const n = parseFloat(v);
      return Number.isFinite(n) ? n : 0;
    });
    const dotsInView = () => page.evaluate(sid => {
      const dot = document.getElementById(`typing-${sid}`);
      if (!dot) return false;
      const r = dot.getBoundingClientRect();
      const scroller = dot.closest('.agent-output') || dot.parentElement;
      if (!scroller) return r.height > 0 && r.width > 0;
      const sr = scroller.getBoundingClientRect();
      // Visible = has real geometry AND its top falls within the scroll
      // container's visible viewport (not just present past the clipped edge).
      return r.height > 0 && r.width > 0 && r.top < sr.bottom && r.bottom > sr.top && sr.height > 20;
    }, SID);

    if (kind === 'stopped') {
      const beforeStopH = await paneHeight();
      await page.evaluate(({ pid, sid }) => { window.stopAgent(pid, sid); }, { pid: PID, sid: SID });
      await page.waitForTimeout(150); // let the fetch + refreshSilent settle
      const afterStopH = await paneHeight();
      console.log(`  pane height before Stop-recovery check: ${beforeStopH.toFixed(0)}px (viewport ${fullHeightPx}px)`);
      (Math.abs(afterStopH - fullHeightPx) < 20)
        ? ok(`pane is full height immediately after Stop settles (${afterStopH.toFixed(0)}px vs ${fullHeightPx}px viewport)`)
        : fail(`pane still short after Stop settles: ${afterStopH.toFixed(0)}px vs ${fullHeightPx}px viewport (--mc-app-vh never recovered)`);
    }

    // ── Now type + send via a REAL click, so native button-steals-focus
    // blur behavior applies exactly as it would for a user's tap. ──
    const input = await page.$(`#agent-followup-${SID}`);
    await input.click();
    await input.type(kind === 'stopped' ? 'keep going' : 'wake up');

    const t0 = Date.now();
    const sendBtn = await page.$(`button[onclick*="sendFollowup('${PID}','${SID}')"]`);
    if (sendBtn) await sendBtn.click(); else await page.evaluate(({ pid, sid }) => window.sendFollowup(pid, sid), { pid: PID, sid: SID });

    // Poll every 16ms (one frame) up to 2s for dots-in-view and full pane height.
    let dotsAtMs = null, paneAtMs = null;
    for (let elapsed = 0; elapsed <= 2000; elapsed += 16) {
      const [dv, ph] = await Promise.all([dotsInView(), paneHeight()]);
      if (dotsAtMs === null && dv) dotsAtMs = Date.now() - t0;
      if (paneAtMs === null && Math.abs(ph - fullHeightPx) < 20) paneAtMs = Date.now() - t0;
      if (dotsAtMs !== null && paneAtMs !== null) break;
      await page.waitForTimeout(16);
    }

    console.log(`  send -> dots visible: ${dotsAtMs === null ? 'NEVER within 2000ms' : dotsAtMs + 'ms'}`);
    console.log(`  send -> pane full height: ${paneAtMs === null ? 'NEVER within 2000ms' : paneAtMs + 'ms'}`);

    (dotsAtMs !== null && dotsAtMs <= 300)
      ? ok(`dots visible within 300ms of send (${dotsAtMs}ms)`)
      : fail(`dots not visible within 300ms of send (${dotsAtMs === null ? 'never' : dotsAtMs + 'ms'})`);
    (paneAtMs !== null && paneAtMs <= 300)
      ? ok(`pane at full height within 300ms of send (${paneAtMs}ms)`)
      : fail(`pane not at full height within 300ms of send (${paneAtMs === null ? 'never' : paneAtMs + 'ms'})`);

    sendPosted ? ok('the /agent/send POST fired') : fail('/agent/send was never called');
    await page.waitForTimeout(400);

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    uncaught.forEach((e) => fail('uncaught page error: ' + e));

    await ctx.close();
    return { dotsAtMs, paneAtMs };
  } finally {
    if (browser) await browser.close();
  }
}

let exitCode = 1;
try {
  for (const [VW, VH, label] of [[1280, 800, 'Desktop'], [390, 844, 'Mobile 390px']]) {
    await runScenario('stopped', VW, VH);
    await runScenario('idle-live', VW, VH);
    await runScenario('parked', VW, VH);
  }
  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error(e);
  exitCode = 1;
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(exitCode);

#!/usr/bin/env node
/**
 * hivemind hm_d9c76579 / finding f_6506aeb9: the conversation rail must
 * never render 0 rows because a turn/session ended.
 *
 * ROOT CAUSE (static/js/resume-preview.js, connectAgentStream's onmessage,
 * the 'status' and 'error' branches — NOT the 'turn_complete' branch, which
 * was already clean since 8114775a):
 *   delete agentLogCache[projectId];
 *   delete conversationsCache[projectId];
 *   if (agentLogOpen[projectId]) loadAgentLog(projectId);
 *   loadConversations(projectId);
 *   refreshModal();                          // <-- synchronous, cache is EMPTY here
 * refreshModal() re-renders the rail from the just-deleted caches before
 * either loader's fetch has resolved, so _userInitiatedConvos(pid) reads
 * `[]` and the rail (agentLogCache alone supplies ~139 of mission_control's
 * 141 rows) renders 0 rows. It only recovers once loadAgentLog's ~1.6MB
 * refetch lands — instant on localhost, seconds over a phone tunnel.
 *
 * The fix (this session) removed the delete-then-sync-render: the loaders
 * already fetch unconditionally and only replace + repaint the cache once
 * the response lands, so there is no window where the rail can render off
 * an emptied cache, and a failed fetch now leaves the previous list
 * standing (and logs a console.warn) instead of blanking it.
 *
 * WHY THIS DRIVES THE REAL APP, NOT A HAND-INVOKED RENDER PATH
 * --------------------------------------------------------------
 * This dispatches a REAL session against the REAL running server
 * (localhost:5199) via window.dispatchAgent — real POST /agent/dispatch,
 * real claude process, real EventSource (window.connectAgentStream). It
 * then ends that real session by POSTing straight to
 * /api/project/<id>/agent/stop — deliberately NOT window.stopAgent(),
 * which closes THIS tab's own EventSource before the network round-trip
 * (conversation.js ~4321-4325) and would starve the very SSE handler under
 * test of the real server-pushed terminal event. Left alone, the tab's
 * EventSource stays open, the server's ~0.3s SSE poll (agent_routes.py
 * generate()) notices the real status flip, and pushes a REAL
 * `{type:'status', status:'stopped'}` message down the REAL wire into the
 * REAL onmessage handler being tested. No synthetic SSE payload, no
 * render function called by hand.
 *
 * A 10ms in-page sampler watches the real DOM (.agent-rail-list .conv-row)
 * for the whole window, so a flash as brief as the hivemind's measured
 * ~147ms recovery can't hide between two Playwright round-trips.
 *
 * RUN: node tools/smoke/rail-no-empty-flash.mjs   (needs MC on localhost:5199,
 *      mission_control project with a populated rail, Claude signed in)
 *
 * Side effect: leaves one tiny stopped test conversation in mission_control
 * (task text is tagged "[smoke test artifact ... safe to delete]").
 */
import { chromium } from 'playwright';

const PID = 'mission_control';
const BASE = 'http://localhost:5199';

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 20000 });

  await page.evaluate((pid) => openProjectModal(pid), PID);
  await page.waitForSelector('.agent-rail-list .conv-row', { timeout: 20000 });

  const baseline = await page.evaluate(() => document.querySelectorAll('.agent-rail-list .conv-row').length);
  baseline >= 20
    ? ok(`baseline rail: ${baseline} rows rendered from the real conversationsCache/agentLogCache merge`)
    : fail(`baseline rail only has ${baseline} rows — too few to prove a 0-row flash on a populated project`);

  // Open a fresh dispatch composer and send a real, cheap, trivial task —
  // a REAL dispatch through the REAL POST /agent/dispatch, a REAL claude
  // process, and a REAL SSE connection.
  await page.evaluate((pid) => window.newAgentTab(pid), PID);
  await page.waitForSelector(`#agent-task-${PID}`, { timeout: 5000 });
  await page.fill(`#agent-task-${PID}`,
    'Reply with exactly the word OK and nothing else. Do not use any tools. ' +
    '[smoke test artifact: tools/smoke/rail-no-empty-flash.mjs — safe to delete]');

  // Sub-100ms sampler running INSIDE the page — a Playwright round-trip
  // (tens of ms) could straddle and miss the measured ~147ms 0-row window.
  await page.evaluate(() => {
    window.__rowSamples = [];
    window.__rowTimer = setInterval(() => {
      window.__rowSamples.push(document.querySelectorAll('.agent-rail-list .conv-row').length);
    }, 10);
  });

  // dispatchAgent() calls window.connectAgentStream() SYNCHRONOUSLY right
  // after the dispatch POST resolves (resume-preview.js), so
  // agentEventSources[sid] is already populated by the time the
  // dispatchAgent() promise itself resolves. Firing the stop in the SAME
  // page.evaluate, immediately after that await, closes the race against a
  // fast real reply racing turn_complete closed the SSE first (a trivial
  // one-word prompt can complete in well under a second) — this way the
  // stop request is in flight before the model has had time to respond.
  const dispatch = await page.evaluate(async (pid) => {
    await window.dispatchAgent(pid);
    const sid = activeAgentTab[pid];
    const esOpenAtDispatch = !!agentEventSources[sid];
    if (sid) {
      // Deliberately NOT window.stopAgent() — it closes THIS tab's own
      // EventSource before the network round-trip (conversation.js
      // ~4321-4325), which would starve the SSE handler under test of the
      // real server-pushed terminal event.
      fetch(`/api/project/${pid}/agent/stop`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sid }),
      }).catch(() => {});
    }
    return { sid: sid || null, esOpenAtDispatch };
  }, PID);
  const sid = dispatch.sid;
  sid
    ? ok(`real session dispatched (EventSource open at stop-fire: ${dispatch.esOpenAtDispatch}): ${sid}`)
    : fail('dispatch never produced a real session — cannot exercise the SSE handler under test');
  if (!sid) throw new Error('no live session to drive the test with');

  // Confirm the real onmessage handler actually processed a real terminal
  // status event (proves the code path under test ran at all, not just
  // that nothing happened).
  let handled = false;
  for (let i = 0; i < 100 && !handled; i++) {
    handled = await page.evaluate((s) => {
      const h = agentHistory.find((x) => x.sessionId === s);
      return !!h && h.status === 'stopped';
    }, sid);
    if (!handled) await page.waitForTimeout(100);
  }
  handled
    ? ok('real terminal `status:\'stopped\'` SSE event received and processed by the onmessage handler')
    : fail('never observed the handler process a real terminal status — test did not exercise the fixed code path');

  await page.waitForTimeout(500);  // settle: let any refetch/repaint finish

  const { samples, min } = await page.evaluate(() => {
    clearInterval(window.__rowTimer);
    const s = window.__rowSamples || [];
    return { samples: s, min: s.length ? Math.min(...s) : -1 };
  });

  console.log(`  samples: ${samples.length}, min row count observed: ${min}`);
  min > 0
    ? ok(`row count NEVER hit zero across ${samples.length} samples (min=${min}) while the session ended`)
    : fail(`row count hit ${min} at some point while the terminal status was handled — the 0-row flash (f_6506aeb9) reproduced`);

  const finalCount = await page.evaluate(() => document.querySelectorAll('.agent-rail-list .conv-row').length);
  finalCount >= baseline
    ? ok(`rail settled at ${finalCount} rows (baseline was ${baseline})`)
    : fail(`rail settled at only ${finalCount} rows vs baseline ${baseline}`);

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0 ? '\n✅ PASS' : `\n❌ FAIL (${bad})`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}

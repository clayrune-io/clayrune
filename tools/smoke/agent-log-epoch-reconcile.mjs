#!/usr/bin/env node
// MC backlog 7aeb4922: a session evicted by the guardian, then revived by a
// follow-up message, replaces log_lines under the SAME session_id — history
// + revive markers + the new turn, which is very often LONGER than the
// client's cursor (eviction/exited-with-code-1 lines get dropped, several
// turns of real reply get added). That length INCREASE means the shrink
// guard (_mergeShorterHistory / `serverLines.length < have`) never fires,
// so _reconcileAgentBuffer's "missing = serverLines.slice(have)" logic
// silently assumes serverLines[0..have-1] is unchanged and only appends the
// tail past `have` — dropping the user's own echoed message, which the
// rebuild placed INSIDE that (wrongly trusted) prefix range.
//
// Repro, mirroring agent-stream-dedupe.mjs's vm-harness approach for this
// same file: seed a buffer/cursor as if the client caught the eviction lines
// live over SSE, hand _reconcileAgentBuffer a stubbed /agent/status response
// shaped exactly like the real revival rebuild (agent_routes.py ~7094-7104),
// and assert the rendered buffer ends up with the server's true content —
// not a version that silently kept the stale eviction/exited lines and
// skipped the user's own message.
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(resolve(here, '..', '..', 'static', 'js', 'resume-preview.js'), 'utf8');

const SID = 'sess-revive-1';
const PID = 'proj-1';

// Original 30-line conversation, then the guardian appended 2 more lines live
// over SSE (index 31, 32 in 1-based server terms) — the client caught both,
// so its cursor sits at 32 and the DOM shows the eviction text.
const preEvictionLines = Array.from({ length: 30 }, (_, i) => `line-${i}`);
const shownBeforeRevive = [
  ...preEvictionLines,
  '[Guardian: idle 60 min - process evicted...]',
  '[exited with code 1]',
];

// The revival rebuild (agent_routes.py _revive_history_lines / seed_lines):
// same original 30 lines, then the two synthetic markers REPLACE the
// eviction/exited text at the same positions, then the new user echo, then
// the agent's reply lines. Longer than `shownBeforeRevive` (34 vs 32).
const rebuiltLines = [
  ...preEvictionLines,
  '[— restored from transcript; conversation continues below —]',
  '[Session revived from agent log — resuming claude_session=abc123]',
  '> Ron: Tobin has been working on this...',
  'Got it — picking up where Tobin left off.',
];

function makeContext() {
  const appended = [];
  const dom = { innerHTML: '', dataset: {} };
  const context = vm.createContext({
    window: {}, console, setTimeout, clearTimeout,
    document: { getElementById: () => dom },
    agentServerLines: {}, agentLogEpoch: {},
    agentOutputBuffers: {}, agentOutputTimestamps: {},
    agentStatusCache: {}, agentHistory: [],
    rolloverLoadedCounts: {},
    conversationsCache: {}, agentLogCache: {}, pendingResumeId: {},
    pendingResumeProvider: {}, pendingResumeMcSessionId: {}, allProjects: [],
    getProjectSessions: () => [], _getProviderCaps: () => ({}),
    esc: value => String(value ?? ''),
    API_BASE: '',
    updateHistoryStatus: () => {},
    updateAgentStatusUI: () => {},
    renderAgentQuestion: () => {},
    appendAgentLine: (sid, text) => appended.push(text),
    fetchFailFast: async () => ({
      ok: true,
      json: async () => ({
        sessions: [{
          session_id: SID,
          status: 'running',
          log_lines: rebuiltLines,
          log_line_ts: rebuiltLines.map(() => null),
          log_epoch: 1727654321.5,  // bumped by the revive path (was 0/absent)
        }],
      }),
    }),
  });
  vm.runInContext(source, context, { filename: 'resume-preview.js' });
  return { context, appended, dom };
}

async function run() {
  const { context, appended } = makeContext();

  // Seed state as if the client had been live-following the session up to
  // (and including) the eviction, same shape conversation.js/SSE leave it in.
  context.agentOutputBuffers[SID] = shownBeforeRevive.slice();
  context.agentOutputTimestamps[SID] = shownBeforeRevive.map(() => null);
  context.agentServerLines[SID] = shownBeforeRevive.length;  // 32
  context.agentLogEpoch[SID] = 0;  // no rebuild seen yet — matches a fresh page's default

  await context.window._reconcileAgentBuffer(PID, SID);

  const finalBuf = context.agentOutputBuffers[SID];
  const check = (cond, msg) => { if (!cond) throw new Error(msg); };

  check(Array.isArray(finalBuf), '_reconcileAgentBuffer did not touch the buffer at all');
  check(finalBuf.includes('> Ron: Tobin has been working on this...'),
    "Ron's own echoed message is missing from the reconciled buffer — the exact MC-7aeb4922 symptom");
  check(!finalBuf.includes('[Guardian: idle 60 min - process evicted...]'),
    'stale eviction line survived the revive rebuild — server truth was not adopted');
  check(finalBuf.join('\n') === rebuiltLines.join('\n'),
    `reconciled buffer diverges from server truth:\n${JSON.stringify(finalBuf)}`);
  check(context.agentServerLines[SID] === rebuiltLines.length,
    'cursor was not re-anchored to the rebuilt array length');
  check(context.agentLogEpoch[SID] === 1727654321.5, 'epoch was not recorded from the response');
  // _repaintAgentOutput clears the DOM node and replays appendAgentLine over
  // the full buffer — the fix path must go through it (not just the tail-only
  // append loop) so the DOM actually gets Ron's message painted.
  check(appended.includes('> Ron: Tobin has been working on this...'),
    'appendAgentLine was never called with the revived user echo — DOM stayed stale');

  console.log('PASS agent-log-epoch-reconcile: revive rebuild (grown, not shrunk) forces a full re-adopt instead of a stale tail-slice');
}

run().catch(e => { console.error('FAIL agent-log-epoch-reconcile:', e.message); process.exit(1); });

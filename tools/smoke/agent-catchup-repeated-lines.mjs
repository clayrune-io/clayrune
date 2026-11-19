#!/usr/bin/env node
// FE1 (docs/_review/2026-11-16_04_frontend.md): _readAgentBuffer's catch-up
// walked the lines the client missed with `for (const text of missing)` and
// located each line with `missing.indexOf(text)`. indexOf returns the FIRST copy,
// so the second and later copy of any repeated line pointed back at the first,
// the race guard (`agentServerLines >= have + _mi + 1`) was true and the line was
// skipped: not buffered, not drawn, cursor not advanced. The buffer ended short
// and the next catch-up appended the last lines of the server buffer twice.
//
// Same vm harness as agent-log-epoch-reconcile.mjs: no DOM, a stubbed
// /agent/status, the real resume-preview.js. This one feeds the APPEND branch
// (server longer than the cursor, same epoch) a slice that repeats lines.
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(resolve(here, '..', '..', 'static', 'js', 'resume-preview.js'), 'utf8')
  .replace("import { deferChatRefresh } from './chat-selection.js';", '');

const SID = 'sess-catchup-1';
const PID = 'proj-1';
const check = (cond, msg) => { if (!cond) throw new Error(msg); };

function makeContext(serverLines, serverTs) {
  const appended = [];
  const context = vm.createContext({
    window: {}, console, setTimeout, clearTimeout,
    deferChatRefresh: () => false,
    document: { getElementById: () => null },
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
    appendAgentLine: (sid, text, ts) => appended.push([text, ts]),
    fetchFailFast: async () => ({
      ok: true,
      json: async () => ({ sessions: [{ session_id: SID, status: 'running',
        log_lines: serverLines, log_line_ts: serverTs, log_epoch: 0 }] }),
    }),
  });
  vm.runInContext(source, context, { filename: 'resume-preview.js' });
  return { context, appended };
}

async function run() {
  // The client saw the first 10 lines, then lost its stream. In the minute it
  // was away the agent printed tool lines that repeat: X, Y, X, Y, X, Z, X.
  const seen = Array.from({ length: 10 }, (_, i) => `line-${i}`);
  const missed = ['[tool: Read a.py]', '[tool: Edit a.py]', '[tool: Read a.py]', '[tool: Edit a.py]',
    '[tool: Read a.py]', 'done', '[tool: Read a.py]'];
  const serverLines = [...seen, ...missed];
  const serverTs = serverLines.map((_, i) => `2026-11-17T10:00:${String(i).padStart(2, '0')}Z`);

  const { context, appended } = makeContext(serverLines, serverTs);
  context.agentOutputBuffers[SID] = seen.slice();
  context.agentOutputTimestamps[SID] = serverTs.slice(0, seen.length);
  context.agentServerLines[SID] = seen.length;
  context.agentLogEpoch[SID] = 0;

  await context.window._reconcileAgentBuffer(PID, SID);
  const buf = context.agentOutputBuffers[SID];
  check(buf.join('\n') === serverLines.join('\n'),
    `after the first catch-up the buffer differs from the server:\n  got      ${JSON.stringify(buf.slice(seen.length))}\n  expected ${JSON.stringify(missed)}`);
  check(context.agentServerLines[SID] === serverLines.length,
    `cursor ${context.agentServerLines[SID]} should be ${serverLines.length}`);
  check(appended.length === missed.length, `${appended.length} lines drawn, expected ${missed.length}`);
  // Each line is drawn with ITS OWN timestamp, not the first copy's.
  appended.forEach(([text, ts], i) => check(ts === serverTs[seen.length + i],
    `line ${i} (${text}) drawn with ts ${ts}, expected ${serverTs[seen.length + i]}`));

  // A second catch-up with nothing new must change nothing (the symptom was a
  // short cursor re-appending the tail).
  await context.window._reconcileAgentBuffer(PID, SID);
  check(context.agentOutputBuffers[SID].join('\n') === serverLines.join('\n'),
    'a second catch-up changed the buffer');
  check(appended.length === missed.length, 'a second catch-up drew lines again');

  console.log('PASS agent-catchup-repeated-lines: a catch-up over repeated lines buffers and draws every line once, with its own timestamp');
}

run().catch(e => { console.error('FAIL agent-catchup-repeated-lines:', e.message); process.exit(1); });

#!/usr/bin/env node
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(resolve(here, '..', '..', 'static', 'js', 'resume-preview.js'), 'utf8');
const context = vm.createContext({
  window: {}, console, setTimeout, clearTimeout,
  agentServerLines: {},
  conversationsCache: {}, agentLogCache: {}, pendingResumeId: {},
  pendingResumeProvider: {}, pendingResumeMcSessionId: {}, allProjects: [],
  getProjectSessions: () => [], _getProviderCaps: () => ({}),
  esc: value => String(value ?? ''),
});
vm.runInContext(source, context, { filename: 'resume-preview.js' });

const claim = context.window._claimAgentOutputEvent;
const advance = context.window._advanceAgentServerCursor;
const cursor = sid => context.agentServerLines[sid] || 0;
const setCursor = (sid, value) => { context.agentServerLines[sid] = Number(value); };
const check = (condition, message) => { if (!condition) throw new Error(message); };

// Reconciliation won the race and already rendered line 2.  Its delayed SSE
// event must be ignored rather than appended a second time.
setCursor('reconcile-first', 2);
let result = claim('reconcile-first', { type: 'output', text: 'same', line_index: 2 });
check(!result.accepted && !result.gap, 'late reconciled SSE event was not rejected');
check(cursor('reconcile-first') === 2, 'duplicate changed the authoritative cursor');

// SSE won the race.  Advancing to the exact server position means the later
// status reconciliation sees an in-sync cursor and appends nothing.
setCursor('sse-first', 1);
result = claim('sse-first', { type: 'output', text: 'same', line_index: 2 });
check(result.accepted && cursor('sse-first') === 2, 'ordered SSE event did not advance exactly');

// A status request started before that SSE may resolve afterward with a stale
// count. It must not rewind the cursor and make reconnect replay line 2.
advance('sse-first', 1);
check(cursor('sse-first') === 2, 'stale status response rewound the SSE cursor');

// Equal text at different positions is legitimate and must not be text-deduped.
result = claim('sse-first', { type: 'output', text: 'same', line_index: 3 });
check(result.accepted && cursor('sse-first') === 3, 'legitimate repeated text was rejected');

// A forward jump is withheld so reconciliation can recover the missing slice.
result = claim('gap', { type: 'output', text: 'later', line_index: 4 });
check(!result.accepted && result.gap && cursor('gap') === 0, 'forward gap skipped server history');

// Rolling upgrade compatibility: old servers omit line_index.
result = claim('legacy', { type: 'output', text: 'old server' });
check(result.accepted && cursor('legacy') === 1, 'unindexed legacy event was rejected');

console.log('PASS agent stream dedupe: indexed SSE and reconciliation cannot double-render a line');

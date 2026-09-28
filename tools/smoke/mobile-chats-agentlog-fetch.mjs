#!/usr/bin/env node
// Regression for Ron, 2026-09-28 (phone): "why do I now see only one Dave
// conversation?" Root cause: on mobile, whenever the project already has any
// tab history (sessions.length > 0 — true for almost any actively-used
// project), agentPanelHTML's `noActiveTab` gate is false, so the block that
// calls loadAgentLog() never runs. A second block (added earlier for the
// analogous loadConversations() gap) covers the Layer-2 list case but never
// called loadAgentLog() too — so the merge that rescues a conversation aged
// out of the /conversations top-20 window (_userInitiatedConvos's
// agentLogCache fallback) never happens on mobile. In a busy project the
// top-20 window churns fast enough that 2 of a persona's 3 chats can be
// missing at any given moment, with no fallback to recover them.
//
// This drives the exact source block (not a reimplementation) so a revert
// of the fix fails this test.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../../static/js/conversation.js', import.meta.url), 'utf8');
const start = source.indexOf('  // Mobile Layer-2 list is built from the durable conversations');
const end = source.indexOf('\n  }', source.indexOf('if (mobileMode && !activeSession && !wantNew', start)) + '\n  }'.length;
assert.ok(start >= 0 && end > start, 'could not locate the mobile Layer-2 load block in conversation.js');
const block = source.slice(start, end);
assert.ok(block.includes('loadAgentLog(p.id)'), 'block must call loadAgentLog');
assert.ok(block.includes('loadConversations(p.id)'), 'block must call loadConversations');

function runScenario({ conversationsCache, agentLogCache }) {
  const calls = [];
  const context = vm.createContext({
    mobileMode: true,
    activeSession: null,       // mobile never auto-selects (line ~928)
    wantNew: false,
    p: { id: 'mission_control' },
    conversationsCache,
    agentLogCache,
    loadConversations: (pid) => calls.push(['loadConversations', pid]),
    loadAgentLog: (pid) => calls.push(['loadAgentLog', pid]),
  });
  vm.runInContext(block, context);
  return calls;
}

// Ron's exact scenario: mobile, Layer-2 list showing (no active tab selected —
// by design, mobile never auto-selects), but the project already has tab
// history (sessions.length > 0 elsewhere in the function — irrelevant to this
// block, which doesn't read `sessions` at all). Neither cache is warm yet.
const calls = runScenario({ conversationsCache: {}, agentLogCache: {} });
assert.deepEqual(calls.sort(), [['loadAgentLog', 'mission_control'], ['loadConversations', 'mission_control']].sort(),
  'mobile Layer-2 list must fetch BOTH conversations and the agent log, so aged-out chats can be rescued by the merge');
console.log('PASS mobile Layer-2 list loads agent log alongside conversations when both caches are cold');

// Already-warm caches must not re-fetch.
const warmCalls = runScenario({ conversationsCache: { mission_control: [] }, agentLogCache: { mission_control: [] } });
assert.deepEqual(warmCalls, [], 'warm caches must not trigger a re-fetch');
console.log('PASS warm caches are left alone');

#!/usr/bin/env node
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../../static/js/conversation.js', import.meta.url), 'utf8');
const start = source.indexOf('function openChannelPerson(');
const end = source.indexOf('window.openChannelPerson = openChannelPerson;', start);
assert.ok(start >= 0 && end > start);
const events = [];
const context = vm.createContext({
  _channelExpanded: {}, conversationsCache: {},
  _convCharKey: row => row.key, _isNoiseConvoRow: row => !!row.noise,
  refreshModalById: pid => events.push(['refresh', pid]),
  openConversation: (...args) => events.push(['open', ...args]),
});
vm.runInContext(source.slice(start, end), context);
context.conversationsCache.p = [
  { key: 'vector', mtime: 30, provider: 'codex', provider_session_id: 'external' },
  { key: 'other', mtime: 40, mc_session_id: 'other' },
  { key: 'vector', mtime: 25, mc_session_id: 'noise', noise: true },
  { key: 'vector', mtime: 20, mc_session_id: 'saved-vector', live: false },
];
context.openChannelPerson('p', 'vector');
assert.equal(context._channelExpanded.p, 'vector');
assert.deepEqual(events, [['refresh', 'p'], ['open', 'p', '', 'saved-vector', false]]);

// External-only and empty rosters must still expand, without a no-op open.
events.length = 0;
context.conversationsCache.p = [{ key: 'vector', provider_session_id: 'external' }];
context.openChannelPerson('p', 'vector');
assert.deepEqual(events, [['refresh', 'p']]);
events.length = 0;
context.conversationsCache.p = [];
context.openChannelPerson('p', 'vector');
assert.deepEqual(events, [['refresh', 'p']]);

// Existing Claude transcript-only shortcuts remain supported.
events.length = 0;
context.conversationsCache.p = [{ key: 'vector', claude_session_id: 'native' }];
context.openChannelPerson('p', 'vector');
assert.deepEqual(events, [['refresh', 'p'], ['open', 'p', 'native', '', false]]);
console.log('PASS roster expansion and newest openable conversation selection');

// Clicking an external row must hand its native id to a read-only viewer,
// without touching the MC cache, reconstruct route, or resume controls.
const openStart = source.indexOf('async function openConversation(');
const openEnd = source.indexOf('window.openConversation = openConversation;', openStart);
const viewed = [];
const viewerContext = vm.createContext({
  conversationsCache: { p: [{ provider: 'codex', provider_session_id: 'native', label: 'External chat' }] },
  openTranscriptViewer: async (...args) => viewed.push(args),
});
vm.runInContext(source.slice(openStart, openEnd), viewerContext);
await viewerContext.openConversation('p', 'native', '', false);
assert.deepEqual(viewed, [['p', 'native', 'External chat', 'codex']]);
assert.ok(source.includes("!c.mc_session_id && c.provider === 'codex' ? c.provider_session_id"));
console.log('PASS external Codex row opens native transcript read-only');

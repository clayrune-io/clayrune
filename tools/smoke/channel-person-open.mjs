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
  _userInitiatedConvos: pid => context.conversationsCache[pid] || [],
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

// External history opens the normal pane with native identity and full text.
const openStart = source.indexOf('async function openConversation(');
const openEnd = source.indexOf('window.openConversation = openConversation;', openStart);
const viewed = [];
const viewerContext = vm.createContext({
  conversationsCache: { p: [{ provider: 'codex', provider_session_id: 'native', label: 'External chat' }] },
  agentStatusCache: {}, agentOutputBuffers: {}, agentServerLines: {}, agentHistory: [], API_BASE: '',
  fetch: async () => ({ok: true, json: async () => ({messages: [
    {role: 'user', text: 'original question'}, {role: 'assistant', text: 'original answer'}]})}),
  switchAgentTab: (...args) => viewed.push(args),
  showToast: message => { throw new Error(message); },
});
vm.runInContext(source.slice(openStart, openEnd), viewerContext);
await viewerContext.openConversation('p', 'native', '', false);
assert.deepEqual(viewed, [['p', 'codex:p:native']]);
assert.equal(viewerContext.agentStatusCache['codex:p:native'].providerSessionId, 'native');
assert.equal(viewerContext.agentStatusCache['codex:p:native']._nativeHistory, true);
assert.equal(viewerContext.agentOutputBuffers['codex:p:native'][1], 'original answer');
assert.ok(source.includes("!c.mc_session_id && c.provider === 'codex' ? c.provider_session_id"));
console.log('PASS external Codex history opens normal conversation pane');

// The second, slower history source must be consumed after it arrives.
// Run the actual merge function: no older row at first, then a Dave chat
// arrives through agentLogCache after the recent-conversations render.
const mergeStart = source.indexOf('function _userInitiatedConvos(');
const mergeEnd = source.indexOf('window._userInitiatedConvos = _userInitiatedConvos;', mergeStart);
const delayed = [];
const historyContext = vm.createContext({
  _channelExpanded: {}, _showHiddenConvos: {}, conversationsCache: {p: []}, agentLogCache: {p: []},
  _hiddenConvSet: () => new Set(), _isStewardConvo: () => false,
  _convHideKey: c => c.claude_session_id, _isNoiseConvoRow: () => false,
  _getProviderCaps: () => ({}), _convCharKey: c => c.character?.name || '',
  refreshModalById: () => {}, openConversation: (...args) => delayed.push(args),
});
vm.runInContext(source.slice(mergeStart, mergeEnd), historyContext);
vm.runInContext(source.slice(start, end), historyContext);
historyContext.openChannelPerson('p', 'dave');
assert.equal(delayed.length, 0);
historyContext.agentLogCache.p = [{session_id: 'old-mc', claude_session_id: 'old-native',
  character: {name: 'dave'}, ts: '2026-09-10T12:00:00Z', task: 'older conversation'}];
historyContext.openChannelPerson('p', 'dave');
assert.deepEqual(delayed, [['p', 'old-native', 'old-mc', false]]);
assert.ok(source.includes('for (const c of _userInitiatedConvos(projectId, true))'));
assert.ok(source.includes('const _all = _userInitiatedConvos(p.id, true)'));
console.log('PASS older persona conversations appear when delayed run history arrives');

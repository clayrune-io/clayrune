#!/usr/bin/env node
// Provider-neutral conversation cache regression.
// Two live Codex chats in one project must be distinct, newest-first, and
// remain addressable after the runtime thread id arrives or the user navigates.
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(resolve(here, '..', '..', 'static', 'js', 'agent-log.js'), 'utf8');
const previewSource = readFileSync(resolve(here, '..', '..', 'static', 'js', 'resume-preview.js'), 'utf8');
const context = vm.createContext({
  window: {}, console, Date,
  conversationsCache: {}, agentLogCache: {}, agentStatusCache: {},
  agentOutputBuffers: {}, agentPendingImages: {},
});
vm.runInContext(source, context, { filename: 'agent-log.js' });

const upsert = (csid, text, status, meta) => {
  context.__args = ['project', csid, text, status, meta];
  vm.runInContext('upsertConversationCache(...__args)', context);
};
const rows = () => context.conversationsCache.project;
const fail = (message) => { throw new Error(message); };

upsert('', 'first Codex chat', 'running', {
  mcSessionId: 'mc-1', provider: 'codex', live: true,
});
upsert('', 'second Codex chat', 'running', {
  mcSessionId: 'mc-2', provider: 'codex', live: true,
});
if (rows().length !== 2) fail(`parallel chats collapsed: ${JSON.stringify(rows())}`);
if (rows()[0].mc_session_id !== 'mc-2') fail('newest Codex chat is not first');

upsert('', '', 'running', {
  mcSessionId: 'mc-1', providerSessionId: 'thread-1',
  provider: 'codex', live: true, touch: false,
});
if (rows().length !== 2) fail('thread-id enrichment duplicated the first chat');
if (rows().find(r => r.mc_session_id === 'mc-1').provider_session_id !== 'thread-1')
  fail('provider thread id did not enrich the existing MC-session row');
if (rows()[0].mc_session_id !== 'mc-2') fail('status reconciliation reordered the rail');

upsert('', 'follow-up in first', 'running', {
  mcSessionId: 'mc-1', providerSessionId: 'thread-1',
  provider: 'codex', live: true,
});
if (rows().length !== 2) fail('follow-up duplicated a Codex conversation');
if (rows()[0].mc_session_id !== 'mc-1') fail('most recently active chat did not move first');

upsert('claude-thread', 'Claude chat', 'running', { provider: 'claude' });
if (!rows().some(r => r.claude_session_id === 'claude-thread'))
  fail('legacy Claude transcript-id upsert regressed');
upsert('', '', 'running', {
  mcSessionId: 'mc-2', providerSessionId: 'thread-2',
  provider: 'codex', live: true, touch: false,
});

// The New/Resume picker must preserve the owning provider and MC session id;
// otherwise selecting a Codex row silently dispatches it as a Claude resume
// and its preview has no durable route back to the stored log.
const previewContext = vm.createContext({
  window: {}, console, setTimeout, clearTimeout,
  conversationsCache: { project: rows() }, agentLogCache: {},
  pendingResumeId: {}, pendingResumeProvider: {}, pendingResumeMcSessionId: {},
  allProjects: [{ id: 'project', pinned_conversations: [] }],
  getProjectSessions: () => [],
  _getProviderCaps: () => ({ supports_session_resume: true }),
  esc: value => String(value ?? ''),
});
vm.runInContext(previewSource, previewContext, { filename: 'resume-preview.js' });
const picker = vm.runInContext("sessionPickerHTML('project')", previewContext);
if (!picker.includes("selectResumeSession('project','thread-1','codex','mc-1')"))
  fail(`Codex resume picker lost provider identity: ${picker}`);
if (!picker.includes('second Codex chat') || !picker.includes('follow-up in first'))
  fail('parallel Codex chats are missing from the resume picker');

console.log('PASS provider-neutral rail cache: parallel Codex chats stay distinct, newest-first, durable, and resumable through the correct provider.');

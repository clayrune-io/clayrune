#!/usr/bin/env node
// A Codex-first install must check and prompt for Codex auth without touching
// Claude's auth endpoints. Runs the real provider-auth.js in a tiny DOM shell.
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(resolve(here, '..', '..', 'static', 'js', 'provider-auth.js'), 'utf8');
const calls = [];
const classes = new Set(['hidden']);
const elements = {
  'auth-banner': { classList: { add: c => classes.add(c), remove: c => classes.delete(c) } },
  'auth-banner-text': { textContent: '' },
  'auth-banner-signin': { textContent: '', onclick: null },
  'auth-banner-recheck': { disabled: false, textContent: 'Re-check' },
};
const response = (body) => ({ ok: true, json: async () => body });
const context = vm.createContext({
  window: {}, console, API_BASE: '',
  _globalConfig: { default_provider: 'codex' },
  // Simulate the real first-run race: config already reflects the user's
  // Codex click while the cached provider list still marks Claude as default.
  _agentProviders: [
    { name: 'claude', display_name: 'Claude Code', installed: true, in_use: true, default: true },
    { name: 'codex', display_name: 'Codex CLI', installed: true, in_use: false, default: false },
  ],
  _ensureAgentProviders: async () => context._agentProviders,
  document: { getElementById: id => elements[id] || null },
  fetchFailFast: async (url) => {
    calls.push(url);
    return response({ ok: false, status: 'not_logged_in' });
  },
  fetch: async (url) => { calls.push(url); return response({}); },
  alert: () => {}, showToast: () => {}, setTimeout, clearTimeout,
});
vm.runInContext(source, context, { filename: 'provider-auth.js' });
await context.window.refreshAuthStatus();

const fail = (message) => { throw new Error(message); };
if (!calls.includes('/api/agent/codex/auth-status'))
  fail(`selected provider was not checked: ${JSON.stringify(calls)}`);
if (calls.some(u => u.includes('/claude/')))
  fail(`Codex-first boot touched Claude auth: ${JSON.stringify(calls)}`);
if (classes.has('hidden')) fail('missing Codex login did not surface the banner');
if (!elements['auth-banner-text'].textContent.includes('Codex CLI'))
  fail(`banner names the wrong provider: ${elements['auth-banner-text'].textContent}`);
if (elements['auth-banner-signin'].textContent !== 'Authenticate Codex CLI')
  fail(`CTA names the wrong provider: ${elements['auth-banner-signin'].textContent}`);
if (!context.window.providerAuthKnownBad('codex'))
  fail('Codex missing-login state was not exposed to the dispatch gate');
if (context.window.providerAuthKnownBad('claude'))
  fail('unused Claude was incorrectly marked as blocking');

console.log('PASS provider-neutral auth: Codex-first boot prompts for Codex and makes no Claude auth request.');

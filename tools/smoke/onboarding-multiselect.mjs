import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../../static/js/walkthrough.js', import.meta.url), 'utf8');
const calls = [];
let installReply = {ok: true};
const validation = {textContent: ''};
const context = vm.createContext({
  window: {addEventListener() {}},
  document: {getElementById: () => validation, addEventListener() {}},
  _globalConfig: {}, _agentProviders: [
    {name: 'codex', display_name: 'Codex', installed: false},
    {name: 'claude', display_name: 'Claude', installed: false},
  ],
  esc: String, API_BASE: '',
  fetch: async (url, init) => { calls.push({url, body: init && init.body ? JSON.parse(init.body) : null}); return {json: async () => installReply}; },
  saveSetting: async (key, value) => { context._globalConfig[key] = value; },
  _ensureAgentProviders: async () => {},
});
vm.runInContext(source + '\nglobalThis.steps = WT_STEPS;', context);
context.wtSelectProvider('codex', true);
context.wtSelectProvider('claude', true);
const html = context.steps[1].body();
assert.equal((html.match(/type="checkbox"/g) || []).length, 2);
assert.equal((html.match(/name="wt-provider-default"/g) || []).length, 2);
// F7: "Install selected" is ONE batch request naming every selected,
// uninstalled vendor - not one single-vendor request (and terminal) per vendor.
await context.wtInstallSelectedProviders();
assert.deepEqual(calls, [{url: '/api/agent/providers/install-launch', body: {names: ['codex', 'claude']}}]);
// F6: when the server changed the PowerShell script policy, the install message
// says so on every installed vendor's row instead of changing the machine silently.
const msgs = {};
context.document.getElementById = id => (msgs[id] ??= {textContent: ''});
installReply = {ok: true, installed: ['codex', 'claude'], unsupported: [],
  execution_policy: {action: 'set', effective: 'Restricted', message: 'POLICY-NOTE'}};
await context.wtInstallSelectedProviders();
for (const n of ['codex', 'claude']) {
  assert.match(msgs[`wt-install-msg-${n}`].textContent, /A terminal opened to install it/);
  assert.match(msgs[`wt-install-msg-${n}`].textContent, /POLICY-NOTE/);
}
installReply = {ok: true, installed: ['codex', 'claude'], execution_policy: {action: 'unchanged', message: ''}};
await context.wtInstallSelectedProviders();
assert.doesNotMatch(msgs['wt-install-msg-codex'].textContent, /POLICY-NOTE/);
context.document.getElementById = () => validation;
vm.runInContext('wtStep = 1;', context);
context.wtNext();
assert.match(validation.textContent, /Choose a default/);
// F2: the gate names each unfinished vendor and its exact problem.
assert.match(validation.textContent, /Codex: not installed/);
assert.match(validation.textContent, /Claude: not installed/);
context._globalConfig.default_provider = 'codex';
context.wtNext();
assert.doesNotMatch(validation.textContent, /Choose a default/);
assert.match(validation.textContent, /Codex: not installed/);
assert.equal(context.steps[1].skip(), false, 'saving default must not skip unfinished second vendor');
// Installed but not signed in is a different, named reason.
context._agentProviders.forEach(p => { p.installed = true; p.auth_status = 'not_logged_in'; });
context.wtNext();
assert.match(validation.textContent, /Codex: not signed in/);
assert.match(validation.textContent, /Claude: not signed in/);
// Deselecting the default vendor must re-demand a default.
context.wtSelectProvider('codex', false);
context.wtNext();
assert.match(validation.textContent, /Choose a default/);
// F4: on a FRESH tour the step is skipped only when the saved default is
// installed AND signed in; a default that was merely saved (this step's own
// save writes it) must not hide the only install offer.
function freshSkip(provider) {
  const c = vm.createContext({window: {addEventListener() {}}, document: {getElementById: () => validation, addEventListener() {}},
    _globalConfig: {default_provider: 'claude'}, _agentProviders: [provider], esc: String, API_BASE: ''});
  vm.runInContext(source + '\nglobalThis.steps = WT_STEPS;', c);
  return c.steps[1].skip();
}
assert.equal(freshSkip({name: 'claude', installed: false, auth_status: 'unknown'}), false);
assert.equal(freshSkip({name: 'claude', installed: true, auth_status: 'not_logged_in'}), false);
assert.equal(freshSkip({name: 'claude', installed: true, auth_status: 'ok'}), true);
console.log('PASS multi-select, batch install (F7), policy note (F6), named gate reasons (F2), explicit default gate, fresh-tour skip rule (F4) and unfinished setup retention');

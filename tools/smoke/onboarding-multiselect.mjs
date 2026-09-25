import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

// First-run setup owns the provider step now (first-run.js); the install batch
// and the shared row live in provider-auth.js. Both are plain scripts loaded
// into one context, the same way the page loads them.
const source = ['first-run.js', 'provider-auth.js']
  .map(f => readFileSync(new URL('../../static/js/' + f, import.meta.url), 'utf8')).join('\n');
const calls = [];
let installReply = {ok: true};
const validation = {textContent: ''};
const policyNoteEl = {textContent: ''};
// Point 2 (clean-VM run 2026-09-24): while an install terminal launched from
// setup is running, first-run.js must poll provider status on its own
// (_setupStartInstallWatch/_setupStopInstallWatch) instead of leaving the
// user to hunt for "Check setup status". Track the real setInterval/
// clearInterval + classList calls instead of no-oping them.
let intervalStarts = 0, intervalClears = 0, bodyAdds = 0, bodyRemoves = 0, tickFn = null;
const context = vm.createContext({
  window: {addEventListener() {}},
  document: {
    getElementById: () => validation, addEventListener() {},
    // .prov-install-policy-note is the ONE shared batch note (F6, 2026-09-24:
    // used to repeat once per selected-vendor row); terminal-dock lookup
    // (setup-terminal-live) matches nothing here, same as a real DOM with no
    // terminal open.
    querySelectorAll: (sel) => sel === '.prov-install-policy-note' ? [policyNoteEl] : [],
    body: {classList: {
      add: (c) => { if (c === 'setup-terminal-live') bodyAdds++; },
      remove: (c) => { if (c === 'setup-terminal-live') bodyRemoves++; },
    }},
  },
  _globalConfig: {}, _agentProviders: [
    {name: 'codex', display_name: 'Codex', installed: false},
    {name: 'claude', display_name: 'Claude', installed: false},
  ],
  // STORE vars (real index.html declares these; provider-auth.js/first-run.js
  // read+write the bare names) — seeded here the same way _agentProviders is.
  _providerInstallMsg: {}, _providerInstallPolicyNoteText: '', _providerInstallStatusUrl: '',
  _providerInstallProgress: {},
  MutationObserver: class { observe() {} disconnect() {} },
  esc: String, API_BASE: '',
  fetch: async (url, init) => { calls.push({url, body: init && init.body ? JSON.parse(init.body) : null}); return {json: async () => installReply}; },
  saveSetting: async (key, value) => { context._globalConfig[key] = value; },
  _ensureAgentProviders: async () => {},
  setInterval: (fn) => { intervalStarts++; tickFn = fn; return 42; },
  clearInterval: () => { intervalClears++; },
});
vm.runInContext(source + '\nglobalThis.steps = SETUP_STEPS;', context);
context.setupSelectProvider('codex', true);
context.setupSelectProvider('claude', true);
const html = context.steps[1].body();
assert.equal((html.match(/type="checkbox"/g) || []).length, 2);
assert.equal((html.match(/name="setup-provider-default"/g) || []).length, 2);
// F7: "Install selected" is ONE batch request naming every selected,
// uninstalled vendor - not one single-vendor request (and terminal) per vendor.
await context.setupInstallSelected();
assert.deepEqual(calls, [{url: '/api/agent/providers/install-launch', body: {names: ['codex', 'claude']}}]);
// F6, revised 2026-09-24 (clean-VM run: the note repeated once per selected
// vendor row, 4x with everything ticked): when the server changed the
// PowerShell script policy, the note is shown ONCE for the batch — never
// duplicated onto every installed vendor's per-row message.
const msgs = {};
context.document.getElementById = id => (msgs[id] ??= {textContent: ''});
installReply = {ok: true, installed: ['codex', 'claude'], unsupported: [],
  execution_policy: {action: 'set', effective: 'Restricted', message: 'POLICY-NOTE'}};
await context.setupInstallSelected();
assert.equal(policyNoteEl.textContent, 'POLICY-NOTE');
for (const n of ['codex', 'claude']) {
  assert.match(msgs[`prov-install-msg-${n}`].textContent, /A terminal opened to install it/);
  assert.doesNotMatch(msgs[`prov-install-msg-${n}`].textContent, /POLICY-NOTE/);
}
installReply = {ok: true, installed: ['codex', 'claude'], execution_policy: {action: 'unchanged', message: ''}};
await context.setupInstallSelected();
assert.equal(policyNoteEl.textContent, 'POLICY-NOTE'); // unchanged: an empty note this time leaves the last one shown, never blanked
assert.doesNotMatch(msgs['prov-install-msg-codex'].textContent, /POLICY-NOTE/);
// Point 2: three "Install selected" clicks so far must have started the
// polling watch exactly ONCE (it guards against re-arming while already
// running), and never stopped it while codex/claude are still uninstalled.
assert.equal(intervalStarts, 1, `install watch started ${intervalStarts} times, want exactly 1 across repeated Install-selected clicks`);
assert.ok(bodyAdds >= 1, 'setup-terminal-live class was never added while an install terminal is live');
assert.equal(intervalClears, 0, 'install watch stopped before every selected vendor finished installing');
assert.ok(tickFn, 'setInterval callback was not captured');
// Simulate the poll noticing both selected vendors are now installed+signed
// in (the same condition "Check setup status" would show) — the watch must
// stop itself, not require the user to close setup.
context._agentProviders.forEach((p) => { p.installed = true; p.auth_status = 'ok'; });
await tickFn();
assert.equal(intervalClears, 1, 'install watch did not stop once every selected vendor finished installing');
assert.equal(bodyRemoves, 1, 'setup-terminal-live class was not removed once the install watch stopped');
context._agentProviders.forEach((p) => { p.installed = false; p.auth_status = undefined; }); // restore for the gate assertions below
context.document.getElementById = () => validation;
vm.runInContext('setupStep = 1;', context);
context.setupNext();
assert.match(validation.textContent, /Choose a default/);
// F2: the gate names each unfinished vendor and its exact problem.
assert.match(validation.textContent, /Codex: not installed/);
assert.match(validation.textContent, /Claude: not installed/);
context._globalConfig.default_provider = 'codex';
context.setupNext();
assert.doesNotMatch(validation.textContent, /Choose a default/);
assert.match(validation.textContent, /Codex: not installed/);
assert.equal(context.steps[1].skip(), false, 'saving default must not skip unfinished second vendor');
// Installed but not signed in is a different, named reason.
context._agentProviders.forEach(p => { p.installed = true; p.auth_status = 'not_logged_in'; });
context.setupNext();
assert.match(validation.textContent, /Codex: not signed in/);
assert.match(validation.textContent, /Claude: not signed in/);
// Deselecting the default vendor must re-demand a default.
context.setupSelectProvider('codex', false);
context.setupNext();
assert.match(validation.textContent, /Choose a default/);
// F4: on a FRESH tour the step is skipped only when the saved default is
// installed AND signed in; a default that was merely saved (this step's own
// save writes it) must not hide the only install offer.
function freshSkip(provider) {
  const c = vm.createContext({window: {addEventListener() {}}, document: {getElementById: () => validation, addEventListener() {}},
    _globalConfig: {default_provider: 'claude'}, _agentProviders: [provider], esc: String, API_BASE: ''});
  vm.runInContext(source + '\nglobalThis.steps = SETUP_STEPS;', c);
  return c.steps[1].skip();
}
assert.equal(freshSkip({name: 'claude', installed: false, auth_status: 'unknown'}), false);
assert.equal(freshSkip({name: 'claude', installed: true, auth_status: 'not_logged_in'}), false);
assert.equal(freshSkip({name: 'claude', installed: true, auth_status: 'ok'}), true);
console.log('PASS multi-select, batch install (F7), policy note (F6), named gate reasons (F2), explicit default gate, fresh-tour skip rule (F4) and unfinished setup retention');

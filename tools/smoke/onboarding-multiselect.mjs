import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../../static/js/walkthrough.js', import.meta.url), 'utf8');
const calls = [];
const validation = {textContent: ''};
const context = vm.createContext({
  window: {addEventListener() {}},
  document: {getElementById: () => validation, addEventListener() {}},
  _globalConfig: {}, _agentProviders: [
    {name: 'codex', display_name: 'Codex', installed: false},
    {name: 'claude', display_name: 'Claude', installed: false},
  ],
  esc: String, API_BASE: '',
  fetch: async url => { calls.push(url); return {json: async () => ({ok: true})}; },
  saveSetting: async (key, value) => { context._globalConfig[key] = value; },
  _ensureAgentProviders: async () => {},
});
vm.runInContext(source + '\nglobalThis.steps = WT_STEPS;', context);
context.wtSelectProvider('codex', true);
context.wtSelectProvider('claude', true);
const html = context.steps[1].body();
assert.equal((html.match(/type="checkbox"/g) || []).length, 2);
assert.equal((html.match(/name="wt-provider-default"/g) || []).length, 2);
await context.wtInstallSelectedProviders();
assert.deepEqual(calls, ['/api/agent/provider/codex/install-launch', '/api/agent/provider/claude/install-launch']);
vm.runInContext('wtStep = 1;', context);
context.wtNext();
assert.match(validation.textContent, /Choose a default/);
context._globalConfig.default_provider = 'codex';
assert.equal(context.steps[1].skip(), false, 'saving default must not skip unfinished second vendor');
context.wtSelectProvider('codex', false);
context.wtNext();
assert.match(validation.textContent, /Choose a default/);
console.log('PASS multi-select, all selected installs, explicit default gate and unfinished setup retention');

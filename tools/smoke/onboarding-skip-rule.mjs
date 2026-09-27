import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

// Replaces tools/smoke/onboarding-multiselect.mjs (retired MC-974/MC-982,
// 2026-09-26). That file's shared-state flow tested first-run.js behavior
// that no longer exists: setupNext() used to gate the connections step on
// "choose a default" / "not signed in" and write those reasons into a
// validation div; Ron removed that gate entirely on 2026-09-24 (189e3888,
// "vendor sign-in never blocks setup; Next warns instead") — the literal
// string "Choose a default" no longer appears anywhere in first-run.js, so
// those assertions were failing against dead code, not a real regression.
// The rest of that file's coverage (F7 batch install request, F6 shared
// policy note, named warning reasons, the non-blocking Next rule) is already
// exercised against a real DOM by onboarding-multiselect-browser.mjs, and the
// install-terminal-live visibility class it also tracked is now driven by a
// live MutationObserver that a vm-fake `document` cannot fire at all (also
// covered in the browser smoke). The one piece of logic that file checked
// which is NOT flow-dependent and NOT covered elsewhere is this file: whether
// the connections step skips itself on a fresh tour (F4, steps[1].skip()).
const source = ['first-run.js', 'provider-auth.js']
  .map(f => readFileSync(new URL('../../static/js/' + f, import.meta.url), 'utf8')).join('\n');
const validation = {textContent: ''};

// F4: on a FRESH tour the connections step is skipped only when the saved
// default is BOTH installed and signed in — a default that was merely saved
// (the step's own save writes it) must not hide the only install offer
// (clean-VM run, 2026-09-18: a default picked but never installed was
// skipped past on reload, leaving the user stuck with no working agent).
function freshSkip(provider) {
  const c = vm.createContext({
    window: {addEventListener() {}},
    document: {getElementById: () => validation, addEventListener() {}},
    _globalConfig: {default_provider: 'claude'}, _agentProviders: [provider],
    esc: String, API_BASE: '',
  });
  vm.runInContext(source + '\nglobalThis.steps = SETUP_STEPS;', c);
  return c.steps[1].skip();
}
assert.equal(freshSkip({name: 'claude', installed: false, auth_status: 'unknown'}), false,
  'a saved-but-not-installed default must not skip the only install offer');
assert.equal(freshSkip({name: 'claude', installed: true, auth_status: 'not_logged_in'}), false,
  'installed but not signed in must still show the step');
assert.equal(freshSkip({name: 'claude', installed: true, auth_status: 'ok'}), true,
  'installed and signed in is the only case that skips');
console.log('PASS connections-step fresh-tour skip rule (F4)');

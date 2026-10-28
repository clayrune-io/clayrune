// Package Result after a GitHub post-Save check: the title says what the check found, never "Registered".
import assert from 'node:assert/strict';
import {packageResultHTML, packageResultTitle} from '../../static/js/desk-v1-connect-package-result.js';
globalThis.window = {};
await import('../../static/js/desk-v1-connect-copy.js');
const words = window.DeskV1ConnectCopy.words;
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const working = {state:'registered', message:'Working: 10 tools.', check:{status:'working', tools:10}};
const failing = {state:'registered', message:'Not working: the server stopped and said: Error: at least one YouTube API key is required.', check:{status:'failed'}};
assert.equal(packageResultTitle(working, words), 'Ready');
assert.equal(packageResultTitle(failing, words), 'Not working');
assert.equal(packageResultTitle({state:'registered'}, words), 'Not checked yet');
const ok = packageResultHTML(working, words, esc), bad = packageResultHTML(failing, words, esc);
assert.match(ok, /data-pk-check="working"/); assert.match(ok, /started the server once to check it/);
assert.match(bad, /data-pk-check="failed"/); assert.match(bad, /YouTube API key is required/);
assert.doesNotMatch(ok + bad, /not checked/);
console.log('All package-check result checks passed');

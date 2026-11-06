import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createProjectTestRuns, contentHTML, projectRuns, runHTML } from '../../static/js/project-test-runs.js';

const run = (extra = {}) => ({ project_id: 'a', run_id: 'r', owner_session_id: 's1',
  display_label: 'same test', source: 'wrapper', runner: 'pytest', unit: 'test',
  selected_total: 10, completed: 4, state: 'running', capture: 'complete', seq: 1,
  counts: { pass: 3, fail: 1, skip: 0, error: 0, xfail: 0, xpass: 0 }, ...extra });
const view = (pid, runs, state = 'ok') => ({ projectId: pid, runs,
  status: { state, lastOkAt: ['loading', 'error', 'disabled'].includes(state) ? null : 1 } });
function harness() {
  const callbacks = new Map(), subscriptions = [], unsubs = [], refreshed = [], painted = new Map();
  const data = {
    subscribe(pid, sid, cb) { subscriptions.push([pid, sid]); callbacks.set(pid, cb); return () => unsubs.push(pid); },
    refresh: pid => refreshed.push(pid),
  };
  const panel = createProjectTestRuns({ data, refreshModal: pid => refreshed.push(pid), root: pid => ({ querySelector: () => ({
    set innerHTML(html) { painted.set(pid, html); },
  }) }) });
  return { panel, data, callbacks, subscriptions, unsubs, refreshed, painted };
}

test('disabled/loading/initial error add zero navigation and panel bytes', () => {
  const h = harness(); h.panel.attach('a');
  for (const state of ['loading', 'error', 'disabled', 'restricted', 'missing']) {
    h.callbacks.get('a')(view('a', [], state));
    assert.equal(h.panel.available('a'), false);
    assert.equal(h.panel.navigationHTML('a', 'agent'), '');
    assert.equal(h.panel.panelHTML('a', 'test-runs'), '');
  }
});
test('one project subscription, idempotent attach and detach, late callbacks ignored', () => {
  const h = harness(); h.panel.attach('a'); h.panel.attach('a');
  assert.deepEqual(h.subscriptions, [['a', null]]);
  h.callbacks.get('a')(view('a', [run()]));
  assert.equal(h.panel.available('a'), true);
  h.panel.detach('a'); h.panel.detach('a');
  h.callbacks.get('a')(view('a', [run()]));
  assert.equal(h.panel.available('a'), false);
  assert.deepEqual(h.unsubs, ['a']);
  assert.deepEqual(h.refreshed, ['a']);
});
test('project/run identity isolates identical labels, run IDs and two session owners', () => {
  const h = harness(); h.panel.attach('a'); h.panel.attach('b');
  h.callbacks.get('a')(view('a', [run(), run({ run_id: 'r2', owner_session_id: 's2' }), run({ project_id: 'b', display_label: 'leak' })]));
  h.callbacks.get('b')(view('b', [run({ project_id: 'b', owner_session_id: 'b1', state: 'failed' })]));
  assert.match(h.panel.panelHTML('a', 'test-runs'), /Conversation s1/);
  assert.match(h.panel.panelHTML('a', 'test-runs'), /Conversation s2/);
  assert.doesNotMatch(h.panel.panelHTML('a', 'test-runs'), /leak|Conversation b1/);
  assert.match(h.panel.panelHTML('b', 'test-runs'), /Conversation b1/);
  assert.doesNotMatch(h.panel.panelHTML('b', 'test-runs'), /Conversation s1/);
  h.callbacks.get('a')(view('b', []));
  assert.equal(h.panel.available('a'), true);
});
test('ownerless work and empty projects require no figure/session lookup', () => {
  assert.match(contentHTML(view('a', [run({ owner_session_id: null })])), /Ownerless/);
  assert.match(contentHTML(view('a', [])), /No test runs recorded/);
});
test('newest numeric canonical timestamps order within active/recent groups', () => {
  const rows = [run({ run_id: 'old', created_at: 9 }), run({ run_id: 'new', created_at: 10 })];
  assert.deepEqual(projectRuns(view('a', rows)).map(r => r.run_id), ['new', 'old']);
  const html = contentHTML(view('a', [run(), run({ state: 'failed', run_id: 'f' })]));
  assert.match(html, /Active \(1\)/); assert.match(html, /Recent \(1\)/);
});
test('truthful known/zero/unknown/partial/generic measurements and finalizing', () => {
  assert.match(runHTML(run()), /4\/10 tests done/);
  assert.match(runHTML(run({ unit: 'script' })), /4\/10 smoke scripts/);
  assert.match(runHTML(run({ completed: 10 })), /finalizing/);
  assert.doesNotMatch(runHTML(run({ completed: 10 })), />passed</);
  assert.doesNotMatch(runHTML(run({ selected_total: 0, completed: 0 })), /<progress/);
  for (const extra of [{ selected_total: null }, { capture: 'partial' }, { completed: 11 }, { selected_total: -1 }]) {
    const html = runHTML(run(extra)); assert.match(html, /total unknown/); assert.doesNotMatch(html, /<progress/);
  }
  const generic = runHTML(run({ unit: null, selected_total: null }));
  assert.match(generic, /\u2014 pass/); assert.doesNotMatch(generic, /3 pass/);
});
test('all terminal verdicts, missing exits, not-run and denominator revisions stay explicit', () => {
  for (const state of ['passed', 'failed', 'error', 'cancelled', 'timeout', 'no_tests', 'lost']) {
    const html = runHTML(run({ state, exit_code: null }));
    assert.ok(html.includes(`>${state.replaceAll('_', ' ')}</span>`));
    assert.match(html, /Exit code: unknown/); assert.doesNotMatch(html, /<progress/);
  }
  const html = runHTML(run({ not_run: 6, collection_errors: 2, total_recalculated: true, failure_details_omitted: 7 }));
  for (const text of ['6 not run', '2 collection errors', 'Total recalculated', '7 failure details omitted']) assert.ok(html.includes(text));
});
test('runner fields, labels, names and all identity attributes escape markup and apostrophes', () => {
  const text = `'"><img src=x onerror=alert(1)>`;
  const html = runHTML(run({ run_id: text, owner_session_id: text, source: text, runner: text,
    display_label: text, parent_run_id: text, latest_failures: [{ name: text, phase: text }] }));
  assert.doesNotMatch(html, /<img|onerror=alert\(1\)>/);
  assert.match(html, /&#39;&quot;&gt;&lt;img/);
});
test('stale snapshots remain visible, enable/disable refreshes navigation, updates paint locally', () => {
  const h = harness(); h.panel.attach('a');
  h.callbacks.get('a')(view('a', [run()]));
  h.callbacks.get('a')(view('a', [run({ completed: 5 })], 'stale'));
  assert.match(h.painted.get('a'), /showing the last received snapshots/);
  assert.match(h.painted.get('a'), /5\/10/);
  assert.deepEqual(h.refreshed, ['a']);
  h.panel.refresh('a'); assert.deepEqual(h.refreshed, ['a', 'a']);
  h.callbacks.get('a')(view('a', [], 'disabled'));
  assert.deepEqual(h.refreshed, ['a', 'a', 'a']);
  assert.equal(h.panel.panelHTML('a', 'test-runs'), '');
});
test('one global disabled response removes every open project, stale callback cannot revive them', () => {
  const h = harness(); h.panel.attach('a'); h.panel.attach('b');
  h.callbacks.get('a')(view('a', [run()]));
  h.callbacks.get('b')(view('b', [run({ project_id: 'b' })]));
  h.data.isDisabled = () => true;
  h.callbacks.get('a')(view('a', [], 'disabled'));
  assert.equal(h.panel.panelHTML('b', 'test-runs'), '');
  assert.deepEqual(h.refreshed, ['a', 'b', 'a', 'b']);
  h.callbacks.get('b')(view('b', [run({ project_id: 'b' })]));
  assert.equal(h.panel.available('b'), false);
});

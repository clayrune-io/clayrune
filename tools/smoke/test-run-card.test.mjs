// MC-1148 phase 10: pure unit cases for the card's display model (no DOM, no browser).
// RUN: node --test tools/smoke/test-run-card.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { cardModel, testRunMarkerId, testRunCardPlaceholderHTML } from '../../static/js/test-run-card.js';

const run = (o = {}) => ({
  run_id: 'r', state: 'running', capture: 'complete', unit: 'test', selected_total: null, completed: 0,
  counts: { pass: 0, fail: 0, skip: 0, error: 0, xfail: 0, xpass: 0 }, latest_failures: [], failure_total: 0,
  total_recalculated: false, eta_seconds: null, created_at: 100, updated_at: 160, finished_at: null, ...o,
});
const ID = 'a'.repeat(32);

test('marker: exactly one 32-hex id on a line of its own', () => {
  assert.equal(testRunMarkerId(`[test-run:${ID}]`), ID);
  assert.equal(testRunMarkerId(`  [test-run:${ID}]  `), ID);
  for (const bad of [`x [test-run:${ID}]`, `[test-run:${ID}] x`, '[test-run:abc]', `[test-run:${'A'.repeat(32)}]`,
                     `[test-run:${ID}\n]`, null, undefined, 42]) assert.equal(testRunMarkerId(bad), null, String(bad));
});

test('placeholder: escapes every attribute and refuses a malformed id', () => {
  const html = testRunCardPlaceholderHTML(ID, 'p"><x', "s'&");
  assert.ok(!html.includes('"><x') && html.includes('&quot;&gt;&lt;x') && html.includes('&#39;&amp;'));
  assert.equal(testRunCardPlaceholderHTML('nope', 'p', 's'), '');
});

test('unknown total: indeterminate, no percent, no ETA', () => {
  const m = cardModel(run());
  assert.equal(m.bar.mode, 'indeterminate');
  assert.equal(m.eta, null);
  assert.ok(!/%/.test(m.progress));
  assert.match(m.progress, /total not announced/);
});

test('known total: floor percent; completed > total, partial capture and zero total are never a percent', () => {
  assert.deepEqual(cardModel(run({ selected_total: 3, completed: 1 })).bar, { mode: 'determinate', percent: 33 });
  assert.equal(cardModel(run({ selected_total: 3, completed: 4 })).bar.mode, 'indeterminate');
  assert.equal(cardModel(run({ selected_total: 3, completed: 1, capture: 'partial' })).bar.mode, 'indeterminate');
  assert.equal(cardModel(run({ selected_total: 0, completed: 0 })).bar.mode, 'indeterminate');
});

test('all units done while running is Finalizing, never Passed', () => {
  const m = cardModel(run({ selected_total: 4, completed: 4 }));
  assert.equal(m.verdict, 'Finalizing');
  assert.equal(m.terminal, false);
  assert.equal(m.eta, null);
});

test('ETA: only from the server, only while running, never after a total change', () => {
  assert.equal(cardModel(run({ selected_total: 9, completed: 3, eta_seconds: 125 })).eta, 'about 2m 05s left');
  assert.equal(cardModel(run({ selected_total: 9, completed: 3, eta_seconds: 125, total_recalculated: true })).eta, null);
  assert.equal(cardModel(run({ state: 'passed', eta_seconds: 5 })).eta, null);
});

test('verdicts: terminal states, unknown state, lost', () => {
  assert.equal(cardModel(run({ state: 'passed', exit_code: 0 })).verdict, 'Passed');
  assert.equal(cardModel(run({ state: 'timeout' })).verdict, 'Timed out');
  assert.equal(cardModel(run({ state: 'lost', capture: 'lost' })).tone, 'bad');
  assert.equal(cardModel(run({ state: 'weird' })).verdict, 'Status unknown');
  assert.equal(cardModel(run({ state: 'weird' })).terminal, false);
});

test('failures: capped, clipped, malformed rows dropped, hidden count reported', () => {
  const many = Array.from({ length: 60 }, (_, i) => ({ name: `t${i}`, phase: 'call' }));
  const m = cardModel(run({ state: 'failed', latest_failures: [...many, null, { name: 5 }, { name: '' }], failure_total: 80 }));
  assert.equal(m.failures.shown.length, 50);
  assert.equal(m.failures.total, 80);
  assert.equal(m.failures.hidden, 30);
  const long = cardModel(run({ state: 'failed', latest_failures: [{ name: 'x'.repeat(900), phase: 'p'.repeat(90) }], failure_total: 1 }));
  assert.ok(long.failures.shown[0].name.length <= 300 && long.failures.shown[0].phase.length <= 32);
});

test('hostile strings stay strings: the model never builds markup', () => {
  const evil = '<img src=x onerror=alert(1)>';
  const m = cardModel(run({ state: 'failed', display_label: evil, latest_failures: [{ name: evil, phase: evil }], failure_total: 1 }));
  assert.equal(m.title, evil);                    // passed through untouched: the DOM layer uses textContent
  assert.equal(m.failures.shown[0].name, evil);
});

test('notes disclose stale telemetry, partial capture, recalculated totals, collection errors', () => {
  const m = cardModel(run({ capture: 'partial', total_recalculated: true, collection_errors: 2, not_run: 1 }), 'stale');
  const t = m.notes.join(' ');
  for (const re of [/delayed/, /incomplete/, /total changed/, /2 collection errors/, /1 not run/]) assert.match(t, re);
});

test('counts: pass/fail/skip always listed, others only when non-zero', () => {
  const m = cardModel(run({ counts: { pass: 2, fail: 0, skip: 0, error: 0, xfail: 1, xpass: 0 } }));
  assert.deepEqual(m.counts.map((c) => c.key), ['pass', 'fail', 'skip', 'xfail']);
});

test('elapsed: from created_at to finished_at when terminal, else to updated_at', () => {
  assert.equal(cardModel(run()).elapsed, '1m 00s');
  assert.equal(cardModel(run({ state: 'passed', finished_at: 400 })).elapsed, '5m 00s');
  assert.equal(cardModel(run({ created_at: 'nope' })).elapsed, null);
});

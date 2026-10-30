import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

function fixture() {
  const ctx = vm.createContext({ window: {}, esc: s => String(s).replaceAll('"', '&quot;') });
  vm.runInContext(readFileSync(new URL('../../static/js/report-waiting.js', import.meta.url), 'utf8'), ctx);
  return (expression) => vm.runInContext(expression, ctx);
}
const reports = JSON.stringify([1, 2, 3].map(n => ({ event_id: String(n), stage: 'failed', label: 'Report was not delivered', detail: 'Proven absent' })));

test('multiple reports default to a single collapsed summary', () => {
  const run = fixture();
  const html = run(`_reportWaitingHTML('parent', ${reports})`);
  assert.match(html, /3 not delivered/);
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /class="report-waiting-rows" hidden/);
  assert.equal((html.match(/data-action="mark-handled"/g) || []).length, 3);
  assert.equal((html.match(/data-action="retry"/g) || []).length, 3);
});

test('summary counts each actual stage without treating queued reports as failures', () => {
  const run = fixture();
  assert.equal(run(`_reportWaitingSummary([{stage:'waiting'},{stage:'delivering'}])`), '1 delivering, 1 queued');
  assert.equal(run(`_reportWaitingSummary([{stage:'failed'},{stage:'waiting'},{stage:'check_failed'}])`),
    '1 not delivered, 1 could not check, 1 queued');
  assert.doesNotMatch(run(`_reportWaitingHTML('parent', [{stage:'waiting'},{stage:'delivering'}])`),
    /not delivered|may not have arrived|data-action="retry"/);
});

test('a pending check is neutral and a failed check offers Retry', () => {
  const run = fixture();
  assert.doesNotMatch(run(`_reportWaitingHTML('parent', [{stage:'delivering',label:'Delivering…'}])`),
    /data-action="retry"|data-action="mark-handled"/);
  const html = run(`_reportWaitingHTML('parent', [{stage:'check_failed',label:'Could not check: chat log unreadable'}])`);
  assert.match(html, /chat log unreadable/);
  assert.match(html, /data-action="retry"/);
});

test('expanded state survives rerender for this session only', () => {
  const run = fixture();
  run(`_reportWaitingExpanded.add('parent')`);
  assert.match(run(`_reportWaitingHTML('parent', ${reports})`), /aria-expanded="true"/);
  assert.match(run(`_reportWaitingHTML('other', ${reports})`), /aria-expanded="false"/);
});

test('one waiting report has no summary or recovery buttons', () => {
  const run = fixture();
  const html = run(`_reportWaitingHTML('parent', [{stage:'waiting',label:'Queued',detail:'Waiting'}])`);
  assert.doesNotMatch(html, /data-action=/);
  assert.doesNotMatch(html, /report-waiting-rows" hidden/);
});

test('changed details and action table invalidate the rendered key', () => {
  const run = fixture();
  assert.notEqual(run(`_reportWaitingKey([{event_id:'e',stage:'review',table:'inbox'}])`),
    run(`_reportWaitingKey([{event_id:'e',stage:'review',table:'outbox'}])`));
});

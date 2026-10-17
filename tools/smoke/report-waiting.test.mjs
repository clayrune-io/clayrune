import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

function fixture() {
  const ctx = vm.createContext({ window: {}, esc: s => String(s).replaceAll('"', '&quot;') });
  vm.runInContext(readFileSync(new URL('../../static/js/report-waiting.js', import.meta.url), 'utf8'), ctx);
  return (expression) => vm.runInContext(expression, ctx);
}
const reports = JSON.stringify([1, 2, 3].map(n => ({ event_id: String(n), stage: 'review', label: 'Report', detail: 'Unknown' })));

test('multiple reports default to a single collapsed summary', () => {
  const run = fixture();
  const html = run(`_reportWaitingHTML('parent', ${reports})`);
  assert.match(html, /3 reports may not have arrived - review/);
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /class="report-waiting-rows" hidden/);
  assert.equal((html.match(/data-action="mark-handled"/g) || []).length, 3);
  assert.equal((html.match(/data-action="retry"/g) || []).length, 3);
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

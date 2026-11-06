// MC-1148 phase 9: unit tests for static/js/test-run-data.js (no browser, no deps).
// Run: node --test tools/smoke/test-run-data.test.mjs
// The module is driven through createTestRunData() with a fake clock, fake fetch
// and fake document/window, so every timing claim below is deterministic.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createTestRunData } from '../../static/js/test-run-data.js';

const flush = () => new Promise((r) => setImmediate(r));

function target(extra = {}) {
  const l = new Map();
  return Object.assign({
    addEventListener(n, f) { if (!l.has(n)) l.set(n, new Set()); l.get(n).add(f); },
    removeEventListener(n, f) { l.get(n)?.delete(f); },
    emit(n) { for (const f of [...(l.get(n) || [])]) f({ type: n }); },
    count: (n) => (l.get(n) ? l.get(n).size : 0),
  }, extra);
}

function snap(id, seq, state = 'running', extra = {}) {
  return { run_id: id, project_id: 'p', owner_session_id: 's1', state, seq,
           created_at: `2026-11-06T09:00:${String(seq % 60).padStart(2, '0')}Z`, ...extra };
}
const res = (status, body, isJson = true) => ({
  status, ok: status >= 200 && status < 300,
  json: async () => { if (!isJson) throw new SyntaxError('not json'); return body; },
});

function harness(handler) {
  let t = 1_000_000, id = 0;
  const timers = new Map();
  const calls = [];
  const document = target({ hidden: false, visibilityState: 'visible' });
  const window = target();
  const env = {
    now: () => t,
    setTimeout: (f, ms) => { const i = ++id; timers.set(i, { f, at: t + ms }); return i; },
    clearTimeout: (i) => { timers.delete(i); },
    document, window, base: '',
    fetch: (url, init = {}) => {
      calls.push({ url, at: t, signal: init.signal });
      const out = handler(url, init, calls.length);
      if (out && out.never) {
        return new Promise((_, rej) => {
          init.signal?.addEventListener('abort', () => rej(Object.assign(new Error('aborted'), { name: 'AbortError' })));
        });
      }
      if (out instanceof Error) return Promise.reject(out);
      return Promise.resolve(out);
    },
  };
  async function advance(ms) {
    const end = t + ms;
    for (;;) {
      await flush();
      const due = [...timers.entries()].filter(([, x]) => x.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      timers.delete(due[0]);
      t = Math.max(t, due[1].at);
      due[1].f();
    }
    t = end;
    await flush();
  }
  return { env, calls, advance, timers, document, window, now: () => t, setNow: (v) => { t = v; } };
}

const okRuns = (rows) => res(200, { runs: rows });
function views(data, pid, sid) {
  const seen = [];
  const un = data.subscribe(pid, sid, (v) => seen.push(v));
  return { seen, un, last: () => seen[seen.length - 1] };
}

test('cold subscribe fetches once and two sessions in one project share one request', async () => {
  const h = harness(() => okRuns([snap('a', 3, 'running', { owner_session_id: 's1' }),
                                  snap('b', 5, 'passed', { owner_session_id: 's2' }),
                                  snap('c', 1, 'running', { owner_session_id: null })]));
  const data = createTestRunData(h.env);
  const one = views(data, 'p', 's1'), two = views(data, 'p', 's2'), all = views(data, 'p', null);
  await h.advance(100);
  assert.equal(h.calls.length, 1, 'one request for three subscribers');
  assert.match(h.calls[0].url, /\/api\/project\/p\/test-runs\?limit=100$/);
  assert.deepEqual(one.last().runs.map((r) => r.run_id), ['a']);
  assert.deepEqual(two.last().runs.map((r) => r.run_id), ['b']);
  assert.deepEqual(all.last().runs.map((r) => r.run_id).sort(), ['a', 'b', 'c']);
  assert.equal(all.last().status.state, 'ok');
  assert.ok(Object.isFrozen(all.last().runs[0]) && Object.isFrozen(all.last().runs[0]));
});

test('polls ~1s while a run is active, backs off 2/4/8/10s once everything is terminal', async () => {
  let state = 'running', seq = 1;
  const h = harness(() => okRuns([snap('a', seq, state)]));
  const data = createTestRunData(h.env);
  views(data, 'p', null);
  await h.advance(5000);
  const active = h.calls.map((c) => c.at);
  const gaps = active.slice(1).map((v, i) => v - active[i]);
  assert.ok(gaps.length >= 3 && gaps.every((g) => g >= 1000 && g <= 1100), `active gaps ${gaps}`);
  state = 'passed'; seq = 9;
  await h.advance(1200);
  const n = h.calls.length;
  await h.advance(60000);
  const quiet = h.calls.slice(n - 1).map((c) => c.at);
  const qgaps = quiet.slice(1).map((v, i) => v - quiet[i]);
  assert.deepEqual(qgaps.slice(0, 4).map((g) => Math.round(g / 100) * 100), [2000, 4000, 8000, 10000]);
  assert.ok(qgaps.every((g) => g <= 10100), `quiet cap ${qgaps}`);
});

test('keeps polling with no chat stream, so it continues after the turn ends', async () => {
  globalThis.EventSource = class { constructor() { throw new Error('EventSource must not be created'); } };
  try {
    let state = 'running';
    const h = harness(() => okRuns([snap('a', state === 'running' ? 2 : 7, state)]));
    const data = createTestRunData(h.env);
    const v = views(data, 'p', 's1');
    await h.advance(30000);              // "turn over": nothing but the poller is alive
    state = 'failed';
    await h.advance(12000);
    assert.equal(v.last().runs[0].state, 'failed');
    assert.equal(data.stats().projects, 1);
  } finally { delete globalThis.EventSource; }
});

test('reconciles by seq: stale or out-of-order rows never move a run backwards', async () => {
  const pages = [[snap('a', 5)], [snap('a', 3)], [snap('a', 5, 'passed')], [snap('a', 4, 'failed')]];
  let i = 0;
  const h = harness(() => okRuns(pages[Math.min(i++, pages.length - 1)]));
  const data = createTestRunData(h.env);
  const v = views(data, 'p', null);
  await h.advance(50);
  assert.equal(v.last().runs[0].seq, 5);
  await h.advance(1100);                         // seq 3 arrives late
  assert.equal(data.get('p', 'a').seq, 5);
  assert.equal(data.get('p', 'a').state, 'running');
  await h.advance(1100);                         // equal seq, terminal: upgrade
  assert.equal(data.get('p', 'a').state, 'passed');
  await h.advance(1100);                         // lower seq failed must not win
  assert.equal(data.get('p', 'a').state, 'passed');
  assert.equal(data.get('p', 'a').seq, 5);
});

test('the same run id in two projects stays separate', async () => {
  const h = harness((url) => okRuns([snap('same', url.includes('/p1/') ? 2 : 8, url.includes('/p1/') ? 'running' : 'passed')]));
  const data = createTestRunData(h.env);
  views(data, 'p1', null); views(data, 'p2', null);
  await h.advance(100);
  assert.equal(data.get('p1', 'same').state, 'running');
  assert.equal(data.get('p2', 'same').state, 'passed');
  assert.equal(h.calls.length, 2);
});

test('hidden page makes no requests; becoming visible refetches immediately', async () => {
  const h = harness(() => okRuns([snap('a', 1)]));
  h.document.hidden = true; h.document.visibilityState = 'hidden';
  const data = createTestRunData(h.env);
  views(data, 'p', null);
  await h.advance(30000);
  assert.equal(h.calls.length, 0);
  assert.equal(h.timers.size, 0);
  h.document.hidden = false; h.document.visibilityState = 'visible';
  h.document.emit('visibilitychange');
  await h.advance(100);
  assert.equal(h.calls.length, 1);
});

test('a hidden tab stops its timer mid-flight; focus/pageshow/online/resume each refetch', async () => {
  const h = harness(() => okRuns([snap('a', 1, 'running')]));
  const data = createTestRunData(h.env);
  views(data, 'p', null);
  await h.advance(100);
  const base = h.calls.length;
  h.document.hidden = true; h.document.visibilityState = 'hidden';
  await h.advance(20000);
  assert.equal(h.calls.length, base, 'no polling while hidden');
  h.document.hidden = false; h.document.visibilityState = 'visible';
  for (const [t, name] of [[h.window, 'focus'], [h.window, 'pageshow'], [h.window, 'online'], [h.document, 'resume']]) {
    const before = h.calls.length;
    t.emit(name);
    await h.advance(100);
    assert.equal(h.calls.length, before + 1, `${name} refetches once`);
  }
});

test('a timer that fires very late (mobile suspension) is a resume: backoff resets to the short interval', async () => {
  const h = harness(() => okRuns([snap('a', 4, 'passed')]));
  const data = createTestRunData(h.env);
  views(data, 'p', null);
  await h.advance(30000);                         // quiet, deep into backoff
  const quietGap = h.calls.at(-1).at - h.calls.at(-2).at;
  assert.ok(quietGap >= 8000, `quiet gap ${quietGap}`);
  // Page frozen: the pending timer is overdue by 10 minutes when it finally fires.
  const [[, pending]] = [...h.timers.entries()];
  h.setNow(pending.at + 600000);
  await h.advance(100);
  const n = h.calls.length;
  await h.advance(25000);
  const at = h.calls.slice(n - 1).map((c) => c.at);
  const first = at[1] - at[0];
  assert.ok(first <= 4100, `first gap after resume restarts the backoff, got ${first}`);
});

test('bare 404 means flag off: status disabled, slow re-probe; recovers when enabled', async () => {
  let enabled = false;
  const h = harness(() => (enabled ? okRuns([snap('a', 1)]) : res(404, null, false)));
  const data = createTestRunData(h.env);
  const v = views(data, 'p', null);
  await h.advance(100);
  assert.equal(v.last().status.state, 'disabled');
  assert.equal(data.isDisabled(), true);
  await h.advance(60000);
  assert.equal(h.calls.length, 1, 'no hammering while disabled');
  enabled = true;
  await h.advance(61000);
  const probes = h.calls.map((c) => c.at - 1000000);
  assert.ok(probes[1] >= 120000 && probes[1] < 120100, `re-probed at 120s, got ${probes}`);
  assert.equal(probes.filter((x) => x < 120000).length, 1, 'only the first probe before then');
  assert.equal(v.last().status.state, 'ok');
  assert.equal(data.isDisabled(), false);
});

test('JSON 404 (unknown project) and 403 (restricted) stop polling that project', async () => {
  const h = harness((url) => (url.includes('/gone/') ? res(404, { error: 'Test run, project or session not found' })
                                                      : res(403, { error: 'restricted' })));
  const data = createTestRunData(h.env);
  const a = views(data, 'gone', null), b = views(data, 'locked', null);
  await h.advance(60000);
  assert.equal(h.calls.length, 2);
  assert.equal(a.last().status.state, 'missing');
  assert.equal(b.last().status.state, 'restricted');
  assert.equal(data.isDisabled(), false, 'a missing project does not read as flag-off');
});

test('failures back off, report stale, and recover on the next success', async () => {
  let mode = 'fail';
  const h = harness(() => (mode === 'fail' ? new TypeError('network down') : okRuns([snap('a', 1)])));
  const data = createTestRunData(h.env);
  const v = views(data, 'p', null);
  await h.advance(40000);
  const at = h.calls.map((c) => c.at);
  const gaps = at.slice(1).map((x, i) => x - at[i]);
  assert.ok(gaps[1] > gaps[0] && gaps[2] > gaps[1], `growing gaps ${gaps}`);
  assert.ok(Math.max(...gaps) <= 30100);
  assert.equal(v.last().status.state, 'error');         // never had data
  mode = 'ok';
  await h.advance(35000);
  assert.equal(v.last().status.state, 'ok');
  mode = 'fail';
  await h.advance(120000);
  assert.equal(v.last().status.state, 'stale');
  assert.equal(v.last().runs.length, 1, 'last good data is kept while stale');
});

test('a hung request is aborted at the timeout and counted as a failure', async () => {
  const h = harness(() => ({ never: true }));
  const data = createTestRunData(h.env);
  const v = views(data, 'p', null);
  await h.advance(8100);
  assert.equal(h.calls[0].signal.aborted, true);
  assert.equal(v.last().status.failures, 1);
  assert.equal(v.last().status.error, 'timeout');
});

test('hint: pulls the next poll forward only when it is news, and never writes data', async () => {
  let rows = [snap('a', 4, 'passed')];
  const h = harness(() => okRuns(rows));
  const data = createTestRunData(h.env);
  const v = views(data, 'p', null);
  await h.advance(50);
  await h.advance(9000);                           // deep in quiet backoff
  const base = h.calls.length;
  assert.equal(data.hint('p', { run_id: 'a', seq: 4 }), false, 'already have seq 4');
  assert.equal(data.hint('p', { run_id: 'a', seq: 3 }), false, 'older than cache');
  assert.equal(data.hint('p', { run_id: 'zzz', seq: -1 }), false);
  assert.equal(data.hint('p', { run_id: 7, seq: 2 }), false);
  assert.equal(data.hint('p', null), false);
  assert.equal(data.hint('nobody-subscribed', { run_id: 'x', seq: 1 }), false);
  await h.advance(300);
  assert.equal(h.calls.length, base, 'rejected hints cause no request');
  rows = [snap('a', 4, 'passed'), snap('n', 1, 'running')];
  assert.equal(data.hint('p', { run_id: 'n', seq: 1, state: 'FORGED', counts: { pass: 999 } }), true);
  assert.equal(data.get('p', 'n'), null, 'a hint alone carries no data');
  await h.advance(400);
  assert.equal(h.calls.length, base + 1);
  assert.equal(data.get('p', 'n').state, 'running', 'data came from the server read');
  assert.equal(v.last().runs.length, 2);
});

test('hint during an in-flight request forces one follow-up fetch (that response may predate it)', async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  let n = 0;
  const h = harness(() => {
    n++;
    if (n === 1) return gate.then(() => okRuns([snap('a', 1)]));
    return okRuns([snap('a', 1), snap('b', 2)]);
  });
  const data = createTestRunData(h.env);
  views(data, 'p', null);
  await h.advance(50);
  assert.equal(h.calls.length, 1);
  assert.equal(data.hint('p', { run_id: 'b', seq: 2 }), true);
  release();
  await h.advance(300);
  assert.equal(h.calls.length, 2);
  assert.ok(data.get('p', 'b'));
});

test('refresh() resolves after a fetch that STARTED after the call', async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  let n = 0;
  const h = harness(() => { n++; return n === 1 ? gate.then(() => okRuns([snap('a', 1)])) : okRuns([snap('a', 2)]); });
  const data = createTestRunData(h.env);
  views(data, 'p', null);
  await h.advance(50);
  let result = null;
  data.refresh('p').then((r) => { result = r; });
  release();
  await h.advance(10);
  await h.advance(300);
  assert.ok(result, 'resolved');
  assert.equal(h.calls.length, 2, 'resolved by the second fetch, not the one already in flight');
  assert.equal(data.get('p', 'a').seq, 2);
});

test('a full page asks for each subscribed session directly', async () => {
  const filler = Array.from({ length: 100 }, (_, i) => snap(`r${i}`, 1, 'passed', { owner_session_id: 'other' }));
  const h = harness((url) => (url.includes('session=mine') ? okRuns([snap('old', 3, 'running', { owner_session_id: 'mine' })])
                                                          : okRuns(filler)));
  const data = createTestRunData(h.env);
  const v = views(data, 'p', 'mine');
  await h.advance(100);
  assert.equal(h.calls.length, 2);
  assert.match(h.calls[1].url, /session=mine/);
  assert.deepEqual(v.last().runs.map((r) => r.run_id), ['old']);
});

test('malformed rows are rejected; a throwing subscriber does not break the others', async () => {
  const h = harness(() => okRuns([snap('ok', 1), { run_id: 5 }, null, { run_id: 'x', seq: -2, state: 'running' },
                                  { run_id: 'y', seq: 1.5, state: 'running' }, { run_id: 'z'.repeat(500), seq: 1, state: 'running' }]));
  const data = createTestRunData(h.env);
  const orig = console.error; console.error = () => {};
  try {
    data.subscribe('p', null, () => { throw new Error('boom'); });
    const good = views(data, 'p', null);
    await h.advance(100);
    assert.deepEqual(good.last().runs.map((r) => r.run_id), ['ok']);
    assert.equal(data.stats().rejectedRows, 5);
  } finally { console.error = orig; }
});

test('unsubscribe stops polling, clears the timer and removes page listeners', async () => {
  const h = harness(() => okRuns([snap('a', 1)]));
  const data = createTestRunData(h.env);
  const a = views(data, 'p', null), b = views(data, 'q', null);
  await h.advance(100);
  assert.equal(h.document.count('visibilitychange'), 1);
  a.un(); a.un();                                  // idempotent
  const n = h.calls.length;
  await h.advance(3000);
  assert.ok(h.calls.every((c, i) => i < n || c.url.includes('/q/')), 'p no longer polled');
  b.un();
  assert.equal(h.timers.size, 0);
  assert.equal(h.document.count('visibilitychange'), 0);
  assert.equal(h.window.count('focus'), 0);
  const total = h.calls.length;
  await h.advance(60000);
  assert.equal(h.calls.length, total);
});

test('re-subscribing (modal rebuild) paints cached data and refetches at once', async () => {
  let seq = 1;
  const h = harness(() => okRuns([snap('a', seq)]));
  const data = createTestRunData(h.env);
  const first = views(data, 'p', 's1');
  await h.advance(100);
  first.un();
  const n = h.calls.length;
  seq = 6;
  const again = views(data, 'p', 's1');
  await h.advance(30);
  assert.equal(again.seen[0].runs[0].seq, 1, 'first paint comes from cache');
  assert.equal(h.calls.length, n + 1, 'and a fresh read starts immediately');
  await h.advance(100);
  assert.equal(again.last().runs[0].seq, 6);
});

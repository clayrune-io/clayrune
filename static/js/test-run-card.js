// ── In-chat test-run card (`[test-run:<id>]`, MC-1148 phase 10) ──────────────
// A test run started by an agent belongs to the conversation that launched it
// and keeps going after that turn ends. The SERVER posts one marker line into
// the chat when it enrolls the run; this turns the line into a live card:
//
//   pytest · tests/unit          Running            [=====>      ] 42 of 100 tests
//   40 pass · 1 fail · 1 skip         2 failures ▸ tests/test_x.py::test_a (call)
//
// The marker carries only an id. Every word on the card comes from the run
// snapshot the shared data layer (test-run-data.js) holds, never from the
// transcript, so a marker typed into a reply can only show a run the server
// recorded for THIS chat's session (or "no longer available").
//
// Fetching: none here. The card subscribes to window.TestRunData (one poll per
// project, no per-run stream). One subscription serves every card of a
// (project, session); it is dropped once every card is settled or detached.
//
// Honesty rules (docs/TEST_RUN_PROGRESS_DESIGN.md §4, §6):
//   - a percentage exists only when the total is announced, capture is complete
//     and completed <= total; otherwise the bar is indeterminate and no
//     percentage is shown;
//   - no ETA unless the server supplies one; nothing is extrapolated here;
//   - "all units done" says Finalizing, never Passed: success waits for the
//     process exit the server records;
//   - every dynamic string enters the DOM through textContent / setAttribute.
//
// Hooks (same shape as agent-read-card.js): a cold-render placeholder
// (`testRunCardPlaceholderHTML`, conversation.js), a live per-line interceptor
// (`_handleTestRunLine`, appendAgentLine) and `mountTestRunCards(root)`
// (refreshModalById). Duplicates are removed by looking at the DOM
// (`data-test-run-id`), not at a JS set that outlives a rebuild.
// ES module -> shared via window.* . Contract: docs/TEST_RUN_CARD.md.

const TERMINAL = new Set(['passed', 'failed', 'error', 'cancelled', 'timeout', 'no_tests', 'lost']);
const KNOWN_STATES = new Set(['running', ...TERMINAL]);
const MARKER_RE = /^\s*\[test-run:([0-9a-f]{32})\]\s*$/;
const RUN_ID_RE = /^[0-9a-f]{32}$/;
const MAX_FAILURES_SHOWN = 50;
const SWEEP_MS = 15000;
const OUTCOME_LABELS = [['pass', 'pass'], ['fail', 'fail'], ['skip', 'skip'],
                        ['error', 'error'], ['xfail', 'xfail'], ['xpass', 'xpass']];
const ALWAYS_SHOWN = new Set(['pass', 'fail', 'skip']);
const UNIT_WORDS = { test: ['test', 'tests'], script: ['script', 'scripts'] };

const _isCount = (v) => Number.isSafeInteger(v) && v >= 0;
const _clip = (s, n) => { const t = String(s); return t.length > n ? t.slice(0, n - 1) + '…' : t; };

/** The run id a marker line names, or null. */
export function testRunMarkerId(line) {
  const m = MARKER_RE.exec(typeof line === 'string' ? line : '');
  return m ? m[1] : null;
}

function _unit(run, n) {
  const w = UNIT_WORDS[run.unit];
  return w ? (n === 1 ? w[0] : w[1]) : (n === 1 ? 'unit' : 'units');
}

function _duration(sec) {
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`;
}

const VERDICTS = {
  running: ['Running', 'run'], passed: ['Passed', 'ok'], failed: ['Failed', 'bad'],
  error: ['Error', 'bad'], cancelled: ['Cancelled', 'warn'], timeout: ['Timed out', 'bad'],
  no_tests: ['No tests ran', 'warn'], lost: ['Lost: no exit record', 'bad'],
};

/**
 * Pure display model for one run snapshot. No DOM, no clock: unit-testable.
 * `status` is the data layer's status.state ('ok', 'stale', ...), used only
 * for the delayed-updates note.
 */
export function cardModel(run, status) {
  const state = KNOWN_STATES.has(run.state) ? run.state : 'unknown';
  const terminal = TERMINAL.has(state);
  const total = _isCount(run.selected_total) ? run.selected_total : null;
  const done = _isCount(run.completed) ? run.completed : null;
  const complete = run.capture === 'complete';
  const determinate = total !== null && total > 0 && done !== null && done <= total && complete;
  const percent = determinate ? Math.floor((100 * done) / total) : null;
  const finalizing = state === 'running' && determinate && done === total;

  let verdict = state === 'unknown' ? ['Status unknown', 'warn'] : VERDICTS[state];
  if (finalizing) verdict = ['Finalizing', 'run'];

  let progress;
  if (total === 0) progress = `0 ${_unit(run, 0)}`;
  else if (determinate) {
    progress = finalizing
      ? `${done} of ${total} ${_unit(run, total)} done, waiting for the process to exit`
      : `${done} of ${total} ${_unit(run, total)}`;
    if (terminal && done < total) progress += ' (stopped early)';
  } else if (total !== null && done !== null) {
    progress = `${done} of ${total} ${_unit(run, total)} (not a verified percentage)`;
  } else if (done) {
    progress = `${done} ${_unit(run, done)} done, total not announced`;
  } else {
    progress = terminal ? 'No results were reported' : 'Waiting for the first result; total not announced';
  }

  const counts = [];
  if (run.counts && typeof run.counts === 'object') {
    for (const [key, label] of OUTCOME_LABELS) {
      const n = run.counts[key];
      if (_isCount(n) && (n > 0 || ALWAYS_SHOWN.has(key))) counts.push({ key, label, n });
    }
  }

  const notes = [];
  if (status === 'stale' || status === 'error') notes.push('Updates are delayed; showing the last known state.');
  if (run.capture === 'partial') notes.push('Capture was incomplete; counts may be low.');
  if (run.capture === 'lost') notes.push('Observation of this run was lost.');
  if (run.total_recalculated === true) notes.push('The total changed during the run, so progress may have moved backwards.');
  if (_isCount(run.collection_errors) && run.collection_errors > 0) {
    notes.push(`${run.collection_errors} collection ${run.collection_errors === 1 ? 'error' : 'errors'}.`);
  }
  if (_isCount(run.not_run) && run.not_run > 0) notes.push(`${run.not_run} not run.`);
  if (terminal && Number.isInteger(run.exit_code)) notes.push(`Exit code ${run.exit_code}.`);

  const list = Array.isArray(run.latest_failures) ? run.latest_failures : [];
  const shown = [];
  for (const f of list) {
    if (shown.length >= MAX_FAILURES_SHOWN) break;
    if (f && typeof f === 'object' && typeof f.name === 'string' && f.name) {
      shown.push({ name: _clip(f.name, 300), phase: typeof f.phase === 'string' ? _clip(f.phase, 32) : '' });
    }
  }
  const ftotal = Math.max(_isCount(run.failure_total) ? run.failure_total : 0, list.length);

  let elapsed = null;
  const end = terminal && typeof run.finished_at === 'number' ? run.finished_at : run.updated_at;
  if (typeof run.created_at === 'number' && typeof end === 'number'
      && Number.isFinite(run.created_at) && Number.isFinite(end) && end >= run.created_at) {
    elapsed = _duration(end - run.created_at);
  }

  let eta = null;
  if (state === 'running' && _isCount(run.eta_seconds) && run.total_recalculated !== true && !finalizing) {
    eta = `about ${_duration(run.eta_seconds)} left`;
  }

  const title = _clip((typeof run.display_label === 'string' && run.display_label.trim())
    || (typeof run.runner === 'string' && run.runner.trim()) || 'Test run', 200);
  return {
    id: run.run_id, state, terminal, title, verdict: verdict[0], tone: verdict[1],
    bar: determinate ? { mode: 'determinate', percent } : (terminal ? { mode: 'none' } : { mode: 'indeterminate' }),
    progress, counts, notes, elapsed, eta,
    failures: { total: ftotal, shown, hidden: Math.max(0, ftotal - shown.length) },
  };
}

// ── DOM ────────────────────────────────────────────────────────────────────
const _el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

function _buildCard(id) {
  const root = _el('section', 'test-run-card');
  root.dataset.testRunId = id;
  const head = _el('div', 'test-run-card-head');
  const kicker = _el('span', 'test-run-card-kicker', 'Test run');
  const title = _el('span', 'test-run-card-title');
  const verdict = _el('span', 'test-run-card-verdict');
  head.append(kicker, title, verdict);
  const bar = _el('progress', 'test-run-card-bar');
  bar.max = 100;
  const progress = _el('div', 'test-run-card-progress');
  const counts = _el('div', 'test-run-card-counts');
  const meta = _el('div', 'test-run-card-meta');
  const notes = _el('ul', 'test-run-card-notes');
  const failures = _el('details', 'test-run-card-failures');
  const summary = _el('summary', 'test-run-card-failures-summary');
  const flist = _el('ul', 'test-run-card-failure-list');
  const fmore = _el('div', 'test-run-card-failure-more');
  failures.append(summary, flist, fmore);
  const live = _el('div', 'test-run-card-live');
  live.setAttribute('role', 'status');
  live.setAttribute('aria-live', 'polite');
  root.append(head, bar, progress, counts, meta, notes, failures, live);
  return { root, title, verdict, bar, progress, counts, meta, notes, failures, summary, flist, fmore, live };
}

const _openPref = new Map();   // "project|run" -> user's open/closed choice for the failure list

function _paint(card, run, status) {
  const m = cardModel(run, status);
  if (!card.dom) {
    card.dom = _buildCard(card.id);
    card.dom.failures.addEventListener('toggle', () => { _openPref.set(card.prefKey, card.dom.failures.open); });
    card.mount.replaceChildren(card.dom.root);
  }
  const d = card.dom;
  d.root.dataset.state = m.state;
  d.root.dataset.tone = m.tone;
  d.root.setAttribute('aria-label', `Test run ${m.title}: ${m.verdict}`);
  d.title.textContent = m.title;
  d.verdict.textContent = m.verdict;
  d.root.setAttribute('aria-busy', m.terminal ? 'false' : 'true');

  const bar = d.bar;
  if (m.bar.mode === 'none') {
    bar.hidden = true;
  } else {
    bar.hidden = false;
    if (m.bar.mode === 'determinate') {
      bar.value = m.bar.percent;
      bar.setAttribute('aria-valuetext', `${m.bar.percent} percent, ${m.progress}`);
    } else {
      bar.removeAttribute('value');           // native indeterminate: no value, no percent
      bar.setAttribute('aria-valuetext', `In progress, ${m.progress}`);
    }
    bar.setAttribute('aria-label', `Progress of ${m.title}`);
  }
  d.progress.textContent = m.bar.mode === 'determinate' ? `${m.bar.percent}% · ${m.progress}` : m.progress;

  d.counts.replaceChildren(...m.counts.map((c) => {
    const s = _el('span', 'test-run-card-count', `${c.n} ${c.label}`);
    s.dataset.outcome = c.key;
    if ((c.key === 'fail' || c.key === 'error') && c.n > 0) s.classList.add('is-alert');
    return s;
  }));
  d.counts.hidden = m.counts.length === 0;

  const meta = [];
  if (m.elapsed) meta.push(`${m.elapsed} elapsed`);
  if (m.eta) meta.push(m.eta);
  d.meta.textContent = meta.join(' · ');
  d.meta.hidden = meta.length === 0;

  d.notes.replaceChildren(...m.notes.map((n) => _el('li', null, n)));
  d.notes.hidden = m.notes.length === 0;

  const f = m.failures;
  d.failures.hidden = f.total === 0;
  if (f.total > 0) {
    d.summary.textContent = `${f.total} ${f.total === 1 ? 'failure' : 'failures'}`;
    const sig = f.shown.map((x) => `${x.name}\u0000${x.phase}`).join('\u0001');
    if (card.failSig !== sig) {
      card.failSig = sig;
      d.flist.replaceChildren(...f.shown.map((x) => {
        const li = _el('li', 'test-run-card-failure');
        li.append(_el('code', 'test-run-card-failure-name', x.name));
        if (x.phase) li.append(_el('span', 'test-run-card-failure-phase', ` (${x.phase})`));
        return li;
      }));
    }
    d.fmore.textContent = f.hidden > 0 ? `${f.hidden} more not listed here; see the job log.` : '';
    d.fmore.hidden = f.hidden === 0;
    const pref = _openPref.get(card.prefKey);
    const want = pref !== undefined ? pref : f.total <= 5;
    if (d.failures.open !== want) d.failures.open = want;
  }

  // One restrained announcement, only when a card we watched run finishes.
  if (card.wasTerminal === false && m.terminal) {
    d.live.textContent = `Test run ${m.verdict}${f.total ? `, ${f.total} ${f.total === 1 ? 'failure' : 'failures'}` : ''}.`;
  }
  card.wasTerminal = m.terminal;
  return m.terminal;
}

function _plainLine(card) {
  const line = _el('div', 'agent-line', `[test-run:${card.id}]`);
  line.setAttribute('dir', 'auto');
  card.mount.replaceChildren(line);
}

function _notice(card, text) {
  const n = _el('div', 'test-run-card-gone', text);
  n.dataset.testRunId = card.id;
  card.mount.replaceChildren(n);
}

// ── subscriptions: one per (project, session), shared by its cards ─────────
const _subs = new Map();      // key -> { unsub, cards:Set, projectId, sessionId }
let _sweepTimer = null;

function _drop(key) {
  const reg = _subs.get(key);
  if (!reg) return;
  _subs.delete(key);
  try { reg.unsub(); } catch (e) { console.error('[test-run-card] unsubscribe failed', e); }
  if (_subs.size === 0 && _sweepTimer != null) { clearInterval(_sweepTimer); _sweepTimer = null; }
}

function _prune(key) {
  const reg = _subs.get(key);
  if (!reg) return;
  for (const c of [...reg.cards]) if (c.settled || !c.mount.isConnected) reg.cards.delete(c);
  if (reg.cards.size === 0) _drop(key);
}

function _sweep() { for (const key of [..._subs.keys()]) _prune(key); }

function _apply(card, view) {
  const st = view.status.state;
  if (st === 'disabled' || st === 'restricted' || st === 'missing') {
    // Same as today: a marker the feature cannot back is just a line of text.
    _plainLine(card); card.settled = true; return;
  }
  const run = view.runs.find((r) => r.run_id === card.id);
  if (run) {
    card.gone = false;
    if (_paint(card, run, st)) card.settled = true;
    return;
  }
  if (st === 'ok' && !card.dom && !card.checking) {
    // Not in the newest page: ask once more before declaring it gone.
    card.checking = true;
    const TRD = window.TestRunData;
    TRD.refresh(card.projectId).then(() => {
      if (card.settled || !card.mount.isConnected) return;
      const again = TRD.list(card.projectId, card.sessionId).find((r) => r.run_id === card.id);
      if (again) _apply(card, { status: TRD.status(card.projectId) || { state: 'ok' }, runs: [again] });
      else { _notice(card, 'This test run is no longer available.'); card.settled = true; }
    });
  } else if (st === 'error' && !card.dom) {
    _notice(card, 'Test run status is unavailable right now.');
  }
}

function _onView(key, view) {
  const reg = _subs.get(key);
  if (!reg) return;
  for (const c of reg.cards) if (c.mount.isConnected && !c.settled) _apply(c, view);
  _prune(key);
}

function _ensureSub(projectId, sessionId) {
  const key = `${projectId}\u0000${sessionId}`;
  let reg = _subs.get(key);
  if (!reg) {
    reg = { cards: new Set(), projectId, sessionId, unsub: () => {} };
    _subs.set(key, reg);
    reg.unsub = window.TestRunData.subscribe(projectId, sessionId, (view) => _onView(key, view));
    if (_sweepTimer == null) _sweepTimer = setInterval(_sweep, SWEEP_MS);
  }
  return reg;
}

// ── mounting ───────────────────────────────────────────────────────────────
function _scopeOf(mount) {
  let sessionId = mount.dataset.sessionId || '';
  if (!sessionId) {
    const out = mount.closest('[id^="agent-output-"]');
    if (out) sessionId = out.id.slice('agent-output-'.length);
  }
  let projectId = mount.dataset.projectId || '';
  if (!projectId && sessionId) {
    try { projectId = (agentStatusCache[sessionId] || {}).projectId || ''; } catch (e) { projectId = ''; }
  }
  return { projectId, sessionId };
}

function _attach(mount, id) {
  const { projectId, sessionId } = _scopeOf(mount);
  const card = { id, mount, projectId, sessionId, dom: null, settled: false, checking: false,
                 wasTerminal: null, failSig: null, prefKey: `${projectId}|${id}` };
  const TRD = window.TestRunData;
  if (!TRD || !projectId || !sessionId) { _plainLine(card); return; }
  // Paint what the data layer already holds, then let the subscription refresh it.
  const cached = TRD.list(projectId, sessionId).find((r) => r.run_id === id);
  if (cached && _paint(card, cached, 'ok')) return;      // terminal runs never change again
  const reg = _ensureSub(projectId, sessionId);
  reg.cards.add(card);
}

/** Mount every unmounted placeholder under `root`; remove DOM duplicates. */
export function mountTestRunCards(root) {
  const scope = root || document;
  scope.querySelectorAll('.test-run-card-mount:not([data-trc-mounted])').forEach((mount) => {
    const id = mount.dataset.testRunId || '';
    if (!RUN_ID_RE.test(id)) { mount.remove(); return; }
    // One card per run per chat view, decided by what is in the DOM.
    const view = mount.closest('.agent-output') || mount.parentElement || document;
    const twin = [...view.querySelectorAll(`.test-run-card-mount[data-trc-mounted][data-test-run-id="${id}"]`)];
    if (twin.length) { mount.remove(); return; }
    mount.dataset.trcMounted = '1';
    _attach(mount, id);
  });
  _sweep();
}

function _esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** Cold-render placeholder for the string builder in conversation.js. */
export function testRunCardPlaceholderHTML(rid, projectId, sessionId) {
  if (!RUN_ID_RE.test(rid)) return '';
  return `<div class="test-run-card-mount" data-test-run-id="${_esc(rid)}" data-project-id="${_esc(projectId || '')}" data-session-id="${_esc(sessionId || '')}"></div>`;
}

/** Live per-line path (appendAgentLine). True when the line was the marker. */
export function handleTestRunLine(sessionId, text, el) {
  const id = testRunMarkerId(text);
  if (!id) return false;
  if (el.querySelector(`.test-run-card-mount[data-test-run-id="${id}"]`)) return true;   // already here
  const ph = document.createElement('div');
  ph.className = 'test-run-card-mount';
  ph.dataset.testRunId = id;
  ph.dataset.sessionId = sessionId;
  el.appendChild(ph);
  mountTestRunCards(el);
  return true;
}

export const cardStats = () => ({
  subscriptions: _subs.size,
  cards: [..._subs.values()].reduce((n, r) => n + r.cards.size, 0),
  sweeping: _sweepTimer != null,
});

if (typeof window !== 'undefined') {
  window.testRunMarkerId = testRunMarkerId;
  window.testRunCardPlaceholderHTML = testRunCardPlaceholderHTML;
  window.mountTestRunCards = mountTestRunCards;
  window._handleTestRunLine = handleTestRunLine;
  window.TestRunCards = { cardModel, cardStats };
}

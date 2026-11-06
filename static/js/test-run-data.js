// ── Test-run data layer (MC-1148 phase 9) ────────────────────────────────────
// ONE shared poll/cache/reconcile coordinator for the live test-run API
// (mc/blueprints/test_run_routes.py). It renders nothing: phase 10's card and
// later the Floor / project panel subscribe to it and draw from what it hands
// back. Contract: docs/TEST_RUN_DATA.md.
//
// Why polling, not a stream per run (design §6, Dave's Q3 = A): background tests
// outlive the chat turn, and chat SSE closes at turn completion to free the
// browser's 6-per-origin slots. So the source of truth is GET
// /api/project/<pid>/test-runs, polled by this single coordinator:
//   - one request per project per tick, never one per run or per mounted card;
//   - ~1s while any displayed run is active, backing off while quiet;
//   - paused while the page is hidden, an immediate refetch on visible / focus /
//     pageshow / resume / online (mobile suspension freezes timers, so the
//     refetch is event-driven and a late timer also counts as a resume);
//   - fetches carry an abort timeout; failures back off and surface as `stale`.
// This module opens NO EventSource. The optional SSE hint (`hint()`, called by
// resume-preview.js) only says "re-read sooner"; it never writes run data, so a
// stale hint cannot overwrite a newer poll.
//
// Reconciliation is by the server's per-run snapshot `seq`: a row is accepted
// only if it is new or its seq is higher (equal seq only upgrades to a terminal
// state). Page order, response arrival order and hint timing therefore cannot
// move a run backwards.
//
// Gated by the server flag `test_run_progress_enabled`: with it off every route
// answers a bare (non-JSON) 404, which this layer reads as "feature disabled"
// and re-probes slowly. A JSON 404 is a real missing project and stops polling.
//
// ES module: shared via `window.TestRunData` (top-level names are not global).

const TERMINAL = new Set(['passed', 'failed', 'error', 'cancelled', 'timeout', 'no_tests', 'lost']);

const DEFAULTS = {
  activeMs: 1000,        // poll cadence while a displayed run is non-terminal
  quietStartMs: 2000,    // first quiet interval ...
  quietMaxMs: 10000,     // ... doubling to this cap while nothing changes
  errorMaxMs: 30000,     // failure backoff cap
  disabledMs: 120000,    // re-probe cadence while the server flag is off
  timeoutMs: 8000,       // per-request abort
  pageLimit: 100,        // server maximum
  maxRunsPerProject: 300,
  hintDebounceMs: 150,
  lateTimerMs: 5000,     // a timer this late means the page was suspended
  staleAfterFailures: 2,
  maxIdLength: 128,
};

const _isInt = (v) => Number.isInteger(v) && v >= 0;

function _deepFreeze(o) {
  if (o && typeof o === 'object' && !Object.isFrozen(o)) {
    Object.freeze(o);
    for (const k of Object.keys(o)) _deepFreeze(o[k]);
  }
  return o;
}

function _validSnapshot(r, maxId) {
  return !!r && typeof r === 'object' && typeof r.run_id === 'string'
    && r.run_id.length > 0 && r.run_id.length <= maxId
    && _isInt(r.seq) && typeof r.state === 'string';
}

const _byNewest = (a, b) => {
  const ca = String(a.created_at || ''), cb = String(b.created_at || '');
  if (ca !== cb) return ca < cb ? 1 : -1;
  return a.run_id < b.run_id ? 1 : (a.run_id > b.run_id ? -1 : 0);
};

export function createTestRunData(env = {}) {
  const cfg = { ...DEFAULTS };
  const g = globalThis;
  // Everything the coordinator touches comes through `E`, resolved lazily so a
  // test can inject fakes and the browser build just uses the real globals.
  const E = {
    fetch: (...a) => (env.fetch || g.fetch).apply(g, a),
    now: () => (env.now ? env.now() : Date.now()),
    setTimeout: (f, ms) => (env.setTimeout || g.setTimeout)(f, ms),
    clearTimeout: (t) => (env.clearTimeout || g.clearTimeout)(t),
    doc: () => (env.document !== undefined ? env.document : g.document),
    win: () => (env.window !== undefined ? env.window : g.window),
    base: () => (env.base !== undefined ? env.base : (typeof g.API_BASE === 'string' ? g.API_BASE : '')),
    abort: () => (env.AbortController ? new env.AbortController() : (g.AbortController ? new g.AbortController() : null)),
  };

  const projects = new Map();    // projectId -> project record
  const stats = { requests: 0, hints: 0, hintsAccepted: 0, rejectedRows: 0 };
  let disabled = false;          // server flag off (bare 404)
  let timer = null, timerAt = 0, timerExpectedAt = 0;
  let listening = false;
  let nextSubId = 1;

  const hidden = () => {
    const d = E.doc();
    return !!d && (d.hidden === true || d.visibilityState === 'hidden');
  };

  function proj(id) {
    let p = projects.get(id);
    if (!p) {
      p = { id, runs: new Map(), subs: new Map(), inflight: null, refetch: false,
            failures: 0, quietStep: 0, dueAt: 0, lastOkAt: null, lastError: null,
            restricted: false, missing: false, waiters: [] };
      projects.set(id, p);
    }
    return p;
  }

  const runActive = (r) => !TERMINAL.has(r.state);
  const matches = (sub, r) => sub.sessionId == null || r.owner_session_id === sub.sessionId;

  function statusOf(p) {
    let state;
    if (p.restricted) state = 'restricted';
    else if (p.missing) state = 'missing';
    else if (disabled) state = 'disabled';
    else if (p.lastOkAt == null) state = p.failures > 0 ? 'error' : 'loading';
    else if (p.failures >= cfg.staleAfterFailures) state = 'stale';
    else state = 'ok';
    return { state, lastOkAt: p.lastOkAt, failures: p.failures, error: p.lastError };
  }

  function viewFor(p, sub) {
    const runs = [];
    for (const r of p.runs.values()) if (matches(sub, r)) runs.push(r);
    runs.sort(_byNewest);
    return { projectId: p.id, sessionId: sub.sessionId, runs, status: statusOf(p) };
  }

  function signature(view) {
    const s = view.status;
    return view.runs.map((r) => `${r.run_id}:${r.seq}:${r.state}`).join('|')
      + `#${s.state}:${s.failures}`;
  }

  function notify(p, force) {
    for (const sub of [...p.subs.values()]) {
      const view = viewFor(p, sub);
      const sig = signature(view);
      if (!force && sig === sub.sig) continue;
      sub.sig = sig;
      try { sub.cb(view); } catch (e) { console.error('[test-run-data] subscriber failed', e); }
    }
  }

  function hasActive(p) {
    for (const sub of p.subs.values()) {
      for (const r of p.runs.values()) if (matches(sub, r) && runActive(r)) return true;
    }
    return false;
  }

  function intervalFor(p) {
    if (disabled) return cfg.disabledMs;
    if (p.failures > 0) return Math.min(cfg.errorMaxMs, cfg.activeMs * 2 ** Math.min(p.failures, 8));
    if (hasActive(p)) return cfg.activeMs;
    return Math.min(cfg.quietMaxMs, cfg.quietStartMs * 2 ** Math.min(p.quietStep, 8));
  }

  // ── merging ────────────────────────────────────────────────────────────────
  function merge(p, rows) {
    let changed = false;
    for (const row of rows) {
      if (!_validSnapshot(row, cfg.maxIdLength)) { stats.rejectedRows++; continue; }
      const cur = p.runs.get(row.run_id);
      if (!cur || row.seq > cur.seq
          || (row.seq === cur.seq && TERMINAL.has(row.state) && !TERMINAL.has(cur.state))) {
        p.runs.set(row.run_id, _deepFreeze(JSON.parse(JSON.stringify(row))));
        changed = true;
      }
    }
    if (p.runs.size > cfg.maxRunsPerProject) {
      const evictable = [...p.runs.values()].filter((r) => TERMINAL.has(r.state)).sort(_byNewest).reverse();
      while (p.runs.size > cfg.maxRunsPerProject && evictable.length) p.runs.delete(evictable.shift().run_id);
    }
    return changed;
  }

  // ── transport ──────────────────────────────────────────────────────────────
  // Resolves {kind:'ok',rows} | {kind:'disabled'|'missing'|'restricted'|'bad'|'fail'}.
  async function fetchPage(url) {
    const ac = E.abort();
    const to = E.setTimeout(() => { try { ac && ac.abort(); } catch (e) { /* already settled */ } }, cfg.timeoutMs);
    stats.requests++;
    try {
      const resp = await E.fetch(url, { signal: ac ? ac.signal : undefined, cache: 'no-store',
                                        headers: { Accept: 'application/json' } });
      if (resp.status === 404) {
        let body = null;
        try { body = await resp.json(); } catch (e) { body = null; }
        return body && typeof body.error === 'string' ? { kind: 'missing' } : { kind: 'disabled' };
      }
      if (resp.status === 403) return { kind: 'restricted' };
      if (!resp.ok) return { kind: 'fail', error: `HTTP ${resp.status}` };
      let body;
      try { body = await resp.json(); } catch (e) { return { kind: 'fail', error: 'bad JSON' }; }
      if (!body || !Array.isArray(body.runs)) return { kind: 'bad', error: 'unexpected response shape' };
      return { kind: 'ok', rows: body.runs };
    } catch (e) {
      return { kind: 'fail', error: (e && e.name === 'AbortError') ? 'timeout' : 'network' };
    } finally {
      E.clearTimeout(to);
    }
  }

  async function refreshProject(p) {
    if (p.inflight) { p.refetch = true; return p.inflight; }
    const waiters = p.waiters; p.waiters = [];   // callers registered BEFORE this fetch started
    p.inflight = (async () => {
      const base = `${E.base()}/api/project/${encodeURIComponent(p.id)}/test-runs`;
      let changed = false, outcome = 'ok', error = null;
      const page = await fetchPage(`${base}?limit=${cfg.pageLimit}`);
      if (page.kind === 'ok') {
        changed = merge(p, page.rows);
        // A full page can hide an older run owned by a subscribed session:
        // ask for that session's own page instead of assuming it is absent.
        if (page.rows.length >= cfg.pageLimit) {
          for (const sid of new Set([...p.subs.values()].map((s) => s.sessionId).filter(Boolean))) {
            const sp = await fetchPage(`${base}?limit=${cfg.pageLimit}&session=${encodeURIComponent(sid)}`);
            if (sp.kind === 'ok' && merge(p, sp.rows)) changed = true;
          }
        }
      } else { outcome = page.kind; error = page.error || null; }

      const before = signature({ runs: [], status: statusOf(p) });
      if (outcome === 'ok') {
        const wasDisabled = disabled;
        disabled = false; p.restricted = false; p.missing = false;
        p.failures = 0; p.lastError = null; p.lastOkAt = E.now();
        p.quietStep = changed ? 0 : p.quietStep + 1;
        if (wasDisabled) changed = true;
      } else if (outcome === 'disabled') {
        disabled = true; p.lastError = null;
      } else if (outcome === 'missing') {
        p.missing = true; p.lastError = null;
      } else if (outcome === 'restricted') {
        p.restricted = true; p.lastError = null;
      } else {
        p.failures++; p.lastError = error;
      }
      const statusChanged = before !== signature({ runs: [], status: statusOf(p) });
      p.inflight = null;
      notify(p, false);
      const again = p.refetch; p.refetch = false;
      p.dueAt = E.now() + (again ? 0 : intervalFor(p));
      schedule();
      for (const w of waiters) w({ outcome, changed: changed || statusChanged });
      return outcome;
    })();
    return p.inflight;
  }

  // ── scheduling: one timer for every project ────────────────────────────────
  function pollable(p) {
    return p.subs.size > 0 && !p.restricted && !p.missing;
  }

  function schedule() {
    if (timer != null) { E.clearTimeout(timer); timer = null; }
    if (hidden()) return;
    let next = Infinity;
    for (const p of projects.values()) if (pollable(p) && !p.inflight) next = Math.min(next, p.dueAt);
    if (next === Infinity) return;
    const delay = Math.max(25, next - E.now());
    timerExpectedAt = E.now() + delay;
    timer = E.setTimeout(tick, delay);
  }

  function tick() {
    timer = null;
    const now = E.now();
    if (now - timerExpectedAt > cfg.lateTimerMs) {
      // The timer fired long after it was due: the page was suspended. Treat as a resume.
      for (const p of projects.values()) { p.quietStep = 0; p.failures = 0; }
    }
    if (hidden()) return;
    for (const p of projects.values()) {
      if (pollable(p) && !p.inflight && p.dueAt <= now) refreshProject(p);
    }
    schedule();
  }

  function wake(reason) {
    // visible / focus / pageshow / resume / online: read server truth now.
    if (hidden() || projects.size === 0) return;
    const now = E.now();
    for (const p of projects.values()) {
      if (!pollable(p)) continue;
      p.quietStep = 0; p.failures = 0;
      p.dueAt = now;
    }
    if (disabled) disabled = false; // re-probe at once; a 404 sets it again
    schedule();
  }

  const onVisible = () => wake('visibility');
  const onWake = () => wake('wake');
  const EVENTS = [['doc', 'visibilitychange', onVisible], ['doc', 'resume', onWake],
                  ['win', 'focus', onWake], ['win', 'pageshow', onWake], ['win', 'online', onWake]];

  function listen(on) {
    if (on === listening) return;
    listening = on;
    for (const [where, name, fn] of EVENTS) {
      const t = where === 'doc' ? E.doc() : E.win();
      if (t && t.addEventListener) (on ? t.addEventListener : t.removeEventListener).call(t, name, fn);
    }
  }

  // ── public API ─────────────────────────────────────────────────────────────
  /**
   * Subscribe to a project's runs, optionally narrowed to one chat session.
   * `cb(view)` fires (async) at once with whatever is cached, then on every
   * change. View: {projectId, sessionId, runs:[frozen snapshot], status:{state,
   * lastOkAt, failures, error}}. Returns an unsubscribe function.
   */
  function subscribe(projectId, sessionId, cb) {
    if (typeof projectId !== 'string' || !projectId || typeof cb !== 'function') {
      throw new TypeError('subscribe(projectId, sessionId|null, cb)');
    }
    const p = proj(projectId);
    const id = nextSubId++;
    p.subs.set(id, { sessionId: sessionId || null, cb, sig: null });
    // A re-subscribe (modal rebuild) paints from cache and refetches immediately.
    p.restricted = false; p.missing = false;
    p.dueAt = E.now();
    listen(true);
    Promise.resolve().then(() => {
      const sub = p.subs.get(id);
      if (!sub) return;
      sub.sig = signature(viewFor(p, sub));
      try { sub.cb(viewFor(p, sub)); } catch (e) { console.error('[test-run-data] subscriber failed', e); }
    });
    schedule();
    let done = false;
    return function unsubscribe() {
      if (done) return;
      done = true;
      p.subs.delete(id);
      if (p.subs.size === 0) {
        p.refetch = false;
        const w = p.waiters; p.waiters = [];
        for (const f of w) f({ outcome: 'unsubscribed', changed: false });
      }
      if ([...projects.values()].every((q) => q.subs.size === 0)) {
        listen(false);
        if (timer != null) { E.clearTimeout(timer); timer = null; }
      } else schedule();
    };
  }

  /** Read server truth for a project now. Resolves {outcome, changed}. */
  function refresh(projectId) {
    const p = projects.get(projectId);
    if (!p || p.subs.size === 0) return Promise.resolve({ outcome: 'no-subscribers', changed: false });
    return new Promise((resolve) => {
      p.waiters.push(resolve);
      p.quietStep = 0;
      if (p.inflight) p.refetch = true; else refreshProject(p);
    });
  }

  /**
   * SSE hint ({run_id, seq}) from an open chat stream. Never writes data: it
   * only pulls the next poll forward, and only when the hint is news.
   */
  function hint(projectId, info) {
    stats.hints++;
    const p = projects.get(projectId);
    if (!p || p.subs.size === 0 || !info || typeof info !== 'object') return false;
    const { run_id: runId, seq } = info;
    if (typeof runId !== 'string' || !runId || runId.length > cfg.maxIdLength || !_isInt(seq)) return false;
    const cur = p.runs.get(runId);
    if (cur && seq <= cur.seq) return false;           // already have it
    stats.hintsAccepted++;
    p.quietStep = 0;
    if (p.inflight) { p.refetch = true; return true; }  // that response may predate the hint
    p.dueAt = Math.min(p.dueAt, E.now() + cfg.hintDebounceMs);
    schedule();
    return true;
  }

  function list(projectId, sessionId) {
    const p = projects.get(projectId);
    if (!p) return [];
    return viewFor(p, { sessionId: sessionId || null }).runs;
  }
  const get = (projectId, runId) => (projects.get(projectId) || { runs: new Map() }).runs.get(runId) || null;

  return {
    subscribe, refresh, hint, list, get,
    status: (projectId) => (projects.has(projectId) ? statusOf(projects.get(projectId)) : null),
    isDisabled: () => disabled,
    stats: () => ({ ...stats, projects: projects.size, timerArmed: timer != null }),
    configure: (o) => Object.assign(cfg, o),
    TERMINAL_STATES: [...TERMINAL],
  };
}

const _shared = createTestRunData();
if (typeof window !== 'undefined') window.TestRunData = _shared;
export default _shared;

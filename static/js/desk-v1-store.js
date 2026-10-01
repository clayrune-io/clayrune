// Desk v1 (MC-1021 R1-W, slice S0) — the data store every v1 surface reads.
// Window-bridged module, no `import` (same ground rule as the rest of
// desk-v1-*.js). docs/desk_v1/R1W_WIRING_PLAN.md §3.0.
//
// Before this file every surface called `window.DeskV1Fixtures` straight from
// its own `_fx()`. They now call `DeskV1Store.state()`, which returns the same
// object SHAPE, so a surface changes one line and nothing else:
//
//   desk_v1_live ON   state() is hydrated from `GET /api/desk/workspace` (M1).
//                     Until a slice wires a surface to a route the keys it
//                     reads that M1 does not carry stay empty, never invented.
//   desk_v1_live OFF  There is no live data. The fixture file is not shipped
//                     (it lives in tools/smoke/fixtures/); the smoke harness
//                     seeds `window.DeskV1Fixtures` before the page loads and
//                     state() returns that object itself, so a smoke that
//                     mutates it sees its own writes. In production nothing
//                     seeds it, state() is empty and `gate()` says so.
//
// PRODUCTION NEVER FALLS BACK TO DEMO DATA. A failed live load leaves
// `gate()` returning an error the shell paints (with Try again); it does not
// quietly hand the surfaces fixtures. Substitution is a lie.
//
// `run()` is the write path: optimistic apply, the route call, and on a
// refusal a rollback plus a toast carrying the server's own error text. It
// wraps DeskV1Kit.commandBus, so a successful write still gets its Undo toast.
(function () {
  // Collection keys the surfaces index into. An empty state has to carry the
  // right TYPE for each (an array where a surface calls .find, an object where
  // it indexes by id) or the first read throws instead of rendering empty.
  const ARRAY_KEYS = ['projects', 'campaigns', 'channels', 'families', 'conversations',
    'existingArticles', 'recentAssets', 'ledger'];
  const OBJECT_KEYS = ['materialLibrary', 'studio', 'renderBudget', 'results', 'reviewDetail',
    'videoDetail', 'workerHeartbeat', 'campaignSuggestions', 'contentPreview', 'calendarSchedule',
    'conversationDetail', 'conversationCoverageGaps', 'resultsInsight', 'proposedExtras', 'retros',
    'suggestReply'];

  function _emptyState() {
    const s = { playbook: { findings: [], rejections: [] } };
    ARRAY_KEYS.forEach((k) => { s[k] = []; });
    OBJECT_KEYS.forEach((k) => { s[k] = {}; });
    return s;
  }

  let _phase = 'idle';          // idle | loading | ready | error (live mode only)
  let _error = null;
  let _live = null;             // hydrated state once a live load succeeds
  const _empty = _emptyState();   // stable identity so callers can hold on to it
  let _inflight = null;

  function live() {
    const cfg = (typeof _globalConfig !== 'undefined' && _globalConfig) || {};
    return !!cfg.desk_v1_live;
  }

  function state() {
    if (live()) return _live || _empty;
    return window.DeskV1Fixtures || _empty;
  }

  // What the shell should paint INSTEAD of a surface, or null when the surfaces
  // can render. `off` only fires with no seed: production, flag off.
  function gate() {
    if (!live()) {
      return window.DeskV1Fixtures ? null : {
        kind: 'off',
        message: 'Desk v1 has no live data yet. Turn on "desk_v1_live" to use your real workspace; demo data is not shipped.',
      };
    }
    if (_phase === 'ready') return null;
    if (_phase === 'error') return { kind: 'error', message: `Could not load the Desk workspace: ${_error}` };
    return { kind: 'loading', message: 'Loading your workspace…' };
  }

  // M1 → the object shape `_fx()` has always returned. `channels` is the
  // workspace's accounts, `families` its pieces (none until slice S4 builds the
  // piece store, so [] here is the real answer, not a placeholder).
  function _fromWorkspace(ws) {
    const s = _emptyState();
    s.projects = Array.isArray(ws.projects) ? ws.projects : [];
    s.campaigns = Array.isArray(ws.campaigns) ? ws.campaigns : [];
    s.channels = Array.isArray(ws.accounts) ? ws.accounts : [];
    s.pieces = Array.isArray(ws.pieces) ? ws.pieces : [];
    return s;
  }

  // JSON in, JSON out, and a thrown Error carrying the SERVER's message so a
  // caller (run() below) can show the user why instead of "failed".
  async function api(method, url, body) {
    const init = { method, headers: {} };
    if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
    let res;
    try { res = await fetch(url, init); } catch (e) { throw new Error(`network error: ${e && e.message ? e.message : e}`); }
    let json = null;
    try { json = await res.json(); } catch (_) { /* a non-JSON body: fall through to the status text */ }
    if (!res.ok) {
      throw new Error((json && (json.error || json.message)) || `HTTP ${res.status}`);
    }
    return json;
  }

  function load(opts) {
    if (!live()) return Promise.resolve(gate());
    if (_inflight && !(opts && opts.force)) return _inflight;
    _phase = 'loading';
    _error = null;
    const p = api('GET', '/api/desk/workspace').then((ws) => {
      _live = _fromWorkspace(ws || {});
      _phase = 'ready';
    }).catch((e) => {
      _live = null;
      _phase = 'error';
      _error = e && e.message ? e.message : String(e);
    }).then(() => { if (_inflight === p) _inflight = null; return gate(); });
    _inflight = p;
    return p;
  }

  function _toast(msg) {
    if (window.DeskV1Kit && typeof window.DeskV1Kit.toast === 'function') window.DeskV1Kit.toast(msg);
    else if (typeof window.showToast === 'function') window.showToast(msg, 5000);
  }

  // cmd: { label, request, apply, unapply, undoRequest?, repaint? }
  //   apply()      the optimistic local change, run before the request.
  //   request()    the route call (use api()); a rejection is a refusal.
  //   unapply()    its inverse: runs on a refusal, and on the toast's Undo.
  //   undoRequest(result)  the route call that reverses the write, if one
  //                exists; if it refuses, the local change is put back.
  //   repaint()    optional, called after any rollback so the surface redraws.
  // Resolves { ok:true, result } or { ok:false, error } — never rejects, so a
  // click handler can `await` it without its own try/catch.
  async function run(cmd) {
    if (!cmd || typeof cmd.apply !== 'function' || typeof cmd.unapply !== 'function'
        || typeof cmd.request !== 'function') {
      throw new Error('DeskV1Store.run: cmd needs apply(), unapply() and request()');
    }
    const repaint = () => { if (typeof cmd.repaint === 'function') cmd.repaint(); };
    cmd.apply();
    let result;
    try {
      result = await cmd.request();
    } catch (e) {
      const msg = e && e.message ? e.message : String(e);
      cmd.unapply();
      _toast(`${cmd.label || 'That change'} was not saved: ${msg}`);
      repaint();
      return { ok: false, error: msg };
    }
    const bus = window.DeskV1Kit && window.DeskV1Kit.commandBus;
    const undo = async () => {
      cmd.unapply();
      repaint();
      if (typeof cmd.undoRequest !== 'function') return;
      try { await cmd.undoRequest(result); } catch (e) {
        cmd.apply();
        _toast(`Could not undo ${cmd.label || 'that change'}: ${e && e.message ? e.message : e}`);
        repaint();
      }
    };
    // The change has already happened (apply + request above), so the bus's own
    // `do` is a no-op: it is here for the Undo toast, the history and the
    // announcement, the same three things every other v1 command gets.
    if (bus) bus.run({ label: cmd.label, do: () => {}, undo });
    return { ok: true, result };
  }

  window.DeskV1Store = { state, gate, load, run, api, live };
})();

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
//   desk_v1_live OFF  state() is `window.DeskV1Fixtures` when something put it
//                     there, else empty and gate() says so. Production pages
//                     never have it (S10: the fixtures live in tools/smoke/
//                     fixtures/ and only the smoke harness injects them), so
//                     OFF is an explicit "nothing to show" state. With the
//                     harness's fixtures present a smoke that mutates them
//                     sees its own writes, and demo() is true so the shell
//                     shows "Demo data - not your workspace" on every page.
//
// DEMO IS NEVER A FALLBACK. Live ON never reads the fixtures: a failed live
// load leaves `gate()` returning an error the shell paints (with Try again),
// it does not quietly hand the surfaces demo data. Substitution is a lie.
//
// `run()` is the write path: optimistic apply, the route call, and on a
// refusal a rollback plus a toast carrying the server's own error text. It
// wraps DeskV1Kit.commandBus, so a successful write is undoable from the header's Undo.
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

  // True only in demo mode (flag off, fixtures present): the shell's banner.
  function demo() { return !live() && !!window.DeskV1Fixtures; }

  // What the shell should paint INSTEAD of a surface, or null when the surfaces
  // can render. `off` fires whenever the flag is off and no fixtures were
  // injected, which is every production page since S10 (only the smoke
  // harness puts `window.DeskV1Fixtures` there).
  function gate() {
    if (!live()) {
      return window.DeskV1Fixtures ? null : {
        kind: 'off',
        message: 'The Desk workspace is switched off (desk_v1_live is off) and this build ships no demo data, so there is nothing to show. Turn desk_v1_live on in Settings.',
      };
    }
    if (_phase === 'ready') return null;
    if (_phase === 'error') return { kind: 'error', message: `Could not load the Desk workspace: ${_error}` };
    return { kind: 'loading', message: 'Loading your workspace…' };
  }

  // M1 → the object shape `_fx()` has always returned. `channels` is the
  // workspace's accounts, `families` its pieces (the v1 piece store, slice S4;
  // `pieces` is the same array under the name M1 gives it).
  function _fromWorkspace(ws) {
    const s = _emptyState();
    s.projects = Array.isArray(ws.projects) ? ws.projects : [];
    s.campaigns = Array.isArray(ws.campaigns) ? ws.campaigns : [];
    s.channels = Array.isArray(ws.accounts) ? ws.accounts : [];
    s.pieces = Array.isArray(ws.pieces) ? ws.pieces : [];
    s.families = s.pieces;
    return s;
  }

  // JSON in, JSON out, and a thrown Error carrying the SERVER's message so a
  // caller (run() below) can show the user why instead of "failed". A FormData
  // body (a file upload) goes as multipart: no Content-Type, the browser sets
  // the boundary.
  async function api(method, url, body) {
    const init = { method, headers: {} };
    if (typeof FormData !== 'undefined' && body instanceof FormData) init.body = body;
    else if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
    let res;
    try { res = await fetch(url, init); } catch (e) { throw new Error(`network error: ${e && e.message ? e.message : e}`); }
    let json = null;
    try { json = await res.json(); } catch (_) { /* a non-JSON body: fall through to the status text */ }
    if (!res.ok) {
      const err = new Error((json && (json.error || json.message)) || `HTTP ${res.status}`);
      err.status = res.status;
      throw err;
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
  //   destructive  optional: a delete / remove / archive / skip. Only these pulse
  //                the Undo button; every change is undone from the Desk
  //                header's Undo (or Ctrl/Cmd+Z). No popup.
  //   irreversible optional: the route has no inverse (a finding's Re-confirm,
  //                a project pause that already cascaded...). A live change then
  //                gets a plain confirmation toast, NOT an Undo that would only
  //                un-draw it locally while the server kept it. May be a
  //                function (result) => message for a toast that reads the
  //                server's answer.
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
    if (cmd.irreversible) {
      _toast(typeof cmd.irreversible === 'function' ? cmd.irreversible(result) : (cmd.label || 'Saved'));
      return { ok: true, result };
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
    if (bus) bus.run({ label: cmd.label, do: () => {}, undo, destructive: !!cmd.destructive });
    return { ok: true, result };
  }

  // write(cmd): the one entry a surface uses for a change, same cmd as run().
  //   live ON   -> run(): apply, the route call, rollback + toast on a refusal.
  //   live OFF  -> DEMO: apply and the Undo toast through the command bus, and
  //                NO route call: demo data must never reach a real backend.
  // Resolves { ok, result?, error? } in both modes.
  async function write(cmd) {
    if (live()) return run(cmd);
    if (!cmd || typeof cmd.apply !== 'function' || typeof cmd.unapply !== 'function') {
      throw new Error('DeskV1Store.write: cmd needs apply() and unapply()');
    }
    cmd.apply();
    const bus = window.DeskV1Kit && window.DeskV1Kit.commandBus;
    const undo = () => { cmd.unapply(); if (typeof cmd.repaint === 'function') cmd.repaint(); };
    if (bus) bus.run({ label: cmd.label, do: () => {}, undo, destructive: !!cmd.destructive });
    return { ok: true, result: null };
  }

  // ── Storyboards (R1-W S9a = MC-1020) ──────────────────────────────────────
  // A storyboard is persisted per OWNER, `{kind:'piece'|'studio', id}`: a video
  // piece (shared by its versions) or a standalone Studio item. The server keeps
  // the scene list whole and guards every write with a `rev` counter, so this
  // side remembers the rev it last read or wrote per owner and sends it back; a
  // stale one is a 409. Live mode only: demo mode never calls any of this.
  //
  // CLIENT scene  {id, label, line, durationSec, edited, picture, thumb, source}
  // SERVER scene  {id, label, line, duration_sec, picture:{path,title,src}|null, edited}
  // (`thumb` is the picture's src, `source` the demo capture label: not stored.)
  const _sbRev = {};
  const _sbChain = {};
  function _sbKey(owner) { return `${owner.kind}:${owner.id}`; }
  function _sbBase(owner) {
    const id = encodeURIComponent(owner.id);
    return owner.kind === 'studio' ? `/api/desk/studio/${id}/storyboard` : `/api/desk/pieces/${id}/storyboard`;
  }
  function _sceneIn(s) {
    const pic = s.picture || null;
    return { id: s.id, label: s.label, line: s.line || '', durationSec: s.duration_sec, edited: !!s.edited,
      picture: pic, thumb: (pic && pic.src) || '', source: '' };
  }
  function _sceneOut(s) {
    return { id: s.id, label: s.label, line: s.line || '', duration_sec: s.durationSec, edited: !!s.edited,
      picture: s.picture ? { path: s.picture.path, title: s.picture.title } : null };
  }
  // Replace what `detail` holds with the server's board (and remember its rev).
  function _sbAdopt(owner, detail, board) {
    _sbRev[_sbKey(owner)] = board.rev || 0;
    detail.scenes = (board.scenes || []).map(_sceneIn);
    detail.pendingEdits = (board.pending_edits || []).map((p) => ({ id: p.id, label: p.label }));
    detail.story = board.story || '';
    return board;
  }
  async function sbLoad(owner, detail) {
    return _sbAdopt(owner, detail, await api('GET', _sbBase(owner)));
  }
  // The whole list, serialized per owner so two quick changes never race on one
  // rev. The body is built when the request RUNS, so a queued save carries the
  // newest state. `extra` rides along (a Studio item's title).
  function sbSave(owner, detail, extra) {
    const key = _sbKey(owner);
    const run = async () => {
      const body = Object.assign({
        rev: _sbRev[key] || 0,
        scenes: (detail.scenes || []).filter((s) => !s.placeholder).map(_sceneOut),
        pending_edits: (detail.pendingEdits || []).map((p) => ({ id: p.id, label: p.label })),
      }, typeof detail.story === 'string' ? { story: detail.story } : {}, extra || {});
      const board = await api('PUT', _sbBase(owner), body);
      _sbRev[key] = board.rev;
      return board;
    };
    const next = (_sbChain[key] || Promise.resolve()).then(run, run);
    _sbChain[key] = next.catch(() => {});
    return next;
  }
  // One scene picture up to the material library; the AssetRef comes back with
  // its /api/serve-image src. The scene is changed by the caller's command.
  function sbUploadPicture(owner, file) {
    const fd = new FormData();
    fd.append('file', file, file.name);
    return api('POST', `${_sbBase(owner)}/pictures`, fd);
  }
  // c: { owner, detail, label, do, undo, repaint, extra? }
  //   do()/undo()  the local change and its inverse, each repainting (the same
  //                pair the demo path hands commandBus).
  //   repaint()    redraw after a resync.
  // do() runs, then the whole list is PUT. A refusal brings the page back to
  // the SERVER's list (a 409 means someone else changed it) and says so; if
  // even that read fails the change is just un-done locally. A good write gets
  // the bus's Undo toast, and the Undo is another PUT of the earlier list.
  async function _sbSync(c, revert) {
    try { await sbSave(c.owner, c.detail, c.extra ? c.extra() : undefined); return true; } catch (e) {
      const msg = e && e.message ? e.message : String(e);
      let synced = false;
      try { await sbLoad(c.owner, c.detail); synced = true; } catch (_) { /* server unreachable */ }
      if (!synced) revert();
      _toast(e && e.status === 409
        ? `${c.label} was not saved: this storyboard was changed somewhere else. Showing the latest version; make the change again.`
        : `${c.label} was not saved: ${msg}`);
      if (typeof c.repaint === 'function') c.repaint();
      return false;
    }
  }
  async function sbCommand(c) {
    c.do();
    if (!(await _sbSync(c, c.undo))) return { ok: false };
    const bus = window.DeskV1Kit && window.DeskV1Kit.commandBus;
    const undo = async () => {
      c.undo();
      await _sbSync(Object.assign({}, c, { label: `Undoing “${c.label}”` }), c.do);
    };
    if (bus) bus.run({ label: c.label, do: () => {}, undo, destructive: !!c.destructive });
    return { ok: true };
  }

  window.DeskV1Store = { state, gate, load, run, write, api, live, demo,
    storyboard: { load: sbLoad, save: sbSave, uploadPicture: sbUploadPicture, command: sbCommand } };
})();

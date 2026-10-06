// Desk v1 — Connections: the single-view Connect wizard SHELL (MC-1062 ticket 02,
// docs/desk_v1/CONNECT_FLOW_SIMPLIFY.md sections 2, 7 and 10). Window-bridged module, no
// `import` (ground rule 1). The wizard shows ONE screen at a time:
//
//     Service > Connection > Setup > Permissions > Review > Result
//
// This file is only the frame and the rules every screen shares. It owns no screen's body:
// each screen registers itself from its own file (`registerScreen`), so a new screen, a new
// branch or a new step body never grows this one. Service and Connection are in
// desk-v1-connect-wizard-pick.js; Setup, Permissions, Review and Result screens arrive with
// tickets 04 and 08 to 13. A step no screen has claimed for the chosen branch says so, with
// Back and nothing that looks like progress.
//
// WHAT THE FRAME GUARANTEES
//   * One active branch. The selection is (service, type, variant, account); the screen shown
//     for a step is the first registered one whose `match(sel, info)` is true.
//   * Step state is separate from DOM-owned secrets. State holds only non-secret drafts
//     (`setDraft`). A secret lives in an <input> inside a node the screen asks the frame to
//     keep (`host`): the frame re-attaches that node after every repaint and after Back, and
//     never reads its value. The value is read by the screen, once, into its own request.
//   * Back keeps compatible data. Changing the service, the type or the account drops every
//     draft, kept node, approval and owned operation scoped at or below the level that
//     changed (`SCOPES`): one account's credentials are never carried to another.
//   * Close (`close()`, called through DeskV1ConnectFlow.reset() when the panel is closed or
//     another tile is picked) empties every kept input and calls the cancel each screen gave
//     `own()`, which is that operation's existing cancel path (for a held sign-in,
//     DeskV1ConnectHeld.cancel).
//   * Continue never writes. The frame's own Continue calls a read-only endpoint at most; a
//     screen's primary action is its own, and only a Review screen's Save may write.
//   * One <form> per screen, one primary action. Screens render no <form> of their own and
//     no second Save; there is one Details disclosure and it holds no other disclosure.
//
// ACTIVATION. `enabled()` is false until ticket 14 turns the wizard on with `setEnabled`;
// until then desk-v1-connect-flow.js delegates here only when it is, and the old flow runs.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const STEPS = [['service', 'Service'], ['connection', 'Connection'], ['setup', 'Setup'], ['permissions', 'Permissions'], ['review', 'Review'], ['result', 'Result']];
  const SCOPES = { service: 0, type: 1, account: 2 };       // a change at a level drops everything scoped at that level or a higher number
  const PAGE_SIZE = 4;                                       // alternatives per screen (the design's cap)

  const _screens = [];
  let _enabled = false;
  let _ctx = null;                                           // the Connections panel's ctx, kept for async answers

  function _fresh() {
    return { step: 'service', error: '', hint: '', info: null, running: false,
      sel: { service: null, type: null, variant: null, account: null },
      drafts: {}, approvals: {}, hosts: {}, owned: {}, pages: {}, refocus: null, focusTitle: false, detailsOpen: false };
  }
  let S = _fresh();

  function enabled() { return _enabled; }
  function setEnabled(on) { _enabled = !!on; if (!_enabled) close(); }
  function active() { return _enabled; }                     // the wizard owns the whole Add service panel, its own first screen included

  // ── screens ─────────────────────────────────────────────────────────────
  // def: { id, step, match?(sel, info), title(api), copy(api), body(api), details?(api), bind?(root, api),
  //        primary?(api) -> { label, disabled?, run?() -> true|false|Promise }, substep?(api) -> [n, of], discard?(why) }
  function registerScreen(def) {
    if (!def || !def.id || !STEPS.some((s) => s[0] === def.step)) throw new Error('registerScreen: a screen needs an id and a known step');
    const at = _screens.findIndex((s) => s.id === def.id);
    if (at >= 0) _screens[at] = def; else _screens.push(def);
  }
  function _screenFor(step) {
    return _screens.find((d) => {
      if (d.step !== step) return false;
      try { return !d.match || !!d.match(_selCopy(), S.info); } catch (e) { console.warn('[connect-wizard] match failed for ' + d.id + ': ' + e); return false; }
    }) || null;
  }

  // ── selection, scopes and what a change drops ───────────────────────────
  function _selCopy() { return Object.assign({}, S.sel); }
  function _stepIndex(step) { return STEPS.findIndex((s) => s[0] === step); }

  function _cancelOwned(key) {
    const o = S.owned[key];
    delete S.owned[key];
    if (!o) return;
    try { const r = o.cancel(); if (r && r.catch) r.catch((e) => console.warn('[connect-wizard] cancel of ' + key + ' failed: ' + e)); }
    catch (e) { console.warn('[connect-wizard] cancel of ' + key + ' failed: ' + e); }
  }
  function _emptyNode(node) {
    node.querySelectorAll('input, textarea').forEach((i) => { if (i.type !== 'radio' && i.type !== 'checkbox') i.value = ''; });
  }

  // Drop everything scoped at `level` or deeper. The held operations are cancelled first.
  function _invalidate(level, why) {
    Object.keys(S.owned).forEach((k) => { if (SCOPES[S.owned[k].scope] >= level) _cancelOwned(k); });
    Object.keys(S.hosts).forEach((k) => { if (SCOPES[S.hosts[k].scope] >= level) { _emptyNode(S.hosts[k].node); delete S.hosts[k]; } });
    Object.keys(S.drafts).forEach((k) => { if (SCOPES[S.drafts[k].scope] >= level) delete S.drafts[k]; });
    Object.keys(S.approvals).forEach((k) => { if (SCOPES[S.approvals[k].scope] >= level) delete S.approvals[k]; });
    _screens.forEach((d) => { if (d.discard) { try { d.discard(why || 'change'); } catch (e) { console.warn('[connect-wizard] discard failed for ' + d.id + ': ' + e); } } });
  }

  // Change part of the selection. `patch` keys: service, type, variant, account. The first level
  // that actually changes drops what depends on it, and the keys below it are cleared.
  function select(patch) {
    const order = [['service', 0], ['type', 1], ['variant', 1], ['account', 2]];
    let level = null;
    order.forEach(([k, lv]) => { if (k in patch && patch[k] !== S.sel[k] && (level === null || lv < level)) level = lv; });
    if (level === null) return;
    _invalidate(level, level === 0 ? 'service' : level === 1 ? 'type' : 'account');
    if ('service' in patch && patch.service !== S.sel.service) { S.sel = { service: patch.service, type: null, variant: null, account: null }; }
    if ('type' in patch && patch.type !== S.sel.type) { S.sel.type = patch.type; S.sel.variant = null; }
    if ('variant' in patch) S.sel.variant = patch.variant;
    if ('account' in patch) S.sel.account = patch.account;
  }

  // ── what a screen may keep ──────────────────────────────────────────────
  // A screen's api. Every method keys its data by the screen's id, so two screens never share a slot.
  function _api(def) {
    const id = def ? def.id : '';
    return {
      ctx: _ctx, sel: _selCopy(), info: S.info,
      repaint: () => _repaint(),
      error: (msg, hint) => { S.error = msg || ''; S.hint = hint || ''; _repaint(); },
      // What the Service screen found out about the address (the types answer). A different service drops the old branch's data.
      setInfo: (info) => { S.info = info; select({ service: info ? (info.service && info.service.id) || info.host : null }); },
      go: (step) => _go(step),
      select,
      // non-secret data only: a name, a choice. Never a value a person typed into a password box.
      draft: (key) => { const d = S.drafts[`${id}:${key || ''}`]; return d ? d.data : undefined; },
      setDraft: (key, data, scope) => { S.drafts[`${id}:${key || ''}`] = { scope: scope || 'account', data }; },
      // The kept DOM node for something secret: built once by `build(node)`, put into `slot` every paint.
      host: (slot, key, build, scope) => {
        const k = `${id}:${key}`;
        let h = S.hosts[k];
        if (!h) { const node = document.createElement('div'); node.dataset.cfwHost = k; build(node); h = S.hosts[k] = { scope: scope || 'account', node }; }
        slot.appendChild(h.node);
        return h.node;
      },
      // An operation this screen started and the shell must cancel if it is abandoned.
      own: (key, cancel, scope) => { _cancelOwned(`${id}:${key}`); S.owned[`${id}:${key}`] = { scope: scope || 'account', cancel }; },
      release: (key) => { delete S.owned[`${id}:${key}`]; },          // it was consumed (saved, claimed): nothing is left to cancel
      approve: (key, value, scope) => { S.approvals[`${id}:${key}`] = { scope: scope || 'account', value }; },
      approval: (key) => { const a = S.approvals[`${id}:${key}`]; return a ? a.value : undefined; },
      // A long option set, four to a page, with Previous and Next. `render(item)` returns one alternative's HTML.
      paged: (key, items, render) => _pagedHTML(`${id}:${key}`, items, render),
      // Leave the wizard having saved something: the panel gets the saved service, the wizard is emptied.
      finish: (svc, info) => { const cb = _ctx && _ctx.onSaved; close(); if (cb) cb(svc || {}, info || {}); },
    };
  }

  function _pagedHTML(key, items, render) {
    const pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
    const at = Math.min(Math.max(0, S.pages[key] || 0), pages - 1);
    S.pages[key] = at;
    const list = items.slice(at * PAGE_SIZE, at * PAGE_SIZE + PAGE_SIZE).map(render).join('');
    if (pages === 1) return list;
    return `${list}<nav class="desk-v1-cfw-pager" data-cfw-pager="${esc(key)}" aria-label="More options">
        <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cfw-page="prev" data-cfw-page-key="${esc(key)}" ${at === 0 ? 'disabled' : ''}>Previous</button>
        <span class="desk-v1-cfw-pageof" data-cfw-pageof>Page ${at + 1} of ${pages}</span>
        <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cfw-page="next" data-cfw-page-key="${esc(key)}" ${at === pages - 1 ? 'disabled' : ''}>Next</button>
      </nav>`;
  }

  // ── leaving ─────────────────────────────────────────────────────────────
  // Everything typed is emptied, every owned operation is cancelled, the state starts over.
  function close() {
    _invalidate(0, 'close');
    S = _fresh();
  }

  function _go(step) {
    if (_stepIndex(step) < _stepIndex('review') && _stepIndex(S.step) >= _stepIndex('review')) S.approvals = {};   // an approval is for the card as it was read
    S.step = step; S.error = ''; S.hint = ''; S.focusTitle = true; S.detailsOpen = false;
    _repaint();
  }
  function _repaint() { if (_ctx && _ctx.repaint) _ctx.repaint(); }

  // ── drawing ─────────────────────────────────────────────────────────────
  function _stepsHTML(def, api) {
    const at = _stepIndex(S.step);
    const items = STEPS.map((s, i) => `<li data-cfw-step-item="${s[0]}"${i === at ? ' aria-current="step"' : ''}${i < at ? ' data-done="true"' : ''}><span class="desk-v1-cfw-stepnum">${i + 1}</span><span class="desk-v1-cfw-steplabel">${esc(s[1])}</span></li>`);
    const sub = def && def.substep ? def.substep(api) : null;
    const label = STEPS[at][1];
    const of = sub ? `${label} · ${sub[0]} of ${sub[1]}` : `${label} · ${at + 1} of ${STEPS.length}`;
    return `<ol class="desk-v1-cfw-steps" data-cfw-steps aria-label="Steps">${items.join('')}</ol><div class="desk-v1-cfw-stepof" data-cfw-stepof>${esc(of)}</div>`;
  }

  // What a step with no screen for this branch shows. Nothing here can be pressed forward.
  const _MISSING = {
    id: '(missing)',
    title: () => 'Not available yet',
    copy: () => 'This part of the new Connect flow is not built yet. Go back and choose another way.',
    body: () => '',
  };

  function html(ctx) {
    if (!ctx.live) return '';
    _ctx = ctx;
    const def = _screenFor(S.step) || _MISSING;
    const api = _api(def === _MISSING ? null : def);
    const primary = def.primary ? def.primary(api) : null;
    const details = def.details ? def.details(api) : '';
    const back = _stepIndex(S.step) > 0 && S.step !== 'result';
    return `<section class="desk-v1-cfw" data-cfw data-cfw-step="${S.step}" data-cfw-screen="${esc(def.id)}" aria-label="Connect a service">
        ${_stepsHTML(def, api)}
        <form class="desk-v1-cfw-form" data-cfw-form autocomplete="off" novalidate>
          <h3 class="desk-v1-cfw-title" data-cfw-title tabindex="-1">${esc(def.title(api))}</h3>
          <p class="desk-v1-cfw-copy" data-cfw-copy>${esc(def.copy(api))}</p>
          <div class="desk-v1-cfw-body" data-cfw-body>${def.body(api)}</div>
          ${S.error ? `<div class="desk-v1-cfw-msg" data-cfw-msg="error" role="alert">${esc(S.error)}${S.hint ? ` <span class="desk-v1-cfw-hint">${esc(S.hint)}</span>` : ''}</div>` : ''}
          ${details ? `<details class="desk-v1-cfw-details" data-cfw-details${S.detailsOpen ? ' open' : ''}><summary>Details</summary><div class="desk-v1-cfw-details-body">${details}</div></details>` : ''}
          <div class="desk-v1-cfw-actions" data-cfw-actions>
            ${back ? '<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cfw-back" data-cfw-back>‹ Back</button>' : ''}
            ${primary ? `<button type="submit" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cfw-primary" data-cfw-primary ${primary.disabled ? 'disabled' : ''}>${esc(primary.label)}</button>` : ''}
          </div>
        </form>
      </section>`;
  }

  // ctx: the Connections panel's { live, api, engines, repaint, channels, openPick, onSaved }
  function bind(el, ctx) {
    const root = el.querySelector('[data-cfw]');
    if (!root) return;
    _ctx = ctx;
    const def = _screenFor(S.step) || _MISSING;
    const api = _api(def === _MISSING ? null : def);
    const form = root.querySelector('[data-cfw-form]');
    const back = root.querySelector('[data-cfw-back]');
    if (back) back.addEventListener('click', () => {
      const at = _stepIndex(S.step);
      if (at > 0) _go(STEPS[at - 1][0]);
    });
    const disc = root.querySelector('[data-cfw-details]');
    if (disc) disc.addEventListener('toggle', () => { S.detailsOpen = disc.open; });       // a repaint (a page turn) keeps it as it was
    root.querySelectorAll('[data-cfw-page]').forEach((b) => b.addEventListener('click', () => {
      const key = b.dataset.cfwPageKey;
      S.pages[key] = (S.pages[key] || 0) + (b.dataset.cfwPage === 'next' ? 1 : -1);
      const other = b.dataset.cfwPage === 'next' ? 'prev' : 'next';
      S.refocus = [`[data-cfw-page="${b.dataset.cfwPage}"][data-cfw-page-key="${key}"]:not([disabled])`, `[data-cfw-page="${other}"][data-cfw-page-key="${key}"]:not([disabled])`];
      _repaint();
    }));
    if (def.bind) def.bind(root, api);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (S.running) return;                                  // a second Enter while the first is still answering
      const primary = def.primary ? def.primary(_api(def === _MISSING ? null : def)) : null;
      if (!primary || primary.disabled) return;
      let advance = true;
      if (primary.run) {
        S.running = true;
        try { advance = await primary.run(); }
        catch (err) { S.running = false; S.error = err && err.message ? err.message : 'That did not work.'; _repaint(); return; }
        S.running = false;
      }
      if (advance !== false) { const at = _stepIndex(S.step); if (at < STEPS.length - 1) _go(STEPS[at + 1][0]); }
    });
    // Focus: a fresh screen starts at its heading; a screen that asked for a field gets it; a repaint keeps focus where it was.
    if (S.refocus) {
      const target = S.refocus.map((q) => root.querySelector(q)).find(Boolean);
      S.refocus = null;
      if (target) target.focus({ preventScroll: true });
    } else if (S.focusTitle) {
      S.focusTitle = false;
      const field = root.querySelector('[data-cfw-focus]');
      (field || root.querySelector('[data-cfw-title]')).focus({ preventScroll: false });
    } else if (!root.contains(document.activeElement)) {
      const field = root.querySelector('[data-cfw-focus]');
      if (field) field.focus({ preventScroll: true });
    }
  }

  // The frame's own state, for the screens that live in other files and for the smoke.
  function state() {
    return { step: S.step, sel: _selCopy(), hasInfo: !!S.info, hosts: Object.keys(S.hosts), owned: Object.keys(S.owned), drafts: Object.keys(S.drafts), approvals: Object.keys(S.approvals) };
  }
  window.DeskV1ConnectWizard = { enabled, setEnabled, active, registerScreen, html, bind, close, state, STEPS: STEPS.map((s) => s[0]), PAGE_SIZE };
})();

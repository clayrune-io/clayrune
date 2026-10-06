// Desk v1 — Connections: the Connect wizard's browser SIGN-IN Setup screen (MC-1062 ticket 04,
// docs/desk_v1/connect_flow_tickets/04-signin-screen.md; frame and rules in desk-v1-connect-wizard.js).
// Window-bridged module, no `import` (ground rule 1). It registers ONE Setup screen through
// `registerScreen`; the frame owns nothing here.
//
// WHEN IT SHOWS. Sign in is the chosen connection type, and its route is a browser sign-in route of a
// service the browser setup can save (x-browser, linkedin-browser). It binds by that ROUTE ID, never by
// an OAuth `connect_method`: choosing OAuth is the API branch (ticket 09), not this one.
//
// WHAT IT SHOWS (the design's X4a to X5c, one at a time, 3 alternatives, one Details):
//   Saved login            a vault login picked BY NAME, a named browser profile, Open sign-in, Sign in.
//                          "Sign in" is the existing passcode-gated POST /api/desk/connect/signin/fill
//                          through desk-v1-connect-signin.js (`fillHTML`/`bindFill`): this module sends a
//                          login NAME and a profile NAME, never a value, and never retries on its own.
//   Username and password typed here; stored only by the final Save. No early vault write.
//   In browser             a named browser profile (an existing one is chosen explicitly, never adopted
//                          by default) and Open sign-in. No login is stored.
// Continue validates what is on the screen and moves on. It writes nothing.
//
// SECRETS. A typed username/password lives only in the two <input>s of a node the frame keeps (`host`):
// the frame re-attaches it after every repaint and after Back, empties it when the type, account or
// service changes and on Close, and never reads it. This module copies the password into no variable,
// draft, storage, log or markup; `commit` reads it once into the one request that carries it.
// Everything else here (mode, login NAME, profile NAME, vault name, label) is non-secret and is dropped
// by `discard` on every invalidation the frame makes.
//
// WHERE THE SAVE HAPPENS. The frame's rule is "only a Review screen's Save may write". `commit()` is
// that Save for this branch: it posts ticket 03's POST /api/desk/connect/browser-setup/commit through
// the dashboard passcode prompt and, on success, empties the typed login. The Review screen (ticket 13a)
// calls it; nothing on this screen does. A failure keeps everything typed.
//
// WHICH ACCOUNT. The account being connected (X3 / L3 / L4) is chosen by an earlier screen that is not
// part of this ticket; it hands the choice over with `setTarget` AFTER the `api.select({ account })`
// that records it (a select drops what is scoped to the old account, and that includes the target):
//     setTarget({ kind, account: { id } | { new: { identity, label?, organization_id? } }, identity? })
// `kind` is one of the service's account kinds (account / member / organization); `identity` is only
// used to suggest a profile and a vault name. With no target the screen says so and offers no way on.
(function () {
  const W = window.DeskV1ConnectWizard;
  if (!W) return;
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const SERVICES = ['x', 'linkedin'];                   // the platforms the browser setup commit can save
  const FILL_KEY = 'wizard-login';                       // desk-v1-connect-signin.js keeps the Sign in message under this key
  const NEW_PROFILE = '__new__';
  const PROFILE_RE = /^[a-z0-9][a-z0-9_-]{0,40}$/;
  const NAME_RE = /^[a-z0-9][a-z0-9._-]{0,63}$/;         // mc/secrets_store.py _NAME_RE; the server stays the authority
  const MODES = [['saved', 'Saved login'], ['new', 'Username and password'], ['manual', 'In browser']];

  function _fresh() {
    return { mode: null, pick: '', profile: NEW_PROFILE, newProfile: null, vaultName: null, label: '',
      loaded: null, loading: false, options: null, optionsFailed: false, profiles: [], profilesFailed: false,
      node: null, info: null, sel: null, requestId: null, committing: false, refocus: null };
  }
  let L = _fresh();
  let _target = null;
  let _seq = 0;

  // ── what the frame is showing ───────────────────────────────────────────
  function _signinType(info) { return ((info && info.picker) || []).find((t) => t.id === 'signin') || null; }

  // The browser sign-in route of the chosen branch, by route id: `{ route_id, url, hosts }` or null.
  function _route(sel, info) {
    const t = _signinType(info);
    if (!t || !info.service || SERVICES.indexOf(info.service.id) < 0) return null;
    const ok = (v) => v && v.route_id && v.setup && v.setup.via === 'browser_signin' && v.setup.signin && v.setup.signin.url;
    const v = sel.variant ? t.variants.find((x) => x.id === sel.variant) : (t.variants.filter(ok).length === 1 ? t.variants.find(ok) : null);
    return ok(v) ? { route_id: v.route_id, url: v.setup.signin.url, hosts: v.setup.signin.hosts || [] } : null;
  }
  function _label(info) { return info && info.service && info.service.label ? info.service.label : 'the service'; }
  function _service(info) { return info && info.service ? info.service.id : ''; }

  function _slug(text) { return String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, ''); }
  function _suggestProfile(info) {
    const s = _service(info), id = _target && _target.identity ? _slug(_target.identity) : '';
    const name = `${s}-${id || 'account'}`.slice(0, 41).replace(/[-_]+$/, '');
    return PROFILE_RE.test(name) ? name : `${s}-account`;
  }
  function _suggestVault(info) {
    const s = _service(info), id = _target && _target.kind !== 'organization' && _target.identity ? _slug(_target.identity) : '';
    const name = `${s}.${id || 'login'}`.slice(0, 64).replace(/[-._]+$/, '');
    return NAME_RE.test(name) ? name : `${s}.login`;
  }

  // The profile name the person has chosen (an existing one, or the new name), or '' when none is usable.
  function _existing() { return L.profile !== NEW_PROFILE && L.profiles.indexOf(L.profile) >= 0; }
  function _profileName(info) {
    if (_existing()) return L.profile;
    const v = (L.newProfile == null ? _suggestProfile(info) : L.newProfile).trim().toLowerCase();
    return PROFILE_RE.test(v) ? v : '';
  }
  function _vaultName(info) { return (L.vaultName == null ? _suggestVault(info) : L.vaultName).trim(); }
  function _locked() { return !!(L.options && L.options.vault_locked); }
  function _typedFilled() {
    const n = L.node;
    if (!n) return false;
    const u = n.querySelector('[data-lg-user]'), p = n.querySelector('[data-lg-pass]');
    return !!(u && u.value.trim() && p && p.value);
  }

  // Why Continue is off (empty when it may be on). Reads the typed fields only for "is it filled in".
  function _problem(info) {
    if (!_target) return 'Choose the account first.';
    if (!L.mode) return 'Choose how you want to sign in.';
    if (L.mode !== 'manual' && _locked()) return 'The vault is locked. Unlock it, then check again.';
    if (!_profileName(info)) return 'Give the browser profile a name: lowercase letters, digits, - or _.';
    if (L.mode === 'saved' && !L.pick) return 'Choose a saved login.';
    if (L.mode === 'new') {
      if (!_typedFilled()) return 'Type the username and the password.';
      if (!NAME_RE.test(_vaultName(info))) return 'The vault name uses lowercase letters, digits, . - _.';
    }
    return '';
  }

  // ── loading metadata: login NAMES and profile NAMES only ────────────────
  function _load(api, force) {
    const svc = _service(api.info);
    if (!svc || L.loading || (!force && L.loaded === svc)) return;
    L.loading = true; L.optionsFailed = false; L.profilesFailed = false;
    const n = ++_seq;
    const ctx = api.ctx;
    Promise.all([
      ctx.api('POST', '/api/desk/connect/signin/options', { service: svc }).catch(() => { L.optionsFailed = true; return null; }),
      ctx.api('GET', '/api/browser/profiles').catch(() => { L.profilesFailed = true; return null; }),
    ]).then(([o, p]) => {
      if (n !== _seq) return;                            // discarded or reloaded meanwhile
      L.loading = false; L.loaded = svc;
      L.options = o;
      L.profiles = ((p && p.profiles) || []).map((x) => x.name).filter((x) => typeof x === 'string');
      api.repaint();
    });
  }

  // ── drawing ─────────────────────────────────────────────────────────────
  function _optionsHTML() {
    return `<div class="desk-v1-cfw-options" data-cfw-options role="radiogroup" aria-label="How to sign in">${MODES.map(([id, text]) => `
        <label class="desk-v1-cfw-option" data-cfw-option="${id}">
          <input type="radio" name="lg-mode" value="${id}" data-lg-mode ${L.mode === id ? 'checked' : ''}>
          <span class="desk-v1-cfw-option-label">${esc(text)}</span>
        </label>`).join('')}</div>`;
  }

  function _profileHTML(info) {
    const sel = _existing() ? L.profile : NEW_PROFILE;
    const opts = [`<option value="${NEW_PROFILE}"${sel === NEW_PROFILE ? ' selected' : ''}>New profile</option>`]
      .concat(L.profiles.map((p) => `<option value="${esc(p)}"${sel === p ? ' selected' : ''}>${esc(p)}</option>`));
    const typing = sel === NEW_PROFILE
      ? `<label class="desk-v1-conn-add-field">Profile name
            <input type="text" class="desk-v1-rules-textinput" data-lg-newprofile maxlength="41" autocomplete="off" autocapitalize="off" spellcheck="false" value="${esc(L.newProfile == null ? _suggestProfile(info) : L.newProfile)}"></label>`
      : '<div class="desk-v1-cfw-fact-text" data-lg-existing-profile>This profile keeps the sign-in it already has. Use one for this account only.</div>';
    return `<div class="desk-v1-cfw-group" data-lg-profile-group>
        <label class="desk-v1-conn-add-field">Browser profile
          <select class="desk-v1-rules-textinput" data-lg-profile>${opts.join('')}</select></label>
        ${typing}
        ${L.profilesFailed ? '<div class="desk-v1-cfw-fact-text" data-lg-profiles-failed>The saved profiles could not be listed. A new profile still works.</div>' : ''}
      </div>`;
  }

  function _lockedHTML() {
    return _locked() ? `<div class="desk-v1-cfw-msg" data-lg-locked role="alert">The vault is locked. Unlock it, then
        <button type="button" class="desk-v1-cf-link" data-lg-recheck>check again</button>.</div>` : '';
  }

  function _loginPickHTML(info) {
    if (L.optionsFailed || (L.loaded && !L.options)) {
      return `<div class="desk-v1-cfw-msg" data-lg-options-failed role="alert">The saved logins could not be loaded. <button type="button" class="desk-v1-cf-link" data-lg-recheck>Try again</button> or choose another way.</div>`;
    }
    if (!L.options) return '<div class="desk-v1-cfw-fact-text" data-lg-loading>Loading your saved logins…</div>';
    const logins = L.options.logins || [];
    if (!logins.length) return '<div class="desk-v1-cfw-fact-text" data-lg-nologins>There is no saved login for this service yet. Choose another way.</div>';
    const opts = ['<option value="">Choose a saved login…</option>'].concat(logins.map((l) => `<option value="${esc(l.name)}"${L.pick === l.name ? ' selected' : ''}>${esc(l.name)}</option>`));
    return `<label class="desk-v1-conn-add-field">Saved login
        <select class="desk-v1-rules-textinput" data-lg-pick ${_locked() ? 'disabled' : ''}>${opts.join('')}</select></label>`;
  }

  // The Sign in button types a stored login into the page open in the pane, on this route's own address only. It is
  // offered for a login in this service's own namespace (what the fill route accepts before the connection is saved).
  function _fillHTML(info, route) {
    const mine = L.pick && L.pick.toLowerCase().indexOf(_service(info) + '.') === 0;
    const spec = { url: route.url, login: mine && !_locked() ? L.pick : '', profile: _profileName(info), hint: '', why: 'Choose a saved login named for this service.' };
    const why = L.pick && !mine ? `<div class="desk-v1-cfw-fact-text" data-lg-notmine>${esc(L.pick)} is not named for ${esc(_label(info))} (${esc(_service(info))}.…), so it cannot be typed from here. You can still save it and sign in after.</div>` : '';
    return `${window.DeskV1ConnectSignin ? window.DeskV1ConnectSignin.fillHTML(FILL_KEY, spec) : ''}${why}`;
  }

  function _typedHTML() {
    return '<div data-lg-typed-slot></div>';
  }

  function _openHTML(route) {
    return `<div class="desk-v1-cs-fill-row"><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-lg-open="${esc(route.url)}">Open sign-in</button></div>`;
  }

  function _bodyHTML(api) {
    const info = api.info, route = _route(api.sel, info);
    const head = _optionsHTML();
    if (!_target) return `${head}<div class="desk-v1-cfw-msg" data-lg-notarget role="alert">Choose which ${esc(_label(info))} account this is first.</div>`;
    if (L.mode === 'saved') return `${head}${_lockedHTML()}${_loginPickHTML(info)}${_profileHTML(info)}${route ? _fillHTML(info, route) : ''}`;
    if (L.mode === 'new') return `${head}${_lockedHTML()}${_typedHTML()}`;
    if (L.mode === 'manual') return `${head}${_profileHTML(info)}${route ? _openHTML(route) : ''}`;
    return head;
  }

  function _detailsHTML(api) {
    const info = api.info;
    if (!_target || !L.mode) return '';
    const parts = [];
    if (_target.account && _target.account.new) {
      parts.push(`<label class="desk-v1-conn-add-field">Account label (optional)
          <input type="text" class="desk-v1-rules-textinput" data-lg-label maxlength="200" autocomplete="off" value="${esc(L.label)}"></label>`);
    }
    if (L.mode === 'new') {
      parts.push(`<label class="desk-v1-conn-add-field">Vault name
          <input type="text" class="desk-v1-rules-textinput" data-lg-vaultname maxlength="64" autocomplete="off" autocapitalize="off" spellcheck="false" value="${esc(_vaultName(info))}"></label>`);
      parts.push(_profileHTML(info));
    }
    return parts.join('');
  }

  const _TITLES = {
    none: (info) => `Sign in to ${_label(info)}`, saved: (info) => `Saved ${_label(info)} login`,
    new: (info) => `Your ${_label(info)} login`, manual: () => 'Sign in yourself',
  };
  const _COPY = {
    none: 'Use a saved login, enter a new one, or sign in yourself.',
    saved: 'Your password stays in Secrets. Finish any security check in the browser.',
    new: 'Stored when you save. You can finish signing in afterwards.',
    manual: 'Sign in in the browser pane. Clayrune keeps the browser session.',
  };

  // ── the screen ──────────────────────────────────────────────────────────
  W.registerScreen({
    id: 'login-step', step: 'setup',
    match: (sel, info) => sel.type === 'signin' && !!_route(sel, info),
    title: (api) => (_TITLES[L.mode || 'none'])(api.info),
    copy: () => _COPY[L.mode || 'none'],
    body: (api) => { L.info = api.info; L.sel = api.sel; return _bodyHTML(api); },
    details: _detailsHTML,
    primary: (api) => ({
      label: 'Continue', disabled: !!_problem(api.info),
      run: async () => {
        const why = _problem(api.info);
        if (why) { api.error(why); return false; }
        return true;                                       // Continue writes nothing: the Save is `commit`, on Review
      },
    }),
    bind: (root, api) => {
      L.info = api.info; L.sel = api.sel;
      const route = _route(api.sel, api.info);
      if (!L.loaded && _target) _load(api);
      const primary = root.querySelector('[data-cfw-primary]');
      const sync = () => { if (primary) primary.disabled = !!_problem(api.info); };
      const again = () => api.repaint();

      root.querySelectorAll('[data-lg-mode]').forEach((r) => r.addEventListener('change', () => {
        L.mode = r.value; L.refocus = `[data-lg-mode][value="${r.value}"]`; again();
      }));
      const pick = root.querySelector('[data-lg-pick]');
      if (pick) pick.addEventListener('change', () => { L.pick = pick.value; again(); });
      root.querySelectorAll('[data-lg-recheck]').forEach((b) => b.addEventListener('click', () => { L.loaded = null; _load(api, true); again(); }));
      root.querySelectorAll('[data-lg-profile]').forEach((s) => s.addEventListener('change', () => { L.profile = s.value; again(); }));
      root.querySelectorAll('[data-lg-newprofile]').forEach((i) => i.addEventListener('input', () => { L.newProfile = i.value; sync(); }));
      root.querySelectorAll('[data-lg-vaultname]').forEach((i) => i.addEventListener('input', () => { L.vaultName = i.value; sync(); }));
      root.querySelectorAll('[data-lg-label]').forEach((i) => i.addEventListener('input', () => { L.label = i.value; }));
      root.querySelectorAll('[data-lg-open]').forEach((b) => b.addEventListener('click', () => {
        try { if (typeof window.openBrowserPane === 'function') window.openBrowserPane(b.dataset.lgOpen, null, null, _profileName(api.info) || null); } catch (_) { /* the pane shows its own failure */ }
      }));

      const slot = root.querySelector('[data-lg-typed-slot]');
      if (slot) {
        L.node = api.host(slot, 'typed', (node) => {
          node.innerHTML = `<label class="desk-v1-conn-add-field">Username or email
              <input type="text" class="desk-v1-rules-textinput" data-lg-user autocomplete="off" autocapitalize="off" spellcheck="false"></label>
            <label class="desk-v1-conn-add-field">Password
              <input type="password" class="desk-v1-rules-textinput" data-lg-pass autocomplete="off" spellcheck="false"></label>`;
        });
        L.node.querySelectorAll('input').forEach((i) => { if (!i._lgBound) { i._lgBound = true; i.addEventListener('input', () => { const p = document.querySelector('[data-cfw-primary]'); if (p) p.disabled = !!_problem(L.info); }); } });
      }

      // "Sign in" on the saved-login branch: the existing passcode-gated fill, one click, one request, no retry of its own.
      if (L.mode === 'saved' && route && window.DeskV1ConnectSignin) {
        window.DeskV1ConnectSignin.bindFill(root, FILL_KEY, { url: route.url, profile: _profileName(api.info) }, api.ctx,
          () => ({ service: _service(api.info), route_id: route.route_id, login: L.pick, profile: _profileName(api.info) }));
      }
      if (L.refocus) { const t = root.querySelector(L.refocus); L.refocus = null; if (t) t.focus({ preventScroll: true }); }
      sync();
    },
    discard: () => {                                       // service, type or account changed, or Close: nothing of this branch is kept
      if (L.node) { L.node.querySelectorAll('input').forEach((i) => { i.value = ''; }); }
      if (window.DeskV1ConnectSignin && window.DeskV1ConnectSignin.clearFill) window.DeskV1ConnectSignin.clearFill(FILL_KEY);
      _seq++;
      L = _fresh();
      _target = null;
    },
  });

  // ── the account this sign-in is for ─────────────────────────────────────
  function setTarget(t) {
    if (t == null) { _target = null; return; }
    if (!t || typeof t !== 'object' || !t.kind || !t.account || (!t.account.id && !(t.account.new && t.account.new.identity))) throw new Error('setTarget: kind and an account (id, or new with an identity) are required');
    _target = { kind: String(t.kind), account: JSON.parse(JSON.stringify(t.account)), identity: t.identity ? String(t.identity) : (t.account.new ? String(t.account.new.identity) : '') };
  }

  // ── the Save ────────────────────────────────────────────────────────────
  // The draft of POST /api/desk/connect/browser-setup/commit, or `{ error }`. Reads the typed password once, into the
  // object that is sent; nothing else keeps it.
  function _draft() {
    const info = L.info, sel = L.sel;
    const route = sel && info ? _route(sel, info) : null;
    if (!route) return { error: 'Choose how to connect first.' };
    const why = _problem(info);
    if (why) return { error: why };
    const account = JSON.parse(JSON.stringify(_target.account));
    if (account.new && L.label.trim()) account.new.label = L.label.trim();
    const draft = { service: _service(info), revision: info.service.revision, route_id: route.route_id, account, account_kind: _target.kind, browser_profile: _profileName(info) };
    if (L.mode === 'saved') draft.login = L.pick;
    if (L.mode === 'new') draft.new_login = { name: _vaultName(info), username: L.node.querySelector('[data-lg-user]').value.trim(), value: L.node.querySelector('[data-lg-pass]').value };
    return { draft };
  }

  // Save the connection: one passcode-gated request. Resolves `{ ok: true, result }`, `{ cancelled: true }` (the
  // passcode prompt was closed: nothing was sent) or `{ ok: false, error, code, status }`. A failure keeps everything
  // typed and the same request id, so a repeat is a replay, not a second write. A success empties the typed login.
  async function commit() {
    if (L.committing) return { ok: false, error: 'A save is already running.', code: 'busy', status: 0 };
    const d = _draft();
    if (d.error) return { ok: false, error: d.error, code: 'incomplete', status: 0 };
    if (!L.requestId) L.requestId = (window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() : `bs-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
    L.committing = true;
    const label = _label(L.info);
    let res;
    try {
      res = await window.humanProofFetch('/api/desk/connect/browser-setup/commit', { method: 'POST', body: JSON.stringify({ request_id: L.requestId, draft: d.draft }) },
        { title: 'Save connection', description: `Re-enter your dashboard passcode to save how ${label} is connected.` });
    } catch (e) {
      res = { ok: false, status: 0, body: { error: e && e.message ? e.message : 'could not reach the server' } };
    }
    L.committing = false;
    if (res === null) return { cancelled: true };
    const body = res.body || {};
    if (!res.ok) return { ok: false, error: body.error || body.message || `The save failed (HTTP ${res.status}).`, code: body.code || '', status: res.status };
    if (L.node) L.node.querySelectorAll('input').forEach((i) => { i.value = ''; });      // stored now: no password stays on the page
    L.requestId = null;
    return { ok: true, result: body };
  }

  // The non-secret facts of what is chosen, for a Review row (never the password).
  function summary() {
    if (!L.mode || !L.info) return null;
    return { mode: L.mode, login: L.mode === 'saved' ? L.pick : (L.mode === 'new' ? _vaultName(L.info) : ''),
      newLogin: L.mode === 'new', username: L.mode === 'new' && L.node ? L.node.querySelector('[data-lg-user]').value.trim() : '',
      hasPassword: L.mode === 'new' && !!(L.node && L.node.querySelector('[data-lg-pass]').value),
      profile: _profileName(L.info), newProfile: L.profiles.indexOf(_profileName(L.info)) < 0 };
  }

  window.DeskV1ConnectLoginStep = { setTarget, commit, summary };
})();

// Desk v1 — Connections: the Connect wizard's API / provider SETUP screen (MC-1062 ticket 09,
// docs/desk_v1/connect_flow_tickets/09-api-screen.md; frame and rules in desk-v1-connect-wizard.js).
// Window-bridged module, no `import` (ground rule 1). It registers ONE Setup screen through
// `registerScreen`; the frame owns nothing here, and the old Details step is not touched.
//
// WHEN IT SHOWS. The chosen connection variant is one a provider adapter can set up
// (`variant.setup.via === 'provider'`, mc/desk_connect/providers): X's developer app, Higgsfield's key,
// the key engines (Google AI, OpenAI), Higgsfield's sign-in, curated Notion. It never matches an API
// variant that is `reference_only` (supplied API details, LinkedIn's built-in API): those stay setup-only
// and belong to the unknown-service ticket; nothing here connects them.
//
// WHAT IT SHOWS, one thing at a time (the design's X4b then X5d):
//   fields      the provider's own fields: X Client ID and optional Client Secret, a key. An entry already
//               stored in Secrets is named and not asked again (desk-v1-connect-adapter.js). The callback
//               address and app instructions are under the one Details disclosure.
//   authorize   only where the provider signs in (X, Higgsfield): "Sign in and approve your app". This is the
//               existing held sign-in (desk-v1-connect-held.js): POST .../start-held through its OWN passcode
//               prompt, the vendor token held in server memory, expiry/cancel/claim as before. Changing the
//               app's Client ID or Secret on the fields view drops the held sign-in (Held's own watcher).
//               Details has the optional saved-login help and a new login to store with the Save.
// Continue writes nothing. `commit()` is the Save for this branch, called by the Review screen (ticket 13a);
// it posts the existing POST /api/desk/connect/commit through the final passcode prompt. start-held and the
// Save each keep their own passcode: nothing is replayed between them.
//
// SECRETS. Typed values live only in the adapter's and the login form's own <input> nodes (kept alive by
// those modules, emptied by their `clear`). This module copies none into a variable, draft, storage, log or
// markup; `commit` reads them once into the one request that carries them. Everything else it keeps (the
// stage, the chosen login NAME) is non-secret and is dropped by `discard`.
//
// WHICH ACCOUNT. Like the sign-in and Permissions screens, the account is chosen by an earlier screen that is
// not part of this ticket:   setTarget({ kind, account: { id } | { new: { identity, label? } }, identity? })
//   * { new }   the handle and name come from the account choice, so the provider's own identity and name
//               fields are not asked again;
//   * none      the provider's own fields are shown, as the old flow showed them;
//   * { id }    REFUSED, and said so. `account_attach.py` states the contract (a later connection attaches to the
//               saved account's id) but the provider side is not built (its docstring, "NOT built here"): `XProvider.apply`
//               still calls create_account and start-held still mints a new account id. Saving would create a
//               second account, so this screen offers no way on rather than claim an attach that does not exist.
(function () {
  const W = window.DeskV1ConnectWizard;
  if (!W) return;
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const COST_BEARING = ['published', 'account_specific'];     // type_view: the cost bases that mean "may cost money"
  const IDENTITY_KEYS = ['identity', 'label'];                 // fields the account choice answers once it is made
  const LOGIN_KEY = 'api-login';                               // desk-v1-connect-signin.js keeps the typed-login form under this key

  function _fresh() {
    return { loaded: '', loading: false, failed: false, connector: null, url: '', method: '', stage: null, requestId: null, committing: false,
      logins: [], pick: '', route: null, locked: false, optionsFor: '', info: null, sel: null };
  }
  let A = _fresh();
  let _target = null;
  let _seq = 0;

  // ── what the frame is showing ───────────────────────────────────────────
  function _label(info) { return info && info.service && info.service.label ? info.service.label : (info && info.host) || 'the service'; }
  function _service(info) { return info && info.service ? info.service.id : ''; }
  function _variant(sel, info) {
    const t = ((info && info.picker) || []).find((x) => x.id === sel.type);
    if (!t) return null;
    if (sel.variant) return t.variants.find((v) => v.id === sel.variant) || null;
    return t.variants.length === 1 ? t.variants[0] : null;
  }
  function _provider(sel, info) {
    const v = _variant(sel, info);
    return v && v.setup && v.setup.mode === 'full' && v.setup.via === 'provider' ? v : null;
  }
  function _costs(v) { return !!v && COST_BEARING.indexOf(v.cost && v.cost.basis) >= 0; }

  // The account choice: a new one (the handle is known), a saved one (refused, see the header), or none yet.
  function _targetMode() { return !_target ? 'none' : (_target.account.id ? 'existing' : 'new'); }
  // Whether the provider signs in (X, Higgsfield): the type projection says so, so the title is right before the fields load.
  function _signs() { const v = A.info ? _provider(A.sel, A.info) : null; return !!(v && v.setup.signs_in); }

  // The provider's fields as the person must still answer them: identity and name drop out once the account is chosen.
  function _displayConnector() {
    const c = A.connector;
    if (!c || _targetMode() !== 'new') return c;
    return Object.assign({}, c, { fields: (c.fields || []).filter((f) => IDENTITY_KEYS.indexOf(f.key) < 0) });
  }
  function _hasFields() { const c = _displayConnector(); return !!(c && c.fields && c.fields.length); }
  function _adapterKey(info) { return `${_service(info)}:${A.connector ? A.connector.service : ''}:${_targetMode()}`; }
  function _stage() { return A.stage || (!A.connector || _hasFields() ? 'fields' : 'authorize'); }

  // Everything this screen owns: the held sign-in, the typed values, the loaded answer. A change of service, type or account.
  function _dropAll() {
    const Hd = window.DeskV1ConnectHeld, Ad = window.DeskV1ConnectAdapter, Sg = window.DeskV1ConnectSignin;
    if (Hd) Hd.cancel();
    if (Ad) Ad.clear();
    if (Sg) { Sg.clear(LOGIN_KEY); Sg.clearFill(LOGIN_KEY); }
    _seq++;
    A = _fresh();
  }

  // The account choice changed which fields are asked, not what the service needs: drop what was typed and signed in,
  // keep the loaded field specs (no second read of them).
  function _dropTyped() {
    const Hd = window.DeskV1ConnectHeld, Ad = window.DeskV1ConnectAdapter, Sg = window.DeskV1ConnectSignin;
    if (Hd) Hd.cancel();
    if (Ad) Ad.clear();
    if (Sg) { Sg.clear(LOGIN_KEY); Sg.clearFill(LOGIN_KEY); }
    A.stage = null; A.requestId = null; A.pick = '';
  }

  // ── loading: the provider's field specs, and the saved-login names ──────
  // The field specs come from the same read-only inspect answer the old flow used (a provider's `connector`);
  // the type projection does not carry them. No credential is sent, no value comes back.
  function _load(api) {
    const svc = _service(api.info), v = _provider(api.sel, api.info);
    const key = `${svc}:${v ? v.setup.method : ''}`;
    if (!v || A.loading || A.loaded === key) return;
    A.loading = true; A.failed = false;
    const n = ++_seq;
    api.ctx.api('POST', '/api/desk/connect/inspect', { input: api.info.input || api.info.url }).then((r) => {
      if (n !== _seq) return;                                       // discarded or reloaded meanwhile
      const row = ((r && r.options) || []).find((o) => o.method === v.setup.method && o.connector);
      A.loading = false; A.loaded = key; A.failed = !row;
      A.connector = row ? row.connector : null; A.method = v.setup.method; A.url = (r && r.url) || api.info.url || '';
      api.repaint();
    }).catch(() => { if (n !== _seq) return; A.loading = false; A.failed = true; api.repaint(); });
  }

  // Saved logins (names only) for the optional sign-in help. Failing to load them only hides the help.
  function _loadLogins(api) {
    const svc = _service(api.info), v = _provider(api.sel, api.info);
    if (!v || !_signs() || A.optionsFor === svc) return;
    A.optionsFor = svc;
    const n = _seq;
    api.ctx.api('POST', '/api/desk/connect/signin/options', { service: svc }).then((o) => {
      if (n !== _seq) return;
      A.route = ((o && o.routes) || []).find((r) => r.connect_method === v.setup.method) || null;
      A.logins = (o && o.logins) || []; A.locked = !!(o && o.vault_locked);
      api.repaint();
    }).catch(() => { /* an optional help: without it the sign-in is as it was */ });
  }

  // ── drawing ─────────────────────────────────────────────────────────────
  function _blocked(info) {
    if (_targetMode() === 'existing') {
      return `Connecting an API to a saved ${esc(_label(info))} account is not available yet: it would create a second account. Go back and add a new account, or use its sign-in.`;
    }
    return '';
  }

  function _fieldsBody(api) {
    const c = _displayConnector();
    return `<div class="desk-v1-cfw-group" data-api-fields><div class="desk-v1-cf-cred" data-cfa-slot ${_hasFields() ? '' : 'hidden'}></div></div>${c && c.install ? '<div class="desk-v1-cfw-fact-text" data-api-install>This installs software. You approve it on the Review screen.</div>' : ''}`;
  }

  function _authorizeBody() {
    const Hd = window.DeskV1ConnectHeld;
    const back = _hasFields() ? '<div><button type="button" class="desk-v1-cf-link" data-api-back-fields>Change the app details</button></div>' : '';
    return `<div class="desk-v1-cfw-group" data-api-authorize>${Hd ? Hd.html() : ''}${back}</div>`;
  }

  function _body(api) {
    const why = _blocked(api.info);
    if (why) return `<div class="desk-v1-cfw-msg" data-api-blocked role="alert">${why}</div>`;
    if (A.loading || (!A.loaded && !A.failed)) return '<div class="desk-v1-cfw-fact-text" data-api-loading>Loading what this service needs…</div>';
    if (A.failed || !A.connector) {
      return `<div class="desk-v1-cfw-msg" data-api-failed role="alert">Clayrune could not load the setup details for ${esc(_label(api.info))}. <button type="button" class="desk-v1-cf-link" data-api-retry>Try again</button></div>`;
    }
    return _stage() === 'fields' ? _fieldsBody(api) : _authorizeBody();
  }

  function _loginHelp(api) {
    const Sg = window.DeskV1ConnectSignin, Hd = window.DeskV1ConnectHeld;
    if (!Sg || !A.route) return '';
    const opts = ['<option value="">Choose a saved login…</option>'].concat(A.logins.map((l) => `<option value="${esc(l.name)}"${A.pick === l.name ? ' selected' : ''}>${esc(l.name)}</option>`));
    const saved = A.logins.length
      ? `<label class="desk-v1-conn-add-field">Saved login
          <select class="desk-v1-rules-textinput" data-api-pick ${A.locked ? 'disabled' : ''}>${opts.join('')}</select></label>
        ${Sg.fillHTML(LOGIN_KEY, { url: null, login: A.pick, profile: Hd ? Hd.profile() : '', hint: '', why: 'Choose a saved login.' })}`
      : '<div class="desk-v1-cfw-fact-text" data-api-nologins>There is no saved login for this service yet.</div>';
    return `<div class="desk-v1-cfw-dgroup" data-api-loginhelp><div class="desk-v1-cfw-dtitle">Sign-in help</div>
        ${A.locked ? '<div class="desk-v1-cfw-fact-text" data-api-locked>The vault is locked. Unlock it, then come back.</div>' : ''}
        ${saved}
        <div class="desk-v1-cfw-fact-text">A new login is stored only when you save.</div>
        ${Sg.newLoginHTML(LOGIN_KEY)}</div>`;
  }

  function _details(api) {
    if (_blocked(api.info) || !A.connector) return '';
    const Ad = window.DeskV1ConnectAdapter;
    if (_stage() === 'fields') {
      const guide = Ad ? Ad.guideHTML(A.connector) : '';
      const note = A.connector.summary ? `<div class="desk-v1-cfw-fact-text" data-api-summary>${esc(A.connector.summary)}</div>` : '';
      return `${note}${guide}`;
    }
    return _loginHelp(api);
  }

  // The checks Continue makes. Empty when it may go on. Reads typed fields only for "is it filled in".
  function _problem(api, root) {
    if (_blocked(api.info)) return 'This account cannot be used for an API connection yet.';
    if (!A.connector) return 'The setup details are not loaded yet.';
    if (_stage() === 'fields') {
      const r = window.DeskV1ConnectAdapter && window.DeskV1ConnectAdapter.mounted() ? window.DeskV1ConnectAdapter.read() : { fields: {} };
      return r.error || '';
    }
    if (root && root.querySelector('[data-cfh-state="waiting"], [data-cfh-start][disabled]')) return 'Finish signing in, or cancel it, before you continue.';
    const typed = window.DeskV1ConnectSignin ? window.DeskV1ConnectSignin.read(LOGIN_KEY) : null;
    return typed && typed.error ? typed.error : '';
  }

  // ── the screen ──────────────────────────────────────────────────────────
  W.registerScreen({
    id: 'api-step', step: 'setup',
    match: (sel, info) => !!_provider(sel, info),
    title: (api) => { A.info = api.info; A.sel = api.sel; return _stage() === 'authorize' ? `Authorize ${_label(api.info)}` : (_signs() ? `${_label(api.info)} developer app` : `${_label(api.info)} API key`); },
    copy: (api) => {
      const v = _provider(api.sel, api.info);
      if (_stage() === 'authorize') return `Sign in and approve your app in ${_label(api.info)}.`;
      return `${_signs() ? 'Use an app you own.' : 'Use a key from your own account.'}${_costs(v) ? ' API use may cost money.' : ''}`;
    },
    substep: () => (_signs() && _hasFields() ? [_stage() === 'fields' ? 1 : 2, 2] : null),
    body: (api) => { A.info = api.info; A.sel = api.sel; return _body(api); },
    details: _details,
    primary: (api) => ({
      label: 'Continue',
      disabled: !!_blocked(api.info) || !A.connector,
      run: async () => {
        const why = _problem(api, document.querySelector('[data-cfw]'));
        if (why) { api.error(why); return false; }
        if (_stage() === 'fields' && _signs()) { A.stage = 'authorize'; api.error(''); return false; }       // the sub-view changed: an old message does not follow it
        return true;                                       // Continue writes nothing: the Save is `commit`, on Review
      },
    }),
    bind: (root, api) => {
      const Ad = window.DeskV1ConnectAdapter, Hd = window.DeskV1ConnectHeld, Sg = window.DeskV1ConnectSignin;
      if (_blocked(api.info)) return;
      _load(api);
      root.querySelectorAll('[data-api-retry]').forEach((b) => b.addEventListener('click', () => { A.loaded = ''; A.failed = false; api.repaint(); }));
      if (!A.connector) return;
      const label = _label(api.info), method = _provider(api.sel, api.info).setup.method;
      if (_signs() && Hd) Hd.begin(_service(api.info), method, label, api.ctx);       // the same service and method keeps its sign-in
      if (Ad) {
        const slot = root.querySelector('[data-cfa-slot]');
        if (slot) {
          Ad.mount(slot, _displayConnector(), _adapterKey(api.info));
          const first = slot.querySelector('input:not([type="checkbox"])');
          if (first) first.setAttribute('data-cfw-focus', '');
        }
      }
      if (_signs() && Hd) Hd.bind(root);                       // the buttons, and the watcher that drops a sign-in when the app changes
      root.querySelectorAll('[data-api-back-fields]').forEach((b) => b.addEventListener('click', () => { A.stage = 'fields'; api.error(''); }));
      if (_stage() === 'authorize' && _signs()) {
        _loadLogins(api);
        const pick = root.querySelector('[data-api-pick]');
        if (pick) pick.addEventListener('change', () => { A.pick = pick.value; api.repaint(); });
        if (Sg && A.route) {
          Sg.mount(root, LOGIN_KEY, `${_service(api.info)}.login`, () => api.repaint());
          const profile = () => (Hd ? Hd.profile() : '');
          Sg.bindFill(root, LOGIN_KEY, { url: null, profile: profile() }, api.ctx,
            () => ({ service: _service(api.info), route_id: A.route.route_id, login: A.pick, profile: profile() }));
        }
      }
      const start = root.querySelector('[data-cfh-start]:not([disabled])');
      if (start) start.setAttribute('data-cfw-focus', '');
    },
    discard: () => { _dropAll(); _target = null; },          // service, type or account changed, or Close: nothing of this branch is kept
  });

  // ── the account this connection is for ──────────────────────────────────
  function setTarget(t) {
    if (t == null) { if (_target) _dropAll(); _target = null; return; }
    if (!t || typeof t !== 'object' || !t.kind || !t.account || (!t.account.id && !(t.account.new && t.account.new.identity))) throw new Error('setTarget: kind and an account (id, or new with an identity) are required');
    const next = { kind: String(t.kind), account: JSON.parse(JSON.stringify(t.account)), identity: t.identity ? String(t.identity) : (t.account.new ? String(t.account.new.identity) : '') };
    if (_target && JSON.stringify(_target) !== JSON.stringify(next)) _dropAll();      // another account: what was typed or signed in is not carried over
    else if (!_target && A.connector && _targetMode() !== (next.account.id ? 'existing' : 'new')) _dropTyped();   // the fields it asked change with the choice
    _target = next;
  }

  // ── the Save ────────────────────────────────────────────────────────────
  // The draft of POST /api/desk/connect/commit, or `{ error }`. Reads the typed values once, into the object that is sent.
  function _draft(info) {
    const Ad = window.DeskV1ConnectAdapter, Hd = window.DeskV1ConnectHeld, Sg = window.DeskV1ConnectSignin, In = window.DeskV1ConnectInstall;
    if (!A.connector || !info) return { error: 'Choose how to connect first.' };
    if (_blocked(info)) return { error: 'This account cannot be used for an API connection yet.' };
    const r = Ad && Ad.mounted() ? Ad.read() : { fields: {} };
    if (r.error) return { error: r.error };
    const fields = Object.assign({}, r.fields);
    if (_targetMode() === 'new') {                                    // answered by the account choice, not typed here
      const n = _target.account.new;
      fields.identity = n.identity;
      if (n.label) fields.label = n.label;
    }
    if (A.connector.install) {
      const consent = In ? In.read(A.connector.install) : null;
      if (!consent) return { error: 'Approve the install on the Review step first.', step: 'review' };
      fields.install = consent;
    }
    const draft = { url: A.url, method: A.method, fields };
    const held = A.connector.signs_in && Hd ? Hd.draft() : null;
    if (held) draft.held = held;                                      // the sign-in made on the authorize view: Save claims it
    const typed = A.connector.signs_in && Sg ? Sg.read(LOGIN_KEY) : null;
    if (typed && typed.error) return { error: typed.error };
    if (typed) draft.new_login = typed.new_login;                     // stored by this one passcode, with the connection
    return { draft };
  }

  // Save the connection: one passcode-gated request. Resolves `{ ok: true, result }`, `{ cancelled: true }` (the passcode
  // prompt was closed: nothing was sent) or `{ ok: false, error, code, status }`. A failure keeps everything typed and the
  // same request id, so a repeat is a replay, not a second write. A success empties the typed values and the held sign-in.
  async function commit() {
    if (A.committing) return { ok: false, error: 'A save is already running.', code: 'busy', status: 0 };
    const d = _draft(A.info);
    if (d.error) return { ok: false, error: d.error, code: 'incomplete', status: 0, step: d.step || 'setup' };
    if (!A.requestId) A.requestId = (window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() : `api-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
    A.committing = true;
    const label = _label(A.info);
    let res;
    try {
      res = await window.humanProofFetch('/api/desk/connect/commit', { method: 'POST', body: JSON.stringify({ request_id: A.requestId, draft: d.draft }) },
        { title: 'Save service', description: `Re-enter your dashboard passcode to connect ${label}.` });
    } catch (e) {
      res = { ok: false, status: 0, body: { error: e && e.message ? e.message : 'could not reach the server' } };
    }
    d.draft = null;
    A.committing = false;
    if (res === null) return { cancelled: true };
    const body = res.body || {};
    if (!res.ok) return { ok: false, error: body.error || body.message || `The save failed (HTTP ${res.status}).`, code: body.code || '', status: res.status };
    if (window.DeskV1ConnectAdapter) window.DeskV1ConnectAdapter.clear();
    if (window.DeskV1ConnectHeld) window.DeskV1ConnectHeld.consumed();             // the Save stored it
    if (window.DeskV1ConnectSignin) window.DeskV1ConnectSignin.clear(LOGIN_KEY);   // a typed password is in the vault now: it leaves the page
    A.requestId = null;
    return { ok: true, result: body };
  }

  // Non-secret facts for the Review screen: the typed fields (a stored entry by name, a secret as "entered, hidden"),
  // the sign-in, and the login choice. No value.
  function reviewHTML() {
    const Ad = window.DeskV1ConnectAdapter, Hd = window.DeskV1ConnectHeld, Sg = window.DeskV1ConnectSignin;
    if (!A.connector) return '';
    const typed = Sg ? Sg.meta(LOGIN_KEY) : null;
    const login = typed
      ? `<div><dt>Sign-in login</dt><dd data-api-r-login>New: <code>${esc(typed.name)}</code>${typed.username ? `, username ${esc(typed.username)}` : ''}; password ${typed.hasValue ? 'entered, hidden: shown nowhere' : 'not entered'}. Stored when you save.</dd></div>`
      : (A.pick ? `<div><dt>Sign-in login</dt><dd data-api-r-login><code>${esc(A.pick)}</code>, typed into the sign-in page when you ask</dd></div>` : '');
    const who = _targetMode() === 'new' ? `<div><dt>Account</dt><dd data-api-r-account>${esc(_target.account.new.identity)} (new)</dd></div>` : '';
    const rows = `${who}${_signs() && Hd ? Hd.reviewRowHTML() : ''}${login}`;
    return `${Ad && Ad.mounted() ? Ad.summaryHTML() : ''}${rows ? `<dl class="desk-v1-cf-facts">${rows}</dl>` : ''}`;
  }

  // The install card the Review screen must show and have approved, or null.
  function installCard() { return A.connector && A.connector.install ? A.connector.install : null; }

  window.DeskV1ConnectApiStep = { setTarget, commit, reviewHTML, installCard };
})();

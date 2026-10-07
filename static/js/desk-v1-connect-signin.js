// Desk v1 — Connections: sign in with a saved login (docs/DESK_SERVICE_PROFILES_SPEC.md, slice P2b).
// Two things a sign-in route offers next to each other, in the Connect flow's Routes screen
// (desk-v1-connect-purpose.js) and in the Result step of a provider sign-in (desk-v1-connect-result.js):
//
//   Use a saved login     a vault login picked BY NAME (the owner's picker does that).
//   Store a new login     the shared secret form (static/js/secret-form.js): username and password on ONE
//                         vault entry. It is typed here and sent only with the passcode-gated Save of the
//                         owning screen (`new_login` rides the routes Save and the Details step's provider
//                         Save, so one passcode covers it and the connection; the Result step, where the
//                         connection is already saved, has its own Save to /api/desk/connect/signin/store-login).
//                         Nothing is written before that.
//   Sign in with it       POST /api/desk/connect/signin/fill (through the passcode prompt, every click): the
//                         SERVER types the stored login into the sign-in page open in the browser pane. This
//                         module sends a login NAME, never a value, and gets a state word back (submitted,
//                         handed to you, no form, refused).
//
// A password lives only in the form's own input: nothing here copies it into a variable, a draft, a
// summary, storage or a log (`read` hands it to the one caller that sends it; `clear` empties the input).
// Window-bridged module, no `import` (ground rule 1). Server side: mc/desk_connect/signin_*.py and
// mc/blueprints/desk_connect_signin_routes.py.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const forms = {};                 // key -> { idx, host, open, state }
  const fills = {};                 // key -> { busy, kind, text }
  let _n = 0;

  function _f(key) { return forms[key] || (forms[key] = { idx: ++_n, host: null, open: false, state: { type: 'login' } }); }
  function _p(key) { return `csf${_f(key).idx}`; }

  // ── store a new login ───────────────────────────────────────────────────
  // The toggle and the (empty) slot the form is mounted into. `mount` keeps the form's node alive
  // across the owner's repaints, so a typed password survives them.
  function newLoginHTML(key) {
    const f = _f(key);
    return `<div class="desk-v1-cs-new" data-cs-new="${esc(key)}">
        <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cs-toggle="${esc(key)}" aria-expanded="${f.open}">${f.open ? 'Do not store a new login' : 'Store a new login…'}</button>
        <div class="desk-v1-cs-form" data-cs-slot="${esc(key)}" ${f.open ? '' : 'hidden'}></div>
      </div>`;
  }

  function mount(root, key, hint, onChange) {
    const slot = root.querySelector(`[data-cs-slot="${key}"]`);
    const toggle = root.querySelector(`[data-cs-toggle="${key}"]`);
    if (!slot) return;
    const f = _f(key);
    const SF = window.SecretForm;
    if (f.host) slot.appendChild(f.host);
    else if (f.open) {
      f.host = document.createElement('div');
      f.host.dataset.csFormHost = key;
      f.host.innerHTML = SF.fieldsHtml({ p: _p(key), isNew: true, name: '', projects: null, numbered: false, intro: false });
      slot.appendChild(f.host);
      SF.bind(_p(key), f.state, {});
      SF.render(_p(key), f.state);
      const nm = document.getElementById(`${_p(key)}-name`);
      if (nm && hint) nm.value = hint;
      const row = document.getElementById(`${_p(key)}-type-row`);      // a sign-in is a login: the type is not a choice here
      if (row && row.parentElement) row.parentElement.hidden = true;
      const fa = document.getElementById(`${_p(key)}-2fa-help`);
      if (fa) fa.hidden = true;
      if (onChange) onChange();            // the owner's summary and Save state were drawn before the form existed
    }
    if (toggle) toggle.addEventListener('click', () => {
      f.open = !f.open;
      if (!f.open) clear(key);
      if (onChange) onChange();
    });
  }

  // The form's content, or `{ error }`, or `null` when no new login is being typed. The one place the
  // password is read; the caller sends it in the passcode-gated Save and nowhere else.
  function read(key) {
    const f = forms[key];
    if (!f || !f.open || !f.host) return null;
    const c = window.SecretForm.read(_p(key), f.state);
    if (c.error) return { error: c.error };
    if (!c.username) return { error: 'Enter the username: it is typed on the sign-in page with the password.' };
    return { new_login: { name: c.name, username: c.username, value: c.value, description: c.description, allow_unattended: c.allow_unattended } };
  }

  // What is typed, minus the password, for a review line.
  function meta(key) {
    const f = forms[key];
    if (!f || !f.open || !f.host) return null;
    const m = window.SecretForm.meta(_p(key), f.state);
    return { name: m.name, username: m.username, hasValue: m.hasValue };
  }

  function clear(key) {
    const f = forms[key];
    if (!f) return;
    if (f.host) window.SecretForm.clear(_p(key));
    if (f.host && f.host.parentNode) f.host.parentNode.removeChild(f.host);
    f.host = null;
  }

  function clearFill(key) { delete fills[key]; }

  function reset() { Object.keys(forms).forEach((k) => { clear(k); delete forms[k]; }); Object.keys(fills).forEach((k) => { delete fills[k]; }); }

  // ── sign in with it ─────────────────────────────────────────────────────
  // `spec` = { url, login, profile } : the sign-in page to open (or null), and whether a login and a pane
  // profile are known. The click handler is wired by `bindFill` with the request body the owner builds.
  function fillHTML(key, spec) {
    const st = fills[key] || {};
    const can = !!spec.login && !!spec.profile;
    const why = !spec.login ? (spec.why || 'Choose a stored login (or store a new one) and save first.') : !spec.profile ? 'Open the sign-in page in the browser pane first.' : '';
    return `<div class="desk-v1-cs-fill" data-cs-fill="${esc(key)}">
        <div class="desk-v1-cs-fill-row">
          ${spec.url ? `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cs-open="${esc(key)}">Open the sign-in page</button>` : ''}
          <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cs-fillbtn="${esc(key)}" ${can && !st.busy ? '' : 'disabled'} ${why ? `title="${esc(why)}"` : ''}>${st.busy ? 'Signing in…' : 'Sign in with the saved login'}</button>
        </div>
        ${spec.hint === '' ? '' : '<div class="desk-v1-rules-hint">Clayrune types the saved login into the sign-in page open in the browser pane, only on this route\'s own sign-in address. If the page asks for a code or a CAPTCHA, it stops and the pane is yours. The password is never shown to you or to an agent.</div>'}
        ${st.text ? `<div class="desk-v1-cf-msg" data-cs-fillmsg="${esc(st.kind || 'ok')}" data-cf-msg="${st.kind === 'error' ? 'error' : 'ok'}" role="${st.kind === 'error' ? 'alert' : 'status'}">${esc(st.text)}</div>` : ''}
      </div>`;
  }

  // `body()` builds the request ({ service, route_id, login | account_id, profile }); `pane` opens the page.
  function bindFill(root, key, spec, ctx, body) {
    const open = root.querySelector(`[data-cs-open="${key}"]`);
    if (open) open.addEventListener('click', () => {
      try { if (typeof window.openBrowserPane === 'function') window.openBrowserPane(spec.url, null, null, spec.profile || null); } catch (_) { /* the pane failing to open is shown by the pane */ }
    });
    const btn = root.querySelector(`[data-cs-fillbtn="${key}"]`);
    if (!btn) return;
    btn.addEventListener('click', async () => {
      const st = (fills[key] = { busy: true, kind: 'ok', text: '' });
      ctx.repaint();
      try {
        // Typing a stored login into a page needs the dashboard passcode on every click: the one proof an agent cannot forge.
        const res = await window.humanProofFetch('/api/desk/connect/signin/fill', { method: 'POST', body: JSON.stringify(body()) },
          { title: 'Sign in', description: 'Re-enter your dashboard passcode to have Clayrune type the saved login into the sign-in page.' });
        if (res === null) { st.busy = false; st.text = ''; ctx.repaint(); return; }          // cancelled at the passcode: nothing was sent
        const out = res.body || {};
        if (!res.ok) {
          st.kind = 'error';
          st.text = out.error || 'Could not sign in (HTTP ' + res.status + ').';
        } else {
          st.kind = out.state === 'no_form' ? 'error' : 'ok';
          st.text = out.message || 'Done. Check the browser pane.';
        }
      } catch (e) {
        st.kind = 'error';
        st.text = e && e.message ? e.message : 'Could not sign in.';
      }
      st.busy = false;
      ctx.repaint();
    });
  }

  // ── the Result step of a provider sign-in (Higgsfield's "Sign in with Higgsfield") ──────────────
  // That route has no routes screen, so the same three things are offered here, under the sign-in
  // that has just opened in its named pane: pick a saved login, or store a new one (its own passcode
  // Save), then "Sign in with the saved login". Loaded from /api/desk/connect/signin/options (names only).
  const KEY = 'result';
  let R = null;      // { res, ctx, route, logins, locked, pick, busy, msg, kind }

  // ── the Details step of a provider sign-in, before Save ────────────────────────────────────────────
  // EVERY way to sign in is drawn here, side by side (Ron 2026-10-05: "if step 3 is the login details, all
  // options should be covered there"): the browser sign-in itself (desk-v1-connect-held.js: held for the Save),
  // a saved login picked by name, and a new login typed and stored with its own passcode ("Save this login").
  // The saved/new login is what Clayrune types into the sign-in page once the browser option has opened it.
  // Only a pick (a name) and the typed form's DOM node are kept; no password is held here.
  let D = null;      // { service, method, label, ctx, route, logins, locked, pick, failed, busy, msg, kind }

  function detailsStart(service, method, ctx, label) {
    if (window.DeskV1ConnectHeld) window.DeskV1ConnectHeld.begin(service, method, label || service, ctx);
    if (D && D.service === service && D.method === method) { D.ctx = ctx; return; }
    if (D) reset();                       // another service or method: a login typed for the old one is not carried over
    const mine = { service, method, label: label || service, ctx, route: null, logins: [], locked: false, pick: '', failed: false, busy: false, msg: '', kind: 'ok', loaded: false };
    D = mine;
    ctx.api('POST', '/api/desk/connect/signin/options', { service }).then((o) => {
      if (D !== mine) return;
      mine.route = (o.routes || []).find((r) => r.connect_method === method) || null;
      mine.logins = o.logins || []; mine.locked = !!o.vault_locked; mine.loaded = true;
      mine.ctx.repaint();
    }).catch(() => { if (D === mine) { mine.failed = true; mine.ctx.repaint(); } });
  }

  function _loginOptions(logins, pick) {
    return ['<option value="">Choose a saved login…</option>'].concat(logins.map((l) => `<option value="${esc(l.name)}"${pick === l.name ? ' selected' : ''}>${esc(l.name)}</option>`)).join('');
  }

  // Type a new login and press "Save this login": stored with its own passcode, then selected. Used by
  // the Result step only, where the connection is already saved. On the Details step the typed login
  // rides the connection's own Save (`detailsDraft`), so the passcode is asked once.
  async function _storeTyped(st, serviceId, label, again) {
    const typed = read(KEY);
    if (!typed || typed.error) { st.kind = 'error'; st.msg = (typed && typed.error) || 'Type the login first.'; again(); return; }
    st.busy = true; st.msg = ''; again();
    let out;
    try {
      out = await window.humanProofFetch('/api/desk/connect/signin/store-login', {
        method: 'POST', body: JSON.stringify({ service: serviceId, route_id: st.route.route_id, new_login: typed.new_login }),
      }, { title: 'Store login', description: `Re-enter your dashboard passcode to store the ${label} login.` });
    } catch (e) { out = { ok: false, status: 0, body: { error: e && e.message ? e.message : 'could not reach the server' } }; }
    st.busy = false;
    if (out === null) { again(); return; }                       // cancelled at the passcode: nothing was sent
    if (!out.ok) { st.kind = 'error'; st.msg = (out.body && out.body.error) || `The save failed (HTTP ${out.status}).`; again(); return; }
    const name = out.body && out.body.login && out.body.login.name;
    clear(KEY); forms[KEY].open = false;                          // the password leaves the page the moment it is stored
    if (name && !st.logins.some((l) => l.name === name)) st.logins.unshift({ name, matches: true });
    st.pick = name || st.pick; st.kind = 'ok'; st.msg = name ? `Stored ${name}. It is selected.` : 'Stored.';
    again();
  }

  function _saveBtn(st) {
    const typing = forms[KEY] && forms[KEY].open;
    return typing ? `<div class="desk-v1-cf-actions"><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cf-primary" data-cs-savelogin ${st.busy ? 'disabled' : ''}>Save this login</button></div>` : '';
  }

  function _msgHTML(st) {
    return st.msg ? `<div class="desk-v1-cf-msg" data-cs-msg="${esc(st.kind)}" data-cf-msg="${st.kind === 'error' ? 'error' : 'ok'}" role="${st.kind === 'error' ? 'alert' : 'status'}">${esc(st.msg)}</div>` : '';
  }

  function _opt(id, title, body) {
    return `<div class="desk-v1-cs-option" data-cs-opt="${id}"><div class="desk-v1-cs-opt-title">${esc(title)}</div>${body}</div>`;
  }

  function detailsHTML() {
    if (!D) return '';
    const Hd = window.DeskV1ConnectHeld;
    let saved, fresh;
    if (D.failed) {
      saved = fresh = '<div class="desk-v1-rules-hint" data-cs-details-failed>The saved logins could not be loaded. You can still sign in in the browser.</div>';
    } else if (!D.route) {
      saved = fresh = '<div class="desk-v1-rules-hint" data-cs-details-loading>Loading your saved logins…</div>';
    } else {
      saved = `<div class="desk-v1-rules-hint">A login already in the Vault. Clayrune types it into the sign-in page once it is open; you never see the password.</div>
        ${D.logins.length ? `<label class="desk-v1-conn-add-field">Use a saved login
          <select class="desk-v1-rules-textinput" data-cs-pick>${_loginOptions(D.logins, D.pick)}</select></label>` : '<div class="desk-v1-rules-hint" data-cs-nologins>There is no saved login for this service yet.</div>'}`;
      fresh = `<div class="desk-v1-rules-hint">Type a username and password to keep in the Vault. It is stored when you press Save on step 4, together with the connection: one passcode. Nothing is written before that.</div>
        ${newLoginHTML(KEY)}`;
    }
    return `<section class="desk-v1-cs-pick" data-cs-details aria-label="Ways to sign in">
        <div class="desk-v1-rules-group-title">Ways to sign in</div>
        <div class="desk-v1-rules-hint">Everything you can do to sign in to ${esc(D.label)} is here. Step 4 only reviews and saves what you chose or did.${D.locked ? ' The vault is locked: unlock it here, then try again.' : ''}${D.locked && window.VaultUnlockUI ? window.VaultUnlockUI.buttonHTML({ vault_locked: true }) : ''}</div>
        <div class="desk-v1-cs-options" data-cs-options>
          ${_opt('browser', 'In the browser', Hd ? Hd.html() : '')}
          ${D.loaded && !D.route ? '' : _opt('saved', 'A saved login', saved)}
          ${D.loaded && !D.route ? '' : _opt('new', 'A new login', fresh)}
        </div>
        ${_msgHTML(D)}
        ${D.route ? fillHTML(KEY, { url: null, login: D.pick, profile: Hd ? Hd.profile() : '' }) : ''}
      </section>`;
  }

  function detailsBind(root) {
    if (!D) return;
    const mine = D;
    const box = root.querySelector('[data-cs-details]');
    if (!box) return;
    if (window.DeskV1ConnectHeld) window.DeskV1ConnectHeld.bind(root);
    if (!mine.route) return;
    const again = () => mine.ctx.repaint();
    const pick = box.querySelector('[data-cs-pick]');
    if (pick) pick.addEventListener('change', () => { mine.pick = pick.value; mine.msg = ''; again(); });
    mount(box, KEY, `${mine.service}.login`, again);
    const profile = () => (window.DeskV1ConnectHeld ? window.DeskV1ConnectHeld.profile() : '');
    bindFill(box, KEY, { url: null, profile: profile() }, mine.ctx,
      () => ({ service: mine.service, route_id: mine.route.route_id, login: mine.pick, profile: profile() }));
  }

  // Review's rows for the sign-in and the login choice, with no password in them. The typed form's node is kept
  // in a hidden slot here so it survives the step (and so its facts can be read back); the rows are filled in by the bind.
  function detailsReviewHTML() {
    if (!D) return '';
    return `<div data-cs-review></div>${D.route ? `<div data-cs-slot="${KEY}" hidden></div>` : ''}`;
  }

  function detailsReviewBind(root) {
    if (!D) return;
    const box = root.querySelector('[data-cs-review]');
    if (!box) return;
    const slot = root.querySelector(`[data-cs-slot="${KEY}"]`);
    const f = forms[KEY];
    if (slot && f && f.host) slot.appendChild(f.host);
    const typed = slot ? meta(KEY) : null;
    let row = window.DeskV1ConnectHeld ? window.DeskV1ConnectHeld.reviewRowHTML() : '';
    if (typed) {
      row += `<div><dt>Sign-in login</dt><dd data-cs-r-login>New: <code>${esc(typed.name)}</code>${typed.username ? `, username ${esc(typed.username)}` : ''}; password ${typed.hasValue ? 'entered, hidden: shown nowhere' : 'not entered'}. Stored when you press Save, with the connection.</dd></div>`;
    } else if (D.pick) {
      row += `<div><dt>Sign-in login</dt><dd data-cs-r-login><code>${esc(D.pick)}</code>, typed into the sign-in page when you ask</dd></div>`;
    }
    box.innerHTML = row ? `<dl class="desk-v1-cf-facts">${row}</dl>` : '';
  }

  // The typed login for the connection's Save: `null` when none is being typed, `{ error }` when what is
  // typed will not do, else `{ new_login }`. The one place the Details form's password is read; the caller
  // puts it in the passcode-gated commit draft and nowhere else.
  function detailsDraft() {
    return D && D.route ? read(KEY) : null;
  }

  // The Save went through: the password is in the vault, so it leaves the page, and the stored login becomes
  // the pick the Result step carries (it is what "Sign in with the saved login" types). `login` is the server's
  // value-free `{ name, ... }`, absent when nothing was typed.
  function detailsSaved(login) {
    if (!D) return;
    if (login && login.name) {
      if (!D.logins.some((l) => l.name === login.name)) D.logins.unshift({ name: login.name, matches: true });
      D.pick = login.name;
    }
    if (forms[KEY]) { clear(KEY); forms[KEY].open = false; }
  }

  function resultStart(res, ctx) {
    R = null;
    if (!res || !res.signin || !res.service || !ctx) return;
    const mine = { res, ctx, route: null, logins: [], locked: false, pick: (D && D.service === res.service.id && D.method === res.method) ? D.pick : '', busy: false, msg: '', kind: 'ok' };
    R = mine;
    ctx.api('POST', '/api/desk/connect/signin/options', { service: res.service.id }).then((o) => {
      if (R !== mine) return;
      mine.route = (o.routes || []).find((r) => r.connect_method === res.method) || null;
      mine.logins = o.logins || []; mine.locked = !!o.vault_locked;
      ctx.repaint();
    }).catch(() => { /* an optional panel: without its options the Result step is as it was */ });
  }

  function resultHTML() {
    if (!R || !R.route) return '';
    const opts = ['<option value="">Choose a saved login…</option>'].concat(R.logins.map((l) => `<option value="${esc(l.name)}"${R.pick === l.name ? ' selected' : ''}>${esc(l.name)}</option>`));
    const typing = forms[KEY] && forms[KEY].open;
    return `<section class="desk-v1-cs-pick" data-cs-result aria-label="Sign in with a saved login">
        <div class="desk-v1-rules-group-title">Sign in with a saved login</div>
        <div class="desk-v1-rules-hint">Optional. Clayrune can type a username and password you have stored into the sign-in page that just opened, instead of you typing them in the pane.${R.locked ? ' The vault is locked: unlock it here, then try again.' : ''}${R.locked && window.VaultUnlockUI ? window.VaultUnlockUI.buttonHTML({ vault_locked: true }) : ''}</div>
        ${R.logins.length ? `<label class="desk-v1-conn-add-field">Use a saved login
          <select class="desk-v1-rules-textinput" data-cs-pick>${opts.join('')}</select></label>` : '<div class="desk-v1-rules-hint" data-cs-nologins>There is no saved login for this service yet.</div>'}
        ${newLoginHTML(KEY)}
        ${typing ? `<div class="desk-v1-cf-actions"><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cf-primary" data-cs-savelogin ${R.busy ? 'disabled' : ''}>Save this login</button></div>` : ''}
        ${R.msg ? `<div class="desk-v1-cf-msg" data-cs-msg="${esc(R.kind)}" data-cf-msg="${R.kind === 'error' ? 'error' : 'ok'}" role="${R.kind === 'error' ? 'alert' : 'status'}">${esc(R.msg)}</div>` : ''}
        ${fillHTML(KEY, { url: null, login: R.pick, profile: R.res.signin.profile })}
      </section>`;
  }

  function resultBind(root) {
    if (!R || !R.route) return;
    const mine = R;
    const again = () => mine.ctx.repaint();
    const box = root.querySelector('[data-cs-result]');
    if (!box) return;
    const pick = box.querySelector('[data-cs-pick]');
    if (pick) pick.addEventListener('change', () => { mine.pick = pick.value; mine.msg = ''; again(); });
    mount(box, KEY, `${mine.res.service.id}.login`, again);
    const save = box.querySelector('[data-cs-savelogin]');
    if (save) save.addEventListener('click', () => _storeTyped(mine, mine.res.service.id, mine.res.service.label, again));
    bindFill(box, KEY, { url: null, profile: mine.res.signin.profile }, mine.ctx,
      () => ({ service: mine.res.service.id, route_id: mine.route.route_id, login: mine.pick, profile: mine.res.signin.profile }));
  }

  function resultReset() { R = null; D = null; reset(); }

  window.DeskV1ConnectSignin = { newLoginHTML, mount, read, meta, clear, reset, clearFill, fillHTML, bindFill, resultStart, resultHTML, resultBind, resultReset,
    detailsStart, detailsHTML, detailsBind, detailsReviewHTML, detailsReviewBind, detailsDraft, detailsSaved };
})();

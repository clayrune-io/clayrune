// Desk v1 — Connections: sign in with a saved login (docs/DESK_SERVICE_PROFILES_SPEC.md, slice P2b).
// Two things a sign-in route offers next to each other, in the Connect flow's Routes screen
// (desk-v1-connect-purpose.js) and in the Result step of a provider sign-in (desk-v1-connect-result.js):
//
//   Use a saved login     a vault login picked BY NAME (the owner's picker does that).
//   Store a new login     the shared secret form (static/js/secret-form.js): username and password on ONE
//                         vault entry. It is typed here and sent only with the passcode-gated Save of the
//                         owning screen (`new_login` rides the routes Save; the Result step has its own
//                         Save to /api/desk/connect/signin/store-login). Nothing is written before that.
//   Sign in with it       POST /api/desk/connect/signin/fill: the SERVER types the stored login into the
//                         sign-in page open in the browser pane. This module sends a login NAME, never a
//                         value, and gets a state word back (submitted, handed to you, no form, refused).
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

  function reset() { Object.keys(forms).forEach((k) => { clear(k); delete forms[k]; }); Object.keys(fills).forEach((k) => { delete fills[k]; }); }

  // ── sign in with it ─────────────────────────────────────────────────────
  // `spec` = { url, login, profile } : the sign-in page to open (or null), and whether a login and a pane
  // profile are known. The click handler is wired by `bindFill` with the request body the owner builds.
  function fillHTML(key, spec) {
    const st = fills[key] || {};
    const can = !!spec.login && !!spec.profile;
    const why = !spec.login ? 'Choose a stored login (or store a new one) and save first.' : !spec.profile ? 'Open the sign-in page in the browser pane first.' : '';
    return `<div class="desk-v1-cs-fill" data-cs-fill="${esc(key)}">
        <div class="desk-v1-cs-fill-row">
          ${spec.url ? `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cs-open="${esc(key)}">Open the sign-in page</button>` : ''}
          <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cs-fillbtn="${esc(key)}" ${can && !st.busy ? '' : 'disabled'} ${why ? `title="${esc(why)}"` : ''}>${st.busy ? 'Signing in…' : 'Sign in with the saved login'}</button>
        </div>
        <div class="desk-v1-rules-hint">Clayrune types the saved login into the sign-in page open in the browser pane, only on this route's own sign-in address. If the page asks for a code or a CAPTCHA, it stops and the pane is yours. The password is never shown to you or to an agent.</div>
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
        const out = await ctx.api('POST', '/api/desk/connect/signin/fill', body());
        st.kind = out.state === 'handoff' ? 'ok' : out.state === 'no_form' ? 'error' : 'ok';
        st.text = out.message || 'Done. Check the browser pane.';
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

  function resultStart(res, ctx) {
    R = null;
    if (!res || !res.signin || !res.service || !ctx) return;
    const mine = { res, ctx, route: null, logins: [], locked: false, pick: '', busy: false, msg: '', kind: 'ok' };
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
        <div class="desk-v1-rules-hint">Optional. Clayrune can type a username and password you have stored into the sign-in page that just opened, instead of you typing them in the pane.${R.locked ? ' The vault is locked: unlock it in Settings, then try again.' : ''}</div>
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
    if (save) save.addEventListener('click', async () => {
      const typed = read(KEY);
      if (!typed || typed.error) { mine.kind = 'error'; mine.msg = (typed && typed.error) || 'Type the login first.'; again(); return; }
      mine.busy = true; mine.msg = ''; again();
      let out;
      try {
        out = await window.humanProofFetch('/api/desk/connect/signin/store-login', {
          method: 'POST', body: JSON.stringify({ service: mine.res.service.id, route_id: mine.route.route_id, new_login: typed.new_login }),
        }, { title: 'Store login', description: `Re-enter your dashboard passcode to store the ${mine.res.service.label} login.` });
      } catch (e) { out = { ok: false, status: 0, body: { error: e && e.message ? e.message : 'could not reach the server' } }; }
      mine.busy = false;
      if (out === null) { again(); return; }                       // cancelled at the passcode: nothing was sent
      if (!out.ok) { mine.kind = 'error'; mine.msg = (out.body && out.body.error) || `The save failed (HTTP ${out.status}).`; again(); return; }
      const name = out.body && out.body.login && out.body.login.name;
      clear(KEY); forms[KEY].open = false;                          // the password leaves the page the moment it is stored
      if (name && !mine.logins.some((l) => l.name === name)) mine.logins.unshift({ name, matches: true });
      mine.pick = name || mine.pick; mine.kind = 'ok'; mine.msg = name ? `Stored ${name}. It is selected below.` : 'Stored.';
      again();
    });
    bindFill(box, KEY, { url: null, profile: mine.res.signin.profile }, mine.ctx,
      () => ({ service: mine.res.service.id, route_id: mine.route.route_id, login: mine.pick, profile: mine.res.signin.profile }));
  }

  function resultReset() { R = null; reset(); }

  window.DeskV1ConnectSignin = { newLoginHTML, mount, read, meta, clear, reset, fillHTML, bindFill, resultStart, resultHTML, resultBind, resultReset };
})();

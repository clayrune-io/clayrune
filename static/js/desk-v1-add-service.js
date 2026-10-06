// Desk v1 — Connections: the Add service panel (Ron 2026-10-03: "only a single tile
// Add service ... it should avoid any specific patterns so the user can add any
// service of his choosing"). The last tile of the grid opens ONE generic flow: a
// search box over a single list of everything Clayrune can actually connect today,
// then "Something else" for a service it has no integration for. Window-bridged
// module, no `import` (ground rule 1).
//
//   Social account   X, the LinkedIn Company Page, a blog: the account form below
//                    (live: POST /api/desk/accounts; its Connect steps open in the
//                    account's own detail afterwards).
//   Generation engine  each engine that is not connected yet: its own guided connect
//                    card (desk-v1-engines.js), unchanged.
//   Something else   a name, an optional link, an optional vault credential NAME:
//                    desk-v1-services.js (it says "Saved for agents", never "Connected").
//
// Content sources (Google Drive, Dropbox) are not in the list: Clayrune has no way to
// connect one today, and a list of "everything it can actually connect" must not
// offer what it cannot. A credential VALUE is never asked for on this panel.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const ACCOUNT_CHOICES = [
    { platform: 'x', label: 'X account', hint: 'Post and read as an X account', placeholder: '@handle' },
    { platform: 'linkedin', label: 'LinkedIn Company Page', hint: 'The Clayrune page', placeholder: 'the page name' },
    { platform: 'blog', label: 'Blog', hint: 'You publish it yourself', placeholder: 'the blog name' },
  ];

  let _pick = null;          // null = the list; 'account:<platform>' | 'engine:<id>' | 'other'
  let _query = '';
  function reset() { _pick = null; _query = ''; if (window.DeskV1ConnectFlow) window.DeskV1ConnectFlow.reset(); }
  // The engine id the panel is showing the connect card of, or null.
  function pickedEngine() { return _pick && _pick.indexOf('engine:') === 0 ? _pick.slice(7) : null; }

  function _choices(ctx) {
    const out = ACCOUNT_CHOICES.map((a) => ({ key: `account:${a.platform}`, label: a.label, kind: 'Social account', hint: a.hint }));
    const engines = window.DeskV1Engines;
    (ctx.engines || []).filter((e) => !engines.tileState(e)).forEach((e) => {
      out.push({ key: `engine:${e.id}`, label: e.label, kind: 'Generation engine', hint: engines.kindsOf(e) });
    });
    return out;
  }

  function _listHTML(ctx) {
    const items = _choices(ctx).map((c) => `
          <li><button type="button" class="desk-v1-add-item" data-add-pick="${esc(c.key)}" data-add-text="${esc(`${c.label} ${c.kind} ${c.hint}`.toLowerCase())}">
            <span class="desk-v1-add-item-name">${esc(c.label)}</span>
            <span class="desk-v1-add-item-kind">${esc(c.kind)}${c.hint ? ` · ${esc(c.hint)}` : ''}</span>
          </button></li>`);
    items.push(`
          <li><button type="button" class="desk-v1-add-item desk-v1-add-item-other" data-add-pick="other">
            <span class="desk-v1-add-item-name">Something else</span>
            <span class="desk-v1-add-item-kind">Any other service: saved so agents can see it</span>
          </button></li>`);
    return `
        <label class="desk-v1-conn-add-field">Find a service
          <input type="search" class="desk-v1-rules-textinput" data-add-search autocomplete="off" placeholder="Search, or pick Something else" value="${esc(_query)}"></label>
        <ul class="desk-v1-add-list" data-add-list role="list">${items.join('')}</ul>
        <div class="desk-v1-rules-hint" data-add-nomatch hidden>Nothing here matches. If it is not in the list, pick Something else.</div>
        ${ctx.engineError ? `<div class="desk-v1-rules-hint" data-add-engines-error>Could not load the engines: ${esc(ctx.engineError)}</div>` : ''}`;
  }

  function _accountFormHTML(platform) {
    const a = ACCOUNT_CHOICES.find((c) => c.platform === platform) || ACCOUNT_CHOICES[0];
    return `
        <form class="desk-v1-conn-add" data-conn-add autocomplete="off">
          <div class="desk-v1-rules-group-title">Add ${esc(a.label)}</div>
          <input type="hidden" data-conn-add-platform value="${esc(platform)}">
          <label class="desk-v1-conn-add-field">Handle or name
            <input type="text" class="desk-v1-rules-textinput" data-conn-add-identity maxlength="80" placeholder="${esc(a.placeholder)}"></label>
          <label class="desk-v1-conn-add-field">Label (optional)
            <input type="text" class="desk-v1-rules-textinput" data-conn-add-label maxlength="80"></label>
          <div class="desk-v1-conn-add-actions">
            <button type="submit" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-conn-add-submit>Add account</button>
            <span class="desk-v1-rules-hint" data-conn-add-status role="status"></span>
          </div>
        </form>`;
  }

  function _otherFormHTML() {
    return `
        <form class="desk-v1-conn-add" data-svc-add autocomplete="off">
          <div class="desk-v1-rules-group-title">Something else</div>
          <div class="desk-v1-rules-hint">Clayrune has no integration for this service. It is saved so agents can see it and use the credential you name; Clayrune does not connect to it or post to it.</div>
          <label class="desk-v1-conn-add-field">Name
            <input type="text" class="desk-v1-rules-textinput" data-svc-add-name maxlength="80" placeholder="e.g. Plausible analytics"></label>
          <label class="desk-v1-conn-add-field">Link (optional)
            <input type="text" class="desk-v1-rules-textinput" data-svc-add-link maxlength="300" placeholder="https://"></label>
          <label class="desk-v1-conn-add-field">Credential name in Secrets (optional)
            <input type="text" class="desk-v1-rules-textinput" data-svc-add-cred maxlength="64" placeholder="the name of an entry in Secrets, never the secret itself"></label>
          <div class="desk-v1-conn-add-actions">
            <button type="submit" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-svc-add-submit>Save service</button>
            <span class="desk-v1-rules-hint" data-svc-add-status role="status"></span>
          </div>
        </form>`;
  }

  // ctx: { engines: [] | null, engineError: string | null }
  function panelHTML(ctx) {
    let body;
    const flow = window.DeskV1ConnectFlow;
    const flowHTML = flow ? flow.html({ live: window.DeskV1Store.live(), engines: ctx.engines }) : '';
    if (flow && flow.active()) {
      body = flowHTML;                     // a connect-by-address flow past its first step owns the panel
    } else if (!_pick) {
      body = flowHTML + (flowHTML ? '<div class="desk-v1-cf-or">Or pick from the list</div>' : '') + _listHTML(ctx);
    } else {
      let form = '';
      if (_pick === 'other') form = _otherFormHTML();
      else if (_pick.indexOf('account:') === 0) form = _accountFormHTML(_pick.slice(8));
      else {
        const e = (ctx.engines || []).find((x) => `engine:${x.id}` === _pick);
        form = e ? `<div data-add-engine="${esc(e.id)}">${window.DeskV1Engines.rowHTML(e)}</div>`
          : `<div class="desk-v1-stub-empty">${ctx.engines ? 'That engine is not available.' : 'Loading…'}</div>`;
      }
      body = `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-add-back>‹ All services</button>${form}`;
    }
    return `<div class="desk-v1-add" data-add-service><div class="desk-v1-rules-group-title">Add service</div>${body}</div>`;
  }

  // The account an "Add account" form saves. Live: the server's answer replaces the
  // provisional `publish`. Demo: a fixture-shaped account, flagged as added by the
  // user (so it shows while unconnected), with nothing sent anywhere.
  function _newAccount(live, platform, identity, label) {
    const id = 'acct-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    const body = { id, platform, identity, capability: platform === 'blog' ? 'manual' : 'direct' };
    if (label) body.label = label;
    const base = Object.assign({ label: label || identity, voice: '' }, body);
    const acc = live
      ? Object.assign(base, { connected: false, publish: { ready: false, reason: 'checking…', secret: null, unattended_ok: null } })
      : Object.assign(base, { connected: false, health: 'ok', userAdded: true });
    return { id, body, acc };
  }

  function _bindAccountForm(form, ctx) {
    const status = form.querySelector('[data-conn-add-status]');
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const platform = form.querySelector('[data-conn-add-platform]').value;
      const identity = form.querySelector('[data-conn-add-identity]').value.trim();
      const label = form.querySelector('[data-conn-add-label]').value.trim();
      if (!identity) { status.textContent = 'Enter the handle or page name.'; return; }
      status.textContent = '';
      const live = window.DeskV1Store.live();
      const { id, body, acc } = _newAccount(live, platform, identity, label);
      const list = ctx.channels();
      const Tiles = window.DeskV1ConnTiles;
      const done = () => { reset(); Tiles.select(id); ctx.repaint(); };
      window.DeskV1Store.write({
        label: `Added ${acc.label}${live ? '' : ' (preview: nothing was saved)'}`,
        apply: () => { list.push(acc); done(); },
        unapply: () => { const i = list.indexOf(acc); if (i >= 0) list.splice(i, 1); },
        repaint: ctx.repaint,
        request: () => ctx.api('POST', '/api/desk/accounts', body).then((saved) => { Object.assign(acc, saved); ctx.repaint(); return saved; }),
        undoRequest: () => ctx.api('DELETE', `/api/desk/accounts/${encodeURIComponent(id)}`),
      });
    });
  }

  function _bindOtherForm(form, ctx) {
    const status = form.querySelector('[data-svc-add-status]');
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const name = form.querySelector('[data-svc-add-name]').value.trim();
      const link = form.querySelector('[data-svc-add-link]').value.trim();
      const credential = form.querySelector('[data-svc-add-cred]').value.trim();
      if (!name) { status.textContent = 'Enter a name for the service.'; return; }
      status.textContent = '';
      const body = { id: 'svc-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6), name };
      if (link) body.link = link;
      if (credential) body.credential = credential;
      window.DeskV1Services.create(body, ctx.repaint).then((r) => {
        if (!r.ok) { status.textContent = r.error || 'could not save'; return; }
        reset();
        window.DeskV1ConnTiles.select(`service:${body.id}`);
        ctx.repaint();
      });
    });
  }

  // ctx: { repaint, channels(), api, engines, onEnginesChanged() }
  function bind(el, ctx) {
    const root = el.querySelector('[data-add-service]');
    if (!root) return;
    // The flows in this panel store a secret (a sign-in, a login, a key): a locked vault is shown, with its unlock, first.
    if (window.DeskV1VaultGate) window.DeskV1VaultGate.attach(root);
    if (window.DeskV1ConnectFlow) window.DeskV1ConnectFlow.bind(root, {
      live: window.DeskV1Store.live(), api: ctx.api, engines: ctx.engines, repaint: ctx.repaint, channels: ctx.channels,
      // "Open the guide" on a method row: hand over to the flow Connections already has.
      openPick: (key) => {
        window.DeskV1ConnectFlow.reset();
        const id = key.indexOf('engine:') === 0 ? key.slice(7) : null;
        const eng = id && (ctx.engines || []).find((x) => x.id === id);
        if (eng && window.DeskV1Engines.tileState(eng)) window.DeskV1ConnTiles.select(key);   // already connected: its own tile
        else _pick = key;
        ctx.repaint();
      },
      // Saved: the new record is the server's; show it as its own tile.
      onSaved: (svc, info) => {
        reset();
        if (info && info.provider) {        // a known-host method: its own tile, status as the server derived it
          if (ctx.onEnginesChanged) ctx.onEnginesChanged();
          window.DeskV1Kit.toast(`${svc && svc.label ? svc.label : 'The service'} is connected${info.status && info.status.label ? ` (${info.status.label.toLowerCase()})` : ''}.`);
          ctx.repaint();
          return;
        }
        window.DeskV1Services.load(true).then(() => {
          if (svc && svc.id) window.DeskV1ConnTiles.select(`service:${svc.id}`);
          window.DeskV1Kit.toast(`Saved ${svc && svc.name ? svc.name : 'the service'} for agents${info && info.credentialStored ? ' and stored its credential (not verified)' : ''}.`);
          ctx.repaint();
        });
      },
    });
    root.querySelectorAll('[data-add-pick]').forEach((b) => b.addEventListener('click', () => { _pick = b.dataset.addPick; ctx.repaint(); }));
    const back = root.querySelector('[data-add-back]');
    if (back) back.addEventListener('click', () => { _pick = null; ctx.repaint(); });
    const search = root.querySelector('[data-add-search]');
    if (search) {
      const apply = () => {
        _query = search.value;
        const q = _query.trim().toLowerCase();
        let shown = 0;
        root.querySelectorAll('[data-add-text]').forEach((b) => {
          const hit = !q || b.dataset.addText.indexOf(q) >= 0;
          b.parentElement.hidden = !hit;
          if (hit) shown++;
        });
        root.querySelector('[data-add-nomatch]').hidden = shown > 0;
      };
      search.addEventListener('input', apply);
      apply();
    }
    const acct = root.querySelector('[data-conn-add]');
    if (acct) _bindAccountForm(acct, ctx);
    const other = root.querySelector('[data-svc-add]');
    if (other) _bindOtherForm(other, ctx);
    const eng = root.querySelector('[data-add-engine]');
    if (eng) {
      const e = (ctx.engines || []).find((x) => x.id === eng.dataset.addEngine);
      if (e) window.DeskV1Engines.bindConnections(root, [e], ctx.onEnginesChanged);
    }
    const first = root.querySelector('form input[type="text"]');
    if (first && _pick && !root.contains(document.activeElement)) first.focus({ preventScroll: true });
  }

  window.DeskV1AddService = { panelHTML, bind, reset, pickedEngine };
})();

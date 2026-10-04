// Desk v1 — Connections: connect by address (docs/DESK_CONNECT_BY_URL_SPEC.md, slice 1).
// The Add service panel starts here: paste a service address, see what Clayrune can
// really do with it, fill in the details (and a credential, in the same flow), review,
// and press ONE Save. Window-bridged module, no `import` (ground rule 1). The server
// side is mc/desk_connect/ + mc/blueprints/desk_connect_routes.py.
//
//   1. Address   POST /api/desk/connect/inspect: the address is checked and its host is
//                shown normalised. Editing it throws away what was detected.
//   2. Method    the rows the registry (mc/desk_connect/registry.json) lists for the
//                host. Only "Save for agents" can be chosen in this version; the rest
//                say what they are ("Available" opens the guide Connections already has,
//                "Restricted" and "Information only" can be read and nothing else).
//   3. Details   the service's name and, optionally, a credential typed here with the
//                shared vault form (static/js/secret-form.js).
//   4. Review    exactly what Save will write; Save asks the dashboard passcode once
//                and posts POST /api/desk/connect/commit. Nothing is written before it.
//
// Where a password lives: ONLY in the open form's own <input>. This module keeps the
// form's DOM node (so Back and a repaint do not lose what was typed) but never copies
// the value into a variable, storage, a draft or a log. It is read from the input once,
// into the Save request, and emptied the moment Save is accepted or the flow is left.
// A wrong passcode or a refusal keeps everything, so the user can retry.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const STEPS = [['url', 'Address'], ['method', 'Method'], ['details', 'Details'], ['review', 'Review']];
  const SUPPORT_WORD = { available: 'Available', restricted: 'Restricted', info_only: 'Information only' };
  const FORM = 'cf';                       // the shared form's id prefix

  function _fresh() {
    return { step: 'url', urlText: '', busy: false, error: '', hint: '', info: null, method: null,
      name: '', nameEdited: false, useCred: false, requestId: '', status: '', statusKind: 'error', saving: false };
  }
  let S = _fresh();
  let _formHost = null;                    // the credential form's DOM node: the one place a value lives
  const _formState = { type: 'api_key', preset: null, isNew: true, noTwoFa: true };

  function active() { return S.step !== 'url'; }

  // Leave the flow: forget the draft and empty the credential form.
  function reset() {
    if (_formHost) { _formHost.querySelectorAll('input').forEach((i) => { if (i.type !== 'radio' && i.type !== 'checkbox') i.value = ''; }); _formHost = null; }
    _formState.type = 'api_key';
    S = _fresh();
  }

  function _defaultName(info) {
    if (info.service) return info.service.label;
    return String(info.host || '').replace(/^www\./, '');
  }
  function _suggestCredName(info) {
    const base = window.SecretForm.slug((info.service ? info.service.label : String(info.host || '').replace(/^www\./, '').split('.')[0]) || 'service');
    return `${base || 'service'}.${_formState.type === 'login' ? 'login' : _formState.type === 'token' ? 'token' : 'api-key'}`;
  }

  function _stepsHTML() {
    const at = STEPS.findIndex((s) => s[0] === S.step);
    const items = STEPS.map((s, i) => `<li data-cf-step-item="${s[0]}"${i === at ? ' aria-current="step"' : ''}${i < at ? ' data-done="true"' : ''}><span class="desk-v1-cf-stepnum">${i + 1}</span><span class="desk-v1-cf-steplabel">${esc(s[1])}</span></li>`);
    return `<ol class="desk-v1-cf-steps" data-cf-steps aria-label="Steps">${items.join('')}</ol>
        <div class="desk-v1-cf-stepof" data-cf-stepof>Step ${at + 1} of ${STEPS.length}: ${esc(STEPS[at][1])}</div>`;
  }

  function _msg(kind, text) {
    return `<div class="desk-v1-cf-msg" data-cf-msg="${kind}" ${kind === 'error' ? 'role="alert"' : 'role="status"'}>${esc(text)}</div>`;
  }

  function _actions(back, primary) {
    return `<div class="desk-v1-cf-actions" data-cf-actions>
          ${back ? '<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cf-back" data-cf-back>‹ Back</button>' : ''}
          ${primary}
        </div>`;
  }

  // ── 1. Address ──────────────────────────────────────────────────────────
  function _urlHTML() {
    const err = S.error ? `<div class="desk-v1-cf-msg" data-cf-msg="error" role="alert">${esc(S.error)}${S.hint ? ` <span class="desk-v1-cf-hint">${esc(S.hint)}</span>` : ''}</div>` : '';
    return `
        <form class="desk-v1-cf-form" data-cf-url-form autocomplete="off" novalidate>
          <label class="desk-v1-conn-add-field">Service address
            <input type="text" inputmode="url" class="desk-v1-rules-textinput" data-cf-url data-cf-focus maxlength="400"
              placeholder="https://plausible.io" value="${esc(S.urlText)}" autocapitalize="off" spellcheck="false"></label>
          <div class="desk-v1-rules-hint">Paste the web address of the service or of your account on it. Clayrune shows how it can be connected; it does not open the page.</div>
          ${err}
          <div class="desk-v1-cf-actions desk-v1-cf-actions-flat">
            <button type="submit" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cf-primary" data-cf-continue ${S.busy ? 'disabled' : ''}>${S.busy ? 'Checking…' : 'Continue'}</button>
          </div>
        </form>`;
  }

  // ── 2. Method ───────────────────────────────────────────────────────────
  function _optionHTML(o, ctx) {
    const word = SUPPORT_WORD[o.support] || o.support;
    const head = `<span class="desk-v1-cf-option-head"><span class="desk-v1-cf-option-title">${esc(o.title)}</span>
            <span class="desk-v1-cf-badge" data-support="${esc(o.support)}">${esc(word)}</span></span>
          <span class="desk-v1-cf-option-evidence">${esc(o.evidence)}</span>
          <span class="desk-v1-cf-option-guidance">${esc(o.guidance)}</span>`;
    if (o.selectable) {
      return `
        <label class="desk-v1-cf-option desk-v1-cf-option-pick" data-cf-option="${esc(o.method)}" data-support="${esc(o.support)}">
          <input type="radio" name="cf-method" value="${esc(o.method)}" data-cf-method ${S.method === o.method ? 'checked' : ''}>
          <span class="desk-v1-cf-option-main">${head}</span>
        </label>`;
    }
    let open = '';
    if (o.open) {
      const engines = ctx.engines || [];
      const id = o.open.replace(/^engine:/, '');
      const eng = o.open.indexOf('engine:') === 0 ? engines.find((e) => e.id === id) : null;
      if (o.open.indexOf('account:') === 0) {
        open = `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cf-open="${esc(o.open)}">Open the guide</button>`;
      } else if (eng) {
        const connected = !!window.DeskV1Engines.tileState(eng);
        open = `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cf-open="${esc(o.open)}" data-cf-open-connected="${connected}">${connected ? 'Already connected: open it' : 'Open the guide'}</button>`;
      }
    }
    return `
        <div class="desk-v1-cf-option desk-v1-cf-option-info" data-cf-option="${esc(o.method)}" data-support="${esc(o.support)}">
          <span class="desk-v1-cf-option-main">${head}${open}</span>
        </div>`;
  }

  function _methodHTML(ctx) {
    const info = S.info;
    const known = info.service
      ? `<div class="desk-v1-cf-known" data-cf-known="${esc(info.service.id)}">Recognised: <strong>${esc(info.service.label)}</strong></div>`
      : '<div class="desk-v1-cf-known" data-cf-known="">Not recognised yet. Clayrune has no integration for this host, so the only thing it can do is save it for agents.</div>';
    return `
        <div class="desk-v1-cf-host" data-cf-host>${esc(info.host)}${info.path && info.path !== '/' ? `<span class="desk-v1-cf-path">${esc(info.path)}</span>` : ''}</div>
        ${known}
        <div class="desk-v1-cf-options" data-cf-options role="radiogroup" aria-label="Connection method">${info.options.map((o) => _optionHTML(o, ctx)).join('')}</div>
        <div class="desk-v1-rules-hint">Recognising an address is not support: a method marked “Information only” or “Restricted” cannot be set up from here.</div>
        ${_actions(true, `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cf-primary" data-cf-next ${S.method ? '' : 'disabled'}>Continue</button>`)}`;
  }

  // ── 3. Details ──────────────────────────────────────────────────────────
  function _detailsHTML() {
    return `
        <form class="desk-v1-cf-form" data-cf-details-form autocomplete="off" novalidate>
          <label class="desk-v1-conn-add-field">Name
            <input type="text" class="desk-v1-rules-textinput" data-cf-name data-cf-focus maxlength="80" value="${esc(S.name)}" placeholder="e.g. Plausible analytics"></label>
          <label class="desk-v1-cf-check"><input type="checkbox" data-cf-usecred ${S.useCred ? 'checked' : ''}>
            <span>Store a credential for it now<span class="desk-v1-rules-hint">Optional. It goes into Secrets when you press Save on the next step, and nowhere before.</span></span></label>
          <div class="desk-v1-cf-cred" data-cf-cred-fields ${S.useCred ? '' : 'hidden'}><div data-cf-form-slot></div></div>
          ${S.error ? _msg('error', S.error) : ''}
          ${_actions(true, '<button type="submit" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cf-primary" data-cf-next>Review</button>')}
        </form>`;
  }

  // ── 4. Review ───────────────────────────────────────────────────────────
  // The credential facts, minus the value. Built from the mounted form, so bind()
  // fills it in after the form's node is back in the page.
  function _credSummaryHTML() {
    if (!S.useCred || !_formHost) return '<div class="desk-v1-rules-hint" data-cf-r-nocred>No credential: agents see the service, with nothing to sign in with.</div>';
    const m = window.SecretForm.meta(FORM, _formState);
    const T = window.SecretForm.TYPES[m.entry_type];
    return `<dl class="desk-v1-cf-facts">
            <div><dt>Credential</dt><dd data-cf-r-cred><code>${esc(m.name)}</code></dd></div>
            <div><dt>Type</dt><dd data-cf-r-credtype>${esc(T ? T.label : m.entry_type)}</dd></div>
            ${m.username ? `<div><dt>${esc(T && T.user ? T.user.label : 'Username')}</dt><dd>${esc(m.username)}</dd></div>` : ''}
            <div><dt>Value</dt><dd data-cf-r-credvalue>${m.hasValue ? 'entered, hidden: shown nowhere' : 'none'}</dd></div>
            <div><dt>Unattended runs</dt><dd>${m.allow_unattended ? 'may use it' : 'may not use it'}</dd></div>
            ${m.description ? `<div><dt>For</dt><dd>${esc(m.description)}</dd></div>` : ''}
          </dl>`;
  }

  function _reviewHTML() {
    return `
        <dl class="desk-v1-cf-facts" data-cf-review>
          <div><dt>Service</dt><dd data-cf-r-name>${esc(S.name)}</dd></div>
          <div><dt>Address</dt><dd data-cf-r-url>${esc(S.info.url)}</dd></div>
          <div><dt>Method</dt><dd data-cf-r-method>Save for agents</dd></div>
        </dl>
        <div data-cf-r-credbox></div>
        <div class="desk-v1-rules-hint" data-cf-r-honest>Save keeps the service where agents can see it. Clayrune does not connect to it or post to it. Saving asks for your dashboard passcode once; nothing has been written yet.</div>
        <div data-cf-form-slot hidden></div>
        ${S.status ? `<div class="desk-v1-cf-msg" data-cf-msg="${S.statusKind || 'error'}" role="${S.statusKind === 'ok' ? 'status' : 'alert'}">${esc(S.status)}</div>` : ''}
        ${_actions(true, `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cf-primary" data-cf-save ${S.saving ? 'disabled' : ''}>${S.saving ? 'Saving…' : 'Save'}</button>`)}`;
  }

  // The panel body (steps header + the step). '' when the live Desk is off: the
  // flow talks to the server, and the preview has none.
  function html(ctx) {
    if (!ctx.live) return '';
    let body;
    if (S.step === 'url') body = _urlHTML();
    else if (S.step === 'method') body = _methodHTML(ctx);
    else if (S.step === 'details') body = _detailsHTML();
    else body = _reviewHTML();
    const head = S.step === 'url' ? '<div class="desk-v1-rules-group-title">Connect by address</div>' : '';
    return `<section class="desk-v1-cf" data-cf data-cf-step="${S.step}" aria-label="Connect by address">${head}${_stepsHTML()}${body}</section>`;
  }

  // ── wiring ──────────────────────────────────────────────────────────────
  function _go(step, ctx) { S.step = step; S.error = ''; S.status = ''; ctx.repaint(); }

  async function _inspect(ctx) {
    const text = S.urlText.trim();
    if (!text) { S.error = 'Paste the address of the service.'; S.hint = ''; ctx.repaint(); return; }
    // A new address replaces what was detected for the old one.
    const same = S.info && S.info.input === text;
    S.busy = true; S.error = ''; S.hint = ''; ctx.repaint();
    try {
      const info = await ctx.api('POST', '/api/desk/connect/inspect', { url: text });
      info.input = text;
      if (!same) { S.method = null; S.name = ''; S.nameEdited = false; }
      S.info = info;
      if (!S.nameEdited && !S.name) S.name = _defaultName(info);
      S.busy = false; S.step = 'method'; ctx.repaint();
    } catch (e) {
      S.busy = false; S.info = null; S.method = null;
      S.error = e && e.message ? e.message : 'could not check that address';
      S.hint = (e && e.hint) || '';
      ctx.repaint();
    }
  }

  // Build the credential form once, in the slot it is first needed in. The node
  // survives every repaint after that: Back, the engines list loading, a toast.
  function _mountForm(root) {
    const slot = root.querySelector('[data-cf-form-slot]');
    if (!slot) return;
    const SF = window.SecretForm;
    if (_formHost) { slot.appendChild(_formHost); SF.render(FORM, _formState); return; }
    _formHost = document.createElement('div');
    _formHost.dataset.cfFormHost = '1';
    _formHost.innerHTML = SF.fieldsHtml({ p: FORM, isNew: true, name: '', projects: null, numbered: false, intro: false });
    slot.appendChild(_formHost);
    SF.bind(FORM, _formState, { typeChanged: () => _suggestName(false) });
    SF.render(FORM, _formState);
    _suggestName(true);
  }

  // Fill the credential name from the service when the human has not typed one.
  function _suggestName(force) {
    const el = document.getElementById(`${FORM}-name`);
    if (!el || !S.info) return;
    if (force || !el.dataset.touched) el.value = _suggestCredName(S.info);
    if (!el.dataset.listen) { el.dataset.listen = '1'; el.addEventListener('input', () => { el.dataset.touched = '1'; }); }
  }

  function _readName(root) {
    const el = root.querySelector('[data-cf-name]');
    if (el) { S.name = el.value; S.nameEdited = true; }
  }

  function _draft() {
    const draft = { url: S.info.url, method: S.method, name: S.name.trim() };
    if (S.useCred) {
      const c = window.SecretForm.read(FORM, _formState);
      if (c.error) return { error: c.error };
      draft.credential = { name: c.name, entry_type: c.entry_type, username: c.username, value: c.value,
        description: c.description, allow_unattended: c.allow_unattended };
    }
    return { draft };
  }

  async function _save(root, ctx) {
    if (S.saving) return;
    const built = _draft();
    if (built.error) { S.step = 'details'; S.error = built.error; ctx.repaint(); return; }
    S.saving = true; S.status = ''; ctx.repaint();
    let result;
    try {
      result = await window.humanProofFetch('/api/desk/connect/commit', {
        method: 'POST', body: JSON.stringify({ request_id: S.requestId, draft: built.draft }),
      }, { title: 'Save service', description: `Re-enter your dashboard passcode to save “${built.draft.name}”${built.draft.credential ? ' and store its credential' : ''}.` });
    } catch (e) {
      result = { ok: false, status: 0, body: { error: e && e.message ? e.message : 'could not reach the server' } };
    }
    built.draft = null;
    S.saving = false;
    if (result === null) { ctx.repaint(); return; }      // cancelled at the passcode: nothing was sent, the draft stays
    if (!result.ok) {
      S.statusKind = 'error';
      S.status = (result.body && (result.body.error || result.body.message)) || `The save failed (HTTP ${result.status}).`;
      ctx.repaint();
      return;
    }
    const saved = result.body || {};
    const svc = saved.service || {};
    const stored = !!(saved.credential && saved.credential.stored);
    reset();
    ctx.onSaved(svc, { credentialStored: stored });
  }

  // ctx: { live, api, engines, repaint, openPick(key), onSaved(service, {credentialStored}) }
  function bind(el, ctx) {
    const root = el.querySelector('[data-cf]');
    if (!root) return;
    const urlForm = root.querySelector('[data-cf-url-form]');
    if (urlForm) {
      const input = urlForm.querySelector('[data-cf-url]');
      input.addEventListener('input', () => {
        S.urlText = input.value;
        if (S.info && S.info.input !== input.value.trim()) { S.info = null; S.method = null; }
      });
      urlForm.addEventListener('submit', (e) => { e.preventDefault(); if (!S.busy) { S.urlText = input.value; _inspect(ctx); } });
    }
    const back = root.querySelector('[data-cf-back]');
    if (back) back.addEventListener('click', () => {
      _readName(root);
      const prev = { method: 'url', details: 'method', review: 'details' }[S.step];
      _go(prev, ctx);
    });
    root.querySelectorAll('[data-cf-method]').forEach((r) => r.addEventListener('change', () => {
      S.method = r.value;
      const next = root.querySelector('[data-cf-next]');
      if (next) next.disabled = false;
    }));
    root.querySelectorAll('[data-cf-open]').forEach((b) => b.addEventListener('click', () => ctx.openPick(b.dataset.cfOpen)));
    if (S.step === 'method') {
      const next = root.querySelector('[data-cf-next]');
      if (next) next.addEventListener('click', () => { if (S.method) _go('details', ctx); });
    }
    const dform = root.querySelector('[data-cf-details-form]');
    if (dform) {
      const cred = dform.querySelector('[data-cf-usecred]');
      const fields = dform.querySelector('[data-cf-cred-fields]');
      _mountForm(root);
      cred.addEventListener('change', () => {
        S.useCred = cred.checked;
        fields.hidden = !cred.checked;
        if (!cred.checked) window.SecretForm.clear(FORM);       // no credential saved = no value left typed
        else { const v = document.getElementById(`${FORM}-value`); if (v) v.focus(); }
      });
      dform.addEventListener('submit', (e) => {
        e.preventDefault();
        _readName(root);
        if (!S.name.trim()) { S.error = 'Give the service a name.'; ctx.repaint(); return; }
        if (S.useCred) {
          const c = window.SecretForm.read(FORM, _formState);
          if (c.error) { S.error = c.error; ctx.repaint(); return; }
        }
        S.error = ''; S.requestId = _newRequestId(); S.step = 'review'; ctx.repaint();
      });
    }
    if (S.step === 'review') {
      if (S.useCred) _mountForm(root);
      const box = root.querySelector('[data-cf-r-credbox]');
      if (box) box.innerHTML = _credSummaryHTML();
      const save = root.querySelector('[data-cf-save]');
      if (save) save.addEventListener('click', () => _save(root, ctx));
    }
    // Focus stays where the human put it; a fresh step puts it on its first field.
    const focus = root.querySelector('[data-cf-focus]');
    if (focus && !root.contains(document.activeElement)) focus.focus({ preventScroll: true });
  }

  function _newRequestId() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    return 'cf-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  }

  window.DeskV1ConnectFlow = { active, reset, html, bind };
})();

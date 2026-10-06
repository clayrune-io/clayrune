// Desk v1 — Connections: the Connect wizard's first two screens, Service and Connection
// (MC-1062 ticket 02; frame and rules in desk-v1-connect-wizard.js). Window-bridged module, no
// `import` (ground rule 1). They register through `registerScreen`; the frame owns nothing here.
//
//   Service      one box for a name or an address, at most four name suggestions. Continue asks
//                the read-only POST /api/desk/connect/types (ticket 01) and nothing else; it
//                writes nothing. An address already answered is not asked again.
//   Connection   the types that have at least one variant Clayrune can set up, as radio rows
//                (Sign in, API, MCP; four at most, from the server's `picker`). Everything the
//                server hid under `details` (Dave's Q1 = A: unavailable methods are not
//                disabled rows), the manual delivery routes and the reference fallback live
//                under the one Details disclosure. Choosing a row records a selection and
//                grants nothing.
(function () {
  const W = window.DeskV1ConnectWizard;
  if (!W) return;
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const MAX_SUGGEST = 4;
  const COST_BEARING = ['published', 'account_specific'];
  const REFERENCE_TYPE = 'reference';                    // the "Save a reference" branch: ticket 12 supplies its Setup screen

  // ── Service ─────────────────────────────────────────────────────────────
  function _freshService() { return { text: '', busy: false, seq: 0, didYouMean: [] }; }
  let T = _freshService();

  // The suggest module paints whatever the server lists (up to six); this view shows four.
  function _cappedApi(api) {
    return (method, url, body) => api.ctx.api(method, url, body).then((r) => (r && r.suggestions ? Object.assign({}, r, { suggestions: r.suggestions.slice(0, MAX_SUGGEST) }) : r));
  }

  W.registerScreen({
    id: 'service', step: 'service',
    title: () => 'Add service',
    copy: () => 'Enter a service name or web address.',
    body: () => {
      const Sg = window.DeskV1ConnectSuggest;
      const didYou = T.didYouMean.length
        ? `<div class="desk-v1-cfw-didyou" data-cfw-didyou>Did you mean ${T.didYouMean.slice(0, MAX_SUGGEST).map((d) => `<button type="button" class="desk-v1-cf-link" data-cfw-pick-name="${esc(d.label)}">${esc(d.label)}</button>`).join(', ')}?</div>` : '';
      return `<label class="desk-v1-conn-add-field">Service name or web address
          <input type="text" inputmode="text" class="desk-v1-rules-textinput" data-cfw-input data-cfw-focus maxlength="400"
            placeholder="Higgsfield, or https://plausible.io" value="${esc(T.text)}" autocapitalize="off" spellcheck="false"></label>
        ${Sg ? Sg.listHTML() : ''}${didYou}`;
    },
    primary: (api) => ({
      label: T.busy ? 'Checking…' : 'Continue', disabled: T.busy,
      run: async () => {
        const text = T.text.trim();
        if (!text) { api.error('Type the name of the service or paste its web address.'); return false; }
        if (api.info && api.info.input === text) return true;           // already answered for this text: nothing to ask
        const n = ++T.seq;
        T.busy = true; T.didYouMean = [];
        api.repaint();
        try {
          const info = await api.ctx.api('POST', '/api/desk/connect/types', { input: text });
          if (n !== T.seq) return false;                                 // the box changed while this was in flight
          info.input = text;
          T.busy = false;
          api.setInfo(info);
          return true;
        } catch (e) {
          if (n !== T.seq) return false;
          T.busy = false;
          T.didYouMean = (e && e.body && e.body.suggestions) || [];
          api.error(e && e.message ? e.message : 'could not check that', (e && e.body && e.body.hint) || '');
          return false;
        }
      },
    }),
    bind: (root, api) => {
      const input = root.querySelector('[data-cfw-input]');
      const form = root.querySelector('[data-cfw-form]');
      input.addEventListener('input', () => {
        T.text = input.value; T.seq++; T.busy = false; T.didYouMean = [];
        if (api.info && api.info.input !== T.text.trim()) api.setInfo(null);
        const primary = root.querySelector('[data-cfw-primary]');
        if (primary) { primary.disabled = false; primary.textContent = 'Continue'; }
      });
      const choose = (label) => { T.text = label; input.value = label; form.requestSubmit(); };
      if (window.DeskV1ConnectSuggest) window.DeskV1ConnectSuggest.bind(root, { input, api: _cappedApi(api), onPick: (item) => choose(item.label) });
      root.querySelectorAll('[data-cfw-pick-name]').forEach((b) => b.addEventListener('click', () => choose(b.dataset.cfwPickName)));
    },
    discard: (why) => { if (why === 'close') { if (window.DeskV1ConnectSuggest) window.DeskV1ConnectSuggest.reset(); T = _freshService(); } },
  });

  // ── Connection ──────────────────────────────────────────────────────────
  function _label(info) { return info && info.service && info.service.label ? info.service.label : (info && info.host) || 'service'; }
  function _costTag(t) {
    return t.id === 'api' && t.variants.some((v) => COST_BEARING.indexOf(v.cost && v.cost.basis) >= 0)
      ? '<span class="desk-v1-cfw-tag" data-cfw-cost>May cost money</span>' : '';
  }

  // Details: what the server hid from the picker, why, the manual routes, and the reference fallback.
  function _detailsHTML(api) {
    const info = api.info || {};
    const d = info.details || { unavailable: [], delivery: [] };
    const items = d.unavailable.map((v) => ({ head: `${(v.explanation && v.explanation.code) === 'manual' ? 'Manual' : (v.type === 'signin' ? 'Sign in' : v.type === 'api' ? 'API' : 'MCP server')}: ${v.title}`, text: v.explanation ? v.explanation.text : '' }))
      .concat(d.delivery.map((v) => ({ head: `Manual delivery: ${v.title}`, text: v.text })));
    const list = items.length
      ? `<div class="desk-v1-cfw-dgroup" data-cfw-unavailable><div class="desk-v1-cfw-dtitle">Not available here</div>${api.paged('unavailable', items,
        (it) => `<div class="desk-v1-cfw-fact"><div class="desk-v1-cfw-fact-head">${esc(it.head)}</div><div class="desk-v1-cfw-fact-text">${esc(it.text)}</div></div>`)}</div>` : '';
    const ref = info.reference
      ? `<div class="desk-v1-cfw-dgroup" data-cfw-reference-group><div class="desk-v1-cfw-dtitle">${esc(info.reference.title)}</div><div class="desk-v1-cfw-fact-text">${esc(info.reference.text)}</div>
          <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cfw-reference>Save a reference instead</button></div>` : '';
    return list + ref + (window.DeskV1ConnectUnknownStep ? window.DeskV1ConnectUnknownStep.details(info) : '');
  }

  W.registerScreen({
    id: 'connection', step: 'connection',
    match: (_sel, info) => !window.DeskV1ConnectUnknownStep || !window.DeskV1ConnectUnknownStep.unknown(info),
    title: (api) => `Connect ${_label(api.info)}`,
    copy: () => 'Choose a connection type.',
    body: (api) => {
      const picker = (api.info && api.info.picker) || [];
      if (!picker.length) return '<p class="desk-v1-cfw-empty" data-cfw-empty>Clayrune cannot set up a connection here. Details lists why.</p>';
      return `<div class="desk-v1-cfw-options" data-cfw-options role="radiogroup" aria-label="Connection type">${picker.map((t) => `
          <label class="desk-v1-cfw-option" data-cfw-option="${esc(t.id)}">
            <input type="radio" name="cfw-type" value="${esc(t.id)}" data-cfw-type ${api.sel.type === t.id ? 'checked' : ''}>
            <span class="desk-v1-cfw-option-label">${esc(t.label)}</span>${_costTag(t)}
          </label>`).join('')}</div>`;
    },
    details: _detailsHTML,
    primary: (api) => {
      const picker = (api.info && api.info.picker) || [];
      return { label: 'Continue', disabled: !picker.some((t) => t.id === api.sel.type) };
    },
    bind: (root, api) => {
      const picker = (api.info && api.info.picker) || [];
      if (window.DeskV1ConnectUnknownStep) window.DeskV1ConnectUnknownStep.bindDetails(root, api);
      const primary = root.querySelector('[data-cfw-primary]');
      root.querySelectorAll('[data-cfw-type]').forEach((r) => r.addEventListener('change', () => {
        const t = picker.find((x) => x.id === r.value);
        if (!t) return;
        api.select({ type: t.id, variant: t.variants.length === 1 ? t.variants[0].id : null });     // a type with one way to set it up needs no second choice
        if (primary) primary.disabled = false;
      }));
      const ref = root.querySelector('[data-cfw-reference]');
      if (ref) ref.addEventListener('click', () => { api.select({ type: REFERENCE_TYPE, variant: REFERENCE_TYPE }); api.go('setup'); });
    },
  });
})();

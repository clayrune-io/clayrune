// Desk v1 — Connections: "Look it up" for a service Clayrune does not know
// (docs/DESK_CONNECT_BY_URL_SPEC.md, slice 3). On the Method step, an address the registry
// does not list offers ONE explicit button; pressing it asks the server to read the public
// page in a temporary signed-out browser and to ask the official MCP registry, then sort what
// it found (POST /api/desk/connect/discover, up to 60 s, cancellable). The answer is
// information: every row is "Information only", none can be chosen, and "Save for agents"
// stays the only way to go on. Nothing is looked up until the button is pressed.
//
// What it shows is untrusted text from third parties, so it is only ever escaped text:
// an address in the answer is printed, never made a link, never fetched or opened.
// Window-bridged module, no `import` (ground rule 1).
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // One lookup's state, for one address: Back and forward keep the answer, a new address drops it.
  function _fresh() { return { url: '', busy: false, requestId: '', answer: null, error: '' }; }
  let D = _fresh();

  let _api = null;                      // the flow's api(), kept so a run can be cancelled when it is abandoned

  // Forget the lookup; a run still going is told to stop (it would otherwise hold the one slot).
  function reset() {
    if (D.busy && _api) { try { Promise.resolve(_api('POST', '/api/desk/connect/discover/cancel', { request_id: D.requestId })).catch((e) => console.warn('[connect-discover] cancellation failed: ' + e)); } catch (e) { console.warn('[connect-discover] cancellation failed: ' + e); } }
    D = _fresh();
  }
  function _for(info) { if (D.url !== info.url) { reset(); D.url = info.url; } return D; }

  function _newRequestId() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    return 'dd-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  }

  // Only an address with no integration and no name is offered a lookup.
  function offered(info) { return !!info && !info.service && info.input_kind === 'url'; }

  function _rowHTML(o) {
    return `
        <div class="desk-v1-cf-option desk-v1-cf-option-info desk-v1-cfd-row" data-cfd-row="${esc(o.method)}" data-cfd-evidence="${esc(o.evidence_id)}" data-support="info_only">
          <span class="desk-v1-cf-option-main">
            <span class="desk-v1-cf-option-head"><span class="desk-v1-cf-option-title">${esc(o.title)}</span><span class="desk-v1-cf-badge" data-support="info_only">Information only</span></span>
            <span class="desk-v1-cf-option-evidence">${esc(o.evidence)}${o.evidence_url ? ` <span class="desk-v1-cfd-url" data-cfd-url>${esc(o.evidence_url)}</span>` : ''}</span>
            <span class="desk-v1-cf-option-guidance">${esc(o.usage || o.guidance)}</span>
            <span class="desk-v1-rules-hint">${esc(o.guidance)}</span>
          </span>
        </div>`;
  }

  function _answerHTML(a) {
    const rows = a.options || [];
    const problems = (a.problems || []).map((p) => `<div class="desk-v1-cf-msg" data-cf-msg="error" data-cfd-problem="${esc(p.code)}" role="alert">${esc(p.message)}</div>`).join('');
    const notes = (a.notes || []).map((n) => `<div class="desk-v1-rules-hint" data-cfd-note>${esc(n)}</div>`).join('');
    let head;
    if (a.outcome === 'found' && !a.incomplete) head = 'What Clayrune found';
    else if (rows.length) head = 'Discovery incomplete: what Clayrune could confirm';
    else if (a.outcome === 'signin_wall') head = 'Nothing readable: this is a sign-in page';
    else if (a.outcome === 'incomplete') head = 'Discovery incomplete: nothing could be confirmed';
    else head = 'Nothing found';
    const empty = rows.length ? '' : '<div class="desk-v1-rules-hint" data-cfd-empty>Clayrune found no way to connect this service. You can still save it for agents below.</div>';
    return `
        <div class="desk-v1-cfd-answer" data-cfd-answer="${esc(a.outcome)}" data-cfd-incomplete="${a.incomplete ? 'true' : 'false'}">
          <div class="desk-v1-cfd-head" data-cfd-head>${esc(head)}</div>
          ${problems}
          ${rows.length ? `<div class="desk-v1-cf-options" data-cfd-rows>${rows.map(_rowHTML).join('')}</div>` : empty}
          ${notes}
          <div class="desk-v1-rules-hint" data-cfd-warning>${esc(a.warning || '')}</div>
        </div>`;
  }

  // The lookup area under the "Not recognised yet" line. '' for anything but an unknown address.
  function html(info) {
    const d = info ? _for(info) : D;        // a different address drops (and cancels) the old lookup, offered or not
    if (!offered(info)) return '';
    if (d.busy) {
      return `<div class="desk-v1-cfd" data-cfd="busy">
          <div class="desk-v1-cf-msg" data-cfd-progress role="status">Looking this service up: reading its page and asking the MCP registry. This takes up to a minute.</div>
          <div class="desk-v1-cf-actions desk-v1-cf-actions-flat"><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cfd-cancel>Cancel</button></div>
        </div>`;
    }
    const err = d.error ? `<div class="desk-v1-cf-msg" data-cf-msg="error" data-cfd-error role="alert">${esc(d.error)}</div>` : '';
    if (d.answer) return `<div class="desk-v1-cfd" data-cfd="done">${err}${_answerHTML(d.answer)}</div>`;
    return `<div class="desk-v1-cfd" data-cfd="idle">
          <div class="desk-v1-rules-hint" data-cfd-explain>Clayrune can look this service up: it opens the address in a temporary browser that is not signed in to anything, asks the public MCP registry about it, and summarises what it finds. It saves nothing and uses no credential.</div>
          ${err}
          <div class="desk-v1-cf-actions desk-v1-cf-actions-flat"><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cfd-lookup>Look it up</button></div>
        </div>`;
  }

  async function _run(info, ctx) {
    _api = ctx.api;
    const d = _for(info);
    if (d.busy) return;
    d.busy = true; d.error = ''; d.answer = null; d.requestId = _newRequestId();
    const mine = d.requestId;
    ctx.repaint();
    let answer = null, error = '';
    try {
      answer = await ctx.api('POST', '/api/desk/connect/discover', { input: info.url, request_id: mine });
    } catch (e) {
      error = (e && e.message) || 'The lookup failed.';
      if (e && e.body && e.body.hint) error += ' ' + e.body.hint;
    }
    if (D.requestId !== mine) return;                       // the flow moved on (a new address, Cancel, leaving)
    D.busy = false;
    if (answer && answer.cancelled) { D.error = 'The lookup was cancelled.'; }
    else if (answer && answer.ok) { D.answer = answer; }
    else { D.error = error || (answer && answer.error) || 'The lookup failed.'; }
    ctx.repaint();
  }

  async function _cancel(info, ctx) {
    const d = _for(info);
    if (!d.busy) return;
    const id = d.requestId;
    D.busy = false; D.requestId = ''; D.answer = null; D.error = 'The lookup was cancelled.'; ctx.repaint();
    try { await ctx.api('POST', '/api/desk/connect/discover/cancel', { request_id: id }); }
    catch (e) { console.warn('[connect-discover] cancellation failed: ' + e); }
  }

  // ctx: { api(method, url, body), repaint() }
  function bind(root, info, ctx) {
    if (!offered(info)) return;
    _api = ctx.api;
    const go = root.querySelector('[data-cfd-lookup]');
    if (go) go.addEventListener('click', () => _run(info, ctx));
    const stop = root.querySelector('[data-cfd-cancel]');
    if (stop) stop.addEventListener('click', () => _cancel(info, ctx));
  }

  // The rows a finished lookup adds under the registry's own, for the flow to count.
  function rows() { return D.answer ? D.answer.options || [] : []; }

  window.DeskV1ConnectDiscover = { html, bind, reset, offered, rows, run: _run, cancel: _cancel,
    state: () => ({ busy: D.busy, answer: D.answer, error: D.error }) };
})();

// ── Settings > Connectivity > Agent mail (MC-1166 unit 7e-1) ───────────────────
// READ-ONLY status and recovery card. It performs exactly one request, a GET of
// /api/agent-mail/enrollment, and draws what static/js/agent-mail-status-model.js
// makes of the answer. There is no start / verify / enable / disable control, no
// form field and no POST in THIS file, not even a disabled one. "Refresh" is the
// same GET again. The mutating controls (unit 7e-2) live in their own host under
// this card (agent-mail-actions.js); the only link between them is the
// `agent-mail-status` event dispatched below after each paint, carrying the raw
// { status, body } of the GET so the controls can follow the same answer.
//
// Every value is escaped, and the model only passes booleans, integers, a masked
// owner hint and closed reason codes, so no mail or reply text can reach this
// file. Registered into Settings by two hooks in settings-drill.js
// (window.agentMailStatusSettingsHTML, window.refreshAgentMailStatus).
// Window-bridged, no `import`.
(function () {
  const URL_PATH = '/api/agent-mail/enrollment';
  let busy = false;

  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const base = () => (typeof API_BASE === 'string' ? API_BASE : '');

  function settingsHTML() {
    return `
    <div class="settings-section" id="agent-mail-status-section">
      <div class="settings-section-title">Agent mail</div>
      <div class="settings-hint am-hint">Status of the optional agent-mail inbox: whether it is on, who owns it,
        what is held or uncertain, and what the background recovery is doing.</div>
      <div id="agent-mail-status-host" class="am-host"><div class="settings-hint">Loading…</div></div>
    </div>`;
  }

  function paint(host, view) {
    const rows = view.rows.map((r) => `
      <div class="am-row" data-am-row="${esc(r.key)}" data-tone="${esc(r.tone)}">
        <div class="am-label">${esc(r.label)}</div>
        <div class="am-value">${esc(r.value)}</div>
      </div>`).join('');
    const notes = view.notes.map((n) => `<div class="am-note">${esc(n)}</div>`).join('');
    host.innerHTML = `
      <div class="am-card" id="agent-mail-status" data-am-state="${esc(view.state)}" data-tone="${esc(view.tone)}">
        <div class="am-head"><span class="am-dot" aria-hidden="true"></span>
          <span class="am-headline" data-am="headline">${esc(view.headline)}</span></div>
        <div class="am-detail" data-am="detail">${esc(view.detail)}</div>
        ${rows ? `<div class="am-rows">${rows}</div>` : ''}
        <div class="am-foot">${notes}
          <button type="button" class="am-refresh" data-am="refresh">Refresh</button></div>
      </div>`;
    const btn = host.querySelector('[data-am="refresh"]');
    if (btn) btn.addEventListener('click', () => refresh());
  }

  async function refresh() {
    const host = document.getElementById('agent-mail-status-host');
    if (!host || busy) return;
    busy = true;
    let result;
    try {
      const res = await fetch(base() + URL_PATH, { method: 'GET', cache: 'no-store' });
      const body = await res.json().catch(() => null);
      result = { status: res.status, body };
    } catch (_) {
      result = { status: 0, body: null };
    }
    busy = false;
    const live = document.getElementById('agent-mail-status-host');
    if (live) {
      paint(live, window.AgentMailStatusModel.describe(result));
      try { document.dispatchEvent(new CustomEvent('agent-mail-status', { detail: result })); } catch (_) { /* cosmetic */ }
    }
  }

  window.agentMailStatusSettingsHTML = settingsHTML;   // interop: settings template (_renderSettings)
  window.refreshAgentMailStatus = refresh;             // interop: settings hydration (_renderSettings)
})();

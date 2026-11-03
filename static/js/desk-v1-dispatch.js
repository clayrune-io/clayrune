// Campaign dispatch receipts/results. Uses the saved thread, never a model call.
(function () {
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  function card(t) {
    const d = t.dispatch;
    if (!d) return '';
    const result = t.role === 'dispatch_result';
    const href = `?project=${encodeURIComponent(d.project_id)}&session=${encodeURIComponent(d.session_id)}`;
    return `<div class="desk-v1-dispatch-card" data-dispatch-card="${esc(d.session_id)}" role="group" aria-label="Agent dispatch">
      <div>${esc(result ? t.agent : d.agent)} <span>${esc(result ? d.status : 'Dispatched')}</span></div>
      <div class="desk-v1-chat-text">${esc(t.text)}</div>
      <a href="${esc(href)}" target="_blank" rel="noopener">Open child session</a>
    </div>`;
  }
  function waiting(st) {
    const finished = new Set(st.turns.filter((t) => t.role === 'dispatch_result' && t.dispatch && t.dispatch.status !== 'waiting_for_answer').map((t) => t.dispatch.session_id));
    return st.pending || st.turns.some((t) => t.role === 'dispatch' && t.dispatch && !finished.has(t.dispatch.session_id));
  }
  async function refresh(campaignId, st) {
    const res = await window.DeskV1Store.api('GET', `/api/desk/campaigns/${encodeURIComponent(campaignId)}/chat`);
    if (!Array.isArray(res.thread)) return false;
    // Merge by durable id: an older GET cannot erase a just-returned POST.
    const known = new Set(st.turns.map((t) => t.id));
    const extra = res.thread.filter((t) => !known.has(t.id));
    if (extra.length) st.turns.push(...extra);
    return !!extra.length;
  }
  function watch(box, campaignId, st, paint) {
    let last = '';
    async function tick() {
      if (!box.isConnected) return;
      try {
        if (waiting(st)) {
          const changed = await refresh(campaignId, st);
          if (box.isConnected && changed) paint();
          last = '';
        }
      } catch (e) {
        const msg = `Child results could not be refreshed: ${e && e.message ? e.message : e}`;
        const status = box.querySelector('[data-camp-chat-status]');
        if (status && msg !== last) { status.textContent = msg; status.dataset.kind = 'error'; last = msg; }
      } finally {
        if (box.isConnected) setTimeout(tick, 3000);
      }
    }
    setTimeout(tick, 1000);
  }
  window.DeskV1Dispatch = { card, watch, refresh };
})();

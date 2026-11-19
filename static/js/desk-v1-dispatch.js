// Dispatch receipts/results in a Desk chat thread (campaign, storyboard, article).
// Uses the saved thread, never a model call. A "source" says where the thread is
// read from: a campaign id (string, the original form) or { url, status } where
// `url` is the chat's GET and `status` the selector of its status line.
(function () {
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const urlOf = (src) => (typeof src === 'string' ? `/api/desk/campaigns/${encodeURIComponent(src)}/chat` : src.url);
  const statusOf = (src) => (typeof src === 'string' || !src.status ? '[data-camp-chat-status]' : src.status);
  function card(t) {
    const d = t.dispatch;
    if (!d) return '';
    const result = t.role === 'dispatch_result';
    const href = `?project=${encodeURIComponent(d.project_id)}&session=${encodeURIComponent(d.session_id)}`;
    const engine = !result && ('model' in d || 'effort' in d)
      ? `<small class="desk-v1-dispatch-engine">${esc(d.model || 'Default model')} · ${esc(d.effort ? `${d.effort} effort` : 'default effort')}</small>` : '';
    return `<div class="desk-v1-dispatch-card" data-dispatch-card="${esc(d.session_id)}" role="group" aria-label="Agent dispatch">
      <div>${esc(result ? t.agent : d.agent)} <span>${esc(result ? d.status : 'Dispatched')}</span></div>
      ${engine}
      <div class="desk-v1-chat-text">${esc(t.text)}</div>
      <a href="${esc(href)}" target="_blank" rel="noopener">Open child session</a>
    </div>`;
  }
  function waiting(st) {
    const finished = new Set(st.turns.filter((t) => t.role === 'dispatch_result' && t.dispatch && t.dispatch.status !== 'waiting_for_answer').map((t) => t.dispatch.session_id));
    return st.pending || st.turns.some((t) => t.role === 'dispatch' && t.dispatch && !finished.has(t.dispatch.session_id));
  }
  async function refresh(source, st) {
    const res = await window.DeskV1Store.api('GET', urlOf(source));
    if (!Array.isArray(res.thread)) return false;
    // Merge by durable id: an older GET cannot erase a just-returned POST.
    const known = new Set(st.turns.map((t) => t.id));
    const extra = res.thread.filter((t) => !known.has(t.id));
    if (extra.length) st.turns.push(...extra);
    return !!extra.length;
  }
  function watch(box, source, st, paint) {
    let last = '';
    async function tick() {
      if (!box.isConnected) return;
      try {
        if (waiting(st)) {
          const changed = await refresh(source, st);
          if (box.isConnected && changed) paint();
          last = '';
        }
      } catch (e) {
        const msg = `Child results could not be refreshed: ${e && e.message ? e.message : e}`;
        const status = box.querySelector(statusOf(source));
        if (status && msg !== last) { status.textContent = msg; status.dataset.kind = 'error'; last = msg; }
      } finally {
        if (box.isConnected) setTimeout(tick, 3000);
      }
    }
    setTimeout(tick, 1000);
  }
  window.DeskV1Dispatch = { card, watch, refresh };
})();

// MC-1063: "report waiting from <child>" marker in the parent's chat.
//
// A finished child's report is queued until its parent's turn ends. While it
// waits the parent's chat showed nothing, so a person looking at the child's
// "done" chat and the parent's quiet one could not tell whether the report was
// lost. The server sends the queue for THIS session as `reports_waiting` on
// /agent/status (mc/delegation_waiting.py); this paints one notice above the
// transcript, same slot as the fork notice, and removes it once the list is
// empty, i.e. once the report has been handed to the parent.

const _reportWaitingExpanded = new Set();
function _reportWaitingKey(reports) { return JSON.stringify(reports); }

function _reportWaitingHTML(sessionId, reports) {
  const rows = reports.map(r =>
    `<div class="report-waiting-row" data-stage="${esc(r.stage)}" data-child="${esc(r.child_session_id || '')}">` +
    `<strong>${esc(r.label)}</strong> <span class="report-waiting-detail">${esc(r.detail)}</span>` +
    (r.stage === 'held' || r.stage === 'review' ?
      `<div class="report-waiting-actions"><button type="button" data-action="retry" data-event="${esc(r.event_id)}">Retry</button>` +
      `<button type="button" data-action="mark-handled" data-event="${esc(r.event_id)}">Mark handled</button></div>` : '') +
    `</div>`).join('');
  const multiple = reports.length > 1;
  const expanded = _reportWaitingExpanded.has(sessionId);
  return `<div class="report-waiting-notice" id="report-waiting-${esc(sessionId)}" ` +
    `data-key="${esc(_reportWaitingKey(reports))}" role="status">` +
    (multiple ? `<button type="button" class="report-waiting-summary" data-action="expand" ` +
      `aria-expanded="${expanded}" aria-controls="report-waiting-rows-${esc(sessionId)}">` +
      `${reports.length} reports may not have arrived - review</button>` : '') +
    `<div id="report-waiting-rows-${esc(sessionId)}" class="report-waiting-rows"${multiple && !expanded ? ' hidden' : ''}>${rows}</div>` +
    `<span class="report-waiting-error" role="alert"></span></div>`;
}

function paintReportsWaiting(sessionId, reports) {
  reports = Array.isArray(reports) ? reports : [];
  const old = document.getElementById(`report-waiting-${sessionId}`);
  // The notice sits in .agent-chat above the thread and sizeAgentChat sizes the
  // thread around it, so any add/replace/remove re-measures (same as the fork
  // notice) or the composer is pushed off the bottom.
  const resize = () => {
    const out = document.getElementById(`agent-output-${sessionId}`);
    const win = out && out.closest('.modal-window');
    if (win && typeof sizeAgentChat === 'function') sizeAgentChat(win, sessionId);
  };
  if (!reports.length) {
    _reportWaitingExpanded.delete(sessionId);
    if (old) { old.remove(); resize(); }
    return;
  }
  const host = document.createElement('div');
  host.innerHTML = _reportWaitingHTML(sessionId, reports);
  const fresh = host.firstElementChild;
  fresh.addEventListener('click', async event => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    if (button.dataset.action === 'expand') {
      const expanded = button.getAttribute('aria-expanded') !== 'true';
      if (expanded) _reportWaitingExpanded.add(sessionId);
      else _reportWaitingExpanded.delete(sessionId);
      button.setAttribute('aria-expanded', String(expanded));
      fresh.querySelector('.report-waiting-rows').hidden = !expanded;
      resize();
      return;
    }
    const report = reports.find(r => r.event_id === button.dataset.event);
    const projectId = (agentStatusCache[sessionId] || {}).projectId;
    if (!report || !projectId) return;
    const retry = button.dataset.action === 'retry';
    if (retry && !window.confirm('Retry this report? It may already have arrived, so retrying can send it twice.')) return;
    const buttons = fresh.querySelectorAll('.report-waiting-actions button');
    buttons.forEach(b => { b.disabled = true; });
    const error = fresh.querySelector('.report-waiting-error');
    error.textContent = '';
    try {
      const response = await fetch(`/api/project/${encodeURIComponent(projectId)}/agent/delegation/${retry ? 'retry' : 'mark-handled'}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ event_id: report.event_id, ...(retry ? {
          table: report.table || 'inbox', reviewed_resolution: true } : {}) }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Report action failed');
      await window.fetchAgentStatus(projectId);
    } catch (e) { error.textContent = e.message || 'Report action failed'; }
    finally { buttons.forEach(b => { b.disabled = false; }); resize(); }
  });
  if (old) {
    if (old.dataset.key !== fresh.dataset.key) { old.replaceWith(fresh); resize(); }
    return;
  }
  const out = document.getElementById(`agent-output-${sessionId}`);
  if (out && out.parentNode) { out.parentNode.insertBefore(fresh, out); resize(); }
}
// ES-module cross-boundary rule: conversation.js reaches this via window.
window.paintReportsWaiting = paintReportsWaiting;

// Modal rebuilds preserve the transcript node, but replace its surrounding
// notices. Restore both chat panes from the last authoritative status poll.
window.restoreReportsWaiting = function(root) {
  root.querySelectorAll('[id^="agent-output-"]').forEach(out => {
    const sid = out.id.slice('agent-output-'.length);
    paintReportsWaiting(sid, (agentStatusCache[sid] || {}).reportsWaiting);
  });
};

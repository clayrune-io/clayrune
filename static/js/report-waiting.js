// MC-1063: "report waiting from <child>" marker in the parent's chat.
//
// A finished child's report is queued until its parent's turn ends. While it
// waits the parent's chat showed nothing, so a person looking at the child's
// "done" chat and the parent's quiet one could not tell whether the report was
// lost. The server sends the queue for THIS session as `reports_waiting` on
// /agent/status (mc/delegation_waiting.py); this paints one notice above the
// transcript, same slot as the fork notice, and removes it once the list is
// empty, i.e. once the report has been handed to the parent.

function _reportWaitingKey(reports) {
  return reports.map(r => `${r.event_id}|${r.stage}`).join(',');
}

function _reportWaitingHTML(sessionId, reports) {
  const rows = reports.map(r =>
    `<div class="report-waiting-row" data-stage="${esc(r.stage)}" data-child="${esc(r.child_session_id || '')}">` +
    `<strong>${esc(r.label)}</strong> <span class="report-waiting-detail">${esc(r.detail)}</span></div>`).join('');
  return `<div class="report-waiting-notice" id="report-waiting-${esc(sessionId)}" ` +
    `data-key="${esc(_reportWaitingKey(reports))}" role="status">${rows}</div>`;
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
  if (!reports.length) { if (old) { old.remove(); resize(); } return; }
  const host = document.createElement('div');
  host.innerHTML = _reportWaitingHTML(sessionId, reports);
  const fresh = host.firstElementChild;
  if (old) {
    if (old.dataset.key !== fresh.dataset.key) { old.replaceWith(fresh); resize(); }
    return;
  }
  const out = document.getElementById(`agent-output-${sessionId}`);
  if (out && out.parentNode) { out.parentNode.insertBefore(fresh, out); resize(); }
}
// ES-module cross-boundary rule: conversation.js reaches this via window.
window.paintReportsWaiting = paintReportsWaiting;

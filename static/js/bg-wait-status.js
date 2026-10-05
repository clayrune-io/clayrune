// MC-946: "waiting on background task" in the chat header.
//
// A dispatched child whose spawner callback is held on a genuine background
// job has ended its turn, so the chat reads idle while the job runs. The
// server sends the label as `bg_wait` on /agent/status (empty when nothing is
// held); this paints it into the header's activity span and clears only what
// it painted, so the tool-line ticker that shares the span is left alone.

function paintBgWait(sessionId, label) {
  const el = document.getElementById(`agent-activity-${sessionId}`);
  if (!el) return;
  if (label) {
    el.textContent = label;
    el.dataset.bgWait = '1';
  } else if (el.dataset.bgWait) {
    el.textContent = '';
    delete el.dataset.bgWait;
  }
}
// ES-module cross-boundary rule: conversation.js reaches this via window.
window.paintBgWait = paintBgWait;

// UI-only metadata from the existing status poll. Never enters the transcript,
// composer or agent send path. Release/Reject remain in human-gated Settings.
const holdsByProject = new Map();

function paint(sessionId) {
  const out = document.getElementById(`agent-output-${sessionId}`);
  if (!out) return;
  const old = document.getElementById(`agent-mail-hold-${sessionId}`);
  const notice = [...holdsByProject.values()].map(holds => holds[sessionId]).find(Boolean);
  if (!notice && !old) return;
  if (notice && old && old.textContent === notice.text) return;
  if (!notice) old.remove();
  else {
    const fresh = document.createElement('div');
    fresh.id = `agent-mail-hold-${sessionId}`;
    fresh.className = 'agent-mail-hold-notice';
    fresh.setAttribute('role', 'status');
    fresh.textContent = notice.text;
    if (old) old.replaceWith(fresh);
    else out.parentNode.insertBefore(fresh, out);
  }
  const win = out.closest('.modal-window');
  if (win && typeof window.sizeAgentChat === 'function') window.sizeAgentChat(win, sessionId);
}

window.updateAgentMailHoldNotices = function(projectId, holds) {
  if (!holds || typeof holds !== 'object' || Array.isArray(holds)) return;
  const affected = new Set([...Object.keys(holdsByProject.get(projectId) || {}), ...Object.keys(holds)]);
  holdsByProject.set(projectId, holds);
  affected.forEach(paint);
};

// Opening archived chats and rebuilding modal chrome must restore the notice
// even if status was polled before the output container existed.
new MutationObserver(records => {
  const outputs = new Set();
  for (const record of records) for (const node of record.addedNodes) {
    if (node.nodeType !== 1) continue;
    if (node.id?.startsWith('agent-output-')) outputs.add(node);
    node.querySelectorAll('[id^="agent-output-"]').forEach(out => outputs.add(out));
  }
  outputs.forEach(out => paint(out.id.slice('agent-output-'.length)));
}).observe(document.documentElement, {childList: true, subtree: true});

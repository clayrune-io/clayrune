// "Tobin is working in Mission Control, 10m, 94 steps" in the dispatching chat.
//
// A child often runs in another project, so the parent's chat showed nothing
// until the report arrived. /agent/status sends `children_running` per session
// (mc/delegation_running.py); this paints one line above the transcript, the
// slot report-waiting.js uses, and removes it once the list is empty. Several
// children collapse into one summary line that expands.

const _childrenExpanded = new Set();

const _CHILD_VERB = {
  working: 'is working in',
  asking: 'is waiting on a question in',
  background: 'is waiting on a background job in',
};

function _childAge(startedAt) {
  const t = Date.parse(startedAt || '');
  if (!Number.isFinite(t)) return '';
  const mins = Math.max(0, Math.floor((Date.now() - t) / 60000));
  if (mins < 1) return '<1m';
  return mins < 60 ? `${mins}m` : `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

function _childLine(c) {
  const steps = Number(c.steps) || 0;
  return [`${c.name} ${_CHILD_VERB[c.state] || _CHILD_VERB.working} ${c.project_name}`,
    _childAge(c.started_at), `${steps} step${steps === 1 ? '' : 's'}`].filter(Boolean).join(', ');
}

function _childrenSummary(children) {
  const shown = children.slice(0, 3).map(c => c.name).join(', ');
  const more = children.length > 3 ? ` +${children.length - 3}` : '';
  const verb = children.every(c => c.state === 'working') ? 'working' : 'running';
  return `${children.length} agents ${verb}: ${shown}${more}`;
}

function _childrenHTML(sessionId, children) {
  const rows = children.map(c =>
    `<div class="children-running-row" data-state="${esc(c.state)}">` +
    `<span class="children-running-text">${esc(_childLine(c))}</span>` +
    `<button type="button" class="children-running-open" data-child="${esc(c.session_id)}" ` +
    `aria-label="Open ${esc(c.name)}'s chat">Open</button></div>`).join('');
  const multiple = children.length > 1;
  const expanded = _childrenExpanded.has(sessionId);
  return `<div class="children-running-notice" id="children-running-${esc(sessionId)}" role="status">` +
    (multiple ? `<button type="button" class="children-running-summary" aria-expanded="${expanded}" ` +
      `aria-controls="children-running-rows-${esc(sessionId)}">` +
      `<span class="children-running-dot" aria-hidden="true"></span>` +
      `<span class="children-running-text">${esc(_childrenSummary(children))}</span></button>` : '') +
    `<div id="children-running-rows-${esc(sessionId)}" class="children-running-rows` +
    `${multiple ? ' is-list' : ''}"${multiple && !expanded ? ' hidden' : ''}>${rows}</div></div>`;
}

function paintChildrenRunning(sessionId, children) {
  children = Array.isArray(children) ? children : [];
  const cached = agentStatusCache[sessionId];
  if (cached) cached.childrenRunning = children;
  const old = document.getElementById(`children-running-${sessionId}`);
  // Same as report-waiting: the thread is sized around anything above it.
  const resize = () => {
    const out = document.getElementById(`agent-output-${sessionId}`);
    const win = out && out.closest('.modal-window');
    if (win && typeof sizeAgentChat === 'function') sizeAgentChat(win, sessionId);
  };
  if (!children.length) {
    _childrenExpanded.delete(sessionId);
    if (old) { old.remove(); resize(); }
    return;
  }
  const html = _childrenHTML(sessionId, children);
  // An unchanged line stays put: a poll that rebuilt it between press and
  // release would swallow the tap. The handler reads _children, so Open still
  // follows a child whose claude_session_id has only just arrived.
  if (old && old._html === html) {
    old._children = children;
    return;
  }
  const host = document.createElement('div');
  host.innerHTML = html;
  const fresh = host.firstElementChild;
  fresh._html = html;
  fresh._children = children;
  fresh.addEventListener('click', event => {
    const summary = event.target.closest('.children-running-summary');
    if (summary) {
      const expanded = summary.getAttribute('aria-expanded') !== 'true';
      if (expanded) _childrenExpanded.add(sessionId);
      else _childrenExpanded.delete(sessionId);
      summary.setAttribute('aria-expanded', String(expanded));
      fresh.querySelector('.children-running-rows').hidden = !expanded;
      resize();
      return;
    }
    const open = event.target.closest('.children-running-open');
    const child = open && fresh._children.find(c => c.session_id === open.dataset.child);
    // The same route as clicking the child's figure on the Floor.
    if (child) window.floorOpenFigure?.(child.project_id, child.claude_session_id, child.session_id);
  });
  if (old) {
    const focused = old.contains(document.activeElement) ? document.activeElement : null;
    old.replaceWith(fresh);
    if (focused) {
      const again = focused.dataset.child
        ? [...fresh.querySelectorAll('.children-running-open')].find(b => b.dataset.child === focused.dataset.child)
        : fresh.querySelector('.children-running-summary');
      again?.focus({ preventScroll: true });
    }
    resize();
    return;
  }
  const out = document.getElementById(`agent-output-${sessionId}`);
  if (out && out.parentNode) {
    out.parentNode.insertBefore(fresh, out);
    resize();
  }
}

// The 15s fallback poll (index.html) already reads /agent/status for every open
// project window; this keeps the line current from that same payload, so an
// idle parent chat picks up a new child without a poller of its own.
window.syncChildrenRunning = function(projectId, sessions) {
  const listed = new Set();
  for (const s of sessions || []) {
    listed.add(s.session_id);
    paintChildrenRunning(s.session_id, s.children_running);
  }
  // A parent the server no longer holds must not keep claiming a running child.
  document.querySelectorAll('.children-running-notice').forEach(el => {
    const sid = el.id.slice('children-running-'.length);
    if (!listed.has(sid) && (agentStatusCache[sid] || {}).projectId === projectId) {
      paintChildrenRunning(sid, []);
    }
  });
};

// ES-module cross-boundary rule: conversation.js reaches this via window.
window.paintChildrenRunning = paintChildrenRunning;

// A modal rebuild keeps the transcript node but replaces the notices around it.
window.restoreChildrenRunning = function(root) {
  root.querySelectorAll('[id^="agent-output-"]').forEach(out => {
    const sid = out.id.slice('agent-output-'.length);
    paintChildrenRunning(sid, (agentStatusCache[sid] || {}).childrenRunning);
  });
};

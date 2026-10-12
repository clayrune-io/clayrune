// MC-1086: what Send means while the agent is mid-turn.
//
// The server decides the route (mc/mid_turn_send.py): Send STEERS a live
// Claude Mode B turn, QUEUES for vendors with no live process, and the
// Interrupt button here keeps the old kill-and-resume. This module only owns
// the composer's wording and the Interrupt control; conversation.js calls it
// from the two composer templates and passes `send_mode` through sendFollowup.

// Placeholder for a composer whose session is running. `caps` is the
// provider-capability object (mode_b_kind: 'native' = live stdin).
function runningPlaceholder(caps, mobile) {
  // Claude's default caps object carries no mode_b_kind: absent = live stdin.
  const live = !caps || !caps.mode_b_kind || caps.mode_b_kind === 'native';
  if (mobile) return live ? 'Steer agent…' : 'Queue for next turn…';
  return live
    ? 'Message the agent — steers the current turn (Enter to send)'
    : 'Message the agent — queued for its next turn (Enter to send)';
}

// Interrupt control, shown beside Send only while a turn is running.
// Empty string otherwise so the templates can interpolate it unconditionally.
function interruptButtonHTML(projectId, sessionId, status, mobile) {
  if (status !== 'running') return '';
  const e = (s) => String(s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const title = 'Stop the current turn and send this now (Esc / Stop just stops)';
  const call = `sendFollowup('${e(projectId)}','${e(sessionId)}','interrupt')`;
  return mobile
    ? `<button class="btn-interrupt-send btn-interrupt-send-mobile" type="button" onclick="${call}" title="${title}" aria-label="Interrupt and send">&#9099;</button>`
    : `<button class="btn-interrupt-send" type="button" onclick="${call}" title="${title}">Interrupt</button>`;
}

// What the server did with a send, from its response. Steer/queue start no new
// turn, so no turn_start is coming -- the caller must lift its send-in-flight
// gate instead of waiting 8s for one.
function startsNoNewTurn(data) {
  return !!data && (data.route === 'steer' || data.route === 'queue' || data.queued === true);
}

window.mcSendIntent = { runningPlaceholder, interruptButtonHTML, startsNoNewTurn };

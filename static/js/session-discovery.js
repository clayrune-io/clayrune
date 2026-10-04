// Live discovery of sessions this client has never seen.
//
// Ron, 2026-10-03 (phone): "Tobin is inactive" ... "I shouldn't be refreshing
// all the time to see who's active". Dave dispatched Tobin server-side; the
// server listed him as running, the chat list never showed him.
//
// Why: the Chats rows (_liveConvStates) and the Channel roster
// (_channelRoster) take live state ONLY from agentStatusCache, and only
// fetchAgentStatus() adds a session to it. That ran on modal open, on
// resurface, and on the client's own sends. The one recurring read, the 15s
// fallback poll in index.html, fetched every session the server had and then
// looped only over sessions already in agentHistory, so a session started by
// someone else (an agent dispatch, a schedule, another device) was in that
// response every tick and was thrown away every tick. The poll also returned
// early when the client knew of no running/idle session, so a project whose
// last chat had finished never polled at all.
//
// The poll now asks this module two things: which projects to read (adds the
// open project windows), and whether a payload it already fetched carries a
// session the client does not know. Only then does it pay for a
// fetchAgentStatus(), which adds the session, seeds its chat row, opens its
// stream and repaints, exactly as a reload would.

// A session worth surfacing: alive on the server. A finished session the
// client never saw is history, not presence; the next rail rebuild lists it.
const _LIVE = new Set(['running', 'idle']);
const _inFlight = {};

// Projects the fallback poll should read this tick: every project with a
// known running/idle session (the poll's original set) plus every open,
// un-minimized project window while the page is visible, since that is
// where a new arrival has to show up.
function statusPollProjectIds(knownLive) {
  const ids = new Set((knownLive || []).map(h => h.projectId));
  if (document.visibilityState === 'visible') {
    for (const [id, entry] of openModals) {
      if (!entry || entry.minimized || id.startsWith('__')) continue;
      ids.add(entry.projectId || id);
    }
  }
  return [...ids];
}

// True when `sessions` (an /agent/status payload) holds a live session this
// client has no record of; in that case a full fetchAgentStatus() is started
// and the caller can skip its own per-session reconcile, which that call
// covers. One discovery per project at a time.
function discoverNewSessions(projectId, sessions) {
  if (_inFlight[projectId]) return true;
  const known = new Set(agentHistory.filter(h => h.projectId === projectId).map(h => h.sessionId));
  const unseen = (sessions || []).some(s => s && s.session_id && !known.has(s.session_id) && _LIVE.has(s.status));
  if (!unseen) return false;
  _inFlight[projectId] = Promise.resolve(fetchAgentStatus(projectId))
    .catch(() => {})
    .finally(() => { delete _inFlight[projectId]; });
  return true;
}

// static/js/*.js are ES modules; the poll lives in index.html's inline script.
window.statusPollProjectIds = statusPollProjectIds;
window.discoverNewSessions = discoverNewSessions;

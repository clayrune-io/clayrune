// FE5 (MC-1179): a ceiling on how many agent chats hold a live SSE stream.
//
// Chromium allows 6 HTTP/1.1 connections per origin and an agent stream does not
// end until its turn does. A tab holding 6 streams cannot make any other request:
// the grid stops refreshing and Send/Stop never leave the browser. Four leaves
// two connections for ordinary requests (and a second dashboard tab shares the
// same budget). Over the tunnel (HTTP/2) the cap does not apply; this is for the
// plain-localhost origin where it shows first.
// Chats on screen are never dropped, so more than four visible at once (several
// modals plus split panes) can exceed it; see admitAgentStream.
export const MAX_LIVE_AGENT_STREAMS = 4;

// A chat that loses its stream is not broken: the 15 s status poll
// (fetchAgentStatus) keeps refilling its buffer and advancing agentServerLines,
// and connectAgentStream reopens with since=<that cursor> when it is viewed again,
// so the server replays only what the client has not seen.

let clock = 0;
const viewedAt = {};   // session id -> tick of the last time the chat was on screen

function _onScreen() {
  const ids = new Set();
  for (const pid of openModals.keys()) {
    if (activeAgentTab[pid]) ids.add(activeAgentTab[pid]);
    if (splitAgentTab[pid]) ids.add(splitAgentTab[pid]);
  }
  return ids;
}

export function noteAgentViewed(sessionId) {
  if (sessionId) viewedAt[sessionId] = ++clock;
}

function _drop(sessionId) {
  try { agentEventSources[sessionId].close(); } catch (_) {}
  delete agentEventSources[sessionId];
  if (agentSSEWatchdogs[sessionId]) {
    clearInterval(agentSSEWatchdogs[sessionId]);
    delete agentSSEWatchdogs[sessionId];
  }
}

// May `sessionId` open a stream? Called from connectAgentStream before it opens
// one. Under the cap: yes. At the cap: a chat on screen takes the slot of the
// least recently viewed chat that is not on screen; a chat nobody is looking at
// is refused unless it was viewed more recently than the one it would displace,
// so the 15 s poll cannot swap two background chats back and forth.
// If every other stream belongs to a chat on screen the on-screen request is
// still admitted: more than four chats visible at once (modals plus split panes)
// is rare, and a visible chat without live output is the worse failure.
export function admitAgentStream(sessionId) {
  const onScreen = _onScreen();
  const requesterVisible = onScreen.has(sessionId);
  if (requesterVisible) noteAgentViewed(sessionId);
  const others = Object.keys(agentEventSources).filter(sid => sid !== sessionId);
  if (others.length < MAX_LIVE_AGENT_STREAMS) return true;
  // A chat with a send in flight opened its stream on purpose seconds ago.
  const victims = others.filter(sid => !onScreen.has(sid)
    && !(_sendInFlight[sid] && Date.now() - _sendInFlight[sid] < 8000));
  victims.sort((a, b) => (viewedAt[a] || 0) - (viewedAt[b] || 0));
  const victim = victims[0];
  if (!victim) return requesterVisible;
  if (!requesterVisible && (viewedAt[sessionId] || 0) <= (viewedAt[victim] || 0)) return false;
  _drop(victim);
  return true;
}

window.admitAgentStream = admitAgentStream;
window.noteAgentViewed = noteAgentViewed;

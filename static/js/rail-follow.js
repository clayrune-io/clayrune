// The left rail follows the open chat (Ron, 2026-11-25: the rail showed Dave
// expanded while the pane held Tilda's chat — "I thought I was looking at Dave").
//
// Every way of opening a chat ends in one agentPanelHTML() render, so the sync
// happens there rather than in each caller. In Channel mode it expands the
// person the open chat belongs to; in every mode it brings the active row into
// view once. A user's own click on a roster row wins until another chat opens.
//
// It also carries the rail's scroll position across rebuilds: the panel is
// replaced with innerHTML on every refresh, which reset the list to the top and
// would have undone the scroll-into-view on the next poll tick.

// pid -> { sid, key, held }. `key` is the person this module expanded for `sid`;
// `held` means the user has taken the accordion over for that chat.
const _followed = {};
// pid -> { chat, personShown }: rows still waiting to be scrolled into view.
const _reveal = {};
const _scheduled = {};
const _lastMode = {};
const _rowRequested = new Set();

function _railList(pid) {
  const win = Array.from(document.querySelectorAll('.modal-window'))
    .find(el => el.dataset.modalId === pid);
  return win ? win.querySelector('.agent-rail-list, .conv-list-scroll') : null;
}

// Which roster person the open chat belongs to. The conversation row comes
// first: it is what decides whose list the chat appears under. A session too new
// to have a row falls back to its live status entry. '' = not known (yet).
function _openChatPerson(pid, sid) {
  const s = agentStatusCache[sid] || {};
  const csid = s.claudeSessionId || (s._nativeHistory ? s.providerSessionId : '') || '';
  const rows = window._userInitiatedConvos(pid, true);
  const row = (csid && rows.find(c => c.claude_session_id === csid
      || (!c.mc_session_id && c.provider_session_id === csid)))
    || rows.find(c => c.mc_session_id === sid);
  return {
    row,
    key: (row && window._convCharKey(row))
      || window._convCharKey({ character: s.character, identity: s.identity }),
  };
}

// A chat dispatched since the last /conversations read has no row to mark under
// its person. Ask once; the fetch includes the open chat and re-renders.
function _requestMissingRow(pid, sid) {
  const tag = pid + '|' + sid;
  if (_rowRequested.has(tag) || !conversationsCache[pid]) return;
  _rowRequested.add(tag);
  setTimeout(() => window.loadConversations?.(pid), 0);
}

function _followPerson(pid, sid, expanded) {
  const st = _followed[pid];
  if (st.held) return;
  const { row, key } = _openChatPerson(pid, sid);
  if (!row) _requestMissingRow(pid, sid);
  if (!key) {
    // Unknown owner: highlight nobody rather than leave someone else lit. Left
    // unrecorded so a later render, with more data, can still resolve it.
    if (!st.key) delete expanded[pid];
    return;
  }
  if (key === st.key) return;
  st.key = key;
  expanded[pid] = key;
  _reveal[pid] = { chat: true, personShown: false };
}

// Scroll only the rail list itself; scrollIntoView() would also move the modal.
function _scrollRows(list, first, last) {
  const box = list.getBoundingClientRect();
  const top = first.getBoundingClientRect().top;
  const lastBox = last.getBoundingClientRect();
  if (top >= box.top && lastBox.bottom <= box.bottom) return;
  if (lastBox.bottom - top <= box.height) list.scrollTop += top - box.top;
  else list.scrollTop += lastBox.top - box.top - (box.height - lastBox.height) / 2;
}

function _revealRows(pid, list) {
  const want = _reveal[pid];
  if (!want || !list.clientHeight) return;
  const person = list.querySelector('.channel-row.expanded');
  const chat = list.querySelector('.conv-row.active:not(.channel-row), .rail-topic.active');
  if (chat) {
    _scrollRows(list, person || chat, chat);
    delete _reveal[pid];
  } else if (person && !want.personShown) {
    // The chat's own row may still be loading (or the phone list has none):
    // show its person once and keep waiting without scrolling again.
    _scrollRows(list, person, person);
    want.personShown = true;
  }
}

// Runs while the OLD panel is still in the DOM (its HTML is being built), so the
// scroll offset read here is the one the user is looking at.
function _afterRender(pid, mode) {
  if (_scheduled[pid]) return;
  _scheduled[pid] = true;
  const old = _railList(pid);
  const keep = old && old.clientHeight && _lastMode[pid] === mode ? old.scrollTop : null;
  queueMicrotask(() => {
    delete _scheduled[pid];
    const list = _railList(pid);
    if (!list) return;
    _lastMode[pid] = mode;
    if (keep !== null && list !== old) list.scrollTop = keep;
    _revealRows(pid, list);
  });
}

window.railFollow = {
  // `expanded` is conversation.js's Channel accordion map (pid -> person key).
  sync(pid, openSid, mode, expanded) {
    if (openSid) {
      if (!_followed[pid] || _followed[pid].sid !== openSid) {
        _followed[pid] = { sid: openSid, key: '', held: false };
        _reveal[pid] = { chat: true, personShown: false };
      }
      if (mode === 'channel') _followPerson(pid, openSid, expanded);
    }
    _afterRender(pid, mode);
  },
  // A chat was opened on purpose, or the rail was reset: follow it afresh, even
  // if it is the chat that was already open.
  forget(pid) { delete _followed[pid]; },
  // The user clicked a roster row: leave the accordion alone for this chat.
  hold(pid, openSid) {
    _followed[pid] = { sid: openSid, key: '', held: true };
    delete _reveal[pid];
  },
  // The same chat continued under a new session id (a send that respawned it).
  retarget(pid, fromSid, toSid) {
    if (_followed[pid] && _followed[pid].sid === fromSid) _followed[pid].sid = toSid;
  },
};

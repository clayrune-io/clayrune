// ── In-chat search ────────────────────────────────────────────────────────
// Find something said earlier in the conversation that's CURRENTLY OPEN.
// Project-wide search across chats is a different, already-shipped surface
// (search-chats.js) — this one is scoped to a single open thread.
//
// Why this can't be a DOM scan: conversation.js caps the render at
// MAX_RENDER_LINES=500 (a "▲ N earlier lines" affordance loads the rest via
// expandedOutputSessions), and even the full client buffer it renders from
// (agentOutputBuffers[sid]) is itself capped — resume-preview.js's live SSE
// append path trims it to the last 1500 lines once a session passes 2000. A
// find-in-DOM would silently report "no matches" for text that is
// demonstrably in the conversation, for two independent reasons. So on open
// we go to the server for the complete, uncapped transcript (same log_lines
// shape the buffer already uses — GET .../transcript/<csid>/full-buffer),
// adopt it into agentOutputBuffers[sid] if it's more complete than what's
// there, force the "load all" render path, and only THEN search — so by the
// time a match is found, it is already reachable on screen. There is no
// separate "is this match above the cut" branch to get wrong.
//
// Highlighting walks rendered TEXT NODES (TreeWalker) inside #agent-output-
// <sid> and wraps matched ranges in <mark>, rather than touching innerHTML —
// the chat renders markdown/tables/mermaid/plan cards as injected HTML, and a
// string-replace across that would corrupt or double-escape it. This also
// means a match can only be found within a single text node (no regex, no
// cross-tag stitching) — the same "case-insensitive substring, nothing
// fancier" scope the brief asked for.

let csState = {};  // sessionId -> { open, query, matches:[<mark> els in doc order], idx, partial, projectId }

function _csGet(sid) {
  if (!csState[sid]) csState[sid] = { open: false, query: '', matches: [], idx: -1, partial: false, projectId: null };
  return csState[sid];
}

// Fetch the complete on-disk transcript and adopt it into the client buffer
// if it's more complete than what's already there; force the full-render
// path either way. No return value — callers always follow this with exactly
// one refreshModal() to actually draw the (now-complete) DOM.
async function _csEnsureComplete(projectId, sid) {
  const st = _csGet(sid);
  const cached = agentStatusCache[sid];
  const csid = cached && cached.claudeSessionId;
  if (csid) {
    try {
      const r = await fetch(API_BASE + `/api/project/${encodeURIComponent(projectId)}/transcript/${encodeURIComponent(csid)}/full-buffer`);
      if (r.ok) {
        const rd = await r.json();
        const serverLines = rd.log_lines || [];
        const clientLines = agentOutputBuffers[sid] || [];
        if (serverLines.length > clientLines.length) {
          agentOutputBuffers[sid] = serverLines;
        }
        st.partial = false;
      } else {
        // Nothing on disk yet (e.g. transcript not flushed) — search whatever
        // the client already has and say plainly that it's not the full story.
        st.partial = true;
      }
    } catch (e) {
      st.partial = true;
    }
  } else {
    // No claude_session_id — a non-Claude provider that keeps no transcript
    // store at all (MC-929 / _non_claude_conversation_rows). There is no
    // authoritative source to reach for; search is genuinely limited to
    // whatever this client session has buffered.
    st.partial = true;
  }
  expandedOutputSessions.add(sid);
}

function openChatSearch(projectId, sid) {
  if (!sid) return;
  const st = _csGet(sid);
  if (st.open) {
    const inp = document.getElementById(`chat-search-input-${sid}`);
    if (inp) inp.focus();
    return;
  }
  st.open = true;
  st.projectId = projectId;
  _csEnsureComplete(projectId, sid).then(() => {
    refreshModal();  // draws the bar (and, per the "load all" flag, an unrendered tail)
    // refreshModalById PRESERVES the existing #agent-output-<sid> node across
    // that rebuild instead of redrawing it (perf: avoids re-rendering a live
    // stream's whole history every few seconds) — so when the buffer just
    // grew wholesale (adopted the server's complete transcript, above), the
    // preserved node is still the OLD, shorter one and the recovered lines
    // stay invisible. Same gap _repaintAgentOutput's own docstring describes;
    // switchAgentTab hits it too and fixes it the same way, right after its
    // own refreshModal() call.
    window._repaintAgentOutput?.(sid);
    requestAnimationFrame(() => {
      const inp = document.getElementById(`chat-search-input-${sid}`);
      if (inp) { inp.focus(); inp.select(); }
    });
  });
}
window.openChatSearch = openChatSearch;

function closeChatSearch(sid) {
  const st = csState[sid];
  if (!st || !st.open) return;
  st.open = false;
  _csClearHighlights(sid);
  const bar = document.getElementById(`chat-search-bar-${sid}`);
  if (bar) bar.remove();
  const ta = document.getElementById(`agent-task-${st.projectId}`);
  if (ta) ta.focus();
}
window.closeChatSearch = closeChatSearch;

function chatSearchOnInput(sid, value) {
  const st = _csGet(sid);
  st.query = value;
  _csRunSearch(sid);
}
window.chatSearchOnInput = chatSearchOnInput;

function chatSearchOnKeydown(e, sid) {
  if (e.key === 'Enter') {
    e.preventDefault();
    chatSearchNav(sid, e.shiftKey ? -1 : 1);
  } else if (e.key === 'Escape') {
    e.preventDefault();
    e.stopPropagation();  // don't also let the document-level Escape handler close the whole modal
    closeChatSearch(sid);
  }
}
window.chatSearchOnKeydown = chatSearchOnKeydown;

function chatSearchNav(sid, dir) {
  const st = _csGet(sid);
  if (!st.matches.length) return;
  _csSetCurrent(sid, st.idx + dir);
  _csUpdateCountUI(sid);
}
window.chatSearchNav = chatSearchNav;

// ── DOM search + highlight ──────────────────────────────────────────────

function _csCollectTextNodes(root) {
  const out = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      const p = node.parentElement;
      if (!p || p.closest('.typing-indicator')) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    }
  });
  let n;
  while ((n = walker.nextNode())) out.push(n);
  return out;
}

function _csFindMatches(root, query) {
  const q = query.toLowerCase();
  if (!q) return [];
  const matches = [];
  for (const node of _csCollectTextNodes(root)) {
    const lower = node.nodeValue.toLowerCase();
    let i = 0;
    while (true) {
      const idx = lower.indexOf(q, i);
      if (idx === -1) break;
      matches.push({ node, start: idx, end: idx + q.length });
      i = idx + q.length;
    }
  }
  return matches;
}

function _csApplyHighlights(root, query) {
  const rawMatches = _csFindMatches(root, query);
  // Wrap back-to-front WITHIN each text node so an earlier match's offsets in
  // that same node stay valid as later ones split it (matches are collected
  // left-to-right per node; splitting from the tail leaves head offsets alone).
  const byNode = new Map();
  for (const m of rawMatches) {
    if (!byNode.has(m.node)) byNode.set(m.node, []);
    byNode.get(m.node).push(m);
  }
  const marks = [];
  for (const ms of byNode.values()) {
    for (let i = ms.length - 1; i >= 0; i--) {
      const { node, start, end } = ms[i];
      const range = document.createRange();
      range.setStart(node, start);
      range.setEnd(node, end);
      const mark = document.createElement('mark');
      mark.className = 'mc-chat-search-hit';
      try {
        range.surroundContents(mark);
      } catch (e) {
        continue;
      }
      marks.push(mark);
    }
  }
  // Collected in per-node reverse order across an unordered Map — restore true
  // document order so match numbering ("3 of 17") reads top-to-bottom.
  marks.sort((a, b) => {
    const pos = a.compareDocumentPosition(b);
    if (pos & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
    if (pos & Node.DOCUMENT_POSITION_PRECEDING) return 1;
    return 0;
  });
  return marks;
}

function _csClearHighlights(sid) {
  const root = document.getElementById(`agent-output-${sid}`);
  if (root) {
    root.querySelectorAll('mark.mc-chat-search-hit').forEach(mark => {
      const parent = mark.parentNode;
      if (!parent) return;
      while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
      parent.removeChild(mark);
      parent.normalize();
    });
  }
  const st = csState[sid];
  if (st) { st.matches = []; st.idx = -1; }
}

function _csRunSearch(sid) {
  const st = _csGet(sid);
  const root = document.getElementById(`agent-output-${sid}`);
  _csClearHighlights(sid);
  const q = (st.query || '').trim();
  if (!root || !q) {
    _csUpdateCountUI(sid);
    return;
  }
  st.matches = _csApplyHighlights(root, q);
  _csSetCurrent(sid, 0);
  _csUpdateCountUI(sid);
}

function _csSetCurrent(sid, idx) {
  const st = _csGet(sid);
  st.matches.forEach(m => m.classList.remove('mc-chat-search-current'));
  if (!st.matches.length) { st.idx = -1; return; }
  st.idx = ((idx % st.matches.length) + st.matches.length) % st.matches.length;
  const cur = st.matches[st.idx];
  cur.classList.add('mc-chat-search-current');
  // Deferred one frame: opening search often just forced a full repaint of
  // #agent-output-<sid> (see openChatSearch's _repaintAgentOutput call), and
  // that repaint's own bottom-pin scroll (_scheduleAgentPinScroll, resume-
  // preview.js) is coalesced into a requestAnimationFrame that may still be
  // pending — scrolling to the match synchronously here would just get
  // stomped a frame later. Queuing behind it (same rAF-coalescing pattern,
  // one tick after) guarantees this write is the last one and it sticks.
  //
  // 'auto' (instant), not 'smooth': the first jump can be the ENTIRE length
  // of a just-expanded conversation (bottom, where the pin-scroll above just
  // put it, to a match near the top) — thousands of pixels. A smooth animation
  // over that distance is slow and disorienting, not a nice touch; jump there
  // directly, the same way opening a search result normally works.
  requestAnimationFrame(() => cur.scrollIntoView({ block: 'center', behavior: 'auto' }));
}

function _csUpdateCountUI(sid) {
  const st = _csGet(sid);
  const el = document.getElementById(`chat-search-count-${sid}`);
  if (el) {
    el.textContent = !st.query.trim() ? ''
      : (st.matches.length ? `${st.idx + 1} of ${st.matches.length}` : 'No matches');
  }
  const prev = document.getElementById(`chat-search-prev-${sid}`);
  const next = document.getElementById(`chat-search-next-${sid}`);
  if (prev) prev.disabled = !st.matches.length;
  if (next) next.disabled = !st.matches.length;
}

// Re-run the current query after something OTHER than our own open/typing
// flow rebuilds #agent-output-<sid> from scratch (wiping any <mark> wraps
// without going through chatSearchBarHTML). See the hook in
// resume-preview.js's _repaintAgentOutput.
window._csOnRepaint = function (sid) {
  const st = csState[sid];
  if (!st || !st.open) return;
  _csRunSearch(sid);
};

// Called from conversation.js's template every time the panel re-renders
// while search is open (tab switch, plan approval, our own open call, ...).
// A fresh render means #agent-output-<sid> was just rebuilt from
// agentOutputBuffers[sid] with no highlight marks, so schedule a reapply for
// right after this render's innerHTML assignment commits.
function chatSearchBarHTML(pid, sid) {
  const st = csState[sid];
  if (!st || !st.open) return '';
  setTimeout(() => { if (csState[sid] && csState[sid].open) _csRunSearch(sid); }, 0);
  const q = esc(st.query || '');
  const hasQuery = !!st.query.trim();
  const countText = !hasQuery ? '' : (st.matches.length ? `${st.idx + 1} of ${st.matches.length}` : 'No matches');
  const navDisabled = st.matches.length ? '' : 'disabled';
  return `<div class="mc-chat-search-bar" id="chat-search-bar-${esc(sid)}">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true" style="flex-shrink:0;opacity:.6"><circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="2"/><line x1="16.5" y1="16.5" x2="21" y2="21" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
    <input type="text" class="mc-chat-search-input" id="chat-search-input-${esc(sid)}"
      placeholder="Find in this conversation" value="${q}" autocomplete="off"
      oninput="chatSearchOnInput('${esc(sid)}', this.value)"
      onkeydown="chatSearchOnKeydown(event,'${esc(sid)}')">
    <span class="mc-chat-search-count" id="chat-search-count-${esc(sid)}">${esc(countText)}</span>
    <button type="button" class="mc-chat-search-nav" id="chat-search-prev-${esc(sid)}" title="Previous (Shift+Enter)" onclick="chatSearchNav('${esc(sid)}',-1)" ${navDisabled}>&#9650;</button>
    <button type="button" class="mc-chat-search-nav" id="chat-search-next-${esc(sid)}" title="Next (Enter)" onclick="chatSearchNav('${esc(sid)}',1)" ${navDisabled}>&#9660;</button>
    <button type="button" class="mc-chat-search-close" title="Close (Esc)" onclick="closeChatSearch('${esc(sid)}')">&#10005;</button>
    ${st.partial ? `<div class="mc-chat-search-partial">Partial results — this conversation keeps no full transcript on disk, so only what's currently loaded was searched.</div>` : ''}
  </div>`;
}
window.chatSearchBarHTML = chatSearchBarHTML;

// ── Ctrl/Cmd+F while the chat pane has focus ────────────────────────────
// Scoped to the topmost/focused project modal (focusedModalId, index.html),
// its "agent" tab, and its active session. If focus sits in some OTHER text
// field (global search, backlog note, settings...) outside that modal, THAT
// field owns Ctrl+F — fall through to it (and, failing that, to the
// browser's native find) rather than hijacking the shortcut everywhere.
function _csFindTarget() {
  if (typeof focusedModalId === 'undefined' || !focusedModalId) return null;
  const entry = openModals.get(focusedModalId);
  if (!entry || entry.minimized || !entry.projectId || !entry.element) return null;
  if ((modalActiveTab[entry.projectId] || 'agent') !== 'agent') return null;
  const sid = activeAgentTab[entry.projectId];
  if (!sid || !agentStatusCache[sid]) return null;
  const ae = document.activeElement;
  if (ae && ae !== document.body && !entry.element.contains(ae)) return null;
  return { projectId: entry.projectId, sessionId: sid };
}

document.addEventListener('keydown', (e) => {
  const isFind = (e.ctrlKey || e.metaKey) && !e.altKey && (e.key === 'f' || e.key === 'F');
  if (!isFind) return;
  const target = _csFindTarget();
  if (!target) return;  // not our surface — let the browser's native find run
  e.preventDefault();
  openChatSearch(target.projectId, target.sessionId);
});

// ── Viewport-height sync (keyboard show/hide) ───────────────────────────────
// Android WebView doesn't reliably recompute `dvh` after the soft keyboard is
// dismissed, so a full-height project modal (height:100dvh) stayed pinned at the
// keyboard-open height — leaving the dashboard visible below it (the "split
// screen" bug). Drive the mobile modal height from visualViewport.height via a
// CSS var instead; it updates on every keyboard show AND hide, so the modal
// shrinks to keep the composer above the keyboard and expands back on dismiss.
// Desktop is unaffected: the mobile modal-height CSS rules live inside the
// ≤960px media query, and the var falls back to 100dvh until first set.
(function mcViewportHeightSync() {
  const vv = window.visualViewport || null;
  const _isField = t => !!t && (t.tagName === 'TEXTAREA' || t.tagName === 'INPUT' || t.isContentEditable);
  // A real soft keyboard is a big bite out of the screen. Anything smaller is a
  // collapsing URL bar, a rounding artefact, or a stale reading — and shrinking
  // the app for those is half of why it ended up pinned short.
  const MIN_KB = 120;
  let _raf = 0, _lastApplied = 0;
  // Layout viewport — the honest number. In `resizes-visual` (Chrome/Safari
  // default) the keyboard never touches it; in `resizes-content` (Android
  // WebView adjustResize) the window genuinely resizes, so it shrinks and
  // restores for real. Either way it is never a stale leftover, which
  // visualViewport.height demonstrably can be.
  function layoutH() {
    return Math.max(window.innerHeight || 0, document.documentElement.clientHeight || 0);
  }
  function _renudgeOpenModals() {
    try {
      if (typeof openModals === 'undefined' || typeof sizeAgentChat !== 'function') return;
      openModals.forEach((entry, id) => {
        if (!entry || entry.minimized || !entry.element) return;
        const sid = (typeof activeAgentTab !== 'undefined') ? activeAgentTab[entry.projectId || id] : null;
        if (sid) sizeAgentChat(entry.element, sid);
      });
    } catch (e) { /* best-effort relayout — never block the height write */ }
  }
  // Whether the current vv.height reading is known-fresh rather than a
  // leftover from before the app was backgrounded. Sets false only around a
  // background/foreground cycle (forceFull, below) and flips back true the
  // moment vv reports ANYTHING new — a real keyboard reopening on resume
  // (e.g. an autofocus) always fires a genuine resize, so that path still
  // shrinks correctly; it just can't be assumed true from an untouched
  // pre-background reading.
  let _vvTrusted = true;
  // A recent keystroke in the composer is the only signal available (in
  // Chrome's default resizes-visual mode the layout viewport never shrinks
  // for a keyboard at all, so layoutH() can't tell real-open from
  // stale-dismissed either) that a focused field's keyboard is genuinely up
  // RIGHT NOW, rather than a stale-vv leftover with nothing left to disprove
  // it. Recovery paths below must not yank the inset out from under someone
  // actively typing a follow-up while the agent finishes (the common case)
  // — gate every recovery that can fire while a field is still focused on
  // this, exactly as forceFull()'s bg-resume case is gated on backgrounding
  // being unconditional proof instead.
  const RECENT_ACTIVITY_MS = 2000;
  let _lastFieldActivityAt = 0;
  // A live keystroke is also itself trust-restoring, same as a vv resize/
  // scroll event: it's real physical evidence that whatever vv.height reads
  // RIGHT NOW reflects an actually-present keyboard, not a stale leftover
  // (a truly-dismissed, inactive field never gets one). This is what makes
  // the guard below self-correcting rather than a one-way door: if a false
  // recovery ever forces full height during a genuine typing pause, the
  // next keystroke re-trusts the real vv reading and reschedules a proper
  // recompute — it doesn't wait for a vv event that may never come while
  // the keyboard's own on-screen state never changes.
  function _onFieldActivity(e) {
    if (!_isField(e.target)) return;
    _lastFieldActivityAt = Date.now();
    _vvTrusted = true;
    schedule();
  }
  document.addEventListener('input', _onFieldActivity, true);
  document.addEventListener('keydown', _onFieldActivity, true);
  function _fieldLooksLive() {
    return _isField(document.activeElement) && (Date.now() - _lastFieldActivityAt) < RECENT_ACTIVITY_MS;
  }
  // Recovery for a focused field with no live-typing evidence: same
  // assume-stale-then-let-a-fresh-vv-reading-correct-it move as forceFull(),
  // just reached from a different kind of "something happened, the vv might
  // be lying" moment instead of backgrounding. A field that IS being typed
  // into is left completely alone — its existing vv-tracked inset is trusted.
  function _recoverStaleFocusedInset() {
    if (_fieldLooksLive()) return;
    forceFull();
  }
  function apply() {
    _raf = 0;
    const lh = layoutH();
    const vh = (vv && vv.height) ? vv.height : lh;
    // Size from the LAYOUT viewport minus a keyboard inset, rather than from
    // visualViewport.height directly. The inset is only believed when a text
    // field has focus (nothing else can raise a keyboard) AND it is large
    // enough to be one, and it is capped so a bad reading can never eat the
    // screen. Previously a stale short vv.height became the app height outright
    // and nothing could walk it back — the reported half-window.
    let inset = _vvTrusted ? Math.max(0, lh - vh - (vv ? vv.offsetTop : 0)) : 0;
    if (inset < MIN_KB || !_isField(document.activeElement)) inset = 0;
    inset = Math.min(inset, Math.round(lh * 0.6));
    const h = Math.round(lh - inset);
    if (h === _lastApplied) return;
    const grew = h > _lastApplied;
    _lastApplied = h;
    document.documentElement.style.setProperty('--mc-app-vh', h + 'px');
    // sizeAgentChat latches EXPLICIT pixel heights onto the tab content, agent
    // panel, chat and output. The modal's own ResizeObserver re-runs it, but
    // only once layout has settled — re-run it directly on the way back up so
    // the thread refills the reclaimed space in the same frame instead of
    // leaving a dead band under it. Deliberately narrower than a synthetic
    // window 'resize', which would also force a full dashboard re-render.
    if (grew) _renudgeOpenModals();
  }
  function schedule() { if (!_raf) _raf = requestAnimationFrame(apply); }
  // Re-apply across a settle window: some Android WebViews report a stale
  // viewport height for a beat after the keyboard animates away, and the vv
  // 'resize' event can fire before the height finishes updating — or not at all
  // on a programmatic blur (e.g. sending a follow-up that interrupts the agent),
  // which left the modal stuck at keyboard height (the "split screen").
  function settle() { schedule(); setTimeout(apply, 120); setTimeout(apply, 350); setTimeout(apply, 700); }
  // Every recovery above still depends on either the FOCUSED element changing
  // or vv/layout actually reporting a new number — both of which a stale-vv
  // dismiss (2c7e42a) can permanently deny. Until now the only way out was a
  // tap on non-control content (see the touchend handler below), which never
  // fires if the user just backgrounds the app / locks the screen with the
  // keyboard open and comes back to read, not type (the 2026-09-22 report: a
  // COMPLETED chat stuck at ~60% height with no interaction in between).
  // Backgrounding is unconditional proof the keyboard is gone — both Android
  // and iOS force-dismiss it, and neither reopens it on its own on return — so
  // treat "visible again" as an authority stale focus/vv can't override:
  // write the full layout height immediately, bypassing the focus/inset gate
  // for this one write, then let settle() reassert a real inset shortly after
  // if resuming genuinely re-opened the keyboard (e.g. an autofocus).
  function forceFull() {
    _vvTrusted = false;  // don't let apply()'s own settle() calls undo this with the same stale reading
    const lh = layoutH();
    _lastApplied = -1;  // defeat the h === _lastApplied no-op guard in apply()
    document.documentElement.style.setProperty('--mc-app-vh', lh + 'px');
    _lastApplied = lh;
    _renudgeOpenModals();
    settle();
  }
  apply();
  if (vv) {
    vv.addEventListener('resize', () => { _vvTrusted = true; schedule(); });
    vv.addEventListener('scroll', () => { _vvTrusted = true; schedule(); });
  }
  window.addEventListener('resize', schedule);
  // A one-shot apply() at a guessed 200ms couldn't help if the post-rotation
  // layout/vv values hadn't settled yet — same flakiness settle() already
  // exists to cover, so use it here too instead of a single fixed-delay guess.
  window.addEventListener('orientationchange', () => setTimeout(settle, 200));
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') forceFull(); });
  window.addEventListener('pageshow', forceFull);
  // Keyboard show/hide tracks focus entering/leaving a text field — the most
  // reliable signal when the vv event is flaky. Settle on both.
  document.addEventListener('focusin', e => { if (_isField(e.target)) settle(); });
  document.addEventListener('focusout', e => { if (_isField(e.target)) settle(); });
  // Down-button keyboard dismiss keeps focus ON the field and fires NEITHER a
  // focusout NOR (on some Android WebViews) a visualViewport 'resize' — so
  // nothing re-runs apply() and the modal stays pinned short (the reported
  // half-screen). A light watchdog catches it: whenever the visual viewport is
  // meaningfully taller than what we've allocated, there is unused room (the
  // keyboard is gone) → re-sync. It only ever expands, so it can't fight a
  // legitimately open keyboard.
  if (vv) setInterval(() => { if (vv.height - _lastApplied > 6) schedule(); }, 500);
  // Second watchdog, on the layout viewport. The one above can't help when
  // vv.height itself is the stale value (some Android WebViews stop updating it
  // after a down-button dismiss) — then vv.height === _lastApplied forever and
  // the app stays pinned short. The layout viewport doesn't go stale, so if we
  // have allocated meaningfully less than it and no text field is focused,
  // there is no keyboard and the missing space is ours to take back.
  //
  // Deliberately still bails whenever a field is focused, at this 500ms
  // cadence: `layoutH() - _lastApplied > 6` is true for the ENTIRE duration
  // of any correctly-tracked open keyboard (that gap IS the inset, not
  // evidence of staleness), so gating this tight a poll on _fieldLooksLive()
  // alone force-recovers every ordinary "focused, keyboard up, hasn't typed
  // in the last 2s yet" moment — including the instant right after a fresh,
  // legitimate focus — not just the stale-dismiss case. Measured regression
  // during review (2026-09-25): it broke the plain "focus a field, keyboard
  // opens" baseline outright. The focused-field self-heal lives in the
  // slower, deliberately-rare invariant below instead.
  setInterval(() => {
    if (_isField(document.activeElement)) return;
    if (layoutH() - _lastApplied > 6) schedule();
  }, 500);
  // NO standing timer for a focused field (Dave, 2026-09-25 review of 8d4ca7d).
  // An 8s invariant gated on RECENT_ACTIVITY_MS fires during every ordinary
  // 2-8s pause with a genuinely open keyboard (reading the reply, thinking,
  // voice input that fires no keydown) and drops the composer behind the
  // keyboard until the next keystroke: a recurring visible regression traded
  // for a rare stale case. Focused-field recovery stays event-driven: turn
  // settle (mcRecoverViewportOnStatusSettle) and backgrounding (forceFull).
  // A tap/scroll after dismissing the keyboard is another chance to re-read a
  // now-fresh viewport height.
  document.addEventListener('touchend', schedule, { passive: true });
  // Down-button (or swipe) dismiss keeps focus ON the field, so every
  // keyboard-open signal we have stays true and the app never re-expands. The
  // user's next TAP on ordinary content is an unambiguous "done typing" — blur
  // the field, which fires focusout and settles back to full height. Movement
  // is measured so a scroll (which must not close a keyboard mid-read) is not
  // mistaken for a tap, and taps on controls are left alone so pressing Send or
  // moving the caret doesn't dismiss the keyboard out from under the user.
  const _CONTROLS = 'input, textarea, select, button, a, label, [contenteditable=""], [contenteditable="true"], [role="button"], .agent-chat-separator';
  let _tx = 0, _ty = 0;
  document.addEventListener('touchstart', e => {
    const t = e.touches && e.touches[0];
    _tx = t ? t.clientX : 0; _ty = t ? t.clientY : 0;
  }, { passive: true });
  document.addEventListener('touchend', e => {
    const a = document.activeElement;
    if (!_isField(a)) return;
    const t = e.changedTouches && e.changedTouches[0];
    if (!t || Math.abs(t.clientX - _tx) > 10 || Math.abs(t.clientY - _ty) > 10) return;  // a scroll, not a tap
    const el = e.target;
    if (el === a || (el && el.closest && el.closest(_CONTROLS))) return;
    try { a.blur(); } catch (err) {}
    settle();
  }, { passive: true });
  // Exposed for the send path (conversation.js): after Send blurs the
  // composer, call this synchronously (before any await/network/render) so
  // the layout expands in the SAME frame instead of waiting for whichever
  // rAF/timeout/watchdog next happens to fire. `apply()` already forces
  // inset=0 once the field is no longer focused, so this doesn't need its
  // own "pretend the keyboard is gone" branch — it just needs to run NOW.
  window.mcRestoreFullHeight = apply;
  // Exposed for updateAgentStatusUI (index.html): a turn settling (running →
  // idle/error/completed) deliberately skips the full modal rebuild (MC-940 —
  // rebuilding the composer every turn cost ~205ms/keystroke on mobile), so
  // nothing in that lightweight status patch ever touches focus or the
  // keyboard inset. If the composer still holds focus from a stale-vv dismiss
  // (down-button or the Android back gesture — same WebView quirk as the
  // 2c7e42a/931449b cases: no focusout, no vv resize) that predates the turn
  // ending, the modal is stuck at keyboard height with no keyboard on screen
  // and NOTHING left to prove it — the layout watchdog explicitly stands down
  // while a field is focused, and the user may never tap the transcript (they
  // were just watching the reply finish). Unlike backgrounding, a settled turn
  // is NOT unconditional proof the keyboard is gone — the common case is
  // someone actively typing a follow-up while the agent finishes — so this
  // goes through the guarded recovery, not a raw forceFull(): a field with a
  // live-typing signal is left alone; a quiet one gets the same
  // assume-no-keyboard-then-let-a-real-vv-reading-correct-it recovery already
  // trusted for app backgrounding.
  window.mcRecoverViewportOnStatusSettle = _recoverStaleFocusedInset;
})();

// ── Mobile UI: app bar greeting + filter pills (≤960px, warm tone) ──────────
function renderMobileAppBar() {
  if (window.innerWidth > 960) return;
  const eyebrow = document.getElementById('mc-eyebrow');
  if (!eyebrow) return;
  const now = new Date();
  const day = now.toLocaleDateString(undefined, { weekday: 'long' });
  const h = now.getHours();
  const part = h < 5 ? 'night' : h < 12 ? 'morning' : h < 17 ? 'afternoon' : 'evening';
  eyebrow.textContent = `${day} ${part}`;
}

function renderMobileFilterPills() {
  const row = document.getElementById('mobile-filter-pills');
  if (!row) return;
  if (window.innerWidth > 960) { row.innerHTML = ''; return; }
  const counts = { all: allProjects.length, unread: 0, urgent: 0, working: 0, done: 0, idle: 0 };
  allProjects.forEach(p => {
    const fs = friendlyStatus(p);
    if (unreadCount(p) > 0) counts.unread++;
    if (fs === 'asking' || fs === 'stuck') counts.urgent++;
    if (fs === 'working') counts.working++;
    if (fs === 'done') counts.done++;
    if (fs === 'idle') counts.idle++;
  });
  const af = activeFilter || 'all';
  const pills = [
    { id: 'urgent',    label: 'Needs you', count: counts.urgent,  cls: 'urgent' },
    { id: 'unread',    label: 'Unread',    count: counts.unread,  cls: '' },
    { id: 'all',       label: 'All',       count: counts.all,     cls: '' },
    { id: 'active',    label: 'Working',   count: counts.working, cls: '' },
    { id: 'completed', label: 'Done',      count: counts.done,    cls: '' },
    { id: 'parked',    label: 'Resting',   count: counts.idle,    cls: '' },
  ];
  row.innerHTML = pills.map(p => {
    if (!p.count && p.id !== 'all' && af !== p.id) return '';
    const active = af === p.id;
    // §1b: the "Needs you" pill now opens the dedicated inbox surface (Decision
    // 5a) instead of filtering the list; the other pills still filter.
    const onclick = p.id === 'urgent' ? 'openInbox()' : `setFilter('${p.id}')`;
    return `<button class="mc-pill ${p.cls} ${active ? 'active' : ''}" onclick="${onclick}">${esc(p.label)}${p.count ? ` <span class="count">${p.count}</span>` : ''}</button>`;
  }).join('');
}

// Re-render on resize so mobile-only blocks appear/disappear cleanly
// (incl. the grid↔chat-list swap when crossing the 960px boundary).
// ── Mobile chat list (WhatsApp-style) ───────────────────────────────────────
// On ≤960px the card grid is replaced by a contact-list of projects:
// avatar + name + live-status subtitle + time + unread badge, pinned items
// (asking/stuck) on top, then by recency. Desktop is untouched.
function isMobileChatList() { return window.innerWidth <= 960; }

// Single client-local touch-point for read state. Structured so a later
// move to server-side per-device tracking is a body swap, not a refactor.
function markProjectSeen(pid) {
  if (!pid) return;
  projectLastSeen[pid] = Date.now();
  try { localStorage.setItem('mc_proj_seen', JSON.stringify(projectLastSeen)); } catch (e) {}
}

// Count of actionable agent events the user hasn't seen for project p.
// Actionable = (a) the agent is waiting on the user to continue
// (friendlyStatus 'asking' — plan approval / question / waiting), or
// (b) an autonomous (non-manual trigger) session produced an update.
// Interactive turns the user drove are NOT counted (they were watching).
// Derived on every render from polled state — deliberately NOT SSE — because
// closed projects have no live connection (Chromium 6-slot cap closes SSE on
// turn_complete), so an SSE-incremented counter would silently miss them.
function unreadCount(p) {
  if (!p) return 0;
  const seen = projectLastSeen[p.id] || 0;
  let n = 0;
  // (a) standing "asking" — counts once, keyed to the project's last_updated
  // onset so it does not inflate every poll while the user is away.
  if (friendlyStatus(p) === 'asking') {
    if ((Date.parse(p.last_updated || '') || 0) > seen) n += 1;
  }
  // (b) autonomous-session updates newer than last-seen. Hivemind workers are
  // excluded as noise; the orchestrator finishing IS a real update.
  agentHistory.forEach(h => {
    if (h.projectId !== p.id) return;
    if (!h.triggerType || h.triggerType === 'manual') return;
    if (isHivemindWorker(h)) return;
    if (!(h.status === 'completed' || h.status === 'idle' || h.status === 'error')) return;
    if ((Date.parse(h.startedAt || '') || 0) > seen) n += 1;
  });
  return n;
}

function projectInitials(p) {
  const s = String((p && (p.name || p.id)) || '?').trim();
  const parts = s.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return s.slice(0, 2).toUpperCase();
}

function projectRowHTML(p) {
  const fs = friendlyStatus(p);
  const unread = unreadCount(p);
  const av = p.emoji
    ? `<span class="cr-av cr-av-emoji">${esc(p.emoji)}</span>`
    : `<span class="cr-av cr-av-init">${esc(projectInitials(p))}</span>`;
  const badge = unread > 0
    ? `<span class="cr-badge">${unread > 9 ? '9+' : unread}</span>` : '';
  return `
  <div class="mc-chat-row friendly-${fs} ${unread ? 'cr-unread' : ''}" data-id="${esc(p.id)}">
    <div class="cr-avatar friendly-${fs}">${av}<span class="cr-ring"></span></div>
    <div class="cr-main">
      <div class="cr-top">
        <span class="cr-name">${(p.pinned_conversations || []).length ? '<span class="cr-pin" title="Has pinned chat(s)">&#x1F4CC;</span> ' : ''}${esc(p.name || p.id)}</span>
        <span class="cr-time">${esc(p.last_updated_relative || '')}</span>
      </div>
      <div class="cr-bot">
        <span class="cr-sub">${esc(friendlySummary(p))}</span>
        ${badge}
      </div>
    </div>
  </div>`;
}

// "Waiting on you" — the dashboard's blocking/actionable surface (distinct from
// the Inbox timeline). Rendered inline at the top of the mobile dashboard from
// _buildAttentionList; each card carries an Answer / Review / Unblock button that
// deep-links to the question form / plan card in that chat.
function _waitingOnYouHTML() {
  const items = (typeof _buildAttentionList === 'function') ? _buildAttentionList() : [];
  if (!items.length) return '';
  const rows = items.map(it => {
    const filled = it.kind === 'question' || it.kind === 'input';  // Answer = primary
    return `<div class="woy-row" data-project-id="${esc(it.projectId)}" data-session-id="${esc(it.sessionId || '')}">
      <span class="woy-icon">${it.icon}</span>
      <div class="woy-main">
        <div class="woy-project">${esc(it.project)}</div>
        <div class="woy-msg">${esc(it.msg)}</div>
      </div>
      <button class="woy-act ${filled ? 'woy-primary' : 'woy-ghost'}">${esc(it.action || 'Open')}</button>
    </div>`;
  }).join('');
  return `<div class="woy-section">
    <div class="woy-head"><span class="woy-title">Waiting on you</span><span class="woy-count">${items.length}</span></div>
    <div class="woy-list">${rows}</div>
  </div>`;
}

function _wireWaitingOnYou(col) {
  col.querySelectorAll('.woy-row').forEach(row => {
    row.addEventListener('click', () => {
      const pid = row.dataset.projectId, sid = row.dataset.sessionId;
      if (sid && typeof openProjectAtSession === 'function') openProjectAtSession(pid, sid);
      else if (typeof openProjectModal === 'function') openProjectModal(pid);
    });
  });
}

// Desktop dashboard: fill the #waiting-on-you block above the project grid.
// (Mobile renders its own copy inside the conversation list via renderMobileChatList.)
// Empty output → the container is :empty and CSS hides it.
function renderWaitingOnYou() {
  const el = document.getElementById('waiting-on-you');
  if (!el) return;
  el.innerHTML = _waitingOnYouHTML();
  _wireWaitingOnYou(el);
}
window.renderWaitingOnYou = renderWaitingOnYou;

function renderMobileChatList(col) {
  const waiting = _waitingOnYouHTML();
  const filtered = filterProjects();
  if (!filtered.length) { col.innerHTML = waiting + '<div class="loading">No projects match filter</div>'; _wireWaitingOnYou(col); return; }
  const rank = p => {
    if ((p.pinned_conversations || []).length) return 0;  // has a pinned chat → always top (survives restarts/interfaces)
    const fs = friendlyStatus(p);
    if (fs === 'asking') return 1;   // needs you → next
    if (fs === 'stuck')  return 2;   // blocked → next
    return 3;                        // everything else → recency below
  };
  const sorted = filtered.slice().sort((a, b) => {
    const r = rank(a) - rank(b);
    if (r !== 0) return r;
    return (Date.parse(b.last_updated || '') || 0) - (Date.parse(a.last_updated || '') || 0);
  });
  col.innerHTML = waiting + `<div class="mc-chat-list">${sorted.map(projectRowHTML).join('')}</div>`;
  _wireWaitingOnYou(col);
  col.querySelectorAll('.mc-chat-row').forEach(row => {
    row.addEventListener('click', () => openProjectModal(row.dataset.id));
  });
}

// ── Mobile Inbox — the cross-project notification timeline ───────────────────
// "What happened while I was away" (past-tense, non-blocking). Distinct from
// "Waiting on you" (blocking/actionable, on the dashboard). Backed by
// /api/notifications — every agent update that fired a push. Email-like:
// search, read/unread, dismiss. Tapping a row opens that chat.
let _inboxQuery = '', _inboxTimer = null, _inboxSeq = 0;

function _inboxDayLabel(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const same = (a, b) => a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (same(d, now)) return 'Today';
  if (same(d, y)) return 'Yesterday';
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

function _inboxRowHTML(it) {
  const icon = it.kind === 'turn_complete' ? '✓' : '💬';  // ✓ done · 💬 update
  const when = timeAgoJS(new Date((it.ts || 0) * 1000).toISOString()) || '';
  const text = (it.body || it.title || '').slice(0, 160);
  return `<div class="mib-row${it.read ? '' : ' mib-unread'}" data-id="${esc(it.id)}"
      data-project-id="${esc(it.project_id || '')}" data-session-id="${esc(it.session_id || '')}">
    <span class="mib-icon">${icon}</span>
    <div class="mib-main">
      <div class="mib-project">${esc(it.project_name || 'Clayrune')}<span class="mib-time">${esc(when)}</span></div>
      <div class="mib-msg">${esc(text)}</div>
    </div>
    <button class="mib-del" data-id="${esc(it.id)}" title="Dismiss" aria-label="Dismiss">&#10005;</button>
  </div>`;
}

// Shared timeline renderer — fills any list element (mobile overlay OR the
// desktop Inbox modal) from /api/notifications. The two never render at once.
async function _renderInboxList(list) {
  if (!list) return;
  const seq = ++_inboxSeq;
  const q = _inboxQuery.trim();
  if (!list.dataset.loaded) list.innerHTML = '<div class="mib-empty">Loading…</div>';
  let data = { items: [], unread: 0 };
  try {
    const res = await fetch(API_BASE + '/api/notifications?limit=200'
      + (q ? '&q=' + encodeURIComponent(q) : ''));
    if (res.ok) data = await res.json();
  } catch (e) { /* offline → keep whatever's shown */ }
  if (seq !== _inboxSeq) return;  // a newer render superseded this one
  list.dataset.loaded = '1';
  setInboxBadge(data.unread || 0);
  const items = data.items || [];
  if (!items.length) {
    list.innerHTML = `<div class="mib-empty">${q ? 'No updates match &ldquo;' + esc(q) + '&rdquo;.' : 'No updates yet.'}</div>`;
    return;
  }
  let html = '', curLabel = null;
  items.forEach(it => {
    const label = _inboxDayLabel(it.ts);
    if (label !== curLabel) { curLabel = label; html += `<div class="mib-day">${esc(label)}</div>`; }
    html += _inboxRowHTML(it);
  });
  list.innerHTML = html;
  list.querySelectorAll('.mib-row').forEach(row => {
    row.addEventListener('click', () => openNotification(row.dataset.id,
      row.dataset.projectId, row.dataset.sessionId));
  });
  list.querySelectorAll('.mib-del').forEach(btn => {
    btn.addEventListener('click', e => { e.stopPropagation(); deleteNotification(btn.dataset.id); });
  });
}
function renderInbox() { return _renderInboxList(document.getElementById('mobile-inbox-list')); }

// ── Desktop Inbox — Outlook-style mail list ──────────────────────────────────
// Sender (project) · Subject · Time, with a Conversation | Sender view toggle.
// The server already collapses the notification store to one row per
// conversation thread, so "Conversation" is the flat time-sorted list (each row
// IS a thread) and "Sender" re-groups those same rows under each project.
let _inboxView = 'conversation';
try { _inboxView = localStorage.getItem('mc_inbox_view') === 'sender' ? 'sender' : 'conversation'; } catch (e) {}

function setInboxView(v) {
  _inboxView = (v === 'sender') ? 'sender' : 'conversation';
  try { localStorage.setItem('mc_inbox_view', _inboxView); } catch (e) {}
  document.querySelectorAll('.dib-view-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.view === _inboxView));
  renderDesktopInbox();
}
window.setInboxView = setInboxView;

// Notifications carry `title` and `body`. For turn-complete events the title is
// just the project name (== sender), so it's useless as a subject. Derive a real
// subject from the first sentence/line of the body in that case; the remainder
// becomes the preview — mirroring how a mail client shows "Subject — preview…".
function _inboxSubjectPreview(it) {
  const proj = (it.project_name || 'Clayrune').trim();
  let subj = (it.title || '').trim();
  let body = (it.body || '').replace(/\s+/g, ' ').trim();
  if (!subj || subj === proj) {
    const firstLine = ((it.body || '').split('\n')[0] || body).replace(/\s+/g, ' ').trim();
    const m = firstLine.match(/^(.{0,80}?[.!?])(\s|$)/);
    subj = (m ? m[1] : firstLine).slice(0, 90).trim();
    const rest = body.slice(subj.length).replace(/^[\s—–-]+/, '').trim();
    body = rest;
  }
  return { subject: subj || '(no summary)', preview: (body || '').slice(0, 140) };
}

function _desktopInboxRowHTML(it) {
  const { subject, preview } = _inboxSubjectPreview(it);
  const when = timeAgoJS(new Date((it.ts || 0) * 1000).toISOString()) || '';
  return `<div class="dib-row${it.read ? '' : ' dib-unread'}" data-id="${esc(it.id)}"
      data-project-id="${esc(it.project_id || '')}" data-session-id="${esc(it.session_id || '')}"
      title="${esc(it.project_name || 'Clayrune')}">
    <span class="dib-dot" aria-hidden="true"></span>
    <span class="dib-sender">${esc(it.project_name || 'Clayrune')}</span>
    <div class="dib-body"><span class="dib-subject">${esc(subject)}</span>${preview ? `<span class="dib-preview"> — ${esc(preview)}</span>` : ''}</div>
    <span class="dib-time">${esc(when)}</span>
    <button class="dib-del" data-id="${esc(it.id)}" title="Dismiss" aria-label="Dismiss">&#10005;</button>
  </div>`;
}

async function _renderDesktopInboxList(list) {
  if (!list) return;
  const seq = ++_inboxSeq;
  const q = _inboxQuery.trim();
  if (!list.dataset.loaded) list.innerHTML = '<div class="dib-empty">Loading…</div>';
  let data = { items: [], unread: 0 };
  try {
    const res = await fetch(API_BASE + '/api/notifications?limit=200'
      + (q ? '&q=' + encodeURIComponent(q) : ''));
    if (res.ok) data = await res.json();
  } catch (e) { /* offline → keep whatever's shown */ }
  if (seq !== _inboxSeq) return;  // a newer render superseded this one
  list.dataset.loaded = '1';
  setInboxBadge(data.unread || 0);
  const items = data.items || [];
  list.classList.toggle('dib-grouped', _inboxView === 'sender');
  if (!items.length) {
    list.innerHTML = `<div class="dib-empty">${q ? 'No updates match &ldquo;' + esc(q) + '&rdquo;.' : 'No updates yet.'}</div>`;
    return;
  }
  let html = '';
  if (_inboxView === 'sender') {
    // Group by sender (project); groups ordered by most-recent activity, rows
    // within a group stay newest-first (items arrive newest-first).
    const groups = new Map();
    for (const it of items) {
      const k = it.project_name || 'Clayrune';
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(it);
    }
    const ordered = [...groups.entries()].sort((a, b) => (b[1][0].ts || 0) - (a[1][0].ts || 0));
    for (const [sender, rows] of ordered) {
      const unread = rows.filter(r => !r.read).length;
      html += `<div class="dib-group-head"><span class="dib-group-name">${esc(sender)}</span>`
        + `<span class="dib-group-meta">${rows.length}${unread ? ` · ${unread} new` : ''}</span></div>`;
      html += rows.map(_desktopInboxRowHTML).join('');
    }
  } else {
    // Conversation view: flat, day-grouped, newest-first.
    let cur = null;
    for (const it of items) {
      const label = _inboxDayLabel(it.ts);
      if (label !== cur) { cur = label; html += `<div class="dib-day">${esc(label)}</div>`; }
      html += _desktopInboxRowHTML(it);
    }
  }
  list.innerHTML = html;
  list.querySelectorAll('.dib-row').forEach(row => {
    row.addEventListener('click', () => openNotification(row.dataset.id,
      row.dataset.projectId, row.dataset.sessionId));
  });
  list.querySelectorAll('.dib-del').forEach(btn => {
    btn.addEventListener('click', e => { e.stopPropagation(); deleteNotification(btn.dataset.id); });
  });
}

function renderDesktopInbox() { return _renderDesktopInboxList(document.getElementById('desktop-inbox-list')); }
// Re-render whichever inbox is currently on screen (desktop modal wins if open).
function _renderActiveInbox() {
  return document.getElementById('desktop-inbox-list') ? renderDesktopInbox() : renderInbox();
}

function onInboxSearchInput(v) {
  _inboxQuery = v;
  clearTimeout(_inboxTimer);
  _inboxTimer = setTimeout(_renderActiveInbox, 250);
}

function openNotification(id, projectId, sessionId) {
  // Mark read (best-effort) then open the chat the notification points to.
  fetch(API_BASE + '/api/notifications/read', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids: [id] }),
  }).catch(() => {});
  if (!projectId) { setTimeout(updateInboxBadge, 300); return; }
  // Desktop Inbox is a modal — close it, then open the project (no mobile
  // back-stack dance).
  if (typeof openModals !== 'undefined' && openModals.has && openModals.has('__inbox')) {
    closeModalById('__inbox');
    if (sessionId && typeof openProjectAtSession === 'function') openProjectAtSession(projectId, sessionId);
    else if (typeof openProjectModal === 'function') openProjectModal(projectId);
    setTimeout(updateInboxBadge, 300);
    return;
  }
  // Keep the inbox on the back-stack: hide its UI but KEEP _mcInboxOpen + its
  // history sentinel, so hardware-back from the chat returns to the INBOX (not
  // the project's conversation list). Suppress the conv-list history level so
  // that's a single back — straight from the chat back to the inbox.
  _closeInboxUI();
  _mcConvFromInbox = true;
  _mcSuppressConvPush = true;
  var _clear = function () { _mcSuppressConvPush = false; };
  if (sessionId && typeof openProjectAtSession === 'function') {
    Promise.resolve(openProjectAtSession(projectId, sessionId)).finally(_clear);
  } else if (typeof openProjectModal === 'function') {
    try { openProjectModal(projectId); } finally { _clear(); }
  } else { _clear(); }
  setTimeout(updateInboxBadge, 300);
}

async function deleteNotification(id) {
  try { await fetch(API_BASE + '/api/notifications/' + encodeURIComponent(id), { method: 'DELETE' }); }
  catch (e) { /* keep the row on failure */ return; }
  _renderActiveInbox();
}

async function inboxMarkAllRead() {
  try {
    await fetch(API_BASE + '/api/notifications/read', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ all: true }),
    });
  } catch (e) { return; }
  _renderActiveInbox();
}

// Unread badges — mobile bottom-bar tab (#inbox-badge) + desktop sidebar
// (#sidebar-inbox-badge). Whichever exists gets updated.
function setInboxBadge(n) {
  const show = n > 0, txt = n > 99 ? '99+' : String(n);
  ['inbox-badge', 'sidebar-inbox-badge'].forEach(id => {
    const b = document.getElementById(id);
    if (!b) return;
    if (show) { b.textContent = txt; b.hidden = false; } else { b.hidden = true; }
  });
}

async function updateInboxBadge() {
  try {
    const r = await fetch(API_BASE + '/api/notifications?limit=1');
    if (r.ok) { const d = await r.json(); setInboxBadge(d.unread || 0); }
  } catch (e) { /* leave the badge as-is */ }
}
// Desktop Inbox — the notification timeline as a floating surface modal (same
// family as All Backlog / Skills / etc.). Reuses the shared timeline renderer.
function openInboxSurface() {
  if (typeof openModals === 'undefined') return;
  const modalId = '__inbox';
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized && typeof restoreModal === 'function') restoreModal(modalId);
    if (typeof focusModal === 'function') focusModal(modalId);
    renderDesktopInbox();
    return;
  }
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  if (typeof _clampModalSize === 'function') _clampModalSize(content, 640);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;gap:12px;padding:16px 20px 12px 24px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">Inbox</span>
      <div class="dib-viewtoggle" title="Arrange the inbox by">
        <button class="dib-view-btn${_inboxView === 'conversation' ? ' active' : ''}" data-view="conversation" onclick="setInboxView('conversation')">Conversation</button>
        <button class="dib-view-btn${_inboxView === 'sender' ? ' active' : ''}" data-view="sender" onclick="setInboxView('sender')">Sender</button>
      </div>
      <input type="text" id="desktop-inbox-search" placeholder="Search updates&hellip;" value="${esc(_inboxQuery || '')}" spellcheck="false"
        style="flex:1;min-width:120px;padding:7px 12px;font-size:13px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);outline:none"
        oninput="onInboxSearchInput(this.value)">
      <button onclick="inboxMarkAllRead()" title="Mark all read" style="background:none;border:none;color:var(--accent);cursor:pointer;font-size:14px;padding:4px 6px">&#10003;&#10003;</button>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div class="desktop-inbox-body"><div id="desktop-inbox-list"></div></div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  if (typeof centerModalElement === 'function') centerModalElement(win);
  if (typeof focusModal === 'function') focusModal(modalId);
  renderDesktopInbox();
}

function openInbox() {
  const el = document.getElementById('mobile-inbox');
  if (!el) return;
  renderInbox();
  el.classList.add('open');
  el.setAttribute('aria-hidden', 'false');
  if (!_mcInboxOpen) {
    try { history.pushState({ mc: 'inbox' }, ''); _mcInboxOpen = true; } catch (e) {}
  }
}
// DOM-only close — shared by the header button, row-tap, and the popstate
// handler (which has already consumed the sentinel, so it must NOT re-unwind).
function _closeInboxUI() {
  const el = document.getElementById('mobile-inbox');
  if (!el) return;
  el.classList.remove('open');
  el.setAttribute('aria-hidden', 'true');
}
function closeInbox() {
  if (_mcInboxOpen) { _mcInboxOpen = false; _mcUnwindHistory(1); }
  _closeInboxUI();
}
window.openInbox = openInbox;
window.openInboxSurface = openInboxSurface;
window.renderDesktopInbox = renderDesktopInbox;
window.closeInbox = closeInbox;
window.renderInbox = renderInbox;
window._closeInboxUI = _closeInboxUI;
window.onInboxSearchInput = onInboxSearchInput;
window.inboxMarkAllRead = inboxMarkAllRead;
window.updateInboxBadge = updateInboxBadge;
// Keep the Inbox tab badge fresh: on boot + a light poll (unread count only).
try { updateInboxBadge(); setInterval(updateInboxBadge, 30000); } catch (e) {}

// ── §1 Global "🔍 Search" — cross-project transcript search overlay ──────────
let _gsTimer = null, _gsSeq = 0;
function renderGlobalSearchResults(data) {
  const list = document.getElementById('global-search-list');
  if (!list) return;
  const q = (data && data.query) || '';
  const results = (data && data.results) || [];
  if (q.length < 2) { list.innerHTML = '<div class="mib-empty">Type to search across all chats.</div>'; return; }
  if (!results.length) { list.innerHTML = `<div class="mib-empty">No chats mention “${esc(q)}”.</div>`; return; }
  list.innerHTML = results.map(r => `
    <div class="mib-row" data-project-id="${esc(r.project_id)}" data-csid="${esc(r.csid)}">
      <div class="mib-main">
        <div class="mib-project">${esc(r.project)} · ${esc((r.label || '').slice(0, 60))}</div>
        <div class="mib-msg">${esc(r.snippet || '')}</div>
      </div>
      <span class="mib-chev">&#x203A;</span>
    </div>`).join('');
  list.querySelectorAll('.mib-row').forEach(row => {
    row.addEventListener('click', () => {
      const pid = row.dataset.projectId, csid = row.dataset.csid;
      closeGlobalSearch();
      // Deep-link: open the project and arm resume of that past chat (matches
      // the per-project search behavior; the thread preview + Continue follow).
      if (typeof openProjectModal === 'function') openProjectModal(pid);
      if (typeof selectResumeSession === 'function') setTimeout(() => selectResumeSession(pid, csid), 60);
    });
  });
}
function onGlobalSearchInput(value) {
  clearTimeout(_gsTimer);
  const q = (value || '').trim();
  if (q.length < 2) { renderGlobalSearchResults({ query: q, results: [] }); return; }
  const list = document.getElementById('global-search-list');
  if (list) list.innerHTML = '<div class="mib-empty">Searching…</div>';
  _gsTimer = setTimeout(() => runGlobalSearch(q), 350);
}
async function runGlobalSearch(q) {
  const seq = ++_gsSeq;
  let data = { query: q, results: [] };
  try {
    const res = await fetch(API_BASE + '/api/search/global?q=' + encodeURIComponent(q));
    if (res.ok) data = await res.json();
  } catch (e) { /* network/parse error → empty */ }
  if (seq !== _gsSeq) return;  // superseded by a newer query
  renderGlobalSearchResults(data);
}
function openGlobalSearch() {
  const el = document.getElementById('global-search');
  if (!el) return;
  renderGlobalSearchResults({ query: '', results: [] });
  el.classList.add('open');
  el.setAttribute('aria-hidden', 'false');
  if (!_mcGlobalSearchOpen) { try { history.pushState({ mc: 'gsearch' }, ''); _mcGlobalSearchOpen = true; } catch (e) {} }
  setTimeout(() => document.getElementById('global-search-input')?.focus(), 60);
}
function _closeGlobalSearchUI() {
  const el = document.getElementById('global-search');
  if (!el) return;
  el.classList.remove('open');
  el.setAttribute('aria-hidden', 'true');
}
function closeGlobalSearch() {
  if (_mcGlobalSearchOpen) { _mcGlobalSearchOpen = false; _mcUnwindHistory(1); }
  _closeGlobalSearchUI();
}
window.openGlobalSearch = openGlobalSearch;
window.closeGlobalSearch = closeGlobalSearch;
window.onGlobalSearchInput = onGlobalSearchInput;
window._closeGlobalSearchUI = _closeGlobalSearchUI;

// ── §1c Context-adaptive bottom nav bar ─────────────────────────────────────
// "Inside a project, the bar becomes the project" (mockup Turn 4). When a
// project modal is focused on mobile, the global bar swaps to project surfaces
// (Home / Chats / +New chat / Backlog / More); back to global otherwise.
// Self-healing: driven off live state every render tick with a change-guard, so
// no lifecycle path can leave a stale/orphaned bar and the 2s poll never
// clobbers the active :active state or an open ⋮ menu.
let _globalBarHTML = null;      // snapshot of the static global markup (captured once)
let _barContextPid = undefined; // current context key, to skip needless re-renders

function _focusedProjectModalId() {
  if (!isMobileChatList()) return null;
  if (typeof openModals === 'undefined' || !openModals.size) return null;
  let top = null, topZ = -1;
  openModals.forEach((entry, id) => {
    if (!id || id.startsWith('__')) return;         // skip special modals (settings/all-backlog/…)
    if (!entry || entry.minimized) return;
    const z = parseInt((entry.element && entry.element.style.zIndex) || entry.zIndex || 0, 10) || 0;
    if (z >= topZ) { topZ = z; top = entry.projectId || id; }
  });
  return top;
}

function _syncBottomBarContext() {
  const bar = document.getElementById('bottom-tab-bar');
  if (!bar) return;
  if (_globalBarHTML === null) _globalBarHTML = bar.innerHTML;  // capture static global markup once
  // Spec §1 (2026-07-06): the bottom bar exists ONLY on the Layer-1 Dashboard.
  // Inside a project — the conversation LIST (Layer 2) or a THREAD (Layer 3) —
  // there is NO bar; navigation is the header ‹ back + ⋮ project menu (iOS
  // push/pop; "the user chose no-bar"). So hide the bar whenever a project modal
  // is focused on mobile (and let the modal fill full height); restore the
  // global dashboard bar otherwise. (Supersedes the earlier context-adaptive
  // project bar.)
  const inProject = !!_focusedProjectModalId();
  const ctx = inProject ? '__hidden__' : '__global__';
  if (ctx === _barContextPid) return;   // unchanged → skip re-render
  _barContextPid = ctx;
  bar.classList.remove('project-context');
  bar.classList.toggle('mc-bar-hidden', inProject);
  document.body.classList.toggle('mc-modal-fullh', inProject);
  if (!inProject) bar.innerHTML = _globalBarHTML;
}
window._syncBottomBarContext = _syncBottomBarContext;

function mcPushModalHistory() {
  if (!isMobileChatList() || _mcModalHistoryActive) return;
  try { history.pushState({ mc: 'modal' }, ''); _mcModalHistoryActive = true; } catch (e) {}
}
function mcPushConvHistory() {
  if (!isMobileChatList() || _mcConvHistoryActive) return;
  // Opened from the inbox → the chat sits directly above the inbox on the stack
  // (no conv-list level) so hardware-back returns to the inbox in one step.
  if (_mcSuppressConvPush) { _mcSuppressConvPush = false; return; }
  try { history.pushState({ mc: 'conv' }, ''); _mcConvHistoryActive = true; } catch (e) {}
}
// Arming a resume preview inside the 5a compose is a sub-level of the compose
// screen → push so hardware-back returns to the picker, not out to the list.
function mcPushResumeHistory() {
  if (!isMobileChatList() || _mcResumeHistoryActive) return;
  try { history.pushState({ mc: 'resume' }, ''); _mcResumeHistoryActive = true; } catch (e) {}
}
window.mcPushResumeHistory = mcPushResumeHistory;
// A global surface modal (New-project form, or any __-modal opened NOT from the
// drawer where there's no drawer entry to relabel) pushes its own back entry;
// the popstate _mcSurfaceOpen branch closes it. Mobile-only.
function mcPushSurfaceHistory() {
  if (!isMobileChatList() || _mcSurfaceOpen) return;
  try { history.pushState({ mc: 'surface' }, ''); _mcSurfaceOpen = true; } catch (e) {}
}

// ── Mobile modal header: Minimize → Back (2026-09-24) ───────────────────────
// Ron's call: on mobile every modal's header button next to X does what the
// Android hardware back button does for that surface, not "minimize" (mobile
// has no way to reveal a minimized modal, so it just vanished). One shared
// implementation instead of ~20 hand-built headers: _mcApplyMobileModalHeader
// is invoked from interactions.js's existing #modal-layer MutationObserver
// (_mcInitModalResize), which already fires for every .modal-window mounted
// by any of the ~25 modules that build one, regardless of call site.
//
// mcModalHeaderBack walks the SAME sentinel stack the popstate handler reads
// (index.html ~1391), in the same priority order, so the on-screen button
// does exactly what a hardware back press would do right now: drill up one
// level (conv → list, settings detail → subs → list) or close, never more.
// history.back() lets popstate do that unwind; if no sentinel is live (the
// modal's opener never pushed one — a bug, or a modal opened by a path this
// doesn't cover yet) closeModalById is the safe fallback, same as the X.
function mcModalHeaderBack(modalId) {
  const hasSentinel = _mcDrawerHistoryActive || _mcSettingsNavDepth > 0
    || _mcSettingsHistoryActive || _mcSurfaceOpen || _mcResumeHistoryActive
    || _mcConvHistoryActive || _mcModalHistoryActive || _mcInboxOpen
    || _mcGlobalSearchOpen;
  if (hasSentinel) { history.back(); return; }
  if (typeof closeModalById === 'function') closeModalById(modalId);
}
window.mcModalHeaderBack = mcModalHeaderBack;

// Applied once per mounted .modal-window (see interactions.js _mcInitModalResize).
// Desktop is untouched: the existing Minimize button and its onclick stay as
// the template rendered them.
function _mcApplyMobileModalHeader(win) {
  if (!win || !(_isMobileDevice || window.innerWidth <= 960)) return;
  const modalId = win.dataset && win.dataset.modalId;
  if (!modalId) return;
  const btn = win.querySelector('.modal-minimize');
  if (btn && !btn.dataset.mcBackWired) {
    btn.dataset.mcBackWired = '1';
    btn.title = 'Back';
    // SVG, not the U+2190 glyph: a text arrow sits on the font baseline, so it
    // rendered visibly low in the 32px button next to a centred ✕ (Ron, phone
    // screenshot 2026-09-24). An SVG box is centred exactly by the flex rule.
    btn.setAttribute('aria-label', 'Back');
    btn.dataset.icon = 'back';
    btn.innerHTML = '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="display:block" aria-hidden="true"><path d="M15 9H3M8 4L3 9l5 5"/></svg>';
    btn.onclick = () => mcModalHeaderBack(modalId);
  }
  // Non-project (__) surfaces have no back-stack entry of their own unless
  // their opener explicitly pushes one (historically only Claydo + the
  // new-project form did) — push it here so EVERY __-surface participates,
  // both for hardware back and for the button above. __settings manages its
  // own dedicated push (mcPushSettingsHistory/mcPushSettingsNav) and must not
  // get a second, conflicting sentinel. Project modals push their own L1
  // entry from openProjectModal; idempotent guard inside mcPushSurfaceHistory
  // covers Claydo/new-project calling it a second time here.
  if (modalId.startsWith('__') && modalId !== '__settings') {
    mcPushSurfaceHistory();
  }
}
window._mcApplyMobileModalHeader = _mcApplyMobileModalHeader;
function mcPushDrawerHistory() {
  if (_mcDrawerHistoryActive) return;
  try { history.pushState({ mc: 'drawer' }, ''); _mcDrawerHistoryActive = true; } catch (e) {}
}
function _settingsWantsBackNav() { return _isMobileDevice || window.innerWidth <= 960; }
function mcPushSettingsHistory() {
  if (!_settingsWantsBackNav() || _mcSettingsHistoryActive) return;
  try { history.pushState({ mc: 'settings' }, ''); _mcSettingsHistoryActive = true; } catch (e) {}
}
function mcPushSettingsNav() {
  if (!_settingsWantsBackNav()) return;
  try { history.pushState({ mc: 'settings-nav' }, ''); _mcSettingsNavDepth++; } catch (e) {}
}

// ── Mobile navigation drawer (hamburger) ────────────────────────────────────
// Pure DOM toggle so closeMobileDrawer can be reused by the popstate handler
// without re-entering history.go (the popstate path has already consumed the
// sentinel). UI-initiated close synthetically unwinds via _mcUnwindHistory(1)
// to keep the back stack in sync, matching the modal/conv discipline.
function _closeMobileDrawerUI() {
  const d  = document.getElementById('mobile-drawer');
  const bd = document.getElementById('mobile-drawer-backdrop');
  if (d)  { d.classList.remove('open'); d.setAttribute('aria-hidden', 'true'); }
  if (bd) bd.classList.remove('open');
}
function openMobileDrawer() {
  const d  = document.getElementById('mobile-drawer');
  const bd = document.getElementById('mobile-drawer-backdrop');
  if (!d || !bd) return;
  // Mirror the current top-level surface as the drawer's active row (visual
  // continuity with the sidebar). dashboard is the default when no sidebar
  // item is marked active.
  const activeNav = document.querySelector('.sidebar-item.active')?.dataset.nav || 'dashboard';
  d.querySelectorAll('.mobile-drawer-item').forEach(el => {
    el.classList.toggle('active', el.dataset.nav === activeNav);
  });
  d.classList.add('open');
  d.setAttribute('aria-hidden', 'false');
  bd.classList.add('open');
  mcPushDrawerHistory();
}
function closeMobileDrawer() {
  if (_mcDrawerHistoryActive) {
    _mcDrawerHistoryActive = false;
    _mcUnwindHistory(1);
  }
  _closeMobileDrawerUI();
}
// Tap a drawer item → close drawer, then route. Incognito has its own opener;
// everything else goes through sidebarNav (which already knows how to close
// any open project modal on 'dashboard').
function mobileDrawerNav(target) {
  // Global surfaces (skills/mcp/backlog/scheduler/hivemind/shared-rules/processes)
  // open a non-project modal with no mobile back entry of its own. Reuse the
  // drawer's history entry AS the surface entry (relabel — no pop+push, so no
  // history race): one hardware-back then closes the surface → dashboard.
  const _isSurface = target !== 'dashboard' && target !== 'settings' && target !== 'incognito';
  if (_isSurface && _mcDrawerHistoryActive) {
    _mcDrawerHistoryActive = false;
    _mcSurfaceOpen = true;
    _closeMobileDrawerUI();
    sidebarNav(target);
    return;
  }
  // dashboard / settings / incognito manage their own history — close the drawer
  // normally (unwind its sentinel), then route.
  closeMobileDrawer();
  if (target === 'incognito') { openIncognito(); return; }
  sidebarNav(target);
}
// Synthetically unwind `n` MC sentinels (UI-initiated close/back) so a later
// hardware back isn't swallowed by a now-dead entry.
function _mcUnwindHistory(n) {
  if (n <= 0) return;
  _mcSuppressPop = true;
  try { history.go(-n); } catch (e) { _mcSuppressPop = false; }
}

// ── interop: window re-exposure for inline/generated/cross-module callers ──
window.renderMobileAppBar = renderMobileAppBar;
window.renderMobileFilterPills = renderMobileFilterPills;
window.isMobileChatList = isMobileChatList;
window.markProjectSeen = markProjectSeen;
window.unreadCount = unreadCount;
window.renderMobileChatList = renderMobileChatList;
window.mcPushModalHistory = mcPushModalHistory;
window.mcPushConvHistory = mcPushConvHistory;
window.mcPushSurfaceHistory = mcPushSurfaceHistory;
window.mcPushSettingsHistory = mcPushSettingsHistory;
window.mcPushSettingsNav = mcPushSettingsNav;
window._closeMobileDrawerUI = _closeMobileDrawerUI;
window.openMobileDrawer = openMobileDrawer;
window.closeMobileDrawer = closeMobileDrawer;
window.mobileDrawerNav = mobileDrawerNav;
window._mcUnwindHistory = _mcUnwindHistory;

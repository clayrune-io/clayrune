// Desk v1: the chrome around any chat thread, shared (Ron 2026-10-06: the Studio
// agent chat's text was too small to read and the panel too narrow for a long
// reply). Window-bridged module, no `import` (ground rule 1). One unit, one file
// (AGENT_RULES.md): it knows nothing about storyboards or agents, only about a
// `.desk-v1-chat` element, so the campaign chat can adopt it with two lines.
//
//   window.DeskV1ChatChrome.barHTML()           the A- / A+ / pop-out strip (paint it as
//                                               the first child of the chat element)
//   window.DeskV1ChatChrome.attach(chatEl)      wire that strip (safe to call again)
//   window.DeskV1ChatChrome.scale()             the saved text scale (1 = normal)
//
// TEXT SIZE is one setting for every chat, kept in localStorage and applied as the
// --desk-chat-scale custom property on :root; the chat's own CSS multiplies its font
// sizes by it. POP OUT does not copy or move anything: the SAME element, in the same
// place in the DOM, is lifted into the top layer (popover) and sized for reading, so
// the thread, the ask box and every listener on them are the live ones. Closing just
// drops the class (and any size the reader dragged it to).
//
// The large view is NOT modal (Ron 2026-11-15): no dimmed backdrop, no focus trap, and a
// click on the page behind it neither closes it nor is swallowed, so the Desk and the
// dashboard stay usable beside it. The slot the chat came from shows a darkened stand-in
// ("open in the large view", with a button to bring it back) so there is never a second
// copy of the thread to keep in sync. Esc closes it only while focus is inside it.
//
// The large view is a window like every other: attach() hangs the shared 8-handle resizer
// (makeResizable in interactions.js, .mc-resize-handle) on the chat element. The handles are
// only shown while popped out (desk-v1-chat-chrome.css), and a fresh pop-out starts at the
// default size.
(function () {
  const KEY = 'clayrune.desk.chatTextScale';
  const MIN = 0.85, MAX = 1.75, STEP = 0.15;
  let _open = null;   // { el, back, away } while a chat is popped out
  // What makeResizable / modalResizeStart write on the chat element: removed on open and close.
  const _SIZED = ['width', 'height', 'left', 'top', 'position', 'margin'];
  const _unsize = (el) => _SIZED.forEach((p) => el.style.removeProperty(p));

  const clamp = (v) => Math.min(MAX, Math.max(MIN, Math.round(v * 100) / 100));
  function scale() {
    let v = 1;
    try { v = parseFloat(localStorage.getItem(KEY)); } catch (_) { /* storage blocked: stay at normal */ }
    return Number.isFinite(v) ? clamp(v) : 1;
  }
  function _apply(v) {
    document.documentElement.style.setProperty('--desk-chat-scale', String(v));
    document.querySelectorAll('[data-chat-chrome]').forEach((bar) => _syncBar(bar, v));
  }
  function _syncBar(bar, v) {
    const pct = Math.round(v * 100);
    const less = bar.querySelector('[data-chat-text-less]');
    const more = bar.querySelector('[data-chat-text-more]');
    const read = bar.querySelector('[data-chat-text-read]');
    if (less) less.disabled = v <= MIN + 0.001;
    if (more) more.disabled = v >= MAX - 0.001;
    if (read) read.textContent = pct + '%';
  }
  function _set(v) {
    v = clamp(v);
    try { localStorage.setItem(KEY, String(v)); } catch (_) { /* storage blocked: applies for this page only */ }
    _apply(v);
  }

  function barHTML() {
    return `<div class="desk-v1-chat-chrome" data-chat-chrome role="toolbar" aria-label="Conversation view">
      <button type="button" class="desk-v1-chat-tool" data-chat-text-less aria-label="Smaller text" title="Smaller text">A&minus;</button>
      <span class="desk-v1-chat-tool-read" data-chat-text-read aria-live="polite"></span>
      <button type="button" class="desk-v1-chat-tool desk-v1-chat-tool-big" data-chat-text-more aria-label="Larger text" title="Larger text">A+</button>
      <button type="button" class="desk-v1-chat-tool desk-v1-chat-tool-pop" data-chat-pop aria-label="Open the conversation in a large view" title="Open in a large view">&#x2922;</button>
    </div>`;
  }

  // ── Pop out ────────────────────────────────────────────────────────────────
  function _pop(el, btn) {
    if (_open) return;
    const back = { opener: btn || document.activeElement };
    _unsize(el);
    el.classList.add('is-popped');
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-label', 'Conversation, large view');
    // a press anywhere in the large view (its drag strip, a turn) puts focus in it, so Esc reaches it
    if (!el.hasAttribute('tabindex')) { el.setAttribute('tabindex', '-1'); el.dataset.chatTabindex = '1'; }
    if (typeof el.showPopover === 'function') {
      el.setAttribute('popover', 'manual');
      try { el.showPopover(); } catch (_) { el.removeAttribute('popover'); }
    }
    const pop = el.querySelector('[data-chat-pop]');
    if (pop) { pop.innerHTML = '&times;'; pop.setAttribute('aria-label', 'Close the large view'); pop.title = 'Close (Esc)'; }
    _open = { el, back, away: _standIn(el) };
    document.addEventListener('keydown', _onKey, true);
    const thread = el.querySelector('[data-chat-thread]');
    if (thread) thread.scrollTop = thread.scrollHeight;
    const ask = el.querySelector('[data-sb-ask]');
    if (ask && !ask.disabled) ask.focus({ preventScroll: true }); else if (pop) pop.focus({ preventScroll: true });
  }
  function close() {
    if (!_open) return;
    const { el, back } = _open;
    window.DeskV1ChatMove.cancel(el);
    const away = _open.away;
    _open = null;
    document.removeEventListener('keydown', _onKey, true);
    if (away) {
      if (away.parentElement) away.parentElement.classList.remove('has-chat-away');
      away.remove();
    }
    _unsize(el);
    if (el.hasAttribute('popover')) { try { el.hidePopover(); } catch (_) { /* already hidden */ } el.removeAttribute('popover'); }
    el.classList.remove('is-popped');
    el.removeAttribute('role'); el.removeAttribute('aria-label');
    if (el.dataset.chatTabindex) { el.removeAttribute('tabindex'); delete el.dataset.chatTabindex; }
    const pop = el.querySelector('[data-chat-pop]');
    if (pop) { pop.innerHTML = '&#x2922;'; pop.setAttribute('aria-label', 'Open the conversation in a large view'); pop.title = 'Open in a large view'; }
    const to = pop && pop.isConnected ? pop : back.opener;
    if (to && to.isConnected) to.focus({ preventScroll: true });
  }
  // The darkened stand-in left in the chat's own slot while it is in the large view: the page around
  // the large view stays live, so the small slot must say where the conversation went.
  function _standIn(el) {
    const host = el.parentElement;
    if (!host) return null;
    const away = document.createElement('div');
    away.className = 'desk-v1-chat-away';
    away.setAttribute('data-chat-away', '');
    away.innerHTML = '<span class="desk-v1-chat-away-text">The conversation is open in the large view.</span>'
      + '<button type="button" class="desk-v1-chat-tool desk-v1-chat-away-back" data-chat-away-back>Bring it back here</button>';
    away.querySelector('[data-chat-away-back]').addEventListener('click', () => close());
    // a click anywhere else on the stand-in brings the large view forward (focus into its ask box)
    away.addEventListener('click', (e) => {
      if (e.target.closest('[data-chat-away-back]') || !_open) return;
      const ask = _open.el.querySelector('[data-sb-ask]');
      if (ask && !ask.disabled) ask.focus({ preventScroll: true });
    });
    host.insertBefore(away, el);
    host.classList.add('has-chat-away');
    return away;
  }
  function _onKey(e) {
    if (!_open) return;
    // Esc belongs to the large view while the reader is in it (or nowhere in particular);
    // with focus out on the page, the page keeps it
    const a = document.activeElement;
    if (e.key === 'Escape' && (!a || a === document.body || _open.el.contains(a))) { e.preventDefault(); e.stopPropagation(); close(); }
  }

  function attach(el) {
    if (!el) return;
    // The chat was repainted (a new element) while an old one was popped out: the old one is gone.
    if (_open && !_open.el.isConnected) close();
    const bar = el.querySelector('[data-chat-chrome]');
    if (!bar || bar.dataset.wired) return;
    bar.dataset.wired = '1';
    if (typeof window.makeResizable === 'function') window.makeResizable(el);
    window.DeskV1ChatMove.attach(el, bar);
    _apply(scale());
    bar.querySelector('[data-chat-text-less]').addEventListener('click', () => _set(scale() - STEP));
    bar.querySelector('[data-chat-text-more]').addEventListener('click', () => _set(scale() + STEP));
    const pop = bar.querySelector('[data-chat-pop]');
    pop.addEventListener('click', () => { if (_open && _open.el === el) close(); else _pop(el, pop); });
  }

  _apply(scale());
  window.DeskV1ChatChrome = { barHTML, attach, scale, close };
})();

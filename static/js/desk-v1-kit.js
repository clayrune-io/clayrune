// Desk v1 (MC-977) — T0b: the shared kit every later surface ticket builds
// on (docs/desk_v1_r0_plan.md; THE_DESK_V1_UI.md §9 vocabulary, §10 drag).
// Window-bridged module, no `import` (ground rule 1) — every export hangs
// off `window.DeskV1Kit`, replacing the T0a stub whole (that stub's own
// comment: "callers must not depend on this shape past T0a").
(function () {
  // ── Posy's face for posyBoxHTML() below (T8, MC-977 R0 exit cosmetic fix).
  // desk.js resolves this the same way for the LEGACY Queue thread
  // (_deskPosyAvatar, desk.js:116-119: /api/floor's bench, the global
  // 'social-media-strategist' entry's `avatar` field, e.g. "fig:courier") —
  // but that resolution runs from _loadDeskShell(), which openDesk() never
  // reaches once desk_v1 is on (it returns via deskV1Open() first). Without
  // a v1 copy of the same lookup, every posyBoxHTML() call fell through
  // avatarHTML()'s "absence" branch and drew the neutral dashed circle
  // instead of Posy's face. Fetched once, cached; failure leaves '' (same
  // neutral-dot fallback as before this fix, never a guessed face).
  let _posyAvatar = '';
  fetch('/api/floor').then((r) => r.json()).then((floor) => {
    const posy = ((floor && floor.bench) || []).find(
      (b) => (b.scope || 'global') === 'global' && b.name === 'social-media-strategist');
    _posyAvatar = posy ? (posy.avatar || '') : '';
    if (typeof window.avatarHTML !== 'function') return;
    document.querySelectorAll('.desk-v1-posy-box:not(.desk-v1-posy-box-compact) .desk-thread-head').forEach((head) => {
      if (head.firstElementChild) head.firstElementChild.outerHTML = window.avatarHTML(_posyAvatar, 24);
    });
  }).catch(() => {});

  // ── §9 vocabulary: 15 version states + 6 campaign states, glyph + word ───
  // (LIF-01/02/03). Never a kind-local string — every surface renders status
  // through stateLabel()/stateLabelHTML() below so A12/A15 are testable once,
  // here, instead of per screen. The two tables' keys don't collide (except
  // `archived`, which means the same thing and carries the same glyph in
  // both), so lookups merge into one table rather than branching on a kind
  // the caller would otherwise have to track.
  const VERSION_STATES = {
    planned:            { glyph: '◇', word: 'Planned' },             // ◇
    drafting:           { glyph: '✎', word: 'Drafting' },            // ✎
    needs_review:       { glyph: '✋', word: 'Needs review' },        // ✋
    blocked:            { glyph: '⛔', word: 'Blocked' },             // ⛔
    approved:           { glyph: '✓', word: 'Approved' },            // ✓
    scheduled:          { glyph: '✓', word: 'Scheduled' },           // ✓
    sending:            { glyph: '⟳', word: 'Sending' },             // ⟳
    submitted:          { glyph: '⟳', word: 'Submitted' },           // ⟳
    verified_published: { glyph: '✓', word: 'Verified published' },  // ✓
    you_reported:       { glyph: '✋', word: 'You reported' },        // ✋
    unknown_outcome:    { glyph: '?', word: 'Unknown outcome' },
    failed:             { glyph: '✕', word: 'Failed' },              // ✕
    held:               { glyph: '⚠', word: 'Held' },                // ⚠
    skipped:            { glyph: '·', word: 'Skipped' },             // ·
    archived:           { glyph: '·', word: 'Archived' },            // ·
  };
  const CAMPAIGN_STATES = {
    proposed:  { glyph: '◇', word: 'Proposed' },   // ◇
    active:    { glyph: '▶', word: 'Active' },     // ▶
    paused:    { glyph: '⏸', word: 'Paused' },      // ⏸
    completed: { glyph: '✓', word: 'Completed' },  // ✓
    archived:  { glyph: '·', word: 'Archived' },   // ·
    draft:     { glyph: '✎', word: 'Draft' },       // ✎ (manually started, incomplete)
  };
  const ALL_STATES = Object.assign({}, VERSION_STATES, CAMPAIGN_STATES);

  // ── Map stops (R2-3, IA revision 2 §3/§4.1) — the six-stop vocabulary the
  // campaign map stepper (desk-v1-campaign.js `deskV1FillCampaignTabStrip`)
  // and the project page's draft card label (desk-v1-project.js
  // `_draftCardLabel`) both read, so a stop never gets two names. Order
  // matters: it drives Next/Back and `validatePlan`'s `missing[].stop` links.
  const MAP_STOPS = ['goal', 'how', 'what', 'when', 'where', 'launch'];
  const MAP_STOP_WORDS = { goal: 'Goal', how: 'How', what: 'What', when: 'When', where: 'Where', launch: 'Launch' };

  function stateLabel(state) {
    return ALL_STATES[state] || { glyph: '?', word: state ? String(state) : 'Unknown' };
  }

  // glyph + word together, never color alone (A15: status readable in
  // greyscale). `data-state` lets a surface's own CSS add color as a second
  // signal on top, never a replacement for the glyph.
  function stateLabelHTML(state, opts) {
    opts = opts || {};
    const { glyph, word } = stateLabel(state);
    const cls = opts.className ? ' ' + opts.className : '';
    return `<span class="desk-v1-state-label${esc(cls)}" data-state="${esc(state || '')}">` +
      `<span class="desk-v1-state-glyph" aria-hidden="true">${esc(glyph)}</span>` +
      `<span class="desk-v1-state-word">${esc(word)}</span></span>`;
  }

  // Capability × permission (§9): combines what the connection CAN do
  // (channel.capability) with the standing per-campaign release position
  // (every piece is approved — IA2/THE_DESK_V1_IA_REVISION.md §3 row 7
  // retired the "approve themes, then run" review mode with no replacement).
  // `held` overrides either — a disconnected/rate-limited/expired channel
  // can't publish regardless of capability.
  function channelCapabilityCopy(channel) {
    if (!channel) return '';
    if (channel.health === 'held') return `⚠ Held — ${channel.holdReason || 'disconnected'}`;
    if (channel.capability === 'manual') return '✋ You publish it';
    return 'Publishes after approval';
  }

  // Money (§9): never "free" — usage stays behind the ℹ popover.
  function noChargeYetCopy(thing) { return `No ${thing} charge yet`; }

  // ── Copy lint (closes A12) — a static list of the §9 "never" rules, so any
  // surface can lint a string of copy it's about to render instead of a
  // reviewer having to catch it by eye per screen. ─────────────────────────
  const BANNED_PHRASES = [
    { re: /\bposts automatically\b/i, why: 'use the badge copy "Publishes automatically" (§9)' },
    { re: /\bfree\b/i, why: 'money is never "free" — say "No <X> charge yet" (§9)' },
    { re: /\breleased\b/i, why: '"Released" is not a version state — use the §9 word for it' },
    { re: /\bon pace\b/i, why: 'forecasts are estimates, never "on pace" (§7/A10)' },
  ];
  function lintCopy(text) {
    const s = String(text == null ? '' : text);
    const hits = [];
    for (const rule of BANNED_PHRASES) {
      const m = s.match(rule.re);
      if (m) hits.push({ phrase: m[0], why: rule.why });
    }
    return hits;
  }

  // ── Channel badge — identity first (UX-02: platform + account, never a
  // bare logo), matching frame 12a's CHANNELS row (identity-only pills; the
  // capability sentence lives in the Rules chips beside them, not repeated on
  // every badge). A glyph suffix appears only when it changes what the badge
  // means at a glance — held or manual — so the default (direct + review
  // mode's own copy) doesn't clutter every pill; the full sentence is always
  // available via `title` for a hover/screen-reader read. ──────────────────
  function channelBadge(channel, opts) {
    if (!channel) return '';
    opts = opts || {};
    const copy = channelCapabilityCopy(channel);
    const suffix = channel.health === 'held' ? ' ⚠' : (channel.capability === 'manual' ? ' ✋' : '');
    return `<span class="desk-v1-channel-badge" data-platform="${esc(channel.platform || '')}" ` +
      `data-capability="${esc(channel.capability || '')}" data-health="${esc(channel.health || 'ok')}" ` +
      `title="${esc(copy)}">${esc(channel.label || channel.identity || '')}${suffix}</span>`;
  }

  // ── Screen-reader announcer — one shared aria-live region, lazily created.
  // Used by the command bus below so every drop/undo is spoken, not just
  // shown (§10). ────────────────────────────────────────────────────────────
  function _announcer() {
    let el = document.getElementById('desk-v1-sr-announcer');
    if (!el) {
      el = document.createElement('div');
      el.id = 'desk-v1-sr-announcer';
      el.setAttribute('aria-live', 'polite');
      el.setAttribute('role', 'status');
      el.style.cssText = 'position:absolute;width:1px;height:1px;overflow:hidden;' +
        'clip:rect(0,0,0,0);white-space:nowrap;';
      document.body.appendChild(el);
    }
    return el;
  }
  function announce(text) {
    const el = _announcer();
    el.textContent = '';
    // Re-writing an unchanged aria-live node doesn't re-fire in most screen
    // readers — clear it, then write on the next frame so back-to-back
    // identical messages (e.g. two quick drops of the same kind) both speak.
    requestAnimationFrame(() => { el.textContent = text || ''; });
  }

  // ── Toast with Undo + client command bus (§10: every drop is a command
  // carrying its own inverse, an optimistic UI update, a toast offering
  // Undo, and an announcement). Reuses `showActionToast` (index.html) — the
  // same richer toast the update-available prompt already uses — rather
  // than forking a second toast implementation for this one extra button. ──
  function toast(message, opts) {
    opts = opts || {};
    announce(message);
    if (typeof opts.undo === 'function' && typeof window.showActionToast === 'function') {
      return window.showActionToast(esc(message), [
        { label: 'Undo', primary: true, onclick: opts.undo },
      ], { dismissOnAction: true, key: opts.key });
    }
    if (typeof window.showToast === 'function') window.showToast(message, opts.durationMs);
    return null;
  }

  const commandBus = {
    history: [],
    // cmd: { label, do, undo }. `do` runs immediately (the optimistic
    // update); Undo on the toast runs `undo` and nothing else. A command
    // with no inverse is not a command this bus accepts — that is the whole
    // point of the bus (§10's "every drop... a toast with Undo").
    run(cmd) {
      if (!cmd || typeof cmd.do !== 'function' || typeof cmd.undo !== 'function') {
        throw new Error('DeskV1Kit.commandBus.run: cmd needs both do() and undo()');
      }
      cmd.do();
      this.history.push(cmd);
      toast(cmd.label || 'Done', {
        undo: () => { cmd.undo(); this.history.pop(); },
      });
    },
  };

  // ── Add to… ▾ menu (UX-05): every shelf item needs BOTH a drag path and a
  // click/keyboard path — focus + Enter, or a visible trigger, opens this
  // menu (campaigns + "New campaign"); Esc closes it and returns focus to
  // the trigger. One menu open at a time, closed by an outside click. ──────
  let _openMenu = null;
  function _closeAddToMenu() {
    if (!_openMenu) return;
    const { el, returnFocus } = _openMenu;
    if (el.parentNode) el.parentNode.removeChild(el);
    _openMenu = null;
    if (returnFocus && typeof returnFocus.focus === 'function') returnFocus.focus();
  }
  document.addEventListener('click', (e) => {
    // e.target is whatever child node the browser hit-tested (a shelf item's
    // icon/badge span, a button's text), almost never the trigger element
    // itself — an exact `!==` here closed the menu on the SAME click that
    // opened it for any trigger with child markup. contains() matches what
    // the menu-body check on the left already does.
    if (_openMenu && !_openMenu.el.contains(e.target) && !_openMenu.trigger.contains(e.target)) _closeAddToMenu();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && _openMenu) { e.stopPropagation(); _closeAddToMenu(); }
  });

  // items: [{id, label}] (e.g. running campaigns). "+ New campaign" is
  // appended automatically — every Add to… menu offers it (UX-05).
  // opts.noAppendNew (T5): a plain choice menu (e.g. the Posy scope picker)
  // has no "+ New campaign" concept — opt out rather than filter it out
  // after the fact. Defaults to appending, so every existing 3-arg caller is
  // byte-identical.
  function addToMenu(triggerEl, items, onPick, opts) {
    if (!triggerEl) return;
    opts = opts || {};
    _closeAddToMenu();
    const menu = document.createElement('div');
    menu.className = 'desk-v1-addto-menu';
    menu.setAttribute('role', 'menu');
    const all = opts.noAppendNew ? (items || []) : (items || []).concat([{ id: '__new__', label: '+ New campaign' }]);
    all.forEach((item, i) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = item.label;
      b.setAttribute('role', 'menuitem');
      b.tabIndex = i === 0 ? 0 : -1;
      b.onclick = () => { _closeAddToMenu(); onPick(item.id); };
      menu.appendChild(b);
    });
    menu.addEventListener('keydown', (e) => {
      const btns = Array.from(menu.querySelectorAll('button'));
      const idx = btns.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); (btns[idx + 1] || btns[0]).focus(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); (btns[idx - 1] || btns[btns.length - 1]).focus(); }
      else if (e.key === 'Enter' && document.activeElement === menu) { btns[0] && btns[0].focus(); }
    });
    const host = triggerEl.closest('.desk-v1-addto-wrap') || triggerEl.parentElement || document.body;
    if (host.style && !host.style.position) host.style.position = 'relative';
    host.appendChild(menu);
    _openMenu = { el: menu, trigger: triggerEl, returnFocus: triggerEl };
    const first = menu.querySelector('button');
    if (first) first.focus();
  }

  // Wires a trigger element (a shelf item's ＋, or the item itself) to open
  // addToMenu() on click OR on Enter/Space while focused — the keyboard path
  // UX-05 requires alongside drag.
  //
  // opts (T2a): forwarded to addToMenu() as-is — e.g. `{noAppendNew: true}`
  // for a caller whose own item list already covers every destination (the
  // campaign page's Add tray offers "attach to this piece" per family, not
  // "+ New campaign"). Undefined for every existing 3-arg caller, so
  // addToMenu sees exactly what it always has.
  function bindAddToTrigger(triggerEl, getItems, onPick, opts) {
    if (!triggerEl) return;
    triggerEl.setAttribute('aria-haspopup', 'menu');
    const open = (e) => { e.preventDefault(); addToMenu(triggerEl, getItems(), onPick, opts); };
    triggerEl.addEventListener('click', open);
    triggerEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') open(e);
    });
  }

  // ── ⓘ popover (§9: "Explanations go behind ⓘ. Keep only what the current
  // decision needs on screen.") — one open at a time, closed by Esc or an
  // outside click, same convention as the Add to… menu above. ─────────────
  let _openPopover = null;
  function _closePopover() {
    if (_openPopover && _openPopover.el.parentNode) _openPopover.el.parentNode.removeChild(_openPopover.el);
    if (_openPopover) delete _openPopover.btn.dataset.infoOpen;
    _openPopover = null;
  }
  document.addEventListener('click', (e) => {
    if (_openPopover && !_openPopover.el.contains(e.target) && !e.target.closest('.desk-v1-info-btn')) _closePopover();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && _openPopover) _closePopover(); });

  function infoIconHTML(id) {
    return `<button type="button" class="desk-v1-info-btn" data-info-id="${esc(id)}" aria-label="More detail">&#9432;</button>`;
  }
  // Call once per rendered container after its HTML (including infoIconHTML
  // buttons) is in the DOM, with a { id: explanationText } map.
  function bindInfoIcons(containerEl, texts) {
    if (!containerEl) return;
    containerEl.querySelectorAll('.desk-v1-info-btn').forEach((btn) => {
      btn.onclick = (e) => {
        e.stopPropagation();
        const wasOpen = btn.dataset.infoOpen === '1';
        _closePopover();
        if (wasOpen) return;
        const pop = document.createElement('div');
        pop.className = 'desk-v1-info-popover';
        pop.setAttribute('role', 'tooltip');
        pop.textContent = (texts && texts[btn.dataset.infoId]) || '';
        const host = btn.parentElement || containerEl;
        if (host.style && !host.style.position) host.style.position = 'relative';
        host.appendChild(pop);
        btn.dataset.infoOpen = '1';
        _openPopover = { el: pop, btn };
      };
    });
  }

  // ── Confirm sheet (item 3, MC-977 R0 UX pass) — generic version of the
  // overlay/scrim/sheet T2b's Start sheet and widening-confirm already built
  // (desk-v1-rules.js's deskV1OpenStartSheet/_openWideningConfirm), pulled
  // here so a caller outside that file (the campaign/Home delete flow) can
  // reuse the SAME in-page sheet instead of window.confirm() — d146df0's own
  // rule (native confirm blocks the render thread and can't be styled)
  // applies just as much to delete as it did to widening authority. Reuses
  // the exact class names desk-v1.css already defines for
  // .desk-v1-rules-overlay/-scrim/-sheet/... so no new CSS is needed and the
  // two call sites render pixel-identical. Appended to document.body, not
  // `.desk-v1-shell` — same stacking-context reason d146df0 fixed: the shell
  // lives inside the Desk's own z-indexed `.modal-window`, so nothing inside
  // it can out-rank an overlay appended straight to body.
  function openConfirmSheet(opts) {
    opts = opts || {};
    const wrap = document.createElement('div');
    wrap.className = 'desk-v1-rules-confirm-overlay';
    wrap.innerHTML = `
      <div class="desk-v1-rules-scrim" data-confirm-scrim></div>
      <div class="desk-v1-rules-sheet" role="dialog" aria-modal="true" aria-label="${esc(opts.title || '')}">
        <div class="desk-v1-rules-sheet-title">${esc(opts.title || '')}</div>
        <div class="desk-v1-rules-sheet-body"><p class="desk-v1-rules-confirmtext">${esc(opts.body || '')}</p></div>
        ${opts.note ? `<div class="desk-v1-rules-sheet-note">${esc(opts.note)}</div>` : ''}
        <div class="desk-v1-rules-sheet-actions">
          <button type="button" class="desk-v1-rules-sheet-cancel" data-confirm-decline>${esc(opts.cancelLabel || 'Cancel')}</button>
          <button type="button" class="desk-v1-rules-sheet-confirm" data-confirm-accept>${esc(opts.confirmLabel || 'Confirm')}</button>
        </div>
      </div>`;
    document.body.appendChild(wrap);
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); decline(); } };
    function cleanup() { wrap.remove(); document.removeEventListener('keydown', onKey, true); }
    function decline() { cleanup(); if (opts.onDecline) opts.onDecline(); }
    function accept() { cleanup(); if (opts.onConfirm) opts.onConfirm(); }
    document.addEventListener('keydown', onKey, true);
    wrap.querySelector('[data-confirm-scrim]').onclick = decline;
    wrap.querySelector('[data-confirm-decline]').onclick = decline;
    wrap.querySelector('[data-confirm-accept]').onclick = accept;
    return { close: decline };
  }

  // ── Posy box (scope dropdown, suggestion, 1–3 chips, input) — built from
  // the existing chat classes verbatim (`.agent-output`, `.agent-question-
  // chip`, `.agent-input-row`, `.typing-indicator`; source doc for the
  // component set: docs/CONVERSATION_REDESIGN_ACTION_PLAN.md, NOT the UI
  // doc's `CLAYRUNE_CONVERSATION_REDESIGN.md` reference), same reuse desk.js
  // already established for the Queue's push-back thread. This renders
  // markup only — a surface ticket supplies the real scope/suggestion/chips
  // and wires `onSend`; the kit doesn't invent a suggestion source. ────────
  //
  // opts.compact / opts.sendStyle are opt-in (frame 12b): compact drops the
  // avatar+"Posy" name row so the scope chip is the box's only top-row
  // content; sendStyle:'arrow' swaps the text Send button for a round icon
  // one. Both default OFF, producing byte-identical markup to before either
  // option existed — callers that don't pass them are unaffected.
  function posyBoxHTML(opts) {
    opts = opts || {};
    const inputId = opts.inputId || ('desk-v1-posy-input-' + Math.random().toString(36).slice(2));
    const compact = !!opts.compact;
    const avatar = (!compact && typeof window.avatarHTML === 'function') ? window.avatarHTML(opts.avatar || _posyAvatar, 24) : '';
    const nameHTML = compact ? '' : '<span class="desk-thread-name">Posy</span>';
    const scope = opts.scopeLabel
      ? `<button type="button" class="desk-v1-posy-scope" data-scope-trigger="1">About: ${esc(opts.scopeLabel)} &#9662;</button>`
      : '';
    const suggestionHTML = opts.suggestion
      ? `<div class="agent-line">${esc(opts.suggestion)}</div>`
      : '';
    const chips = (opts.chips || []).slice(0, 3);
    const chipsHTML = chips.length
      ? `<div class="desk-v1-posy-chips agent-question-chips">${
          chips.map(c => `<button type="button" class="agent-question-chip" data-chip="${esc(c)}">${esc(c)}</button>`).join('')
        }</div>`
      : '';
    const sendBtnHTML = opts.sendStyle === 'arrow'
      ? `<button type="button" class="desk-v1-posy-send-arrow" data-posy-send="${esc(inputId)}" aria-label="Send">&#10148;</button>`
      : `<button class="btn-dispatch" data-posy-send="${esc(inputId)}">Send</button>`;
    return `
      <div class="desk-v1-posy-box${compact ? ' desk-v1-posy-box-compact' : ''}">
        <div class="desk-thread-head">${avatar}${nameHTML}${scope}</div>
        <div class="agent-output desk-v1-posy-output">${suggestionHTML}</div>
        ${chipsHTML}
        <div class="agent-input-row">
          <textarea class="agent-task-input" id="${esc(inputId)}" rows="1" placeholder="Tell Posy what to change…"></textarea>
          ${sendBtnHTML}
        </div>
      </div>`;
  }

  // ── Draft + Posy task lifecycle store (item 5 + T3, MC-977 R0 UX pass).
  // Text typed into a Posy box but not yet sent used to live only in the DOM
  // node's `.value` — every caller rebuilds that DOM wholesale (a commandBus
  // re-render, a tab-strip navigation to Conversations/Results and back, a
  // Home round-trip, a different card selected in the campaign rail), so the
  // draft vanished on all of those, not just a real navigation-away. A plain
  // Map keyed by the caller's own `draftKey` survives every one of those
  // rebuilds because it lives here in the module closure, not in the element
  // bindPosyBox mounted last time. Callers that pass no draftKey get the
  // exact old behaviour — no restore, no persistence, no durable task.
  //
  // T3 extends the same Map's entries (never a second store) from a bare
  // draft string to `{draftText, asks:[{id, clientReqId, state, stage,
  // startedAt, request, result, error}]}` — one array slot per Send, but
  // only the LAST one is ever active (§5: "Posy is still on the last one",
  // enforced in send() below). `_posyRenderers` is a separate, non-persisted
  // Map (key -> the currently mounted box's {containerEl, ta, key, onSend})
  // so a running ask can keep repainting whichever box is mounted RIGHT NOW
  // for that key, and mounting a fresh box (tab switch, navigate back) picks
  // up an in-flight ask instead of showing blank.
  const _posyDrafts = new Map();
  const _posyRenderers = new Map();
  const _POSY_TERMINAL = ['ready', 'failed', 'cancelled', 'timed_out'];
  function _isPosyTerminal(state) { return _POSY_TERMINAL.indexOf(state) !== -1; }
  function _entryFor(key) {
    if (!key) return { draftText: '', asks: [] };
    let e = _posyDrafts.get(key);
    if (!e) { e = { draftText: '', asks: [] }; _posyDrafts.set(key, e); }
    return e;
  }
  function _fmtElapsed(ms) {
    const s = Math.max(0, Math.floor(ms / 1000));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  }

  // Renders the current ask's state into `ctx.containerEl`'s
  // `.desk-v1-posy-output` per §5's exact copy. `ready` is deliberately left
  // untouched here: the word "applied" and the before/after diff belong to
  // the caller's own apply logic (deskV1HandlePosyInstruction and friends),
  // invoked via `ctx.onSend` below — kit renders every OTHER state so those
  // callers don't each reimplement Sending/Working/Failed/the question card.
  function _paintPosyOutput(ctx, ask) {
    const output = ctx && ctx.containerEl && ctx.containerEl.querySelector('.desk-v1-posy-output');
    if (!output || !ask) return;
    const bubble = `<div class="desk-v1-posy-sent-bubble">${esc(ask.request)}</div>`;
    if (ask.state === 'sending') {
      output.innerHTML = `${bubble}<div class="desk-v1-posy-status" aria-live="polite">Sending&hellip;</div>`;
    } else if (ask.state === 'accepted') {
      output.innerHTML = `${bubble}<div class="desk-v1-posy-status" aria-live="polite">Posy has it</div>`;
    } else if (ask.state === 'working') {
      const elapsed = Date.now() - ask.startedAt;
      const indicator = typeof window.actIndicatorHTML === 'function' ? window.actIndicatorHTML('tool') : '';
      let extra = '';
      if (elapsed >= 30000) {
        extra = `<div class="desk-v1-posy-status" aria-live="polite">Taking longer than usual. You can leave; the answer will wait here.</div>
          <button type="button" class="btn-secondary desk-v1-posy-cancel" data-posy-cancel="1">Cancel</button>`;
      } else if (elapsed >= 10000) {
        extra = `<div class="desk-v1-posy-status" aria-live="polite">Still working &middot; ${_fmtElapsed(elapsed)}</div>`;
      }
      output.innerHTML = `${bubble}
        <div class="agent-line typing-indicator" data-act="tool">${indicator}</div>
        <div class="desk-v1-posy-stage">${esc(ask.stage || '')}</div>${extra}`;
      const cancelBtn = output.querySelector('[data-posy-cancel]');
      if (cancelBtn) cancelBtn.onclick = () => _cancelPosyTask(ctx.key, ctx);
    } else if (ask.state === 'needs_answer') {
      const q = ask.question || {};
      const options = q.options || ['Yes', 'No'];
      output.innerHTML = `${bubble}
        <div class="desk-v1-posy-question">${esc(q.text || 'Needs your answer')}</div>
        <div class="desk-v1-posy-chips agent-question-chips">${
          options.map((o) => `<button type="button" class="agent-question-chip" data-posy-answer="${esc(o)}">${esc(o)}</button>`).join('')
        }</div>`;
      output.querySelectorAll('[data-posy-answer]').forEach((btn) => {
        btn.onclick = () => _answerPosyQuestion(ctx.key, ctx, btn.dataset.posyAnswer);
      });
    } else if (ask.state === 'failed' || ask.state === 'cancelled' || ask.state === 'timed_out') {
      const reason = ask.error || (ask.state === 'cancelled' ? 'cancelled' : 'unknown error');
      output.innerHTML = `
        <div class="desk-v1-posy-failed" aria-live="polite">&#9888; Posy couldn't finish: ${esc(reason)}. Nothing was changed.</div>
        <div class="desk-v1-posy-failed-actions">
          <button type="button" class="btn-secondary" data-posy-retry="1">Retry</button>
          <button type="button" class="btn-secondary" data-posy-edit="1">Edit request</button>
          ${ask.state === 'timed_out' ? '<button type="button" class="btn-secondary" data-posy-keepwaiting="1">Keep waiting</button>' : ''}
        </div>`;
      const retryBtn = output.querySelector('[data-posy-retry]');
      if (retryBtn) retryBtn.onclick = () => { _entryFor(ctx.key).asks.pop(); _startPosyTask(ctx.key, ask.request, ctx); };
      const editBtn = output.querySelector('[data-posy-edit]');
      if (editBtn) editBtn.onclick = () => {
        _entryFor(ctx.key).asks.pop();
        _entryFor(ctx.key).draftText = ask.request;
        if (ctx.ta) { ctx.ta.value = ask.request; ctx.ta.focus(); }
        output.innerHTML = '';
      };
      const kwBtn = output.querySelector('[data-posy-keepwaiting]');
      if (kwBtn) kwBtn.onclick = () => _keepWaitingPosyTask(ctx.key, ctx, ask);
    }
    // 'ready': nothing painted here — see comment above the function.
  }

  // §5 Ready row's no-diff fallback ("Posy answered; nothing changed.") for
  // callers whose own apply logic (unlike rules.js's deskV1HandlePosyInstruction,
  // which paints a real before/after) has nothing to show — without this the
  // box is left stuck on its last "Working…" frame forever, since kit never
  // paints Ready on its own (see _paintPosyOutput above).
  function paintPosyReadyNoDiff(boxEl) {
    const output = boxEl && boxEl.querySelector('.desk-v1-posy-output');
    if (output) output.innerHTML = '<div class="desk-v1-posy-status" aria-live="polite">Posy answered; nothing changed.</div>';
  }

  function _clearPosyTimers(ask) {
    if (ask._tick) clearInterval(ask._tick);
    if (ask._resolve) clearTimeout(ask._resolve);
    ask._tick = null; ask._resolve = null;
  }

  function _resolvePosyTask(key, ask, state, patch) {
    if (ask.state === state) return;
    _clearPosyTimers(ask);
    ask.state = state;
    Object.assign(ask, patch || {});
    const ctx = key && _posyRenderers.get(key);
    if (state === 'ready' && ctx && typeof ctx.onSend === 'function') {
      ctx.onSend(ask.request, ask);
      return; // Ready rendering is the caller's own apply logic, not kit's.
    }
    if (ctx) _paintPosyOutput(ctx, ask);
  }

  function _runWorkingPhase(key, ask, latencyMs, onDone) {
    ask.state = 'working';
    ask.stage = ask.stage || 'Working on it';
    const ctx = key && _posyRenderers.get(key);
    if (ctx) _paintPosyOutput(ctx, ask);
    ask._tick = setInterval(() => {
      const c = key && _posyRenderers.get(key);
      if (ask.state === 'working' && c) _paintPosyOutput(c, ask);
    }, 1000);
    ask._resolve = setTimeout(onDone, latencyMs);
  }

  // §5 R0 simulation: no network, same state names R1's real `/ask` poll
  // will use. `window.__deskV1PosyForce` ('fail'|'timeout'|'slow'|'question')
  // is the only test seam — 'slow' runs the real 35s scripted clock the doc
  // calls out (a real setTimeout; the smoke fast-forwards Playwright's clock
  // instead of sleeping 35s of wall time).
  function _startPosyTask(key, text, ctx) {
    const entry = _entryFor(key);
    const cur = entry.asks[entry.asks.length - 1];
    if (cur && !_isPosyTerminal(cur.state)) return { blocked: true, ask: cur };
    const ask = {
      id: 'ask-' + Math.random().toString(36).slice(2),
      clientReqId: 'creq-' + Date.now().toString(36) + Math.random().toString(36).slice(2),
      state: 'sending', stage: '', startedAt: Date.now(), request: text, result: null, error: null, question: null,
    };
    entry.asks.push(ask);
    if (key) _posyRenderers.set(key, ctx);
    _paintPosyOutput(ctx, ask); // Sending renders synchronously, well under 100ms.
    ask._resolve = setTimeout(() => {
      if (ask.state !== 'sending') return;
      ask.state = 'accepted';
      if (ctx && ctx.ta) ctx.ta.value = ''; // draft cleared NOW, not at Send.
      entry.draftText = '';
      _paintPosyOutput(ctx, ask);
      const force = window.__deskV1PosyForce;
      if (force === 'slow') {
        _runWorkingPhase(key, ask, 35000, () => _resolvePosyTask(key, ask, 'ready', { result: { affected: [] } }));
        return;
      }
      _runWorkingPhase(key, ask, 1500 + Math.random() * 2500, () => {
        if (force === 'fail') _resolvePosyTask(key, ask, 'failed', { error: 'Simulated failure (R0 test hook)' });
        else if (force === 'timeout') _resolvePosyTask(key, ask, 'timed_out', { error: 'timed out' });
        else if (force === 'question') _resolvePosyTask(key, ask, 'needs_answer', { question: { text: 'This would widen what Posy can do — go ahead?', options: ['Yes', 'No'] } });
        else _resolvePosyTask(key, ask, 'ready', { result: { affected: [] } });
      });
    }, 120);
    return { blocked: false, ask };
  }

  function _answerPosyQuestion(key, ctx, answer) {
    const entry = _entryFor(key);
    const ask = entry.asks[entry.asks.length - 1];
    if (!ask || ask.state !== 'needs_answer') return;
    ask.answer = answer;
    _runWorkingPhase(key, ask, 800 + Math.random() * 700, () => _resolvePosyTask(key, ask, 'ready', { result: { affected: [] } }));
  }

  function _keepWaitingPosyTask(key, ctx, ask) {
    ask.startedAt = Date.now();
    _runWorkingPhase(key, ask, 1500 + Math.random() * 2500, () => _resolvePosyTask(key, ask, 'ready', { result: { affected: [] } }));
  }

  function _cancelPosyTask(key, ctx) {
    const entry = _entryFor(key);
    const ask = entry.asks[entry.asks.length - 1];
    if (!ask) return;
    _resolvePosyTask(key, ask, 'cancelled', { error: 'cancelled' });
  }

  // True while any stored ask under a key starting with `prefix` (or any key
  // at all, with no prefix) is not yet settled — the query a Home card (T7)
  // uses for "⟳ Posy working"; kept as a query rather than a push model so
  // T7 can call it from its own render pass without kit knowing Home exists.
  function anyPosyWorking(prefix) {
    for (const [k, entry] of _posyDrafts) {
      if (prefix && k.indexOf(prefix) !== 0) continue;
      const cur = entry.asks[entry.asks.length - 1];
      if (cur && !_isPosyTerminal(cur.state)) return true;
    }
    return false;
  }
  const POSY_WORKING_LABEL = '⟳ Posy working';

  // Wires a mounted posyBoxHTML() instance: chips FILL the input (never
  // auto-send — same fixed-set convention as the Queue thread's quick
  // replies), Send/Enter calls onSend(text) and clears the box.
  //
  // opts.onScopeClick (T5): the `data-scope-trigger` button posyBoxHTML()
  // already renders (whenever `scopeLabel` is set) has never been wired to
  // anything — T3's static "About: This article" never needed a dropdown.
  // T5's scope genuinely changes (Scene N / Whole video / a version), so it
  // needs a click hook; passing nothing leaves the button exactly as inert
  // as it's always been (existing 3-arg callers are unaffected).
  //
  // opts.draftKey: a string identifying WHICH conversation this box is (e.g.
  // "campaign:camp-1:card:My piece", "review:v-42", "video:fam-9") — stable
  // across re-renders of the same logical box, distinct across different
  // ones (so selecting a different card doesn't leak its neighbour's draft).
  //
  // opts.taskLifecycle (T3, opt-in, additive, default OFF — same convention
  // as compact/sendStyle above): when true, Send starts the §5 Posy task
  // lifecycle (Sending -> Accepted -> Working -> Ready/Needs your answer/
  // Failed) instead of calling onSend(text) synchronously. `onSend` then
  // fires once, when the simulated task reaches Ready — never on Send —
  // so a caller's own apply logic (before/after, "applied", Undo) can never
  // land before Posy has actually "finished". The draftKey also keys the ask
  // store, so remounting the SAME box (tab switch, navigate back) resumes
  // whatever ask was in flight instead of showing blank. Every T3 caller
  // (desk-v1-campaign.js/-review.js/-video.js) now passes this; a caller
  // that doesn't (there are none left) stays byte-identical to before T3:
  // instant onSend, no ask, no lifecycle, no footer.
  function bindPosyBox(containerEl, inputId, onSend, opts) {
    if (!containerEl) return;
    opts = opts || {};
    const key = opts.draftKey || null;
    const simulate = !!opts.taskLifecycle;
    containerEl.querySelectorAll('.desk-v1-posy-chips .agent-question-chip').forEach((btn) => {
      btn.onclick = () => {
        const ta = document.getElementById(inputId);
        if (ta) { ta.value = btn.dataset.chip || ''; ta.focus(); }
      };
    });
    // §5: "R0 is simulated and says so" — a durable footer, not tied to any
    // one ask's render, since R0 has no other way to say "this isn't real".
    if (simulate && !containerEl.querySelector('.desk-v1-posy-footer')) {
      const footer = document.createElement('div');
      footer.className = 'desk-v1-posy-footer';
      footer.textContent = 'Simulated reply (R0)';
      containerEl.appendChild(footer);
    }
    const ta = document.getElementById(inputId);
    const ctx = { containerEl, ta, key, onSend };
    if (ta && key) {
      const entry = _entryFor(key);
      if (entry.draftText) ta.value = entry.draftText;
      ta.addEventListener('input', () => { entry.draftText = ta.value || ''; });
    }
    if (simulate && key) {
      const entry = _entryFor(key);
      const cur = entry.asks[entry.asks.length - 1];
      if (cur && cur.state !== 'ready') {
        // A box remounted while its ask is still going (or sitting on a
        // question/failure the user hasn't acted on yet) repaints that state
        // immediately instead of showing blank (§5: "still Working").
        _posyRenderers.set(key, ctx);
        _paintPosyOutput(ctx, cur);
      }
    }
    const send = () => {
      const text = (ta && ta.value.trim()) || '';
      if (!text) return;
      if (!simulate) {
        if (ta) ta.value = '';
        if (key) _entryFor(key).draftText = '';
        onSend(text);
        return;
      }
      if (key) {
        const entry = _entryFor(key);
        const cur = entry.asks[entry.asks.length - 1];
        if (cur && !_isPosyTerminal(cur.state)) { toast('Posy is still on the last one.'); return; }
      }
      _startPosyTask(key, text, ctx);
    };
    const sendBtn = containerEl.querySelector(`[data-posy-send="${inputId}"]`);
    if (sendBtn) sendBtn.onclick = send;
    if (ta) ta.addEventListener('keydown', (e) => {
      if (typeof window.handleInputEnter === 'function') window.handleInputEnter(e, send, null);
    });
    const scopeBtn = containerEl.querySelector('[data-scope-trigger]');
    if (scopeBtn && typeof opts.onScopeClick === 'function') {
      scopeBtn.onclick = () => opts.onScopeClick(scopeBtn);
    }
  }

  // ── validatePresence (IA2, THE_DESK_V1_IA_REVISION.md §5) — the project's
  // own required bound: at least one workspace account bound to it. A
  // campaign can't set up until its project clears this (§2.3 step 0).
  function validatePresence(project) {
    const presence = (project && project.presence) || {};
    const missing = [];
    if (!(presence.accounts && presence.accounts.length)) missing.push({ bound: 'accounts', label: 'accounts' });
    return { ok: missing.length === 0, missing };
  }

  // Effective cadence (§5 IA2 acceptance: "campaign cadence 5 under project
  // ceiling 3 -> effective 3") — inherit the project's per-account ceiling
  // when the campaign hasn't set its own, clamp DOWN to it when the campaign
  // asks for more; never widen past what the project allows (§2.1: "Widening
  // a project field... never widens a running campaign" runs the other
  // direction, but a campaign may never exceed today's project ceiling
  // either — one direction of the same inherit+clamp rule).
  function _effectiveCadence(plan, project) {
    const campPerWeek = plan && plan.cadence ? plan.cadence.per_week : null;
    const ceilings = (project && project.presence && project.presence.ceilings) || {};
    const accounts = (plan && plan.accounts) || [];
    let projCeiling = null;
    accounts.forEach((chId) => {
      const c = ceilings[chId];
      if (c && c.per_week != null) projCeiling = projCeiling == null ? c.per_week : Math.min(projCeiling, c.per_week);
    });
    if (campPerWeek == null) return { value: projCeiling, fromProject: projCeiling != null };
    if (projCeiling != null && projCeiling < campPerWeek) return { value: projCeiling, fromProject: true };
    return { value: campPerWeek, fromProject: false };
  }

  // ── validatePlan (§4: "one canonical plan object"; rescoped IA2 §5, IA
  // revision 2 §5/§8 R2-1) — the single gate the checklist state, the Start
  // button, Resume and Renew all share. Checks the §2.3 step-2 bound table's
  // "Required to leave" rows — accounts, cadence, end date and/or post cap
  // (>=1). `source_projects` is retired (§3 row 14: owner is the parent
  // project, implicit, no bound) and `min_gap_h` moved to the project's own
  // ceilings (§3 row 22) — neither is a campaign-level plan bound any more.
  // `project` is optional so callers without a resolved project (e.g. a
  // just-created draft campaign) still get a usable result; effective
  // cadence then falls back to the campaign's own value with no clamp.
  //
  // Each bound now carries a `stop` (§2 map vocabulary: 'goal'|'how'|'what'|
  // 'when'|'where'|'launch') alongside the older numeric `step`, additive —
  // existing callers reading `.step`/`.bound` see no change; R2-3's map
  // stepper (not built by this ticket) will read `.stop` to link a missing
  // bound to the right ①-⑥ stop.
  const _PLAN_BOUNDS = [
    { bound: 'accounts', step: 2, stop: 'where', label: 'accounts',
      missing: (p) => !(p.accounts && p.accounts.length) },
    { bound: 'cadence', step: 2, stop: 'when', label: 'cadence',
      missing: (p) => !(p.cadence && p.cadence.per_week != null) },
    { bound: 'end', step: 2, stop: 'when', label: 'end date',
      missing: (p) => !(p.end && (p.end.date != null || p.end.post_cap != null)) },
  ];

  // ── R2-1 additions (IA revision 2 §5.1/§5.2, §9 Ron's binding answers) ──
  // These three checks are gated on the NEW shapes (`plan.goal.metric`,
  // `plan.term`, `plan.how.budget`) rather than added unconditionally to
  // _PLAN_BOUNDS above — no fixture campaign carries those fields yet (R2-1
  // is fixtures + kit shapes only; R2-3/R2-4/R2-6/R2-11 wire the map, goal
  // editor, how-budget UI and the Launch bounds table that actually set
  // them). Adding an unconditional goal/budget requirement to every
  // validatePlan() call would silently fail today's camp-1/camp-2 fixtures
  // (neither carries `source`/`metric`) and flip Resume/Pause outcomes that
  // have nothing to do with this ticket's scope — gating on the new field
  // names keeps this purely additive until a later ticket migrates a real
  // plan onto the new shape.
  //
  // §9 Q1 (binding): a goal needs a target AND a source — `manual` allowed.
  function _goalMissing(plan) {
    const goal = plan.goal;
    if (!goal || goal.metric === undefined) return null; // old-shape or absent — not this ticket's gate
    if (goal.target == null || !goal.source) return { bound: 'goal', stop: 'goal', label: 'goal' };
    return null;
  }
  // §9 Q2 (binding): terms run ≤90 days; a long-horizon goal renews rather
  // than holding one open-ended approval.
  const _MAX_TERM_DAYS = 90;
  function _termMissing(plan) {
    const term = plan.term;
    if (!term || !term.starts || !term.ends) return null; // no term set yet — not this ticket's gate
    const days = (new Date(term.ends).getTime() - new Date(term.starts).getTime()) / 86400000;
    if (days > _MAX_TERM_DAYS) return { bound: 'term', stop: 'launch', label: 'term', detail: `${Math.round(days)} days (max ${_MAX_TERM_DAYS})` };
    return null;
  }
  // §5.2/§9 Q3 (binding): a project-funded budget is earmarked at Launch;
  // Launch refuses an earmark the project cannot cover and says by how much.
  // `opts.projectRemaining` is the caller's job to compute (project budget
  // minus the sum of the OTHER live campaigns' earmarks) — this function
  // stays a pure comparison, no cross-campaign lookup here.
  function _howBudgetMissing(plan, opts) {
    const budget = plan.how && plan.how.budget;
    if (!budget || budget.source !== 'project' || opts.projectRemaining == null) return null;
    const short = (budget.amount || 0) - opts.projectRemaining;
    if (short > 0) return { bound: 'how_budget', stop: 'how', label: 'how', detail: `short by $${short}` };
    return null;
  }

  function validatePlan(plan, project, opts) {
    plan = plan || {};
    opts = opts || {};
    const missing = _PLAN_BOUNDS
      .filter((b) => b.missing(plan))
      .map((b) => ({ bound: b.bound, step: b.step, stop: b.stop, label: b.label }));
    [_goalMissing(plan), _termMissing(plan), _howBudgetMissing(plan, opts)]
      .filter(Boolean)
      .forEach((m) => missing.push(m));
    const eff = _effectiveCadence(plan, project);
    return {
      ok: missing.length === 0,
      missing,
      effective: { cadence_per_week: eff.value, cadence_from_project: eff.fromProject },
    };
  }

  // ── Bounds hash (SIMPLIFICATION §4 / IA revision 2 §5.1 `approval.
  // bounds_hash`, §5.2 "the ceiling that goes into bounds_hash is the number
  // at approval time... a later raise is a widening [needs a new hash]...
  // lowering keeps approval [same hash]"). `computeBoundsHash` is a pure
  // function of the canonical bounds object — deterministic, no crypto dep
  // (djb2 over a stable-sorted-keys JSON string is enough: this hash is an
  // equality fingerprint for "did the approved envelope change", never a
  // security boundary). `nextBoundsHash` is the actual widen/narrow policy:
  // call it every time a bound-affecting field is edited; it returns a NEW
  // hash only when the edit widens, otherwise the previous hash unchanged —
  // this is what "lowering does not change the bounds hash" means in
  // practice (the narrowed bounds are in effect, but the approval on file,
  // and its hash, is untouched).
  function _stableStringify(v) {
    if (v === null || typeof v !== 'object') return JSON.stringify(v);
    if (Array.isArray(v)) return '[' + v.map(_stableStringify).join(',') + ']';
    const keys = Object.keys(v).sort();
    return '{' + keys.map((k) => JSON.stringify(k) + ':' + _stableStringify(v[k])).join(',') + '}';
  }
  function computeBoundsHash(bounds) {
    const s = _stableStringify(bounds || {});
    let h = 5381;
    for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
    return h.toString(16).padStart(8, '0');
  }
  // Widening rule — §141's bounds table (accounts, cadence, end/cap,
  // budget, term). Dave's review (2026-09-29): the R2-1 version only
  // checked budget, so a cadence raise (R2-9), an account add (R2-10) or a
  // term extension (R2-11) would silently keep the old approval's hash —
  // the one direction this must never get wrong. Every dimension below is
  // additive-OR: ANY widening move on ANY dimension widens the whole
  // envelope; narrowing every dimension at once is the only way to keep
  // the old hash. Each dimension only fires when BOTH sides carry that
  // field — a bound neither side sets yet isn't this function's problem
  // (validatePlan gates presence, this only gates "did an existing bound
  // get looser").
  function _accountsWiden(prevBounds, nextBounds) {
    const idOf = (a) => (typeof a === 'string' ? a : a && a.channel_id);
    const prev = new Set(((prevBounds && prevBounds.accounts) || []).map(idOf));
    const next = ((nextBounds && nextBounds.accounts) || []).map(idOf);
    return next.some((id) => !prev.has(id));
  }
  function _cadenceWiden(prevBounds, nextBounds) {
    const prev = (prevBounds && prevBounds.cadence) || {};
    const next = (nextBounds && nextBounds.cadence) || {};
    if (prev.per_week == null) return false;
    // A ceiling removed is looser than any finite one.
    return next.per_week == null || next.per_week > prev.per_week;
  }
  // end.date later, end.post_cap raised, or a cap removed entirely
  // (post_cap: null after having one — an unbounded run is looser than any
  // finite cap) all widen.
  function _endWiden(prevBounds, nextBounds) {
    const prev = (prevBounds && prevBounds.end) || {};
    const next = (nextBounds && nextBounds.end) || {};
    if (prev.date) {
      // An end date removed is looser than any date.
      if (!next.date) return true;
      if (new Date(next.date).getTime() > new Date(prev.date).getTime()) return true;
    }
    if (prev.post_cap != null) {
      if (next.post_cap == null) return true;
      if (next.post_cap > prev.post_cap) return true;
    }
    return false;
  }
  function _termWiden(prevBounds, nextBounds) {
    const prev = (prevBounds && prevBounds.term) || {};
    const next = (nextBounds && nextBounds.term) || {};
    if (!prev.ends) return false;
    return !next.ends || new Date(next.ends).getTime() > new Date(prev.ends).getTime();
  }
  // §5.2: budget.amount raised, or budget.source switches 'own' -> 'project'
  // (a pool that can be larger than the fixed own amount), is a widening.
  // Equal or lower amount, or 'project' -> 'own', never widens.
  function _budgetWiden(prevBounds, nextBounds) {
    const prevBudget = (prevBounds && prevBounds.budget) || {};
    const nextBudget = (nextBounds && nextBounds.budget) || {};
    if ((nextBudget.amount || 0) > (prevBudget.amount || 0)) return true;
    if (prevBudget.source === 'own' && nextBudget.source === 'project') return true;
    return false;
  }
  function boundsWiden(prevBounds, nextBounds) {
    return _accountsWiden(prevBounds, nextBounds)
      || _cadenceWiden(prevBounds, nextBounds)
      || _endWiden(prevBounds, nextBounds)
      || _termWiden(prevBounds, nextBounds)
      || _budgetWiden(prevBounds, nextBounds);
  }
  function nextBoundsHash(prevHash, prevBounds, nextBounds) {
    return boundsWiden(prevBounds, nextBounds) ? computeBoundsHash(nextBounds) : prevHash;
  }

  window.DeskV1Kit = {
    VERSION_STATES, CAMPAIGN_STATES,
    MAP_STOPS, MAP_STOP_WORDS,
    channelCapabilityCopy, noChargeYetCopy,
    BANNED_PHRASES, lintCopy,
    stateLabel, stateLabelHTML,
    channelBadge,
    announce, toast, commandBus,
    addToMenu, bindAddToTrigger,
    infoIconHTML, bindInfoIcons,
    posyBoxHTML, bindPosyBox,
    anyPosyWorking, POSY_WORKING_LABEL, paintPosyReadyNoDiff,
    openConfirmSheet,
    validatePlan, validatePresence,
    computeBoundsHash, boundsWiden, nextBoundsHash,
  };
})();

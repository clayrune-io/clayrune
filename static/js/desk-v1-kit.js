// Desk v1 (MC-977) — T0b: the shared kit every later surface ticket builds
// on (docs/desk_v1_r0_plan.md; THE_DESK_V1_UI.md §9 vocabulary, §10 drag).
// Window-bridged module, no `import` (ground rule 1) — every export hangs
// off `window.DeskV1Kit`, replacing the T0a stub whole (that stub's own
// comment: "callers must not depend on this shape past T0a").
(function () {
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
  // (channel.capability) with what review mode currently ALLOWS (reviewMode).
  // `held` overrides either — a disconnected/rate-limited/expired channel
  // can't publish regardless of capability or rules.
  function channelCapabilityCopy(channel, reviewMode) {
    if (!channel) return '';
    if (channel.health === 'held') return `⚠ Held — ${channel.holdReason || 'disconnected'}`;
    if (channel.capability === 'manual') return '✋ You publish it';
    return reviewMode === 'themes' ? 'Publishes automatically' : 'Publishes after approval';
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
    const copy = channelCapabilityCopy(channel, opts.reviewMode);
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
    if (_openMenu && !_openMenu.el.contains(e.target) && e.target !== _openMenu.trigger) _closeAddToMenu();
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
  function bindAddToTrigger(triggerEl, getItems, onPick) {
    if (!triggerEl) return;
    triggerEl.setAttribute('aria-haspopup', 'menu');
    const open = (e) => { e.preventDefault(); addToMenu(triggerEl, getItems(), onPick); };
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
    const avatar = (!compact && typeof window.avatarHTML === 'function') ? window.avatarHTML(opts.avatar || 'posy', 24) : '';
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
  function bindPosyBox(containerEl, inputId, onSend, opts) {
    if (!containerEl) return;
    opts = opts || {};
    containerEl.querySelectorAll('.desk-v1-posy-chips .agent-question-chip').forEach((btn) => {
      btn.onclick = () => {
        const ta = document.getElementById(inputId);
        if (ta) { ta.value = btn.dataset.chip || ''; ta.focus(); }
      };
    });
    const ta = document.getElementById(inputId);
    const send = () => {
      const text = (ta && ta.value.trim()) || '';
      if (!text) return;
      if (ta) ta.value = '';
      onSend(text);
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

  window.DeskV1Kit = {
    VERSION_STATES, CAMPAIGN_STATES,
    channelCapabilityCopy, noChargeYetCopy,
    BANNED_PHRASES, lintCopy,
    stateLabel, stateLabelHTML,
    channelBadge,
    announce, toast, commandBus,
    addToMenu, bindAddToTrigger,
    infoIconHTML, bindInfoIcons,
    posyBoxHTML, bindPosyBox,
  };
})();

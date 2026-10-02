// Desk v1 (MC-977) — T0b: the shared kit every later surface ticket builds
// on (docs/desk_v1_r0_plan.md; THE_DESK_V1_UI.md §9 vocabulary, §10 drag).
// Window-bridged module, no `import` (ground rule 1) — every export hangs
// off `window.DeskV1Kit`, replacing the T0a stub whole (that stub's own
// comment: "callers must not depend on this shape past T0a").
(function () {
  // ── Agent of choice (R2-5, MC-977 IA revision 2 §5.3). The chat box below
  // used to hardcode "Posy" (`social-media-strategist`) for every project;
  // it now draws whoever the campaign picked (`how.agent`, R2-18) or, failing
  // that, the project's `presence.desk_agent` — resolved against the real roster via GET /api/characters. Fetched
  // ONCE and cached by "scope:name" ref (same one-shot-fetch-then-repaint
  // shape the old avatar lookup used, replaced here since it only ever
  // resolved Posy's face, never her name). A ref this fetch doesn't
  // recognise — never chosen, or a deleted character — resolves to `name:
  // null`; callers paint UNRESOLVED_AGENT_LABEL for that, never a guess or
  // the old hardcoded default.
  let _agentsByRef = null; // null = the one fetch below hasn't landed yet
  // R2-2 (Home block headers resolve an agent per PROJECT, outside any
  // `.desk-v1-posy-box` — `_repaintDeskAgentBoxes` below can't reach them).
  // A caller that can't wait synchronously registers here instead; each
  // queued callback fires exactly once, whether the fetch resolves or falls
  // back to `{}` (still "ready" — just nothing recognised).
  let _agentsReadyCallbacks = [];
  function onAgentsReady(cb) {
    if (_agentsByRef) { cb(); return; }
    _agentsReadyCallbacks.push(cb);
  }
  function _flushAgentsReady() {
    const cbs = _agentsReadyCallbacks;
    _agentsReadyCallbacks = [];
    cbs.forEach((cb) => cb());
  }
  // `projectId` also lists that project's own (project-scope) characters, so a
  // hired project-local agent resolves. Each call replaces the map.
  function _loadAgents(projectId) {
    return fetch('/api/characters' + (projectId ? '?project_id=' + encodeURIComponent(projectId) : '')).then((r) => r.json()).then((list) => {
      const map = {};
      (list || []).forEach((c) => { map[`${c.scope || 'global'}:${c.name}`] = c; });
      _agentsByRef = map;
      _repaintDeskAgentBoxes();
      _flushAgentsReady();
    }).catch(() => { if (!_agentsByRef) _agentsByRef = {}; _flushAgentsReady(); });
  }
  _loadAgents();

  const UNRESOLVED_AGENT_LABEL = 'Pick who plans for this project ›';
  // The agent is per CAMPAIGN (standing position, Ron 2026-09-30): the campaign
  // page's box says so. Project-level surfaces keep UNRESOLVED_AGENT_LABEL.
  const UNRESOLVED_CAMPAIGN_AGENT_LABEL = 'Pick who plans for this campaign ›';

  // opts: {project, campaign}. R2-18 (Ron 2026-09-30, reversing the same-day
  // "agents per project only" ruling): the agent belongs to the CAMPAIGN —
  // picked on its Brief stop into `how.agent` — so that wins; the project's
  // standing `presence.desk_agent` is the default when the campaign has none.
  function deskAgentRef(opts) {
    opts = opts || {};
    const presence = (opts.project && opts.project.presence) || {};
    return (opts.campaign && opts.campaign.how && opts.campaign.how.agent) || presence.desk_agent || null;
  }

  // The voice an account speaks in on THIS campaign. The campaign owns it
  // (`plan.voices[channelId]`, edited on its Where board); the account's own
  // default (`channel.voice`, a workspace-level asset) fills in until the
  // campaign sets one. Empty string when neither exists.
  function accountVoice(plan, channel) {
    if (!channel) return '';
    const own = plan && plan.voices && plan.voices[channel.id];
    return own || channel.voice || '';
  }

  // R2-18: the agents a campaign's Brief picker offers — those hired on the
  // project's floor (its live `roster` rows: `character` ref, not `removed_at`)
  // plus the project's own desk agent, so the default is always selectable.
  // A project record may carry its own `roster` array of refs (the Desk's
  // fixture projects are not Clayrune project ids, so the real roster can't
  // be looked up for them); otherwise GET /api/projects is read by id. Both
  // fetches re-run each call — a hire or a new character made since the last
  // Brief render must show. Resolves to [{ref, name, avatar}], only for refs
  // GET /api/characters recognises; never rejects.
  function projectAgentChoices(project) {
    if (!project) return Promise.resolve([]);
    const refsP = Array.isArray(project.roster)
      ? Promise.resolve(project.roster)
      : fetch('/api/projects').then((r) => r.json()).then((list) => {
        const rec = (Array.isArray(list) ? list : (list && list.projects) || []).find((x) => x.id === project.id);
        return ((rec && rec.roster) || []).filter((r) => !r.removed_at).map((r) => r.character);
      }).catch(() => []);
    return Promise.all([refsP, _loadAgents(project.id)]).then(([refs]) => {
      const def = (project.presence || {}).desk_agent;
      const all = (def ? [def] : []).concat(refs.filter((r) => r && r !== def));
      return all.map((ref) => ({ ref, rec: resolveDeskAgent(ref) }))
        .filter((x) => x.rec.name)
        .map((x) => ({ ref: x.ref, name: x.rec.name, avatar: x.rec.avatar }));
    });
  }

  // {ref, name, avatar} once the roster fetch has landed and recognises the
  // ref; {ref, name: null, avatar: ''} while the fetch is still in flight,
  // `ref` was never set, or nothing installed matches it.
  function resolveDeskAgent(ref) {
    if (!ref || !_agentsByRef) return { ref: ref || null, name: null, avatar: '' };
    const rec = _agentsByRef[ref];
    if (!rec) return { ref, name: null, avatar: '' };
    return { ref, name: rec.agent_name || rec.display_name || rec.name, avatar: rec.avatar || '' };
  }

  // Convenience for the many sentence-embedded copy spots ("Tell X what to
  // change…", "X has it") that need a plain string, never the header's
  // pick-agent prompt — `fallback` defaults to a lowercase generic term that
  // reads fine mid-sentence regardless of where it lands.
  function deskAgentName(opts, fallback) {
    return resolveDeskAgent(deskAgentRef(opts)).name || (fallback === undefined ? 'your agent' : fallback);
  }

  // Re-resolves every mounted box once the roster fetch lands — the same
  // deferred-patch shape the old avatar-only version of this used, now also
  // correcting the name text, not just the face.
  function _repaintDeskAgentBoxes() {
    document.querySelectorAll('.desk-v1-posy-box[data-agent-ref]:not(.desk-v1-posy-box-compact)').forEach((box) => {
      const resolved = resolveDeskAgent(box.getAttribute('data-agent-ref') || null);
      const nameEl = box.querySelector('.desk-thread-name');
      const unresolvedLabel = box.hasAttribute('data-pick-agent') ? UNRESOLVED_CAMPAIGN_AGENT_LABEL : UNRESOLVED_AGENT_LABEL;
      if (nameEl) nameEl.textContent = resolved.name || unresolvedLabel;
      const head = box.querySelector('.desk-thread-head');
      if (head && head.firstElementChild && resolved.name && typeof window.avatarHTML === 'function') {
        head.firstElementChild.outerHTML = window.avatarHTML(resolved.avatar, 24);
      }
    });
  }

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
  // R2-18 (Ron 2026-09-30, "we start too deep"): the campaign is framed
  // first — the old ② How stop is now the FIRST stop and reads `Brief`. The
  // route key stays `how` (deep links, fixtures, `missing[].stop`, `camp.how`
  // all keep working); only the order and the visible word changed.
  // R2-19 (Ron 2026-09-30, When/Where overlap): Where comes BEFORE When. Where
  // owns channel placement only (which accounts, which messages go to each);
  // When owns time only, for versions already placed in Where — so When never
  // has to handle a channel the campaign has not picked yet.
  const MAP_STOPS = ['how', 'goal', 'what', 'where', 'when', 'launch'];
  const MAP_STOP_WORDS = { goal: 'Goal', how: 'Brief', what: 'What', when: 'When', where: 'Where', launch: 'Launch' };

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

  // ── Toast with Undo + client command bus (§10: every edit is a command
  // carrying its own inverse, an optimistic UI update, an announcement and an
  // entry in the Undo history). A toast with Undo is raised ONLY for a command
  // marked `destructive: true` (delete / remove / archive / skip): routine
  // edits (a drag, a reorder, a rename) stay quiet and are undone from the
  // Desk header's Undo button or Ctrl/Cmd+Z (desk-v1-shell.js). Every Desk
  // toast shares ONE key, so a new one replaces the old instead of stacking.
  // Reuses `showActionToast` / `showToast` (index.html). ──
  const TOAST_KEY = 'desk-v1';
  const DESTRUCTIVE_TOAST_MS = 6000;
  function toast(message, opts) {
    opts = opts || {};
    announce(message);
    const key = opts.key || TOAST_KEY;
    if (typeof opts.undo === 'function' && typeof window.showActionToast === 'function') {
      return window.showActionToast(esc(message), [
        { label: 'Undo', primary: true, onclick: opts.undo },
      ], { dismissOnAction: true, key, autoDismissMs: opts.durationMs || DESTRUCTIVE_TOAST_MS });
    }
    if (typeof window.showToast === 'function') window.showToast(message, opts.durationMs, key);
    return null;
  }

  const _HISTORY_MAX = 50;
  const _busListeners = [];
  const commandBus = {
    history: [],
    // cmd: { label, do, undo, destructive? }. `do` runs immediately (the
    // optimistic update). A command with no inverse is not a command this bus
    // accepts: every edit stays undoable (§10). Only `destructive` ones raise
    // the Undo toast; the rest are announced and left in `history`.
    run(cmd) {
      if (!cmd || typeof cmd.do !== 'function' || typeof cmd.undo !== 'function') {
        throw new Error('DeskV1Kit.commandBus.run: cmd needs both do() and undo()');
      }
      cmd.do();
      this.history.push(cmd);
      if (this.history.length > _HISTORY_MAX) this.history.shift();
      this._changed();
      const label = cmd.label || 'Done';
      if (cmd.destructive) toast(label, { undo: () => this._undoCmd(cmd) });
      else announce(label);
    },
    // Undo one command wherever it sits in the history (a toast's Undo can
    // outlive later commands), then tell the header button.
    _undoCmd(cmd) {
      const i = this.history.lastIndexOf(cmd);
      if (i >= 0) this.history.splice(i, 1);
      this._changed();
      return cmd.undo();
    },
    canUndo() { return this.history.length > 0; },
    peek() { return this.history.length ? this.history[this.history.length - 1] : null; },
    // Undo the most recent command (header button, Ctrl/Cmd+Z). Returns its label.
    undoLast() {
      const cmd = this.peek();
      if (!cmd) return null;
      this._undoCmd(cmd);
      announce('Undid: ' + (cmd.label || 'the last change'));
      return cmd.label || '';
    },
    clear() { this.history.length = 0; this._changed(); },
    onChange(fn) { if (typeof fn === 'function') _busListeners.push(fn); },
    _changed() { _busListeners.forEach((fn) => { try { fn(); } catch (e) { /* a listener must not break an edit */ } }); },
  };

  // ── Popover placement (MC-977 mobile fix batch 1: WT-4 / P-3 / H-5) ─────────
  // ONE helper for every Desk popover that hangs off a trigger (Add to… menu,
  // the ⋯ card menus, the Review ⋯ menu, ⓘ popovers). Their CSS anchors
  // (`right:0`, `top:calc(100% + 4px)`) assume a roomy desktop row; on a phone
  // a left-edge trigger with `right:0` put the menu 118px off the left edge,
  // and a wrapped crumb made `top:100%` resolve against the crumb, not the
  // button. This measures instead of assuming: it keeps the pop where it is in
  // the DOM (still a child of its position:relative host, so scrolling and
  // focus order are untouched) but sets left/top from the trigger's real rect,
  // clamped to the visible bounds = the modal ∩ the scrolling Desk body ∩ the
  // viewport. Horizontal: align to the trigger's edge on the side that has more
  // room, then clamp. Vertical: below if it fits, else above, else the larger
  // side with a max-height + scroll. Call it right after appending `pop`.
  // opts.align 'start'|'end' forces the edge; opts.prefer 'above' tries above first.
  function placePopover(pop, trigger, opts) {
    if (!pop || !trigger || !pop.isConnected) return;
    opts = opts || {};
    const M = 8, GAP = 4;
    const inter = (a, b) => ({
      left: Math.max(a.left, b.left), top: Math.max(a.top, b.top),
      right: Math.min(a.right, b.right), bottom: Math.min(a.bottom, b.bottom),
    });
    const vv = window.visualViewport;
    let bounds = { left: 0, top: 0, right: vv ? vv.width : window.innerWidth, bottom: vv ? vv.height : window.innerHeight };
    const modal = trigger.closest('.modal-content');
    if (modal) bounds = inter(bounds, modal.getBoundingClientRect());
    const scroller = trigger.closest('#desk-v1-body');
    if (scroller) bounds = inter(bounds, scroller.getBoundingClientRect());
    pop.style.maxWidth = '';
    // The pop's own CSS max-width (the ⓘ popover's 260px) still applies; the
    // bounds only ever tighten it.
    const cssMax = parseFloat(getComputedStyle(pop).maxWidth);
    const availW = Math.max(120, Math.min(isFinite(cssMax) ? cssMax : Infinity, bounds.right - bounds.left - 2 * M));

    // Measure the natural size with the anchor CSS neutralised.
    Object.assign(pop.style, { position: 'fixed', left: '0px', top: '0px', right: 'auto', bottom: 'auto', width: 'max-content', maxWidth: availW + 'px', maxHeight: '', overflowY: '' });
    const pw = pop.offsetWidth;
    const ph = pop.offsetHeight;
    const t = trigger.getBoundingClientRect();

    let align = opts.align;
    if (!align) align = ((t.left + t.right) / 2 > (bounds.left + bounds.right) / 2) ? 'end' : 'start';
    let L = align === 'end' ? t.right - pw : t.left;
    L = Math.min(L, bounds.right - M - pw);
    L = Math.max(L, bounds.left + M);

    const roomBelow = bounds.bottom - M - (t.bottom + GAP);
    const roomAbove = (t.top - GAP) - (bounds.top + M);
    let above = opts.prefer === 'above';
    if (above ? ph > roomAbove && roomBelow >= ph : ph > roomBelow && roomAbove >= ph) above = !above;
    if (ph > (above ? roomAbove : roomBelow)) {
      // Neither side fits: take the larger one and let the menu scroll.
      above = roomAbove > roomBelow;
      const room = Math.max(80, above ? roomAbove : roomBelow);
      pop.style.maxHeight = room + 'px';
      pop.style.overflowY = 'auto';
    }
    const usedH = Math.min(ph, Math.max(80, above ? roomAbove : roomBelow));
    const T = above ? t.top - GAP - usedH : t.bottom + GAP;

    // position:fixed, not absolute: a trigger inside an overflow:auto column body
    // (the Where board) would clip an absolute menu to that box. The pop's rect at
    // left/top 0 is its containing block's origin (the viewport, or a transformed
    // ancestor), so the offset self-calibrates.
    const o = pop.getBoundingClientRect();
    pop.style.left = Math.round(L - o.left) + 'px';
    pop.style.top = Math.round(T - o.top) + 'px';
    // Fixed does not follow its trigger when something scrolls underneath: re-place
    // on the next scroll anywhere (capture), until the menu is gone.
    if (!pop._placeBound) {
      pop._placeBound = true;
      const again = (e) => {
        if (e && e.target === pop) return;
        if (!pop.isConnected) { document.removeEventListener('scroll', again, true); return; }
        placePopover(pop, trigger, opts);
      };
      document.addEventListener('scroll', again, true);
    }
  }

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
    placePopover(menu, triggerEl);
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
        placePopover(pop, btn);
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
  // avatar+name row so the scope chip is the box's only top-row content;
  // sendStyle:'arrow' swaps the text Send button for a round icon one. Both
  // default OFF, producing byte-identical markup to before either option
  // existed — callers that don't pass them are unaffected.
  //
  // opts.agentRef (R2-5): the "scope:name" character ref this box speaks
  // as — pass `DeskV1Kit.deskAgentRef({project, campaign})`'s result. Drives
  // both the name row and the avatar; `data-agent-ref` carries it into the
  // DOM so `_repaintDeskAgentBoxes` can correct a box mounted before the one
  // roster fetch above landed. No ref (nothing chosen) or an unrecognised one
  // both render UNRESOLVED_AGENT_LABEL, never a guessed or hardcoded name.
  function posyBoxHTML(opts) {
    opts = opts || {};
    const inputId = opts.inputId || ('desk-v1-posy-input-' + Math.random().toString(36).slice(2));
    const compact = !!opts.compact;
    const ref = opts.agentRef || null;
    const resolved = resolveDeskAgent(ref);
    const avatar = (!compact && resolved.name && typeof window.avatarHTML === 'function') ? window.avatarHTML(resolved.avatar, 24) : '';
    // opts.pickAgent (campaign page): the name is a button that opens the
    // campaign's agent picker, and an unset agent reads "this campaign".
    const unresolvedLabel = opts.pickAgent ? UNRESOLVED_CAMPAIGN_AGENT_LABEL : UNRESOLVED_AGENT_LABEL;
    const nameHTML = compact ? ''
      : opts.pickAgent
        ? `<button type="button" class="desk-thread-name desk-v1-posy-pickagent" data-pick-agent-btn aria-haspopup="menu">${esc(resolved.name || unresolvedLabel)}</button>`
        : `<span class="desk-thread-name">${esc(resolved.name || unresolvedLabel)}</span>`;
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
      <div class="desk-v1-posy-box${compact ? ' desk-v1-posy-box-compact' : ''}" data-agent-ref="${esc(ref || '')}"${opts.pickAgent ? ' data-pick-agent' : ''}>
        <div class="desk-thread-head">${avatar}${nameHTML}${scope}</div>
        <div class="agent-output desk-v1-posy-output">${suggestionHTML}</div>
        ${chipsHTML}
        <div class="agent-input-row">
          <textarea class="agent-task-input desk-v1-posy-input" id="${esc(inputId)}" rows="2" placeholder="Tell ${esc(resolved.name || 'your agent')} what to change…"></textarea>
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
      output.innerHTML = `${bubble}<div class="desk-v1-posy-status" aria-live="polite">${esc(ctx.agentName || 'Your agent')} has it</div>`;
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
        <div class="desk-v1-posy-failed" aria-live="polite">&#9888; ${esc(ctx.agentName || 'Your agent')} couldn't finish: ${esc(reason)}. Nothing was changed.</div>
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
    if (!output) return;
    const name = resolveDeskAgent(_refFromContainer(boxEl)).name || 'Your agent';
    output.innerHTML = `<div class="desk-v1-posy-status" aria-live="polite">${esc(name)} answered; nothing changed.</div>`;
  }

  // Live mode (R1-W S10): a box whose agent path is not wired says so and ends
  // the lifecycle, instead of a simulated reply claiming the agent answered.
  function paintPosyNotConnected(boxEl) {
    const output = boxEl && boxEl.querySelector('.desk-v1-posy-output');
    if (!output) return;
    output.innerHTML = '<div class="desk-v1-posy-status" data-posy-not-connected aria-live="polite">Not connected to the agent yet. Nothing was sent or changed.</div>';
  }

  // The box's own ref lives on posyBoxHTML()'s `data-agent-ref` — read it
  // back off the DOM rather than asking every caller to pass it a second
  // time to bindPosyBox()/paintPosyReadyNoDiff() (both always operate on the
  // same element posyBoxHTML() rendered into, or a direct ancestor of it).
  function _refFromContainer(containerEl) {
    if (!containerEl) return null;
    const el = containerEl.matches && containerEl.matches('[data-agent-ref]')
      ? containerEl : containerEl.querySelector('[data-agent-ref]');
    return el ? (el.getAttribute('data-agent-ref') || null) : null;
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
      if (ctx && ctx.ta) { ctx.ta.value = ''; ctx.ta.dispatchEvent(new Event('input')); } // draft cleared NOW, not at Send.
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
        else if (force === 'question') _resolvePosyTask(key, ask, 'needs_answer', { question: { text: `This would widen what ${ctx.agentName || 'your agent'} can do — go ahead?`, options: ['Yes', 'No'] } });
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
  // R2-5: was a fixed '⟳ Posy working' string; now built per-caller from the
  // resolved agent so Home's project-card badge (the one caller) names
  // whoever the project actually picked. Same deskAgentName(opts, fallback)
  // resolution as every other sentence-embedded label in this file.
  function deskAgentWorkingLabel(opts) {
    return `⟳ ${deskAgentName(opts)} working`;
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
    // Live (R1-W S10): `taskLifecycle` boxes are the R0 simulation, a canned
    // "Working on it" then a made-up answer. A box whose agent route is wired
    // passes taskLifecycle off in live (Review); every other one says it is not
    // connected rather than pretend the agent answered.
    const unwired = !!opts.taskLifecycle && !!window.DeskV1Store && window.DeskV1Store.live();
    const simulate = !!opts.taskLifecycle && !unwired;
    containerEl.querySelectorAll('.desk-v1-posy-chips .agent-question-chip').forEach((btn) => {
      btn.onclick = () => {
        const ta = document.getElementById(inputId);
        if (ta) { ta.value = btn.dataset.chip || ''; ta.dispatchEvent(new Event('input')); ta.focus(); }
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
    const agentName = resolveDeskAgent(_refFromContainer(containerEl)).name || 'your agent';
    const ctx = { containerEl, ta, key, onSend, agentName };
    // The box is narrow (a 320px rail), so the placeholder wraps: start two
    // lines tall (CSS min-height) and grow with the text up to ~8 lines. Any
    // programmatic value change (chips, Send clearing it) dispatches `input`
    // so this refits too.
    if (ta) {
      const fit = () => {
        ta.style.height = 'auto';
        if (ta.scrollHeight > 0) ta.style.height = Math.min(ta.scrollHeight + 2, 190) + 'px';
      };
      ta.addEventListener('input', fit);
      requestAnimationFrame(fit);
    }
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
      if (unwired) {
        toast('This box is not connected to the agent yet. Nothing was sent or changed.');
        paintPosyNotConnected(containerEl);
        return;
      }
      if (!simulate) {
        if (ta) { ta.value = ''; ta.dispatchEvent(new Event('input')); }
        if (key) _entryFor(key).draftText = '';
        onSend(text);
        return;
      }
      if (key) {
        const entry = _entryFor(key);
        const cur = entry.asks[entry.asks.length - 1];
        if (cur && !_isPosyTerminal(cur.state)) { toast(`${ctx.agentName || 'Your agent'} is still on the last one.`); return; }
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

  // Effective cadence — the campaign's OWN per_week, nothing else. The
  // project used to cap it (a per-account "ceiling" on the Presence screen);
  // that screen is retired (MC-977 2026-10-01, Ron: one place per setting), so
  // every limit a campaign runs under is one the user can see and edit on the
  // campaign's Brief. `effective` keeps its shape for the callers that read it.
  function _effectiveCadence(plan) {
    const campPerWeek = plan && plan.cadence ? plan.cadence.per_week : null;
    return { value: campPerWeek == null ? null : campPerWeek, fromProject: false };
  }

  // ── validatePlan (§4: "one canonical plan object"; rescoped IA2 §5, IA
  // revision 2 §5/§8 R2-1) — the single gate the checklist state, the Start
  // button, Resume and Renew all share. Checks the §2.3 step-2 bound table's
  // "Required to leave" rows — accounts, cadence, end date and/or post cap
  // (>=1). `source_projects` is retired (§3 row 14: owner is the parent
  // project, implicit, no bound). `min_gap_h` lives on `plan.cadence` again
  // (Presence retired, MC-977 2026-10-01) but is not a required bound. `project`
  // is accepted and ignored — no project-level ceiling clamps a campaign any
  // more; the arg stays so existing callers need no change.
  //
  // Each bound now carries a `stop` (§2 map vocabulary: 'goal'|'how'|'what'|
  // 'when'|'where'|'launch') alongside the older numeric `step`, additive —
  // existing callers reading `.step`/`.bound` see no change; R2-3's map
  // stepper (not built by this ticket) will read `.stop` to link a missing
  // bound to the right ①-⑥ stop.
  const _PLAN_BOUNDS = [
    { bound: 'accounts', step: 2, stop: 'where', label: 'accounts',
      missing: (p) => !(p.accounts && p.accounts.length) },
    { bound: 'cadence', step: 2, stop: 'how', label: 'cadence',
      missing: (p) => !(p.cadence && p.cadence.per_week != null) },
    { bound: 'end', step: 2, stop: 'how', label: 'end date',
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
  function validatePlan(plan, project) {
    plan = plan || {};
    const missing = _PLAN_BOUNDS
      .filter((b) => b.missing(plan))
      .map((b) => ({ bound: b.bound, step: b.step, stop: b.stop, label: b.label }));
    [_goalMissing(plan), _termMissing(plan)]
      .filter(Boolean)
      .forEach((m) => missing.push(m));
    const eff = _effectiveCadence(plan);
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
    // A tighter minimum gap lets the same campaign post more often, so a
    // LOWER min_gap_h widens (only when both sides carry one).
    if (prev.min_gap_h != null && next.min_gap_h != null && next.min_gap_h < prev.min_gap_h) return true;
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
  // §5.2: budget.amount raised is a widening; equal or lower never is. (The
  // 'own' -> 'project' source switch widened because a project pool could be
  // larger; the pool is gone with the Presence screen, so only the amount counts.)
  function _budgetWiden(prevBounds, nextBounds) {
    const prevBudget = (prevBounds && prevBounds.budget) || {};
    const nextBudget = (nextBounds && nextBounds.budget) || {};
    if ((nextBudget.amount || 0) > (prevBudget.amount || 0)) return true;
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

  // ── R2-14 (§10 outcome learning loop) — retro dimension table + the
  // sample-size verdict rules. Fixtures + kit shapes only, same ground rule
  // as R2-1 above: R2-15 wires the ① Retro section UI, R1-L wires the
  // backend `mc/desk_retro.py` that must reproduce this byte-for-byte
  // (§8 R1-L acceptance) — this is the ONE place the verdict rule lives so
  // both sides read the same table. `unit` picks the §10.1 dimension-table
  // row: 'post' dimensions gate on post count, 'campaign' dimensions gate
  // on distinct campaign count (angle/spend kind — almost always too few in
  // v1, per spec, shown anyway so the gap is visible).
  const RETRO_DIMENSIONS = {
    format: { label: 'Piece format', unit: 'post', note: '' },
    // §10.1 honesty rule: the voice split fixes Ron to 𝕏 and Clayrune to
    // LinkedIn, so v1 cannot separate "LinkedIn worked" from "the Clayrune
    // voice worked" — one dimension, not two.
    platform_voice: {
      label: 'Platform + voice', unit: 'post',
      note: 'one dimension, not two: the voice split fixes Ron to \u{1D54F} and Clayrune to LinkedIn, ' +
        'so v1 can’t separate “LinkedIn worked” from “the Clayrune voice worked”.',
    },
    slot: { label: 'Posting day / time slot', unit: 'post',
      note: 'compared within one account only (platforms have different audiences at different hours)' },
    angle: { label: 'Angle / strategy', unit: 'campaign',
      note: 'n = campaigns, so almost always “Too few campaigns to tell” in v1; shown anyway so the gap is visible' },
    spend_kind: { label: 'Spend kind', unit: 'campaign', note: 'cost per outcome, same n caveat' },
  };

  // arm item: a number, or {value, id, campaign_id} — id links "one post
  // drives this" back to the post; campaign_id groups a campaign-unit arm's
  // distinct-campaign count. Plain numbers get a synthetic id.
  function _armStats(items) {
    const norm = (items || []).map((it, i) => (typeof it === 'number' ? { value: it, id: 'item-' + i } : it));
    const n = norm.length;
    const total = norm.reduce((s, it) => s + (it.value || 0), 0);
    const mean = n ? total / n : 0;
    const sorted = norm.map((it) => it.value).sort((a, b) => a - b);
    const median = n ? (n % 2 ? sorted[(n - 1) / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2) : 0;
    let dominant = null;
    if (total > 0) {
      for (const it of norm) {
        const share = it.value / total;
        if (!dominant || share > dominant.share) dominant = { id: it.id, share };
      }
    }
    const campaignIds = new Set(norm.map((it) => it.campaign_id).filter(Boolean));
    return { n, mean, median, dominant, campaignCount: campaignIds.size || n };
  }

  // §10.1 sample-size table, in order — the model never picks a winner this
  // table didn't already produce (§10.1: "The model only words the retro
  // summary ... it never picks a winner the table did not already produce").
  function retroVerdict(dimension, arms) {
    const meta = RETRO_DIMENSIONS[dimension] || { unit: 'post' };
    const a = _armStats(arms && arms.a);
    const b = _armStats(arms && arms.b);

    if (meta.unit === 'campaign') {
      if (a.campaignCount < 3 || b.campaignCount < 3) {
        return { verdict: 'too_few_campaigns', dimension,
          text: `Too few campaigns to tell (${a.campaignCount} and ${b.campaignCount}; need 3 each)` };
      }
    } else if (a.n < 10 || b.n < 10) {
      return { verdict: 'too_few_posts', dimension,
        text: `Too few posts to tell (${a.n} and ${b.n}; need 10 each)` };
    }

    const meanDir = a.mean >= b.mean ? 'a' : 'b';
    const medianDir = a.median >= b.median ? 'a' : 'b';
    const hi = Math.max(a.mean, b.mean), lo = Math.min(a.mean, b.mean);
    const ratio = lo > 0 ? hi / lo : (hi > 0 ? Infinity : 1);
    if (ratio < 1.3 || meanDir !== medianDir) {
      return { verdict: 'no_clear_difference', dimension, text: 'No clear difference' };
    }

    const bestA = a.dominant, bestB = b.dominant;
    const dominant = (bestA && (!bestB || bestA.share >= bestB.share)) ? bestA : bestB;
    if (dominant && dominant.share > 0.5) {
      return { verdict: 'one_post_drives', dimension, post_id: dominant.id,
        text: 'One post drives this, not a pattern' };
    }

    // §10.2 confidence: low = one campaign/term; medium = same direction in
    // >=2 campaigns, pooled n>=20/arm; high = >=3 campaigns, pooled n>=30/arm
    // (the "no confirmed retro pointing the other way" clause is R1-L's own
    // read of the playbook store, not a kit-level concern).
    const minCampaigns = Math.min(a.campaignCount, b.campaignCount);
    const minN = Math.min(a.n, b.n);
    let confidence = 'low';
    if (minCampaigns >= 3 && minN >= 30) confidence = 'high';
    else if (minCampaigns >= 2 && minN >= 20) confidence = 'medium';

    return {
      verdict: 'finding', dimension, confidence,
      effect: { ratio, direction: `${meanDir}>${meanDir === 'a' ? 'b' : 'a'}` },
      n_total: a.n + b.n,
    };
  }

  // ── R2-7: the piece shape, read in ONE place (docs/THE_DESK_V1_IA_REVISION_2.md
  // §8 R2-7; frames 5a/7b). A piece is `{id, campaignId, kind, title, assets[],
  // versions[]}`: `assets[]` are its media (`{id, kind, title, src}`), `versions[]`
  // its destination versions (one per channel, each with its own state). What's
  // list rows and Where's Messages column both read `on N channels` from here, so
  // the two can never disagree about how many channels a piece is on.
  const PIECE_KIND_WORDS = { post: 'Post', article: 'Article', video: 'Video', image: 'Image' };
  function pieceKindWord(kind) {
    return PIECE_KIND_WORDS[kind] || (kind ? String(kind).charAt(0).toUpperCase() + String(kind).slice(1) : 'Piece');
  }
  // A version is live while it sits on a channel and was not archived or skipped.
  function pieceVersions(piece) {
    return ((piece && piece.versions) || []).filter((v) => v.channelId && v.state !== 'archived' && v.state !== 'skipped');
  }
  function pieceChannelCount(piece) { return pieceVersions(piece).length; }
  function pieceChannelsText(piece) {
    const n = pieceChannelCount(piece);
    return `on ${n} channel${n === 1 ? '' : 's'}`;
  }
  function pieceAssets(piece) { return (piece && piece.assets) || []; }

  // ── R2-17: the Suggest brief's `because` (docs/THE_DESK_V1_IA_REVISION_2.md
  // §10.3). An agent's suggestion cites `[finding ids]` or `'untested'`. The
  // ids are the AGENT'S claim, not evidence: only an id that is a CONFIRMED
  // finding of THIS project survives, everything else (unknown, proposed,
  // rejected, stale, retired, another project's) is dropped so an agent cannot
  // invent authority. A suggestion left with no surviving id and no
  // `'untested'` gets no chip at all. Reads `DeskV1Fixtures.playbook` (R2-16's
  // store); finding state stays human-only, nothing here writes it.
  function resolveBecause(because, projectId) {
    const fx = (window.DeskV1Store ? window.DeskV1Store.state() : {}).playbook || {};
    const confirmed = new Set((fx.findings || [])
      .filter((f) => f.state === 'confirmed' && f.project_id === projectId).map((f) => f.id));
    const ids = Array.isArray(because)
      ? Array.from(new Set(because.filter((id) => typeof id === 'string' && confirmed.has(id)))) : [];
    return { ids, untested: because === 'untested' };
  }
  function _becauseEsc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function becauseChipsHTML(because, projectId) {
    const r = resolveBecause(because, projectId);
    if (r.untested) return '<span class="desk-v1-because-chip desk-v1-because-chip--trying" data-because-trying>Trying: untested</span>';
    return r.ids.map((id) => `<button type="button" class="desk-v1-because-chip" data-because-finding="${_becauseEsc(id)}">Based on ${_becauseEsc(id)} ›</button>`).join('');
  }
  // `Based on F3 ›` opens that finding's evidence on the project page Playbook
  // (R2-16: rows carry `data-finding-id`, evidence disclosure per row).
  function bindBecauseChips(root, projectId) {
    root.querySelectorAll('[data-because-finding]').forEach((btn) => {
      btn.onclick = () => { if (typeof window.deskV1Nav === 'function') window.deskV1Nav('project', { projectId, findingId: btn.dataset.becauseFinding }); };
    });
  }

  window.DeskV1Kit = {
    pieceKindWord, pieceVersions, pieceChannelCount, pieceChannelsText, pieceAssets,
    VERSION_STATES, CAMPAIGN_STATES,
    MAP_STOPS, MAP_STOP_WORDS,
    channelCapabilityCopy, noChargeYetCopy,
    BANNED_PHRASES, lintCopy,
    stateLabel, stateLabelHTML,
    channelBadge,
    announce, toast, commandBus,
    addToMenu, bindAddToTrigger, placePopover,
    infoIconHTML, bindInfoIcons,
    posyBoxHTML, bindPosyBox,
    deskAgentRef, accountVoice, projectAgentChoices, resolveDeskAgent, deskAgentName, UNRESOLVED_AGENT_LABEL, UNRESOLVED_CAMPAIGN_AGENT_LABEL, onAgentsReady,
    anyPosyWorking, deskAgentWorkingLabel, paintPosyReadyNoDiff, paintPosyNotConnected,
    openConfirmSheet,
    validatePlan, MAX_TERM_DAYS: _MAX_TERM_DAYS,
    computeBoundsHash, boundsWiden, nextBoundsHash,
    RETRO_DIMENSIONS, retroVerdict,
    resolveBecause, becauseChipsHTML, bindBecauseChips,
  };
})();

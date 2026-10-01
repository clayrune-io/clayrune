// Desk v1 (MC-977) — T2a: Campaign page + Content list (frame 12a, + 12e;
// docs/desk_v1_r0_plan.md; THE_DESK_V1_UI.md §3.1-3.4, 3.6, §9, §10, §11).
// Window-bridged module, no `import` (ground rule 1). `desk-v1-shell.js` owns
// the campaign-page skeleton (summary / tab strip / tab body / right column /
// add tray) and mounts each slot by calling the five `deskV1FillCampaign*`
// hooks below — this file owns their content; the skeleton itself never
// changes (T0a's own comment on _renderCampaignSkeleton).
//
// Fixtures only (ground rule 3): every drop/attach/move/skip/archive mutates
// the in-memory DeskV1Fixtures objects directly through DeskV1Kit.commandBus
// — the same "client-side over fixture data with Undo" contract T1/T3/T4
// already established. Nothing here calls a backend route.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // R2-6: the literal request text desk-v1-how.js's Suggest button sends
  // through the rightcol Posy box — the `onSend` handler below (in
  // `deskV1FillCampaignRightColumn`) matches on this exact string to run
  // `_runSuggestTask` instead of the generic instruction handler.
  const _HOW_SUGGEST_TEXT = 'Suggest What / When / Where';

  // ── data resolution (same _fx() convention as desk-v1-review.js/-home.js) ─
  function _fx() { return window.DeskV1Fixtures || {}; }
  function _campaigns() { return _fx().campaigns || []; }
  function _channels() { return _fx().channels || []; }
  function _channel(id) { return _channels().find((c) => c.id === id); }
  function _campaign(id) { return _campaigns().find((c) => c.id === id) || null; }
  function _project(id) { return (_fx().projects || []).find((p) => p.id === id) || null; }
  function _families() { return _fx().families || []; }
  function _familiesFor(campaignId) { return _families().filter((f) => f.campaignId === campaignId); }
  function _conversations() { return _fx().conversations || []; }

  const _TERMINAL_STATES = new Set(['verified_published', 'you_reported', 'failed', 'skipped', 'archived']);
  const _SCHEDULED_STATES = new Set(['scheduled', 'approved', 'sending', 'submitted']);
  const _PUBLISHED_STATES = new Set(['verified_published', 'you_reported']);

  // §6.1's "before the first publish" empty state, shared by the goal bar,
  // Results and Conversations panels: true once any version of any family in
  // this campaign has actually gone out.
  function _hasPublished(campaignId) {
    return _familiesFor(campaignId).some((f) => _familyHasState(f, _PUBLISHED_STATES));
  }
  window.deskV1CampaignHasPublished = _hasPublished;

  // Earliest still-scheduled version's slot, campaign-scoped (§6.1 "Next:
  // Tue 09:00 on 𝕏 · @ron."). Shared with the Results empty state and the
  // project page's "next post across campaigns" (IA6).
  function _nextScheduledVersion(campaignId) {
    const versions = [];
    _familiesFor(campaignId).forEach((f) => (f.versions || []).forEach((v) => {
      if (_SCHEDULED_STATES.has(v.state) && v.publishAt) versions.push(v);
    }));
    versions.sort((a, b) => (a.publishAt < b.publishAt ? -1 : 1));
    return versions[0] || null;
  }
  window.deskV1CampaignNextScheduled = _nextScheduledVersion;

  function _familyHasState(fam, states) { return (fam.versions || []).some((v) => states.has ? states.has(v.state) : v.state === states); }

  // ── module state — one campaign-page mount at a time (same single-slot
  // precedent as desk-v1-review.js's `_st`). Rebuilt fresh whenever the
  // campaignId changes (a different campaign, or first mount); preserved
  // across a tab-body-only re-render (filter/view toggles) so the user's
  // List/Calendar choice and filters survive their own interactions. ────────
  let _st = null;
  function _ensureState(campaignId) {
    if (_st && _st.campaignId === campaignId) return _st;
    _st = {
      campaignId, view: 'list', filter: 'all', channelFilter: 'all',
      selection: { scope: 'campaign', id: null, label: null },
    };
    return _st;
  }

  // ────────────────────────────────────────────────────────────────────────
  // Summary bar (§3.1). The shell's own crumb already renders "‹ <parent> ·
  // <campaign name>" (desk-v1-shell.js _renderCrumb/_campaignLabel) — this
  // slot renders the rest of the bar the frame draws beside/below that: the
  // state pill + Pause, then the Goal / Channels groups. Not
  // duplicating the name avoids two "Windows beta testers" on screen.
  // ────────────────────────────────────────────────────────────────────────
  function deskV1FillCampaignSummary(el, params) {
    const camp = _campaign(params.campaignId);
    if (!camp) { el.innerHTML = '<div class="desk-v1-stub-inline">Campaign not found.</div>'; return; }
    // §3.5: "Same page, with state ◇ Proposed" — T2b owns that whole variant
    // (editable goal sentence, Start campaign button) rather than this file
    // branching internally on every group below. Backward-compatible seam:
    // undefined until desk-v1-rules.js loads, at which point every proposed
    // campaign uses it.
    if (camp.state === 'proposed' && typeof window.deskV1FillProposedSummary === 'function') {
      window.deskV1FillProposedSummary(el, params, camp);
      return;
    }
    // R2-3b: a Draft has no summary bar — the map stepper is the first thing
    // under the crumb, like every other campaign page (Ron 2026-09-30), and
    // its own ⋯ menu (`deskV1FillCampaignTabStrip`) carries Delete draft.
    if (camp.state === 'draft') { el.innerHTML = ''; return; }
    const stateHTML = DeskV1Kit.stateLabelHTML(camp.state, { className: 'desk-v1-camp-state-pill' });
    // §4/Dave review pass 1: target/deadline/outcome/tracked are read from
    // `camp.plan.goal` — the SAME object the rules popover and Resume's
    // validatePlan gate read/write — so an edited plan never leaves this
    // bar showing a stale number. `current` (live progress) has no plan
    // field and stays on `camp.goal`.
    const goal = (camp.plan && camp.plan.goal) || {};
    const current = (camp.goal && camp.goal.current) || 0;
    const pct = goal.tracked && goal.target
      ? Math.max(0, Math.min(100, Math.round((current / goal.target) * 100))) : 0;
    // §6.1 empty state: "before the first publish ... (no `0/30` bar)" — a
    // campaign that has never had a version go out shows the goal as plain
    // text instead of a bar reading 0/<target>, which would otherwise imply
    // measured zero rather than "not started counting yet" (MET-01).
    const fresh = !_hasPublished(camp.id);
    const goalHTML = goal.tracked
      ? (fresh
        ? `<div class="desk-v1-camp-summary-goal">
            <span class="desk-v1-camp-summary-label">GOAL</span>
            <span class="desk-v1-camp-summary-goaltext">${esc(goal.target)} ${esc(goal.outcome)}${goal.deadline ? ' by ' + esc(_fmtDate(goal.deadline)) : ''} · starts counting at the first post</span>
          </div>`
        : `<button type="button" class="desk-v1-camp-summary-goal" data-goal-btn>
            <span class="desk-v1-camp-summary-label">GOAL</span>
            <span class="desk-v1-camp-summary-goaltext">${esc(current)}/${esc(goal.target)} ${esc(goal.outcome)}${goal.deadline ? ' by ' + esc(_fmtDate(goal.deadline)) : ''}</span>
            <span class="desk-v1-camp-summary-goalbar"><span style="width:${pct}%"></span></span>
          </button>`)
      : `<div class="desk-v1-camp-summary-goal desk-v1-camp-summary-goal-untracked">
          <span class="desk-v1-camp-summary-label">GOAL</span>
          <span class="desk-v1-camp-summary-goaltext">⚠ not tracked yet</span>
        </div>`;
    const chans = (camp.plan.accounts || []).map(_channel).filter(Boolean);
    const channelsHTML = `<div class="desk-v1-camp-summary-group" data-summary-group="channels">
        <span class="desk-v1-camp-summary-label">CHANNELS</span>
        <div class="desk-v1-camp-summary-badges">${chans.length ? chans.map((ch) => DeskV1Kit.channelBadge(ch)).join('') : '<span class="desk-v1-home-camp-nochannels">No channels yet</span>'}</div>
      </div>`;

    el.innerHTML = `
      <div class="desk-v1-camp-summary-top">
        ${stateHTML}
        <div class="desk-v1-camp-summary-top-actions">
          ${camp.state === 'paused'
            ? `<button type="button" class="desk-v1-camp-pause-btn" data-resume-btn>▶ Resume</button>`
            : `<button type="button" class="desk-v1-camp-pause-btn" data-pause-btn ${camp.state !== 'active' ? 'disabled' : ''}>⏸ Pause</button>`}
          ${camp.state !== 'archived' ? `<div class="desk-v1-camp-card-more">
            <button type="button" class="desk-v1-camp-card-morebtn" data-camp-more-btn aria-haspopup="menu" aria-label="More actions">⋯</button>
          </div>` : ''}
        </div>
      </div>
      <div class="desk-v1-camp-summary-groups">
        ${goalHTML}
        ${channelsHTML}
      </div>`;

    const goalBtn = el.querySelector('[data-goal-btn]');
    // R2-3: 'results' is now the deep-link alias for ① goal (PANEL_ALIASES,
    // shell.js) — this call bypasses `deskV1Nav`'s alias table, so it must
    // already name the real panel or the tab body falls through to ③ what.
    if (goalBtn) goalBtn.onclick = () => window.deskV1GotoCampaignPanel('goal', { campaignId: camp.id });
    const moreBtn = el.querySelector('[data-camp-more-btn]');
    if (moreBtn) moreBtn.onclick = (e) => {
      e.stopPropagation();
      deskV1OpenCampaignMoreMenu(moreBtn, camp.id, {
        onDone: (result) => {
          if (result === 'deleted') deskV1Nav('home', {});
          else deskV1FillCampaignSummary(el, params);
        },
      });
    };
    const pauseBtn = el.querySelector('[data-pause-btn]');
    if (pauseBtn) pauseBtn.onclick = () => {
      if (camp.state !== 'active') return;
      const prev = camp.state;
      DeskV1Kit.commandBus.run({
        label: `Paused “${camp.plan.title}”`,
        do: () => { camp.state = 'paused'; deskV1FillCampaignSummary(el, params); },
        undo: () => { camp.state = prev; deskV1FillCampaignSummary(el, params); },
      });
    };
    const resumeBtn = el.querySelector('[data-resume-btn]');
    if (resumeBtn) resumeBtn.onclick = () => _openResumeSheet(camp, el, params);
  }

  // ── Pause / Resume (§6.2) ──────────────────────────────────────────────
  // Resuming isn't a silent restart: it shows the upcoming work affected,
  // then re-validates the plan (§4's shared `validatePlan` gate) plus an
  // expiry check the bound table itself doesn't cover (an end date that has
  // since passed, a post cap already reached, or a destination that went
  // held while paused). `_isoToday()` mirrors desk-v1-rules.js's own
  // `_isPastLocal` local-date convention rather than importing it (no
  // cross-module import in static/js).
  function _isoToday() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }
  function _planExpiryReason(plan) {
    if (!plan) return null;
    if (plan.end && plan.end.date && plan.end.date < _isoToday()) return 'its end date has passed';
    if (plan.end && plan.end.post_cap != null && plan.end.post_cap <= 0) return 'its post cap is reached';
    const held = (plan.destinations || []).find((d) => { const ch = _channel(d.account); return ch && ch.health === 'held'; });
    if (held) return `${(_channel(held.account) || {}).label || held.account} is held`;
    return null;
  }

  // "3 posts resume: Tue 09:00 …" (§6.2) — the earliest still-scheduled
  // version's own slot, same `_fmtWhenShort` weekday/time formatting the
  // content cards already use for a scheduled version's detail line.
  function _upcomingResumeText(camp) {
    const versions = [];
    _familiesFor(camp.id).forEach((f) => (f.versions || []).forEach((v) => {
      if (_SCHEDULED_STATES.has(v.state) && v.publishAt) versions.push(v);
    }));
    versions.sort((a, b) => (a.publishAt < b.publishAt ? -1 : 1));
    if (!versions.length) return 'Resuming restarts this campaign’s cadence. Missed slots are skipped, never posted late.';
    const first = _fmtWhenShort(versions[0].publishAt);
    const ch = _channel(versions[0].channelId);
    const plural = versions.length === 1 ? 'post resumes' : 'posts resume';
    return `${versions.length} ${plural}: ${first}${ch ? ` on ${ch.label}` : ''}. Missed slots are skipped, never posted late.`;
  }

  // `after` (R2-11): the Launch page's own Resume also has to repaint the
  // panel it sits on, not just the summary bar.
  function _openResumeSheet(camp, summaryEl, params, after) {
    const repaint = () => { if (summaryEl) deskV1FillCampaignSummary(summaryEl, params); if (after) after(); };
    DeskV1Kit.openConfirmSheet({
      title: `Resume “${camp.plan.title}”?`,
      body: _upcomingResumeText(camp),
      confirmLabel: 'Resume', cancelLabel: 'Cancel',
      onConfirm: () => {
        const validity = DeskV1Kit.validatePlan(camp.plan);
        const expiryReason = _planExpiryReason(camp.plan);
        if (!validity.ok || expiryReason) {
          const reason = expiryReason || `it's missing ${validity.missing.map((m) => m.label).join(', ')}`;
          // §6.2: "Resume routes to step 4 with only the changed terms, not
          // a silent restart." R2-3b retired the rules popover that used to
          // stand in for that page; ⑥ Launch is where the missing terms are
          // listed, each linking to the stop that fixes it, so Resume lands
          // there and the toast says exactly why it didn't happen.
          window.deskV1GotoCampaignPanel('launch', { campaignId: camp.id });
          DeskV1Kit.toast(`Can’t resume “${camp.plan.title}” — ${reason}. Fix it, then resume.`);
          return;
        }
        const prev = camp.state;
        DeskV1Kit.commandBus.run({
          label: `Resumed “${camp.plan.title}”`,
          do: () => { camp.state = 'active'; repaint(); },
          undo: () => { camp.state = prev; repaint(); },
        });
      },
    });
  }

  // ── Delete / Archive (item 3, MC-977 R0 UX pass; scope note from Dave/
  // Kestrel's review, 2026-09-28): a campaign that has never published
  // (draft/proposed) is fully removable — nothing exists anywhere else to
  // protect. One that has ever gone active/paused/completed carries
  // publication history and receipts, so the destructive action becomes
  // Archive (stops future work, keeps results) instead of outright Delete.
  // Both routes through the SAME in-page confirm sheet (d146df0's rule —
  // never window.confirm) and the SAME commandBus Undo. Shared here (not
  // duplicated in desk-v1-home.js) so Home's card menu and the campaign
  // page's header menu can never drift on the draft/published boundary.
  function _isPrePublish(camp) { return camp.state === 'proposed' || camp.state === 'draft'; }

  // Cascades to the campaign's own families/versions and conversations —
  // Home's _needsYouItems() iterates ALL families/conversations with no
  // existence check on their campaignId, so leaving them behind would
  // deep-link Home's Needs You rail into a campaign that no longer exists.
  // Undo restores every removed item at its ORIGINAL array index (captured
  // before removal), not just appended at the end, so array order — and any
  // other code that assumes it — survives an undo unchanged.
  function _deleteCampaignCascade(camp, onDone) {
    const campaigns = _campaigns();
    const families = _families();
    const conversations = _conversations();
    let campIdx, famRemoved, convRemoved;
    DeskV1Kit.commandBus.run({
      label: `Deleted “${camp.plan.title || 'Untitled draft'}”`,
      do: () => {
        campIdx = campaigns.indexOf(camp);
        famRemoved = [];
        for (let i = families.length - 1; i >= 0; i--) {
          if (families[i].campaignId === camp.id) { famRemoved.unshift({ item: families[i], idx: i }); families.splice(i, 1); }
        }
        convRemoved = [];
        for (let i = conversations.length - 1; i >= 0; i--) {
          if (conversations[i].campaignId === camp.id) { convRemoved.unshift({ item: conversations[i], idx: i }); conversations.splice(i, 1); }
        }
        if (campIdx >= 0) campaigns.splice(campIdx, 1);
        if (onDone) onDone('deleted');
      },
      undo: () => {
        campaigns.splice(Math.min(campIdx, campaigns.length), 0, camp);
        famRemoved.forEach(({ item, idx }) => families.splice(idx, 0, item));
        convRemoved.forEach(({ item, idx }) => conversations.splice(idx, 0, item));
        if (onDone) onDone('restored');
      },
    });
  }

  // R2-3b (Ron 2026-09-30, "an option to delete a drafted campaign, at least
  // from the main Desk page"): a Draft has nothing to protect and is cheap to
  // remake, so deleting one asks no confirmation — the Undo toast
  // (`_deleteCampaignCascade`'s commandBus entry) is the safety. Shared by
  // Home's per-row trash and the campaign page's ⋯ › Delete draft. Refuses any
  // other state: proposed/active/... keep the confirm-sheet/Archive paths.
  function deskV1DeleteDraftCampaign(campaignId, onDone) {
    const camp = _campaign(campaignId);
    if (!camp || camp.state !== 'draft') return false;
    _deleteCampaignCascade(camp, onDone);
    return true;
  }
  window.deskV1DeleteDraftCampaign = deskV1DeleteDraftCampaign;

  // Archive never touches families/conversations/results — §"keeps results
  // and receipts" means literally nothing else in fixture data moves; only
  // camp.state changes, same one-field mutation Pause already makes.
  // `_preArchiveState` mirrors project.js's `_prePauseState` convention: the
  // commandBus Undo toast (below) restores it immediately, but a project
  // page's `More › Restore` (IA6, §5 row IA6) may act long after that toast
  // has expired, so the field has to outlive it.
  function _archiveCampaign(camp, onDone) {
    const prevState = camp.state;
    DeskV1Kit.commandBus.run({
      label: `Archived “${camp.plan.title}”`,
      do: () => { camp._preArchiveState = prevState; camp.state = 'archived'; if (onDone) onDone('archived'); },
      undo: () => { camp.state = prevState; delete camp._preArchiveState; if (onDone) onDone('restored'); },
    });
  }

  // Restore (IA6, §5 row IA6): the project page's "More › Restore" on an
  // archived campaign's card (desk-v1-project.js). Puts the campaign back to
  // whatever state Archive found it in (`_preArchiveState` above) rather than
  // assuming "active" — an archived Paused campaign restores to Paused.
  function _restoreCampaign(camp, onDone) {
    const restoredState = camp._preArchiveState || 'active';
    DeskV1Kit.commandBus.run({
      label: `Restored “${camp.plan.title}”`,
      do: () => { camp.state = restoredState; delete camp._preArchiveState; if (onDone) onDone('restored'); },
      undo: () => { camp._preArchiveState = restoredState; camp.state = 'archived'; if (onDone) onDone('archived'); },
    });
  }
  window.deskV1RestoreCampaign = _restoreCampaign;

  // triggerEl's own parent becomes the positioned host (same convention as
  // _openCardMenu below) — works whether triggerEl sits in the campaign
  // header's own wrapper or a Home card's, so one function serves both.
  // opts.onDone(result) — 'deleted' | 'archived' | 'restored' — lets each
  // caller decide what a completed action means for ITS page (the campaign
  // page navigates Home away from a just-deleted campaign; Home just
  // re-renders its own grid either way).
  function deskV1OpenCampaignMoreMenu(triggerEl, campaignId, opts) {
    opts = opts || {};
    const camp = _campaign(campaignId);
    if (!camp) return;
    const host = triggerEl.parentElement;
    const existing = host.querySelector(':scope > .desk-v1-camp-cardmenu');
    if (existing) { existing.remove(); return; }
    // Only anchor a static host: a host the caller already positioned (Home's
    // absolute ⋯ wrapper) must stay where it is or the button jumps.
    if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
    const prePublish = _isPrePublish(camp);
    const menu = document.createElement('div');
    menu.className = 'desk-v1-camp-cardmenu';
    menu.setAttribute('role', 'menu');
    menu.innerHTML = camp.state === 'draft'
      ? `<button type="button" data-menu-delete-draft>Delete draft</button>`
      : prePublish
        ? `<button type="button" data-menu-delete>Delete campaign</button>`
        : `<button type="button" data-menu-archive>Archive campaign</button>`;
    host.appendChild(menu);
    DeskV1Kit.placePopover(menu, triggerEl);
    const close = () => { menu.remove(); document.removeEventListener('click', closer); };
    const closer = (e) => { if (!menu.contains(e.target) && e.target !== triggerEl) close(); };
    setTimeout(() => document.addEventListener('click', closer), 0);

    // e.stopPropagation() here (not just on the trigger) matters when this
    // menu mounts inside a clickable card (Home): close() detaches `menu`
    // from `host` synchronously, so by the time this click would otherwise
    // bubble to the card's own listener, e.target's ancestor chain no
    // longer reaches .desk-v1-camp-card-more — closest() can't find it, and
    // the click fell through to the card's navigate handler. Stopping
    // propagation at the source doesn't depend on DOM structure surviving
    // the handler that runs first.
    const delDraftBtn = menu.querySelector('[data-menu-delete-draft]');
    if (delDraftBtn) delDraftBtn.onclick = (e) => {
      e.stopPropagation();
      close();
      deskV1DeleteDraftCampaign(camp.id, opts.onDone);
    };
    const delBtn = menu.querySelector('[data-menu-delete]');
    if (delBtn) delBtn.onclick = (e) => {
      e.stopPropagation();
      close();
      DeskV1Kit.openConfirmSheet({
        title: `Delete “${camp.plan.title}”?`,
        body: 'This removes the campaign and its draft content. Nothing here has published yet, so there is nothing on any platform to clean up. You can undo right after.',
        confirmLabel: 'Delete', cancelLabel: 'Cancel',
        onConfirm: () => _deleteCampaignCascade(camp, opts.onDone),
      });
    };
    const archBtn = menu.querySelector('[data-menu-archive]');
    if (archBtn) archBtn.onclick = (e) => {
      e.stopPropagation();
      close();
      DeskV1Kit.openConfirmSheet({
        title: `Archive “${camp.plan.title}”?`,
        body: 'Archiving stops future activity on this campaign and keeps its results and receipts. It does not remove any posts already on a platform. You can undo right after.',
        confirmLabel: 'Archive', cancelLabel: 'Cancel',
        onConfirm: () => _archiveCampaign(camp, opts.onDone),
      });
    };
  }
  window.deskV1OpenCampaignMoreMenu = deskV1OpenCampaignMoreMenu;

  // Dave's review (2026-09-28): `new Date('2026-10-20')` parses a bare
  // YYYY-MM-DD as UTC midnight, which rolls back to the previous local day
  // in a host timezone behind UTC ("Oct 19"). Calendar days, not instants —
  // parse as a LOCAL date (same fix as desk-v1-rules.js's own copy).
  function _localDateFromISO(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
    return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(iso);
  }
  function _fmtDate(iso) {
    try { return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(_localDateFromISO(iso)); }
    catch (e) { return iso; }
  }

  // ────────────────────────────────────────────────────────────────────────
  // Map stepper (R2-3, IA revision 2 §3/§4.1) — replaces the old
  // Content · Conversations · Results tab strip with the six-stop map
  // (`DeskV1Kit.MAP_STOPS`: how/goal/what/where/when/launch). Every stop is
  // clickable (§3 table: "guided, never locked") — a click both switches the
  // panel IN PLACE (same `deskV1GotoCampaignPanel` contract the old tabs
  // used) and records the campaign's current position (`camp.map.stop`),
  // which is what the project page's draft card + "Continue" read (§3
  // "Continue lands on that stop", desk-v1-project.js `_draftCardLabel`).
  // `conversations` is no longer a stop here (§6: moves into the Engagement
  // menu at R2-12) — its route stays reachable exactly as before through
  // PANEL_ALIASES/deep links, just not from this strip.
  // ────────────────────────────────────────────────────────────────────────
  function _campMap(camp) {
    // A pre-R2-3 fixture, or a draft that predates `deskV1CreateDraftCampaign`
    // seeding `map.stop: 'how'` (the Brief, R2-18) — falls back the same way
    // either way.
    if (!camp.map) camp.map = { stop: 'how', done: [] };
    return camp.map;
  }

  function _gotoMapStop(camp, stop) {
    _campMap(camp).stop = stop;
    window.deskV1GotoCampaignPanel(stop, { campaignId: camp.id });
  }

  // §3 table: "✓ done · ● you are here · ○ not started · ⚠ needs you (each
  // glyph + word, never colour alone)". The visible word on each stop button
  // is the stop's own name (Goal/How/...); the state is the glyph that
  // precedes it, with the state spelled out in `title`/`data-state` for
  // anything that needs it unambiguous (screen reader, smoke assertion) —
  // same "glyph conveys state, name identifies the stop" split a numbered
  // wizard step normally uses.
  const _STOP_STATE_GLYPH = { done: '✓', here: '●', needs_you: '⚠', not_started: '○' };
  const _STOP_STATE_WORD = { done: 'Done', here: 'You are here', needs_you: 'Needs you', not_started: 'Not started' };

  // "here" is read off `params.panel` — the panel actually on screen — not
  // `map.stop` (that field is only the Draft resume cursor `_gotoMapStop`
  // writes; `_renderCampaignSkeleton`'s own comment is explicit that a
  // non-draft campaign's `map.stop` is NOT a resume cursor, so a deep link
  // via PANEL_ALIASES — which sets `params.panel` without touching
  // `map.stop` — must still highlight the stop it actually landed on).
  function _stopState(stop, currentPanel, map, missingStops) {
    if (currentPanel === stop) return 'here';
    // R2-11: a stop with an unmet Launch bound reads ⚠ even after Next marked
    // it done — "✓" would tell the user the very thing Start is refusing over
    // is fine.
    if (missingStops.has(stop)) return 'needs_you';
    if ((map.done || []).includes(stop)) return 'done';
    return 'not_started';
  }

  function deskV1FillCampaignTabStrip(el, params) {
    const camp = _campaign(params.campaignId);
    if (!camp) { el.innerHTML = ''; return; }
    const map = _campMap(camp);
    const project = _project(camp.projectId);
    const missing = _launchMissing(camp, project).missing;
    const missingStops = new Set(missing.map((m) => m.stop));
    const stopsHTML = DeskV1Kit.MAP_STOPS.map((stop) => {
      const state = _stopState(stop, params.panel, map, missingStops);
      const word = DeskV1Kit.MAP_STOP_WORDS[stop];
      return `<button type="button" class="desk-v1-map-stop" data-stop="${esc(stop)}" data-state="${esc(state)}" aria-current="${state === 'here'}" title="${esc(word)} — ${esc(_STOP_STATE_WORD[state])}">` +
        `<span class="desk-v1-map-stop-glyph" aria-hidden="true">${_STOP_STATE_GLYPH[state]}</span>` +
        `<span class="desk-v1-map-stop-word">${esc(word)}</span></button>`;
    }).join('');
    // R2-3b: a Draft's summary bar is gone (see deskV1FillCampaignSummary), so
    // its More menu (Delete draft) lives here, at the stepper's right edge —
    // outside `.desk-v1-map-stops`, whose overflow-x would clip the dropdown.
    const moreHTML = camp.state === 'draft'
      ? `<div class="desk-v1-camp-card-more desk-v1-map-more"><button type="button" class="desk-v1-camp-card-morebtn" data-camp-more-btn aria-haspopup="menu" aria-label="More actions">&#8942;</button></div>`
      : '';
    // Batch 2 (Ron, phone): the lone ⋮ read as dead space, so a Draft also
    // carries a plain 'Discard draft' text action beside the state chip. Same
    // deskV1DeleteDraftCampaign + Undo as the menu item; no new delete path.
    const discardHTML = camp.state === 'draft'
      ? `<button type="button" class="desk-v1-map-discard" data-discard-draft>Discard draft</button>` : '';
    el.innerHTML = `
      <div class="desk-v1-map-tabs" role="tablist">
        ${DeskV1Kit.stateLabelHTML(camp.state, { className: 'desk-v1-map-pill' })}
        ${discardHTML}
        <div class="desk-v1-map-stops">${stopsHTML}</div>
        ${moreHTML}
      </div>`;
    el.querySelectorAll('[data-stop]').forEach((btn) => {
      btn.onclick = () => _gotoMapStop(camp, btn.getAttribute('data-stop'));
    });
    const afterDraftAction = (result) => {
      if (result === 'deleted' && typeof window.deskV1PopTo === 'function') window.deskV1PopTo('home');
      else if (typeof window.deskV1Render === 'function') window.deskV1Render();
    };
    const moreBtn = el.querySelector('[data-camp-more-btn]');
    if (moreBtn) moreBtn.onclick = (e) => {
      e.stopPropagation();
      deskV1OpenCampaignMoreMenu(moreBtn, camp.id, { onDone: afterDraftAction });
    };
    const discardBtn = el.querySelector('[data-discard-draft]');
    if (discardBtn) discardBtn.onclick = (e) => { e.stopPropagation(); deskV1DeleteDraftCampaign(camp.id, afterDraftAction); };
    // ≤960px the stops scroll sideways (desk-v1.css); a tab strip rebuilt on
    // every stop change would snap back to the first stop, so recentre the
    // current one (frame 10: "current stop in view").
    const stopsEl = el.querySelector('.desk-v1-map-stops');
    const hereBtn = el.querySelector('.desk-v1-map-stop[data-state="here"]');
    if (stopsEl && hereBtn && stopsEl.scrollWidth > stopsEl.clientWidth) {
      const sr = stopsEl.getBoundingClientRect(); const br = hereBtn.getBoundingClientRect();
      stopsEl.scrollLeft += (br.left - sr.left) - (sr.width - br.width) / 2;
    }
  }

  // Movement (§3 table: "`Next: <stop> ›` / `‹ Back` at the foot of each
  // stop"). Draft only, per R2-3a scope — Proposed/Active/Paused/Completed/
  // Archived move solely through the stop buttons above (free, no gate); the
  // fuller table also gives Proposed the same Next/Back, left for whichever
  // later ticket wires Proposed's own map behaviour (§8 doesn't test it here).
  function deskV1FillCampaignMapFoot(el, params) {
    const camp = _campaign(params.campaignId);
    if (!camp || camp.state !== 'draft') { el.innerHTML = ''; return; }
    const map = _campMap(camp);
    const idx = DeskV1Kit.MAP_STOPS.indexOf(map.stop);
    const backStop = idx > 0 ? DeskV1Kit.MAP_STOPS[idx - 1] : null;
    const nextStop = (idx >= 0 && idx < DeskV1Kit.MAP_STOPS.length - 1) ? DeskV1Kit.MAP_STOPS[idx + 1] : null;
    if (!backStop && !nextStop) { el.innerHTML = ''; return; }
    el.innerHTML = `
      <div class="desk-v1-map-foot-nav">
        ${backStop ? `<button type="button" class="desk-v1-map-back" data-map-back>‹ Back</button>` : ''}
        ${nextStop ? `<button type="button" class="desk-v1-map-next" data-map-next>Next: ${esc(DeskV1Kit.MAP_STOP_WORDS[nextStop])} ›</button>` : ''}
      </div>`;
    const backBtn = el.querySelector('[data-map-back]');
    if (backBtn) backBtn.onclick = () => _gotoMapStop(camp, backStop);
    const nextBtn = el.querySelector('[data-map-next]');
    if (nextBtn) nextBtn.onclick = () => {
      const doneArr = map.done || (map.done = []);
      if (!doneArr.includes(map.stop)) doneArr.push(map.stop);
      _gotoMapStop(camp, nextStop);
    };
  }

  // R2-6: the bounds shape `DeskV1Kit.boundsWiden`/`nextBoundsHash` compare —
  // pulled from wherever each dimension actually lives on a campaign today
  // (`plan.accounts`/`plan.cadence`/`plan.end`, the top-level `term`, and
  // the shared `how.budget` object). Kept local to this file rather than
  // exported from kit.js: kit's own comment on `boundsWiden` says it takes
  // "the canonical bounds object" as a given, never how to build one from a
  // campaign — that assembly is a caller concern, and this is its only
  // caller so far.
  function _currentBounds(camp) {
    const plan = camp.plan || {};
    return {
      accounts: plan.accounts || [],
      cadence: plan.cadence || {},
      end: plan.end || {},
      term: { ends: camp.term && camp.term.ends },
      budget: (plan.how && plan.how.budget) || { source: 'none' },
    };
  }

  // R2-2g: the project is required to start and can change until then.
  function _projectEditable(camp) { return camp.state === 'draft' || camp.state === 'proposed'; }

  // validatePlan's bounds plus the two that only exist once a project does
  // (R2-2g, Ron 2026-09-30: the project is picked at Launch): no project at
  // all, and a plan that doesn't fit the picked project — cadence over its
  // per-account ceiling, or an account it hasn't connected. Only for a
  // not-yet-started campaign; a running one keeps validatePlan's clamp.
  //
  // R2-11 adds the two bounds the kit's own gate never saw on a real
  // campaign: the kit reads `plan.term`, but a term lives on `camp.term`, so
  // it is overlaid here (and its fix is the When stop's Term field, not
  // Launch itself); and the goal's target + measurement source (§9 Q1) are
  // read off `camp.goal`, falling back to the legacy `plan.goal` the R0
  // fixtures still carry (`outcome`/`target`/`tracked`). A goal nobody has
  // started (a fresh draft) is not gated, same as the kit and the server
  // mirror (`mc/desk.py::_start_gate_problems`).
  function _goalReading(camp) {
    const g = camp.goal || {};
    const pg = (camp.plan && camp.plan.goal) || {};
    const metric = g.metric || pg.outcome || '';
    const target = g.target != null ? g.target : (pg.target != null ? pg.target : null);
    const source = g.source || (pg.tracked ? 'manual' : '');
    return { metric, target, source, started: !!(metric || target != null) };
  }
  function _goalPhrase(r) {
    return [r.target != null ? r.target : '', r.metric].filter((x) => x !== '').join(' ');
  }
  function _launchMissing(camp, project) {
    // A campaign that has not started has no term yet: Start will open one
    // from today to `plan.end.date`, so that is the span the 90-day cap judges.
    const prospective = camp.term || (camp.plan && camp.plan.end && camp.plan.end.date ? { starts: _isoToday(), ends: camp.plan.end.date } : undefined);
    const plan = Object.assign({}, camp.plan, { term: prospective });
    const planResult = DeskV1Kit.validatePlan(plan, project) || { ok: true, missing: [] };
    planResult.missing.forEach((m) => { if (m.bound === 'term') m.stop = 'when'; });
    if (!_projectEditable(camp)) return planResult;
    const extra = [];
    const goal = _goalReading(camp);
    if (goal.started && goal.target == null) {
      extra.push({ bound: 'goal', stop: 'goal', label: 'target for the goal', detail: 'no target', inline: `${goal.metric || 'goal'}, no target` });
    } else if (goal.started && !goal.source) {
      extra.push({ bound: 'goal', stop: 'goal', label: 'measurement source for the goal', detail: 'not tracked', inline: `${_goalPhrase(goal)}, not tracked`, action: 'Set a source' });
    }
    if (!project) {
      extra.push({ bound: 'project', stop: 'launch', label: 'Project', detail: 'pick one' });
    } else {
      const plan = camp.plan || {};
      const presence = project.presence || {};
      const connected = (presence.accounts || []).map((a) => a.channel_id);
      const offProject = (plan.accounts || []).filter((id) => !connected.includes(id));
      if (offProject.length) {
        extra.push({ bound: 'accounts', stop: 'where', label: 'accounts', detail: `${offProject.map((id) => (_channel(id) || {}).label || id).join(', ')} not connected to ${project.name}` });
      }
      const perWeek = plan.cadence && plan.cadence.per_week;
      const eff = planResult.effective || {};
      if (perWeek != null && eff.cadence_from_project && eff.cadence_per_week != null && perWeek > eff.cadence_per_week) {
        extra.push({ bound: 'cadence', stop: 'when', label: 'cadence', detail: `${perWeek}/wk is over ${project.name}'s ceiling of ${eff.cadence_per_week}/wk` });
      }
    }
    const missing = extra.concat(planResult.missing.filter((m) => !extra.some((x) => x.bound === m.bound)));
    return { ok: missing.length === 0, missing, effective: planResult.effective };
  }

  // ⑥ Launch (§4.1 row): validatePlan gates Start, each missing bound links
  // to the stop that fixes it (`missing[].stop`, kit.js R2-1). Reuses
  // desk-v1-rules.js's existing Start sheet (`deskV1OpenStartSheet`) rather
  // than a second Start flow.
  //
  // R2-11 (frame 8): the page IS the review. A Bounds table with exactly the
  // eight rows the approval covers; a missing item shows inline on its own
  // row AND in the sentence under the disabled Start; a waiting agent
  // question is a `Needs your answer` card in the panel's right column. Once
  // the campaign has started the same table stays (same bounds shown) under
  // a Live block: Live since, the term, the approval on file, Pause/Resume
  // and — for a long-horizon goal — `Renew term`.
  //
  // The Awaiting-approval branch is R2-6's: `camp.approval.bounds` vs the live
  // plan, so a How-stop budget widen on a running campaign is visible here. A
  // campaign with no `approval` recorded yet (draft, or a fixture that
  // predates it) skips the check exactly as before.
  const _BOUND_ROW = { accounts: 'accounts', cadence: 'cadence', end: 'term', term: 'term', goal: 'goal', how_budget: 'budget' };
  const _DERIVED_COST_PER_POST = 0.2; // §5.2: post cap × $0.20 (the link-post rate, the dearest case)
  const _MS_DAY = 86400000;

  function _fmtDateLong(iso) {
    try { return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(_localDateFromISO(iso)); }
    catch (e) { return iso; }
  }
  function _money(n) { return `$${(Math.round(n * 100) / 100).toFixed(2).replace(/\.00$/, '')}`; }

  function _launchRows(camp, project) {
    const plan = camp.plan || {};
    const presence = (project && project.presence) || {};
    const accounts = plan.accounts || [];
    const accountsText = accounts.length
      ? accounts.map((id) => {
        const ch = _channel(id);
        const acct = (presence.accounts || []).find((a) => a.channel_id === id);
        const label = ch ? ch.label : id;
        return acct && acct.voice ? `${label} (${acct.voice})` : label;
      }).join(' · ')
      : '—';
    const eff = (DeskV1Kit.validatePlan(plan, project) || {}).effective || {};
    const gaps = accounts.map((id) => ((presence.ceilings || {})[id] || {}).min_gap_h).filter((g) => g != null);
    const cadenceText = `${eff.cadence_per_week != null ? eff.cadence_per_week : '—'} / wk${gaps.length ? ` · ${Math.max.apply(null, gaps)}h` : ''}`;
    const ends = (camp.term && camp.term.ends) || (plan.end && plan.end.date) || null;
    const cap = plan.end && plan.end.post_cap;
    const termText = ends
      ? `${camp.term && camp.term.index > 1 ? `term ${camp.term.index} · ` : ''}ends ${_fmtDateLong(ends)} (${cap ? `${cap} post cap` : 'no post cap'})`
      : (cap ? `${cap} post cap (no end date)` : '—');
    // Derived ceiling: the post cap, else cadence × the weeks the term covers.
    const perWeek = eff.cadence_per_week;
    const startMs = camp.term && camp.term.starts ? _localDateFromISO(camp.term.starts).getTime() : Date.now();
    const weeks = ends ? Math.max(0, (_localDateFromISO(ends).getTime() - startMs) / (7 * _MS_DAY)) : 0;
    const posts = cap || (perWeek != null && ends ? Math.ceil(perWeek * weeks) : null);
    const derived = posts != null ? posts * _DERIVED_COST_PER_POST : null;
    const spendText = derived != null ? `derived · up to ${_money(derived)} (${posts} posts × ${_money(_DERIVED_COST_PER_POST)} link rate)` : 'derived · needs a cadence and an end date';
    const budget = (plan.how && plan.how.budget) || (camp.how && camp.how.budget) || { source: 'none' };
    let budgetText = 'none set · spend ceiling is the derived one';
    if (budget.source === 'project') budgetText = `${_money(budget.amount || 0)} earmarked from ${project ? project.name : 'the project'}`;
    else if (budget.source === 'own') budgetText = `${_money(budget.amount || 0)} own budget`;
    if (budget.source !== 'none' && derived != null && (budget.amount || 0) < derived) budgetText += ` · below the derived ${_money(derived)}, publishing would stop early`;
    const goal = _goalReading(camp);
    const goalText = goal.started
      ? `${_goalPhrase(goal)} · ${goal.source ? (goal.source === 'manual' ? 'manual entry' : goal.source) : 'not tracked'}`
      : 'no goal set';
    const stopText = ends ? `Ends ${_fmtDateLong(ends)} · Pause stops it at any time` : (cap ? `Ends after ${cap} posts · Pause stops it at any time` : 'Pause stops it at any time');
    return [
      ['accounts', 'Accounts + voices', accountsText],
      ['cadence', 'Cadence / min gap', cadenceText],
      ['term', 'Term', termText],
      ['scope', 'Source scope', plan.audience || (camp.subject && camp.subject.label) || '—'],
      ['spend', 'Spend ceiling', spendText],
      ['budget', 'Budget', budgetText],
      ['goal', 'Goal / measurement', goalText],
      ['stop', 'Stop conditions', stopText],
    ];
  }

  function _launchNeedsSentence(missing) {
    const names = missing.map((m) => (m.bound === 'project' ? 'a project' : m.label));
    return `Set ${missing.length} thing${missing.length === 1 ? '' : 's'} first: ${names.join(', ')}.`;
  }

  // The agent's one waiting question (fixtures: `proposedExtras[camp].blocker`).
  // "Answered" is a record on the campaign (`camp.answers`), not DOM state, so
  // it survives a repaint and the What stop's own blocker card reads it too.
  function _pendingQuestion(camp) {
    const blocker = ((_fx().proposedExtras || {})[camp.id] || {}).blocker;
    if (!blocker) return null;
    return (camp.answers || []).some((a) => a.id === blocker.id) ? null : blocker;
  }
  function _answerQuestion(camp, blocker, answer, repaint) {
    DeskV1Kit.commandBus.run({
      label: `Answered ${DeskV1Kit.deskAgentName({ project: _project(camp.projectId), campaign: camp })}’s question: “${answer.label}”`,
      do: () => { (camp.answers = camp.answers || []).push({ id: blocker.id, question: blocker.question, answer_id: answer.id, answer: answer.label, at: new Date().toISOString() }); repaint(); },
      undo: () => { camp.answers = (camp.answers || []).filter((a) => a.id !== blocker.id); repaint(); },
    });
  }
  // The Start sheet (desk-v1-rules.js) judges with the SAME gate as this page,
  // so the Proposed summary's own Start button cannot walk past it.
  window.deskV1LaunchMissing = _launchMissing;
  window.deskV1CampaignPendingQuestion = _pendingQuestion;
  window.deskV1CampaignAnswerQuestion = _answerQuestion;

  function _approvalLine(camp) {
    const a = camp.approval;
    if (!a || !a.bounds) return '';
    const hash = a.bounds_hash || DeskV1Kit.computeBoundsHash(a.bounds);
    const term = (camp.term && camp.term.index) || 1;
    return `Approval on file: term ${term}${a.at ? `, approved ${_fmtDateLong(String(a.at).slice(0, 10))}` : ''} · bounds ${hash}`;
  }

  // Renew (§9 Q2: a long-horizon goal renews; it never holds one open-ended
  // approval). The next term starts where this one ended and runs to the goal
  // deadline or 90 days, whichever is sooner; the bounds shown are unchanged
  // but the approval is a NEW record (`camp.approvals[]`, `camp.approval`).
  function _termEnded(camp) {
    return !!(camp.term && camp.term.ends && _localDateFromISO(camp.term.ends).getTime() <= Date.now());
  }
  function _renewTerm(camp, repaint) {
    const prevTerm = camp.term;
    const prevTerms = camp.terms;
    const prevApproval = camp.approval;
    const prevApprovals = camp.approvals;
    const deadline = camp.goal && camp.goal.deadline;
    const startsMs = _localDateFromISO(prevTerm.ends).getTime();
    const capMs = startsMs + DeskV1Kit.MAX_TERM_DAYS * _MS_DAY;
    const endMs = deadline ? Math.min(capMs, _localDateFromISO(deadline).getTime()) : capMs;
    if (!(endMs > startsMs)) { DeskV1Kit.toast('The goal deadline has passed, so there is no next term to renew.'); return; }
    const iso = (ms) => { const d = new Date(ms); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
    const next = { index: (prevTerm.index || 1) + 1, starts: iso(startsMs), ends: iso(endMs), post_cap: prevTerm.post_cap != null ? prevTerm.post_cap : null };
    DeskV1Kit.commandBus.run({
      label: `Renewed “${camp.plan.title}”: term ${next.index}`,
      do: () => {
        camp.terms = (prevTerms && prevTerms.length ? prevTerms : [prevTerm]).concat([next]);
        camp.term = next;
        const bounds = _currentBounds(camp);
        // Same bounds, new approval: the record is what a new term needs, the
        // bounds it holds are exactly what the campaign already runs on.
        camp.approval = { bounds, bounds_hash: DeskV1Kit.computeBoundsHash(bounds), at: new Date().toISOString(), term: next.index };
        camp.approvals = (prevApprovals || (prevApproval ? [prevApproval] : [])).concat([camp.approval]);
        repaint();
      },
      undo: () => { camp.term = prevTerm; camp.terms = prevTerms; camp.approval = prevApproval; camp.approvals = prevApprovals; repaint(); },
    });
  }

  // §5.2: "A project budget cut below live earmarks clamps those campaigns
  // (narrowing rule) and logs it". Called by Presence after it applies a
  // budget change. A clamp only ever lowers an earmark, so a running
  // campaign keeps its approval; the entry lands in `camp.log`, shown on the
  // Launch page. Campaigns are served in list order: earlier ones keep their
  // earmark, later ones absorb the cut.
  function _clampEarmarks(project) {
    const pool = project && project.presence && project.presence.budget && project.presence.budget.amount;
    if (pool == null) return [];
    const clamped = [];
    let used = 0;
    _campaigns().filter((c) => c.projectId === project.id && !['archived', 'completed'].includes(c.state)).forEach((c) => {
      const b = c.how && c.how.budget;
      if (!b || b.source !== 'project') return;
      const allowed = Math.max(0, pool - used);
      if ((b.amount || 0) > allowed) {
        const was = b.amount || 0;
        b.amount = allowed;
        (c.log = c.log || []).push({ at: new Date().toISOString(), text: `Earmark clamped from ${_money(was)} to ${_money(allowed)}: ${project.name}'s budget was cut to ${_money(pool)}.` });
        clamped.push(c);
      }
      used += b.amount || 0;
    });
    return clamped;
  }
  window.deskV1ClampEarmarks = _clampEarmarks;

  function _renderLaunchPanel(el, params, camp) {
    if (!camp) { el.innerHTML = '<div class="desk-v1-stub-inline">Campaign not found.</div>'; return; }
    if (camp.state !== 'draft' && camp.approval && camp.approval.bounds
        && DeskV1Kit.boundsWiden(camp.approval.bounds, _currentBounds(camp))) {
      el.innerHTML = `
        <div class="desk-v1-map-launch">
          <div class="desk-v1-map-launch-status desk-v1-map-launch-awaiting">⚠ Awaiting approval</div>
          <div class="desk-v1-stub-inline">A change since the last approval (Brief stop) widens what this campaign can do. An authorized user needs to approve it again before it takes effect.</div>
        </div>`;
      return;
    }
    const project = _project(camp.projectId);
    const result = _launchMissing(camp, project);
    // R2-2g: the project is chosen HERE, not up front. Missing "project" has
    // no row in the table (the select right above it is the fix); every other
    // missing bound shows inline on its own row. The select is editable while
    // the campaign hasn't started (draft / proposed), a read-only line after.
    const projectEditable = _projectEditable(camp);
    const missingByRow = {};
    result.missing.forEach((m) => { if (_BOUND_ROW[m.bound]) missingByRow[_BOUND_ROW[m.bound]] = m; });
    const rowsHTML = _launchRows(camp, project).map(([key, label, text]) => {
      const m = missingByRow[key];
      const valHTML = m
        ? `<span class="desk-v1-launch-warn">⚠ ${esc(m.inline || `${m.label}${m.detail ? `, ${m.detail}` : ''}`)}</span> · <button type="button" class="desk-v1-launch-fix" data-missing-stop="${esc(m.stop)}">${esc(m.action || `Fix in ${DeskV1Kit.MAP_STOP_WORDS[m.stop]}`)} ›</button>`
        : esc(text);
      return `<div class="desk-v1-launch-row" data-launch-row="${esc(key)}"${m ? ' data-missing="true"' : ''}><span class="desk-v1-launch-label">${esc(label)}</span><span class="desk-v1-launch-val">${valHTML}</span></div>`;
    }).join('');
    const projectMissing = result.missing.find((m) => m.bound === 'project');
    const projectHTML = projectMissing
      ? `<ul class="desk-v1-map-launch-missing"><li class="desk-v1-map-launch-missing-project" data-missing-project>${esc(projectMissing.label)} — ${esc(projectMissing.detail)}</li></ul>`
      : '';

    const question = projectEditable ? _pendingQuestion(camp) : null;
    const questionHTML = question
      ? `<aside class="desk-v1-launch-side"><div class="desk-v1-launch-question" data-needs-answer data-blocker-id="${esc(question.id)}">
          <div class="desk-v1-launch-question-head">⛔ Needs your answer</div>
          <div class="desk-v1-launch-question-q">${esc(question.question)}</div>
          <div class="desk-v1-launch-question-answers">${question.answers.map((a) => `<button type="button" class="desk-v1-launch-answer" data-answer-id="${esc(a.id)}">${esc(a.label)}</button>`).join('')}</div>
        </div></aside>`
      : '';

    let footHTML;
    if (projectEditable) {
      footHTML = `
        <button type="button" class="desk-v1-map-launch-start" data-map-start-btn ${result.ok ? '' : 'disabled'}>Start campaign</button>
        <div class="desk-v1-launch-need" data-launch-need${result.ok ? ' hidden' : ''}>${result.ok ? '' : esc(_launchNeedsSentence(result.missing))}</div>`;
    } else {
      const since = camp.startedAt ? String(camp.startedAt).slice(0, 10) : (camp.term && camp.term.starts) || null;
      const long = camp.goal && camp.goal.horizon === 'long' && camp.term;
      const ended = long && _termEnded(camp);
      const live = camp.state === 'paused' ? '⏸ Paused' : (camp.state === 'active' ? '● Live' : DeskV1Kit.stateLabel(camp.state).word);
      footHTML = `
        <div class="desk-v1-launch-live" data-launch-live>
          <div class="desk-v1-launch-live-head">${esc(live)}${since ? `${camp.state === 'paused' ? ' · live' : ''} since ${esc(_fmtDateLong(since))}` : ''}</div>
          ${camp.term ? `<div class="desk-v1-rules-hint" data-launch-term>Term ${esc(camp.term.index || 1)}: ${esc(_fmtDateLong(camp.term.starts))} to ${esc(_fmtDateLong(camp.term.ends))}</div>` : ''}
          ${_approvalLine(camp) ? `<div class="desk-v1-rules-hint" data-launch-approval>${esc(_approvalLine(camp))}</div>` : ''}
          <div class="desk-v1-launch-live-actions">
            ${camp.state === 'paused'
              ? '<button type="button" class="desk-v1-camp-pause-btn" data-launch-resume>▶ Resume</button>'
              : `<button type="button" class="desk-v1-camp-pause-btn" data-launch-pause ${camp.state !== 'active' ? 'disabled' : ''}>⏸ Pause</button>`}
            ${long ? `<button type="button" class="desk-v1-camp-pause-btn" data-renew-term ${ended ? '' : 'disabled'}>Renew term</button>` : ''}
          </div>
          ${long && !ended ? `<div class="desk-v1-rules-hint">Renew opens when this term ends, ${esc(_fmtDateLong(camp.term.ends))}.</div>` : ''}
        </div>`;
    }

    el.innerHTML = `
      <div class="desk-v1-launch${question ? ' desk-v1-launch-has-side' : ''}">
        <div class="desk-v1-launch-main">
          ${projectEditable ? '' : `<div class="desk-v1-rules-hint" data-launch-project-ro>Project: ${esc(project ? project.name : 'none')}</div>`}
          ${projectHTML}
          <div class="desk-v1-launch-bounds" role="table" aria-label="Bounds">
            <div class="desk-v1-launch-bounds-title">Bounds</div>
            ${rowsHTML}
          </div>
          ${footHTML}
          ${(camp.log || []).length ? `<div class="desk-v1-launch-log" data-launch-log><div class="desk-v1-launch-bounds-title">Log</div>${camp.log.map((l) => `<div class="desk-v1-rules-hint">${esc(String(l.at).slice(0, 10))} · ${esc(l.text)}</div>`).join('')}</div>` : ''}
        </div>
        ${questionHTML}
      </div>`;
    if (projectEditable && typeof window.deskV1MountProjectField === 'function') {
      window.deskV1MountProjectField(el.querySelector('.desk-v1-launch-main'), camp);
    }
    const repaint = () => {
      _renderLaunchPanel(el, params, camp);
      const summaryEl = document.getElementById('desk-v1-camp-summary');
      if (summaryEl) deskV1FillCampaignSummary(summaryEl, params);
      const stripEl = document.getElementById('desk-v1-camp-tabstrip');
      if (stripEl) deskV1FillCampaignTabStrip(stripEl, params);
    };
    el.querySelectorAll('[data-missing-stop]').forEach((btn) => {
      btn.onclick = () => {
        const stop = btn.getAttribute('data-missing-stop');
        _gotoMapStop(camp, stop);
        // "Set a source ›" lands ON the field, not just on the stop.
        if (stop === 'goal') {
          const field = document.querySelector('[data-goal-field="source"]');
          if (field) field.focus();
        }
      };
    });
    const startBtn = el.querySelector('[data-map-start-btn]');
    if (startBtn && result.ok) startBtn.onclick = () => window.deskV1OpenStartSheet(camp.id);
    el.querySelectorAll('[data-answer-id]').forEach((btn) => {
      btn.onclick = () => _answerQuestion(camp, question, question.answers.find((a) => a.id === btn.dataset.answerId), repaint);
    });
    const pauseBtn = el.querySelector('[data-launch-pause]');
    if (pauseBtn) pauseBtn.onclick = () => {
      if (camp.state !== 'active') return;
      const prev = camp.state;
      DeskV1Kit.commandBus.run({
        label: `Paused “${camp.plan.title}”`,
        do: () => { camp.state = 'paused'; repaint(); },
        undo: () => { camp.state = prev; repaint(); },
      });
    };
    const resumeBtn = el.querySelector('[data-launch-resume]');
    if (resumeBtn) resumeBtn.onclick = () => _openResumeSheet(camp, document.getElementById('desk-v1-camp-summary'), params, repaint);
    const renewBtn = el.querySelector('[data-renew-term]');
    if (renewBtn && !renewBtn.disabled) renewBtn.onclick = () => _renewTerm(camp, repaint);
  }

  // ────────────────────────────────────────────────────────────────────────
  // ③ What (R2-7). The list, the CONTENT TYPES tray and the source-first
  // create-cards live in desk-v1-what.js (`deskV1FillWhat`); this file keeps
  // the pieces of the old Content tab that What still leans on — the piece ⋯
  // menu and its commands, the Suggest banner, and the Posy selection — and
  // hands them over through `window.deskV1CampaignWhatBridge`. The grouped
  // list, channel filter, List/Calendar toggle, `+ New piece` and the Material
  // Add tray are retired (calendar is ⑤ When; new pieces come from the tray).
  // ────────────────────────────────────────────────────────────────────────
  function deskV1FillCampaignTabBody(el, params) {
    const camp = _campaign(params.campaignId);
    // shell.js only falls through to this hook for 'what' (the default) and
    // 'launch' (no dedicated renderer of its own) — 'goal'/'when'/'how'/
    // 'where'/'conversations' are all handled before reaching here.
    if (params.panel === 'launch') { _renderLaunchPanel(el, params, camp); return; }
    // Same backward-compatible seam as deskV1FillCampaignSummary above: a
    // Proposed campaign's Content tab shows Posy's proposed pieces plus a
    // blocker card and "? Assumed" popovers (§3.5), not the ordinary grouped
    // list — T2b's own renderer, undefined until desk-v1-rules.js loads.
    if (camp && camp.state === 'proposed' && typeof window.deskV1FillProposedContent === 'function') {
      window.deskV1FillProposedContent(el, params, camp);
      return;
    }
    const st = _ensureState(params.campaignId);
    st.el = el;
    if (!camp) { el.innerHTML = '<div class="desk-v1-stub-inline">Campaign not found.</div>'; return; }
    if (typeof window.deskV1FillWhat !== 'function') { el.innerHTML = '<div class="desk-v1-stub-inline">What is not loaded.</div>'; return; }
    window.deskV1FillWhat(el, params, camp);
  }

  // Repaints the mounted What body after a fixture change made here (card ⋯
  // menu commands, Accept all, the Suggest task).
  function _renderTabBody() {
    if (typeof window.deskV1RepaintWhat === 'function') window.deskV1RepaintWhat();
  }
  function _renderList() { _renderTabBody(); }

  window.deskV1CampaignWhatBridge = {
    runPrimary: (fam, camp) => _runPrimaryAction(fam, camp),
    openCardMenu: (trigger, fam, camp) => _openCardMenu(trigger, fam, camp),
    setSelection: (scope, id, label) => _setSelection(scope, id, label),
    suggestedBannerHTML: (camp) => _suggestedWhatBannerHTML(camp),
    acceptSuggested: (camp) => _acceptSuggestedWhat(camp),
  };

  // R2-6: the ② How stop's "Suggest What / When / Where" task writes
  // `camp.how.suggested.what` — an array of draft piece proposals, none of
  // them real content yet. This banner is the ONLY place that offer is
  // visible on the Content tab; "Accept all" is the one action R2-6 builds
  // for it (per-item accept/reject is R2-9's job, same as the rest of ④'s
  // real UI — this ticket only has to prove the suggestion reaches ③).
  function _suggestedWhatBannerHTML(camp) {
    const items = camp.how && camp.how.suggested && camp.how.suggested.what;
    if (!items || !items.length) return '';
    return `
      <div class="desk-v1-camp-suggested-banner">
        <span>${esc(items.length)} suggested</span>
        <button type="button" class="btn-secondary" data-suggested-accept-all>Accept all</button>
      </div>
      <div class="desk-v1-suggested-list">${items.map((it, i) => `
        <div class="desk-v1-suggested-item" data-suggested-item="${i}">
          <span class="desk-v1-suggested-title">${esc(it.title)}</span>${DeskV1Kit.becauseChipsHTML(it.because, camp.projectId)}
        </div>`).join('')}</div>`;
  }

  function _acceptSuggestedWhat(camp) {
    const items = (camp.how && camp.how.suggested && camp.how.suggested.what) || [];
    if (!items.length) return;
    const created = items.map((it, i) => ({
      id: 'fam-suggest-' + Date.now().toString(36) + '-' + i,
      campaignId: camp.id, kind: 'post', title: it.title,
      versions: [{ id: 'v-suggest-' + Date.now().toString(36) + '-' + i, channelId: it.channelId || null, state: 'planned', revision: 0 }],
    }));
    DeskV1Kit.commandBus.run({
      label: `Accepted ${created.length} suggested piece${created.length === 1 ? '' : 's'}`,
      do: () => {
        created.forEach((fam) => _fx().families.push(fam));
        camp.how.suggested.what = [];
        _renderTabBody();
      },
      undo: () => {
        const arr = _fx().families;
        created.forEach((fam) => { const i = arr.findIndex((f) => f.id === fam.id); if (i >= 0) arr.splice(i, 1); });
        camp.how.suggested.what = items;
        _renderTabBody();
      },
    });
  }

  // R2-6: the Suggest task itself (`_HOW_SUGGEST_TEXT`, fired by
  // desk-v1-how.js driving the rightcol Posy box's real Send). Writes
  // DRAFT suggestions only (§4.2 item 3: "never commitments") — ③'s own
  // `_suggestedWhatBannerHTML`/`_acceptSuggestedWhat` above turn `what`
  // into real pieces; ⑤ When and ④ Where read `how.suggested.when`/`.where` directly
  // (desk-v1-calendar.js, desk-v1-shell.js's 'where' branch) since neither
  // has a dedicated accept flow yet (R2-9/R2-10). Only refreshes the
  // Content tab body if it's the one currently mounted AND currently the
  // active stop. `_st.el` is the SAME shared `#desk-v1-camp-tabbody` node
  // every stop paints into (shell.js `_renderCampaignPanel`), so once
  // Content has rendered once, `_st.el` stays attached to the DOM even
  // while a different stop (e.g. How) is showing — "still in DOM" alone
  // can't tell them apart. Dave's follow-up (2e24880e review): the fix is
  // checking shell.js's own `dataset.panel` stamp on that node, so a
  // Suggest task resolving while parked on How repaints nothing instead of
  // clobbering How with Content-tab HTML.
  function _runSuggestTask(camp, project, posyBoxEl) {
    const plan = camp.plan || {};
    const accounts = plan.accounts || [];
    const title = plan.title || (camp.subject && camp.subject.label) || 'New campaign';
    camp.how = camp.how || {};
    // R2-17: every suggestion carries the agent's `because` (§10.3); the UI
    // resolves it against the confirmed playbook when it paints (kit
    // `resolveBecause`), so a stale or invented id never reaches a chip.
    const reply = (_fx().suggestReply) || {};
    const replyWhat = reply.what || [];
    const replyWhen = reply.when || {};
    const when = { label: `${(plan.cadence && plan.cadence.per_week) || 3}x/week`, because: replyWhen.because };
    // §5 data addendum: an agent's proposed slot is an `origin:'agent',
    // state:'suggested'` entry in `when.slots` (calendar.js R2-9). Re-running
    // Suggest replaces the still-suggested one; accepted ones stay.
    camp.when = camp.when || {};
    camp.when.slots = (camp.when.slots || []).filter((sl) => !(sl.origin === 'agent' && sl.state === 'suggested'));
    if (replyWhen.weekday != null) {
      const at = new Date();
      at.setDate(at.getDate() + 1 + ((replyWhen.weekday - at.getDay() - 1 + 7) % 7));
      const [hh, mm] = String(replyWhen.time || '09:00').split(':').map((x) => parseInt(x, 10));
      at.setHours(hh, mm || 0, 0, 0);
      const slot = { id: 'slot-agent-' + Date.now().toString(36), at: at.toISOString(), origin: 'agent', state: 'suggested', because: replyWhen.because };
      camp.when.slots.push(slot);
      when.slotId = slot.id;
    }
    camp.how.suggested = {
      what: [1, 2, 3].map((n) => ({
        title: `${title} — post ${n}`, channelId: accounts[(n - 1) % (accounts.length || 1)] || null,
        because: (replyWhat[n - 1] || {}).because,
      })),
      when,
      where: { channelId: accounts[0] || null, label: accounts[0] ? (_channel(accounts[0]) || {}).label || accounts[0] : null, because: (reply.where || {}).because },
    };
    DeskV1Kit.toast(`${DeskV1Kit.deskAgentName({ project, campaign: camp })} suggested 3 pieces, a cadence and a placement.`);
    if (_st && _st.campaignId === camp.id && _st.el && document.body.contains(_st.el) && _st.el.dataset.panel === 'what') _renderTabBody();
    if (posyBoxEl) DeskV1Kit.paintPosyReadyNoDiff(posyBoxEl);
  }

  function _fmtWhenShort(iso) {
    try {
      const cfg = (typeof _globalConfig !== 'undefined' && _globalConfig) || {};
      return new Intl.DateTimeFormat(undefined, { timeZone: cfg.user_timezone || undefined, weekday: 'short', hour: 'numeric', minute: '2-digit' }).format(new Date(iso));
    } catch (e) { return iso; }
  }
  function _primaryAction(fam) {
    const needsReview = fam.versions.find((v) => v.state === 'needs_review');
    if (needsReview) return { label: fam.kind === 'video' ? 'Watch & review' : 'Review', kind: 'review', versionId: needsReview.id };
    if (fam.kind === 'video') return { label: 'Open', kind: 'video' };
    return { label: 'Open', kind: 'open-stub' };
  }
  // IA5 (§2.4, §5 row IA5 ticket: "the content card's primary action opens
  // the piece"): every card, whatever its kind or state, opens the piece
  // page now — What hands off to review when a version needs it, How hands
  // off to the video director for a video piece, same as the piece page's
  // own facet actions. Replaces the old kind-branched review/video/toast
  // dispatch (the toast's "later ticket" is this one).
  function _runPrimaryAction(fam, camp) {
    const action = _primaryAction(fam);
    deskV1Nav('piece', { campaignId: camp.id, familyId: fam.id, projectId: camp.projectId, versionId: action.versionId || null });
  }

  // ── ⋯ menu (§3.2: "Add a channel version ▸ · Move to another channel ▸ ·
  // Duplicate · Skip · Archive"). Plain DOM menu, same shape as
  // desk-v1-review.js's _openMoreMenu (no full re-render, so it doesn't
  // fight an open submenu). ─────────────────────────────────────────────────
  function _openCardMenu(triggerEl, fam, camp) {
    const host = triggerEl.parentElement;
    const existing = document.querySelector('.desk-v1-camp-cardmenu');
    if (existing) existing.remove();
    if (host.querySelector(':scope > .desk-v1-camp-cardmenu')) return;
    const availableChannels = _channels().filter((ch) => !fam.versions.some((v) => v.channelId === ch.id));
    const menu = document.createElement('div');
    menu.className = 'desk-v1-camp-cardmenu';
    menu.setAttribute('role', 'menu');
    menu.innerHTML = `
      <button type="button" data-menu-add ${availableChannels.length ? '' : 'disabled'}>Add a channel version ▸</button>
      <button type="button" data-menu-move ${fam.versions.length ? '' : 'disabled'}>Move to another channel ▸</button>
      <button type="button" data-menu-dup>Duplicate</button>
      <button type="button" data-menu-skip>Skip</button>
      <button type="button" data-menu-archive>Archive</button>`;
    host.style.position = 'relative';
    host.appendChild(menu);
    DeskV1Kit.placePopover(menu, triggerEl);
    const close = () => { menu.remove(); document.removeEventListener('click', closer); };
    const closer = (e) => { if (!menu.contains(e.target) && e.target !== triggerEl) close(); };
    setTimeout(() => document.addEventListener('click', closer), 0);

    const addBtn = menu.querySelector('[data-menu-add]');
    if (addBtn && availableChannels.length) addBtn.onclick = () => {
      close();
      DeskV1Kit.addToMenu(triggerEl, availableChannels.map((ch) => ({ id: ch.id, label: ch.label })),
        (chId) => _addChannelVersion(fam, chId), { noAppendNew: true });
    };
    const moveBtn = menu.querySelector('[data-menu-move]');
    if (moveBtn && fam.versions.length) moveBtn.onclick = () => {
      close();
      const fromVersion = fam.versions.length === 1 ? fam.versions[0] : null;
      const pickFrom = (v) => {
        const targets = _channels().filter((ch) => ch.id !== v.channelId);
        DeskV1Kit.addToMenu(triggerEl, targets.map((ch) => ({ id: ch.id, label: ch.label })),
          (chId) => _moveToChannel(fam, v, chId), { noAppendNew: true });
      };
      if (fromVersion) pickFrom(fromVersion);
      else DeskV1Kit.addToMenu(triggerEl, fam.versions.map((v) => ({ id: v.id, label: (_channel(v.channelId) || {}).label || v.id })),
        (vId) => pickFrom(fam.versions.find((v) => v.id === vId)), { noAppendNew: true });
    };
    menu.querySelector('[data-menu-dup]').onclick = () => { close(); _duplicateFamily(fam, camp); };
    menu.querySelector('[data-menu-skip]').onclick = () => { close(); _skipFamily(fam); };
    menu.querySelector('[data-menu-archive]').onclick = () => { close(); _archiveFamily(fam); };
  }

  function _addChannelVersion(fam, channelId) {
    const ch = _channel(channelId);
    const v = { id: fam.id + '-v-' + Date.now().toString(36), channelId, state: 'drafting', revision: 0 };
    DeskV1Kit.commandBus.run({
      label: `Added a ${ch ? ch.label : channelId} version to “${fam.title}”`,
      do: () => { fam.versions.push(v); _renderList(_campaign(fam.campaignId)); },
      undo: () => { const i = fam.versions.indexOf(v); if (i >= 0) fam.versions.splice(i, 1); _renderList(_campaign(fam.campaignId)); },
    });
  }

  // §3.2: "Moving a version to another channel is only available through
  // ⋯ → Move to another channel ▸, and it confirms: 'Move from X to Y? The X
  // version will be archived.'" — window.confirm follows the exact
  // established precedent in desk-v1-calendar.js's own reschedule-approval
  // confirm rather than inventing a second modal component for one dialog.
  function _moveToChannel(fam, version, toChannelId) {
    const fromCh = _channel(version.channelId);
    const toCh = _channel(toChannelId);
    const proceed = window.confirm(`Move from ${fromCh ? fromCh.label : 'this channel'} to ${toCh ? toCh.label : 'the new channel'}? The ${fromCh ? fromCh.label : 'old'} version will be archived.`);
    if (!proceed) return;
    const prevState = version.state;
    const prevChannel = version.channelId;
    const newVersion = { id: fam.id + '-v-' + Date.now().toString(36), channelId: toChannelId, state: prevState === 'needs_review' ? 'drafting' : prevState, revision: version.revision };
    DeskV1Kit.commandBus.run({
      label: `Moved “${fam.title}” from ${fromCh ? fromCh.label : 'a channel'} to ${toCh ? toCh.label : 'a channel'}`,
      do: () => { version.state = 'archived'; fam.versions.push(newVersion); _renderList(_campaign(fam.campaignId)); },
      undo: () => { version.state = prevState; version.channelId = prevChannel; const i = fam.versions.indexOf(newVersion); if (i >= 0) fam.versions.splice(i, 1); _renderList(_campaign(fam.campaignId)); },
    });
  }

  function _duplicateFamily(fam, camp) {
    const dup = {
      id: fam.id + '-copy-' + Date.now().toString(36), campaignId: fam.campaignId, kind: fam.kind,
      title: fam.title + ' (copy)', wordCount: fam.wordCount,
      versions: [{ id: fam.id + '-copy-v1-' + Date.now().toString(36), channelId: null, state: 'drafting', revision: 0 }],
    };
    DeskV1Kit.commandBus.run({
      label: `Duplicated “${fam.title}”`,
      do: () => { _fx().families.push(dup); _renderList(camp); },
      undo: () => { const arr = _fx().families; const i = arr.findIndex((f) => f.id === dup.id); if (i >= 0) arr.splice(i, 1); _renderList(camp); },
    });
  }

  // Skip/Archive act on the whole card (every non-terminal version) — the
  // spec's §3.2 ⋯ menu lists them at card level, not per-version (T3 already
  // owns the per-version Skip/Archive inside review). Interpretation flagged
  // in the final report: the doc doesn't spell out card-level semantics for
  // a multi-version family.
  function _disposeFamily(fam, nextState, label) {
    const targets = fam.versions.filter((v) => !_TERMINAL_STATES.has(v.state));
    if (!targets.length) { DeskV1Kit.toast('Nothing to change — every version already concluded.'); return; }
    const prev = targets.map((v) => v.state);
    DeskV1Kit.commandBus.run({
      label: `${label} “${fam.title}”`,
      do: () => { targets.forEach((v) => { v.state = nextState; }); _renderList(_campaign(fam.campaignId)); },
      undo: () => { targets.forEach((v, i) => { v.state = prev[i]; }); _renderList(_campaign(fam.campaignId)); },
    });
  }
  function _skipFamily(fam) { _disposeFamily(fam, 'skipped', 'Skipped'); }
  function _archiveFamily(fam) { _disposeFamily(fam, 'archived', 'Archived'); }

  // ────────────────────────────────────────────────────────────────────────
  // Posy box (§3.4, right column). Scope label follows `_st.selection`,
  // updated by `_setSelection()` below whenever the campaign/a card is
  // selected. Suggestion + chips come from CAMPAIGN_SUGGESTIONS (T2a's own
  // fixture section) keyed by campaignId.
  // ────────────────────────────────────────────────────────────────────────
  function _setSelection(scope, id, label) {
    if (!_st) return;
    _st.selection = { scope, id, label };
    const host = document.getElementById('desk-v1-camp-rightcol');
    if (host) deskV1FillCampaignRightColumn(host, { campaignId: _st.campaignId });
  }

  function deskV1FillCampaignRightColumn(el, params) {
    const camp = _campaign(params.campaignId);
    if (!camp) { el.innerHTML = ''; return; }
    const st = _ensureState(camp.id);
    const sugg = (_fx().campaignSuggestions || {})[camp.id] || {};
    const scopeLabel = st.selection.scope === 'card' ? st.selection.label : camp.plan.title;
    const project = _project(camp.projectId);
    const agentRef = DeskV1Kit.deskAgentRef({ project, campaign: camp });
    // R2-18 (Ron 2026-09-30): the agent belongs to the CAMPAIGN, picked on the
    // Brief stop (`how.agent`), and needs a project first — its choices are the
    // agents hired on that project's floor. A project-less draft has nothing
    // to plan with yet, so this column is a neutral note pointing at Brief (no
    // box) and the user fills the stops by hand. A campaign with a project but
    // no agent anywhere keeps the box and its "Pick who plans" label; nothing
    // blocks on it.
    if (!project) {
      el.innerHTML = `<div class="desk-v1-camp-posy desk-v1-camp-noagent" data-no-agent>
        <div class="desk-thread-head"><span class="desk-thread-name">No agent yet</span></div>
        <div class="desk-v1-rules-hint">Pick a project on Brief, then an agent for this campaign. Until then, fill in the stops by hand.</div>
      </div>`;
      return;
    }
    el.innerHTML = `<div class="desk-v1-camp-posy">${DeskV1Kit.posyBoxHTML({
      inputId: 'desk-v1-camp-posy-input', scopeLabel, suggestion: sugg.suggestion, chips: sugg.chips, agentRef,
    })}</div>`;
    // §3.4 INS-01/02/03/04 (before → after, widening confirm, durable rule
    // chips) is T2b's Posy-instruction handler — backward-compatible seam,
    // same shape as the two hooks above: falls back to the plain toast T2a
    // shipped with until desk-v1-rules.js defines the real handler.
    DeskV1Kit.bindPosyBox(el.querySelector('.desk-v1-camp-posy'), 'desk-v1-camp-posy-input', (text) => {
      // R2-6: desk-v1-how.js's Suggest button fills this SAME box's textarea
      // with this exact literal and clicks Send — reusing the real task
      // lifecycle (Working/Failed/Retry, `window.__deskV1PosyForce`) rather
      // than a second one. Checked before the generic instruction handler so
      // a How-stop suggestion never falls through to it.
      if (text === _HOW_SUGGEST_TEXT) {
        _runSuggestTask(camp, project, el.querySelector('.desk-v1-camp-posy'));
        return;
      }
      if (typeof window.deskV1HandlePosyInstruction === 'function') {
        window.deskV1HandlePosyInstruction(camp, text, el.querySelector('.desk-v1-camp-posy'), st.selection);
      } else {
        DeskV1Kit.toast('Sent to ' + DeskV1Kit.deskAgentName({ project, campaign: camp }) + ': “' + text + '”');
        DeskV1Kit.paintPosyReadyNoDiff(el.querySelector('.desk-v1-camp-posy'));
      }
    }, {
      onScopeClick: () => _setSelection('campaign', null, null),
      // §5/T3: the key must be a stable id, never a label — two cards can
      // share a title (same bug desk-v1-video.js's own _posyDraftKey fixed),
      // which would collapse their Posy drafts onto one entry.
      // §3 T3 row: every draft key is prefixed `project:<pid>:` so Home's
      // project card (`anyPosyWorking('project:<pid>:')`) sees work in flight
      // anywhere under that project, not just this one campaign scope.
      draftKey: `project:${camp.projectId || ''}:campaign:${camp.id}:${st.selection.scope}:${st.selection.id || ''}`,
      taskLifecycle: true,
    });
  }

  window.deskV1FillCampaignSummary = deskV1FillCampaignSummary;
  window.deskV1FillCampaignTabStrip = deskV1FillCampaignTabStrip;
  window.deskV1FillCampaignMapFoot = deskV1FillCampaignMapFoot;
  window.deskV1FillCampaignTabBody = deskV1FillCampaignTabBody;
  window.deskV1FillCampaignRightColumn = deskV1FillCampaignRightColumn;
  // R2-10: the Where board shows the same Awaiting-approval notice Launch does.
  window.deskV1CampaignBounds = _currentBounds;
})();

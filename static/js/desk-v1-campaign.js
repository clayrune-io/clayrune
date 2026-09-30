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
  function _familyNeedsYou(fam) { return _familyHasState(fam, new Set(['needs_review'])); }

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

  function _openResumeSheet(camp, summaryEl, params) {
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
          do: () => { camp.state = 'active'; deskV1FillCampaignSummary(summaryEl, params); },
          undo: () => { camp.state = prev; deskV1FillCampaignSummary(summaryEl, params); },
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
    host.style.position = 'relative';
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
  // (`DeskV1Kit.MAP_STOPS`: goal/how/what/when/where/launch). Every stop is
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
    if ((map.done || []).includes(stop)) return 'done';
    if (missingStops.has(stop)) return 'needs_you';
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
    el.innerHTML = `
      <div class="desk-v1-map-tabs" role="tablist">
        ${DeskV1Kit.stateLabelHTML(camp.state, { className: 'desk-v1-map-pill' })}
        <div class="desk-v1-map-stops">${stopsHTML}</div>
        ${moreHTML}
      </div>`;
    el.querySelectorAll('[data-stop]').forEach((btn) => {
      btn.onclick = () => _gotoMapStop(camp, btn.getAttribute('data-stop'));
    });
    const moreBtn = el.querySelector('[data-camp-more-btn]');
    if (moreBtn) moreBtn.onclick = (e) => {
      e.stopPropagation();
      deskV1OpenCampaignMoreMenu(moreBtn, camp.id, {
        onDone: (result) => {
          if (result === 'deleted' && typeof window.deskV1PopTo === 'function') window.deskV1PopTo('home');
          else if (typeof window.deskV1Render === 'function') window.deskV1Render();
        },
      });
    };
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
  function _launchMissing(camp, project) {
    const planResult = DeskV1Kit.validatePlan(camp.plan, project) || { ok: true, missing: [] };
    if (!_projectEditable(camp)) return planResult;
    const extra = [];
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
  // than a second Start flow. This ticket wires the frame + the gate only —
  // the Active-state half of the row (approval record, Pause/Resume, Renew
  // term) is R2-11's job; a running campaign sees the same missing/Start
  // body here until then, EXCEPT for the one bound R2-6 does wire on an
  // Active campaign: `camp.approval.bounds` vs the live plan, so a How-stop
  // budget widen is visible somewhere before R2-11 builds the rest of this
  // row. A campaign with no `approval` recorded yet (draft, or a fixture
  // that predates this ticket) skips the check exactly as before.
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
    // R2-2g: the project is chosen HERE, not up front. Missing "project" is a
    // plain row (the select right above it is the fix); the rest link to the
    // stop that fixes them. The select is editable while the campaign hasn't
    // started (draft / proposed), a read-only line after.
    const projectEditable = _projectEditable(camp);
    const missingHTML = result.missing.length
      ? `<ul class="desk-v1-map-launch-missing">${result.missing.map((m) => m.bound === 'project'
        ? `<li class="desk-v1-map-launch-missing-project" data-missing-project>${esc(m.label)} — ${esc(m.detail)}</li>`
        : `<li><button type="button" class="desk-v1-map-launch-missing-link" data-missing-stop="${esc(m.stop)}">${esc(m.label)}${m.detail ? ` — ${esc(m.detail)}` : ''}</button></li>`).join('')}</ul>`
      : '<div class="desk-v1-stub-inline">Everything needed to launch is filled in.</div>';
    el.innerHTML = `
      <div class="desk-v1-map-launch">
        ${projectEditable ? '' : `<div class="desk-v1-rules-hint" data-launch-project-ro>Project: ${esc(project ? project.name : 'none')}</div>`}
        <div class="desk-v1-map-launch-status">${result.ok ? '✓ Ready to launch' : `⚠ ${esc(result.missing.length)} to fix before Start`}</div>
        ${missingHTML}
        <button type="button" class="desk-v1-map-launch-start" data-map-start-btn ${result.ok ? '' : 'disabled'}>Start campaign</button>
      </div>`;
    if (projectEditable && typeof window.deskV1MountProjectField === 'function') {
      window.deskV1MountProjectField(el.querySelector('.desk-v1-map-launch'), camp);
    }
    el.querySelectorAll('[data-missing-stop]').forEach((btn) => {
      btn.onclick = () => _gotoMapStop(camp, btn.getAttribute('data-missing-stop'));
    });
    const startBtn = el.querySelector('[data-map-start-btn]');
    if (startBtn && result.ok) startBtn.onclick = () => window.deskV1OpenStartSheet(camp.id);
  }

  // ────────────────────────────────────────────────────────────────────────
  // Content tab body (§3.2, §3.3). Toolbar (filter · channel filter ·
  // List/Calendar toggle · + New piece) persists across the toggle; only the
  // body under it swaps between the grouped/filtered list and T4's calendar,
  // mounted straight into this same host via the T0a slot contract
  // (`deskV1RenderCalendar(el, params)` — desk-v1-calendar.js's own header
  // comment: "once T2a lands, its toggle can call [this] straight into its
  // Content-tab body slot with no change needed here").
  // ────────────────────────────────────────────────────────────────────────
  const FILTER_LABELS = {
    all: 'All content', needs_you: 'Needs you', scheduled: 'Scheduled',
    published: 'Published', blocked: 'Blocked', archived: 'Archived',
  };
  const FILTER_ORDER = ['all', 'needs_you', 'scheduled', 'published', 'blocked', 'archived'];

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
    // §2/§8 calendar alias: `deskV1Nav('calendar', {campaignId})` lands on
    // this same Content panel and asks it to open in calendar view once —
    // consumed and dropped here so a later plain Content-tab click doesn't
    // keep forcing calendar view back on.
    if (params.calendarView) { st.view = 'calendar'; delete params.calendarView; }
    st.el = el;
    _renderTabBody();
  }

  function _renderTabBody() {
    const st = _st; const el = st.el;
    const camp = _campaign(st.campaignId);
    if (!camp) { el.innerHTML = '<div class="desk-v1-stub-inline">Campaign not found.</div>'; return; }
    el.innerHTML = `
      <div class="desk-v1-camp-content">
        <div class="desk-v1-camp-toolbar" id="desk-v1-camp-toolbar"></div>
        <div class="desk-v1-camp-listwrap" id="desk-v1-camp-listwrap"></div>
      </div>`;
    _renderToolbar(camp);
    if (st.view === 'calendar') {
      if (typeof window.deskV1RenderCalendar === 'function') {
        window.deskV1RenderCalendar(document.getElementById('desk-v1-camp-listwrap'), { campaignId: camp.id });
      } else {
        document.getElementById('desk-v1-camp-listwrap').innerHTML = '<div class="desk-v1-stub-inline">Calendar (T4) not loaded.</div>';
      }
    } else {
      _renderList(camp);
    }
  }

  function _renderToolbar(camp) {
    const st = _st;
    const host = document.getElementById('desk-v1-camp-toolbar');
    if (!host) return;
    const chFilterLabel = st.channelFilter === 'all' ? 'All channels' : ((_channel(st.channelFilter) || {}).label || 'All channels');
    host.innerHTML = `
      <div class="desk-v1-camp-toolbar-left">
        <div class="desk-v1-addto-wrap desk-v1-camp-filterwrap">
          <button type="button" class="desk-v1-camp-filter-btn" data-filter-trigger>${esc(FILTER_LABELS[st.filter] || 'All content')} ▾</button>
        </div>
        <div class="desk-v1-addto-wrap desk-v1-camp-filterwrap">
          <button type="button" class="desk-v1-camp-filter-btn" data-channel-trigger>${esc(chFilterLabel)} ▾</button>
        </div>
        <div class="desk-v1-camp-viewtoggle" role="tablist" aria-label="List or calendar view">
          <button type="button" data-view-btn="list" aria-pressed="${st.view === 'list'}">☰ List</button>
          <button type="button" data-view-btn="calendar" aria-pressed="${st.view === 'calendar'}">▦ Calendar</button>
        </div>
      </div>
      <button type="button" class="desk-v1-camp-newpiece" data-new-piece>+ New piece</button>`;

    host.querySelector('[data-filter-trigger]').onclick = (e) => {
      const items = FILTER_ORDER.map((k) => ({ id: k, label: FILTER_LABELS[k] }));
      DeskV1Kit.addToMenu(e.currentTarget, items, (id) => { st.filter = id; _renderTabBody(); }, { noAppendNew: true });
    };
    host.querySelector('[data-channel-trigger]').onclick = (e) => {
      const items = [{ id: 'all', label: 'All channels' }].concat(_channels().map((ch) => ({ id: ch.id, label: ch.label })));
      DeskV1Kit.addToMenu(e.currentTarget, items, (id) => { st.channelFilter = id; _renderTabBody(); }, { noAppendNew: true });
    };
    host.querySelectorAll('[data-view-btn]').forEach((b) => b.onclick = () => { st.view = b.dataset.viewBtn; _renderTabBody(); });
    host.querySelector('[data-new-piece]').onclick = () => _newPiece(camp);
  }

  // ── list body: grouped ("All content") or a single flat filtered list ────
  function _matchesChannel(fam, channelFilter) {
    return channelFilter === 'all' || (fam.versions || []).some((v) => v.channelId === channelFilter);
  }
  function _familyGroup(fam) {
    if (_familyNeedsYou(fam)) return 'needs_you';
    if (_familyHasState(fam, _SCHEDULED_STATES)) return 'scheduled';
    if (_familyHasState(fam, _TERMINAL_STATES)) return 'published';
    // R0's calendar-ticket fixtures (fam-followup-post: planned only;
    // fam-arm-faq: blocked only) don't fit the doc's 3 named §3.2 groups —
    // rather than drop them from "All content" (the acceptance check says
    // grouped view covers everything), they collect under this 4th, equally
    // collapsed-by-default heading. Flagged in the final report.
    return 'other';
  }
  const GROUP_TITLES = { needs_you: 'NEEDS YOU', scheduled: 'SCHEDULED', published: 'PUBLISHED', other: 'PLANNED' };
  const GROUP_ORDER = ['needs_you', 'scheduled', 'published', 'other'];
  const COLLAPSED_BY_DEFAULT = new Set(['published', 'other']);

  let _expandedGroups = new Set();

  function _renderList(camp) {
    const st = _st;
    const host = document.getElementById('desk-v1-camp-listwrap');
    if (!host) return;
    let fams = _familiesFor(camp.id).filter((f) => _matchesChannel(f, st.channelFilter));

    let bodyHTML;
    if (st.filter === 'all') {
      const groups = {};
      for (const f of fams) { const g = _familyGroup(f); (groups[g] = groups[g] || []).push(f); }
      bodyHTML = GROUP_ORDER.filter((g) => groups[g] && groups[g].length).map((g) => {
        const items = groups[g];
        const collapsed = COLLAPSED_BY_DEFAULT.has(g) && !_expandedGroups.has(g);
        return `<div class="desk-v1-camp-group">
          <div class="desk-v1-camp-group-head">
            <span class="desk-v1-camp-group-title">${GROUP_TITLES[g]} · ${items.length}</span>
            ${collapsed ? `<button type="button" class="desk-v1-camp-group-show" data-group-show="${g}">show ›</button>` : ''}
          </div>
          ${collapsed ? '' : `<div class="desk-v1-camp-cards">${items.map((f) => _familyCardHTML(f, camp)).join('')}</div>`}
        </div>`;
      }).join('') || '<div class="desk-v1-camp-empty">No content yet — drop a channel or material below, or ＋ New piece.</div>';
    } else {
      const pred = {
        needs_you: _familyNeedsYou,
        scheduled: (f) => _familyHasState(f, _SCHEDULED_STATES),
        published: (f) => _familyHasState(f, _TERMINAL_STATES),
        blocked: (f) => _familyHasState(f, new Set(['blocked'])),
        archived: (f) => _familyHasState(f, new Set(['archived'])),
      }[st.filter] || (() => true);
      const items = fams.filter(pred);
      bodyHTML = `<div class="desk-v1-camp-cards">${items.map((f) => _familyCardHTML(f, camp)).join('') || '<div class="desk-v1-camp-empty">Nothing matches this filter.</div>'}</div>`;
    }

    // pd-drop-target is NOT statically present (Dave's review: 12a has no
    // container outline at rest) — onActivate/onTeardown below toggle it
    // for the duration of a drag only.
    host.innerHTML = `<div class="desk-v1-camp-listarea" id="desk-v1-camp-listarea" data-listarea>${_suggestedWhatBannerHTML(camp)}${bodyHTML}</div>`;
    host.querySelectorAll('[data-group-show]').forEach((b) => b.onclick = () => { _expandedGroups.add(b.dataset.groupShow); _renderList(camp); });
    const acceptBtn = host.querySelector('[data-suggested-accept-all]');
    if (acceptBtn) acceptBtn.onclick = () => _acceptSuggestedWhat(camp);
    _wireCards(host, camp);
  }

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
      </div>`;
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
  // into real pieces; ④/⑤ read `how.suggested.when`/`.where` directly
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
    camp.how.suggested = {
      what: [1, 2, 3].map((n) => ({ title: `${title} — post ${n}`, channelId: accounts[(n - 1) % (accounts.length || 1)] || null })),
      when: { label: `${(plan.cadence && plan.cadence.per_week) || 3}x/week` },
      where: { channelId: accounts[0] || null, label: accounts[0] ? (_channel(accounts[0]) || {}).label || accounts[0] : null },
    };
    DeskV1Kit.toast(`${DeskV1Kit.deskAgentName({ project, campaign: camp })} suggested 3 pieces, a cadence and a placement.`);
    if (_st && _st.campaignId === camp.id && _st.el && document.body.contains(_st.el) && _st.el.dataset.panel === 'what') _renderTabBody();
    if (posyBoxEl) DeskV1Kit.paintPosyReadyNoDiff(posyBoxEl);
  }

  // ── content card (§3.2 CNT-01) ────────────────────────────────────────────
  function _kindMeta(fam) {
    if (fam.kind === 'article') return `📄 Article · ${esc(fam.wordCount || 0)} words`;
    if (fam.kind === 'video') return `▶ Video · ${fam.versions.length} version${fam.versions.length === 1 ? '' : 's'}`;
    return null;
  }
  function _previewHTML(fam) {
    if (fam.kind === 'video') {
      const primary = fam.versions.find((v) => v.state === 'verified_published') || fam.versions.find((v) => v.format) || fam.versions[0];
      const format = (primary && primary.format) || '16:9';
      const vd = (_fx().videoDetail || {})[fam.id];
      const durationSec = vd && vd.scenes ? vd.scenes.reduce((s, sc) => s + (sc.durationSec || 0), 0) : null;
      const durLabel = durationSec != null ? ` · ${Math.floor(durationSec / 60)}:${String(durationSec % 60).padStart(2, '0')}` : '';
      return `<div class="desk-v1-camp-preview desk-v1-camp-preview-video"><span class="desk-v1-camp-preview-play">▶</span><span class="desk-v1-camp-preview-caption">${esc(format)}${durLabel}</span></div>`;
    }
    const text = (_fx().contentPreview || {})[fam.id];
    return `<div class="desk-v1-camp-preview desk-v1-camp-preview-text">${text ? esc(text) : ''}</div>`;
  }
  function _versionRowHTML(fam, v) {
    const ch = _channel(v.channelId);
    const badgeHTML = ch ? DeskV1Kit.channelBadge(ch, {}) : '<span class="desk-v1-camp-nochannel">No channel</span>';
    const fmt = v.format ? ` · ${esc(v.format)}` : '';
    const stateHTML = DeskV1Kit.stateLabelHTML(v.state);
    let detail = '';
    if (v.publishedAt) detail = _fmtWhenShort(v.publishedAt);
    else if (v.publishAt) detail = _fmtWhenShort(v.publishAt);
    else if (fam.render && fam.render.jobId && fam.kind === 'video') detail = `render ${esc(fam.render.jobId.replace('render-', ''))} ${esc(fam.render.status)}`;
    return `<div class="desk-v1-camp-vrow" data-version-id="${esc(v.id)}">
      <span class="desk-v1-camp-vrow-badge">${badgeHTML}${fmt}</span>
      <span class="desk-v1-camp-vrow-state">${stateHTML}</span>
      ${detail ? `<span class="desk-v1-camp-vrow-detail">· ${esc(detail)}</span>` : ''}
    </div>`;
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
  function _familyCardHTML(fam, camp) {
    const meta = _kindMeta(fam);
    const action = _primaryAction(fam);
    // pd-drop-target likewise applied only during an active drag (see
    // _contentTargetAdapter.onActivate/onTeardown), not at rest.
    return `<div class="desk-v1-camp-card" data-family-id="${esc(fam.id)}">
      ${_previewHTML(fam)}
      <div class="desk-v1-camp-card-body">
        ${meta ? `<div class="desk-v1-camp-card-meta">${meta}</div>` : ''}
        <div class="desk-v1-camp-card-title">${esc(fam.title)}</div>
        <div class="desk-v1-camp-card-versions">${fam.versions.map((v) => _versionRowHTML(fam, v)).join('')}</div>
        <div class="desk-v1-camp-card-result" aria-live="polite"></div>
      </div>
      <div class="desk-v1-camp-card-actions">
        <button type="button" class="desk-v1-camp-card-primary" data-primary-action>${esc(action.label)}</button>
        <div class="desk-v1-camp-card-more">
          <button type="button" class="desk-v1-camp-card-morebtn" data-more-btn aria-haspopup="menu" aria-label="More actions">⋯</button>
        </div>
      </div>
    </div>`;
  }

  function _wireCards(host, camp) {
    host.querySelectorAll('[data-family-id]').forEach((cardEl) => {
      const fam = _familiesFor(camp.id).find((f) => f.id === cardEl.dataset.familyId);
      if (!fam) return;
      const primary = cardEl.querySelector('[data-primary-action]');
      if (primary) primary.onclick = (e) => { e.stopPropagation(); _runPrimaryAction(fam, camp); };
      const more = cardEl.querySelector('[data-more-btn]');
      if (more) more.onclick = (e) => { e.stopPropagation(); _openCardMenu(e.currentTarget, fam, camp); };
      // Selecting the card (not its buttons) scopes the Posy box to it
      // (§3.4 INS-01: "the campaign, a card, a version").
      cardEl.addEventListener('click', () => _setSelection('card', fam.id, fam.title));
    });
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

  function _newPiece(camp) {
    const fam = {
      id: 'fam-new-' + Date.now().toString(36), campaignId: camp.id, kind: 'post',
      title: 'New piece', versions: [{ id: 'v-new-' + Date.now().toString(36), channelId: null, state: 'drafting', revision: 0 }],
    };
    DeskV1Kit.commandBus.run({
      label: `Created “${fam.title}”`,
      do: () => { _fx().families.push(fam); _renderTabBody(); },
      undo: () => { const arr = _fx().families; const i = arr.findIndex((f) => f.id === fam.id); if (i >= 0) arr.splice(i, 1); _renderTabBody(); },
    });
  }

  // ── drag & drop (§3.2 table, §10). Reuses the Add tray's own shelf items
  // as the drag SOURCE (deskV1RenderShelfPair, T1) with a custom
  // targetAdapter whose targets are content cards + the empty list area,
  // instead of Home's campaign cards — exactly the "caller supplies its own
  // targetAdapter" seam deskV1RenderShelfPair's own comment describes. ──────
  function _setCardResultText(cardEl, text) {
    const t = cardEl.querySelector('.desk-v1-camp-card-result');
    if (t) t.textContent = text || '';
  }
  function _listAreaResultText(text) {
    const el = document.getElementById('desk-v1-camp-listarea');
    if (!el) return;
    let t = el.querySelector('.desk-v1-camp-listarea-result');
    if (!t) { t = document.createElement('div'); t.className = 'desk-v1-camp-listarea-result'; el.insertBefore(t, el.firstChild); }
    t.textContent = text || '';
    if (!text) t.remove();
  }

  function _hoverContentAt(x, y, dragData) {
    const el = document.elementFromPoint(x, y);
    const card = el && el.closest && el.closest('.desk-v1-camp-card');
    document.querySelectorAll('.desk-v1-camp-card').forEach((c) => {
      if (c !== card) { c.classList.remove('pd-drop-hover'); _setCardResultText(c, ''); }
    });
    if (card) {
      card.classList.add('pd-drop-hover');
      _setCardResultText(card, dragData.type === 'channel' ? `Drop to add ${dragData.label} as a new version` : `Drop to attach ${dragData.label} to this piece`);
      _listAreaResultText('');
      return;
    }
    const listArea = el && el.closest && el.closest('#desk-v1-camp-listarea');
    if (listArea) {
      listArea.classList.add('pd-drop-hover');
      _listAreaResultText(dragData.type === 'channel' ? `Drop to add ${dragData.label} to this campaign` : `Drop to create a new piece from ${dragData.label}`);
    } else {
      const la = document.getElementById('desk-v1-camp-listarea');
      if (la) la.classList.remove('pd-drop-hover');
      _listAreaResultText('');
    }
  }

  function _resolveContentDropAt(x, y) {
    const el = document.elementFromPoint(x, y);
    const card = el && el.closest && el.closest('.desk-v1-camp-card');
    if (card) return { type: 'card', familyId: card.dataset.familyId };
    const listArea = el && el.closest && el.closest('#desk-v1-camp-listarea');
    if (listArea) return { type: 'listarea' };
    return null;
  }

  function _handleContentDrop(resolved, dragData, camp) {
    if (resolved.type === 'card') {
      const fam = _familiesFor(camp.id).find((f) => f.id === resolved.familyId);
      if (!fam) return;
      if (dragData.type === 'channel') {
        if (fam.versions.some((v) => v.channelId === dragData.channelId)) { DeskV1Kit.toast(`${dragData.label} is already a version on “${fam.title}”.`); return; }
        _addChannelVersion(fam, dragData.channelId);
      } else {
        // Material → card: attaches the asset (no generation) — modelled
        // here as a note on the family's title-adjacent preview rather than a
        // new version, since §3.2's AttachAsset carries no state of its own.
        DeskV1Kit.commandBus.run({
          label: `Attached “${dragData.label}” to “${fam.title}”`,
          do: () => { fam.attachedAssets = (fam.attachedAssets || []).concat([dragData.asset.id]); _renderList(camp); },
          undo: () => { fam.attachedAssets = (fam.attachedAssets || []).filter((id) => id !== dragData.asset.id); _renderList(camp); },
        });
      }
    } else if (resolved.type === 'listarea') {
      if (dragData.type === 'channel') {
        if (camp.plan.accounts.includes(dragData.channelId)) { DeskV1Kit.toast(`${dragData.label} is already on “${camp.plan.title}”.`); return; }
        DeskV1Kit.commandBus.run({
          label: `Added ${dragData.label} to “${camp.plan.title}”`,
          do: () => { camp.plan.accounts.push(dragData.channelId); deskV1FillCampaignSummary(document.getElementById('desk-v1-camp-summary'), { campaignId: camp.id }); },
          undo: () => { const i = camp.plan.accounts.indexOf(dragData.channelId); if (i >= 0) camp.plan.accounts.splice(i, 1); deskV1FillCampaignSummary(document.getElementById('desk-v1-camp-summary'), { campaignId: camp.id }); },
        });
      } else {
        const asset = dragData.asset;
        const fam = { id: 'fam-drop-' + Date.now().toString(36), campaignId: camp.id, kind: asset.kind, title: asset.title, versions: [{ id: 'v-drop-' + Date.now().toString(36), channelId: null, state: 'drafting', revision: 0 }] };
        DeskV1Kit.commandBus.run({
          label: `Created “${fam.title}” from ${asset.title}`,
          do: () => { _fx().families.push(fam); _renderTabBody(); },
          undo: () => { const arr = _fx().families; const i = arr.findIndex((f) => f.id === fam.id); if (i >= 0) arr.splice(i, 1); _renderTabBody(); },
        });
      }
    }
  }

  function _contentTargetAdapter(camp) {
    return {
      onActivate: () => {
        document.querySelectorAll('.desk-v1-camp-card').forEach((c) => c.classList.add('pd-drop-target'));
        const la = document.getElementById('desk-v1-camp-listarea');
        if (la) la.classList.add('pd-drop-target');
      },
      onMove: (x, y, dragData) => _hoverContentAt(x, y, dragData),
      onDrop: (x, y) => _resolveContentDropAt(x, y),
      afterDrop: (resolved) => {},
      onTeardown: () => {
        document.querySelectorAll('.desk-v1-camp-card').forEach((c) => { c.classList.remove('pd-drop-target', 'pd-drop-hover'); _setCardResultText(c, ''); });
        const la = document.getElementById('desk-v1-camp-listarea');
        if (la) la.classList.remove('pd-drop-target', 'pd-drop-hover');
        _listAreaResultText('');
      },
      addToItems: () => _familiesFor(camp.id).map((f) => ({ id: f.id, label: f.title })),
      onPick: () => {},
    };
  }

  // The Add tray's shelf pair calls afterDrop(resolved, dragData) via
  // deskV1RenderShelfPair -> _wireShelfItem's adapter contract (desk-v1-
  // home.js). This wraps _contentTargetAdapter so the campaign fixture
  // mutation actually runs, since the generic adapter above only resolves
  // WHAT was hit — the campaign-specific "what happens" stays here.
  function _addTrayAdapter(camp) {
    const base = _contentTargetAdapter(camp);
    return Object.assign({}, base, {
      afterDrop: (resolved, dragData) => { if (resolved) _handleContentDrop(resolved, dragData, camp); },
      onPick: (pickedId, dragData) => {
        // Keyboard/click "Add to…" path (UX-05) resolves to a family
        // (attach) rather than a campaign — reusing the same _handleContentDrop
        // as a synthetic card-drop keeps one code path for both entry points.
        _handleContentDrop({ type: 'card', familyId: pickedId }, dragData, camp);
      },
    });
  }

  // Wires the SAME drop targets a second time for drags that originate
  // inside the list itself (channel badges dropped straight from the
  // summary bar are out of scope; this only needs to exist once, wired from
  // the Add tray's shelf items, per _renderTabBody -> Add tray mount order).
  function _wireListDrop() { /* no-op: targets are passive; adapter above drives them from the Add tray's pointerdown */ }

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

  // ────────────────────────────────────────────────────────────────────────
  // Add tray (§3.6): the same two shelves as Home, scoped to this campaign —
  // "reuse, don't fork" (docs/desk_v1_r0_plan.md). Channels already on the
  // campaign are hidden (`hideChannelIds`) rather than merely dimmed — CSS
  // opacity on a still-interactive shelf item would leave a drop target that
  // silently no-ops (already-attached), which is worse than not offering it.
  // ────────────────────────────────────────────────────────────────────────
  function deskV1FillCampaignAddTray(el, params) {
    const camp = _campaign(params.campaignId);
    if (!camp) { el.innerHTML = ''; return; }
    el.innerHTML = `
      <details class="desk-v1-camp-addtray-details">
        <summary class="desk-v1-camp-addtray-summary">+ Add ▾</summary>
        <div class="desk-v1-camp-addtray-body">
          <div class="desk-v1-camp-addtray-shelf">
            <div class="desk-v1-home-shelf-title">Channels</div>
            <div class="desk-v1-home-shelf-items" id="desk-v1-camp-addtray-channels"></div>
          </div>
          <div class="desk-v1-camp-addtray-shelf">
            <div class="desk-v1-home-shelf-title">Material</div>
            <div class="desk-v1-home-shelf-items" id="desk-v1-camp-addtray-material"></div>
          </div>
        </div>
      </details>`;
    window.deskV1RenderShelfPair({
      channelsHost: document.getElementById('desk-v1-camp-addtray-channels'),
      materialHost: document.getElementById('desk-v1-camp-addtray-material'),
    }, {
      hideChannelIds: camp.plan.accounts || [],
      targetAdapter: _addTrayAdapter(camp),
    });
  }

  window.deskV1FillCampaignSummary = deskV1FillCampaignSummary;
  window.deskV1FillCampaignTabStrip = deskV1FillCampaignTabStrip;
  window.deskV1FillCampaignMapFoot = deskV1FillCampaignMapFoot;
  window.deskV1FillCampaignTabBody = deskV1FillCampaignTabBody;
  window.deskV1FillCampaignRightColumn = deskV1FillCampaignRightColumn;
  window.deskV1FillCampaignAddTray = deskV1FillCampaignAddTray;
})();

// Desk v1 (MC-977 IA revision) — IA4: Campaign setup, 3 in-page steps
// (docs/THE_DESK_V1_IA_REVISION.md §2.3, §5 row IA4). Window-bridged module,
// no `import` (ground rule 1, T0a). Standing decision (Ron, 2026-09-28):
// setup is a resumable checklist ON the campaign page, never a separate
// wizard route — so this file adds no new ROUTES entry. It hooks the same
// backward-compatible seam desk-v1-rules.js already uses for the Proposed
// state (`deskV1FillCampaignSummary`'s state branch in desk-v1-campaign.js),
// one state earlier: `camp.state === 'draft'`.
//
// Step 3 ("Review + start") is NOT a new screen here — once step 2 commits a
// plan the campaign's state becomes `proposed` and desk-v1-rules.js's
// existing Proposed-state summary (bounds/goal + `Start campaign`) already
// renders it; this file only adds the "from <project>" inherited labelling
// and the manual-capability Replies copy that page's Start sheet needed
// (desk-v1-rules.js `deskV1OpenStartSheet`) to serve setup's step 3, plus
// the "◇ Planned" pieces desk-v1-rules.js's existing Content-tab override
// already lists once they exist in `DeskV1Fixtures.families`.
//
// Fixtures only (ground rule 3): a draft campaign and its planned pieces are
// plain objects pushed onto DeskV1Fixtures.campaigns/families, same
// client-side contract every other v1 surface uses.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function _fx() { return window.DeskV1Fixtures || {}; }
  function _projects() { return _fx().projects || []; }
  function _project(id) { return _projects().find((p) => p.id === id); }
  function _campaigns() { return _fx().campaigns || []; }
  function _campaign(id) { return _campaigns().find((c) => c.id === id) || null; }
  function _channels() { return _fx().channels || []; }
  function _channel(id) { return _channels().find((c) => c.id === id); }

  // ── Draft campaign factory (§2.2 shape, §3#40 `subject`) — the "＋ New
  // campaign" button on the project page (desk-v1-project.js) and the
  // ambiguous-drop "+ New campaign" path (desk-v1-home.js) both need one of
  // these; only the project page wires it in IA4 (Home's own drop path is
  // out of this ticket's scope, THE_DESK_V1_IA_REVISION.md §5 row IA4). ────
  // R2-2f: `projectId` may be null — Home's page-level "＋ New campaign"
  // creates a draft before any project is chosen and the Goal stop's Project
  // select sets it. `_prefillProjectId` records what the creator supplied so
  // `_isUntouchedDraft` below can tell "nobody changed anything" from "the
  // user picked the same project the block had prefilled, then undid it".
  function deskV1CreateDraftCampaign(projectId) {
    return {
      id: 'camp-draft-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6),
      state: 'draft', // draft | proposed | active | paused | completed | archived
      projectId: projectId || null,
      _prefillProjectId: projectId || null,
      subject: null,
      goal: { current: 0 },
      rules: {},
      setup: { step: 1, done: [] },
      // R2-3 (IA revision 2 §3 table): the map stepper's own resume cursor,
      // separate from `setup` above (IA4's own checklist, untouched by this
      // ticket) — a fresh draft starts the map at ① Goal, same stop the
      // project page's draft card and `_renderCampaignSkeleton` fall back to
      // for any older draft fixture that predates this field.
      map: { stop: 'goal', done: [] },
      plan: {
        brief: '', title: '', audience: '',
        goal: { outcome: '', target: null, deadline: null, tracked: false },
        accounts: [], angle: '', samples: [],
        cadence: { per_week: null }, end: { date: null, post_cap: null },
        replies: 'drafts', paid: false,
        // §5 IA4 acceptance ("step 3 shows 0 '—'") — a real default so the
        // Start sheet's Generation limits row never shows the placeholder
        // straight out of "accept defaults"; camp-1's own fixture plan
        // carries the same sentence for a campaign that also does no video.
        generation: 'No video generation planned for this campaign',
      },
    };
  }

  function deskV1NewCampaignInProject(projectId) {
    const project = _project(projectId);
    const camp = deskV1CreateDraftCampaign(projectId);
    DeskV1Kit.commandBus.run({
      label: `Started setup for a new campaign in ${project ? project.name : 'the project'}`,
      do: () => { _fx().campaigns.push(camp); },
      undo: () => { const arr = _fx().campaigns; const i = arr.findIndex((c) => c.id === camp.id); if (i >= 0) arr.splice(i, 1); if (typeof window.deskV1Render === 'function') window.deskV1Render(); },
    });
    deskV1Nav('campaign', { campaignId: camp.id, projectId });
  }

  // ── Project field (R2-2f, Ron 2026-09-30: "the project picker on the left
  // and the new campaign on the right are doing almost the same thing") —
  // "＋ New campaign" no longer asks which project up front.
  // R2-2g (Ron 2026-09-30, "the pick a project enforcer should come only at
  // the end before the campaign is launched"): the field moved off the Goal
  // stop to the ⑥ Launch panel (desk-v1-campaign.js `_renderLaunchPanel`
  // calls `deskV1MountProjectField`), and a project-less draft now runs the
  // whole setup flow without one. Every change goes through the commandBus so
  // Undo reverts it; a project change also re-defaults the title/brief that
  // were auto-filled from the OLD project's name (only if the user hasn't
  // edited them).
  function _projectFieldHTML(camp) {
    const cur = camp.projectId || '';
    const opts = _projects().map((p) => `<option value="${esc(p.id)}" ${p.id === cur ? 'selected' : ''}>${esc(p.name)}</option>`).join('');
    const picked = !!_project(cur);
    return `<div class="desk-v1-rules-group" data-setup-project-group>
        <div class="desk-v1-rules-group-title">Project</div>
        <select class="desk-v1-goal-select" data-setup-project aria-label="Project">
          ${picked ? '' : '<option value="" selected disabled>Pick a project</option>'}${opts}
        </select>
        ${picked ? '' : '<div class="desk-v1-rules-hint" data-setup-project-hint>Its accounts, limits and agent come from the project. A campaign can’t launch without one.</div>'}
      </div>`;
  }

  function _defaultTitle(project) { return project ? `${project.name} campaign` : ''; }
  function _defaultBrief(project) { return project ? `Promote ${project.name}.` : ''; }

  function _bindProjectField(el, camp) {
    const sel = el.querySelector('[data-setup-project]');
    if (!sel) return;
    sel.onchange = () => {
      const nextId = sel.value || null;
      const prevId = camp.projectId || null;
      if (nextId === prevId) return;
      const prevProject = _project(prevId);
      const plan = camp.plan || {};
      const prevTitle = plan.title; const prevBrief = plan.brief; const prevTouched = camp._touched;
      const nextProject = _project(nextId);
      DeskV1Kit.commandBus.run({
        label: `Set campaign project to ${nextProject ? nextProject.name : 'none'}`,
        do: () => {
          camp.projectId = nextId;
          camp._touched = true;
          if (prevProject && plan.title === _defaultTitle(prevProject)) plan.title = _defaultTitle(nextProject);
          if (prevProject && plan.brief === _defaultBrief(prevProject)) plan.brief = _defaultBrief(nextProject);
          if (typeof window.deskV1Render === 'function') window.deskV1Render();
        },
        undo: () => {
          camp.projectId = prevId;
          camp._touched = prevTouched;
          plan.title = prevTitle; plan.brief = prevBrief;
          if (typeof window.deskV1Render === 'function') window.deskV1Render();
        },
      });
    };
  }

  // Mounts the Project group at the top of `host` (the Launch panel) and binds it.
  function deskV1MountProjectField(host, camp) {
    if (!host) return;
    host.insertAdjacentHTML('afterbegin', _projectFieldHTML(camp));
    _bindProjectField(host, camp);
  }

  // ── Untouched-draft discard (R2-2f): Home's "＋ New campaign" creates a
  // draft on click, so a stray click would otherwise leave a nameless draft
  // behind. `deskV1Back` (desk-v1-shell.js) calls this when leaving a campaign
  // page; only a draft Home marked `_discardIfUntouched` is ever removed, and
  // only if nothing the user could have typed or picked anywhere on the map is
  // set. Fields only written on Continue (title/brief/outcome/subject) are
  // covered by `_touched`, set on their `input` events in step 1.
  function _isUntouchedDraft(camp) {
    if (!camp || camp.state !== 'draft' || !camp._discardIfUntouched || camp._touched) return false;
    if ((camp.projectId || null) !== (camp._prefillProjectId || null)) return false;
    if (camp.subject || ((camp.setup && camp.setup.step) || 1) > 1) return false;
    const map = camp.map || {};
    if ((map.done || []).length || (map.stop && map.stop !== 'goal')) return false;
    const g = camp.goal || {};
    if (['metric', 'target', 'baseline', 'unit', 'horizon', 'deadline', 'source'].some((k) => g[k] != null && g[k] !== '')) return false;
    if ((g.entries || []).length) return false;
    const plan = camp.plan || {};
    if ((plan.accounts || []).length) return false;
    if (plan.cadence && plan.cadence.per_week != null) return false;
    if (plan.end && (plan.end.date != null || plan.end.post_cap != null)) return false;
    const how = camp.how;
    if (how && (how.angle || how.strategy || how.never_claim || how.agent || (how.budget && how.budget.source && how.budget.source !== 'none'))) return false;
    return !(_fx().families || []).some((f) => f.campaignId === camp.id);
  }

  function deskV1DiscardIfUntouchedDraft(campaignId) {
    const camp = _campaign(campaignId);
    if (!_isUntouchedDraft(camp)) return false;
    const arr = _campaigns();
    const i = arr.indexOf(camp);
    if (i >= 0) arr.splice(i, 1);
    return i >= 0;
  }

  // ── Step 0: Presence (§2.3, "if the project has no presence yet... once")
  // — reuses IA3's own settings page verbatim (deskV1RenderPresence), mounted
  // into a sub-host so this file never re-derives account/ceiling markup.
  // ────────────────────────────────────────────────────────────────────────
  // A Draft campaign (steps 0-2) had no More/Delete anywhere — the campaign
  // page shows ONLY this wizard while `camp.state === 'draft'`
  // (desk-v1-campaign.js:126), and the project page's own card has no
  // more-menu either (only an archived card does, Restore-only). Delete was
  // already meant to cover draft (`_isPrePublish` in desk-v1-campaign.js
  // treats 'proposed' and 'draft' identically), it just had no trigger to
  // reach it before Proposed. Reuses the SAME menu/confirm/commandBus path
  // the Proposed/Active summary uses (`deskV1OpenCampaignMoreMenu`) so the
  // draft/proposed boundary can't drift into two implementations.
  function _moreBtnHTML() {
    return `<div class="desk-v1-camp-card-more">
      <button type="button" class="desk-v1-camp-card-morebtn" data-camp-more-btn aria-haspopup="menu" aria-label="More actions">&#8942;</button>
    </div>`;
  }
  function _wireMoreBtn(el, camp, rerender) {
    const moreBtn = el.querySelector('[data-camp-more-btn]');
    if (!moreBtn || typeof window.deskV1OpenCampaignMoreMenu !== 'function') return;
    moreBtn.onclick = (e) => {
      e.stopPropagation();
      window.deskV1OpenCampaignMoreMenu(moreBtn, camp.id, {
        onDone: (result) => { if (result === 'deleted') deskV1Nav('home', {}); else rerender(); },
      });
    };
  }

  function _fillStep0(el, params, camp, project) {
    el.innerHTML = `
      <div class="desk-v1-camp-summary-top">
        <span class="desk-v1-camp-state-pill">Setup — Presence</span>
        ${_moreBtnHTML()}
      </div>
      <div class="desk-v1-rules-hint">${esc(project ? project.name : 'This project')} has no accounts connected yet — connect at least one before setting up a campaign.</div>
      <div id="desk-v1-setup-presence-host"></div>
      <div class="desk-v1-camp-summary-top-actions">
        <button type="button" class="desk-v1-rules-start-btn" data-setup-continue>Continue</button>
      </div>`;
    const host = el.querySelector('#desk-v1-setup-presence-host');
    if (host && typeof window.deskV1RenderPresence === 'function') window.deskV1RenderPresence(host, { projectId: project.id });
    const btn = el.querySelector('[data-setup-continue]');
    if (btn) btn.onclick = () => {
      if (!DeskV1Kit.validatePresence(project).ok) { DeskV1Kit.toast('Connect at least one account first.'); return; }
      deskV1FillDraftSetup(el, params, camp);
    };
    _wireMoreBtn(el, camp, () => deskV1FillDraftSetup(el, params, camp));
  }

  // ── Step 1: Subject + goal (§2.3 table row 1) — subject chips default to
  // the project itself (the common case — "the entire project is the
  // campaign", §2.1), title/brief/outcome pre-filled so "accept defaults"
  // (§5 IA4 acceptance) needs no typing at all. ───────────────────────────
  const SUBJECT_KINDS = [
    { kind: 'project', glyph: '◉', word: 'Project' },
    { kind: 'product', glyph: '▣', word: 'Product' },
    { kind: 'feature', glyph: '✦', word: 'Feature' },
    { kind: 'audience', glyph: '◎', word: 'Audience' },
    { kind: 'event', glyph: '◎', word: 'Event' },
  ];

  // R2-2g: a project-less draft has no project to default the subject to —
  // an empty "Product" the user names themselves.
  function _defaultSubject(project) {
    return project
      ? { kind: 'project', ref: project.id, label: project.name }
      : { kind: 'product', ref: undefined, label: '' };
  }

  function _fillStep1(el, params, camp, project) {
    const subject = camp.subject || _defaultSubject(project);
    const plan = camp.plan;
    if (!plan.title) plan.title = _defaultTitle(project);
    if (!plan.brief) plan.brief = _defaultBrief(project);
    if (!plan.goal.outcome) plan.goal.outcome = 'awareness';
    el.innerHTML = `
      <div class="desk-v1-camp-summary-top">
        <span class="desk-v1-camp-state-pill">Setup 1 of 3 — Subject + goal</span>
        ${_moreBtnHTML()}
      </div>
      <div class="desk-v1-rules-group">
        <div class="desk-v1-rules-group-title">Subject</div>
        <div class="desk-v1-camp-summary-badges" data-setup-subject-kinds>
          ${SUBJECT_KINDS.map((s) => `<button type="button" class="desk-v1-camp-rule-chip" data-subject-kind="${s.kind}" aria-pressed="${s.kind === subject.kind}">${s.glyph} ${esc(s.word)}</button>`).join('')}
        </div>
        <input type="text" class="desk-v1-rules-textinput" data-subject-label value="${esc(subject.label || '')}" placeholder="What this promotes">
      </div>
      <div class="desk-v1-rules-group">
        <div class="desk-v1-rules-group-title">Title</div>
        <input type="text" class="desk-v1-rules-textinput" data-setup-title value="${esc(plan.title)}" placeholder="Campaign name">
      </div>
      <div class="desk-v1-rules-group">
        <div class="desk-v1-rules-group-title">Brief</div>
        <textarea class="desk-v1-rules-textarea" data-setup-brief rows="2" placeholder="What this campaign is for">${esc(plan.brief)}</textarea>
      </div>
      <div class="desk-v1-rules-group">
        <div class="desk-v1-rules-group-title">Outcome</div>
        <input type="text" class="desk-v1-rules-textinput" data-setup-outcome value="${esc(plan.goal.outcome)}">
      </div>
      <div class="desk-v1-camp-summary-top-actions">
        <button type="button" class="desk-v1-rules-start-btn" data-setup-continue>Continue</button>
      </div>`;

    el.querySelectorAll('input, textarea').forEach((f) => f.addEventListener('input', () => { camp._touched = true; }));
    let pickedKind = subject.kind;
    el.querySelectorAll('[data-subject-kind]').forEach((btn) => {
      btn.onclick = () => {
        camp._touched = true;
        pickedKind = btn.dataset.subjectKind;
        el.querySelectorAll('[data-subject-kind]').forEach((b) => b.setAttribute('aria-pressed', String(b === btn)));
      };
    });

    const btn = el.querySelector('[data-setup-continue]');
    if (btn) btn.onclick = () => {
      const label = (el.querySelector('[data-subject-label]').value || '').trim();
      const title = (el.querySelector('[data-setup-title]').value || '').trim();
      const brief = (el.querySelector('[data-setup-brief]').value || '').trim();
      const outcome = (el.querySelector('[data-setup-outcome]').value || '').trim();
      if (!label || !title || !brief || !outcome) { DeskV1Kit.toast('Subject, title, brief and outcome are all required.'); return; }
      camp.subject = { kind: pickedKind, ref: pickedKind === 'project' && project ? project.id : undefined, label };
      plan.title = title;
      plan.brief = brief;
      plan.goal.outcome = outcome;
      camp.setup.step = 2;
      camp.setup.done = [1];
      deskV1FillDraftSetup(el, params, camp);
    };
    _wireMoreBtn(el, camp, () => deskV1FillDraftSetup(el, params, camp));
  }

  // ── Step 2: Plan (§2.3 table row 2) — "Draft the plan" (Posy task,
  // fixture-only per ground rule 3) pre-ticks every project account,
  // inherits the project's per-account ceiling as the cadence, and creates 3
  // `◇ Planned` pieces (§5 IA4 acceptance: "Proposed with 3 Planned pieces"
  // — row #30/#31 of §3's field table: samples become real pieces, not
  // "samples"). Leaving before this runs keeps the campaign in Draft at step
  // 2 — the project card then shows "Setup 2 of 3" (desk-v1-project.js). ──
  function _minCeiling(project, channelIds) {
    const ceilings = (project && project.presence && project.presence.ceilings) || {};
    let min = null;
    channelIds.forEach((id) => {
      const c = ceilings[id];
      if (c && c.per_week != null) min = min == null ? c.per_week : Math.min(min, c.per_week);
    });
    return min;
  }

  function _fillStep2(el, params, camp, project) {
    // R2-2g: with no project yet, every workspace channel is offered; the
    // project's own ceilings are checked against this plan at Launch.
    const accounts = project
      ? ((project.presence && project.presence.accounts) || [])
      : _channels().map((c) => ({ channel_id: c.id }));
    const checked = camp.plan.accounts.length ? camp.plan.accounts : accounts.map((a) => a.channel_id);
    el.innerHTML = `
      <div class="desk-v1-camp-summary-top">
        <span class="desk-v1-camp-state-pill">Setup 2 of 3 — Plan</span>
        ${_moreBtnHTML()}
      </div>
      <div class="desk-v1-rules-group">
        <div class="desk-v1-rules-group-title">Accounts</div>
        ${accounts.map((a) => {
          const ch = _channel(a.channel_id);
          return `<label class="desk-v1-rules-inlinerow"><input type="checkbox" data-setup-account="${esc(a.channel_id)}" ${checked.includes(a.channel_id) ? 'checked' : ''}> ${ch ? DeskV1Kit.channelBadge(ch) : esc(a.channel_id)}</label>`;
        }).join('') || '<div class="desk-v1-stub-empty">No accounts on this project yet.</div>'}
      </div>
      <div class="desk-v1-rules-group">
        <div class="desk-v1-rules-group-title">End</div>
        <div class="desk-v1-rules-inlinerow">After <input type="number" min="1" class="desk-v1-rules-numinput" data-setup-postcap value="${esc(camp.plan.end.post_cap || 12)}"> posts</div>
      </div>
      <div class="desk-v1-camp-summary-top-actions">
        <button type="button" class="desk-v1-rules-start-btn" data-setup-draftplan>Draft the plan</button>
      </div>`;

    const actionsHost = el.querySelector('.desk-v1-camp-summary-top-actions');
    function attemptDraftPlan() {
      const picked = Array.from(el.querySelectorAll('[data-setup-account]:checked')).map((c) => c.dataset.setupAccount);
      if (!picked.length) { DeskV1Kit.toast('Pick at least one account.'); return; }

      // §5 UX_PASS Posy task lifecycle's Failed+Retry, reusing the same
      // window.__deskV1PosyForce test hook desk-v1-kit.js:539 documents
      // (IA8 journey forces a failure here before Start). This step has no
      // free-text ask, only Go/no-go, so it renders the shared Failed markup
      // directly rather than routing through bindPosyBox's chat-box
      // machinery — the success path below is UNCHANGED (still synchronous)
      // so the IA4 acceptance smoke's timing is untouched.
      if (window.__deskV1PosyForce === 'fail') {
        const agentName = window.DeskV1Kit ? DeskV1Kit.deskAgentName({ project, campaign: camp }) : 'Your agent';
        actionsHost.innerHTML = `
          <div class="desk-v1-posy-failed" aria-live="polite">&#9888; ${esc(agentName)} couldn't finish: Simulated failure (R0 test hook). Nothing was changed.</div>
          <div class="desk-v1-posy-failed-actions">
            <button type="button" class="btn-secondary" data-posy-retry="1">Retry</button>
          </div>`;
        actionsHost.querySelector('[data-posy-retry]').onclick = attemptDraftPlan;
        return;
      }

      const postCap = parseInt(el.querySelector('[data-setup-postcap]').value, 10) || 12;
      const plan = camp.plan;
      plan.accounts = picked;
      plan.cadence.per_week = _minCeiling(project, picked);
      plan.end = { date: null, post_cap: postCap };
      // §5 IA4 acceptance: "step 3 shows... inherited rows labelled 'from
      // <project>'" — a static record of which bounds this step defaulted,
      // independent of `_effectiveCadence`'s own clamp comparison (kit.js),
      // which stays untouched (T2b's camp-1 unclamped-chip case must not
      // start showing a "from" suffix just because IA4 shipped).
      plan._inheritedFields = project ? { accounts: true, cadence: true, end: true } : {};

      const pieceCount = 3;
      const families = _fx().families = _fx().families || [];
      for (let i = 1; i <= pieceCount; i++) {
        families.push({
          id: `${camp.id}-piece-${i}`,
          campaignId: camp.id,
          kind: 'post',
          title: `${plan.title} — post ${i}`,
          versions: [{ id: `${camp.id}-piece-${i}-v1`, channelId: picked[i % picked.length], state: 'planned', revision: 0 }],
        });
      }

      camp.state = 'proposed';
      camp.setup.step = 3;
      camp.setup.done = [1, 2];
      DeskV1Kit.toast(`Plan drafted — 3 planned pieces added to "${plan.title}".`);
      // R2-3: a draft's map panel starts (and stays) at ① Goal through IA4's
      // own steps, which never touch the map — but Proposed's summary lives
      // on ③ What (desk-v1-rules.js's `deskV1FillProposedContent`, the same
      // "what" default every non-draft state opens to). `deskV1GotoCampaignPanel`
      // alone only refills tabstrip/tabbody/mapfoot (by design, T2's "same
      // DOM node" contract) — it never touches the summary slot this Setup
      // UI lives in, so it must run BEFORE the full `deskV1Render()` below,
      // not instead of it: this updates the stack's `params.panel` to 'what'
      // first, so the full remount's slot loop (desk-v1-shell.js) fills
      // Proposed's own summary AND resolves tabbody against the right panel.
      if (typeof window.deskV1GotoCampaignPanel === 'function') window.deskV1GotoCampaignPanel('what', { campaignId: camp.id });
      if (typeof window.deskV1Render === 'function') window.deskV1Render();
    }

    const btn = el.querySelector('[data-setup-draftplan]');
    if (btn) btn.onclick = attemptDraftPlan;
    _wireMoreBtn(el, camp, () => deskV1FillDraftSetup(el, params, camp));
  }

  // ── Dispatch (§2.3: step 0 presence gate, then steps 1/2; step 3 is the
  // existing Proposed-state page once state flips, see file header). ──────
  function deskV1FillDraftSetup(el, params, camp) {
    // R2-2g: a project-less draft (Home's page-level New campaign) runs the
    // same steps as any other; the project is picked at Launch. Only a draft
    // that HAS a project is held at step 0 until that project has accounts.
    const project = _project(camp.projectId) || null;
    if (project && !DeskV1Kit.validatePresence(project).ok) { _fillStep0(el, params, camp, project); return; }
    const step = (camp.setup && camp.setup.step) || 1;
    if (step <= 1) _fillStep1(el, params, camp, project);
    else _fillStep2(el, params, camp, project);
  }

  window.DeskV1Setup = { deskV1CreateDraftCampaign };
  window.deskV1CreateDraftCampaign = deskV1CreateDraftCampaign;
  window.deskV1MountProjectField = deskV1MountProjectField;
  window.deskV1NewCampaignInProject = deskV1NewCampaignInProject;
  window.deskV1FillDraftSetup = deskV1FillDraftSetup;
  window.deskV1DiscardIfUntouchedDraft = deskV1DiscardIfUntouchedDraft;
})();

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
  function deskV1CreateDraftCampaign(projectId) {
    return {
      id: 'camp-draft-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6),
      state: 'draft', // draft | proposed | active | paused | completed | archived
      projectId,
      subject: null,
      goal: { current: 0 },
      rules: {},
      setup: { step: 1, done: [] },
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

  // ── Step 0: Presence (§2.3, "if the project has no presence yet... once")
  // — reuses IA3's own settings page verbatim (deskV1RenderPresence), mounted
  // into a sub-host so this file never re-derives account/ceiling markup.
  // ────────────────────────────────────────────────────────────────────────
  function _fillStep0(el, params, camp, project) {
    el.innerHTML = `
      <div class="desk-v1-camp-summary-top">
        <span class="desk-v1-camp-state-pill">Setup — Presence</span>
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

  function _defaultSubject(project) {
    return { kind: 'project', ref: project.id, label: project.name };
  }

  function _fillStep1(el, params, camp, project) {
    const subject = camp.subject || _defaultSubject(project);
    const plan = camp.plan;
    if (!plan.title) plan.title = `${project.name} campaign`;
    if (!plan.brief) plan.brief = `Promote ${project.name}.`;
    if (!plan.goal.outcome) plan.goal.outcome = 'awareness';
    el.innerHTML = `
      <div class="desk-v1-camp-summary-top">
        <span class="desk-v1-camp-state-pill">Setup 1 of 3 — Subject + goal</span>
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
        <input type="text" class="desk-v1-rules-textinput" data-setup-title value="${esc(plan.title)}">
      </div>
      <div class="desk-v1-rules-group">
        <div class="desk-v1-rules-group-title">Brief</div>
        <textarea class="desk-v1-rules-textarea" data-setup-brief rows="2">${esc(plan.brief)}</textarea>
      </div>
      <div class="desk-v1-rules-group">
        <div class="desk-v1-rules-group-title">Outcome</div>
        <input type="text" class="desk-v1-rules-textinput" data-setup-outcome value="${esc(plan.goal.outcome)}">
      </div>
      <div class="desk-v1-camp-summary-top-actions">
        <button type="button" class="desk-v1-rules-start-btn" data-setup-continue>Continue</button>
      </div>`;

    let pickedKind = subject.kind;
    el.querySelectorAll('[data-subject-kind]').forEach((btn) => {
      btn.onclick = () => {
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
      camp.subject = { kind: pickedKind, ref: pickedKind === 'project' ? project.id : undefined, label };
      plan.title = title;
      plan.brief = brief;
      plan.goal.outcome = outcome;
      camp.setup.step = 2;
      camp.setup.done = [1];
      deskV1FillDraftSetup(el, params, camp);
    };
  }

  // ── Step 2: Plan (§2.3 table row 2) — "Draft the plan" (Posy task,
  // fixture-only per ground rule 3) pre-ticks every project account,
  // inherits the project's per-account ceiling as the cadence, and creates 3
  // `◇ Planned` pieces (§5 IA4 acceptance: "Proposed with 3 Planned pieces"
  // — row #30/#31 of §3's field table: samples become real pieces, not
  // "samples"). Leaving before this runs keeps the campaign in Draft at step
  // 2 — the project card then shows "Setup 2 of 3" (desk-v1-project.js). ──
  function _minCeiling(project, channelIds) {
    const ceilings = (project.presence && project.presence.ceilings) || {};
    let min = null;
    channelIds.forEach((id) => {
      const c = ceilings[id];
      if (c && c.per_week != null) min = min == null ? c.per_week : Math.min(min, c.per_week);
    });
    return min;
  }

  function _fillStep2(el, params, camp, project) {
    const accounts = (project.presence && project.presence.accounts) || [];
    const checked = camp.plan.accounts.length ? camp.plan.accounts : accounts.map((a) => a.channel_id);
    el.innerHTML = `
      <div class="desk-v1-camp-summary-top">
        <span class="desk-v1-camp-state-pill">Setup 2 of 3 — Plan</span>
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

    const btn = el.querySelector('[data-setup-draftplan]');
    if (btn) btn.onclick = () => {
      const picked = Array.from(el.querySelectorAll('[data-setup-account]:checked')).map((c) => c.dataset.setupAccount);
      if (!picked.length) { DeskV1Kit.toast('Pick at least one account.'); return; }
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
      plan._inheritedFields = { accounts: true, cadence: true, end: true };

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
      if (typeof window.deskV1Render === 'function') window.deskV1Render();
    };
  }

  // ── Dispatch (§2.3: step 0 presence gate, then steps 1/2; step 3 is the
  // existing Proposed-state page once state flips, see file header). ──────
  function deskV1FillDraftSetup(el, params, camp) {
    const project = _project(camp.projectId);
    if (!project) { el.innerHTML = '<div class="desk-v1-stub-inline">Project not found.</div>'; return; }
    if (!DeskV1Kit.validatePresence(project).ok) { _fillStep0(el, params, camp, project); return; }
    const step = (camp.setup && camp.setup.step) || 1;
    if (step <= 1) _fillStep1(el, params, camp, project);
    else _fillStep2(el, params, camp, project);
  }

  window.DeskV1Setup = { deskV1CreateDraftCampaign };
  window.deskV1CreateDraftCampaign = deskV1CreateDraftCampaign;
  window.deskV1NewCampaignInProject = deskV1NewCampaignInProject;
  window.deskV1FillDraftSetup = deskV1FillDraftSetup;
})();

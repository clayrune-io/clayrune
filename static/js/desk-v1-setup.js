// Desk v1 (MC-977) — draft-campaign factory + the ⑥ Launch Project field.
// Window-bridged module, no `import` (ground rule 1, T0a). Started life as
// IA4's 3-step in-page setup (docs/THE_DESK_V1_IA_REVISION.md §2.3); R2-3b
// (docs/THE_DESK_V1_IA_REVISION_2.md §8) retired those steps — a new campaign
// now renders the ①–⑥ map stepper like every other campaign and lands on
// ① Goal — so what remains is what the map still needs: creating a Draft,
// the Project select that ⑥ Launch mounts (R2-2g), and discarding a Draft
// nobody touched. The filename stays so index.html's script list and the
// `window.deskV1*` names the rest of the Desk calls don't move.
//
// Fixtures only (ground rule 3): a draft campaign is a plain object pushed
// onto DeskV1Fixtures.campaigns, same client-side contract every other v1
// surface uses.
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
  // creates a draft before any project is chosen and ⑥ Launch's Project
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
      // R2-3 (IA revision 2 §3 table): the map stepper's resume cursor — a
      // fresh draft starts at ① Goal, same stop the project page's draft card
      // and `_renderCampaignSkeleton` fall back to for any older draft
      // fixture that predates this field.
      map: { stop: 'goal', done: [] },
      // R2-3b: with IA4's Subject/Title/Brief step gone nothing else names a
      // campaign, so a draft that starts inside a project is named for it (and
      // a project-less one is named when ⑥ Launch picks its project —
      // `_bindProjectField`); Clayrune's Plan stop (R2-18) is where it is
      // meant to be renamed.
      plan: {
        brief: _defaultBrief(_project(projectId)), title: _defaultTitle(_project(projectId)), audience: '',
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
  // whole map without one. Every change goes through the commandBus so
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
          if (!plan.title || (prevProject && plan.title === _defaultTitle(prevProject))) plan.title = _defaultTitle(nextProject);
          if (!plan.brief || (prevProject && plan.brief === _defaultBrief(prevProject))) plan.brief = _defaultBrief(nextProject);
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
  // covered by `_touched`, set when a project is picked at Launch.
  function _isUntouchedDraft(camp) {
    if (!camp || camp.state !== 'draft' || !camp._discardIfUntouched || camp._touched) return false;
    if ((camp.projectId || null) !== (camp._prefillProjectId || null)) return false;
    if (camp.subject) return false;
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

  window.DeskV1Setup = { deskV1CreateDraftCampaign };
  window.deskV1CreateDraftCampaign = deskV1CreateDraftCampaign;
  window.deskV1MountProjectField = deskV1MountProjectField;
  window.deskV1NewCampaignInProject = deskV1NewCampaignInProject;
  window.deskV1DiscardIfUntouchedDraft = deskV1DiscardIfUntouchedDraft;
})();

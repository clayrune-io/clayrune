// Desk v1 (MC-977) — draft-campaign factory + the ⑥ Launch Project field.
// Window-bridged module, no `import` (ground rule 1, T0a). Started life as
// IA4's 3-step in-page setup (docs/THE_DESK_V1_IA_REVISION.md §2.3); R2-3b
// (docs/THE_DESK_V1_IA_REVISION_2.md §8) retired those steps — a new campaign
// now renders the ①–⑥ map stepper like every other campaign and lands on
// ① Brief (R2-18) — so what remains is what the map still needs: creating a Draft,
// the Project select that ⑥ Launch mounts (R2-2g), and discarding a Draft
// nobody touched. The filename stays so index.html's script list and the
// `window.deskV1*` names the rest of the Desk calls don't move.
//
// A draft campaign is a plain object pushed onto the store's `campaigns`
// (R1-W S1: through DeskV1Store.write, so live mode also POSTs it to
// /api/desk/campaigns?shape=v1 and rolls back on a refusal; demo mode, flag
// off, stays client-side with Undo and calls nothing).
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function _fx() { return window.DeskV1Store.state(); }
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
      // fresh draft starts at ① Brief, same stop the project page's draft card
      // and `_renderCampaignSkeleton` fall back to for any older draft
      // fixture that predates this field.
      map: { stop: 'how', done: [] },
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

  // Campaign ids whose POST is still in flight. A PATCH or DELETE for the same
  // id must wait for it (`deskV1AfterCampaignSaved`), or the server would see
  // the edit before the campaign exists and the create after the discard.
  const _saving = new Map();
  function deskV1AfterCampaignSaved(id) { return _saving.get(id) || Promise.resolve(); }

  // R1-W S2: whole top-level keys of `camp` PATCHed to the server (it replaces each
  // key it is sent). `camp.how` and `camp.plan.how` are one object in the client
  // model (desk-v1-how.js) but two after a round trip, so they are re-joined and
  // go up together; the approval/term bookkeeping is never sent: Start / Approve /
  // Renew own it. Resolves the v1 campaign the server now holds.
  function deskV1PatchCampaign(camp, keys) {
    if (camp.how && camp.plan) camp.plan.how = camp.how;
    const body = {};
    (keys || []).forEach((k) => { if (camp[k] !== undefined) body[k] = camp[k]; });
    return deskV1AfterCampaignSaved(camp.id).then(() => window.DeskV1Store.api(
      'PATCH', '/api/desk/campaigns/' + encodeURIComponent(camp.id) + '?shape=v1', body));
  }

  // The state the SERVER decides, copied onto the client's own campaign object
  // (which the surfaces hold by identity) after a Start / Approve / Renew.
  function deskV1AdoptServerCampaign(camp, server) {
    if (!server) return;
    ['state', 'term', 'terms', 'startedAt', 'approval', 'approvals', 'policyRecord'].forEach((k) => {
      if (server[k] === undefined || server[k] === null) delete camp[k]; else camp[k] = server[k];
    });
  }

  // The POST behind a human approval action (start / approve / renew). The
  // client's bounds are saved first so the server snapshots what the user was
  // looking at, then the action runs; the answer is adopted onto `camp`.
  //
  // MC-995: the POST goes through the shared passcode modal (human-proof-modal.js),
  // because the server refuses these three without the retyped dashboard
  // passcode. A wrong one re-prompts inside the modal with the server's error and
  // sends nothing else; Cancel (or any server refusal) throws, so the caller's
  // rollback + toast run exactly as for any other refused write.
  const _ACTION_PROOF = {
    start: { title: 'Start campaign', description: 'Re-enter your dashboard passcode to start this campaign and approve its bounds.' },
    approve: { title: 'Approve campaign bounds', description: 'Re-enter your dashboard passcode to approve the changed bounds of this campaign.' },
    renew: { title: 'Renew campaign', description: 'Re-enter your dashboard passcode to renew this campaign for another term.' },
  };

  async function deskV1CampaignAction(camp, action, saveKeys, body) {
    if (saveKeys && saveKeys.length) await deskV1PatchCampaign(camp, saveKeys);
    if (typeof window.humanProofFetch !== 'function') throw new Error('the passcode prompt is not available');
    const res = await window.humanProofFetch(
      '/api/desk/campaigns/' + encodeURIComponent(camp.id) + '/' + action,
      { method: 'POST', body: JSON.stringify(body || {}) },
      _ACTION_PROOF[action]);
    if (res === null) throw new Error('the dashboard passcode was not entered, so nothing was changed');
    if (!res.ok) throw new Error((res.body && (res.body.error || res.body.message)) || `HTTP ${res.status}`);
    deskV1AdoptServerCampaign(camp, res.body);
    return res.body;
  }

  // Pushes `camp` into the store and, live, creates it server-side. Resolves
  // like DeskV1Store.write. `repaint` redraws whatever page the caller is on.
  function deskV1SaveNewDraft(camp, opts) {
    opts = opts || {};
    const S = window.DeskV1Store;
    return S.write({
      label: opts.label || 'New campaign started',
      apply: () => { _fx().campaigns.push(camp); if (opts.repaint) opts.repaint(); },
      unapply: () => { const arr = _fx().campaigns; const i = arr.findIndex((c) => c.id === camp.id); if (i >= 0) arr.splice(i, 1); },
      repaint: () => { if (typeof window.deskV1Render === 'function') window.deskV1Render(); },
      request: () => {
        const p = S.api('POST', '/api/desk/campaigns?shape=v1', camp);
        const tracked = p.then(() => {}, () => {}).then(() => { if (_saving.get(camp.id) === tracked) _saving.delete(camp.id); });
        _saving.set(camp.id, tracked);
        return p;
      },
      undoRequest: () => S.api('DELETE', '/api/desk/campaigns/' + encodeURIComponent(camp.id)),
    });
  }

  // Opens the new draft's page. Live, only once the server has accepted it: a
  // refusal rolls the draft back, and a page for a campaign that is not there
  // would be a dead end.
  function deskV1OpenNewDraft(saved, projectId, campaignId) {
    const go = () => deskV1Nav('campaign', { campaignId, projectId: projectId || null });
    if (window.DeskV1Store.live()) saved.then((r) => { if (r.ok) go(); }); else go();
  }

  function deskV1NewCampaignInProject(projectId) {
    const camp = deskV1CreateDraftCampaign(projectId);
    const saved = deskV1SaveNewDraft(camp);
    deskV1OpenNewDraft(saved, projectId, camp.id);
  }

  // ── Project field (R2-2f, Ron 2026-09-30: "the project picker on the left
  // and the new campaign on the right are doing almost the same thing") —
  // "＋ New campaign" no longer asks which project up front.
  // R2-2g (Ron 2026-09-30, "the pick a project enforcer should come only at
  // the end before the campaign is launched"): a project-less draft runs the
  // whole map without one; Start stays gated on it at ⑥ Launch.
  // R2-18 (Ron 2026-09-30, later): the select is ALSO the first thing on the
  // ① Brief stop (desk-v1-how.js), still optional there; both panels call
  // `deskV1MountProjectField`. Every change goes through the commandBus so
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
        ${picked ? '' : '<div class="desk-v1-rules-hint" data-setup-project-hint>Its accounts and limits come from the project, and its agents are the ones hired there. A campaign can’t launch without one.</div>'}
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
      // R2-18: the campaign's agent is chosen from the project's hired agents,
      // so a different project drops it (back to that project's own default).
      const prevAgent = camp.how ? camp.how.agent : null;
      const nextProject = _project(nextId);
      const nextTitle = (!plan.title || (prevProject && plan.title === _defaultTitle(prevProject))) ? _defaultTitle(nextProject) : plan.title;
      const nextBrief = (!plan.brief || (prevProject && plan.brief === _defaultBrief(prevProject))) ? _defaultBrief(nextProject) : plan.brief;
      const rerender = () => { if (typeof window.deskV1Render === 'function') window.deskV1Render(); };
      window.DeskV1Store.write({
        label: `Set campaign project to ${nextProject ? nextProject.name : 'none'}`,
        apply: () => {
          camp.projectId = nextId;
          camp._touched = true;
          if (camp.how) camp.how.agent = null;
          plan.title = nextTitle; plan.brief = nextBrief;
          rerender();
        },
        unapply: () => {
          camp.projectId = prevId;
          camp._touched = prevTouched;
          if (camp.how) camp.how.agent = prevAgent;
          plan.title = prevTitle; plan.brief = prevBrief;
        },
        repaint: rerender,
        // PATCH replaces whole top-level keys, so `plan` and `how` go in full.
        request: async () => {
          await deskV1AfterCampaignSaved(camp.id);
          const body = { projectId: nextId, plan };
          if (camp.how) body.how = camp.how;
          return window.DeskV1Store.api('PATCH', '/api/desk/campaigns/' + encodeURIComponent(camp.id) + '?shape=v1', body);
        },
        undoRequest: () => {
          const body = { projectId: prevId, plan };
          if (camp.how) body.how = camp.how;
          return window.DeskV1Store.api('PATCH', '/api/desk/campaigns/' + encodeURIComponent(camp.id) + '?shape=v1', body);
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
    if ((map.done || []).length || (map.stop && map.stop !== 'how')) return false;
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
    // Live, the draft also exists server-side (it was POSTed on creation):
    // delete it too, after any POST still in flight. A failed delete puts the
    // draft back so the page never hides one the server still holds.
    if (i >= 0 && window.DeskV1Store.live()) {
      deskV1AfterCampaignSaved(camp.id)
        .then(() => window.DeskV1Store.api('DELETE', '/api/desk/campaigns/' + encodeURIComponent(camp.id)))
        .catch((e) => {
          arr.push(camp);
          if (window.DeskV1Kit) DeskV1Kit.toast('Could not discard the empty draft: ' + (e && e.message ? e.message : e));
        });
    }
    return i >= 0;
  }

  window.DeskV1Setup = { deskV1CreateDraftCampaign };
  window.deskV1CreateDraftCampaign = deskV1CreateDraftCampaign;
  window.deskV1MountProjectField = deskV1MountProjectField;
  window.deskV1NewCampaignInProject = deskV1NewCampaignInProject;
  window.deskV1SaveNewDraft = deskV1SaveNewDraft;
  window.deskV1OpenNewDraft = deskV1OpenNewDraft;
  window.deskV1AfterCampaignSaved = deskV1AfterCampaignSaved;
  window.deskV1PatchCampaign = deskV1PatchCampaign;
  window.deskV1CampaignAction = deskV1CampaignAction;
  window.deskV1DiscardIfUntouchedDraft = deskV1DiscardIfUntouchedDraft;
})();

// Desk v1 (MC-977 IA revision) — IA1: Project page (docs/THE_DESK_V1_IA_REVISION.md
// §1, §5 row IA1). Window-bridged module, no `import` (ground rule 1, T0a).
//
// Minimal IA1 build: header (name + state), campaign cards scoped to this
// project (subject-glyph per §1's wireframe), and a project-scoped Needs-you
// list. Presence settings (⚙) itself is a later ticket (§5: IA3, IA1's own
// row only asks for the `presence` route as a stub) — this file only wires
// the ⚙ button to that stub route so the affordance exists.
// IA2 (§4 T3 row, §1 wireframe "Posy box scoped About: <project>"): the
// project-level Posy box, keyed `project:<pid>:project` so a draft/ask
// started here is a distinct entry from any campaign/review/video scope
// under the same project, per anyPosyWorking's prefix-match contract.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function _fx() { return window.DeskV1Fixtures || {}; }
  function _projects() { return _fx().projects || []; }
  function _campaigns() { return _fx().campaigns || []; }
  function _families() { return _fx().families || []; }
  function _conversations() { return _fx().conversations || []; }
  function _project(id) { return _projects().find((p) => p.id === id); }

  // §1's wireframe glyphs: "◉ project · ▣ product · ✦ feature · ◎ audience/event".
  const SUBJECT_GLYPH = { project: '◉', product: '▣', feature: '✦', audience: '◎', event: '◎' };

  // §5 IA4 acceptance: "leave at step 2 -> card 'Setup 2 of 3'" — a draft
  // campaign's card names the checklist step it stopped at instead of the
  // generic "✎ Draft" state word, so the project page itself tells you
  // setup is unfinished and where it left off (desk-v1-setup.js owns the
  // step counter this reads, `camp.setup.step`).
  function _draftCardLabel(c) {
    const step = (c.setup && c.setup.step) || 1;
    return `<span class="desk-v1-state-label" data-state="draft">` +
      `<span class="desk-v1-state-glyph" aria-hidden="true">✎</span>` +
      `<span class="desk-v1-state-word">Setup ${esc(step)} of 3</span></span>`;
  }

  function _campCardHTML(c) {
    const subj = c.subject || {};
    const glyph = SUBJECT_GLYPH[subj.kind] || '◉';
    const label = c.state === 'draft'
      ? _draftCardLabel(c)
      : (DeskV1Kit && DeskV1Kit.stateLabelHTML ? DeskV1Kit.stateLabelHTML(c.state) : esc(c.state));
    return `
      <div class="desk-v1-project-camp-card" data-campaign-id="${esc(c.id)}" role="button" tabindex="0">
        <div class="desk-v1-project-camp-top">
          ${label}
          <span class="desk-v1-project-camp-subject" aria-hidden="true">${glyph}</span>
          <span class="desk-v1-project-camp-name">${esc(c.plan.title)}</span>
        </div>
        ${subj.label ? `<div class="desk-v1-project-camp-subject-label">${esc(subj.label)}</div>` : ''}
      </div>`;
  }

  function _renderCampaigns(projectId) {
    const host = document.getElementById('desk-v1-project-camps');
    if (!host) return;
    const camps = _campaigns().filter((c) => c.projectId === projectId && c.state !== 'archived');
    host.innerHTML = camps.length
      ? camps.map(_campCardHTML).join('')
      : '<div class="desk-v1-home-empty">No campaigns yet in this project — use ＋ New campaign to start one.</div>';
    host.querySelectorAll('.desk-v1-project-camp-card').forEach((cardEl) => {
      const campaignId = cardEl.dataset.campaignId;
      const go = () => deskV1Nav('campaign', { campaignId, projectId });
      cardEl.addEventListener('click', go);
      cardEl.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    });
  }

  // Project-scoped Needs-you (§1: "Needs you (this project)") — same states
  // Home's own conversations panel already counts by (`needs_you`,
  // `needs_reply`) plus a `needs_review` piece/video version, filtered to
  // campaigns that belong to THIS project. A separate list from Home's
  // global aggregate (desk-v1-home.js's `_needsYouItems`, unchanged) so
  // adding a project's own row here never touches that function's fixed
  // counts (see desk-v1-fixtures.js's IA1 comment on conv-7/camp-3).
  function _needsYouItemsForProject(projectId) {
    const campIds = new Set(_campaigns().filter((c) => c.projectId === projectId).map((c) => c.id));
    const items = [];
    for (const fam of _families()) {
      if (!campIds.has(fam.campaignId)) continue;
      for (const v of fam.versions) {
        if (v.state !== 'needs_review') continue;
        items.push({ kind: fam.kind === 'video' ? 'video' : 'piece', text: `“${fam.title}” needs review` });
      }
    }
    for (const c of _conversations()) {
      if (!campIds.has(c.campaignId)) continue;
      if (c.state !== 'needs_reply' && c.state !== 'needs_you') continue;
      items.push({ kind: 'reply', text: c.excerpt });
    }
    return items;
  }

  function _renderNeedsYou(projectId) {
    const host = document.getElementById('desk-v1-project-needsyou');
    if (!host) return;
    const items = _needsYouItemsForProject(projectId);
    host.innerHTML = `<div class="desk-v1-home-needsyou-title">Needs you</div>` +
      (items.length
        ? `<div class="desk-v1-home-needsyou-list">${items.map((i) => `
            <div class="desk-v1-home-needsyou-row">
              <span class="desk-v1-home-needsyou-glyph" aria-hidden="true">${i.kind === 'reply' ? '💬' : i.kind === 'video' ? '▶' : '✎'}</span>
              <span class="desk-v1-home-needsyou-text">${esc(i.text)}</span>
            </div>`).join('')}</div>`
        : '<div class="desk-v1-home-needsyou-empty">Nothing needs you right now.</div>');
  }

  function _renderPosyBox(projectId, p) {
    const host = document.getElementById('desk-v1-project-posy');
    if (!host || !window.DeskV1Kit) return;
    const inputId = 'desk-v1-project-posy-input';
    host.innerHTML = DeskV1Kit.posyBoxHTML({ inputId, scopeLabel: p ? p.name : 'Project' });
    DeskV1Kit.bindPosyBox(host, inputId, (text) => {
      DeskV1Kit.toast('Sent to Posy: “' + text + '”');
      DeskV1Kit.paintPosyReadyNoDiff(host);
    }, { draftKey: `project:${projectId}:project`, taskLifecycle: true });
  }

  // ── Pause/Resume (IA3, §2.1: "state ... (pauses every campaign in it)").
  // Pause records each non-archived/completed campaign's own state in
  // `_prePauseState` before forcing it to `paused`, so Resume knows what to
  // put each one BACK to (an active campaign returns to active, a proposed
  // one to proposed) rather than assuming every campaign was running.
  // Resume runs every one of them through the same `DeskV1Kit.validatePlan`
  // gate Start/Renew already use (§5 IA3 acceptance: "Resume runs each
  // through validatePlan") — a campaign that no longer validates against
  // the (possibly narrowed) project stays paused instead of silently
  // reactivating with a bound it can no longer meet.
  function _pausableCampaigns(projectId) {
    return _campaigns().filter((c) => c.projectId === projectId && c.state !== 'archived' && c.state !== 'completed');
  }

  function _pauseProject(projectId, el, params) {
    const p = _project(projectId);
    if (!p) return;
    p.presence = p.presence || {};
    p.presence.state = 'paused';
    let paused = 0;
    _pausableCampaigns(projectId).forEach((c) => {
      if (c.state === 'paused') return;
      c._prePauseState = c.state;
      c.state = 'paused';
      paused++;
    });
    DeskV1Kit.toast(`Paused ${p.name} — ${paused} campaign${paused === 1 ? '' : 's'} paused with it.`);
    deskV1RenderProject(el, params);
  }

  function _resumeProject(projectId, el, params) {
    const p = _project(projectId);
    if (!p) return;
    p.presence = p.presence || {};
    p.presence.state = 'active';
    let resumed = 0, held = 0;
    _pausableCampaigns(projectId).forEach((c) => {
      if (!c._prePauseState) return;
      const result = DeskV1Kit.validatePlan(c.plan, p);
      if (result.ok) {
        c.state = c._prePauseState;
        delete c._prePauseState;
        resumed++;
      } else {
        held++;
      }
    });
    DeskV1Kit.toast(held
      ? `Resumed ${p.name} — ${resumed} campaign${resumed === 1 ? '' : 's'} back running, ${held} still needs setup fixed before it can resume.`
      : `Resumed ${p.name} — ${resumed} campaign${resumed === 1 ? '' : 's'} back running.`);
    deskV1RenderProject(el, params);
  }

  function deskV1RenderProject(el, params) {
    const projectId = (params || {}).projectId;
    const p = _project(projectId);
    const paused = !!(p && p.presence && p.presence.state === 'paused');
    const stateLabel = paused ? 'Paused' : 'Active';
    el.innerHTML = `
      <div class="desk-v1-project">
        <div class="desk-v1-project-header">
          <span class="desk-v1-project-name">${esc(p ? p.name : 'Project')}</span>
          <span class="desk-v1-project-state">${esc(stateLabel)}</span>
          <div class="desk-v1-project-header-actions">
            ${paused
              ? `<button type="button" class="desk-v1-project-pause-btn" data-resume-project-btn>&#9654; Resume project</button>`
              : `<button type="button" class="desk-v1-project-pause-btn" data-pause-project-btn>&#9208; Pause project</button>`}
            <button type="button" class="desk-v1-project-presence-btn">&#9881; Presence</button>
          </div>
        </div>
        <div class="desk-v1-project-camps-head">
          <span>Campaigns</span>
          <button type="button" class="desk-v1-project-newcamp-btn">＋ New campaign</button>
        </div>
        <div class="desk-v1-project-camps" id="desk-v1-project-camps"></div>
        <div class="desk-v1-project-needsyou" id="desk-v1-project-needsyou"></div>
        <div class="desk-v1-project-posy" id="desk-v1-project-posy"></div>
      </div>`;
    _renderCampaigns(projectId);
    _renderNeedsYou(projectId);
    _renderPosyBox(projectId, p);
    const presenceBtn = el.querySelector('.desk-v1-project-presence-btn');
    if (presenceBtn) presenceBtn.onclick = () => deskV1Nav('presence', { projectId });
    const pauseBtn = el.querySelector('[data-pause-project-btn]');
    if (pauseBtn) pauseBtn.onclick = () => _pauseProject(projectId, el, params);
    const resumeBtn = el.querySelector('[data-resume-project-btn]');
    if (resumeBtn) resumeBtn.onclick = () => _resumeProject(projectId, el, params);
    const newCampBtn = el.querySelector('.desk-v1-project-newcamp-btn');
    if (newCampBtn) newCampBtn.onclick = () => window.deskV1NewCampaignInProject(projectId);
  }

  window.deskV1RenderProject = deskV1RenderProject;
})();

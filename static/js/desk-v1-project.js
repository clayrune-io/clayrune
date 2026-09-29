// Desk v1 (MC-977 IA revision) — IA1: Project page (docs/THE_DESK_V1_IA_REVISION.md
// §1, §5 row IA1). Window-bridged module, no `import` (ground rule 1, T0a).
//
// Minimal IA1 build: header (name + state), campaign cards scoped to this
// project (subject-glyph per §1's wireframe), and a project-scoped Needs-you
// list. Presence settings (⚙) and the Posy box are later tickets (§5: IA3,
// IA1's own row only asks for the `presence` route as a stub) — this file
// only wires the ⚙ button to that stub route so the affordance exists.
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

  function _campCardHTML(c) {
    const subj = c.subject || {};
    const glyph = SUBJECT_GLYPH[subj.kind] || '◉';
    const label = DeskV1Kit && DeskV1Kit.stateLabelHTML ? DeskV1Kit.stateLabelHTML(c.state) : esc(c.state);
    return `
      <div class="desk-v1-project-camp-card" data-campaign-id="${esc(c.id)}" role="button" tabindex="0">
        <div class="desk-v1-project-camp-top">
          ${label}
          <span class="desk-v1-project-camp-subject" aria-hidden="true">${glyph}</span>
          <span class="desk-v1-project-camp-name">${esc(c.name)}</span>
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

  function deskV1RenderProject(el, params) {
    const projectId = (params || {}).projectId;
    const p = _project(projectId);
    const stateLabel = p && p.presence && p.presence.state === 'paused' ? 'Paused' : 'Active';
    el.innerHTML = `
      <div class="desk-v1-project">
        <div class="desk-v1-project-header">
          <span class="desk-v1-project-name">${esc(p ? p.name : 'Project')}</span>
          <span class="desk-v1-project-state">${esc(stateLabel)}</span>
          <div class="desk-v1-project-header-actions">
            <button type="button" class="desk-v1-project-pause-btn">&#9208; Pause project</button>
            <button type="button" class="desk-v1-project-presence-btn">&#9881; Presence</button>
          </div>
        </div>
        <div class="desk-v1-project-camps-head">
          <span>Campaigns</span>
          <button type="button" class="desk-v1-project-newcamp-btn">＋ New campaign</button>
        </div>
        <div class="desk-v1-project-camps" id="desk-v1-project-camps"></div>
        <div class="desk-v1-project-needsyou" id="desk-v1-project-needsyou"></div>
      </div>`;
    _renderCampaigns(projectId);
    _renderNeedsYou(projectId);
    const presenceBtn = el.querySelector('.desk-v1-project-presence-btn');
    if (presenceBtn) presenceBtn.onclick = () => deskV1Nav('presence', { projectId });
    const pauseBtn = el.querySelector('.desk-v1-project-pause-btn');
    if (pauseBtn) pauseBtn.onclick = () => DeskV1Kit.toast('Pausing a project lands with the Presence settings ticket (IA3).');
    const newCampBtn = el.querySelector('.desk-v1-project-newcamp-btn');
    if (newCampBtn) newCampBtn.onclick = () => DeskV1Kit.toast('Campaign setup (3 steps) lands in IA4.');
  }

  window.deskV1RenderProject = deskV1RenderProject;
})();

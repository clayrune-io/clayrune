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
  function _channels() { return _fx().channels || []; }
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

  // ── Next post across campaigns (IA6, §5 row IA6: "project Next post =
  // earliest across its campaigns"). Reuses campaign.js's own next-scheduled
  // lookup per campaign (same helper the campaign Overview/Results panels
  // read) so the project page can never disagree with a campaign page about
  // which version is next. Archived campaigns are excluded — same scope as
  // `_renderCampaigns`. ─────────────────────────────────────────────────────
  function _nextPostAcrossCampaigns(projectId) {
    if (typeof window.deskV1CampaignNextScheduled !== 'function') return null;
    let best = null;
    _campaigns().filter((c) => c.projectId === projectId && c.state !== 'archived').forEach((c) => {
      const v = window.deskV1CampaignNextScheduled(c.id);
      if (v && (!best || v.publishAt < best.version.publishAt)) best = { campaign: c, version: v };
    });
    return best;
  }

  function _fmtWhenShort(iso) {
    try {
      const cfg = (typeof _globalConfig !== 'undefined' && _globalConfig) || {};
      return new Intl.DateTimeFormat(undefined, { timeZone: cfg.user_timezone || undefined, weekday: 'short', hour: 'numeric', minute: '2-digit' }).format(new Date(iso));
    } catch (e) { return iso; }
  }

  function _renderNextPost(projectId) {
    const host = document.getElementById('desk-v1-project-nextpost');
    if (!host) return;
    const best = _nextPostAcrossCampaigns(projectId);
    if (!best) {
      host.innerHTML = `<div class="desk-v1-project-nextpost-empty">Nothing scheduled yet across this project’s campaigns.</div>`;
      return;
    }
    const channel = _channels().find((ch) => ch.id === best.version.channelId);
    host.innerHTML = `
      <button type="button" class="desk-v1-project-nextpost" data-nextpost-btn>
        <span class="desk-v1-project-nextpost-label">NEXT POST</span>
        <span class="desk-v1-project-nextpost-text">${esc(_fmtWhenShort(best.version.publishAt))}${channel ? ` on ${esc(channel.label)}` : ''} · ${esc(best.campaign.plan.title)}</span>
      </button>`;
    const btn = host.querySelector('[data-nextpost-btn]');
    if (btn) btn.onclick = () => deskV1Nav('campaign', { campaignId: best.campaign.id, projectId });
  }

  // ── Engagement strip (IA6, §5 row IA6 · IA_REVISION §7 "v1 ... three
  // lanes"): project-scoped counts for the same three lanes the Engagement
  // dashboard itself will render in full once IA7 lands. `Open ›` goes to the
  // existing `engagement` stub route (IA1) — the dashboard's own filtered
  // rendering is IA7's job, not this ticket's (§5 row IA6: "'Open ›' may go
  // to the existing stub route"). Conversations are matched by `projectId`
  // directly (K4: a conversation always has a project, campaign optional),
  // never through a campaign join, so a "No campaign" mention still counts.
  const _ENGAGEMENT_LANES = [
    { key: 'incoming', glyph: '💬', label: 'Incoming', match: (c) => c.state === 'needs_you' },
    { key: 'suggested', glyph: '✎', label: 'Suggested · awaiting you', match: (c) => c.state === 'needs_reply' },
    { key: 'sent', glyph: '✓', label: 'Sent', match: (c) => c.state === 'sent' },
  ];
  function _renderEngagementStrip(projectId) {
    const host = document.getElementById('desk-v1-project-engagement');
    if (!host) return;
    const convs = _conversations().filter((c) => c.projectId === projectId);
    const rows = _ENGAGEMENT_LANES.map((lane) => `
      <div class="desk-v1-project-engagement-row">
        <span class="desk-v1-project-engagement-glyph" aria-hidden="true">${esc(lane.glyph)}</span>
        <span class="desk-v1-project-engagement-label">${esc(lane.label)}</span>
        <span class="desk-v1-project-engagement-count">${convs.filter(lane.match).length}</span>
      </div>`).join('');
    host.innerHTML = `
      <div class="desk-v1-project-engagement-head">Engagement</div>
      ${rows}
      <button type="button" class="desk-v1-project-engagement-open" data-engagement-open>Open &rsaquo;</button>`;
    const openBtn = host.querySelector('[data-engagement-open]');
    if (openBtn) openBtn.onclick = () => deskV1Nav('engagement', { projectId });
  }

  // ── Archived campaigns (IA6, §5 row IA6: "archived campaigns reachable on
  // the project page behind a toggle with `More › Restore`" — IA1 removed
  // Home's archived section, so this toggle is the only way back to one
  // until IA7). Collapsed by default; toggling reveals the archived cards,
  // each with a `More ›` trigger offering the single `Restore` action
  // (`window.deskV1RestoreCampaign`, campaign.js — same commandBus/Undo
  // contract Archive itself uses). ────────────────────────────────────────
  let _archivedOpen = false;
  function _archivedCardHTML(c) {
    const subj = c.subject || {};
    const glyph = SUBJECT_GLYPH[subj.kind] || '◉';
    return `
      <div class="desk-v1-project-camp-card desk-v1-project-camp-card-archived" data-campaign-id="${esc(c.id)}">
        <div class="desk-v1-project-camp-top">
          <span class="desk-v1-state-label" data-state="archived"><span class="desk-v1-state-word">Archived</span></span>
          <span class="desk-v1-project-camp-subject" aria-hidden="true">${glyph}</span>
          <span class="desk-v1-project-camp-name">${esc(c.plan.title)}</span>
          <div class="desk-v1-project-archived-more">
            <button type="button" class="desk-v1-project-archived-morebtn" data-archived-more-btn aria-haspopup="menu">More &rsaquo;</button>
          </div>
        </div>
        ${subj.label ? `<div class="desk-v1-project-camp-subject-label">${esc(subj.label)}</div>` : ''}
      </div>`;
  }

  function _renderArchived(projectId) {
    const host = document.getElementById('desk-v1-project-archived');
    if (!host) return;
    const archived = _campaigns().filter((c) => c.projectId === projectId && c.state === 'archived');
    if (!archived.length) { host.innerHTML = ''; return; }
    host.innerHTML = `
      <button type="button" class="desk-v1-project-archived-toggle" data-archived-toggle aria-expanded="${_archivedOpen}">
        ${_archivedOpen ? '▾' : '▸'} Archived campaigns (${archived.length})
      </button>
      ${_archivedOpen ? `<div class="desk-v1-project-camps desk-v1-project-archived-list">${archived.map(_archivedCardHTML).join('')}</div>` : ''}`;
    const toggle = host.querySelector('[data-archived-toggle]');
    if (toggle) toggle.onclick = () => { _archivedOpen = !_archivedOpen; _renderArchived(projectId); };
    if (!_archivedOpen) return;
    host.querySelectorAll('[data-archived-more-btn]').forEach((btn) => {
      btn.onclick = (e) => {
        e.stopPropagation();
        const cardEl = btn.closest('.desk-v1-project-camp-card');
        const campaignId = cardEl.dataset.campaignId;
        const host2 = btn.parentElement;
        const existing = host2.querySelector(':scope > .desk-v1-camp-cardmenu');
        if (existing) { existing.remove(); return; }
        host2.style.position = 'relative';
        const menu = document.createElement('div');
        menu.className = 'desk-v1-camp-cardmenu';
        menu.setAttribute('role', 'menu');
        menu.innerHTML = `<button type="button" data-restore-btn>Restore</button>`;
        host2.appendChild(menu);
        const close = () => { menu.remove(); document.removeEventListener('click', closer); };
        const closer = (ev) => { if (!menu.contains(ev.target) && ev.target !== btn) close(); };
        setTimeout(() => document.addEventListener('click', closer), 0);
        menu.querySelector('[data-restore-btn]').onclick = (ev) => {
          ev.stopPropagation();
          close();
          const camp = _campaigns().find((c) => c.id === campaignId);
          if (!camp || typeof window.deskV1RestoreCampaign !== 'function') return;
          window.deskV1RestoreCampaign(camp, () => {
            _renderCampaigns(projectId);
            _renderArchived(projectId);
          });
        };
      };
    });
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
    const agentRef = DeskV1Kit.deskAgentRef({ project: p });
    host.innerHTML = DeskV1Kit.posyBoxHTML({ inputId, scopeLabel: p ? p.name : 'Project', agentRef });
    DeskV1Kit.bindPosyBox(host, inputId, (text) => {
      DeskV1Kit.toast('Sent to ' + DeskV1Kit.deskAgentName({ project: p }) + ': “' + text + '”');
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
        <div class="desk-v1-project-nextpost-wrap" id="desk-v1-project-nextpost"></div>
        <div class="desk-v1-project-camps-head">
          <span>Campaigns</span>
          <button type="button" class="desk-v1-project-newcamp-btn">＋ New campaign</button>
        </div>
        <div class="desk-v1-project-camps" id="desk-v1-project-camps"></div>
        <div class="desk-v1-project-archived-wrap" id="desk-v1-project-archived"></div>
        <div class="desk-v1-project-needsyou" id="desk-v1-project-needsyou"></div>
        <div class="desk-v1-project-engagement" id="desk-v1-project-engagement"></div>
        <div class="desk-v1-project-posy" id="desk-v1-project-posy"></div>
      </div>`;
    _renderNextPost(projectId);
    _renderCampaigns(projectId);
    _renderArchived(projectId);
    _renderNeedsYou(projectId);
    _renderEngagementStrip(projectId);
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

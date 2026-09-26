// Desk v1 (MC-977) — shell: modal host + router (T0a, docs/desk_v1_r0_plan.md).
// Window-bridged module, no `import` (ground rule 1). Every later ticket's
// surface is a plain `deskV1Render<X>(el, params)` global this file calls —
// none of them touch index.html or this router.
//
// Reuses the SAME modal slot legacy desk.js opens ('__desk', desk.js's
// DESK_MODAL_ID) — desk.js's openDesk() branches here instead of building its
// own modal when the flag is on, so there is only ever one Desk window. The
// literal string is duplicated rather than imported (no cross-module import
// exists in static/js); desk.js's own constant is the source of truth.
(function () {
  const MODAL_ID = '__desk';

  // Route table, pre-registered for every surface so no later ticket needs to
  // touch this file or index.html (ground rule 2). `render` is resolved
  // lazily (not captured at parse time) because stub files may load in any
  // order relative to this one, and a later ticket replaces the stub function
  // in place without this table changing.
  const ROUTES = {
    home:          { parent: null,      label: 'Home',          render: () => window.deskV1RenderHome },
    campaign:      { parent: 'home',    label: _campaignLabel,  render: () => _renderCampaignSkeleton },
    rules:         { parent: 'campaign', label: 'Rules',        render: () => window.deskV1RenderRules },
    // Empty label: T3 renders its own doc-label/count into the crumb-tools
    // slot below instead (frame 12b, one row). Safe only because no route's
    // `parent` is 'review' today — the SAME label also becomes a child
    // route's back-button text (see _routeLabel/deskV1Render above), so a
    // future child of review would need a real label again.
    review:        { parent: 'campaign', label: '',             render: () => window.deskV1RenderReview },
    calendar:      { parent: 'campaign', label: 'Calendar',     render: () => window.deskV1RenderCalendar },
    video:         { parent: 'campaign', label: 'Video',        render: () => window.deskV1RenderVideo },
    conversations: { parent: 'campaign', label: 'Conversations', render: () => window.deskV1RenderConversations },
    results:       { parent: 'campaign', label: 'Results',      render: () => window.deskV1RenderResults },
  };

  function _campaignLabel(params) {
    const camps = (window.DeskV1Fixtures && window.DeskV1Fixtures.campaigns) || [];
    const c = camps.find(x => x.id === (params || {}).campaignId);
    return c ? c.name : 'Campaign';
  }

  function _routeLabel(entry) {
    const r = ROUTES[entry.route];
    if (!r) return entry.route;
    return typeof r.label === 'function' ? r.label(entry.params) : r.label;
  }

  // Stack of {route, params}. Home has no back button (it's the root); every
  // other entry's back label is the ACTUAL previous stack entry's title, not
  // a static route-table parent — so "Home -> campaign -> item" always shows
  // the real path taken, per the ticket's own '<parent>' wording.
  let _stack = [];

  function deskV1Nav(route, params) {
    if (!ROUTES[route]) return;
    _stack.push({ route, params: params || {} });
    deskV1Render();
  }

  function deskV1Back() {
    if (_stack.length > 1) _stack.pop();
    deskV1Render();
  }

  function deskV1Render() {
    if (!_stack.length) _stack.push({ route: 'home', params: {} });
    const entry = _stack[_stack.length - 1];
    const crumb = document.getElementById('desk-v1-crumb');
    const body = document.getElementById('desk-v1-body');
    if (!crumb || !body) return;

    const parentEntry = _stack.length > 1 ? _stack[_stack.length - 2] : null;
    crumb.innerHTML = `
      ${parentEntry
        ? `<button type="button" class="desk-v1-back" onclick="deskV1Back()">&lsaquo; ${esc(_routeLabel(parentEntry))}</button>`
        : ''}
      <span class="desk-v1-crumb-title">${esc(_routeLabel(entry))}</span>
      <div class="desk-v1-crumb-tools" id="desk-v1-crumb-tools"></div>`;

    const routeDef = ROUTES[entry.route];
    const renderFn = routeDef && routeDef.render();
    body.innerHTML = '';
    if (typeof renderFn === 'function') {
      renderFn(body, entry.params);
    } else {
      // Stub file hasn't loaded (or its ticket hasn't landed yet) — an honest
      // placeholder, never a silent blank pane. review's route label is ''
      // (T3 renders its own doc-label into crumb-tools instead), so fall
      // back to a name here or a missing renderer reads " is not built yet.".
      body.innerHTML = `<div class="desk-v1-stub"><div class="desk-v1-stub-body">${esc(_routeLabel(entry) || 'This page')} is not built yet.</div></div>`;
    }
  }

  // The campaign route is a skeleton with 5 fixed slots (summary / tab strip
  // / tab body / right column / add tray), owned by THIS file. Each slot is
  // filled by a `deskV1FillCampaign*` hook that the surface owning it
  // (desk-v1-campaign.js in T0a, replaced in place by T2a/T2b) provides — the
  // skeleton itself never changes when those hooks grow real content.
  function _renderCampaignSkeleton(el, params) {
    el.innerHTML = `
      <div class="desk-v1-campaign">
        <div class="desk-v1-camp-summary" id="desk-v1-camp-summary"></div>
        <div class="desk-v1-camp-tabstrip" id="desk-v1-camp-tabstrip"></div>
        <div class="desk-v1-camp-main">
          <div class="desk-v1-camp-tabbody" id="desk-v1-camp-tabbody"></div>
          <div class="desk-v1-camp-rightcol" id="desk-v1-camp-rightcol"></div>
        </div>
        <div class="desk-v1-camp-addtray" id="desk-v1-camp-addtray"></div>
      </div>`;
    const slots = [
      ['desk-v1-camp-summary', window.deskV1FillCampaignSummary],
      ['desk-v1-camp-tabstrip', window.deskV1FillCampaignTabStrip],
      ['desk-v1-camp-tabbody', window.deskV1FillCampaignTabBody],
      ['desk-v1-camp-rightcol', window.deskV1FillCampaignRightColumn],
      ['desk-v1-camp-addtray', window.deskV1FillCampaignAddTray],
    ];
    for (const [id, fill] of slots) {
      const slotEl = document.getElementById(id);
      if (slotEl && typeof fill === 'function') fill(slotEl, params);
    }
  }

  function deskV1Open() {
    if (openModals.has(MODAL_ID)) {
      const entry = openModals.get(MODAL_ID);
      if (entry.minimized) restoreModal(MODAL_ID);
      focusModal(MODAL_ID);
      deskV1Render();
      return;
    }

    const win = document.createElement('div');
    win.className = 'modal-window';
    win.dataset.modalId = MODAL_ID;
    const content = document.createElement('div');
    content.className = 'modal-content';
    _clampModalSize(content, 1080);
    content.innerHTML = `
      <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
        <span style="font-size:16px;font-weight:700;color:var(--text)">The Desk</span>
        <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
          <button class="modal-minimize" onclick="minimizeModal('${MODAL_ID}')" title="Minimize">&#x2015;</button>
          <button class="modal-close" onclick="closeModalById('${MODAL_ID}')" title="Close">&#10005;</button>
        </div>
      </div>
      <div class="desk-v1-shell">
        <div class="desk-v1-crumb" id="desk-v1-crumb"></div>
        <div class="desk-v1-body" id="desk-v1-body"></div>
      </div>`;
    win.appendChild(content);
    document.getElementById('modal-layer').appendChild(win);

    const z = nextModalZ++;
    win.style.zIndex = z;
    openModals.set(MODAL_ID, { projectId: null, element: win, minimized: false, zIndex: z });
    centerModalElement(win);
    focusModal(MODAL_ID);

    _stack = [{ route: 'home', params: {} }];
    deskV1Render();

    // Opened maximized (T0a spec) — rides the existing snap machinery rather
    // than a bespoke full-size mode; no-op on mobile/narrow (_snapEnabled()).
    if (typeof window.toggleModalMaximize === 'function') window.toggleModalMaximize(MODAL_ID);
  }

  window.deskV1Open = deskV1Open;
  window.deskV1Nav = deskV1Nav;
  window.deskV1Back = deskV1Back;
  window.deskV1Render = deskV1Render;
})();

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
    // IA1 (docs/THE_DESK_V1_IA_REVISION.md §5 row IA1, §1's hierarchy): label
    // renamed Home -> Desk so a project page's Back button reads '‹ Desk'
    // (the doc's own worked example) — Home's OWN crumb-title uses the same
    // string when landed on directly, which is also the modal's title bar.
    home:          { parent: null,      label: 'Desk',          render: () => window.deskV1RenderHome },
    // IA1 §5 row IA1 / §1: campaign's real parent becomes the project page
    // once a project entry sits below it on the stack (Home -> project ->
    // campaign); a caller that still pushes campaign straight from Home
    // (old deep links, desk-v1-harness.mjs's own direct nav) leaves Back
    // reading whatever IS actually below it, per _renderCrumb's "actual
    // previous stack entry" rule two comments below — this field is
    // documentation only, never read by the router itself.
    project:       { parent: 'home',    label: _projectLabel,  render: () => window.deskV1RenderProject },
    campaign:      { parent: 'project', label: _campaignLabel,  render: () => _renderCampaignSkeleton },
    // IA5 (§2.4, §5 row IA5): shell-owned skeleton (header/facet-strip/
    // body/Posy rightcol), same T2 persistent-frame pattern as the campaign
    // route below — desk-v1-piece.js fills the slots via deskV1FillPiece*
    // hooks, it never owns the skeleton itself.
    piece:         { parent: 'campaign', label: _pieceLabel,   render: () => _renderPieceSkeleton },
    // The one place anything external is connected (MC-977 2026-10-01); the
    // Presence screen it replaces on the project page is retired.
    connections:   { parent: 'home',    label: 'Connections',  render: () => window.deskV1RenderConnections },
    // IA1 stub (§5 row IA1: "engagement (stub)") — dashboard lands in IA7.
    engagement:    { parent: 'home',    label: 'Engagement',   render: () => window.deskV1RenderEngagement },
    // Empty label: T3 renders its own doc-label/count into the crumb-tools
    // slot below instead (frame 12b, one row). Safe only because no route's
    // `parent` is 'review' today — the SAME label also becomes a child
    // route's back-button text (see _routeLabel/deskV1Render above), so a
    // future child of review would need a real label again.
    review:        { parent: 'campaign', label: '',             render: () => window.deskV1RenderReview },
    // Dynamic like _campaignLabel above: frame 12d's crumb title is the video
    // family's own title ("Install in two minutes"), not the static word
    // "Video" — falls back to it during intake (no family picked yet).
    video:         { parent: 'campaign', label: _videoLabel,   render: () => window.deskV1RenderVideo },
    // R2-8 (§8, frames 11, 13): Studio is its own Desk route (Home's header
    // `🎬 Studio`); the storyboard is What's Create-new page, so its Back
    // reads `‹ Back to What` (see _backLabel) and its title `New video ·
    // Storyboard`. Both render from desk-v1-studio.js.
    studio:        { parent: 'home',     label: 'Studio',       render: () => window.deskV1RenderStudio },
    storyboard:    { parent: 'campaign', label: 'New video · Storyboard', render: () => window.deskV1RenderStoryboard },
  };

  // T2 (§2, §8): conversations/results/calendar stop being separate routes —
  // `deskV1Nav('results'|'conversations'|'calendar', params)` still works
  // (existing deep links: Home's Needs-you row, the goal button, old smoke
  // navigation) but now resolves to the persistent `campaign` frame with a
  // panel switch instead of pushing a whole new stack entry/page.
  //
  // R2-3 (IA revision 2 §4.1, §7): the tab strip becomes the ①-⑥ map, so the
  // three old panel names retire onto the stop that now absorbs them —
  // `results` -> ① goal (`desk-v1-results.js`), `content` -> ③ what (T2a's
  // own content list, unchanged), `calendar` -> ⑤ when. `when` now renders
  // `desk-v1-calendar.js` directly as its own panel (§4.1's "Absorbs" column)
  // rather than the old content-tab's List/Calendar toggle, so the toggle-once
  // `calendarView` flag this alias used to set is retired with it — nothing
  // sets or reads it any more.
  //
  // R2-3b: `conversations` is no longer a campaign panel at all — no frame 2 to
  // 8 has a Conversations tab, and R2-12 (Engagement landing) is deferred, so
  // the old deep link (Home's Needs-you reply row, old smoke navigation,
  // `deskV1GotoCampaignPanel('conversations', ...)`) now lands on the
  // Engagement route with the same params: `{campaignId}` makes Engagement
  // show that campaign's conversations (desk-v1-engagement.js), exactly the
  // component the tab used to embed.
  const PANEL_ALIASES = { results: 'goal', content: 'what', calendar: 'when' };

  function _campaignLabel(params) {
    const camps = window.DeskV1Store.state().campaigns || [];
    const c = camps.find(x => x.id === (params || {}).campaignId);
    // A draft started from Home (R2-2f) has no title until step 1 fills one.
    return c ? (c.plan.title || 'New campaign') : 'Campaign';
  }

  // IA1: dynamic like _campaignLabel above — a project page's own crumb
  // title is the fixture project's real name (e.g. "Clayrune"), not the
  // word "Project".
  function _projectLabel(params) {
    const projects = window.DeskV1Store.state().projects || [];
    const p = projects.find(x => x.id === (params || {}).projectId);
    return p ? p.name : 'Project';
  }

  // IA1/IA5: a piece's crumb title (and so a child review/video route's Back
  // label, per §1's "review/video -> piece") is the content family's own
  // title. IA5 resolves it by `familyId` first (every real piece-page nav
  // now carries one) and falls back to the `versionId` lookup IA1 shipped
  // (Home's deep link, desk-v1-home.js's deskV1HomeGotoReview, still only
  // carries versionId), same lookup shape as _videoLabel below but keyed
  // the other way round.
  function _pieceLabel(params) {
    const families = window.DeskV1Store.state().families || [];
    const p = params || {};
    let fam = p.familyId ? families.find(f => f.id === p.familyId) : null;
    if (!fam && p.versionId) fam = families.find(f => (f.versions || []).some(v => v.id === p.versionId));
    return fam ? fam.title : 'Piece';
  }

  function _videoLabel(params) {
    const families = window.DeskV1Store.state().families || [];
    const f = families.find(x => x.id === (params || {}).familyId);
    return f ? f.title : 'Video';
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
    params = params || {};
    if (route === 'conversations') route = 'engagement';
    if (Object.prototype.hasOwnProperty.call(PANEL_ALIASES, route)) {
      _gotoCampaignPanel(PANEL_ALIASES[route], params);
      return;
    }
    if (!ROUTES[route]) return;
    _stack.push({ route, params });
    deskV1Render();
  }

  // Same-campaign panel switch stays on the `campaign` stack entry (in place:
  // no push, header/summary/Posy DOM untouched — only tabstrip + tabbody
  // refill, §2's "route stays campaign with params.panel"); a panel request
  // from anywhere else (Home, a different campaign) pushes a fresh campaign
  // entry the same way a plain `deskV1Nav('campaign', ...)` always has.
  // Params are REPLACED, not merged, on every call — carrying forward a
  // stale `conversationId`/`versionId` from a previous visit across an
  // in-place switch would silently keep re-selecting it.
  function deskV1GotoCampaignPanel(panel, params) { _gotoCampaignPanel(panel, params || {}); }

  function _gotoCampaignPanel(panel, params) {
    // R2-3b: a direct caller naming the retired panel gets the Engagement
    // route, same as the `conversations` alias in deskV1Nav.
    if (panel === 'conversations') { deskV1Nav('engagement', params); return; }
    const campaignId = params.campaignId;
    const top = _stack[_stack.length - 1];
    const nextParams = Object.assign({ campaignId }, params, { panel });
    if (top && top.route === 'campaign' && top.params.campaignId === campaignId) {
      top.params = nextParams;
      _renderCampaignPanel(nextParams);
      return;
    }
    _stack.push({ route: 'campaign', params: nextParams });
    deskV1Render();
  }

  function deskV1Back() {
    // R2-2f: backing out of a campaign page discards a Home-started draft the
    // user never touched (desk-v1-setup.js decides; a no-op for every other
    // campaign), so a stray "＋ New campaign" click leaves nothing behind.
    const top = _stack[_stack.length - 1];
    if (top && top.route === 'campaign' && typeof window.deskV1DiscardIfUntouchedDraft === 'function') {
      window.deskV1DiscardIfUntouchedDraft(top.params.campaignId);
    }
    if (_stack.length > 1) _stack.pop();
    deskV1Render();
  }

  // R2-2 (§8 amended row): `Projects: All ▾` lives in the Desk header — one
  // click from Home or a project page lands on the picked project's own page.
  // Rendered by `_renderCrumb` itself (called on every `deskV1Render`) rather
  // than a per-route crumb-tool, since `#desk-v1-crumb-tools` gets overwritten
  // by whichever route owns it (e.g. desk-v1-review.js) and this must survive
  // that. "All" is a label only in R0 — fixtures model two projects and
  // Home always shows both; picking one navigates, it doesn't filter.
  // R2-2g (Ron 2026-09-30, "two project selectors" on a new campaign page):
  // campaign and deeper routes get Back + title only — the campaign's own
  // Project select (Launch stop) is the only project control there.
  const _PROJECTS_PICKER_ROUTES = ['home', 'project'];
  function _projectsPickerHTML(route) {
    if (_PROJECTS_PICKER_ROUTES.indexOf(route) === -1) return '';
    return `<button type="button" class="desk-v1-projects-picker" aria-haspopup="menu">Projects: All &#9662;</button>`;
  }
  function _bindProjectsPicker(crumb) {
    const btn = crumb.querySelector('.desk-v1-projects-picker');
    if (!btn || !window.DeskV1Kit) return;
    window.DeskV1Kit.bindAddToTrigger(btn, () => {
      const projects = window.DeskV1Store.state().projects || [];
      return projects.map((p) => ({ id: p.id, label: p.name }));
    }, (projectId) => { deskV1Nav('project', { projectId }); }, { noAppendNew: true });
  }

  // R2-7 (§4.3): a piece is a drill-in under the campaign's ③ What stop, so its
  // Back reads `‹ <campaign> · What` — the stop it returns to, taken from the
  // parent campaign entry's own panel. Every other route keeps the plain parent
  // title.
  function _backLabel(entry, parentEntry) {
    if (entry.route === 'storyboard' && parentEntry.route === 'campaign') return 'Back to What';
    const label = _routeLabel(parentEntry);
    if (entry.route === 'piece' && parentEntry.route === 'campaign') {
      const words = (window.DeskV1Kit && window.DeskV1Kit.MAP_STOP_WORDS) || {};
      const stop = words[parentEntry.params.panel || 'what'];
      if (stop) return `${label} · ${stop}`;
    }
    return label;
  }

  function _renderCrumb(entry) {
    const crumb = document.getElementById('desk-v1-crumb');
    if (!crumb) return false;
    const parentEntry = _stack.length > 1 ? _stack[_stack.length - 2] : null;
    crumb.innerHTML = `
      ${parentEntry
        ? `<button type="button" class="desk-v1-back" onclick="deskV1Back()">&lsaquo; ${esc(_backLabel(entry, parentEntry))}</button>`
        : ''}
      <span class="desk-v1-crumb-title">${esc(_routeLabel(entry))}</span>
      ${_projectsPickerHTML(entry.route)}
      <div class="desk-v1-crumb-tools" id="desk-v1-crumb-tools"></div>`;
    _bindProjectsPicker(crumb);
    return true;
  }

  // Lets a route patch its OWN current-entry params in place and refresh just
  // the crumb title off them — no stack push. T5 needs this jumping straight
  // from the video intake sheet to a freshly-created family's Director: the
  // params-based crumb title (_videoLabel, mirroring _campaignLabel above)
  // would otherwise keep reading the stale pre-creation params (no familyId
  // yet) until the next real navigation, and a stack push here would make
  // Back land on the intake sheet it replaced instead of the campaign.
  function deskV1PatchParams(patch) {
    if (!_stack.length) return;
    Object.assign(_stack[_stack.length - 1].params, patch || {});
    _renderCrumb(_stack[_stack.length - 1]);
  }

  // Pops the stack until the top entry's route is `route` (or only the root
  // remains), then renders once. For a deep-link route whose OWN render
  // function immediately bounces back to an ancestor page (T2b's `rules`:
  // it never paints anything under its own breadcrumb, see
  // deskV1RenderRules) — a plain deskV1Nav there would PUSH the ancestor on
  // top of the deep-link entry instead of landing on the one already below
  // it, so Back read '‹ Rules' instead of the real previous page (MC-977
  // Dave review pass 2, T2b popover from T5's "Raise budget…" link).
  function deskV1PopTo(route) {
    while (_stack.length > 1 && _stack[_stack.length - 1].route !== route) _stack.pop();
    deskV1Render();
  }

  function deskV1Render() {
    if (!_stack.length) _stack.push({ route: 'home', params: {} });
    const entry = _stack[_stack.length - 1];
    const body = document.getElementById('desk-v1-body');
    if (!_renderCrumb(entry) || !body) return;

    const routeDef = ROUTES[entry.route];
    const renderFn = routeDef && routeDef.render();
    body.innerHTML = '';
    // R1-W S0: `desk_v1_live` off is DEMO MODE, a mode the user chose: every
    // page carries the banner, so fixtures are never mistaken for the workspace.
    // The banner sits outside #desk-v1-body, so a panel switch never drops it.
    const banner = document.getElementById('desk-v1-demo-banner');
    if (banner) banner.hidden = !window.DeskV1Store.demo();
    // While the live store is loading or has failed, say so instead of painting
    // a surface over nothing. A failed live load never falls back to demo data.
    const gate = window.DeskV1Store.gate();
    if (gate) { _renderStoreGate(body, gate); return; }
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

  function _renderStoreGate(body, gate) {
    body.innerHTML = `<div class="desk-v1-stub" data-store-gate="${esc(gate.kind)}">
      <div class="desk-v1-stub-body">${esc(gate.message)}</div>
      ${gate.kind === 'error' ? '<button type="button" class="desk-v1-stub-link" data-store-retry>Try again</button>' : ''}
    </div>`;
    const retry = body.querySelector('[data-store-retry]');
    if (retry) retry.addEventListener('click', () => { _loadStoreThenRender(true); });
  }

  // Live mode only (DeskV1Store.load() resolves at once otherwise): paint the
  // gate now, then repaint when the load settles.
  function _loadStoreThenRender(force) {
    const p = window.DeskV1Store.load({ force });
    deskV1Render();
    p.then(() => { if (openModals.has(MODAL_ID)) deskV1Render(); });
  }

  // The campaign route is a skeleton with 6 fixed slots (summary / tab strip
  // / tab body / map foot / right column / add tray), owned by THIS file.
  // Each slot is filled by a `deskV1FillCampaign*` hook that the surface
  // owning it (desk-v1-campaign.js, replaced in place ticket by ticket)
  // provides — the skeleton itself never changes when those hooks grow real
  // content. `desk-v1-camp-mapfoot` is new (R2-3, IA revision 2 §3): the
  // Next/Back pair sits "at the foot of each stop" (§4.1's Movement row), so
  // it lives below the tab body rather than inside the tab strip header.
  function _renderCampaignSkeleton(el, params) {
    // §2/R2-3: "route stays campaign with params.panel" — a fresh/full mount
    // (Home -> campaign, or a different campaign) lands on ③ what (T2a's
    // content list, the same default the old tab strip always opened on),
    // unless the caller asked for another panel (an aliased deep link). A
    // Draft campaign is the one exception (§3 table: "Draft · at <stop> ...
    // Continue lands on that stop") — it resumes at `camp.map.stop`, or ①
    // Brief (how) for a campaign that has never touched the map (`deskV1
    // CreateDraftCampaign` seeds `map.stop: 'how'` (Brief, R2-18), but this falls back the
    // same way for any older/hand-built draft fixture missing the field).
    // Non-draft states ignore `map.stop` here on purpose: it is not a resume
    // cursor for them (Active's own "stage" text is goal pace, not a stop,
    // per the same table row), so opening camp-1 (map.stop:'launch',
    // state:'active') from Home must still land on What, not Launch.
    // Mutating `params` in place (the same object the stack entry holds) so
    // a later in-place switch reads the same default without a second nav.
    if (!params.panel) {
      const camps = window.DeskV1Store.state().campaigns || [];
      const camp = camps.find((c) => c.id === params.campaignId);
      params.panel = (camp && camp.state === 'draft')
        ? ((camp.map && camp.map.stop) || 'how')
        : 'what';
    }
    el.innerHTML = `
      <div class="desk-v1-campaign">
        <div class="desk-v1-camp-summary" id="desk-v1-camp-summary"></div>
        <div class="desk-v1-camp-tabstrip" id="desk-v1-camp-tabstrip"></div>
        <div class="desk-v1-camp-main">
          <div class="desk-v1-camp-tabbody" id="desk-v1-camp-tabbody"></div>
          <div class="desk-v1-camp-rightcol" id="desk-v1-camp-rightcol"></div>
        </div>
        <div class="desk-v1-camp-mapfoot" id="desk-v1-camp-mapfoot"></div>
      </div>`;
    const slots = [
      ['desk-v1-camp-summary', window.deskV1FillCampaignSummary],
      ['desk-v1-camp-rightcol', window.deskV1FillCampaignRightColumn],
    ];
    for (const [id, fill] of slots) {
      const slotEl = document.getElementById(id);
      if (slotEl && typeof fill === 'function') fill(slotEl, params);
    }
    _renderCampaignPanel(params);
  }

  // Fills ONLY the tab strip + tab body + map foot — never touches
  // summary/rightcol/addtray, so a same-campaign panel switch
  // (`_gotoCampaignPanel`'s in-place branch) leaves the header, summary bar
  // and Posy box as the SAME DOM elements (T2 acceptance: "tab click keeps
  // header + Posy DOM node").
  function _renderCampaignPanel(params) {
    const tabstripEl = document.getElementById('desk-v1-camp-tabstrip');
    if (tabstripEl && typeof window.deskV1FillCampaignTabStrip === 'function') window.deskV1FillCampaignTabStrip(tabstripEl, params);
    const mapfootEl = document.getElementById('desk-v1-camp-mapfoot');
    if (mapfootEl && typeof window.deskV1FillCampaignMapFoot === 'function') window.deskV1FillCampaignMapFoot(mapfootEl, params);
    const tabbodyEl = document.getElementById('desk-v1-camp-tabbody');
    if (!tabbodyEl) return;
    const panel = params.panel || 'what';
    // Dave's review (2e24880e follow-up): every stop's renderer (What, How,
    // When, ...) paints into this SAME node — desk-v1-campaign.js's
    // `_runSuggestTask` used to guard its own repaint on "is `_st.el` still
    // in the DOM", which is true regardless of which stop currently owns
    // this element, so a Suggest resolving while parked on How clobbered it
    // with Content-tab HTML. Stamping the requested panel here gives that
    // guard something that actually tracks the active stop.
    tabbodyEl.dataset.panel = panel;
    // R2-3 (§4.1 Absorbs column): ① reads `desk-v1-results.js`, ② how reads
    // `desk-v1-how.js`, ④ Where reads `desk-v1-where.js` and ⑤ When reads
    // `desk-v1-calendar.js` directly, each a standalone panel — ③ what / ⑥ launch
    // are desk-v1-campaign.js's own job, so both fall through to the generic hook.
    if (panel === 'goal' && typeof window.deskV1RenderResults === 'function') {
      window.deskV1RenderResults(tabbodyEl, params);
    } else if (panel === 'when' && typeof window.deskV1RenderCalendar === 'function') {
      window.deskV1RenderCalendar(tabbodyEl, params);
    } else if (panel === 'how' && typeof window.deskV1RenderHow === 'function') {
      // R2-18 Brief: project + agent + strategy + angle + budget + the Suggest task.
      window.deskV1RenderHow(tabbodyEl, params);
    } else if (panel === 'where' && typeof window.deskV1RenderWhere === 'function') {
      // R2-10: the Where board (Messages + one column per account the
      // campaign uses + the SOURCES tray).
      window.deskV1RenderWhere(tabbodyEl, params);
    } else if (typeof window.deskV1FillCampaignTabBody === 'function') {
      window.deskV1FillCampaignTabBody(tabbodyEl, params);
    } else {
      tabbodyEl.innerHTML = `<div class="desk-v1-stub-inline">${esc(panel)} is not built yet.</div>`;
    }
  }

  // IA5 (§2.4, §5 row IA5) + R2-7 (§4.3): piece skeleton — same "shell owns
  // the frame, caller fills slots" contract as `_renderCampaignSkeleton` above
  // (T2's own precedent, §4 table: "reused for the project page and the piece
  // page"). R2-7 retired the four facets: the piece is one drill-in page under
  // ③ What, a single scroll with Copy · Media · Versions, so there is no facet
  // strip or in-place facet switch any more — header, body and the Posy
  // rightcol are all filled once per mount.
  function _renderPieceSkeleton(el, params) {
    el.innerHTML = `
      <div class="desk-v1-piece">
        <div class="desk-v1-piece-header" id="desk-v1-piece-header"></div>
        <div class="desk-v1-camp-main">
          <div class="desk-v1-piece-body" id="desk-v1-piece-body"></div>
          <div class="desk-v1-camp-rightcol" id="desk-v1-piece-rightcol"></div>
        </div>
      </div>`;
    const headerEl = document.getElementById('desk-v1-piece-header');
    if (headerEl && typeof window.deskV1FillPieceHeader === 'function') window.deskV1FillPieceHeader(headerEl, params);
    const rightEl = document.getElementById('desk-v1-piece-rightcol');
    if (rightEl && typeof window.deskV1FillPieceRightColumn === 'function') window.deskV1FillPieceRightColumn(rightEl, params);
    const bodyEl = document.getElementById('desk-v1-piece-body');
    if (!bodyEl) return;
    if (typeof window.deskV1FillPieceBody === 'function') {
      window.deskV1FillPieceBody(bodyEl, params);
    } else {
      bodyEl.innerHTML = '<div class="desk-v1-stub-inline">The piece page is not built yet.</div>';
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
        <div class="desk-v1-demo-banner" id="desk-v1-demo-banner" role="status" hidden>Demo data - not your workspace</div>
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
    if (window.DeskV1Store.live()) _loadStoreThenRender(false);
    else deskV1Render();
  }

  window.deskV1Open = deskV1Open;
  window.deskV1Nav = deskV1Nav;
  window.deskV1Back = deskV1Back;
  // True while the Desk is inside a sub-page (a parent entry exists on the stack);
  // the mobile modal header's ← asks this (mobile.js mcModalHeaderBack, MC-977 G-4).
  window.deskV1CanGoBack = () => _stack.length > 1;
  window.deskV1Render = deskV1Render;
  window.deskV1PatchParams = deskV1PatchParams;
  window.deskV1PopTo = deskV1PopTo;
  window.deskV1GotoCampaignPanel = deskV1GotoCampaignPanel;
})();

// Desk v1 (MC-977) — T5: video intake + director (frames: 11d not in repo,
// 12d; docs/desk_v1_r0_plan.md; THE_DESK_V1_UI.md §5). Window-bridged module,
// no `import` (ground rule 1).
//
// Two internal views share this one `video` route, the same way
// desk-v1-review.js's ONE module serves article vs video variants off a
// `kind` flag: **Intake** (no video piece chosen yet — the 4-tile sheet) and
// **Director** (an existing video piece — player, storyboard, render card).
// Frame 12d draws both side by side purely for documentation economy (one
// canvas, two ideas); on screen each renders full-width alone, matching
// every other T5-adjacent route (T3 review, T4 calendar) — see the final
// report's "layout choices" for why.
//
// Fixtures only (ground rule 3): "Make a storyboard" / Upload / Connect / a
// render all mutate window.DeskV1Fixtures directly through
// DeskV1Kit.commandBus, exactly T3's "client-side over fixture data with
// Undo" contract — there is no backend to write to in R0.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // ── data resolution ────────────────────────────────────────────────────
  function _fx() { return window.DeskV1Fixtures || {}; }
  function _campaign(id) { return (_fx().campaigns || []).find((c) => c.id === id); }
  function _family(id) { return (_fx().families || []).find((f) => f.id === id); }
  function _channel(id) { return (_fx().channels || []).find((c) => c.id === id); }
  function _detail(familyId) { return (_fx().videoDetail || {})[familyId] || {}; }
  function _budget() { return _fx().renderBudget || { rate: 0, spent: 0, limit: 0, currency: 'USD' }; }
  function _videoFamiliesFor(campaignId) {
    return (_fx().families || []).filter((f) => f.campaignId === campaignId && f.kind === 'video');
  }

  function _money(n) { return '$' + (Math.round(n * 100) / 100).toFixed(2); }
  function _mmss(totalSec) {
    const m = Math.floor(totalSec / 60), s = Math.round(totalSec % 60);
    return m + ':' + String(s).padStart(2, '0');
  }

  // Render-job vocabulary (MED-06). NOT the §9 version-state table — a
  // ProductionJob's lifecycle (queued/rendering/ready/failed) is a different
  // domain with no entry there, the same reasoning desk-v1-review.js's own
  // CLAIM_COPY table documents for claim status. A local table, not a
  // stateLabel() misuse, keeps A12's "never invent status prose" intact
  // (this IS the one legitimate place for that prose, declared once).
  const JOB_COPY = {
    queued: { glyph: '◇', word: 'Queued' },
    rendering: { glyph: '⟳', word: 'Rendering' },
    ready: { glyph: '✓', word: 'Ready' },
    failed: { glyph: '✕', word: 'Failed' },
  };

  // ── module state ───────────────────────────────────────────────────────
  let _st = null;
  let _dragSt = null; // pointer-drag.js state slot (one drag at a time)

  function deskV1RenderVideo(el, params) {
    params = params || {};
    const campaignId = params.campaignId;
    const existing = _videoFamiliesFor(campaignId);
    const familyId = (params.familyId && existing.some((f) => f.id === params.familyId))
      ? params.familyId
      : (existing[0] && existing[0].id) || null;
    const view = (params.mode === 'intake' || !familyId) ? 'intake' : 'director';
    _st = {
      el, campaignId, familyId, view,
      tab: 'storyboard', // 'source' | 'storyboard' | 'renders'
      scope: { type: 'whole', id: null, label: 'Whole video' },
      intake: {
        tile: 'create', // 'create' | 'upload' | 'connect' | 'record'
        brief: '', materials: [], uploadResult: null, connectUrl: '', connectResult: null,
      },
    };
    _renderAll();
  }

  function _renderAll() {
    const el = _st.el;
    if (_st.view === 'intake') {
      el.innerHTML = _intakeHTML();
      _wireIntake(el);
    } else {
      el.innerHTML = _directorHTML();
      _wireDirector(el);
    }
  }

  // ── Intake sheet (§5: "from shelf tiles, Add tray, or + New piece ->
  // Video" — those three triggers belong to T1/T2a, which don't exist yet on
  // this branch; deskV1Nav('video', {campaignId}) with no familyId lands
  // here directly, and the director's own "+ Add another video" link
  // (below) reaches it without needing them). ─────────────────────────────
  const TILES = [
    { id: 'create', glyph: '&#10022;', label: 'Create' },
    { id: 'upload', glyph: '&#8593;', label: 'Upload' },
    { id: 'connect', glyph: '&#128279;', label: 'Connect' },
    { id: 'record', glyph: '&#9679;', label: 'Record' },
  ];

  function _intakeHTML() {
    const camp = _campaign(_st.campaignId);
    const s = _st.intake;
    const tilesHTML = TILES.map((t) => `
      <button type="button" class="desk-v1-video-tile${s.tile === t.id ? ' desk-v1-video-tile-active' : ''}" data-intake-tile="${t.id}">
        <span class="desk-v1-video-tile-glyph" aria-hidden="true">${t.glyph}</span>
        <span class="desk-v1-video-tile-label">${t.label}</span>
      </button>`).join('');

    const chipsHTML = s.materials.length
      ? `<div class="desk-v1-video-chips">${s.materials.map((m, i) => `
          <span class="desk-v1-video-chip">${esc(m)} <button type="button" data-chip-remove="${i}" aria-label="Remove">&times;</button></span>`).join('')}</div>`
      : '';

    let bodyHTML;
    if (s.tile === 'upload') {
      bodyHTML = _uploadBodyHTML(s);
    } else if (s.tile === 'connect') {
      bodyHTML = _connectBodyHTML(s);
    } else if (s.tile === 'record') {
      // §5 describes Create/Upload/Connect in full; Record has no scripted
      // behaviour there and no frame of its own — an honest "not in this
      // build" beats inventing a capture flow the spec never described
      // (ground rule: never fabricate).
      bodyHTML = `<div class="desk-v1-video-notice">Recording isn't available in this build yet. Use Create, Upload or Connect instead.</div>`;
    } else {
      bodyHTML = `
        <textarea class="desk-v1-video-brief" id="desk-v1-video-brief" rows="3"
          placeholder="A 40-second install walkthrough for Windows testers, ending on Join the beta.">${esc(s.brief)}</textarea>
        ${chipsHTML}
        <button type="button" class="desk-v1-video-primary" data-intake-create>Make a storyboard</button>
        <div class="desk-v1-video-costline">
          No video-generation charge yet. Uses about 2&cent; of AI time
          ${window.DeskV1Kit ? window.DeskV1Kit.infoIconHTML('video-create-cost') : ''}
        </div>`;
    }

    return `
      <div class="desk-v1-video-intake">
        <div class="desk-v1-video-intake-title">Add a video</div>
        <div class="desk-v1-video-tiles">${tilesHTML}</div>
        <div class="desk-v1-video-intake-body">${bodyHTML}</div>
        ${_existingVideosLinkHTML(camp)}
      </div>`;
  }

  function _existingVideosLinkHTML(camp) {
    const existing = _videoFamiliesFor(_st.campaignId);
    if (!existing.length) return '';
    return `<button type="button" class="desk-v1-stub-link" data-back-to-director="${esc(existing[0].id)}">&lsaquo; Back to ${esc(existing[0].title)}</button>`;
  }

  // Upload (MED source handling): a fixed, deterministic pair of "files" —
  // one passes type/size/codec, one fails — so both outcomes in §5's
  // "validate type, size and codec before accepting" are reachable without a
  // real file input (no backend, no filesystem access in R0).
  const CANNED_UPLOADS = [
    { name: 'install-walkthrough.mp4', sizeMB: 84, codec: 'H.264', ok: true },
    { name: 'raw-capture.mov', sizeMB: 640, codec: 'ProRes', ok: false, reason: 'Exceeds the 500MB limit and uses an unsupported codec (ProRes) — export as H.264 MP4 first.' },
  ];
  function _uploadBodyHTML(s) {
    if (s.uploadResult === 'processing') {
      return `<div class="desk-v1-video-notice">Processing "${esc(s.uploadFile)}"&hellip;</div>`;
    }
    if (s.uploadResult && s.uploadResult.ok === false) {
      return `
        <div class="desk-v1-video-reject">${esc(s.uploadResult.reason)}</div>
        <div class="desk-v1-video-filelist">${CANNED_UPLOADS.map((f) => `
          <button type="button" class="desk-v1-video-filerow" data-upload-file="${esc(f.name)}">${esc(f.name)} &middot; ${f.sizeMB}MB &middot; ${esc(f.codec)}</button>`).join('')}</div>`;
    }
    if (s.uploadResult && s.uploadResult.ok === true) {
      return `<div class="desk-v1-video-notice">"${esc(s.uploadResult.name)}" accepted. <button type="button" class="desk-v1-stub-link" data-intake-create>Make a storyboard</button></div>`;
    }
    return `<div class="desk-v1-video-filelist">${CANNED_UPLOADS.map((f) => `
      <button type="button" class="desk-v1-video-filerow" data-upload-file="${esc(f.name)}">${esc(f.name)} &middot; ${f.sizeMB}MB &middot; ${esc(f.codec)}</button>`).join('')}</div>`;
  }

  // Connect (§5: "creates a reference and reports what's possible: preview /
  // import / link only. Never fabricate a preview" — U11). Classification is
  // deterministic on the typed URL, same convention as review.js's
  // _runValidation, so the three outcomes are all reachable/testable rather
  // than a coin flip.
  function _classifyConnect(url) {
    const u = url.toLowerCase();
    if (u.includes('drive') || u.includes('dropbox')) return { mode: 'import', text: 'This can be imported — Clayrune will pull a copy in and encode it.' };
    if (u.includes('youtube') || u.includes('vimeo')) return { mode: 'preview', text: 'This can be previewed here, but not imported — editing works on a reference, not a local copy.' };
    return { mode: 'link', text: 'Link only — Clayrune can’t preview or import from this source. The reference is saved as a link.' };
  }
  function _connectBodyHTML(s) {
    return `
      <input type="text" class="desk-v1-video-connectinput" id="desk-v1-video-connect-url"
        placeholder="Paste a link&hellip;" value="${esc(s.connectUrl)}" />
      <button type="button" class="desk-v1-video-primary" data-connect-run>Connect</button>
      ${s.connectResult ? `
        <div class="desk-v1-video-notice" data-connect-mode="${esc(s.connectResult.mode)}">${esc(s.connectResult.text)}</div>
        ${s.connectResult.mode !== 'link' ? `<button type="button" class="desk-v1-stub-link" data-intake-create>Make a storyboard</button>` : ''}` : ''}`;
  }

  function _wireIntake(el) {
    el.querySelectorAll('[data-intake-tile]').forEach((b) => b.onclick = () => { _st.intake.tile = b.dataset.intakeTile; _renderAll(); });
    el.querySelectorAll('[data-chip-remove]').forEach((b) => b.onclick = () => { _st.intake.materials.splice(parseInt(b.dataset.chipRemove, 10), 1); _renderAll(); });
    const brief = el.querySelector('#desk-v1-video-brief');
    if (brief) brief.oninput = () => { _st.intake.brief = brief.value; };
    const createBtn = el.querySelector('[data-intake-create]');
    if (createBtn) createBtn.onclick = () => _createStoryboard();
    el.querySelectorAll('[data-upload-file]').forEach((b) => b.onclick = () => _runUpload(b.dataset.uploadFile));
    const connectInput = el.querySelector('#desk-v1-video-connect-url');
    if (connectInput) connectInput.oninput = () => { _st.intake.connectUrl = connectInput.value; };
    const connectRun = el.querySelector('[data-connect-run]');
    if (connectRun) connectRun.onclick = () => _runConnect();
    const backLink = el.querySelector('[data-back-to-director]');
    if (backLink) backLink.onclick = () => {
      const familyId = backLink.dataset.backToDirector;
      deskV1RenderVideo(_st.el, { campaignId: _st.campaignId, familyId });
      if (window.deskV1PatchParams) window.deskV1PatchParams({ familyId });
    };
    if (window.DeskV1Kit) {
      window.DeskV1Kit.bindInfoIcons(el, {
        'video-create-cost': 'Generating a storyboard uses about 2¢ of AI time. You’ll see the render cost separately before anything renders.',
      });
    }
  }

  function _runUpload(fileName) {
    const file = CANNED_UPLOADS.find((f) => f.name === fileName);
    if (!file) return;
    _st.intake.uploadResult = 'processing';
    _st.intake.uploadFile = fileName;
    _renderAll();
    setTimeout(() => {
      _st.intake.uploadResult = file.ok ? { ok: true, name: file.name } : { ok: false, reason: file.reason };
      if (_st.view === 'intake') _renderAll();
    }, 400);
  }

  function _runConnect() {
    const url = (_st.intake.connectUrl || '').trim();
    if (!url) return;
    _st.intake.connectResult = _classifyConnect(url);
    _renderAll();
  }

  // Creates a brand-new video family + its T5 detail row and switches into
  // the Director for it — the one write intake makes to shared fixture
  // state, so it goes through commandBus (Undo removes both rows and
  // returns to intake).
  function _createStoryboard() {
    const camp = _campaign(_st.campaignId);
    const brief = (_st.intake.brief || '').trim() || 'Untitled video';
    const id = 'fam-video-' + Date.now().toString(36);
    const family = { id, campaignId: _st.campaignId, kind: 'video', title: brief.length > 60 ? brief.slice(0, 57) + '…' : brief, versions: [], render: null };
    const detail = { brief, materials: _st.intake.materials.slice(), scenes: [], pendingEdits: [], jobs: [] };
    window.DeskV1Kit.commandBus.run({
      label: 'Started a storyboard for "' + family.title + '"',
      do: () => {
        _fx().families.push(family);
        _fx().videoDetail[id] = detail;
        deskV1RenderVideo(_st.el, { campaignId: _st.campaignId, familyId: id });
        if (window.deskV1PatchParams) window.deskV1PatchParams({ familyId: id });
      },
      undo: () => {
        const idx = _fx().families.findIndex((f) => f.id === id);
        if (idx >= 0) _fx().families.splice(idx, 1);
        delete _fx().videoDetail[id];
        deskV1RenderVideo(_st.el, { campaignId: _st.campaignId, mode: 'intake' });
        if (window.deskV1PatchParams) window.deskV1PatchParams({ familyId: null });
      },
    });
  }

  // ── Director ───────────────────────────────────────────────────────────
  function _totalDuration(detail) { return (detail.scenes || []).reduce((sum, s) => sum + s.durationSec, 0); }

  function _scopeOptions(family, detail) {
    const items = (detail.scenes || []).map((s, i) => ({ id: 'scene:' + s.id, label: 'Scene ' + (i + 1) }));
    items.push({ id: 'whole', label: 'Whole video' });
    for (const v of (family.versions || [])) {
      const ch = _channel(v.channelId);
      items.push({ id: 'version:' + v.id, label: (ch && (ch.label || ch.identity)) || v.id });
    }
    return items;
  }

  function _scopeLabelFor(scope, detail) {
    if (scope.type === 'scene') {
      const idx = (detail.scenes || []).findIndex((s) => s.id === scope.id);
      return idx >= 0 ? 'Scene ' + (idx + 1) : 'Whole video';
    }
    if (scope.type === 'version') return scope.label || 'This version';
    return 'Whole video';
  }

  const VIDEO_TABS = [['source', 'Source'], ['storyboard', 'Storyboard'], ['renders', 'Renders']];

  function _directorHTML() {
    const family = _family(_st.familyId);
    if (!family) return `<div class="desk-v1-stub"><div class="desk-v1-stub-body">This video no longer exists.</div></div>`;
    const detail = _detail(family.id);
    const budget = _budget();

    let tabBody;
    if (_st.tab === 'source') tabBody = _sourceTabHTML(family, detail);
    else if (_st.tab === 'renders') tabBody = _rendersTabHTML(family, detail);
    else tabBody = _storyboardTabHTML(family, detail, budget);

    // "Channel exports" (§5) only appears once more than one export exists —
    // no export object exists anywhere in R0 fixtures, so that condition is
    // never true here; omitted rather than stubbed dead (see final report).
    // "desk-v1-video" (bare, no CSS rule of its own) is pre-existing contract:
    // desk-v1-home.mjs's A12 deep-link check (written when this route was
    // still a stub) waits on ".desk-v1-stub-title:has-text('Video'),
    // .desk-v1-video" — dropping it silently broke that smoke on merge.
    return `<div class="desk-v1-video-director desk-v1-video">
      <div class="desk-v1-video-tabstrip" role="tablist" aria-label="Source, storyboard or renders">
        ${VIDEO_TABS.map(([id, label]) => `<button type="button" data-video-tab="${id}" aria-pressed="${_st.tab === id}">${label}</button>`).join('')}
      </div>
      ${tabBody}
    </div>`;
  }

  function _sourceTabHTML(family, detail) {
    const materials = (detail.materials || []).map((m) => `<div class="desk-v1-video-sourcerow">${esc(m)}</div>`).join('')
      || '<div class="desk-v1-video-sourcerow desk-v1-video-sourcerow-empty">No source material attached yet.</div>';
    return `
      <div class="desk-v1-video-source">
        <div class="desk-v1-video-brief-readout">${esc(detail.brief || 'No brief recorded.')}</div>
        ${materials}
      </div>`;
  }

  function _rendersTabHTML(family, detail) {
    const jobs = detail.jobs || [];
    const rows = jobs.length ? jobs.map((j) => {
      const copy = JOB_COPY[j.status] || JOB_COPY.queued;
      return `<div class="desk-v1-video-jobrow" data-job-id="${esc(j.id)}" data-job-status="${esc(j.status)}">
        <span class="desk-v1-video-jobglyph">${copy.glyph}</span>
        r${j.revision} &middot; ${copy.word} &middot; ${esc((j.formats || []).join(' + '))}
      </div>`;
    }).join('') : '<div class="desk-v1-video-sourcerow-empty">No renders yet.</div>';
    return `<div class="desk-v1-video-renders">${rows}</div>`;
  }

  function _storyboardTabHTML(family, detail, budget) {
    const total = _totalDuration(detail);
    const rendered = family.render && family.render.status === 'ready';
    const stale = (detail.pendingEdits || []).length > 0;
    const reviewCandidate = (family.versions || []).find((v) => v.state === 'needs_review');
    const canWatchReview = !!(reviewCandidate && rendered && family.render.revision === reviewCandidate.revision);

    // The badge reports what the LAST RENDER actually contains, not a
    // recompute from today's (possibly since-edited) scenes — `total` here
    // is only the storyboard's current, unrendered length (used below for
    // the scene-strip proportions and the pending-edits card).
    const lastJob = (detail.jobs || []).find((j) => family.render && j.revision === family.render.revision);
    const lastDuration = lastJob && typeof lastJob.durationSec === 'number' ? lastJob.durationSec : total;
    const statusBadge = stale && family.render
      ? `<div class="desk-v1-video-stalebadge">Previous render &middot; r${family.render.revision} &middot; ${_mmss(lastDuration)} &mdash; edits not rendered yet</div>`
      : '';

    const strip = (detail.scenes || []).map((s, i) => {
      const pct = total ? (s.durationSec / total * 100) : (100 / Math.max(1, (detail.scenes || []).length));
      return `
        ${i > 0 ? `<button type="button" class="desk-v1-video-insertgap" data-insert-at="${i}" aria-label="Insert a scene here" title="Insert a scene here">+</button>` : ''}
        <div class="desk-v1-video-scene${s.edited ? ' desk-v1-video-scene-edited' : ''}" data-scene-id="${esc(s.id)}" style="flex-basis:${pct}%" tabindex="0">
          <span class="desk-v1-video-scene-edge desk-v1-video-scene-edge-l" data-trim-edge="l" data-scene-id="${esc(s.id)}"></span>
          <div class="desk-v1-video-scene-thumb" aria-hidden="true"></div>
          <span class="desk-v1-video-scene-label">${i + 1} &middot; ${esc(s.label)}${s.edited ? ' <span class="desk-v1-video-scene-dot">&#8226;</span>' : ''}</span>
          <span class="desk-v1-video-scene-edge desk-v1-video-scene-edge-r" data-trim-edge="r" data-scene-id="${esc(s.id)}"></span>
        </div>`;
    }).join('');

    const posyHTML = window.DeskV1Kit ? window.DeskV1Kit.posyBoxHTML({
      inputId: 'desk-v1-video-posy-input', scopeLabel: _scopeLabelFor(_st.scope, detail),
      compact: true, sendStyle: 'arrow',
    }) : '';

    const pendingHTML = (detail.pendingEdits || []).length
      ? `<div class="desk-v1-video-pending">
          <div class="desk-v1-video-pending-title">Pending edits</div>
          ${detail.pendingEdits.map((p) => `<div class="desk-v1-video-pending-row">${esc(p.label)}</div>`).join('')}
          <div class="desk-v1-video-pending-total">New total length: ${_mmss(total)}</div>
        </div>` : '';

    return `
      <div class="desk-v1-video-layout">
        <div class="desk-v1-video-main">
          <div class="desk-v1-video-player">
            ${statusBadge}
            <span class="desk-v1-video-playicon" aria-hidden="true">&#9658;</span>
          </div>
          <div class="desk-v1-video-watchrow">
            <button type="button" class="desk-v1-stub-link" data-watch-review ${canWatchReview ? '' : 'disabled'}>Watch and review &rsaquo;</button>
            ${!canWatchReview ? `<span class="desk-v1-video-watchreason">${reviewCandidate ? 'Render this version before you can review it.' : 'Nothing waiting on review yet.'}</span>` : ''}
          </div>
          <div class="desk-v1-video-scenestrip" id="desk-v1-video-scenestrip">${strip}</div>
          <div class="desk-v1-video-posy" id="desk-v1-video-posy">${posyHTML}</div>
          ${pendingHTML}
        </div>
        <div class="desk-v1-video-rail">
          ${_renderCardHTML(family, detail, budget, total)}
        </div>
      </div>`;
  }

  // Render card (MED-04, ADS production costs) — §5's exact structure: Rate
  // / Estimate / Maximum, a coverage line, budget left / after, the Render
  // button (disabled + Raise budget... over remaining), the watch line.
  //
  // Unit = the family's distinct OUTPUT FORMATS (16:9, 9:16, ...) rather
  // than one unit per destination channel — a manual/text channel (the blog)
  // embeds the same encoded clip a direct channel already has, so it needs
  // no separate render. Rate/currency come from the campaign's real
  // production ledger (RENDER_BUDGET, T0a) rather than inventing a second
  // per-second number — see the final report's deviations.
  function _renderCardHTML(family, detail, budget, total) {
    const formats = Array.from(new Set((family.versions || []).map((v) => v.format).filter(Boolean)));
    const units = formats.length || 1;
    const rate = budget.rate || 0;
    const estimate = units * rate;
    const maximum = estimate * 2; // covers one retry per version
    const remaining = budget.limit - budget.spent;
    const after = remaining - maximum;
    const overBudget = maximum > remaining;
    return `
      <div class="desk-v1-video-rendercard">
        <div class="desk-v1-video-rendercard-head">Render &middot; ${esc(formats.join(' + ') || '1 format')} &middot; ${_mmss(total)} each</div>
        <div class="desk-v1-video-renderrow"><span>Rate</span><span>${_money(rate)} / format</span></div>
        <div class="desk-v1-video-renderrow"><span>Estimate</span><span>${units} &times; ${_money(rate)} = ${_money(estimate)}</span></div>
        <div class="desk-v1-video-renderrow"><span>Maximum</span><span>${_money(maximum)}</span></div>
        <div class="desk-v1-video-rendernote">Covers one retry per version if a render fails. Won’t go over this without asking you.</div>
        <div class="desk-v1-video-renderrow desk-v1-video-renderrow-dim"><span>Video budget left</span><span>${_money(remaining)}</span></div>
        <div class="desk-v1-video-renderrow desk-v1-video-renderrow-dim"><span>After, at most</span><span>${_money(after)}</span></div>
        ${overBudget
          ? `<button type="button" class="desk-v1-video-primary" data-raise-budget>Raise budget&hellip;</button>
             <div class="desk-v1-video-renderreason">This render's maximum (${_money(maximum)}) is more than the ${_money(remaining)} left this ${esc(budget.period || 'period')}.</div>`
          : `<button type="button" class="desk-v1-video-primary" data-render-btn>Render &middot; up to ${_money(maximum)}</button>`}
        <div class="desk-v1-video-watchline">You watch it before anything is published.</div>
      </div>`;
  }

  function _wireDirector(el) {
    const family = _family(_st.familyId);
    if (!family) return;
    const detail = _detail(family.id);

    const watchBtn = el.querySelector('[data-watch-review]');
    if (watchBtn && !watchBtn.disabled) watchBtn.onclick = () => {
      const v = (family.versions || []).find((x) => x.state === 'needs_review');
      if (v) window.deskV1Nav('review', { campaignId: _st.campaignId, versionId: v.id });
    };

    const raiseBtn = el.querySelector('[data-raise-budget]');
    if (raiseBtn) raiseBtn.onclick = () => window.deskV1Nav('rules', { campaignId: _st.campaignId });
    const renderBtn = el.querySelector('[data-render-btn]');
    if (renderBtn) renderBtn.onclick = () => _startRender(family, detail);

    el.querySelectorAll('[data-insert-at]').forEach((b) => b.onclick = () => _insertScene(detail, parseInt(b.dataset.insertAt, 10)));
    el.querySelectorAll('[data-video-tab]').forEach((b) => b.onclick = () => { _st.tab = b.dataset.videoTab; _renderAll(); });

    if (window.DeskV1Kit) {
      window.DeskV1Kit.bindInfoIcons(el, {});
      window.DeskV1Kit.bindPosyBox(el.querySelector('#desk-v1-video-posy'), 'desk-v1-video-posy-input', (text) => {
        _sendToPosy(family, detail, text);
      }, { onScopeClick: (trigger) => _openScopeMenu(trigger, family, detail) });
    }

    _wireSceneDrag(el, detail);
  }

  function _openScopeMenu(triggerEl, family, detail) {
    const items = _scopeOptions(family, detail);
    window.DeskV1Kit.addToMenu(triggerEl, items, (id) => {
      if (id === 'whole') { _st.scope = { type: 'whole', id: null, label: 'Whole video' }; }
      else if (id.startsWith('scene:')) { _st.scope = { type: 'scene', id: id.slice(6), label: null }; }
      else if (id.startsWith('version:')) {
        const item = items.find((x) => x.id === id);
        _st.scope = { type: 'version', id: id.slice(8), label: item && item.label };
      }
      _renderAll();
    }, { noAppendNew: true });
  }

  // §5: "Replies state which versions are affected." Scoped to a scene or
  // the whole video, every version still awaiting a destination decision is
  // affected; scoped to one version, only that one is.
  function _sendToPosy(family, detail, text) {
    const scope = _st.scope;
    let affected;
    if (scope.type === 'version') {
      const v = (family.versions || []).find((x) => x.id === scope.id);
      const ch = v && _channel(v.channelId);
      affected = ch ? [ch.label || ch.identity] : ['that version'];
    } else {
      affected = (family.versions || []).filter((v) => v.state !== 'verified_published').map((v) => {
        const ch = _channel(v.channelId);
        return (ch && (ch.label || ch.identity)) || v.id;
      });
    }
    window.DeskV1Kit.toast('Sent to Posy for ' + (affected.length ? affected.join(', ') : 'this video') + ': "' + text + '"', {});
  }

  function _insertScene(detail, index) {
    const name = window.prompt ? window.prompt('Insert a screenshot as a new scene — name it:', 'New scene') : null;
    if (name == null) return;
    const scene = { id: 'sc-' + Date.now().toString(36), label: name.trim() || 'New scene', durationSec: 3, edited: true };
    const editLabel = 'Inserted “' + scene.label + '”';
    window.DeskV1Kit.commandBus.run({
      label: editLabel,
      do: () => { detail.scenes.splice(index, 0, scene); detail.pendingEdits.push({ id: 'pe-' + scene.id, label: editLabel }); _renderAll(); },
      undo: () => {
        const i = detail.scenes.findIndex((s) => s.id === scene.id);
        if (i >= 0) detail.scenes.splice(i, 1);
        const pi = detail.pendingEdits.findIndex((p) => p.id === 'pe-' + scene.id);
        if (pi >= 0) detail.pendingEdits.splice(pi, 1);
        _renderAll();
      },
    });
  }

  // ── scene strip drag: reorder (drag a tile) + edge trim (drag a handle).
  // Uses window.PointerDrag (T0c) exactly as floor.js's drag-to-hire does —
  // mechanics from pointer-drag.js, target semantics (what a drop over
  // another scene means) stay here. §10: Pointer Events, ghost offset from
  // the cursor, a command + toast + Undo on every drop. ────────────────────
  function _wireSceneDrag(el, detail) {
    const strip = el.querySelector('#desk-v1-video-scenestrip');
    if (!strip) return;

    strip.querySelectorAll('[data-trim-edge]').forEach((handle) => {
      handle.addEventListener('pointerdown', (e) => _beginTrim(e, handle, detail));
    });
    strip.querySelectorAll('.desk-v1-video-scene').forEach((tile) => {
      tile.addEventListener('pointerdown', (e) => {
        if (e.target.closest('[data-trim-edge]')) return; // the edge handle owns this gesture
        _beginReorder(e, tile, detail);
      });
    });
  }

  function _beginReorder(e, tileEl, detail) {
    const fromId = tileEl.dataset.sceneId;
    window.PointerDrag.begin(tileEl, e, {
      isDragActive: () => !!_dragSt,
      getDragState: () => _dragSt,
      setDragState: (s) => { _dragSt = s; },
      data: { fromId },
      draggingClass: 'desk-v1-video-scene-dragging',
      ghostClass: 'desk-v1-video-scene-ghost',
      ghostHTML: () => tileEl.querySelector('.desk-v1-video-scene-label').innerHTML,
      ghostRotationDeg: -3,
      ghostOffsetX: 12, ghostOffsetY: 12,
      onMove: (st, x, y) => {
        const overEl = document.elementFromPoint(x, y);
        const overTile = overEl && overEl.closest('.desk-v1-video-scene');
        document.querySelectorAll('.desk-v1-video-scene-target').forEach((n) => n.classList.remove('desk-v1-video-scene-target'));
        if (overTile && overTile.dataset.sceneId !== fromId) overTile.classList.add('desk-v1-video-scene-target');
      },
      onDrop: (st, x, y) => {
        const overEl = document.elementFromPoint(x, y);
        const overTile = overEl && overEl.closest('.desk-v1-video-scene');
        return (overTile && overTile.dataset.sceneId !== fromId) ? overTile.dataset.sceneId : null;
      },
      afterDrop: (st, toId) => { if (toId) _reorderScene(detail, fromId, toId); },
      onTeardown: () => document.querySelectorAll('.desk-v1-video-scene-target').forEach((n) => n.classList.remove('desk-v1-video-scene-target')),
    });
  }

  function _reorderScene(detail, fromId, toId) {
    const fromIdx = detail.scenes.findIndex((s) => s.id === fromId);
    const toIdx = detail.scenes.findIndex((s) => s.id === toId);
    if (fromIdx < 0 || toIdx < 0) return;
    const fromLabel = detail.scenes[fromIdx].label;
    const editLabel = 'Reordered “' + fromLabel + '”';
    window.DeskV1Kit.commandBus.run({
      label: editLabel,
      do: () => {
        const [moved] = detail.scenes.splice(fromIdx, 1);
        // Always land immediately AFTER the tile you dropped on (direction-
        // agnostic): re-finding toId's index post-removal and inserting AT
        // it, not after, silently no-ops a forward drag — removing an
        // earlier scene shifts every later index down by one, so "insert at
        // the target's now-shifted index" puts the dragged scene right back
        // where it started.
        const insertAt = detail.scenes.findIndex((s) => s.id === toId) + 1;
        detail.scenes.splice(insertAt, 0, moved);
        detail.pendingEdits.push({ id: 'pe-reorder-' + Date.now(), label: editLabel });
        _renderAll();
      },
      undo: () => {
        const curIdx = detail.scenes.findIndex((s) => s.id === fromId);
        const [moved] = detail.scenes.splice(curIdx, 1);
        detail.scenes.splice(fromIdx, 0, moved);
        const pi = detail.pendingEdits.findIndex((p) => p.label === editLabel);
        if (pi >= 0) detail.pendingEdits.splice(pi, 1);
        _renderAll();
      },
    });
  }

  // 1px horizontal drag == 0.15s of trim, clamped so a scene never drops
  // below 1s — an arbitrary but fixed, documented conversion (no real video
  // frame data exists in R0 to derive one from).
  const TRIM_PX_PER_SEC = 1 / 0.15;
  function _beginTrim(e, handle, detail) {
    e.stopPropagation();
    const sceneId = handle.dataset.sceneId;
    const scene = detail.scenes.find((s) => s.id === sceneId);
    if (!scene) return;
    const startDuration = scene.durationSec;
    const edge = handle.dataset.trimEdge;
    window.PointerDrag.begin(handle, e, {
      isDragActive: () => !!_dragSt,
      getDragState: () => _dragSt,
      setDragState: (s) => { _dragSt = s; },
      data: { sceneId, startDuration, edge },
      onMove: (st, x, y) => {
        const dx = x - st.startX;
        const dir = edge === 'l' ? -1 : 1;
        const deltaSec = (dx * dir) / TRIM_PX_PER_SEC;
        scene._liveDuration = Math.max(1, Math.round(startDuration + deltaSec));
        // Live width feedback only — the committed re-render (on drop) is
        // what actually recomputes every tile's flex-basis from durations;
        // this just previews the ONE tile moving during the drag itself.
        const tile = document.querySelector(`.desk-v1-video-scene[data-scene-id="${CSS.escape(sceneId)}"]`);
        if (tile) {
          const total = detail.scenes.reduce((sum, s) => sum + (s.id === sceneId ? scene._liveDuration : s.durationSec), 0);
          tile.style.flexBasis = (total ? (scene._liveDuration / total * 100) : 100) + '%';
        }
      },
      onDrop: () => scene._liveDuration || startDuration,
      afterDrop: (st, finalDuration) => _commitTrim(detail, scene, startDuration, finalDuration),
    });
  }

  function _commitTrim(detail, scene, startDuration, finalDuration) {
    delete scene._liveDuration;
    if (finalDuration === startDuration) { _renderAll(); return; }
    const editLabel = 'Trimmed “' + scene.label + '” to ' + finalDuration + 's';
    window.DeskV1Kit.commandBus.run({
      label: editLabel,
      do: () => { scene.durationSec = finalDuration; scene.edited = true; detail.pendingEdits.push({ id: 'pe-trim-' + scene.id + '-' + Date.now(), label: editLabel }); _renderAll(); },
      undo: () => {
        scene.durationSec = startDuration;
        const pi = detail.pendingEdits.map((p) => p.label).lastIndexOf(editLabel);
        if (pi >= 0) detail.pendingEdits.splice(pi, 1);
        _renderAll();
      },
    });
  }

  // ── render (MED-04/06, ADS-03): reserves the MAXIMUM up front (an atomic
  // budget reservation, so a mid-render "raise budget" race can't ever push
  // total spend past what the button promised), refunds the unused half of
  // it once the job completes without a retry. Undo is only offered while
  // the job is still queued — once "rendering" has started there is nothing
  // left for a client-side Undo to reverse (matches T3's own async actions,
  // e.g. _runValidation, which also finalize outside the commandBus). ──────
  function _startRender(family, detail) {
    const budget = _budget();
    const formats = Array.from(new Set((family.versions || []).map((v) => v.format).filter(Boolean)));
    const units = formats.length || 1;
    const estimate = units * (budget.rate || 0);
    const maximum = estimate * 2;
    const nextRevision = (family.render ? family.render.revision : 0) + 1;
    const job = { id: 'render-r' + nextRevision, revision: nextRevision, status: 'queued', formats };

    window.DeskV1Kit.commandBus.run({
      label: 'Started rendering r' + nextRevision + ' (' + formats.join(' + ') + ')',
      do: () => {
        budget.spent += maximum;
        detail.jobs.push(job);
        family.render = { jobId: job.id, status: 'queued', revision: nextRevision };
        _renderAll();
        job._timer = setTimeout(() => {
          job.status = 'rendering';
          if (family.render && family.render.jobId === job.id) family.render.status = 'rendering';
          if (_st.familyId === family.id) _renderAll();
          job._timer = setTimeout(() => {
            job.status = 'ready';
            budget.spent -= (maximum - estimate); // refund the unused retry reserve
            family.render = { jobId: job.id, status: 'ready', revision: nextRevision };
            detail.pendingEdits = [];
            if (_st.familyId === family.id) _renderAll();
          }, 600);
        }, 400);
      },
      undo: () => {
        clearTimeout(job._timer);
        const idx = detail.jobs.findIndex((j) => j.id === job.id);
        if (idx >= 0) detail.jobs.splice(idx, 1);
        budget.spent -= maximum;
        family.render = (nextRevision - 1) > 0 ? { jobId: 'render-r' + (nextRevision - 1), status: 'ready', revision: nextRevision - 1 } : null;
        _renderAll();
      },
    });
  }

  window.deskV1RenderVideo = deskV1RenderVideo;
})();

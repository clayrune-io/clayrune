// Desk v1 (MC-977) — T3: full-width review (frame 12b, docs/desk_v1_r0_plan.md;
// THE_DESK_V1_UI.md §4). Window-bridged module, no `import` (ground rule 1).
//
// Fixtures only (ground rule: R0 has no backend store, no publishing, no
// spend): every Approve/Skip/Archive/Accept-revision mutates the in-memory
// DeskV1Fixtures objects directly through DeskV1Kit.commandBus, which is
// exactly the "client-side over fixture data with Undo" contract the R0 plan
// specifies — there is nothing else for a command to write to in R0.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // ── data resolution ────────────────────────────────────────────────────
  function _fx() { return window.DeskV1Fixtures || {}; }
  function _channel(id) { return (_fx().channels || []).find((c) => c.id === id); }

  // Every {family, version} pair in campaignId with state 'needs_review', in
  // fixture array order — the stepper's "n of m to review" list (§4 header).
  function _needsReviewList(campaignId) {
    const out = [];
    for (const fam of (_fx().families || [])) {
      if (fam.campaignId !== campaignId) continue;
      for (const v of (fam.versions || [])) {
        if (v.state === 'needs_review') out.push({ family: fam, version: v });
      }
    }
    return out;
  }

  function _findFamilyVersion(versionId) {
    for (const fam of (_fx().families || [])) {
      const v = (fam.versions || []).find((x) => x.id === versionId);
      if (v) return { family: fam, version: v };
    }
    return null;
  }

  function _detail(versionId) { return (_fx().reviewDetail || {})[versionId] || {}; }

  // ── timezone (ground rule: user_timezone, empty = host tz). No shared kit
  // helper exists yet in T0a/T0b (only T3 needs one in R0), so this stays
  // local to the one surface that needs it — a later ticket can promote it. ─
  function _userTz() {
    const cfg = (typeof _globalConfig !== 'undefined' && _globalConfig) || {};
    return cfg.user_timezone || undefined;
  }
  function _fmtWhen(iso) {
    if (!iso) return null;
    try {
      return new Intl.DateTimeFormat(undefined, {
        timeZone: _userTz(), weekday: 'short', hour: 'numeric', minute: '2-digit',
      }).format(new Date(iso));
    } catch (e) { return iso; }
  }

  // ── canned Ask-Posy rewrites (§4 selection toolbar). A real per-selection
  // rewrite model is out of scope for fixtures; these are the paragraph-level
  // alternates the frame's own selected passage (p-why) demonstrates, plus
  // two more so the toolbar isn't a dead end on every other paragraph. ──────
  const CANNED_REWRITES = {
    'p-lede': {
      shorter: 'Every run gets a restore point. Roll back to before it went wrong — files, memory and backlog together.',
      less_technical: 'Every time an agent runs, we save a restore point first. If something goes wrong, you can go back to right before it happened.',
      rephrase: 'A restore point is created at the start of every agent run, so a bad run can be undone back to that point — files, memory and backlog together.',
    },
    'p-why': {
      shorter: 'It was a cheap mistake, not a smarter agent.',
      less_technical: 'I once lost an afternoon when an agent changed files it shouldn’t have. The fix wasn’t a smarter agent — it was a cheaper mistake.',
      rephrase: 'An agent that "tidied" a migration folder cost me an afternoon. Not a smarter agent’s fault — a cheaper mistake.',
    },
    'p-closer': {
      shorter: 'Free for Windows beta testers.',
      less_technical: 'Windows beta testers get this at no cost.',
      rephrase: 'Windows beta testers can join at no charge.',
    },
  };
  function _rewriteFor(paraId, style, fallback) {
    const t = CANNED_REWRITES[paraId];
    return (t && t[style]) || fallback;
  }

  // ── claim status → the §4 literal copy (this is NOT the §9 version-state
  // vocabulary the kit owns — §4 spells out its own bespoke claim copy, so
  // that copy is reproduced here verbatim rather than forced through
  // DeskV1Kit.stateLabel, which has no entries for it). ─────────────────────
  const CLAIM_COPY = {
    blocked: { glyph: '⛔', word: '⛔ No source for this.' },
    checked: { glyph: '⟳', word: '⟳ Source checked' },
    doesnt_support: { glyph: '⛔', word: '⛔ Source doesn’t support this' },
    supports: { glyph: '✓', word: '✓ Source supports it' },
    resolved_by_posy: { glyph: '✓', word: '✓ Rewritten — no longer needs a source' },
    removed: { glyph: '·', word: 'Removed' },
  };
  // A7: the block clears ONLY on 'supports' or on accepting a revision.
  function _claimBlocks(status) { return status === 'blocked' || status === 'checked' || status === 'doesnt_support'; }

  // ── module state — one review surface mounted in the shell's single body
  // slot at a time. ─────────────────────────────────────────────────────────
  let _st = null;
  let _selToolbarEl = null;

  function deskV1RenderReview(el, params) {
    params = params || {};
    const campaignId = params.campaignId;
    const list = _needsReviewList(campaignId);
    let versionId = params.versionId;
    if (!versionId || !list.some((x) => x.version.id === versionId)) {
      versionId = (list[0] && list[0].version.id) || null;
    }
    _st = {
      el, campaignId, versionId, mode: 'review', showChanges: true,
      claims: {},      // claimId -> { status, sourceName, revision, addingSource, checking }
      editValues: {},  // paragraphId -> live-edited text (Edit-text mode)
      conflictShown: false,
      commentDraft: {}, // paragraphId -> composer open
    };
    // Seed claim runtime state from the fixture baseline, once per mount.
    const d = _detail(versionId);
    for (const c of (d.claims || [])) {
      _st.claims[c.id] = { status: c.state, sourceName: null, revision: c.revision, addingSource: false, checking: false };
    }
    _renderAll();
  }

  function _pair() { return _st && _st.versionId ? _findFamilyVersion(_st.versionId) : null; }

  // ── render ─────────────────────────────────────────────────────────────
  function _renderAll() {
    const el = _st.el;
    _closeSelToolbar();
    const list = _needsReviewList(_st.campaignId);
    if (!_st.versionId || !list.length) {
      el.innerHTML = `
        <div class="desk-v1-review-empty">
          <div class="desk-v1-review-empty-title">Nothing needs review</div>
          <div class="desk-v1-review-empty-body">Every piece in this campaign is scheduled, published, skipped or archived.</div>
          <button type="button" class="desk-v1-stub-link" onclick="deskV1Back()">‹ Back to campaign</button>
        </div>`;
      return;
    }
    const pair = _pair();
    if (!pair) { el.innerHTML = `<div class="desk-v1-stub"><div class="desk-v1-stub-body">This version no longer exists.</div></div>`; return; }
    const { family, version } = pair;
    const channel = _channel(version.channelId);
    const detail = _detail(version.id);
    const idx = list.findIndex((x) => x.version.id === version.id);
    const isVideo = family.kind === 'video';

    el.innerHTML = `
      <div class="desk-v1-review">
        ${_headerHTML(family, idx, list.length, isVideo)}
        <div class="desk-v1-review-layout">
          <div class="desk-v1-review-main" id="desk-v1-review-main">
            ${isVideo ? _videoBodyHTML(family, version) : _articleBodyHTML(family, version, detail)}
          </div>
          <div class="desk-v1-review-rail">
            ${_railHTML(family, version, channel, detail, isVideo)}
          </div>
        </div>
      </div>`;
    _wireAll(el, family, version, channel, detail, isVideo);
  }

  function _headerHTML(family, idx, total, isVideo) {
    const kindGlyph = isVideo ? '🎥' : '📄';
    const kindWord = isVideo ? 'Video' : 'Article';
    return `
      <div class="desk-v1-review-header">
        <div class="desk-v1-review-kind">${kindGlyph} ${kindWord} · ${idx + 1} of ${total} to review</div>
        <div class="desk-v1-review-controls">
          <div class="desk-v1-review-modetoggle" role="tablist" aria-label="Review or edit">
            <button type="button" data-mode-btn="review" aria-pressed="${_st.mode === 'review'}">👁 Review</button>
            <button type="button" data-mode-btn="edit" aria-pressed="${_st.mode === 'edit'}">✎ Edit text</button>
          </div>
          <button type="button" class="desk-v1-review-showchanges" data-show-changes aria-pressed="${_st.showChanges}">Show changes${_st.showChanges ? ' ✓' : ''}</button>
          <div class="desk-v1-review-stepper">
            <button type="button" data-step="-1" aria-label="Previous needs-you item" ${idx <= 0 ? 'disabled' : ''}>‹</button>
            <button type="button" data-step="1" aria-label="Next needs-you item" ${idx >= total - 1 ? 'disabled' : ''}>›</button>
          </div>
        </div>
      </div>
      <div class="desk-v1-review-savestatus" id="desk-v1-review-savestatus"></div>
      <div id="desk-v1-review-conflict"></div>`;
  }

  // ── article body ───────────────────────────────────────────────────────
  function _articleBodyHTML(family, version, detail) {
    const paras = (detail.paragraphs || []).map((p) => _paragraphHTML(p, detail)).join('');
    return `<div class="desk-v1-review-article" id="desk-v1-review-article" data-mode="${_st.mode}">
      <h1 class="desk-v1-review-title">${esc(family.title)}</h1>
      ${paras}
    </div>`;
  }

  function _paragraphHTML(p, detail) {
    if (p.heading) return `<h2 class="desk-v1-review-h2" data-para-id="${esc(p.id)}">${esc(p.heading)}</h2>`;
    if (p.embedFamilyId) return _embedHTML(p.embedFamilyId);
    if (p.claimId) return _claimParagraphHTML(p, detail);

    const override = _st.editValues[p.id];
    const text = override != null ? override : p.text;
    if (_st.mode === 'edit') {
      return `<p class="desk-v1-review-p" data-para-id="${esc(p.id)}">
        <span class="desk-v1-review-editable" contenteditable="true" data-edit-para="${esc(p.id)}">${esc(text)}</span>${p.linkText ? ` <a href="${esc(p.linkHref || '#')}">${esc(p.linkText)}</a>` : ''}
      </p>`;
    }
    return `<p class="desk-v1-review-p" data-para-id="${esc(p.id)}">${esc(text)}${p.linkText ? ` <a href="${esc(p.linkHref || '#')}">${esc(p.linkText)}</a>` : ''}</p>`;
  }

  function _embedHTML(familyId) {
    const fam = (_fx().families || []).find((f) => f.id === familyId);
    if (!fam) return '';
    // Pick the version actually awaiting review (falls back to any version
    // with a format, then the first) — the caption must describe the SAME
    // version whose format drives the box's shape below it, or the caption
    // and the rendered aspect ratio disagree (frame 12b: "16:9 · r3" on a
    // landscape box, not the published 9:16 cut).
    const v = fam.versions.find((x) => x.state === 'needs_review' && x.format)
      || fam.versions.find((x) => x.format) || fam.versions[0];
    const rev = (fam.render && fam.render.revision) || v.revision;
    return `<div class="desk-v1-review-embed" data-para-id="p-embed" contenteditable="false">
      ${_videoPlaceholderHTML(fam.title, v.format || '16:9', rev, { small: true })}
    </div>`;
  }

  function _videoPlaceholderHTML(title, format, revision, opts) {
    opts = opts || {};
    // aspect-ratio follows the format being captioned — a 9:16 caption on a
    // fixed 16:9 box (or vice versa) is the shape/label mismatch frame 12b
    // never shows.
    const portrait = /^\s*9\s*:\s*16\s*$/.test(format || '');
    const ratio = portrait ? '9 / 16' : '16 / 9';
    return `<div class="desk-v1-review-videobox${opts.small ? ' desk-v1-review-videobox-sm' : ''}" style="aspect-ratio:${ratio}">
      <div class="desk-v1-review-videoinner">
        <span class="desk-v1-review-playicon" aria-hidden="true">▶</span>
        <span class="desk-v1-review-videocaption">[ ${esc(title)} · ${esc(format)} · r${esc(revision)} ]</span>
      </div>
    </div>`;
  }

  function _claimParagraphHTML(p, detail) {
    const meta = (detail.claims || []).find((c) => c.id === p.claimId) || {};
    const st = _st.claims[p.claimId] || { status: 'blocked' };
    const removed = st.status === 'removed';
    if (removed) return '';

    let bodyHTML;
    if (st.status === 'checked') {
      // §4: "Show the result inline" — the proposed revision is an
      // unconditional preview (not gated by Show changes, which governs
      // already-CONFIRMED diffs since the last reviewed revision).
      bodyHTML = `${esc(p.text.replace(p.before, ''))}<del>${esc(p.before)}</del> <ins>${esc(p.after)}</ins>${p.linkText ? ` <a href="${esc(p.linkHref || '#')}">${esc(p.linkText)}</a>` : ''}`;
    } else if (st.status === 'supports' && st.revisedText) {
      bodyHTML = _st.showChanges
        ? `${esc(p.text.replace(p.before, ''))}<del>${esc(p.before)}</del> <ins>${esc(st.revisedText)}</ins>`
        : esc(p.text.replace(p.before, st.revisedText));
    } else {
      bodyHTML = esc(p.text);
    }

    return `<p class="desk-v1-review-p desk-v1-review-p-claim" data-para-id="${esc(p.id)}">${bodyHTML}</p>
      ${_claimBarHTML(p.claimId, meta, st)}`;
  }

  function _claimBarHTML(claimId, meta, st) {
    const copy = CLAIM_COPY[st.status] || CLAIM_COPY.blocked;
    if (st.status === 'blocked') {
      return `<div class="desk-v1-claimbar desk-v1-claimbar-blocked" data-claim-bar="${esc(claimId)}">
        <div class="desk-v1-claimbar-head">${copy.word}</div>
        ${st.addingSource ? _sourceFormHTML(claimId, st) : `
          <div class="desk-v1-claimbar-actions">
            <button type="button" data-claim-fixposy="${esc(claimId)}">Fix with Posy</button>
            <button type="button" data-claim-remove="${esc(claimId)}">Remove</button>
            <button type="button" data-claim-addsource="${esc(claimId)}">Add source</button>
          </div>`}
      </div>`;
    }
    if (st.status === 'doesnt_support') {
      return `<div class="desk-v1-claimbar desk-v1-claimbar-blocked" data-claim-bar="${esc(claimId)}">
        <div class="desk-v1-claimbar-head">${copy.word}</div>
        <div class="desk-v1-claimbar-reason">${esc(st.sourceName || 'That source')} doesn’t verify this as written.</div>
        ${st.addingSource ? _sourceFormHTML(claimId, st) : `
          <div class="desk-v1-claimbar-actions">
            <button type="button" data-claim-addsource="${esc(claimId)}">Try another source</button>
            <button type="button" data-claim-editself="${esc(claimId)}">Edit it myself</button>
            <button type="button" data-claim-remove="${esc(claimId)}">Remove sentence</button>
          </div>`}
      </div>`;
    }
    if (st.status === 'checked') {
      return `<div class="desk-v1-claimbar desk-v1-claimbar-checked" data-claim-bar="${esc(claimId)}">
        <div class="desk-v1-claimbar-head">${copy.word} <span class="desk-v1-claimbar-source">${esc(st.sourceName || '')} · you added it</span></div>
        <div class="desk-v1-claimbar-reason">It doesn’t fully support the claim as written. Posy suggests the revision shown above. The block clears only when you accept a revision.</div>
        <div class="desk-v1-claimbar-actions">
          <button type="button" class="desk-v1-claimbar-accept" data-claim-accept="${esc(claimId)}">Accept → r${st.revision + 1}</button>
          <button type="button" data-claim-editself="${esc(claimId)}">Edit it myself</button>
          <button type="button" data-claim-remove="${esc(claimId)}">Remove sentence</button>
        </div>
      </div>`;
    }
    // supports / resolved_by_posy: a quiet confirmation line, not a block bar.
    return `<div class="desk-v1-claimbar desk-v1-claimbar-ok" data-claim-bar="${esc(claimId)}">${copy.word}</div>`;
  }

  function _sourceFormHTML(claimId, st) {
    if (st.checking) return `<div class="desk-v1-claimbar-checking">Checking…</div>`;
    return `<div class="desk-v1-claimbar-sourceform">
      <input type="text" placeholder="benchmark-sep-22.md" data-source-input="${esc(claimId)}" value="${esc(st.sourceDraft || '')}" />
      <button type="button" data-source-run="${esc(claimId)}">Check</button>
      <button type="button" data-source-cancel="${esc(claimId)}">Cancel</button>
    </div>`;
  }

  // ── video body ─────────────────────────────────────────────────────────
  function _videoBodyHTML(family, version) {
    const rendered = family.render && family.render.status === 'ready' && family.render.revision === version.revision;
    return `<div class="desk-v1-review-video">
      <h1 class="desk-v1-review-title">${esc(family.title)}</h1>
      ${_videoPlaceholderHTML(family.title, version.format || '16:9', family.render ? family.render.revision : version.revision, {})}
      ${!rendered ? `<div class="desk-v1-review-videonote">This output hasn’t rendered at the current revision yet — approval opens once it has (MED-05).</div>` : ''}
    </div>`;
  }

  // ── right rail ─────────────────────────────────────────────────────────
  function _railHTML(family, version, channel, detail, isVideo) {
    const hasSchedule = !!detail.whenISO;
    const rendered = !isVideo || (family.render && family.render.status === 'ready' && family.render.revision === version.revision);
    const blockedClaim = (detail.claims || []).map((c) => _st.claims[c.id]).find((s) => s && _claimBlocks(s.status));
    const info = _primaryInfo(channel, hasSchedule, blockedClaim, detail.claims, isVideo, rendered);

    let notice = '';
    if (channel && channel.health === 'held') {
      notice = `<div class="desk-v1-review-notice desk-v1-review-notice-held">⚠ Held — ${esc(channel.holdReason || 'disconnected')}. Reconnect it before this can publish.</div>`;
    } else if (channel && channel.capability === 'manual') {
      notice = `<div class="desk-v1-review-notice">✋ You publish this one. Clayrune can’t post to ${esc(channel.label || channel.identity)}. Approving creates a task with the text, images and link ready to paste.</div>`;
    }

    const whereWhenLink = `
      <div class="desk-v1-review-fact"><span>Where</span> · ${esc(detail.where || (channel && (channel.label || channel.identity)) || '—')}</div>
      <div class="desk-v1-review-fact"><span>When</span> · ${detail.whenISO ? esc(_fmtWhen(detail.whenISO)) + ' ▾' : '—'}</div>
      <div class="desk-v1-review-fact"><span>Link</span> · ${detail.link ? esc(detail.link) : '—'}</div>`;

    const checks = (detail.whyChecks || []).map((c) => {
      let ok = c.ok;
      let label = c.label;
      if (c.claimId) {
        const s = _st.claims[c.claimId];
        ok = s && (s.status === 'supports' || s.status === 'resolved_by_posy');
        label = c.label + (s && s.status === 'checked' ? ' · revision waiting' : '');
      }
      const glyph = ok ? '✓' : '⟳';
      return `<div class="desk-v1-review-check" data-ok="${!!ok}">${glyph} ${esc(label)}</div>`;
    }).join('');

    // A claim already surfaced in the "why" checks (above) is not repeated
    // here — frame 12b's rail has ONE row per reason, never a claim listed
    // twice with two different (and here, contradictory) statuses.
    const checkedClaimIds = new Set((detail.whyChecks || []).map((c) => c.claimId).filter(Boolean));
    const claimsList = (detail.claims || []).filter((c) => !checkedClaimIds.has(c.id)).map((c) => {
      const s = _st.claims[c.id] || { status: c.state };
      const copy = CLAIM_COPY[s.status] || CLAIM_COPY.blocked;
      return `<div class="desk-v1-review-claimrow">${copy.glyph} ${esc(c.label)}</div>`;
    }).join('');

    const posyHTML = window.DeskV1Kit ? window.DeskV1Kit.posyBoxHTML({
      inputId: 'desk-v1-review-posy-input', scopeLabel: 'This article',
    }) : '';

    return `
      ${notice}
      <div class="desk-v1-review-facts">${whereWhenLink}</div>
      ${checks ? `<div class="desk-v1-review-checks">${checks}</div>` : ''}
      ${claimsList ? `<div class="desk-v1-review-claimslist">${claimsList}</div>` : ''}
      <div class="desk-v1-review-posy" id="desk-v1-review-posy">${posyHTML}</div>
      <div class="desk-v1-review-actions">
        <div class="desk-v1-review-actions-row">
          <button type="button" class="desk-v1-review-primary" data-act-primary ${info.disabled ? 'disabled' : ''}>${esc(info.label)}</button>
          ${window.DeskV1Kit ? window.DeskV1Kit.infoIconHTML('review-approve-binding') : ''}
          <div class="desk-v1-review-more">
            <button type="button" class="desk-v1-review-morebtn" data-more-btn aria-haspopup="menu" aria-label="More actions">⋯</button>
          </div>
        </div>
        ${info.reason ? `<div class="desk-v1-review-reason">${esc(info.reason)}</div>` : ''}
        <div class="desk-v1-review-secondary">
          <button type="button" data-act-request>Request changes</button>
          <button type="button" data-act-skip>Skip</button>
          <button type="button" data-act-archive>Archive</button>
        </div>
      </div>`;
  }

  // Primary label: capability × schedule (§4). Doc lists two disable
  // conditions (a claim blocked, a revision waiting); a held destination is a
  // third one added here — approving can't bind a destination that can't
  // currently publish (§9 "Held overrides either"). Not drawn in frame 12b;
  // flagged in the final report as a deviation.
  function _primaryInfo(channel, hasSchedule, blockedClaim, claims, isVideo, rendered) {
    const label = !channel ? 'Approve'
      : channel.capability === 'manual' ? 'Approve and create publishing task'
      : (hasSchedule ? 'Approve and schedule' : 'Approve');
    const reasons = [];
    if (channel && channel.health === 'held') reasons.push(`Reconnect ${channel.label || channel.identity} before scheduling`);
    if (isVideo && !rendered) reasons.push('Render this version before it can be approved');
    if (blockedClaim) {
      const meta = (claims || []).find((c) => _st.claims[c.id] === blockedClaim);
      const slug = meta ? meta.label.toLowerCase().replace(/\s+/g, '-') : 'source';
      if (blockedClaim.status === 'blocked') reasons.push(`Add a source for the ${slug} claim first`);
      else if (blockedClaim.status === 'doesnt_support') reasons.push(`${meta ? meta.label : 'That claim'} needs a different source`);
      else reasons.push(`Accept or edit the ${slug} revision first`);
    }
    return { label, disabled: reasons.length > 0, reason: reasons[0] || null };
  }

  // ── wiring ─────────────────────────────────────────────────────────────
  function _wireAll(el, family, version, channel, detail, isVideo) {
    el.querySelectorAll('[data-mode-btn]').forEach((b) => b.onclick = () => _setMode(b.dataset.modeBtn));
    const scEl = el.querySelector('[data-show-changes]');
    if (scEl) scEl.onclick = () => { _st.showChanges = !_st.showChanges; _renderAll(); };
    el.querySelectorAll('[data-step]').forEach((b) => b.onclick = () => _step(parseInt(b.dataset.step, 10)));

    el.querySelectorAll('[data-claim-addsource]').forEach((b) => b.onclick = () => { _st.claims[b.dataset.claimAddsource].addingSource = true; _renderAll(); });
    el.querySelectorAll('[data-source-cancel]').forEach((b) => b.onclick = () => { _st.claims[b.dataset.sourceCancel].addingSource = false; _renderAll(); });
    el.querySelectorAll('[data-source-input]').forEach((inp) => inp.oninput = () => { _st.claims[inp.dataset.sourceInput].sourceDraft = inp.value; });
    el.querySelectorAll('[data-source-run]').forEach((b) => b.onclick = () => _runValidation(b.dataset.sourceRun, family, version, detail));
    el.querySelectorAll('[data-claim-accept]').forEach((b) => b.onclick = () => _acceptRevision(b.dataset.claimAccept, family, version, detail));
    el.querySelectorAll('[data-claim-editself]').forEach((b) => b.onclick = () => _editSelf(b.dataset.claimEditself, family, version, detail));
    el.querySelectorAll('[data-claim-remove]').forEach((b) => b.onclick = () => _removeSentence(b.dataset.claimRemove, family, version, detail));
    el.querySelectorAll('[data-claim-fixposy]').forEach((b) => b.onclick = () => _fixWithPosy(b.dataset.claimFixposy, family, version, detail));

    el.querySelectorAll('[data-edit-para]').forEach((span) => {
      span.oninput = () => { _st.editValues[span.dataset.editPara] = span.textContent; _scheduleAutosave(); };
    });

    const primary = el.querySelector('[data-act-primary]');
    if (primary) primary.onclick = () => _approve(family, version, channel, detail);
    const req = el.querySelector('[data-act-request]');
    if (req) req.onclick = () => { const ta = document.getElementById('desk-v1-review-posy-input'); if (ta) ta.focus(); };
    const skip = el.querySelector('[data-act-skip]');
    if (skip) skip.onclick = () => _dispose(family, version, 'skipped', 'Skipped');
    const arch = el.querySelector('[data-act-archive]');
    if (arch) arch.onclick = () => _dispose(family, version, 'archived', 'Archived');
    const more = el.querySelector('[data-more-btn]');
    if (more) more.onclick = (e) => _openMoreMenu(e.currentTarget, family, version, channel, detail, isVideo);

    if (window.DeskV1Kit) {
      window.DeskV1Kit.bindPosyBox(el.querySelector('#desk-v1-review-posy'), 'desk-v1-review-posy-input', (text) => {
        window.DeskV1Kit.toast('Sent to Posy: "' + text + '"', {});
      });
      // §4: "Say this once in an ⓘ tooltip; don't print it permanently."
      window.DeskV1Kit.bindInfoIcons(el, {
        'review-approve-binding': 'Approving binds this revision, destination, link, schedule and policy version together — changing any of them invalidates the approval.',
      });
    }

    const article = el.querySelector('#desk-v1-review-article');
    if (article) _wireSelectionToolbar(article);
  }

  function _setMode(mode) {
    if (mode === _st.mode) return;
    _st.mode = mode;
    _renderAll();
    if (mode === 'edit') {
      const status = document.getElementById('desk-v1-review-savestatus');
      if (status) status.textContent = 'Saved';
    }
  }

  function _step(delta) {
    const list = _needsReviewList(_st.campaignId);
    const idx = list.findIndex((x) => x.version.id === _st.versionId);
    const next = list[idx + delta];
    if (!next) return;
    deskV1RenderReview(_st.el, { campaignId: _st.campaignId, versionId: next.version.id });
  }

  let _autosaveTimer = null;
  function _scheduleAutosave() {
    const status = document.getElementById('desk-v1-review-savestatus');
    if (status) status.textContent = 'Saving…';
    clearTimeout(_autosaveTimer);
    _autosaveTimer = setTimeout(() => {
      const s = document.getElementById('desk-v1-review-savestatus');
      if (s) s.textContent = 'Saved';
    }, 600);
  }

  // Simulated conflict (§4 "conflict handling if another editor changed the
  // revision") — exposed as a deterministic hook rather than a timer, so a
  // smoke test can trigger it without racing real time.
  window.__deskV1SimulateEditConflict = function () {
    if (!_st || _st.mode !== 'edit') return false;
    if (_st.conflictShown) return true;
    _st.conflictShown = true;
    const host = document.getElementById('desk-v1-review-conflict');
    if (!host) return false;
    host.innerHTML = `<div class="desk-v1-review-conflictbar">
      Someone changed this while you were editing.
      <button type="button" data-conflict-keep>Keep mine</button>
      <button type="button" data-conflict-theirs>Use theirs</button>
    </div>`;
    host.querySelector('[data-conflict-keep]').onclick = () => { host.innerHTML = ''; };
    host.querySelector('[data-conflict-theirs]').onclick = () => { _st.editValues = {}; host.innerHTML = ''; _renderAll(); };
    return true;
  };

  // ── claim actions ──────────────────────────────────────────────────────
  function _runValidation(claimId, family, version, detail) {
    const st = _st.claims[claimId];
    const draft = (st.sourceDraft || '').trim();
    if (!draft) return;
    st.checking = true;
    _renderAll();
    // Simulated validator (no backend in R0): deterministic on the typed
    // source name so the three §4 outcomes are all reachable and testable,
    // rather than a coin flip a smoke test couldn't assert on.
    setTimeout(() => {
      const lower = draft.toLowerCase();
      const p = (detail.paragraphs || []).find((x) => x.claimId === claimId);
      st.checking = false;
      st.sourceName = draft;
      if (lower.includes('confirms')) {
        st.status = 'supports';
      } else if (lower.includes('reject') || lower.includes('none')) {
        st.status = 'doesnt_support';
      } else {
        st.status = 'checked';
      }
      st.addingSource = false;
      _renderAll();
    }, 250);
  }

  function _acceptRevision(claimId, family, version, detail) {
    const st = _st.claims[claimId];
    const p = (detail.paragraphs || []).find((x) => x.claimId === claimId);
    const rN = st.revision + 1;
    window.DeskV1Kit.commandBus.run({
      label: `Accepted the ${(detail.claims.find((c) => c.id === claimId) || {}).label || 'claim'} revision → r${rN}`,
      do: () => { st.status = 'supports'; st.revisedText = p.after; st.revision = rN; version.revision = rN; _renderAll(); },
      undo: () => { st.status = 'checked'; st.revisedText = null; st.revision = rN - 1; version.revision = rN - 1; _renderAll(); },
    });
  }

  function _editSelf(claimId, family, version, detail) {
    const p = (detail.paragraphs || []).find((x) => x.claimId === claimId);
    const st = _st.claims[claimId];
    const typed = window.prompt ? window.prompt('Rewrite the sentence:', p.after || p.text) : p.after;
    if (typed == null) return;
    const rN = st.revision + 1;
    const prevStatus = st.status;
    window.DeskV1Kit.commandBus.run({
      label: 'Edited the claim sentence yourself → r' + rN,
      do: () => { st.status = 'supports'; st.revisedText = typed; st.revision = rN; version.revision = rN; _renderAll(); },
      undo: () => { st.status = prevStatus; st.revisedText = null; st.revision = rN - 1; version.revision = rN - 1; _renderAll(); },
    });
  }

  function _removeSentence(claimId, family, version, detail) {
    const st = _st.claims[claimId];
    const prevStatus = st.status;
    const rN = st.revision + 1;
    window.DeskV1Kit.commandBus.run({
      label: 'Removed the unsupported sentence → r' + rN,
      do: () => { st.status = 'removed'; st.revision = rN; version.revision = rN; _renderAll(); },
      undo: () => { st.status = prevStatus; st.revision = rN - 1; version.revision = rN - 1; _renderAll(); },
    });
  }

  function _fixWithPosy(claimId, family, version, detail) {
    const p = (detail.paragraphs || []).find((x) => x.claimId === claimId);
    const st = _st.claims[claimId];
    const rN = st.revision + 1;
    const hedge = p.text.replace(p.before, 'keeps recent snapshots automatically — exact retention varies by project size');
    window.DeskV1Kit.commandBus.run({
      label: 'Posy rephrased the claim to remove the unsupported number → r' + rN,
      do: () => { st.status = 'resolved_by_posy'; st.revisedText = hedge; st.revision = rN; version.revision = rN; _renderAll(); },
      undo: () => { st.status = 'blocked'; st.revisedText = null; st.revision = rN - 1; version.revision = rN - 1; _renderAll(); },
    });
  }

  // ── primary / secondary actions ────────────────────────────────────────
  function _approve(family, version, channel, detail) {
    const hasSchedule = !!detail.whenISO;
    const nextState = hasSchedule ? 'scheduled' : 'approved';
    _dispose({ /* no family mutation needed */ }, version, nextState,
      channel && channel.capability === 'manual' ? 'Publishing task created' : (hasSchedule ? 'Scheduled' : 'Approved'));
  }

  function _dispose(family, version, nextState, label) {
    const prev = version.state;
    window.DeskV1Kit.commandBus.run({
      label,
      do: () => { version.state = nextState; _advanceOrEmpty(); },
      undo: () => { version.state = prev; _advanceOrEmpty(); },
    });
  }

  function _advanceOrEmpty() {
    const list = _needsReviewList(_st.campaignId);
    if (list.some((x) => x.version.id === _st.versionId)) { _renderAll(); return; }
    if (list.length) deskV1RenderReview(_st.el, { campaignId: _st.campaignId, versionId: list[0].version.id });
    else deskV1RenderReview(_st.el, { campaignId: _st.campaignId });
  }

  function _openMoreMenu(triggerEl, family, version, channel, detail, isVideo) {
    const host = triggerEl.parentElement;
    const existing = host.querySelector('.desk-v1-review-moremenu');
    if (existing) { existing.remove(); return; }
    const blockedClaim = (detail.claims || []).map((c) => _st.claims[c.id]).find((s) => s && _claimBlocks(s.status));
    const held = channel && channel.health === 'held';
    const rendered = !isVideo || (family.render && family.render.status === 'ready' && family.render.revision === version.revision);
    const manual = channel && channel.capability === 'manual';
    const disabled = manual || held || !!blockedClaim || !rendered;
    const reason = manual ? 'Manual destinations can’t publish now — approving creates the task instead'
      : held ? 'Reconnect the destination first' : (blockedClaim ? 'Resolve the blocked claim first' : (!rendered ? 'Render this version first' : ''));
    const menu = document.createElement('div');
    menu.className = 'desk-v1-review-moremenu';
    menu.setAttribute('role', 'menu');
    menu.innerHTML = `<button type="button" data-publish-now ${disabled ? 'disabled title="' + esc(reason) + '"' : ''}>Publish now…</button>`;
    host.style.position = 'relative';
    host.appendChild(menu);
    if (!disabled) {
      menu.querySelector('[data-publish-now]').onclick = () => { menu.remove(); _dispose(family, version, 'verified_published', 'Published now'); };
    }
    const closer = (e) => { if (!menu.contains(e.target) && e.target !== triggerEl) { menu.remove(); document.removeEventListener('click', closer); } };
    setTimeout(() => document.addEventListener('click', closer), 0);
  }

  // ── selection toolbar (A6: below the selection, covers neither the
  // selection nor the next line). Bound once per mount on the article
  // container; imperative DOM only, no full re-render (would collapse the
  // browser selection). ─────────────────────────────────────────────────────
  let _reflowEl = null;
  let _reflowOrigMargin = '';

  function _closeSelToolbar() {
    if (_selToolbarEl && _selToolbarEl.parentNode) _selToolbarEl.parentNode.removeChild(_selToolbarEl);
    _selToolbarEl = null;
    if (_reflowEl) { _reflowEl.style.marginBottom = _reflowOrigMargin; _reflowEl = null; _reflowOrigMargin = ''; }
  }

  function _wireSelectionToolbar(articleEl) {
    articleEl.addEventListener('mouseup', () => _maybeShowSelToolbar(articleEl));
    articleEl.addEventListener('keyup', (e) => { if (e.shiftKey) _maybeShowSelToolbar(articleEl); });
    document.addEventListener('mousedown', (e) => {
      if (_selToolbarEl && !_selToolbarEl.contains(e.target)) _closeSelToolbar();
    });
  }

  function _maybeShowSelToolbar(articleEl) {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) { _closeSelToolbar(); return; }
    const range = sel.getRangeAt(0);
    if (!articleEl.contains(range.commonAncestorContainer)) { _closeSelToolbar(); return; }
    const anchorEl = range.commonAncestorContainer.nodeType === 1 ? range.commonAncestorContainer : range.commonAncestorContainer.parentElement;
    const para = anchorEl && anchorEl.closest('[data-para-id]');
    // The claim paragraph resolves through its own source-validation flow,
    // not a free rewrite — no toolbar there (keeps A7's gate the only path).
    if (!para || para.dataset.paraId === 'p-claim' || para.dataset.paraId === 'p-embed') { _closeSelToolbar(); return; }

    _closeSelToolbar();
    const rect = range.getBoundingClientRect();
    const hostRect = articleEl.getBoundingClientRect();
    const bar = document.createElement('div');
    bar.className = 'desk-v1-review-seltoolbar';
    bar.innerHTML = `
      <div class="desk-v1-review-seltoolbar-askposy">
        <button type="button" data-sel-askposy>Ask Posy ▾</button>
        <div class="desk-v1-review-askposy-menu" hidden>
          <button type="button" data-sel-style="shorter">Shorter</button>
          <button type="button" data-sel-style="less_technical">Less technical</button>
          <button type="button" data-sel-style="rephrase">Rephrase</button>
          <button type="button" data-sel-style="custom">Custom…</button>
        </div>
      </div>
      <button type="button" data-sel-edit>✎ Edit</button>
      <button type="button" data-sel-comment>💬 Comment</button>`;
    articleEl.style.position = articleEl.style.position || 'relative';
    articleEl.appendChild(bar);
    // Position BELOW the selection with a clear gap so it covers neither the
    // selection nor the following line (A6).
    bar.style.top = (rect.bottom - hostRect.top + 10) + 'px';
    bar.style.left = Math.max(0, rect.left - hostRect.left) + 'px';
    _selToolbarEl = bar;

    // A6 (cont.): a selection on a paragraph's last line leaves only the
    // normal paragraph gap (14px) below it — not enough room for the bar's
    // own height. Rather than let it cover the next paragraph, push that
    // paragraph down by exactly the overlap, restored in _closeSelToolbar.
    const nextEl = para.nextElementSibling;
    if (nextEl) {
      const barRect = bar.getBoundingClientRect();
      const overlap = barRect.bottom - nextEl.getBoundingClientRect().top;
      if (overlap > -4) {
        _reflowEl = para;
        _reflowOrigMargin = para.style.marginBottom;
        const extra = (parseFloat(getComputedStyle(para).marginBottom) || 0) + overlap + 8;
        para.style.marginBottom = extra + 'px';
      }
    }

    const selectedText = sel.toString();
    bar.querySelector('[data-sel-askposy]').onclick = (e) => {
      e.stopPropagation();
      const m = bar.querySelector('.desk-v1-review-askposy-menu');
      m.hidden = !m.hidden;
    };
    bar.querySelectorAll('[data-sel-style]').forEach((b) => b.onclick = () => _applyAskPosy(para.dataset.paraId, b.dataset.selStyle, selectedText));
    bar.querySelector('[data-sel-edit]').onclick = () => _setMode('edit');
    bar.querySelector('[data-sel-comment]').onclick = () => _openCommentComposer(para.dataset.paraId);
  }

  function _applyAskPosy(paraId, style, selectedText) {
    _closeSelToolbar();
    if (style === 'custom') {
      const ta = document.getElementById('desk-v1-review-posy-input');
      if (ta) { ta.value = 'Rewrite: "' + selectedText + '"'; ta.focus(); }
      return;
    }
    const detail = _detail(_st.versionId);
    const p = (detail.paragraphs || []).find((x) => x.id === paraId);
    if (!p) return;
    const before = _st.editValues[paraId] != null ? _st.editValues[paraId] : p.text;
    const after = _rewriteFor(paraId, style, before);
    window.DeskV1Kit.commandBus.run({
      label: 'Applied Posy’s "' + style.replace('_', ' ') + '" rewrite',
      do: () => { _st.editValues[paraId] = after; _renderAll(); },
      undo: () => { delete _st.editValues[paraId]; _renderAll(); },
    });
  }

  function _openCommentComposer(paraId) {
    const p = document.querySelector(`[data-para-id="${CSS.escape(paraId)}"]`);
    if (!p || p.nextElementSibling && p.nextElementSibling.classList.contains('desk-v1-review-comment')) return;
    const box = document.createElement('div');
    box.className = 'desk-v1-review-comment';
    box.innerHTML = `
      <textarea rows="2" placeholder="Comment on this passage…"></textarea>
      <button type="button">Post</button>`;
    p.insertAdjacentElement('afterend', box);
    box.querySelector('textarea').focus();
    box.querySelector('button').onclick = () => {
      const text = box.querySelector('textarea').value.trim();
      if (!text) return;
      box.innerHTML = `<div class="desk-v1-review-comment-posted"><strong>You:</strong> ${esc(text)}</div>
        <div class="desk-v1-review-comment-reply"><strong>Posy:</strong> Noted — I’ll flag this on the next pass.</div>`;
    };
  }

  window.deskV1RenderReview = deskV1RenderReview;
})();

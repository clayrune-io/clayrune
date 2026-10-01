// Desk v1 (MC-977) — T3: full-width review (frame 12b, docs/desk_v1_r0_plan.md;
// THE_DESK_V1_UI.md §4). Window-bridged module, no `import` (ground rule 1).
//
// Two modes, one surface (MC-1021 R1-W S7, docs/desk_v1/R1W_WIRING_PLAN.md §1 Review):
//
//   desk_v1_live OFF  DEMO. Every Approve/Skip/Archive/Accept-revision mutates the
//                     in-memory DeskV1Fixtures objects through DeskV1Kit.commandBus
//                     and calls NOTHING: the original R0 contract.
//   desk_v1_live ON   the version is a stored piece version. The body and claims
//                     are read from it, edits and claim decisions are PATCHed (M18),
//                     "Ask the agent" is M20, and Approve is M19: human-only, the
//                     retyped dashboard passcode every time, and it can PUBLISH, so
//                     the answer (sent, scheduled, held with its reason, failed,
//                     a publishing task to do by hand) is shown, never assumed.
//                     A live claim is checked by a person (a source they name, a
//                     sentence they accept or rewrite); nothing here claims Clayrune
//                     verified a source it did not read.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // ── data resolution ────────────────────────────────────────────────────
  function _fx() { return window.DeskV1Store.state(); }
  function _channel(id) { return (_fx().channels || []).find((c) => c.id === id); }
  function _campaign(id) { return (_fx().campaigns || []).find((c) => c.id === id); }
  function _project(id) { return (_fx().projects || []).find((p) => p.id === id); }
  // R2-5: resolves whoever the project/campaign actually picked, falling
  // back to 'your agent' — same convention every other desk-v1-*.js uses.
  function _agentName(campaign) {
    return window.DeskV1Kit ? DeskV1Kit.deskAgentName({ project: _project(campaign && campaign.projectId), campaign }) : 'your agent';
  }

  function _S() { return window.DeskV1Store; }
  function _live() { return !!(_S() && _S().live()); }
  // What a person can still act on here. Live, that includes what a refusal or
  // a send leaves behind: a version the tick held, one that failed, and one whose
  // outcome is unknown. Demo keeps the one state the fixtures use.
  const _LIVE_ACTIONABLE = ['needs_review', 'held', 'failed', 'unknown_outcome'];
  function _inList(v) { return _live() ? _LIVE_ACTIONABLE.indexOf(v.state) >= 0 : v.state === 'needs_review'; }
  function _versionUrl(family, version, tail) {
    return '/api/desk/pieces/' + encodeURIComponent(family.id) + '/versions/' + encodeURIComponent(version.id) + (tail || '');
  }

  // Every {family, version} pair in campaignId with state 'needs_review', in
  // fixture array order — the stepper's "n of m to review" list (§4 header).
  function _needsReviewList(campaignId) {
    const out = [];
    for (const fam of (_fx().families || [])) {
      if (fam.campaignId !== campaignId) continue;
      for (const v of (fam.versions || [])) {
        if (_inList(v)) out.push({ family: fam, version: v });
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

  function _detail(versionId) {
    if (_live()) {
      const pair = _findFamilyVersion(versionId);
      return pair ? _liveDetail(pair.family, pair.version) : {};
    }
    return (_fx().reviewDetail || {})[versionId] || {};
  }

  // ── live read: a stored version -> the shape the fixtures' reviewDetail has ─
  // The body is plain text, paragraphs split on a blank line, `#` lines headings.
  // A claim is a sentence the piece declared; the paragraph that contains it gets
  // the claim's action bar. A claim whose text is in no paragraph (the version
  // body differs from the piece's) gets a paragraph of its own at the end rather
  // than a blocker with nothing to act on.
  const _HEADING = /^#{1,6}\s+/;
  function _liveBody(family, version) { return version.body || family.body || ''; }
  function _liveRaws(family, version) {
    return _liveBody(family, version).split(/\n{2,}/).map((x) => x.trim()).filter(Boolean);
  }
  function _liveClaimStatus(c, version) {
    const st = (version.claimsState || {})[c.id] || {};
    if (st.status === 'removed') return 'removed';
    if (st.status === 'accepted') return 'accepted';
    if (st.status === 'edited') return 'edited';
    return c.source ? 'sourced' : 'blocked';
  }
  function _claimLabel(text) {
    const t = String(text || '').replace(/\s+/g, ' ').trim();
    return t.length > 40 ? t.slice(0, 37).trimEnd() + '…' : t;
  }
  function _liveDetail(family, version) {
    const claims = version.claims || [];
    const used = new Set();
    const paragraphs = _liveRaws(family, version).map((raw, i) => {
      const id = 'p' + i;
      if (_HEADING.test(raw)) return { id, raw, heading: raw.replace(_HEADING, '') };
      const c = claims.find((x) => !used.has(x.id) && x.text && raw.indexOf(x.text) >= 0);
      if (c) { used.add(c.id); return { id, raw, text: raw, claimId: c.id, before: c.text }; }
      return { id, raw, text: raw };
    });
    claims.forEach((c) => {
      if (!used.has(c.id) && _liveClaimStatus(c, version) !== 'removed') {
        paragraphs.push({ id: 'pc-' + c.id, raw: null, text: c.text, claimId: c.id, before: c.text });
      }
    });
    return {
      paragraphs,
      claims: claims.map((c) => ({
        id: c.id, label: _claimLabel(c.text), source: c.source || null,
        state: _liveClaimStatus(c, version), revision: ((version.claimsState || {})[c.id] || {}).revision || 0,
      })),
      whyChecks: [],
      whenISO: version.publishAt || null,
      where: null,
      link: null,
    };
  }

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
      shorter: 'Open to Windows beta testers.',
      less_technical: 'If you run Windows, you can join the beta.',
      rephrase: 'Windows beta testers can join now.',
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
    // Live only. A person's own decisions, worded as that: Clayrune did not read
    // the source, so none of these says it "supports" anything.
    sourced: { glyph: '✓', word: '✓ Source on file' },
    accepted: { glyph: '✓', word: '✓ You accepted it as written' },
    edited: { glyph: '✓', word: '✓ You rewrote it' },
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
      result: null,     // live: what Approve did, shown until the person continues
      approving: false,
    };
    // Seed claim runtime state from the fixture baseline, once per mount.
    _reseedClaims();
    _renderAll();
  }

  // Claim runtime state from the current detail. Live, called after every write
  // so the bars follow what the version now says; the form a person is typing
  // into (addingSource, its draft) survives it.
  function _reseedClaims() {
    if (!_st) return;
    const d = _detail(_st.versionId);
    const prev = _st.claims || {};
    _st.claims = {};
    for (const c of (d.claims || [])) {
      const was = prev[c.id] || {};
      _st.claims[c.id] = {
        status: c.state, sourceName: null, revision: c.revision,
        addingSource: !!was.addingSource, checking: false, sourceDraft: was.sourceDraft,
      };
    }
  }

  function _pair() { return _st && _st.versionId ? _findFamilyVersion(_st.versionId) : null; }

  // ── render ─────────────────────────────────────────────────────────────
  function _clearCrumbTools() {
    const host = document.getElementById('desk-v1-crumb-tools');
    if (host) host.innerHTML = '';
  }

  function _renderAll() {
    const el = _st.el;
    _closeSelToolbar();
    if (_st.result) { _renderResult(); return; }
    const list = _needsReviewList(_st.campaignId);
    if (!_st.versionId || !list.length) {
      el.innerHTML = `
        <div class="desk-v1-review-empty">
          <div class="desk-v1-review-empty-title">Nothing needs review</div>
          <div class="desk-v1-review-empty-body">Every piece in this campaign is scheduled, published, skipped or archived.</div>
          <button type="button" class="desk-v1-stub-link" onclick="deskV1Back()">‹ Back to campaign</button>
        </div>`;
      _clearCrumbTools();
      return;
    }
    const pair = _pair();
    if (!pair) {
      el.innerHTML = `<div class="desk-v1-stub"><div class="desk-v1-stub-body">This version no longer exists.</div></div>`;
      _clearCrumbTools();
      return;
    }
    const { family, version } = pair;
    const channel = _channel(version.channelId);
    const detail = _detail(version.id);
    const idx = list.findIndex((x) => x.version.id === version.id);
    const isVideo = family.kind === 'video';

    el.innerHTML = `
      <div class="desk-v1-review">
        <div class="desk-v1-review-savestatus" id="desk-v1-review-savestatus"></div>
        <div id="desk-v1-review-conflict"></div>
        <div class="desk-v1-review-layout">
          <div class="desk-v1-review-main" id="desk-v1-review-main">
            ${isVideo ? _videoBodyHTML(family, version) : _articleBodyHTML(family, version, detail)}
          </div>
          <div class="desk-v1-review-rail">
            ${_railHTML(family, version, channel, detail, isVideo)}
          </div>
        </div>
      </div>`;
    _renderCrumbTools(idx, list.length, isVideo);
    _wireAll(el, family, version, channel, detail, isVideo);
  }

  // Frame 12b's one row (back link · doc label/count · controls): rendered
  // straight into the shell's own #desk-v1-crumb-tools slot (desk-v1-
  // shell.js) instead of a second header row inside `el`, so it survives
  // every _renderAll() the same way the crumb itself does. Shell rebuilds
  // this slot fresh on every navigation (deskV1Render), so no teardown is
  // needed when leaving the review route.
  function _renderCrumbTools(idx, total, isVideo) {
    const host = document.getElementById('desk-v1-crumb-tools');
    if (!host) return;
    const kindGlyph = isVideo ? '🎥' : '📄';
    const kindWord = isVideo ? 'Video' : 'Article';
    host.innerHTML = `
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
      </div>`;
    host.querySelectorAll('[data-mode-btn]').forEach((b) => b.onclick = () => _setMode(b.dataset.modeBtn));
    const scEl = host.querySelector('[data-show-changes]');
    if (scEl) scEl.onclick = () => { _st.showChanges = !_st.showChanges; _renderAll(); };
    host.querySelectorAll('[data-step]').forEach((b) => b.onclick = () => _step(parseInt(b.dataset.step, 10)));
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
    const agentName = _agentName(_campaign(_st && _st.campaignId));
    if (st.status === 'blocked') {
      return `<div class="desk-v1-claimbar desk-v1-claimbar-blocked" data-claim-bar="${esc(claimId)}">
        <div class="desk-v1-claimbar-head">${copy.word}</div>
        ${st.addingSource ? _sourceFormHTML(claimId, st) : `
          <div class="desk-v1-claimbar-actions">
            <button type="button" data-claim-fixposy="${esc(claimId)}">Fix with ${esc(agentName)}</button>
            <button type="button" data-claim-remove="${esc(claimId)}">Remove</button>
            <button type="button" data-claim-addsource="${esc(claimId)}">Add source</button>
            ${_live() ? `<button type="button" data-claim-asis="${esc(claimId)}">Accept as written</button>` : ''}
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
        <div class="desk-v1-claimbar-reason">It doesn’t fully support the claim as written. ${esc(agentName)} suggests the revision shown above. The block clears only when you accept a revision.</div>
        <div class="desk-v1-claimbar-actions">
          <button type="button" class="desk-v1-claimbar-accept" data-claim-accept="${esc(claimId)}">Accept → r${st.revision + 1}</button>
          <button type="button" data-claim-editself="${esc(claimId)}">Edit it myself</button>
          <button type="button" data-claim-remove="${esc(claimId)}">Remove sentence</button>
        </div>
      </div>`;
    }
    // supports / resolved_by_posy (live: sourced / accepted / edited): a quiet
    // confirmation line, not a block bar.
    const src = _live() && st.status === 'sourced' && meta.source ? ` <span class="desk-v1-claimbar-source">${esc(meta.source)}</span>` : '';
    return `<div class="desk-v1-claimbar desk-v1-claimbar-ok" data-claim-bar="${esc(claimId)}">${copy.word}${src}</div>`;
  }

  function _sourceFormHTML(claimId, st) {
    if (st.checking) return `<div class="desk-v1-claimbar-checking">Checking…</div>`;
    const live = _live();
    return `<div class="desk-v1-claimbar-sourceform">
      <input type="text" placeholder="${live ? 'A document, link or note this comes from' : 'benchmark-sep-22.md'}" data-source-input="${esc(claimId)}" value="${esc(st.sourceDraft || '')}" />
      <button type="button" data-source-run="${esc(claimId)}">${live ? 'Save' : 'Check'}</button>
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

  // Demo: any time counts (the fixtures' dates are not tied to the clock). Live: a
  // time already passed is not a schedule (the server refuses it rather than
  // quietly posting now), so it neither labels the button "schedule" nor is sent.
  function _hasFutureSchedule(detail) {
    if (!detail || !detail.whenISO) return false;
    if (!_live()) return true;
    const t = Date.parse(detail.whenISO);
    return !isNaN(t) && t > Date.now();
  }

  // ── right rail ─────────────────────────────────────────────────────────
  function _railHTML(family, version, channel, detail, isVideo) {
    const hasSchedule = _hasFutureSchedule(detail);
    const rendered = !isVideo || (family.render && family.render.status === 'ready' && family.render.revision === version.revision);
    const blockedClaim = (detail.claims || []).map((c) => _st.claims[c.id]).find((s) => s && _claimBlocks(s.status));
    const info = _primaryInfo(channel, hasSchedule, blockedClaim, detail.claims, isVideo, rendered, version, detail);

    let notice = '';
    if (_live() && version.state === 'held') {
      notice = `<div class="desk-v1-review-notice desk-v1-review-notice-held" data-review-held>⚠ Held — ${esc((version.failure && version.failure.reason) || 'held')}. Nothing was posted. Fix that, then approve again.</div>`;
    } else if (_live() && version.state === 'failed') {
      notice = `<div class="desk-v1-review-notice desk-v1-review-notice-held" data-review-failed>✗ Failed — ${esc((version.failure && version.failure.reason) || 'the platform refused it')}. Nothing was posted. Approving again retries it.</div>`;
    } else if (_live() && version.state === 'unknown_outcome') {
      notice = `<div class="desk-v1-review-notice desk-v1-review-notice-held" data-review-unknown>? Unknown outcome — ${esc((version.failure && version.failure.reason) || 'the request may have gone out')}. It may already be live: check ${esc((channel && (channel.label || channel.identity)) || 'the account')} before doing anything else.</div>`;
    } else if (channel && channel.health === 'held') {
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
      compact: true, sendStyle: 'arrow',
      agentRef: window.DeskV1Kit.deskAgentRef({ project: _project((_campaign(_st.campaignId) || {}).projectId), campaign: _campaign(_st.campaignId) }),
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
          ${_live() && version.state === 'unknown_outcome' ? '<button type="button" data-act-posted>It is live: mark as posted</button>' : ''}
        </div>
      </div>`;
  }

  // Primary label: capability × schedule (§4). Doc lists two disable
  // conditions (a claim blocked, a revision waiting); a held destination is a
  // third one added here — approving can't bind a destination that can't
  // currently publish (§9 "Held overrides either"). Not drawn in frame 12b;
  // flagged in the final report as a deviation.
  function _primaryInfo(channel, hasSchedule, blockedClaim, claims, isVideo, rendered, version, detail) {
    const retry = _live() && version && (version.state === 'held' || version.state === 'failed');
    const label = !channel ? 'Approve'
      : channel.capability === 'manual' ? 'Approve and create publishing task'
      : retry ? (hasSchedule ? 'Approve again and schedule' : 'Approve again')
      : (hasSchedule ? 'Approve and schedule' : 'Approve');
    const reasons = [];
    if (_live()) {
      // The server refuses all of these too (M19); saying them here is so the
      // button is never a click that can only come back with a 409.
      const pub = channel && channel.publish;
      if (!channel) reasons.push('The account this version is for is not in the workspace');
      else if (pub && !pub.ready) reasons.push(`${channel.label || channel.identity} cannot publish: ${pub.reason || 'not connected'}`);
      if (version && version.state === 'unknown_outcome') reasons.push('This may already be live: check the account, then mark it posted');
      if (detail && detail.whenISO && !hasSchedule) reasons.push('The scheduled time has passed: pick a new one on When, or use ⋯ Publish now');
    }
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
    el.querySelectorAll('[data-claim-addsource]').forEach((b) => b.onclick = () => { _st.claims[b.dataset.claimAddsource].addingSource = true; _renderAll(); });
    el.querySelectorAll('[data-source-cancel]').forEach((b) => b.onclick = () => { _st.claims[b.dataset.sourceCancel].addingSource = false; _renderAll(); });
    el.querySelectorAll('[data-source-input]').forEach((inp) => inp.oninput = () => { _st.claims[inp.dataset.sourceInput].sourceDraft = inp.value; });
    el.querySelectorAll('[data-source-run]').forEach((b) => b.onclick = () => _runValidation(b.dataset.sourceRun, family, version, detail));
    el.querySelectorAll('[data-claim-accept]').forEach((b) => b.onclick = () => _acceptRevision(b.dataset.claimAccept, family, version, detail));
    el.querySelectorAll('[data-claim-editself]').forEach((b) => b.onclick = () => _editSelf(b.dataset.claimEditself, family, version, detail));
    el.querySelectorAll('[data-claim-remove]').forEach((b) => b.onclick = () => _removeSentence(b.dataset.claimRemove, family, version, detail));
    el.querySelectorAll('[data-claim-fixposy]').forEach((b) => b.onclick = () => _fixWithPosy(b.dataset.claimFixposy, family, version, detail));
    el.querySelectorAll('[data-claim-asis]').forEach((b) => b.onclick = () => _acceptAsWritten(b.dataset.claimAsis, family, version));

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
    const posted = el.querySelector('[data-act-posted]');
    if (posted) posted.onclick = () => _markPosted(family, version, null);
    const more = el.querySelector('[data-more-btn]');
    if (more) more.onclick = (e) => _openMoreMenu(e.currentTarget, family, version, channel, detail, isVideo);

    if (window.DeskV1Kit) {
      const live = _live();
      window.DeskV1Kit.bindPosyBox(el.querySelector('#desk-v1-review-posy'), 'desk-v1-review-posy-input', (text) => {
        // Live: the real agent (M20), and the box is not the R0 simulation. It
        // answers by saving a revision back to this version, which the watcher
        // below picks up. Demo: the simulated task, unchanged.
        if (live) { _reviseRequest(family, version, { note: text }); return; }
        window.DeskV1Kit.toast('Sent to ' + _agentName(_campaign(family.campaignId)) + ': "' + text + '"', {});
        window.DeskV1Kit.paintPosyReadyNoDiff(el.querySelector('#desk-v1-review-posy'));
      }, { draftKey: `project:${(_campaign(family.campaignId) || {}).projectId}:review:${version.id}`, taskLifecycle: !live });
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
      _autosaveTimer = null;
      if (_live()) { _saveBody(); return; }
      const s = document.getElementById('desk-v1-review-savestatus');
      if (s) s.textContent = 'Saved';
    }, 600);
  }

  // Live: the edited paragraphs back into one body, saved with M18. Headings and
  // claim paragraphs are not editable here, so they go back exactly as they came.
  function _composeBody(family, version) {
    const d = _liveDetail(family, version);
    const parts = [];
    (d.paragraphs || []).forEach((p) => {
      if (p.raw == null) return;
      const edited = !p.heading && !p.claimId ? _st.editValues[p.id] : null;
      const text = edited != null ? edited.trim() : p.raw;
      if (text) parts.push(text);
    });
    return parts.join('\n\n');
  }

  async function _saveBody() {
    const pair = _pair();
    if (!pair || !_live()) return;
    const { family, version } = pair;
    const body = _composeBody(family, version);
    if (body === _liveBody(family, version).trim()) {
      const s = document.getElementById('desk-v1-review-savestatus');
      if (s) s.textContent = 'Saved';
      return;
    }
    const st = _st;
    try {
      await _S().api('PATCH', _versionUrl(family, version), { body });
      version.body = body;
      if (_st === st) { const s = document.getElementById('desk-v1-review-savestatus'); if (s) s.textContent = 'Saved'; }
    } catch (e) {
      if (_st === st) { const s = document.getElementById('desk-v1-review-savestatus'); if (s) s.textContent = `Not saved: ${e && e.message ? e.message : e}`; }
    }
  }

  // A claim write reads the saved body, so a pending edit is saved first.
  async function _flushBody() {
    if (_autosaveTimer) { clearTimeout(_autosaveTimer); _autosaveTimer = null; await _saveBody(); }
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
    if (_live()) { _saveSource(claimId, family, draft); return; }
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
    if (_live()) return;   // no proposed revision exists live: the agent saves a whole one (M20)
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
    if (_live()) {
      const typed = window.prompt ? window.prompt('Rewrite the sentence:', (p && p.before) || '') : null;
      if (typed == null || !typed.trim()) return;
      _liveClaimWrite(claimId, family, version, 'edited', { body: (b) => _replaceIn(b, p && p.before, typed.trim()), text: typed.trim(),
        label: 'Rewrote the claim sentence yourself' });
      return;
    }
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
    if (_live()) {
      const p = (detail.paragraphs || []).find((x) => x.claimId === claimId);
      _liveClaimWrite(claimId, family, version, 'removed', { body: (b) => _removeFrom(b, p && p.before), label: 'Removed the unsupported sentence' });
      return;
    }
    const prevStatus = st.status;
    const rN = st.revision + 1;
    window.DeskV1Kit.commandBus.run({
      label: 'Removed the unsupported sentence → r' + rN,
      do: () => { st.status = 'removed'; st.revision = rN; version.revision = rN; _renderAll(); },
      undo: () => { st.status = prevStatus; st.revision = rN - 1; version.revision = rN - 1; _renderAll(); },
    });
  }

  function _fixWithPosy(claimId, family, version, detail) {
    if (_live()) { _reviseRequest(family, version, { claim_id: claimId }); return; }
    const p = (detail.paragraphs || []).find((x) => x.claimId === claimId);
    const st = _st.claims[claimId];
    const rN = st.revision + 1;
    const hedge = p.text.replace(p.before, 'keeps recent snapshots automatically — exact retention varies by project size');
    window.DeskV1Kit.commandBus.run({
      label: _agentName(_campaign(family.campaignId)) + ' rephrased the claim to remove the unsupported number → r' + rN,
      do: () => { st.status = 'resolved_by_posy'; st.revisedText = hedge; st.revision = rN; version.revision = rN; _renderAll(); },
      undo: () => { st.status = 'blocked'; st.revisedText = null; st.revision = rN - 1; version.revision = rN - 1; _renderAll(); },
    });
  }

  // ── live: writes ──────────────────────────────────────────────────────
  function _replaceIn(body, from, to) {
    if (!from || body.indexOf(from) < 0) return body;
    return body.replace(from, to);
  }
  function _removeFrom(body, text) {
    if (!text || body.indexOf(text) < 0) return body;
    return body.replace(text, '').replace(/[ \t]{2,}/g, ' ').replace(/ +\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  }
  function _afterLocal() { _reseedClaims(); if (_st && !_st.result) _renderAll(); }

  // One PATCH to a version (M18), applied locally first so the bars follow at
  // once, rolled back with the server's reason on a refusal, with an Undo that
  // PATCHes the previous values back. `next`/`prev` carry only what changes.
  function _versionWrite(family, version, label, next, prev) {
    const put = (v) => {
      if ('body' in v) version.body = v.body;
      if ('claims_state' in v) version.claimsState = v.claims_state;
      if ('revision' in v) version.revision = v.revision;
      // A claim's text is its revised text while one exists (the server's own rule).
      (version.claims || []).forEach((c) => {
        const e = (version.claimsState || {})[c.id];
        c.text = (e && e.revised_text) || c.original || c.text;
      });
    };
    return _S().write({
      label, apply: () => { put(next); _afterLocal(); }, unapply: () => put(prev),
      request: () => _S().api('PATCH', _versionUrl(family, version), next),
      undoRequest: () => _S().api('PATCH', _versionUrl(family, version), prev),
      repaint: _afterLocal,
    });
  }

  // A decision on one claim: `kind` is accepted | edited | removed. `change.body`
  // (optional) maps the saved body to the new one. Bumps the version's revision.
  async function _liveClaimWrite(claimId, family, version, kind, change) {
    await _flushBody();
    const cs = JSON.parse(JSON.stringify(version.claimsState || {}));
    const rev = (version.revision || 0) + 1;
    const claimRev = ((cs[claimId] || {}).revision || 0) + 1;
    const body = _liveBody(family, version);
    const nextBody = change.body ? change.body(body) : body;
    const nextCs = Object.assign({}, cs, { [claimId]: { status: kind, revised_text: kind === 'edited' ? change.text : null, revision: claimRev } });
    const next = { claims_state: nextCs, revision: rev };
    const prev = { claims_state: cs, revision: version.revision || 0 };
    if (nextBody !== body) { next.body = nextBody; prev.body = version.body || ''; }
    return _versionWrite(family, version, `${change.label} → r${rev}`, next, prev);
  }

  function _acceptAsWritten(claimId, family, version) {
    return _liveClaimWrite(claimId, family, version, 'accepted', { label: 'Accepted the claim as written' });
  }

  // Adding a source is a change to the PIECE's claim (claims are piece-level, so
  // every version of it reads the same source). Saved as typed: nobody here reads
  // the source, which is why the bar says "on file", not "supports".
  function _saveSource(claimId, family, text) {
    const claimsOf = () => (family.versions || []).flatMap((v) => v.claims || []).filter((c) => c.id === claimId);
    const first = claimsOf()[0];
    if (!first) return;
    const prevSource = first.source || null;
    const toList = (src) => {
      const seen = new Set();
      const out = [];
      (family.versions || []).forEach((v) => (v.claims || []).forEach((c) => {
        if (seen.has(c.id)) return;
        seen.add(c.id);
        out.push({ id: c.id, text: c.original || c.text, source: c.id === claimId ? src : (c.source || null) });
      }));
      return out;
    };
    const url = '/api/desk/pieces/' + encodeURIComponent(family.id);
    const setLocal = (src) => claimsOf().forEach((c) => { c.source = src; });
    const st = _st.claims[claimId];
    return _S().write({
      label: 'Added a source to the claim',
      apply: () => { setLocal(text); st.addingSource = false; st.sourceDraft = ''; _afterLocal(); },
      unapply: () => setLocal(prevSource),
      request: () => _S().api('PATCH', url, { claims: toList(text) }),
      undoRequest: () => _S().api('PATCH', url, { claims: toList(prevSource) }),
      repaint: _afterLocal,
    });
  }

  // M20: ask the campaign's agent. It answers by saving a revision to this
  // version through the plain PATCH, back in needs_review: nothing is approved or
  // sent by it. The watcher repaints when that revision lands.
  async function _reviseRequest(family, version, opts) {
    const agent = _agentName(_campaign(family.campaignId));
    const toast = (m) => window.DeskV1Kit && window.DeskV1Kit.toast(m, {});
    await _flushBody();
    try {
      await _S().api('POST', _versionUrl(family, version, '/revise'), opts);
    } catch (e) {
      toast(`${agent} was not asked: ${e && e.message ? e.message : e}`);
      return false;
    }
    toast(`Sent to ${agent}. The revision lands here for you to review; nothing is approved or sent by it.`);
    _watchRevision(family, version);
    return true;
  }

  // Polls the campaign's pieces until this version's text or revision changes (the
  // agent saved), then repaints. Stops when the person leaves the surface or after
  // ~6 minutes. `__deskV1ReviewPollMs` is a test hook, not a setting.
  function _watchRevision(family, version) {
    const st = _st;
    const vid = version.id;
    const body0 = version.body || '';
    const rev0 = version.revision || 0;
    const every = window.__deskV1ReviewPollMs || 4000;
    let n = 0;
    const tick = async () => {
      if (_st !== st || n++ > 90) return;
      try {
        const list = await _S().api('GET', '/api/desk/pieces?campaign_id=' + encodeURIComponent(family.campaignId));
        if (_st !== st) return;
        const arr = _fx().families;
        (list || []).forEach((p) => { const i = arr.findIndex((f) => f.id === p.id); if (i >= 0) arr[i] = p; else arr.push(p); });
        const pair = _findFamilyVersion(vid);
        if (pair && ((pair.version.body || '') !== body0 || (pair.version.revision || 0) !== rev0)) {
          if (window.DeskV1Kit) window.DeskV1Kit.toast(`${_agentName(_campaign(family.campaignId))} revised this version.`, {});
          _reseedClaims();
          // Someone else's change over text being typed: the existing "keep mine / use theirs" bar.
          if (_st.mode === 'edit' && Object.keys(_st.editValues).length) window.__deskV1SimulateEditConflict();
          else _renderAll();
          return;
        }
      } catch (e) { /* the next tick tries again */ }
      setTimeout(tick, every);
    };
    setTimeout(tick, every);
  }

  // ── live: approving (M19) ───────────────────────────────────────────────
  // Human-only, the dashboard passcode retyped for each call, and it can publish.
  function _adoptPiece(piece) {
    const arr = _fx().families;
    const i = arr.findIndex((f) => f.id === piece.id);
    if (i >= 0) arr[i] = piece; else arr.push(piece);
  }

  async function _humanPost(url, body, proof) {
    if (typeof window.humanProofFetch !== 'function') throw new Error('the passcode prompt is not available');
    const res = await window.humanProofFetch(url, { method: 'POST', body: JSON.stringify(body || {}) }, proof);
    if (res === null) throw new Error('the dashboard passcode was not entered, so nothing was changed');
    if (!res.ok) throw new Error((res.body && (res.body.error || res.body.message)) || `HTTP ${res.status}`);
    return res.body;
  }

  async function _approveLive(family, version, channel, detail, opts) {
    if (_st.approving) return;
    const now = !!(opts && opts.now);
    const when = !now && _hasFutureSchedule(detail) ? _fmtWhen(detail.whenISO) : null;
    const name = (channel && (channel.label || channel.identity)) || 'the account';
    const proof = {
      title: when ? 'Approve and schedule' : (channel && channel.capability === 'manual' ? 'Approve and create the publishing task' : 'Approve and publish'),
      description: channel && channel.capability === 'manual'
        ? `Re-enter your dashboard passcode to approve this version for ${name}. Nothing is posted: you get the text and a link to post it yourself.`
        : (when
          ? `Re-enter your dashboard passcode to approve this version for ${name} at ${when}. It posts from the scheduler without asking again.`
          : `Re-enter your dashboard passcode to approve and post this version to ${name} now. A post cannot be unsent from here.`),
    };
    _st.approving = true;
    const st = _st;
    try {
      const piece = await _humanPost(_versionUrl(family, version, '/approve'), now ? { scheduled_at: null } : {}, proof);
      _adoptPiece(piece);
      if (_st === st) { st.result = { versionId: version.id }; }
    } catch (e) {
      if (window.DeskV1Kit) window.DeskV1Kit.toast(`Not approved: ${e && e.message ? e.message : e}`, {});
    } finally {
      st.approving = false;
    }
    if (_st === st) _renderAll();
  }

  async function _markPosted(family, version, url) {
    if (_st.approving) return;
    const proof = { title: 'Mark as posted', description: 'Re-enter your dashboard passcode to record that this was posted. It is written to the story ledger as published by you.' };
    _st.approving = true;
    const st = _st;
    try {
      const piece = await _humanPost(_versionUrl(family, version, '/posted'), url ? { url } : {}, proof);
      _adoptPiece(piece);
      st.result = { versionId: version.id };
    } catch (e) {
      if (window.DeskV1Kit) window.DeskV1Kit.toast(`Not recorded: ${e && e.message ? e.message : e}`, {});
    } finally {
      st.approving = false;
    }
    if (_st === st) _renderAll();
  }

  // What Approve did, said plainly and shown until the person continues. Built from
  // the version AS STORED after the call, never from what was expected.
  function _renderResult() {
    const el = _st.el;
    const pair = _findFamilyVersion(_st.result.versionId);
    _clearCrumbTools();
    if (!pair) { _st.result = null; _advanceOrEmpty(); return; }
    const { family, version } = pair;
    const channel = _channel(version.channelId);
    const where = esc((channel && (channel.label || channel.identity)) || 'the account');
    const link = version.receipt && version.receipt.permalink
      ? ` <a href="${esc(version.receipt.permalink)}" target="_blank" rel="noopener noreferrer">${esc(version.receipt.permalink)}</a>` : '';
    const reason = esc((version.failure && version.failure.reason) || '');
    let head; let body = ''; let tone = '';
    switch (version.state) {
      case 'verified_published': head = `✓ Published to ${where}.`; body = link; break;
      case 'submitted': head = `✓ Sent to ${where}.`; body = `The platform has it; Clayrune confirms it is live within a few minutes.${link}`; break;
      case 'scheduled': {
        head = `✓ Approved. It goes out ${esc(_fmtWhen(version.publishAt) || 'at the time you set')}.`;
        const pub = channel && channel.publish;
        body = pub && pub.unattended_ok === false
          ? `<div data-review-unattended>Heads up: the vault entry <code>${esc(pub.secret)}</code> does not allow unattended use, so this post will be HELD at that time. Allow it in Secrets, or approve this one now instead.</div>`
          : 'It posts from the scheduler at that time, and is held with the reason if any check fails then.';
        break;
      }
      case 'approved':
        if (version.manual) { head = `✋ Your turn: post it on ${where}.`; body = _manualTaskHTML(version); }
        else { head = '✓ Approved.'; body = 'The publisher picks it up within a minute.'; }
        break;
      case 'held': head = '⚠ Held.'; body = `${reason}. Nothing was posted. Fix that, then approve again.`; tone = ' desk-v1-review-notice-held'; break;
      case 'failed': head = '✗ Failed.'; body = `${reason}. Nothing was posted. Approving again retries it.`; tone = ' desk-v1-review-notice-held'; break;
      case 'unknown_outcome':
        head = '? Unknown outcome.';
        body = `${reason}. The post may already be live: check ${where} before doing anything else.<div><button type="button" class="desk-v1-review-primary" data-result-posted>It is live: mark as posted</button></div>`;
        tone = ' desk-v1-review-notice-held';
        break;
      case 'you_reported': head = '✓ Recorded as posted by you.'; body = link; break;
      default: head = `Now: ${esc(version.state)}.`;
    }
    el.innerHTML = `
      <div class="desk-v1-review-empty" data-review-result data-state="${esc(version.state)}">
        <div class="desk-v1-review-empty-title">${head}</div>
        <div class="desk-v1-review-empty-body${tone}">${body}</div>
        <button type="button" class="desk-v1-stub-link" data-result-continue>Continue ›</button>
      </div>`;
    const cont = el.querySelector('[data-result-continue]');
    if (cont) cont.onclick = () => { _st.result = null; _advanceOrEmpty(); };
    const posted = el.querySelector('[data-result-posted]');
    if (posted) posted.onclick = () => _markPosted(family, version, null);
    const copy = el.querySelector('[data-task-copy]');
    if (copy) copy.onclick = async () => {
      try { await navigator.clipboard.writeText(version.manual.copy_text || ''); copy.textContent = 'Copied'; } catch (e) { copy.textContent = 'Select the text above and copy it'; }
    };
    const done = el.querySelector('[data-task-posted]');
    if (done) done.onclick = () => {
      const u = (el.querySelector('[data-task-url]') || {}).value || '';
      _markPosted(family, version, u.trim() || null);
    };
  }

  function _manualTaskHTML(version) {
    const m = version.manual || {};
    const share = m.share_url
      ? `<a href="${esc(m.share_url)}" target="_blank" rel="noopener noreferrer" data-task-share>Open X with the text ready ›</a>` : '';
    const note = m.share_url ? '' : `<div>${esc(m.platform || 'The platform')} has no link that carries text: open it yourself and paste.</div>`;
    return `<div data-review-task>
      <div><strong>${esc(m.title || 'Publishing task')}</strong></div>
      <textarea readonly rows="5" data-task-text>${esc(m.copy_text || '')}</textarea>
      <div><button type="button" data-task-copy>Copy text</button> ${share}</div>${note}
      <div>When it is up: <input type="text" data-task-url placeholder="its link (optional)" />
        <button type="button" data-task-posted>I posted it</button></div>
    </div>`;
  }

  // ── primary / secondary actions ────────────────────────────────────────
  function _approve(family, version, channel, detail) {
    if (_live()) { _approveLive(family, version, channel, detail); return; }
    const hasSchedule = !!detail.whenISO;
    const nextState = hasSchedule ? 'scheduled' : 'approved';
    _dispose({ /* no family mutation needed */ }, version, nextState,
      channel && channel.capability === 'manual' ? 'Publishing task created' : (hasSchedule ? 'Scheduled' : 'Approved'));
  }

  // Live Skip / Archive: M18 `state`, the two writable states a person may set.
  // Undo goes back to review, the only state this surface can restore it to.
  function _disposeLive(family, version, nextState, label) {
    const prev = version.state;
    const back = prev === 'needs_review' ? prev : 'needs_review';
    return _S().write({
      label,
      apply: () => { version.state = nextState; _advanceOrEmpty(); },
      unapply: () => { version.state = back; _advanceOrEmpty(); },
      request: () => _S().api('PATCH', _versionUrl(family, version), { state: nextState }),
      undoRequest: () => _S().api('PATCH', _versionUrl(family, version), { state: back }),
      repaint: _advanceOrEmpty,
    });
  }

  function _dispose(family, version, nextState, label) {
    if (_live()) return _disposeLive(family, version, nextState, label);
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
    const liveReason = _live() ? (!channel ? 'The account is not in the workspace'
      : (channel.publish && !channel.publish.ready ? `${channel.label || channel.identity} cannot publish: ${channel.publish.reason || 'not connected'}`
        : (version.state === 'unknown_outcome' ? 'This may already be live: check the account first' : ''))) : '';
    const disabled = manual || held || !!blockedClaim || !rendered || !!liveReason;
    const reason = manual ? 'Manual destinations can’t publish now — approving creates the task instead'
      : liveReason ? liveReason
      : held ? 'Reconnect the destination first' : (blockedClaim ? 'Resolve the blocked claim first' : (!rendered ? 'Render this version first' : ''));
    const menu = document.createElement('div');
    menu.className = 'desk-v1-review-moremenu';
    menu.setAttribute('role', 'menu');
    menu.innerHTML = `<button type="button" data-publish-now ${disabled ? 'disabled title="' + esc(reason) + '"' : ''}>Publish now…</button>`;
    host.style.position = 'relative';
    host.appendChild(menu);
    DeskV1Kit.placePopover(menu, triggerEl, { prefer: 'above' });
    if (!disabled) {
      menu.querySelector('[data-publish-now]').onclick = () => {
        menu.remove();
        if (_live()) _approveLive(family, version, channel, detail, { now: true });
        else _dispose(family, version, 'verified_published', 'Published now');
      };
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
    if (!para || para.dataset.paraId === 'p-claim' || para.dataset.paraId === 'p-embed' || para.classList.contains('desk-v1-review-p-claim')) { _closeSelToolbar(); return; }

    _closeSelToolbar();
    const rect = range.getBoundingClientRect();
    const hostRect = articleEl.getBoundingClientRect();
    const agentName = _agentName(_campaign(_st.campaignId));
    const bar = document.createElement('div');
    bar.className = 'desk-v1-review-seltoolbar';
    bar.innerHTML = `
      <div class="desk-v1-review-seltoolbar-askposy">
        <button type="button" data-sel-askposy>Ask ${esc(agentName)} ▾</button>
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
    if (_live()) {
      const pair = _pair();
      if (pair) _reviseRequest(pair.family, pair.version, { style, selection: selectedText || p.text });
      return;
    }
    const before = _st.editValues[paraId] != null ? _st.editValues[paraId] : p.text;
    const after = _rewriteFor(paraId, style, before);
    window.DeskV1Kit.commandBus.run({
      label: 'Applied ' + _agentName(_campaign(_st.campaignId)) + '’s "' + style.replace('_', ' ') + '" rewrite',
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
      if (_live()) {
        // Live there is no thread to post into: a comment IS a revision request on that passage.
        const pair = _pair();
        const para = (_detail(_st.versionId).paragraphs || []).find((x) => x.id === paraId);
        if (pair) _reviseRequest(pair.family, pair.version, { selection: para && para.text, note: text });
        box.innerHTML = `<div class="desk-v1-review-comment-posted"><strong>You:</strong> ${esc(text)}</div>`;
        return;
      }
      box.innerHTML = `<div class="desk-v1-review-comment-posted"><strong>You:</strong> ${esc(text)}</div>
        <div class="desk-v1-review-comment-reply"><strong>${esc(_agentName(_campaign(_st.campaignId)))}:</strong> Noted — I’ll flag this on the next pass.</div>`;
    };
  }

  window.deskV1RenderReview = deskV1RenderReview;
})();

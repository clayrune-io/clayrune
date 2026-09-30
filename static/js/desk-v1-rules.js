// Desk v1 (MC-977) — T2b: Proposed state, Start sheet, Posy
// instructions (frame: none drawn, gap map C6; docs/desk_v1_r0_plan.md;
// THE_DESK_V1_UI.md §3.5, §8, §9, §11). Window-bridged module, no `import`
// (ground rule 1). R2-3b retired this file's rules popover and the rule
// chips it edited (docs/THE_DESK_V1_IA_REVISION_2.md §8); the filename stays
// because index.html and the `window.deskV1*` hooks below don't move.
//
// Reads the campaign page through the T0a slot contract and two small,
// backward-compatible seams desk-v1-campaign.js (T2a) already carries for
// this exact purpose (its own comments name T2b): a Proposed-state summary/
// content override in deskV1FillCampaignSummary/TabBody, and a Posy-
// instruction override in the right column's onSend. This file never edits
// desk-v1-campaign.js.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function _fx() { return window.DeskV1Fixtures || {}; }
  function _campaigns() { return _fx().campaigns || []; }
  function _campaign(id) { return _campaigns().find((c) => c.id === id) || null; }
  function _project(id) { return (_fx().projects || []).find((p) => p.id === id) || null; }
  function _channels() { return _fx().channels || []; }
  function _channel(id) { return _channels().find((c) => c.id === id); }
  function _families() { return _fx().families || []; }
  function _familiesFor(campaignId) { return _families().filter((f) => f.campaignId === campaignId); }
  // T1 (§4): the blocker card + "? Assumed" notes aren't plan bounds, so
  // they live in their own fixture map, not `camp.plan` — the goal sentence
  // and Start-sheet authority fields that used to share that map now read
  // straight off `camp.plan` below.
  function _proposedExtras(campaignId) { return (_fx().proposedExtras || {})[campaignId] || null; }
  // R2-5: same deskAgentName(opts, fallback) resolution every other
  // desk-v1-*.js uses, keyed off the campaign's own project.
  function _agentName(camp) {
    return window.DeskV1Kit ? DeskV1Kit.deskAgentName({ project: _project(camp && camp.projectId), campaign: camp }) : 'your agent';
  }

  // Dave's review (2026-09-28): `new Date('2026-10-20')` parses a bare
  // YYYY-MM-DD as UTC midnight; formatting that in a host timezone behind
  // UTC (e.g. America/Los_Angeles) rolls it back to the previous local day
  // ("Oct 19"). Every plan date in this file is a calendar day, not an
  // instant, so parse it as a LOCAL date instead (same fix needed in
  // desk-v1-campaign.js's own copy of this helper).
  function _localDateFromISO(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
    return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(iso);
  }
  function _fmtDate(iso) {
    try { return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(_localDateFromISO(iso)); }
    catch (e) { return iso; }
  }
  function _fmtDateLong(iso) {
    try { return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(_localDateFromISO(iso)); }
    catch (e) { return iso; }
  }
  function _isoOf(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }
  function _isPastLocal(iso) { return iso < _isoOf(new Date()); }
  // Accepts the dashed date field's free-typed text. The field itself
  // DISPLAYS a yearless shape ("Oct 20"), so a user types back in that same
  // shape — `new Date('Nov 15')` defaults to year 2001 in V8, which would
  // silently write a garbage date. `refIso` is the plan's own deadline
  // BEFORE this edit: a yearless input first assumes that year, then rolls
  // to the next occurrence if that lands in the past. A literal YYYY-MM-DD
  // (or any string carrying an explicit year) is parsed as its own local
  // calendar day. Anything that still lands in the past, or fails to parse
  // at all, returns null — the caller leaves the plan untouched.
  function _parseDateInput(val, refIso) {
    val = (val || '').trim();
    if (!val) return null;
    if (/^\d{4}-\d{2}-\d{2}$/.test(val)) return _isPastLocal(val) ? null : val;
    if (/\d{4}/.test(val)) {
      const d = new Date(val);
      if (isNaN(d.getTime())) return null;
      const iso = _isoOf(d);
      return _isPastLocal(iso) ? null : iso;
    }
    const refYear = /^\d{4}-/.test(refIso || '') ? parseInt(refIso.slice(0, 4), 10) : new Date().getFullYear();
    const tryYear = (y) => { const d = new Date(`${val} ${y}`); return isNaN(d.getTime()) ? null : _isoOf(d); };
    let iso = tryYear(refYear);
    if (iso === null) return null;
    if (_isPastLocal(iso)) iso = tryYear(refYear + 1);
    return iso === null || _isPastLocal(iso) ? null : iso;
  }
  // §4: "spend + stop conditions derived" — never its own fixture field.
  // Built from the same two bounds the "Ends …" rule chip already reads.
  function _stopConditionsLabel(plan) {
    const end = plan && plan.end;
    if (!end) return null;
    const bound = end.date ? `${_fmtDateLong(end.date)} passes` : (end.post_cap ? `${end.post_cap} posts go out` : null);
    return bound ? `Pause automatically once the goal is reached or ${bound}.` : null;
  }

  // ────────────────────────────────────────────────────────────────────────
  // §3.5 Proposed state — summary slot override (state pill + editable goal
  // sentence + `Start campaign`, replacing T2a's ordinary Goal/Channels
  // groups per the doc: "Same page, with state ◇ Proposed... The goal
  // is a single editable sentence"). Channels still render via the shared
  // kit badge (UX-02 — never a bare logo, even here).
  // ────────────────────────────────────────────────────────────────────────
  function deskV1FillProposedSummary(el, params, camp) {
    // T1 (§4): the editable goal sentence reads/writes `camp.plan.goal` —
    // `plan` IS the fixture, so a commit below mutates it directly rather
    // than a copy that would need writing back.
    const plan = camp.plan = camp.plan || {};
    const goal = plan.goal = plan.goal || {};
    const stateHTML = DeskV1Kit.stateLabelHTML(camp.state, { className: 'desk-v1-camp-state-pill' });
    const chans = (camp.plan.accounts || []).map(_channel).filter(Boolean);

    el.innerHTML = `
      <div class="desk-v1-camp-summary-top">
        ${stateHTML}
        <div class="desk-v1-camp-summary-top-actions">
          <button type="button" class="desk-v1-rules-start-btn" data-start-campaign>Start campaign</button>
          <div class="desk-v1-camp-card-more">
            <button type="button" class="desk-v1-camp-card-morebtn" data-camp-more-btn aria-haspopup="menu" aria-label="More actions">⋯</button>
          </div>
        </div>
      </div>
      <div class="desk-v1-camp-summary-groups">
        <div class="desk-v1-camp-summary-group">
          <span class="desk-v1-camp-summary-label">GOAL</span>
          <div class="desk-v1-rules-goal-sentence">
            <span class="desk-v1-rules-dashed" data-goal-field="target" contenteditable="true" role="textbox" tabindex="0" aria-label="Target number">${esc(goal.target != null ? goal.target : '')}</span>
            ${esc(goal.outcome || '')} by
            <span class="desk-v1-rules-dashed" data-goal-field="dateLabel" contenteditable="true" role="textbox" tabindex="0" aria-label="Deadline">${esc(_fmtDate(goal.deadline))}</span>
            ${plan.audience ? `in <span class="desk-v1-rules-dashed" data-goal-field="audience" contenteditable="true" role="textbox" tabindex="0" aria-label="Audience">${esc(plan.audience)}</span>` : ''}
          </div>
          ${!goal.tracked ? '<span class="desk-v1-rules-goal-warning">⚠ not tracked yet</span>' : ''}
        </div>
        <div class="desk-v1-camp-summary-group" data-summary-group="channels">
          <span class="desk-v1-camp-summary-label">CHANNELS</span>
          <div class="desk-v1-camp-summary-badges">${chans.length ? chans.map((ch) => DeskV1Kit.channelBadge(ch)).join('') : '<span class="desk-v1-home-camp-nochannels">No channels yet</span>'}</div>
        </div>
      </div>`;

    // §4: "every surface re-renders from it" — a commit re-invokes this same
    // function so the goal sentence AND the rule chips beside it (the
    // derived "Ends …" chip above) always reflect the one just-edited plan,
    // instead of patching the blurred span's own text in place.
    el.querySelectorAll('[data-goal-field]').forEach((span) => {
      span.addEventListener('blur', () => {
        const field = span.dataset.goalField;
        const val = span.textContent.trim();
        if (field === 'target') {
          const n = parseInt(val, 10);
          if (!isNaN(n)) goal.target = n;
        } else if (field === 'dateLabel') {
          const parsed = _parseDateInput(val, goal.deadline);
          if (parsed) {
            goal.deadline = parsed;
            if (plan.end) plan.end.date = parsed;
          }
        } else if (field === 'audience') {
          plan.audience = val;
        }
        deskV1FillProposedSummary(el, params, camp);
      });
    });

    const startBtn = el.querySelector('[data-start-campaign]');
    if (startBtn) startBtn.onclick = () => deskV1OpenStartSheet(camp.id);

    // item 3 (MC-977 R0 UX pass, Dave's review): a proposed campaign is
    // exactly the "never published" case Delete applies to — shares the
    // same menu desk-v1-campaign.js's non-proposed summary uses, so the
    // draft/published boundary lives in one place, not two.
    const moreBtn = el.querySelector('[data-camp-more-btn]');
    if (moreBtn) moreBtn.onclick = (e) => {
      e.stopPropagation();
      window.deskV1OpenCampaignMoreMenu(moreBtn, camp.id, {
        onDone: (result) => {
          if (result === 'deleted') deskV1Nav('home', {});
          else window.deskV1FillCampaignSummary(el, params);
        },
      });
    };
  }

  // ────────────────────────────────────────────────────────────────────────
  // §3.5 Proposed state — content slot override: at most one blocker card
  // ("⛔ Posy's one question", CMP-03) above Posy's proposed pieces, each
  // carrying a "? Assumed" popover where the fixture has one.
  // ────────────────────────────────────────────────────────────────────────
  function deskV1FillProposedContent(el, params, camp) {
    const detail = _proposedExtras(camp.id) || {};
    const fams = _familiesFor(camp.id);
    const agentName = _agentName(camp);
    // R2-11: one question, two places it can be answered (here and on
    // Launch); the answer is a record on the campaign, so neither shows it
    // again once it is answered in the other.
    const blocker = detail.blocker && !(camp.answers || []).some((a) => a.id === detail.blocker.id) ? detail.blocker : null;
    el.innerHTML = `
      <div class="desk-v1-rules-proposed">
        ${blocker ? _blockerCardHTML(blocker, agentName) : ''}
        <div class="desk-v1-camp-cards">${fams.map((f) => _proposedCardHTML(f, detail)).join('') || `<div class="desk-v1-camp-empty">${esc(agentName)} hasn’t proposed any pieces yet.</div>`}</div>
      </div>`;
    if (blocker) _wireBlocker(el, camp, blocker);
  }

  function _blockerCardHTML(blocker, agentName) {
    return `<div class="desk-v1-rules-blocker" data-blocker-id="${esc(blocker.id)}">
      <div class="desk-v1-rules-blocker-head">⛔ ${esc(agentName)}’s one question</div>
      <div class="desk-v1-rules-blocker-q">${esc(blocker.question)}</div>
      <div class="desk-v1-rules-blocker-answers">
        ${blocker.answers.map((a) => `<button type="button" class="desk-v1-rules-blocker-answer" data-answer-id="${esc(a.id)}">${esc(a.label)}</button>`).join('')}
      </div>
    </div>`;
  }

  function _wireBlocker(el, camp, blocker) {
    const card = el.querySelector(`[data-blocker-id="${blocker.id}"]`);
    if (!card) return;
    card.querySelectorAll('[data-answer-id]').forEach((btn) => {
      btn.onclick = () => {
        const ans = blocker.answers.find((a) => a.id === btn.dataset.answerId);
        // R2-11: the answer is recorded on the campaign (`camp.answers`), the
        // same record the Launch page's `Needs your answer` card writes.
        window.deskV1CampaignAnswerQuestion(camp, blocker, ans, () => deskV1FillProposedContent(el, { campaignId: camp.id }, camp));
      };
    });
  }

  function _proposedCardHTML(fam, detail) {
    const assumption = (detail.assumptions || {})[fam.id];
    const v = fam.versions && fam.versions[0];
    return `<div class="desk-v1-camp-card desk-v1-rules-proposed-card" data-family-id="${esc(fam.id)}">
      <div class="desk-v1-camp-card-body">
        <div class="desk-v1-camp-card-title">${esc(fam.title)}</div>
        ${v ? `<div class="desk-v1-camp-card-versions">${DeskV1Kit.stateLabelHTML(v.state)}</div>` : ''}
        ${assumption
          ? `<details class="desk-v1-rules-assumed"><summary>? Assumed</summary><div class="desk-v1-rules-assumed-body">${esc(assumption)}</div></details>`
          : ''}
      </div>
    </div>`;
  }

  // ────────────────────────────────────────────────────────────────────────
  // Start sheet (CMP-05): the ongoing authority in plain language, "Starting
  // doesn't approve any piece", Confirm → Active + a policy record. A small
  // self-contained overlay (scrim + panel) appended to the shell itself,
  // not a route — it's a one-time confirmation, not a place you navigate
  // back from, so it doesn't belong in the shell's ROUTES table.
  // ────────────────────────────────────────────────────────────────────────
  function _closeOverlay() {
    const existing = document.querySelector('.desk-v1-rules-overlay');
    if (existing) existing.remove();
  }

  function deskV1OpenStartSheet(campaignId) {
    const camp = _campaign(campaignId);
    if (!camp) return;
    // T1 (§4): the Start sheet's authority rows read straight off `camp.plan`
    // — the same object the goal sentence and rule chips read.
    const plan = camp.plan || {};
    const shell = document.querySelector('.desk-v1-shell');
    if (!shell) return;
    if (!shell.style.position) shell.style.position = 'relative';
    _closeOverlay();

    const project = _project(camp.projectId);
    // IA2 §3 row 20 (fixtures comment, desk-v1-fixtures.js): `plan.accounts`
    // is bare channel ids since the T1 `plan.destinations[].account`/`.voice`
    // shape was retired in favor of the project's own `presence.accounts[]`
    // — no fixture has ever written `plan.destinations` (verified against
    // camp-1/camp-3), so the old read here always showed "—" for Accounts.
    // Resolve the label AND per-account voice through that same seam.
    const pickedChannels = (plan.accounts || []).map(_channel).filter(Boolean);
    const dests = (plan.accounts || []).map((chId) => {
      const ch = _channel(chId);
      const label = ch ? ch.label : chId;
      const acct = ((project && project.presence && project.presence.accounts) || []).find((a) => a.channel_id === chId);
      return acct && acct.voice ? `${label} (${acct.voice})` : label;
    });
    const datesLabel = plan.end && (plan.end.date
      ? `Ends ${_fmtDateLong(plan.end.date)}`
      : (plan.end.post_cap ? `Ends after ${plan.end.post_cap} posts` : null));
    // R2-11: the Launch page's own gate (goal target + source, term <= 90 d,
    // plus the plan bounds) when campaign.js is loaded; the kit's plan-only
    // gate otherwise.
    const validity = typeof window.deskV1LaunchMissing === 'function'
      ? window.deskV1LaunchMissing(camp, project)
      : DeskV1Kit.validatePlan(plan, project);
    const eff = validity.effective;
    // IA4 (§5 row IA4 acceptance: "inherited rows labelled 'from <project>'")
    // — a presentation-only flag desk-v1-setup.js's step 2 stamps on the plan
    // when it defaulted a bound from the project, distinct from
    // `_effectiveCadence`'s own clamp-comparison flag (which stays false
    // when a campaign's own value already equals the ceiling it inherited,
    // as a freshly-drafted plan's does) — never a second validator/clamp,
    // purely which label this sheet prints beside an already-computed value.
    const inherited = plan._inheritedFields || {};
    // §5 IA4 acceptance ("zero connected accounts completes via '✋ You
    // publish it'"): when every picked account is manual-capability (no
    // account here can be posted to via API), there is nothing for Posy to
    // draft into review — the Replies row names the real mechanism instead
    // of the API-review default.
    const allManual = pickedChannels.length > 0 && pickedChannels.every((ch) => ch.capability === 'manual');
    const auth = {
      accounts: dests,
      frequencyPerWeek: eff.cadence_per_week,
      frequencyFromProject: eff.cadence_from_project || !!inherited.cadence,
      dates: datesLabel,
      // §3 row 7: review mode is retired with no replacement — every piece
      // needs approval (§8 position), so the Start sheet no longer names it.
      replies: allManual
        ? DeskV1Kit.channelCapabilityCopy(pickedChannels[0])
        : (plan.replies === 'auto_faq' ? 'Auto-answer verified FAQ' : 'Drafts for review'),
      paid: plan.paid ? 'On' : 'Off',
      generationLimits: plan.generation,
      // §4: "spend + stop conditions derived" — never a fixture field of its
      // own, so this stays "—" unless there's a real end bound to name.
      stopConditions: _stopConditionsLabel(plan),
    };
    const fromSuffix = ` · from ${project ? project.name : 'project'}`;

    // §2.3 row 3: "inherited rows marked `from <project>` and a `Change for
    // the project ›` link" — each row below that carries an inherited flag
    // gets the link, opening IA3's Presence page for the campaign's own
    // project (the same route desk-v1-project.js's own Presence button uses).
    const rows = [
      ['Accounts', (auth.accounts || []).length ? (auth.accounts.join(', ') + (inherited.accounts ? fromSuffix : '')) : '—', !!inherited.accounts],
      ['Frequency ceiling', auth.frequencyPerWeek != null
        ? `Up to ${auth.frequencyPerWeek} a week${auth.frequencyFromProject ? fromSuffix : ''}`
        : '—', !!auth.frequencyFromProject || !!inherited.cadence],
      ['Dates', auth.dates ? (auth.dates + (inherited.end ? fromSuffix : '')) : '—', !!inherited.end],
      ['Replies', auth.replies || '—', false],
      ['Paid', auth.paid || 'Off', false],
      ['Generation limits', auth.generationLimits || '—', false],
      ['Stop conditions', auth.stopConditions || '—', false],
    ];

    // §2.3 row 3: `validatePlan` ok is required to start — Confirm is
    // disabled until every plan bound resolves, and the sheet names each
    // missing one with the map stop that fixes it (R2-3b: the IA4 "step n"
    // numbering is gone with the setup steps; kit.js `missing[].stop`).
    const _stopWord = (s) => s.charAt(0).toUpperCase() + s.slice(1);
    const missingLabel = validity.missing.map((m) => `${m.label} (${m.stop ? _stopWord(m.stop) : `step ${m.step}`})`).join(', ');
    // R2-2g: the project is picked at Launch, so a not-yet-started campaign
    // without one can't be confirmed from here either (this sheet is also
    // opened from Proposed's own Start button, not just the Launch stop).
    const noProject = (camp.state === 'draft' || camp.state === 'proposed') && !project;
    const canStart = validity.ok && !noProject;
    const noteHTML = noProject
      ? 'Can’t start yet — pick a project on the Launch stop.'
      : (validity.ok
        ? 'Starting doesn’t approve any piece.'
        : `Can’t start yet — missing ${esc(missingLabel)}.`);

    const wrap = document.createElement('div');
    wrap.className = 'desk-v1-rules-overlay';
    wrap.innerHTML = `
      <div class="desk-v1-rules-scrim" data-overlay-scrim></div>
      <div class="desk-v1-rules-sheet" role="dialog" aria-modal="true" aria-label="Start campaign">
        <div class="desk-v1-rules-sheet-title">Start “${esc(camp.plan.title)}”</div>
        <div class="desk-v1-rules-sheet-body">
          ${rows.map(([label, val, isInherited]) => `<div class="desk-v1-rules-authrow"><span class="desk-v1-rules-authrow-label">${esc(label)}</span><span class="desk-v1-rules-authrow-val">${esc(val)}</span>${isInherited ? ' <button type="button" class="desk-v1-rules-changeproject-link" data-change-project>Change for the project ›</button>' : ''}</div>`).join('')}
        </div>
        <div class="desk-v1-rules-sheet-note">${noteHTML}</div>
        <div class="desk-v1-rules-sheet-actions">
          <button type="button" class="desk-v1-rules-sheet-cancel" data-sheet-cancel>Cancel</button>
          <button type="button" class="desk-v1-rules-sheet-confirm" data-sheet-confirm${canStart ? '' : ' disabled'}>Confirm — Start campaign</button>
        </div>
      </div>`;
    shell.appendChild(wrap);

    wrap.querySelectorAll('[data-change-project]').forEach((link) => {
      link.onclick = () => { close(); deskV1Nav('presence', { projectId: camp.projectId }); };
    });

    // Capture phase, not bubble: index.html's own boot-time Escape handler
    // (`focusedModalId` -> closeModalById) is a bubble-phase listener on
    // `document`, registered long before this overlay ever opens — a
    // same-phase listener added now would still fire second and let Escape
    // close the whole Desk board out from under the sheet. Capture always
    // runs before bubble regardless of add order, so stopPropagation() here
    // is what actually stops it (same technique that guard's own comment
    // describes needing for the image viewer and pointer-drag cases).
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
    function close() { wrap.remove(); document.removeEventListener('keydown', onKey, true); }
    document.addEventListener('keydown', onKey, true);
    wrap.querySelector('[data-overlay-scrim]').onclick = close;
    wrap.querySelector('[data-sheet-cancel]').onclick = close;
    wrap.querySelector('[data-sheet-confirm]').onclick = () => { close(); _startCampaign(camp, auth); };
  }

  function _startCampaign(camp, auth) {
    const prevState = camp.state;
    const policyRecord = Object.assign({}, auth, { createdAt: new Date().toISOString() });
    const prev = { term: camp.term, terms: camp.terms, approval: camp.approval, approvals: camp.approvals, startedAt: camp.startedAt };
    DeskV1Kit.commandBus.run({
      label: `Started “${camp.plan.title}”`,
      do: () => {
        camp.state = 'active'; camp.policyRecord = policyRecord;
        // R2-11 (Launch, Live state): Start opens term 1 (today to the plan's
        // end date, unless a term was already set on When) and records the
        // approval — the bounds as they stand, hashed — that Renew / the
        // widening check compare against from here on.
        const now = new Date();
        const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
        const end = camp.plan && camp.plan.end;
        camp.startedAt = now.toISOString();
        if (!camp.term) camp.term = { index: 1, starts: today, ends: end && end.date ? end.date : null, post_cap: end && end.post_cap != null ? end.post_cap : null };
        if (typeof window.deskV1CampaignBounds === 'function') {
          const bounds = window.deskV1CampaignBounds(camp);
          camp.approval = { bounds, bounds_hash: DeskV1Kit.computeBoundsHash(bounds), at: camp.startedAt, term: camp.term.index || 1 };
          camp.approvals = [camp.approval];
        }
        if (typeof window.deskV1Render === 'function') window.deskV1Render();
      },
      undo: () => {
        camp.state = prevState; delete camp.policyRecord;
        Object.keys(prev).forEach((k) => { if (prev[k] === undefined) delete camp[k]; else camp[k] = prev[k]; });
        if (typeof window.deskV1Render === 'function') window.deskV1Render();
      },
    });
  }

  // Dave's review pass 3: a native window.confirm() blocks the render
  // thread and can't be styled — replaced with an in-page sheet using the
  // same overlay/scrim/panel markup as deskV1OpenStartSheet above, just a
  // higher z-index (.desk-v1-rules-confirm-overlay, desk-v1.css) since this
  // one can open ON TOP of the still-showing rules POPOVER. Async by
  // necessity (a DOM dialog can't return synchronously like window.confirm
  // did) — callers pass onConfirm/onDecline instead of branching on a
  // return value.
  function _openWideningConfirm(title, body, note, onConfirm, onDecline) {
    if (!document.querySelector('.desk-v1-shell')) { if (onDecline) onDecline(); return; }

    const wrap = document.createElement('div');
    wrap.className = 'desk-v1-rules-confirm-overlay';
    wrap.innerHTML = `
      <div class="desk-v1-rules-scrim" data-confirm-scrim></div>
      <div class="desk-v1-rules-sheet" role="dialog" aria-modal="true" aria-label="${esc(title)}">
        <div class="desk-v1-rules-sheet-title">${esc(title)}</div>
        <div class="desk-v1-rules-sheet-body"><p class="desk-v1-rules-confirmtext">${esc(body)}</p></div>
        <div class="desk-v1-rules-sheet-note">${esc(note)}</div>
        <div class="desk-v1-rules-sheet-actions">
          <button type="button" class="desk-v1-rules-sheet-cancel" data-confirm-decline>Cancel</button>
          <button type="button" class="desk-v1-rules-sheet-confirm" data-confirm-accept>Confirm</button>
        </div>
      </div>`;
    // Appended to body, not `.desk-v1-shell`: the shell lives inside the
    // Desk's `.modal-window`, which sets its own `style.zIndex` and so forms
    // its own (low-numbered) stacking context — any z-index inside it, no
    // matter how high, can never out-rank the Start sheet's overlay, which
    // is itself appended straight to body (deskV1OpenStartSheet, above).
    document.body.appendChild(wrap);

    // Capture phase, same reason (and same risk) as the Start sheet's own
    // Escape handler above.
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); decline(); } };
    function cleanup() { wrap.remove(); document.removeEventListener('keydown', onKey, true); }
    function decline() { cleanup(); if (onDecline) onDecline(); }
    function accept() { cleanup(); if (onConfirm) onConfirm(); }
    document.addEventListener('keydown', onKey, true);
    wrap.querySelector('[data-confirm-scrim]').onclick = decline;
    wrap.querySelector('[data-confirm-decline]').onclick = decline;
    wrap.querySelector('[data-confirm-accept]').onclick = accept;
  }

  // ────────────────────────────────────────────────────────────────────────
  // §3.4 Posy instructions (INS-01..04): before → after + affected items,
  // Undo via the existing commandBus toast; a widening instruction confirms
  // first instead of applying. (A durable instruction used to become a rule
  // chip; R2-3b retired the chips, so it is now applied like any other.)
  // Simulated intent detection (fixtures only, R0): keyword heuristics, not
  // a real model call — this is interaction validation, not NLU.
  // ────────────────────────────────────────────────────────────────────────
  const _WIDENING_RE = /\bpaid\b|\bbudget\b|more accounts?\b|\bevery ?day\b|\bdaily\b|auto-?answer|auto-?repl(y|ies)|more often|increase (the )?frequency/i;

  function _renderPosyReply(posyBoxEl, before, after, affected) {
    if (!posyBoxEl) return;
    const output = posyBoxEl.querySelector('.desk-v1-posy-output');
    if (!output) return;
    output.innerHTML = `
      <div class="desk-v1-rules-posyreply">
        <div class="desk-v1-rules-posyreply-row"><span class="desk-v1-rules-posyreply-label">Before</span> ${esc(before)}</div>
        <div class="desk-v1-rules-posyreply-row"><span class="desk-v1-rules-posyreply-label">After</span> ${esc(after)}</div>
        ${affected && affected.length ? `<div class="desk-v1-rules-posyreply-affected">Affects: ${esc(affected.join(', '))}</div>` : ''}
      </div>`;
  }

  window.deskV1HandlePosyInstruction = function (camp, text, posyBoxEl, selection) {
    const scopeLabel = (selection && selection.scope === 'card' && selection.label) || camp.plan.title;
    const before = `${scopeLabel} follows the existing rules.`;
    const widening = _WIDENING_RE.test(text);
    const agentName = _agentName(camp);

    const apply = () => {
      const after = `“${text}” applied to ${scopeLabel}.`;
      DeskV1Kit.commandBus.run({
        label: `${agentName}: ${text}`,
        do: () => { _renderPosyReply(posyBoxEl, before, after, [scopeLabel]); },
        undo: () => { _renderPosyReply(posyBoxEl, after, 'Reverted.', [scopeLabel]); },
      });
    };

    if (widening) {
      _openWideningConfirm(
        `Widen what ${agentName} can do?`,
        `This instruction would widen what ${agentName} can do:\n“${text}”`,
        'An authorized user must confirm before it applies.',
        apply,
        () => _renderPosyReply(posyBoxEl, before, 'Not applied — needs an authorized user to confirm.', [scopeLabel]),
      );
      return;
    }

    apply();
  };

  window.deskV1FillProposedSummary = deskV1FillProposedSummary;
  window.deskV1FillProposedContent = deskV1FillProposedContent;
  window.deskV1OpenStartSheet = deskV1OpenStartSheet;
})();

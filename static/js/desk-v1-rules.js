// Desk v1 (MC-977) — T2b: Proposed state, Start sheet, rules popover, Posy
// instructions (frame: none drawn, gap map C6; docs/desk_v1_r0_plan.md;
// THE_DESK_V1_UI.md §3.5, §8, §9, §11). Window-bridged module, no `import`
// (ground rule 1).
//
// Reads the campaign page through the T0a slot contract and three small,
// backward-compatible seams desk-v1-campaign.js (T2a) already carries for
// this exact purpose (its own comments name T2b): a Proposed-state summary/
// content override in deskV1FillCampaignSummary/TabBody, and a Posy-
// instruction override in the right column's onSend. This file never edits
// desk-v1-campaign.js — see the final report for the one small addition to
// _ruleChips() that WAS needed for INS-02 (a durable rule chip has to render
// somewhere, and the rule-chip list lives there).
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function _fx() { return window.DeskV1Fixtures || {}; }
  function _campaigns() { return _fx().campaigns || []; }
  function _campaign(id) { return _campaigns().find((c) => c.id === id) || null; }
  function _channels() { return _fx().channels || []; }
  function _channel(id) { return _channels().find((c) => c.id === id); }
  function _families() { return _fx().families || []; }
  function _familiesFor(campaignId) { return _families().filter((f) => f.campaignId === campaignId); }
  function _proposedDetail(campaignId) { return (_fx().proposedDetail || {})[campaignId] || null; }

  // §3.5: "the same page... shows the same chips" as T2a's summary bar.
  // `window.deskV1RuleChips` is a one-line backward-compatible export added
  // to desk-v1-campaign.js's existing (private) `_ruleChips` — see the final
  // report; it's a pure getter already used by T2a itself, not new logic.
  function _ruleChipsFor(camp) { return typeof window.deskV1RuleChips === 'function' ? window.deskV1RuleChips(camp) : []; }

  function _fmtDate(iso) {
    try { return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(new Date(iso)); }
    catch (e) { return iso; }
  }

  // ────────────────────────────────────────────────────────────────────────
  // §3.5 Proposed state — summary slot override (state pill + editable goal
  // sentence + `Start campaign`, replacing T2a's ordinary Goal/Channels/
  // Rules groups per the doc: "Same page, with state ◇ Proposed... The goal
  // is a single editable sentence"). Channels still render via the shared
  // kit badge (UX-02 — never a bare logo, even here).
  // ────────────────────────────────────────────────────────────────────────
  function deskV1FillProposedSummary(el, params, camp) {
    const detail = _proposedDetail(camp.id) || {};
    const gs = detail.goalSentence || {};
    const stateHTML = DeskV1Kit.stateLabelHTML(camp.state, { className: 'desk-v1-camp-state-pill' });
    const chans = (camp.channelIds || []).map(_channel).filter(Boolean);

    el.innerHTML = `
      <div class="desk-v1-camp-summary-top">
        ${stateHTML}
        <button type="button" class="desk-v1-rules-start-btn" data-start-campaign>Start campaign</button>
      </div>
      <div class="desk-v1-camp-summary-groups">
        <div class="desk-v1-camp-summary-group">
          <span class="desk-v1-camp-summary-label">GOAL</span>
          <div class="desk-v1-rules-goal-sentence">
            <span class="desk-v1-rules-dashed" data-goal-field="target" contenteditable="true" role="textbox" tabindex="0" aria-label="Target number">${esc(gs.target != null ? gs.target : '')}</span>
            ${esc(gs.metric || '')} by
            <span class="desk-v1-rules-dashed" data-goal-field="dateLabel" contenteditable="true" role="textbox" tabindex="0" aria-label="Deadline">${esc(gs.dateLabel || _fmtDate(gs.date))}</span>
            ${gs.audience ? `in <span class="desk-v1-rules-dashed" data-goal-field="audience" contenteditable="true" role="textbox" tabindex="0" aria-label="Audience">${esc(gs.audience)}</span>` : ''}
          </div>
          ${!camp.goal || !camp.goal.tracked ? '<span class="desk-v1-rules-goal-warning">⚠ not tracked yet</span>' : ''}
        </div>
        <div class="desk-v1-camp-summary-group" data-summary-group="channels">
          <span class="desk-v1-camp-summary-label">CHANNELS</span>
          <div class="desk-v1-camp-summary-badges">${chans.length ? chans.map((ch) => DeskV1Kit.channelBadge(ch, { reviewMode: camp.rules && camp.rules.reviewMode })).join('') : '<span class="desk-v1-home-camp-nochannels">No channels yet</span>'}</div>
        </div>
        <div class="desk-v1-camp-summary-group" data-summary-group="rules">
          <span class="desk-v1-camp-summary-label">RULES</span>
          <div class="desk-v1-camp-summary-badges">
            ${_ruleChipsFor(camp).map((c) => `<span class="desk-v1-camp-rule-chip">${esc(c)}</span>`).join('')}
            <button type="button" class="desk-v1-camp-rules-edit" data-rules-edit>Edit</button>
          </div>
        </div>
      </div>`;

    const rulesEditBtn = el.querySelector('[data-rules-edit]');
    if (rulesEditBtn) rulesEditBtn.onclick = () => window.deskV1OpenRulesPopover(camp.id, rulesEditBtn);

    el.querySelectorAll('[data-goal-field]').forEach((span) => {
      span.addEventListener('blur', () => {
        const field = span.dataset.goalField;
        const val = span.textContent.trim();
        if (field === 'target') {
          const n = parseInt(val, 10);
          if (!isNaN(n)) { gs.target = n; if (camp.goal) camp.goal.target = n; }
          else span.textContent = String(gs.target != null ? gs.target : '');
        } else if (field === 'dateLabel') {
          gs.dateLabel = val;
        } else if (field === 'audience') {
          gs.audience = val;
          if (camp.goal) camp.goal.audience = val;
        }
      });
    });

    const startBtn = el.querySelector('[data-start-campaign]');
    if (startBtn) startBtn.onclick = () => deskV1OpenStartSheet(camp.id);
  }

  // ────────────────────────────────────────────────────────────────────────
  // §3.5 Proposed state — content slot override: at most one blocker card
  // ("⛔ Posy's one question", CMP-03) above Posy's proposed pieces, each
  // carrying a "? Assumed" popover where the fixture has one.
  // ────────────────────────────────────────────────────────────────────────
  function deskV1FillProposedContent(el, params, camp) {
    const detail = _proposedDetail(camp.id) || {};
    const fams = _familiesFor(camp.id);
    el.innerHTML = `
      <div class="desk-v1-rules-proposed">
        ${detail.blocker ? _blockerCardHTML(detail.blocker) : ''}
        <div class="desk-v1-camp-cards">${fams.map((f) => _proposedCardHTML(f, detail)).join('') || '<div class="desk-v1-camp-empty">Posy hasn’t proposed any pieces yet.</div>'}</div>
      </div>`;
    if (detail.blocker) _wireBlocker(el, camp, detail.blocker);
  }

  function _blockerCardHTML(blocker) {
    return `<div class="desk-v1-rules-blocker" data-blocker-id="${esc(blocker.id)}">
      <div class="desk-v1-rules-blocker-head">⛔ Posy’s one question</div>
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
        DeskV1Kit.commandBus.run({
          label: `Answered Posy’s question: “${ans ? ans.label : ''}”`,
          do: () => { card.remove(); },
          undo: () => { deskV1FillProposedContent(el, { campaignId: camp.id }, camp); },
        });
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
    const detail = _proposedDetail(campaignId) || {};
    const auth = detail.authority || {};
    const shell = document.querySelector('.desk-v1-shell');
    if (!shell) return;
    if (!shell.style.position) shell.style.position = 'relative';
    _closeOverlay();

    const rows = [
      ['Accounts', (auth.accounts || []).join(', ') || '—'],
      ['Frequency ceiling', auth.frequencyPerWeek != null ? `Up to ${auth.frequencyPerWeek} a week` : '—'],
      ['Dates', auth.dates || '—'],
      ['Review mode', auth.reviewMode || '—'],
      ['Replies', auth.replies || '—'],
      ['Paid', auth.paid || 'Off'],
      ['Generation limits', auth.generationLimits || '—'],
      ['Stop conditions', auth.stopConditions || '—'],
    ];

    const wrap = document.createElement('div');
    wrap.className = 'desk-v1-rules-overlay';
    wrap.innerHTML = `
      <div class="desk-v1-rules-scrim" data-overlay-scrim></div>
      <div class="desk-v1-rules-sheet" role="dialog" aria-modal="true" aria-label="Start campaign">
        <div class="desk-v1-rules-sheet-title">Start “${esc(camp.name)}”</div>
        <div class="desk-v1-rules-sheet-body">
          ${rows.map(([label, val]) => `<div class="desk-v1-rules-authrow"><span class="desk-v1-rules-authrow-label">${esc(label)}</span><span class="desk-v1-rules-authrow-val">${esc(val)}</span></div>`).join('')}
        </div>
        <div class="desk-v1-rules-sheet-note">Starting doesn’t approve any piece.</div>
        <div class="desk-v1-rules-sheet-actions">
          <button type="button" class="desk-v1-rules-sheet-cancel" data-sheet-cancel>Cancel</button>
          <button type="button" class="desk-v1-rules-sheet-confirm" data-sheet-confirm>Confirm — Start campaign</button>
        </div>
      </div>`;
    shell.appendChild(wrap);

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
    DeskV1Kit.commandBus.run({
      label: `Started “${camp.name}”`,
      do: () => { camp.state = 'active'; camp.policyRecord = policyRecord; if (typeof window.deskV1Render === 'function') window.deskV1Render(); },
      undo: () => { camp.state = prevState; delete camp.policyRecord; if (typeof window.deskV1Render === 'function') window.deskV1Render(); },
    });
  }

  // ────────────────────────────────────────────────────────────────────────
  // §8 Rules popover, anchored to the summary bar's rule chips (§1: "rule
  // chips... open a popover. They are never form fields on the page" — a
  // prior draft of this file treated the doc's "popover" heading as license
  // to keep the existing full-`‹ <campaign>`-page route; Dave's review
  // pass corrected that). One instance open at a time (`_rulesPop`), closed
  // by Esc (capture phase, same reason the Start sheet needs it — see its
  // own comment above), an outside click, or opening a different campaign's.
  // Desktop: a floating card positioned from the anchor button's own
  // `getBoundingClientRect()`. Phone (§11: "a bottom sheet is fine"): CSS
  // alone re-docks the same markup full-width to the bottom edge — the same
  // pattern already proven by the Start sheet's own `.desk-v1-rules-overlay`
  // above, just under new class names so the two never visually collide if
  // both were ever open (they can't be, in practice — Start sheet only
  // exists on the Proposed page, before there's anything to widen).
  // ────────────────────────────────────────────────────────────────────────
  let _rulesPop = null;

  function _closeRulesPopover() {
    if (!_rulesPop) return;
    document.removeEventListener('keydown', _rulesPop.onKey, true);
    document.removeEventListener('click', _rulesPop.onOutsideClick, true);
    if (_rulesPop.el.parentNode) _rulesPop.el.parentNode.removeChild(_rulesPop.el);
    _rulesPop = null;
  }

  function _positionRulesPopover(panel, anchorEl) {
    // Phone re-docks via the @media rule instead (bottom-sheet, full width);
    // an inline top/left here would fight that, so skip it under 960px.
    if (!anchorEl || window.innerWidth <= 960) return;
    const rect = anchorEl.getBoundingClientRect();
    const width = panel.getBoundingClientRect().width || 380;
    const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
    const top = Math.min(rect.bottom + 8, window.innerHeight - 40);
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
    panel.style.right = 'auto';
  }

  window.deskV1OpenRulesPopover = function (campaignId, anchorEl) {
    const camp = _campaign(campaignId);
    if (!camp) return;
    anchorEl = anchorEl || document.querySelector('#desk-v1-camp-summary [data-rules-edit]');
    _closeRulesPopover();

    const wrap = document.createElement('div');
    wrap.className = 'desk-v1-rules-pop-overlay';
    wrap.innerHTML = `
      <div class="desk-v1-rules-pop-scrim" data-pop-scrim></div>
      <div class="desk-v1-rules-pop" role="dialog" aria-modal="false" aria-label="Rules for ${esc(camp.name)}">
        <div class="desk-v1-rules-pop-title">Rules — ${esc(camp.name)}</div>
        <div class="desk-v1-rules-pop-body"></div>
      </div>`;
    document.body.appendChild(wrap);
    const panel = wrap.querySelector('.desk-v1-rules-pop');
    _positionRulesPopover(panel, anchorEl);

    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); _closeRulesPopover(); } };
    const onOutsideClick = (e) => { if (!panel.contains(e.target) && e.target !== anchorEl) _closeRulesPopover(); };
    document.addEventListener('keydown', onKey, true);
    // Deferred one tick so the click that opened the popover (the Edit
    // button itself) isn't also the click that immediately closes it.
    setTimeout(() => document.addEventListener('click', onOutsideClick, true), 0);
    wrap.querySelector('[data-pop-scrim]').onclick = _closeRulesPopover;

    _rulesPop = { el: wrap, onKey, onOutsideClick, pending: null };
    _fillRulesPopoverBody(panel.querySelector('.desk-v1-rules-pop-body'), camp);
  };

  function _confirmWidening(effectText) {
    return window.confirm(`This widens what Posy can do:\n${effectText}\nAn authorized user must confirm. Continue?`);
  }

  function _refreshRuleChips(camp) {
    const summaryHost = document.getElementById('desk-v1-camp-summary');
    if (!summaryHost) return;
    if (camp.state === 'proposed' && typeof window.deskV1FillProposedSummary === 'function') window.deskV1FillProposedSummary(summaryHost, { campaignId: camp.id }, camp);
    else if (typeof window.deskV1FillCampaignSummary === 'function') window.deskV1FillCampaignSummary(summaryHost, { campaignId: camp.id });
  }

  // §8: "Auto-answer verified FAQ on <account>" — the first attached
  // channel's identity, since replies aren't per-channel in this fixture set.
  function _repliesAccountLabel(camp) {
    const ch = (camp.channelIds || []).map(_channel).filter(Boolean)[0];
    return ch ? ` on ${esc(ch.identity || ch.label)}` : '';
  }

  function _campaignAccountsLabel(camp) {
    return (camp.channelIds || []).map(_channel).filter(Boolean).map((ch) => ch.label).join(', ');
  }

  function _fillRulesPopoverBody(bodyEl, camp) {
    const r = camp.rules = camp.rules || {};
    const budget = _fx().renderBudget || {};
    const chans = _channels();
    const currency = budget.currency === 'USD' ? '$' : (budget.currency || '');

    bodyEl.innerHTML = `
      <div class="desk-v1-rules-group">
        <div class="desk-v1-rules-group-title">Review mode</div>
        <label class="desk-v1-rules-radio"><input type="radio" name="reviewMode" value="each_piece" ${r.reviewMode !== 'themes' ? 'checked' : ''}> You approve each piece</label>
        <label class="desk-v1-rules-radio"><input type="radio" name="reviewMode" value="themes" ${r.reviewMode === 'themes' ? 'checked' : ''}> Approve themes, then run</label>
      </div>
      <div class="desk-v1-rules-group">
        <div class="desk-v1-rules-group-title">Frequency ceiling ${DeskV1Kit.infoIconHTML('freq')}</div>
        <div class="desk-v1-rules-inlinerow">Up to <input type="number" min="0" max="30" class="desk-v1-rules-numinput" data-freq-input value="${esc(r.frequencyPerWeek != null ? r.frequencyPerWeek : 0)}"> a week</div>
      </div>
      <div class="desk-v1-rules-group">
        <div class="desk-v1-rules-group-title">Channels</div>
        ${chans.map((ch) => `<label class="desk-v1-rules-checkrow"><input type="checkbox" data-channel-toggle="${esc(ch.id)}" ${camp.channelIds.includes(ch.id) ? 'checked' : ''}> ${esc(ch.label)}${!camp.channelIds.includes(ch.id) ? ' <span class="desk-v1-rules-excluded">excluded</span>' : ''}</label>`).join('')}
      </div>
      <div class="desk-v1-rules-group">
        <div class="desk-v1-rules-group-title">Replies</div>
        <label class="desk-v1-rules-radio"><input type="radio" name="repliesMode" value="drafts" ${r.repliesMode !== 'auto_faq' ? 'checked' : ''}> Drafts for review</label>
        <label class="desk-v1-rules-radio"><input type="radio" name="repliesMode" value="auto_faq" ${r.repliesMode === 'auto_faq' ? 'checked' : ''}> Auto-answer verified FAQ${_repliesAccountLabel(camp)}</label>
      </div>
      <div class="desk-v1-rules-group">
        <div class="desk-v1-rules-group-title">Paid</div>
        <label class="desk-v1-rules-radio"><input type="radio" name="paid" value="off" ${!r.paid ? 'checked' : ''}> Off</label>
        <label class="desk-v1-rules-radio"><input type="radio" name="paid" value="on" ${r.paid ? 'checked' : ''}> On</label>
      </div>
      <div class="desk-v1-rules-group">
        <div class="desk-v1-rules-group-title">Production budget</div>
        <div class="desk-v1-rules-inlinerow">${esc(currency)}<input type="number" min="0" class="desk-v1-rules-numinput" data-budget-input value="${esc(budget.limit != null ? budget.limit : 0)}"> per ${esc(budget.period || 'month')}</div>
        <div class="desk-v1-rules-hint">${esc(currency)}${esc(budget.spent != null ? budget.spent : 0)} spent so far this ${esc(budget.period || 'month')}.</div>
      </div>
      <div class="desk-v1-rules-pop-preview" id="desk-v1-rules-pop-preview" aria-live="polite" hidden></div>`;

    DeskV1Kit.bindInfoIcons(bodyEl, { freq: 'A ceiling, not a quota — Posy won’t post more than this, but may post fewer.' });

    // §8: "Every change shows its effect before applying" — a control's
    // 'change' event never mutates the fixture directly; it stages one
    // pending change (mutate + a human-readable effect + whether it widens
    // authority) and shows Cancel/Apply. Only Apply calls mutate(); Cancel
    // (or staging a second control before the first is applied) calls
    // revert() so the control snaps back to the still-real value — at most
    // one pending change is ever shown, matching the doc's singular "its".
    const previewEl = bodyEl.querySelector('#desk-v1-rules-pop-preview');
    function setPending(mutate, effectText, widening, revert) {
      if (_rulesPop && _rulesPop.pending) _rulesPop.pending.revert();
      previewEl.hidden = false;
      previewEl.innerHTML = `
        <div class="desk-v1-rules-pop-previewtext">${esc(effectText)}</div>
        <div class="desk-v1-rules-pop-previewbtns">
          <button type="button" class="desk-v1-rules-pop-cancel" data-pop-preview-cancel>Cancel</button>
          <button type="button" class="desk-v1-rules-pop-apply" data-pop-preview-apply>Apply</button>
        </div>`;
      const clear = () => { previewEl.hidden = true; previewEl.innerHTML = ''; if (_rulesPop) _rulesPop.pending = null; };
      previewEl.querySelector('[data-pop-preview-cancel]').onclick = () => { revert(); clear(); };
      previewEl.querySelector('[data-pop-preview-apply]').onclick = () => {
        if (widening && !_confirmWidening(effectText)) { revert(); clear(); return; }
        mutate();
        clear();
        DeskV1Kit.toast(effectText);
        _refreshRuleChips(camp);
      };
      if (_rulesPop) _rulesPop.pending = { revert };
    }

    bodyEl.querySelectorAll('input[name="reviewMode"]').forEach((radio) => {
      radio.addEventListener('change', () => {
        const prevVal = r.reviewMode === 'themes' ? 'themes' : 'each_piece';
        const next = radio.value;
        if (next === prevVal) return;
        const widening = next === 'themes';
        const effect = next === 'themes'
          ? 'Posy approves whole themes and runs them without a per-piece check.'
          : 'Every piece needs your approval again before it goes out.';
        const prevRadio = bodyEl.querySelector(`input[name="reviewMode"][value="${prevVal}"]`);
        setPending(() => { r.reviewMode = next; }, effect, widening, () => { if (prevRadio) prevRadio.checked = true; });
      });
    });

    const freqInput = bodyEl.querySelector('[data-freq-input]');
    if (freqInput) freqInput.addEventListener('change', () => {
      const prev = r.frequencyPerWeek || 0;
      const next = parseInt(freqInput.value, 10) || 0;
      if (next === prev) return;
      const accounts = _campaignAccountsLabel(camp);
      const effect = `Posy will publish at most ${next} post${next === 1 ? '' : 's'} a week${accounts ? ` on ${accounts}` : ''} — was ${prev}.`;
      setPending(() => { r.frequencyPerWeek = next; }, effect, next > prev, () => { freqInput.value = String(prev); });
    });

    bodyEl.querySelectorAll('[data-channel-toggle]').forEach((cb) => {
      cb.addEventListener('change', () => {
        const chId = cb.dataset.channelToggle;
        const ch = _channel(chId);
        const included = camp.channelIds.includes(chId);
        if (cb.checked === included) return;
        const label = ch ? ch.label : chId;
        const effect = cb.checked ? `${label} can now be used by this campaign.` : `${label} is excluded from this campaign.`;
        setPending(() => {
          if (cb.checked) camp.channelIds.push(chId);
          else camp.channelIds = camp.channelIds.filter((id) => id !== chId);
        }, effect, cb.checked, () => { cb.checked = included; });
      });
    });

    bodyEl.querySelectorAll('input[name="repliesMode"]').forEach((radio) => {
      radio.addEventListener('change', () => {
        const prevVal = r.repliesMode === 'auto_faq' ? 'auto_faq' : 'drafts';
        const next = radio.value;
        if (next === prevVal) return;
        const widening = next === 'auto_faq';
        const effect = next === 'auto_faq'
          ? `Verified FAQ replies go out without a draft review${_repliesAccountLabel(camp)}.`
          : 'Every reply goes back to drafts for your review.';
        const prevRadio = bodyEl.querySelector(`input[name="repliesMode"][value="${prevVal}"]`);
        setPending(() => { r.repliesMode = next; }, effect, widening, () => { if (prevRadio) prevRadio.checked = true; });
      });
    });

    bodyEl.querySelectorAll('input[name="paid"]').forEach((radio) => {
      radio.addEventListener('change', () => {
        const prevVal = r.paid ? 'on' : 'off';
        const next = radio.value;
        if (next === prevVal) return;
        const widening = next === 'on';
        const effect = next === 'on'
          ? 'Paid distribution turns on — this opens paid terms (out of scope this release; nothing spends here).'
          : 'Paid distribution turns off.';
        const prevRadio = bodyEl.querySelector(`input[name="paid"][value="${prevVal}"]`);
        setPending(() => { r.paid = next === 'on'; }, effect, widening, () => { if (prevRadio) prevRadio.checked = true; });
      });
    });

    const budgetInput = bodyEl.querySelector('[data-budget-input]');
    if (budgetInput) budgetInput.addEventListener('change', () => {
      const prev = budget.limit || 0;
      const next = parseFloat(budgetInput.value) || 0;
      if (next === prev) return;
      setPending(() => { budget.limit = next; }, `Video budget set to ${currency}${next} per ${budget.period || 'month'} — was ${currency}${prev}.`, next > prev, () => { budgetInput.value = String(prev); });
    });
  }

  // Kept exported only so desk-v1-shell.js's pre-existing ROUTES['rules']
  // entry (T0a, out of this file's scope) and T5's `deskV1Nav('rules',
  // {campaignId})` "Raise budget…" deep link still resolve to something —
  // neither ever paints a page under a `‹ <campaign>` breadcrumb now: this
  // bounces straight back to the campaign page and opens the real popover.
  window.deskV1RenderRules = function (el, params) {
    if (typeof window.deskV1Nav === 'function') window.deskV1Nav('campaign', { campaignId: params.campaignId });
    window.deskV1OpenRulesPopover(params.campaignId);
  };

  // ────────────────────────────────────────────────────────────────────────
  // §3.4 Posy instructions (INS-01..04): before → after + affected items,
  // Undo via the existing commandBus toast; a widening instruction confirms
  // first instead of applying; a durable one becomes a visible rule chip.
  // Simulated intent detection (fixtures only, R0): keyword heuristics, not
  // a real model call — this is interaction validation, not NLU.
  // ────────────────────────────────────────────────────────────────────────
  const _WIDENING_RE = /\bpaid\b|\bbudget\b|more accounts?\b|\bevery ?day\b|\bdaily\b|auto-?answer|auto-?repl(y|ies)|more often|increase (the )?frequency/i;
  const _DURABLE_RE = /\balways\b|from now on|every time|going forward|\bwhenever\b/i;

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
    const scopeLabel = (selection && selection.scope === 'card' && selection.label) || camp.name;
    const before = `${scopeLabel} follows the existing rules.`;
    const widening = _WIDENING_RE.test(text);
    const durable = _DURABLE_RE.test(text);

    if (widening) {
      const proceed = window.confirm(`This instruction would widen what Posy can do:\n“${text}”\nAn authorized user must confirm before it applies. Continue?`);
      if (!proceed) {
        _renderPosyReply(posyBoxEl, before, 'Not applied — needs an authorized user to confirm.', [scopeLabel]);
        return;
      }
    }

    const after = `“${text}” applied to ${scopeLabel}.`;
    let addedChip = null;
    DeskV1Kit.commandBus.run({
      label: `Posy: ${text}`,
      do: () => {
        if (durable) {
          camp.rules = camp.rules || {};
          camp.rules.customChips = camp.rules.customChips || [];
          addedChip = text.length > 40 ? text.slice(0, 37) + '…' : text;
          camp.rules.customChips.push(addedChip);
          const summaryHost = document.getElementById('desk-v1-camp-summary');
          if (summaryHost && typeof window.deskV1FillCampaignSummary === 'function') window.deskV1FillCampaignSummary(summaryHost, { campaignId: camp.id });
        }
        _renderPosyReply(posyBoxEl, before, after, [scopeLabel]);
      },
      undo: () => {
        if (durable && addedChip && camp.rules.customChips) {
          camp.rules.customChips = camp.rules.customChips.filter((c) => c !== addedChip);
          const summaryHost = document.getElementById('desk-v1-camp-summary');
          if (summaryHost && typeof window.deskV1FillCampaignSummary === 'function') window.deskV1FillCampaignSummary(summaryHost, { campaignId: camp.id });
        }
        _renderPosyReply(posyBoxEl, after, 'Reverted.', [scopeLabel]);
      },
    });
  };

  window.deskV1FillProposedSummary = deskV1FillProposedSummary;
  window.deskV1FillProposedContent = deskV1FillProposedContent;
  window.deskV1OpenStartSheet = deskV1OpenStartSheet;
})();

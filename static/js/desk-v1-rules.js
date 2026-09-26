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
      </div>`;

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
  // §8 Rules popover. T5's own "Raise budget…" link already navigates here
  // via `deskV1Nav('rules', {campaignId})` (the shell's pre-registered
  // route, T0a) — `deskV1OpenRulesPopover` (T2a's Edit-hook target) reuses
  // the exact same route rather than a second, competing UI for one page.
  // "Popover" in the doc's own title is the entry point (a link from the
  // summary bar), not a literal floating layer — the shell already treats
  // this as a full `‹ <campaign>` page, same as every other Desk surface.
  // ────────────────────────────────────────────────────────────────────────
  window.deskV1OpenRulesPopover = function (campaignId) {
    if (typeof window.deskV1Nav === 'function') window.deskV1Nav('rules', { campaignId });
  };

  function _confirmWidening(effectText) {
    return window.confirm(`This widens what Posy can do:\n${effectText}\nAn authorized user must confirm. Continue?`);
  }

  // Applies one rule change. Non-widening changes apply immediately;
  // widening ones (AUT, INS-03) ask first and, on decline, re-render the
  // whole page so every control snaps back to the real (unchanged) value
  // rather than tracking each control's prior state by hand.
  function _applyRule(el, camp, mutate, effectText, widening) {
    if (widening && !_confirmWidening(effectText)) {
      deskV1RenderRules(el, { campaignId: camp.id });
      return;
    }
    mutate();
    const effectEl = document.getElementById('desk-v1-rules-effect');
    if (effectEl) effectEl.textContent = effectText;
    DeskV1Kit.toast(effectText);
  }

  // §8: "Auto-answer verified FAQ on <account>" — the first attached
  // channel's identity, since replies aren't per-channel in this fixture set.
  function _repliesAccountLabel(camp) {
    const ch = (camp.channelIds || []).map(_channel).filter(Boolean)[0];
    return ch ? ` on ${esc(ch.identity || ch.label)}` : '';
  }

  function deskV1RenderRules(el, params) {
    const camp = _campaign(params.campaignId);
    if (!camp) { el.innerHTML = '<div class="desk-v1-stub-inline">Campaign not found.</div>'; return; }
    const r = camp.rules = camp.rules || {};
    const budget = _fx().renderBudget || {};
    const chans = _channels();
    const currency = budget.currency === 'USD' ? '$' : (budget.currency || '');

    el.innerHTML = `
      <div class="desk-v1-rules-page">
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
        <div class="desk-v1-rules-effect" id="desk-v1-rules-effect" aria-live="polite"></div>
      </div>`;

    DeskV1Kit.bindInfoIcons(el, { freq: 'A ceiling, not a quota — Posy won’t post more than this, but may post fewer.' });

    el.querySelectorAll('input[name="reviewMode"]').forEach((radio) => {
      radio.addEventListener('change', () => {
        const next = radio.value;
        if (next === (r.reviewMode || 'each_piece')) return;
        const widening = next === 'themes';
        const effect = next === 'themes'
          ? 'Posy approves whole themes and runs them without a per-piece check.'
          : 'Every piece needs your approval again before it goes out.';
        _applyRule(el, camp, () => { r.reviewMode = next; }, effect, widening);
      });
    });

    const freqInput = el.querySelector('[data-freq-input]');
    if (freqInput) freqInput.addEventListener('change', () => {
      const next = parseInt(freqInput.value, 10) || 0;
      const prev = r.frequencyPerWeek || 0;
      if (next === prev) return;
      _applyRule(el, camp, () => { r.frequencyPerWeek = next; }, `Up to ${next} a week (a ceiling, not a quota) — was ${prev}.`, next > prev);
    });

    el.querySelectorAll('[data-channel-toggle]').forEach((cb) => {
      cb.addEventListener('change', () => {
        const chId = cb.dataset.channelToggle;
        const ch = _channel(chId);
        const included = camp.channelIds.includes(chId);
        if (cb.checked === included) return;
        const label = ch ? ch.label : chId;
        const effect = cb.checked ? `${label} can now be used by this campaign.` : `${label} is excluded from this campaign.`;
        _applyRule(el, camp, () => {
          if (cb.checked) camp.channelIds.push(chId);
          else camp.channelIds = camp.channelIds.filter((id) => id !== chId);
        }, effect, cb.checked);
      });
    });

    el.querySelectorAll('input[name="repliesMode"]').forEach((radio) => {
      radio.addEventListener('change', () => {
        const next = radio.value;
        if (next === (r.repliesMode || 'drafts')) return;
        const widening = next === 'auto_faq';
        const effect = next === 'auto_faq'
          ? 'Verified FAQ replies go out without a draft review.'
          : 'Every reply goes back to drafts for your review.';
        _applyRule(el, camp, () => { r.repliesMode = next; }, effect, widening);
      });
    });

    el.querySelectorAll('input[name="paid"]').forEach((radio) => {
      radio.addEventListener('change', () => {
        const next = radio.value === 'on';
        if (next === !!r.paid) return;
        const effect = next
          ? 'Paid distribution turns on — this opens paid terms (out of scope this release; nothing spends here).'
          : 'Paid distribution turns off.';
        _applyRule(el, camp, () => { r.paid = next; }, effect, next);
      });
    });

    const budgetInput = el.querySelector('[data-budget-input]');
    if (budgetInput) budgetInput.addEventListener('change', () => {
      const next = parseFloat(budgetInput.value) || 0;
      const prev = budget.limit || 0;
      if (next === prev) return;
      _applyRule(el, camp, () => { budget.limit = next; }, `Video budget set to ${currency}${next} per ${budget.period || 'month'} — was ${currency}${prev}.`, next > prev);
    });
  }

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

  window.deskV1RenderRules = deskV1RenderRules;
  window.deskV1FillProposedSummary = deskV1FillProposedSummary;
  window.deskV1FillProposedContent = deskV1FillProposedContent;
  window.deskV1OpenStartSheet = deskV1OpenStartSheet;
})();

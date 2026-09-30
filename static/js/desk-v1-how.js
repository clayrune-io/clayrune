// Desk v1 (MC-977) — R2-6: the ② How stop of the campaign map (docs/
// THE_DESK_V1_IA_REVISION_2.md §11 amended row "R2-6", §11.3). Window-bridged
// module, no `import` (ground rule 1) — mounted by desk-v1-shell.js's
// `_renderCampaignPanel` straight into the tabbody slot as
// `window.deskV1RenderHow(el, params)`, same T0a contract every other
// dedicated stop renderer (results/calendar) already follows.
//
// Strategy/angle/never_claim/budget live on `camp.how` (== `camp.plan.how`,
// the SAME object — desk-v1-fixtures.js's `_CAMP1_HOW` comment: "so every
// reader... sees the same data instead of drifting"). §11.3 item 1: this
// panel no longer writes `how.agent` — that picker moved to R2-18's Plan
// stop. The right-column agent box (desk-v1-campaign.js
// `deskV1FillCampaignRightColumn`) keeps resolving its name read-only via
// `DeskV1Kit.deskAgentRef({project, campaign})`, unchanged by this ticket.
//
// §4.2 item 2: "`Suggest What / When / Where` runs one agent task (IA4's
// plan task lifecycle, UX_PASS §5, unchanged: Working, Ready, Failed +
// Retry, Needs answer)" on "the right-column agent box" — that box already
// exists (desk-v1-campaign.js `deskV1FillCampaignRightColumn`, mounted by
// the shell's slot loop regardless of which map stop is showing) and
// already runs that exact lifecycle via `DeskV1Kit.bindPosyBox(...,
// {taskLifecycle: true})`. Rather than build a second lifecycle here, the
// Suggest button below drives THAT box's real Send (fills its textarea,
// clicks its Send button) — Working/Failed/Retry/the "still on the last
// one" guard all come for free, and `window.__deskV1PosyForce` (kit's only
// test seam) forces failure for the smoke exactly as it does everywhere
// else. desk-v1-campaign.js's `onSend` handler recognises this exact
// request string and runs `_runSuggestTask` (see that file).
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const SUGGEST_TEXT = 'Suggest What / When / Where';

  function _fx() { return window.DeskV1Fixtures || {}; }
  function _campaign(id) { return (_fx().campaigns || []).find((c) => c.id === id) || null; }
  function _project(id) { return (_fx().projects || []).find((p) => p.id === id) || null; }

  function _budgetSourceLabel(source) {
    if (source === 'project') return 'Project earmark';
    if (source === 'own') return 'Own budget';
    return 'None';
  }

  // §11.3 item 2 pool line: "$<amount> / term earmarked from <project>'s
  // $<pool>/<period> budget · $<remaining> remaining for other campaigns"
  // (amended row R2-6, mockup frame 4). `remaining` is the project's pool
  // minus every OTHER live campaign's own project-source earmark under the
  // same project, minus THIS campaign's own amount — what's left for a
  // campaign that isn't this one. Only meaningful for `source: 'project'`.
  function _budgetPoolLine(camp, how, project) {
    const pool = project && project.presence && project.presence.budget;
    if (!pool) return null;
    const others = (_fx().campaigns || [])
      .filter((c) => c.id !== camp.id && c.projectId === camp.projectId && c.how && c.how.budget && c.how.budget.source === 'project')
      .reduce((sum, c) => sum + (c.how.budget.amount || 0), 0);
    const amount = how.budget.amount || 0;
    const remaining = (pool.amount || 0) - others - amount;
    return `$${amount} / term earmarked from ${esc(project.name)}'s $${pool.amount}/${esc(pool.period)} budget · $${remaining} remaining for other campaigns`;
  }

  function _budgetHint(camp, how, project) {
    if (how.budget.source === 'project') {
      return _budgetPoolLine(camp, how, project) || `${_budgetSourceLabel(how.budget.source)} · $${how.budget.amount || 0}.`;
    }
    return `${_budgetSourceLabel(how.budget.source)}${how.budget.source !== 'none' ? ` · $${how.budget.amount || 0}` : ''}. A raise (or None → Own → Project) widens what's approved; a lower amount does not restore an already-widened approval.`;
  }

  function deskV1RenderHow(el, params) {
    const camp = _campaign(params.campaignId);
    if (!camp) { el.innerHTML = '<div class="desk-v1-stub-inline">Campaign not found.</div>'; return; }
    camp.how = camp.how || { strategy: '', angle: '', never_claim: '', agent: null, budget: { source: 'none' } };
    const how = camp.how;
    how.budget = how.budget || { source: 'none' };
    const project = _project(camp.projectId);

    el.innerHTML = `
      <div class="desk-v1-how">
        <div class="desk-v1-how-scroll">
          <div class="desk-v1-how-card">
            <div class="desk-v1-how-card-title">Strategy</div>
            <div class="desk-v1-how-field">
              <label class="desk-v1-how-field-label">Angle</label>
              <textarea class="desk-v1-rules-textarea" data-how-angle rows="2" placeholder="The through-line.">${esc(how.angle || '')}</textarea>
            </div>
            <div class="desk-v1-how-field">
              <label class="desk-v1-how-field-label">Strategy</label>
              <textarea class="desk-v1-rules-textarea" data-how-strategy rows="2" placeholder="Who, what argument, why these channels.">${esc(how.strategy || '')}</textarea>
            </div>
            <div class="desk-v1-how-field">
              <label class="desk-v1-how-field-label">Never claim</label>
              <input type="text" class="desk-v1-rules-textinput" data-how-never-claim value="${esc(how.never_claim || '')}" placeholder="What this campaign never asserts.">
            </div>
          </div>
          <div class="desk-v1-how-card" data-how-budget-card>
            <div class="desk-v1-how-card-title">Budget (optional)</div>
            <div class="desk-v1-how-budget-toggle" role="group" aria-label="Budget source">
              <button type="button" data-how-budget-btn="none" aria-pressed="${how.budget.source === 'none'}">None</button>
              <button type="button" data-how-budget-btn="project" aria-pressed="${how.budget.source === 'project'}">Project earmark</button>
              <button type="button" data-how-budget-btn="own" aria-pressed="${how.budget.source === 'own'}">Own</button>
            </div>
            ${how.budget.source !== 'none' ? `<div class="desk-v1-rules-inlinerow">$<input type="number" min="0" class="desk-v1-rules-numinput" data-how-budget-amount value="${esc(how.budget.amount || 0)}"></div>` : ''}
            <div class="desk-v1-rules-hint">${_budgetHint(camp, how, project)}</div>
          </div>
        </div>
        <div class="desk-v1-how-suggest">
          <button type="button" class="desk-v1-how-suggest-btn" data-how-suggest>${esc(SUGGEST_TEXT)}</button>
          <div class="desk-v1-rules-hint">Writes draft suggestions into What, When and Where — nothing is committed until you accept it there.</div>
        </div>
      </div>`;

    _bind(el, camp, project);
  }

  function _bind(el, camp, project) {
    const how = camp.how;

    const stratEl = el.querySelector('[data-how-strategy]');
    if (stratEl) stratEl.addEventListener('change', () => { how.strategy = stratEl.value.trim(); });
    const angleEl = el.querySelector('[data-how-angle]');
    if (angleEl) angleEl.addEventListener('change', () => { how.angle = angleEl.value.trim(); });
    const neverClaimEl = el.querySelector('[data-how-never-claim]');
    if (neverClaimEl) neverClaimEl.addEventListener('change', () => { how.never_claim = neverClaimEl.value.trim(); });

    el.querySelectorAll('[data-how-budget-btn]').forEach((btn) => {
      btn.onclick = () => {
        const source = btn.getAttribute('data-how-budget-btn');
        how.budget = how.budget || {};
        how.budget.source = source;
        if (source === 'none') how.budget.amount = 0;
        else how.budget.amount = how.budget.amount || 0;
        deskV1RenderHow(el, { campaignId: camp.id });
      };
    });
    const amountEl = el.querySelector('[data-how-budget-amount]');
    if (amountEl) amountEl.addEventListener('change', () => {
      how.budget.amount = parseInt(amountEl.value, 10) || 0;
      const hint = el.querySelector('[data-how-budget-card] .desk-v1-rules-hint');
      if (hint) hint.textContent = _budgetHint(camp, how, project);
    });

    const suggestBtn = el.querySelector('[data-how-suggest]');
    if (suggestBtn) suggestBtn.onclick = () => {
      const ta = document.getElementById('desk-v1-camp-posy-input');
      const sendBtn = document.querySelector('[data-posy-send="desk-v1-camp-posy-input"]');
      if (!ta || !sendBtn) { DeskV1Kit.toast('Agent box not ready yet.'); return; }
      ta.value = SUGGEST_TEXT;
      sendBtn.click();
    };
  }

  window.deskV1RenderHow = deskV1RenderHow;
})();

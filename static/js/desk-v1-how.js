// Desk v1 (MC-977) — R2-6 + R2-18: the ① Brief stop of the campaign map (route
// key `how`; docs/THE_DESK_V1_IA_REVISION_2.md §11 amended rows "R2-6" and
// "R2-18", §11.3). R2-18 moved it FIRST and put the campaign's frame on top of
// the R2-6 cards: Project select, then the Agent picker (see `_paintAgent`).
// Window-bridged
// module, no `import` (ground rule 1) — mounted by desk-v1-shell.js's
// `_renderCampaignPanel` straight into the tabbody slot as
// `window.deskV1RenderHow(el, params)`, same T0a contract every other
// dedicated stop renderer (results/calendar) already follows.
//
// Strategy/angle/never_claim/budget live on `camp.how` (== `camp.plan.how`,
// the SAME object — desk-v1-fixtures.js's `_CAMP1_HOW` comment: "so every
// reader... sees the same data instead of drifting"). R2-18 (Ron 2026-09-30,
// reversing the same-day "agents per project only" ruling): this panel writes
// `how.agent` again — the agent belongs to the CAMPAIGN. The right-column
// agent box (desk-v1-campaign.js `deskV1FillCampaignRightColumn`) resolves it
// via `DeskV1Kit.deskAgentRef({project, campaign})`: `how.agent` first, then
// the project's `presence.desk_agent`.
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

  function _fx() { return window.DeskV1Store.state(); }
  function _campaign(id) { return (_fx().campaigns || []).find((c) => c.id === id) || null; }
  function _project(id) { return (_fx().projects || []).find((p) => p.id === id) || null; }

  // Limits (MC-977 2026-10-01, Presence retired): cadence, min gap, end date,
  // post cap and budget are the campaign's own and are edited here, nowhere
  // else. There is no project pool, so a budget is just an amount; a stored
  // `source: 'project'` (the retired earmark) reads as the same amount.
  function _budgetHint(how) {
    return how.budget.source !== 'none'
      ? `Up to $${how.budget.amount || 0} for this campaign. A raise widens what's approved; a lower amount does not restore an already-widened approval.`
      : 'No budget set. Spend is capped only by the derived ceiling (posts × link rate).';
  }

  function _limitsHTML(camp) {
    const plan = camp.plan || {};
    const cad = plan.cadence || {};
    const end = plan.end || {};
    const endDate = end.date || (camp.term && camp.term.ends) || '';
    const how = camp.how;
    return `
          <div class="desk-v1-how-card" data-how-limits-card>
            <div class="desk-v1-how-card-title">Limits</div>
            <div class="desk-v1-rules-inlinerow">Up to <input type="number" min="0" max="30" class="desk-v1-rules-numinput" data-how-limit="per_week" value="${esc(cad.per_week != null ? cad.per_week : '')}" placeholder="—"> posts a week</div>
            <div class="desk-v1-rules-inlinerow">At least <input type="number" min="0" max="72" class="desk-v1-rules-numinput" data-how-limit="min_gap_h" value="${esc(cad.min_gap_h != null ? cad.min_gap_h : '')}" placeholder="—"> hours apart</div>
            <div class="desk-v1-rules-inlinerow">Ends <input type="date" class="desk-v1-rules-textinput" data-how-limit="end_date" value="${esc(endDate)}"></div>
            <div class="desk-v1-rules-inlinerow">Or after <input type="number" min="0" class="desk-v1-rules-numinput" data-how-limit="post_cap" value="${esc(end.post_cap != null ? end.post_cap : '')}" placeholder="none"> posts</div>
            <div class="desk-v1-how-field-label">Budget (optional)</div>
            <div class="desk-v1-how-budget-toggle" role="group" aria-label="Budget">
              <button type="button" data-how-budget-btn="none" aria-pressed="${how.budget.source === 'none'}">None</button>
              <button type="button" data-how-budget-btn="own" aria-pressed="${how.budget.source !== 'none'}">Set an amount</button>
            </div>
            ${how.budget.source !== 'none' ? `<div class="desk-v1-rules-inlinerow">$<input type="number" min="0" class="desk-v1-rules-numinput" data-how-budget-amount value="${esc(how.budget.amount || 0)}"></div>` : ''}
            <div class="desk-v1-rules-hint" data-how-budget-hint>${esc(_budgetHint(how))}</div>
          </div>`;
  }

  function deskV1RenderHow(el, params) {
    const camp = _campaign(params.campaignId);
    if (!camp) { el.innerHTML = '<div class="desk-v1-stub-inline">Campaign not found.</div>'; return; }
    camp.how = camp.how || { strategy: '', angle: '', never_claim: '', agent: null, budget: { source: 'none' } };
    const how = camp.how;
    how.budget = how.budget || { source: 'none' };
    if (how.budget.source === 'project') how.budget.source = 'own';
    const project = _project(camp.projectId);

    const projectEditable = camp.state === 'draft' || camp.state === 'proposed';

    el.innerHTML = `
      <div class="desk-v1-how">
        <div class="desk-v1-how-scroll">
          <div class="desk-v1-how-card" data-how-frame>
            <div class="desk-v1-how-card-title">Campaign</div>
            <div data-how-project-host></div>
            ${projectEditable ? '' : `<div class="desk-v1-rules-hint" data-how-project-ro>Project: ${esc(project ? project.name : 'none')}</div>`}
            <div class="desk-v1-rules-group" data-how-agent-group>
              <div class="desk-v1-rules-group-title">Agent</div>
              <select class="desk-v1-goal-select" data-how-agent aria-label="Agent" disabled></select>
              <div class="desk-v1-rules-hint" data-how-agent-hint></div>
            </div>
          </div>
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
          ${_limitsHTML(camp)}
        </div>
        <div class="desk-v1-how-suggest">
          ${project
            ? `<button type="button" class="desk-v1-how-suggest-btn" data-how-suggest>${esc(SUGGEST_TEXT)}</button>
          <div class="desk-v1-rules-hint">Writes draft suggestions into What, When and Where — nothing is committed until you accept it there.</div>`
            : '' /* MC-977 S-2: the "No agent yet" note lives once, in the campaign thread head (desk-v1-campaign.js) */}
        </div>
      </div>`;

    // The project select mounts into its own host above the agent
    // picker; a campaign that has started shows the read-only line instead.
    if (projectEditable && typeof window.deskV1MountProjectField === 'function') {
      window.deskV1MountProjectField(el.querySelector('[data-how-project-host]'), camp);
    }
    _bind(el, camp, project);
    _paintAgent(el, camp, project);
  }

  // Last successful choices per project id, so a re-render (every budget
  // toggle rebuilds this panel) paints the select at once instead of flashing
  // "Loading agents…" while the roster is fetched again.
  const _choicesCache = {};

  // R2-18 Agent picker: options = the agents hired on the selected project's
  // floor (`DeskV1Kit.projectAgentChoices`, never a hardcoded list), selected =
  // the campaign's own `how.agent`, else the project's desk agent (labelled
  // "project default"). Last option `+ Create new agent` hands off to Claydo's
  // existing "Create an agent character" flow — nothing here creates one.
  // Disabled, with a hint, until a project is picked.
  function _paintAgent(el, camp, project) {
    const sel = el.querySelector('[data-how-agent]');
    const hint = el.querySelector('[data-how-agent-hint]');
    if (!sel) return;
    if (!project) {
      sel.innerHTML = '<option value="" selected disabled>Pick a project first</option>';
      sel.disabled = true;
      hint.textContent = 'The agents you can choose are the ones hired on the project.';
      return;
    }
    const fill = (choices) => {
      if (!sel.isConnected) return;
      const def = (project.presence || {}).desk_agent || null;
      const cur = (camp.how && camp.how.agent) || def;
      const known = choices.some((c) => c.ref === cur);
      sel.innerHTML =
        (known ? '' : '<option value="" selected disabled>Pick an agent</option>') +
        choices.map((c) => `<option value="${esc(c.ref)}" ${c.ref === cur ? 'selected' : ''}>${esc(c.name)}${c.ref === def ? ' (project default)' : ''}</option>`).join('') +
        '<option value="__create__">+ Create new agent</option>';
      sel.disabled = false;
      hint.textContent = choices.length
        ? `Hired on ${project.name}. Plans and drafts for this campaign only.`
        : `No agents hired on ${project.name} yet — create one, or hire one from the Floor.`;
    };
    if (_choicesCache[project.id]) fill(_choicesCache[project.id]);
    else { sel.innerHTML = '<option value="" selected disabled>Loading agents…</option>'; sel.disabled = true; hint.textContent = ''; }
    DeskV1Kit.projectAgentChoices(project).then((choices) => { _choicesCache[project.id] = choices; fill(choices); });

    sel.onchange = () => {
      if (sel.value === '__create__') { _createAgent(); _paintAgent(el, camp, project); return; }
      const nextRef = sel.value || null;
      const prevRef = (camp.how && camp.how.agent) || null;
      if (nextRef === prevRef) return;
      const picked = (_choicesCache[project.id] || []).find((c) => c.ref === nextRef);
      DeskV1Kit.commandBus.run({
        label: `Set campaign agent to ${picked ? picked.name : nextRef}`,
        do: () => { camp.how.agent = nextRef; if (typeof window.deskV1Render === 'function') window.deskV1Render(); },
        undo: () => { camp.how.agent = prevRef; if (typeof window.deskV1Render === 'function') window.deskV1Render(); },
      });
    };
  }

  // Same hand-off conversation.js's `_createNewPersona` uses (that helper is
  // module-private, so the four lines are repeated here): open Claydo in
  // "Create an agent character" mode. Saving is gated on the human's passcode
  // in claydo.js — only the user's own click ever creates an agent.
  function _createAgent() {
    const toChar = () => { if (typeof window.setClaydoMode === 'function') window.setClaydoMode('character'); };
    try {
      if (typeof window.openClaydo === 'function') Promise.resolve(window.openClaydo()).then(toChar).catch(toChar);
      else toChar();
    } catch (e) { /* no Claydo on this surface — nothing to open */ }
  }

  // Apply a limit edit. `get`/`set` read and write the one field; after `set`
  // a started campaign with an approval on file asks first if the edit WIDENS
  // it (the rule Launch's "Awaiting approval" applies, surfaced where the edit
  // happens). Cancel puts the old value back. `repaint` re-reads the model.
  function _applyLimit(camp, label, get, set, next, repaint) {
    const prev = get();
    set(next);
    const bounds = window.deskV1CampaignBounds;
    const live = camp.state !== 'draft' && camp.state !== 'proposed' && camp.approval && camp.approval.bounds && typeof bounds === 'function';
    if (!live || !DeskV1Kit.boundsWiden(camp.approval.bounds, bounds(camp))) { repaint(); return; }
    DeskV1Kit.openConfirmSheet({
      title: `This widens what “${(camp.plan && camp.plan.title) || 'the campaign'}” can do`,
      body: `${label} goes beyond what was approved. It needs your approval again on Launch before it takes effect.`,
      note: 'An authorized user must confirm. Continue?',
      onConfirm: repaint,
      onDecline: () => { set(prev); repaint(); },
    });
  }

  function _bind(el, camp, project) {
    const how = camp.how;

    const stratEl = el.querySelector('[data-how-strategy]');
    if (stratEl) stratEl.addEventListener('change', () => { how.strategy = stratEl.value.trim(); });
    const angleEl = el.querySelector('[data-how-angle]');
    if (angleEl) angleEl.addEventListener('change', () => { how.angle = angleEl.value.trim(); });
    const neverClaimEl = el.querySelector('[data-how-never-claim]');
    if (neverClaimEl) neverClaimEl.addEventListener('change', () => { how.never_claim = neverClaimEl.value.trim(); });

    // Every limit edit goes through _applyLimit (see above).
    const repaint = () => deskV1RenderHow(el, { campaignId: camp.id });
    el.querySelectorAll('[data-how-budget-btn]').forEach((btn) => {
      btn.onclick = () => {
        const source = btn.getAttribute('data-how-budget-btn');
        _applyLimit(camp, 'Budget', () => ({ source: how.budget.source, amount: how.budget.amount }),
          (v) => { how.budget.source = v.source; how.budget.amount = v.amount; },
          { source, amount: source === 'none' ? 0 : (how.budget.amount || 0) }, repaint);
      };
    });
    const amountEl = el.querySelector('[data-how-budget-amount]');
    if (amountEl) amountEl.addEventListener('change', () => {
      _applyLimit(camp, 'Budget', () => how.budget.amount, (v) => { how.budget.amount = v; },
        parseInt(amountEl.value, 10) || 0, () => {
          const hint = el.querySelector('[data-how-budget-hint]');
          if (hint) hint.textContent = _budgetHint(how);
          amountEl.value = String(how.budget.amount || 0);
        });
    });
    el.querySelectorAll('[data-how-limit]').forEach((inp) => {
      const key = inp.getAttribute('data-how-limit');
      inp.addEventListener('change', () => {
        const plan = camp.plan = camp.plan || {};
        const num = inp.value === '' ? null : (parseInt(inp.value, 10) || 0);
        const label = { per_week: 'Posts a week', min_gap_h: 'Minimum gap', end_date: 'End date', post_cap: 'Post cap' }[key];
        const holder = () => (key === 'per_week' || key === 'min_gap_h'
          ? (plan.cadence = plan.cadence || {}) : (plan.end = plan.end || {}));
        const field = key === 'end_date' ? 'date' : key;
        _applyLimit(camp, label, () => holder()[field], (v) => { holder()[field] = v; },
          key === 'end_date' ? (inp.value || null) : num, repaint);
      });
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

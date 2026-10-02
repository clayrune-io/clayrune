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

    // Not-started campaigns can change project; a started one only when it
    // has none (it was started before a project was required), then it locks.
    const projectEditable = camp.state === 'draft' || camp.state === 'proposed' || !camp.projectId;

    el.innerHTML = `
      <div class="desk-v1-how">
        <div class="desk-v1-how-scroll">
          <div class="desk-v1-how-card" data-how-frame>
            <div class="desk-v1-how-card-title">Campaign</div>
            <div data-how-project-host></div>
            ${projectEditable ? '' : `<div class="desk-v1-rules-hint" data-how-project-ro>Project: ${esc(project ? project.name : 'none')}</div>`}
            <div class="desk-v1-rules-group" data-how-agent-group>
              <div class="desk-v1-rules-group-title">Agent</div>
              <button type="button" class="desk-v1-agentpick-btn" data-how-agent data-value="" aria-haspopup="listbox" aria-expanded="false" aria-label="Agent" disabled></button>
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

  // R2-18 Agent picker, the ONE place a campaign's agent is chosen (Ron
  // 2026-10-02; the right-hand agent box only shows the result). Choices = the
  // agents hired on the selected project's floor (`DeskV1Kit.projectAgentChoices`,
  // never a hardcoded list); current = the campaign's own `how.agent`, else the
  // project's desk agent (tagged "(project default)"). The current agent is
  // ALWAYS in the list and on the button, even when the roster fetch has not
  // caught up with a hire — the old <select> went blank in that window. Each row
  // is figure + name + one-line role; the last two rows are `+ Hire an agent
  // onto <project>…` and `+ Create new agent` (which hands off to Claydo's
  // existing "Create an agent character" flow — nothing here creates one).
  // Disabled, with a hint, until a project is picked.
  function _currentAgent(camp, project) {
    const def = (project.presence || {}).desk_agent || null;
    const ref = (camp.how && camp.how.agent) || def;
    return { ref, def, rec: ref ? DeskV1Kit.resolveDeskAgent(ref) : null };
  }

  const _CARET = '<span class="desk-v1-agentpick-caret" aria-hidden="true">&#9662;</span>';
  function _pickerLabel(text) {
    return `<span class="desk-v1-agentpick-text"><span class="desk-v1-agentpick-name desk-v1-agentpick-empty">${esc(text)}</span></span>${_CARET}`;
  }
  function _pickerButtonHTML(cur) {
    if (!cur.rec || !cur.rec.name) return _pickerLabel('Pick an agent');
    return `${DeskV1Kit.agentFaceHTML(cur.rec.avatar, 28)}<span class="desk-v1-agentpick-text"><span class="desk-v1-agentpick-name">${esc(cur.rec.name)}${cur.ref === cur.def ? ' (project default)' : ''}</span>${cur.rec.role ? `<span class="desk-v1-agentpick-role">${esc(cur.rec.role)}</span>` : ''}</span>${_CARET}`;
  }

  function _paintAgent(el, camp, project) {
    const btn = el.querySelector('[data-how-agent]');
    const hint = el.querySelector('[data-how-agent-hint]');
    if (!btn) return;
    if (!project) {
      btn.innerHTML = _pickerLabel('Pick a project first');
      btn.disabled = true;
      hint.textContent = 'The agents you can choose are the ones hired on the project.';
      return;
    }
    const show = (cur) => {
      btn.dataset.value = (cur.rec && cur.rec.name) ? cur.ref : '';
      btn.innerHTML = _pickerButtonHTML(cur);
    };
    const fill = (choices) => {
      if (!btn.isConnected) return;
      show(_currentAgent(camp, project));
      btn.disabled = false;
      hint.textContent = choices.length
        ? `These are the agents hired on ${project.name}. To add another, choose “Hire an agent onto ${project.name}…”. Plans and drafts for this campaign only.`
        : `No agents are hired on ${project.name} yet. Choose “Hire an agent onto ${project.name}…”, or create a new one.`;
    };
    if (_choicesCache[project.id]) fill(_choicesCache[project.id]);
    else {
      const cur = _currentAgent(camp, project);
      if (cur.rec && cur.rec.name) show(cur);
      else { btn.dataset.value = ''; btn.innerHTML = _pickerLabel('Loading agents…'); }
      btn.disabled = true; hint.textContent = '';
    }
    DeskV1Kit.projectAgentChoices(project).then((choices) => { _choicesCache[project.id] = choices; fill(choices); });

    btn.onclick = () => {
      const cur = _currentAgent(camp, project);
      const list = (_choicesCache[project.id] || []).slice();
      // A hire's roster row can land after the pick: the chosen agent still shows.
      if (cur.rec && cur.rec.name && !list.some((c) => c.ref === cur.ref)) list.unshift({ ref: cur.ref, name: cur.rec.name, avatar: cur.rec.avatar, role: cur.rec.role });
      const items = list.map((c) => ({ id: c.ref, name: c.name, avatar: c.avatar, role: c.role, tag: c.ref === cur.def ? '(project default)' : '' }))
        .concat([
          { id: '__hire__', name: `Hire an agent onto ${project.name}…`, action: true },
          { id: '__create__', name: 'Create new agent', action: true },
        ]);
      DeskV1Kit.agentListPopover(btn, items, (id) => {
        if (id === '__create__') { _createAgent(); return; }
        if (id === '__hire__') { _hireMenu(camp, project, btn); return; }
        _setCampaignAgent(camp, id, list.find((c) => c.ref === id));
      }, { selectedId: cur.ref, label: 'Agent', title: 'Choose the agent for this campaign' });
    };
  }

  // The one write for the campaign's agent (the Agent picker above, and a hire
  // through "+ Hire an agent…"): sets the campaign's own `how.agent`, saved
  // through the Store write / PATCH, with Undo.
  function _setCampaignAgent(camp, nextRef, picked) {
    const prevRef = (camp.how && camp.how.agent) || null;
    if (nextRef === prevRef) return;
    const render = () => { if (typeof window.deskV1Render === 'function') window.deskV1Render(); };
    window.DeskV1Store.write({
      label: `Set campaign agent to ${picked ? picked.name : nextRef}`,
      apply: () => { camp.how.agent = nextRef; render(); },
      unapply: () => { camp.how.agent = prevRef; },
      repaint: render,
      request: () => window.deskV1PatchCampaign(camp, ['plan', 'how']),
      undoRequest: () => window.deskV1PatchCampaign(camp, ['plan', 'how']),
    });
  }

  // "+ Hire an agent onto <project>…": the installed agents NOT hired on this
  // project (GET /api/characters?project_id=, minus the project's roster), as the
  // same figure list the Agent picker uses, under `anchor`. Picking one is the
  // user's own click; it hires through floor.js's roster/hire route (the one
  // drag-to-hire uses), refreshes the choices and selects the agent for THIS
  // campaign. Nothing here hires on its own.
  async function _hireMenu(camp, project, anchor) {
    let hired = [], installed = [];
    try {
      hired = await DeskV1Kit.projectAgentChoices(project);
      const r = await fetch('/api/characters?project_id=' + encodeURIComponent(project.id));
      installed = await r.json();
    } catch (e) { DeskV1Kit.toast('Could not load the agents: ' + (e && e.message ? e.message : e)); return; }
    const have = new Set(hired.map((c) => c.ref));
    const cands = (Array.isArray(installed) ? installed : [])
      .map((c) => ({ ref: `${c.scope || 'global'}:${c.name}`, scope: c.scope || 'global', name: c.name, label: c.agent_name || c.display_name || c.name, avatar: c.avatar || '', role: DeskV1Kit.agentRole(c.description), shadowed: !!c.shadowed_by_project }))
      .filter((c) => !c.shadowed && !have.has(c.ref));
    if (!cands.length) {
      DeskV1Kit.toast(`Every installed agent is already hired on ${project.name}. Create a new one from the Agent list.`);
      return;
    }
    DeskV1Kit.agentListPopover(anchor, cands.map((c) => ({ id: c.ref, name: c.label, avatar: c.avatar, role: c.role })), async (ref) => {
      const c = cands.find((x) => x.ref === ref);
      if (!c || typeof window.floorHireQuiet !== 'function') return;
      const data = await window.floorHireQuiet(c.scope, c.name, project.id, c.label);
      if (!data) return;
      delete _choicesCache[project.id];
      _setCampaignAgent(camp, c.ref, { name: c.label });
    }, { label: 'Hire an agent', title: `Hire an agent onto ${project.name}` });
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

  // R1-W S2: a Brief edit saved to the server (live only; the demo store has
  // none). The whole `plan` and `how` go up (the server replaces each key). A
  // refusal puts the old value back through `rollback` and says why. A widening
  // is saved like any other edit: the server then reports the campaign as
  // awaiting approval until a human approves it on Launch.
  function _persist(camp, label, rollback, repaint) {
    if (!window.DeskV1Store.live()) return;
    window.deskV1PatchCampaign(camp, ['plan', 'how']).catch((e) => {
      if (rollback) rollback();
      DeskV1Kit.toast(`${label} was not saved: ${e && e.message ? e.message : e}`);
      if (repaint) repaint();
    });
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
    const keep = () => { repaint(); _persist(camp, label, () => set(prev), repaint); };
    if (!live || !DeskV1Kit.boundsWiden(camp.approval.bounds, bounds(camp))) { keep(); return; }
    DeskV1Kit.openConfirmSheet({
      title: `This widens what “${(camp.plan && camp.plan.title) || 'the campaign'}” can do`,
      body: `${label} goes beyond what was approved. It needs your approval again on Launch before it takes effect.`,
      note: 'An authorized user must confirm. Continue?',
      onConfirm: keep,
      onDecline: () => { set(prev); repaint(); },
    });
  }

  function _bind(el, camp, project) {
    const how = camp.how;

    const stratEl = el.querySelector('[data-how-strategy]');
    if (stratEl) stratEl.addEventListener('change', () => { const prev = how.strategy; how.strategy = stratEl.value.trim(); _persist(camp, 'Strategy', () => { how.strategy = prev; stratEl.value = prev || ''; }); });
    const angleEl = el.querySelector('[data-how-angle]');
    if (angleEl) angleEl.addEventListener('change', () => { const prev = how.angle; how.angle = angleEl.value.trim(); _persist(camp, 'Angle', () => { how.angle = prev; angleEl.value = prev || ''; }); });
    const neverClaimEl = el.querySelector('[data-how-never-claim]');
    if (neverClaimEl) neverClaimEl.addEventListener('change', () => { const prev = how.never_claim; how.never_claim = neverClaimEl.value.trim(); _persist(camp, 'Never-claim list', () => { how.never_claim = prev; neverClaimEl.value = prev || ''; }); });

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

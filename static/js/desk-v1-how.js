// Desk v1 (MC-977) — R2-6: the ② How stop of the campaign map (docs/
// THE_DESK_V1_IA_REVISION_2.md §4.1 row "② How", §8 R2-6). Window-bridged
// module, no `import` (ground rule 1) — mounted by desk-v1-shell.js's
// `_renderCampaignPanel` straight into the tabbody slot as
// `window.deskV1RenderHow(el, params)`, same T0a contract every other
// dedicated stop renderer (results/calendar) already follows.
//
// Strategy/angle/budget live on `camp.how` (== `camp.plan.how`, the SAME
// object — desk-v1-fixtures.js's `_CAMP1_HOW` comment: "so every reader...
// sees the same data instead of drifting"). The agent picker writes
// `camp.how.agent`; `DeskV1Kit.deskAgentRef({project, campaign})` already
// prefers `presence.desk_agent` over it (R2-5), so this picker only changes
// what's shown once a campaign has no project-level pick to inherit — this
// ticket doesn't touch that precedence, only gives `how.agent` a way to get
// written at all.
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

  // Same one-shot-fetch-then-cache shape desk-v1-presence.js's own agent
  // picker uses (§5.3) — not exported from there, so re-fetched here rather
  // than reaching into that file's private closure.
  let _agentsList = null;
  function _fetchAgentsList(cb) {
    if (_agentsList) { cb(_agentsList); return; }
    fetch('/api/characters').then((r) => r.json()).then((list) => {
      _agentsList = (list || []).map((c) => ({
        ref: `${c.scope || 'global'}:${c.name}`,
        name: c.agent_name || c.display_name || c.name,
        avatar: c.avatar || '',
      }));
      cb(_agentsList);
    }).catch(() => { _agentsList = []; cb(_agentsList); });
  }

  function _budgetSourceLabel(source) {
    if (source === 'project') return 'Project earmark';
    if (source === 'own') return 'Own budget';
    return 'None';
  }

  function deskV1RenderHow(el, params) {
    const camp = _campaign(params.campaignId);
    if (!camp) { el.innerHTML = '<div class="desk-v1-stub-inline">Campaign not found.</div>'; return; }
    camp.how = camp.how || { strategy: '', angle: '', agent: null, budget: { source: 'none' } };
    const how = camp.how;
    how.budget = how.budget || { source: 'none' };
    const project = _project(camp.projectId);
    const agentRef = how.agent || (project && project.presence && project.presence.desk_agent) || null;
    const resolvedAgent = DeskV1Kit.resolveDeskAgent(agentRef);
    const agentLabel = resolvedAgent.name
      ? `${resolvedAgent.avatar ? resolvedAgent.avatar + ' ' : ''}${resolvedAgent.name}`
      : DeskV1Kit.UNRESOLVED_AGENT_LABEL;

    el.innerHTML = `
      <div class="desk-v1-how">
        <div class="desk-v1-rules-group">
          <div class="desk-v1-rules-group-title">Strategy</div>
          <textarea class="desk-v1-rules-textarea" data-how-strategy rows="3" placeholder="Who, what argument, why these channels, what never to claim.">${esc(how.strategy || '')}</textarea>
        </div>
        <div class="desk-v1-rules-group">
          <div class="desk-v1-rules-group-title">Angle</div>
          <textarea class="desk-v1-rules-textarea" data-how-angle rows="2" placeholder="The through-line.">${esc(how.angle || '')}</textarea>
        </div>
        <div class="desk-v1-rules-group">
          <div class="desk-v1-rules-group-title">Agent to plan with ${DeskV1Kit.infoIconHTML('agent')}</div>
          <button type="button" class="desk-v1-project-newcamp-btn" data-how-agent-trigger>${esc(agentLabel)}</button>
        </div>
        <div class="desk-v1-rules-group">
          <div class="desk-v1-rules-group-title">Budget</div>
          <div class="desk-v1-rules-inlinerow">
            <select class="desk-v1-rules-numinput desk-v1-how-budget-source" data-how-budget-source>
              <option value="none" ${how.budget.source === 'none' ? 'selected' : ''}>None</option>
              <option value="project" ${how.budget.source === 'project' ? 'selected' : ''}>Project earmark</option>
              <option value="own" ${how.budget.source === 'own' ? 'selected' : ''}>Own</option>
            </select>
            ${how.budget.source !== 'none' ? `$<input type="number" min="0" class="desk-v1-rules-numinput" data-how-budget-amount value="${esc(how.budget.amount || 0)}">` : ''}
          </div>
          <div class="desk-v1-rules-hint">${_budgetSourceLabel(how.budget.source)}${how.budget.source !== 'none' ? ` · $${esc(how.budget.amount || 0)}` : ''}. A raise (or None → Own → Project) widens what's approved; a lower amount does not restore an already-widened approval.</div>
        </div>
        <div class="desk-v1-rules-group">
          <div class="desk-v1-rules-group-title">Plan the rest</div>
          <button type="button" class="btn-secondary" data-how-suggest>${esc(SUGGEST_TEXT)}</button>
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

    const agentBtn = el.querySelector('[data-how-agent-trigger]');
    if (agentBtn) {
      _fetchAgentsList((list) => {
        DeskV1Kit.bindAddToTrigger(agentBtn, () => list.map((a) => ({ id: a.ref, label: `${a.avatar ? a.avatar + ' ' : ''}${a.name}` })), (ref) => {
          const picked = list.find((a) => a.ref === ref);
          how.agent = ref;
          DeskV1Kit.toast(`${picked ? picked.name : ref} now plans this campaign's How.`);
          deskV1RenderHow(el, { campaignId: camp.id });
        }, { noAppendNew: true });
      });
    }

    const sourceSel = el.querySelector('[data-how-budget-source]');
    if (sourceSel) sourceSel.onchange = () => {
      how.budget = how.budget || {};
      how.budget.source = sourceSel.value;
      if (sourceSel.value === 'none') how.budget.amount = 0;
      else how.budget.amount = how.budget.amount || 0;
      deskV1RenderHow(el, { campaignId: camp.id });
    };
    const amountEl = el.querySelector('[data-how-budget-amount]');
    if (amountEl) amountEl.addEventListener('change', () => {
      how.budget.amount = parseInt(amountEl.value, 10) || 0;
      const hint = el.querySelector('.desk-v1-rules-hint');
      if (hint) hint.textContent = `${_budgetSourceLabel(how.budget.source)} · $${how.budget.amount}. A raise (or None → Own → Project) widens what's approved; a lower amount does not restore an already-widened approval.`;
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

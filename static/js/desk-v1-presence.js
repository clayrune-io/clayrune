// Desk v1 (MC-977 IA revision) — IA3: Presence settings page
// (docs/THE_DESK_V1_IA_REVISION.md §1, §2.1, §5 row IA3). Window-bridged
// module, no `import` (ground rule 1, T0a). Replaces the IA1 stub in place —
// `desk-v1-shell.js`'s `presence` route resolves `window.deskV1RenderPresence`
// lazily, so no shell/router change is needed for this file to take over.
//
// Fixtures only (ground rule 3): every edit mutates `project.presence`
// directly, same "client-side over fixture data" contract every other v1
// surface uses. Widening (more accounts, a higher ceiling, a bigger budget —
// §2.1's own list) reuses DeskV1Kit.openConfirmSheet, the generic extraction
// of desk-v1-rules.js's `_openWideningConfirm`/preview-then-Apply pattern
// (kit.js's own comment on `openConfirmSheet`: "so a caller outside that
// file... can reuse the SAME in-page sheet"); narrowing applies at once and
// writes a line to this page's own log, per §2.1's "narrowing... clamps that
// campaign at once and logs it" (the clamp itself needs no extra code here —
// desk-v1-campaign.js's rule chip already recomputes its effective cadence
// from `project.presence.ceilings` fresh on every render via
// `DeskV1Kit.validatePlan`, so mutating the ceiling IS the clamp). Replies
// (§8 Q3: auto-answer out of v1) is display-only — no control, per the
// ticket brief.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function _fx() { return window.DeskV1Fixtures || {}; }
  function _projects() { return _fx().projects || []; }
  function _project(id) { return _projects().find((p) => p.id === id); }
  function _channels() { return _fx().channels || []; }
  function _channel(id) { return _channels().find((c) => c.id === id); }
  function _campaigns() { return _fx().campaigns || []; }
  function _campaignsForProject(pid) { return _campaigns().filter((c) => c.projectId === pid); }

  // K1: voice is a workspace asset — a project that ADDS a channel it hasn't
  // bound before gets the same default voice every other project binding
  // that channel already carries (§2.1's own default row), never a blank one.
  const DEFAULT_VOICE_BY_CHANNEL = {
    'ch-x-ron': 'Ron (first person)',
    'ch-li-page': 'Clayrune page',
    'ch-blog': 'Clayrune blog',
  };
  // One-line sample per voice (§5 IA3: "accounts + voice per account (with
  // one-line sample)") — a fixture-only illustration of what the voice
  // sounds like, not a real generation call (ground rule 3).
  const VOICE_SAMPLE = {
    'Ron (first person)': '“I just shipped restore points — undo any agent mistake in one click.”',
    'Clayrune page': '“Clayrune 2.1 ships restore points: roll back any agent session instantly.”',
    'Clayrune blog': 'A full write-up on how snapshot rollback works in Clayrune 2.1.',
  };
  function _voiceSample(voice) { return VOICE_SAMPLE[voice] || `${voice} — no sample yet.`; }

  function _repliesCopy(replies) {
    if (replies === 'drafts' || !replies) return 'Drafted for your review';
    return String(replies);
  }

  // Last mount, so an in-place edit (ceiling change, add account, measurement
  // add) can re-render the whole page the same way desk-v1-project.js's own
  // sections re-render themselves — simplest correct option for a
  // fixture-only settings page with no independent widget state to preserve.
  let _lastMount = null;
  function _rerender() { if (_lastMount) deskV1RenderPresence(_lastMount.el, _lastMount.params); }

  function _log(p, text) {
    p.presence._log = p.presence._log || [];
    p.presence._log.unshift({ text, at: Date.now() });
  }
  function _logHTML(p) {
    const log = p.presence._log || [];
    return log.length
      ? log.map((l) => `<div class="desk-v1-rules-hint" data-log-line>${esc(l.text)}</div>`).join('')
      : '<div class="desk-v1-stub-empty">No changes yet.</div>';
  }
  function _refreshLog(el, p) {
    const host = el.querySelector('#desk-v1-presence-log-list');
    if (host) host.innerHTML = _logHTML(p);
  }

  // ── Preview + Apply, widening confirms (same shape as desk-v1-rules.js's
  // `setPending`, generalized to one preview slot per row instead of one
  // shared slot per popover — several rows can each stage their own edit
  // here since they're independent fields, unlike the rules popover's
  // single mutually-exclusive group). `widening` routes through
  // DeskV1Kit.openConfirmSheet (the kit's own generic extraction of this
  // file's private `_openWideningConfirm`); narrowing applies immediately.
  function _stageChange(previewEl, mutate, effectText, widening, revert, project) {
    if (!previewEl) { mutate(); _log(project, effectText); DeskV1Kit.toast(effectText); _rerender(); return; }
    previewEl.hidden = false;
    previewEl.innerHTML = `
      <div class="desk-v1-rules-pop-previewtext">${esc(effectText)}</div>
      <div class="desk-v1-rules-pop-previewbtns">
        <button type="button" class="desk-v1-rules-pop-cancel" data-preview-cancel>Cancel</button>
        <button type="button" class="desk-v1-rules-pop-apply" data-preview-apply>Apply</button>
      </div>`;
    const clear = () => { previewEl.hidden = true; previewEl.innerHTML = ''; };
    previewEl.querySelector('[data-preview-cancel]').onclick = () => { revert(); clear(); };
    previewEl.querySelector('[data-preview-apply]').onclick = () => {
      const commit = () => { mutate(); clear(); _log(project, effectText); DeskV1Kit.toast(effectText); _rerender(); };
      if (!widening) { commit(); return; }
      DeskV1Kit.openConfirmSheet({
        title: `This widens what ${project.name || 'the project'} can publish`,
        body: effectText,
        note: 'An authorized user must confirm. Continue?',
        onConfirm: commit,
        onDecline: () => { revert(); clear(); },
      });
    };
  }

  // ── Agent of choice (R1-A/R2-5 follow-up, MC-977 IA revision 2 §5.3).
  // Presence is the spec-correct home for this pick (THE_DESK_V1_IA_REVISION_2
  // §233/299 — Presence settings / setup wizard Step 0, not the campaign map's
  // ② How stop, which is a per-campaign draft not project-level identity).
  // Roster comes from /api/characters, same one-shot-fetch shape kit.js's own
  // box uses; PATCHes `/api/desk/presence/<id>` (R1-A backend) AND mutates
  // `project.presence.desk_agent` directly so smokes stay deterministic
  // without a live server (ground rule 3 — fixtures over live calls).
  let _agentsList = null; // [{ref, name, avatar}], fetched once
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

  function _agentHTML(p) {
    const ref = DeskV1Kit.deskAgentRef({ project: p });
    const resolved = DeskV1Kit.resolveDeskAgent(ref);
    const label = resolved.name ? `${resolved.avatar ? resolved.avatar + ' ' : ''}${resolved.name}` : DeskV1Kit.UNRESOLVED_AGENT_LABEL;
    return `
      <div class="desk-v1-rules-group" id="desk-v1-presence-agent">
        <div class="desk-v1-rules-group-title">Agent of choice ${DeskV1Kit.infoIconHTML('agent')}</div>
        <button type="button" class="desk-v1-project-newcamp-btn" data-agent-trigger>${esc(label)}</button>
      </div>`;
  }

  function _bindAgent(el, p) {
    const btn = el.querySelector('[data-agent-trigger]');
    if (!btn) return;
    _fetchAgentsList((list) => {
      DeskV1Kit.bindAddToTrigger(btn, () => list.map((a) => ({ id: a.ref, label: `${a.avatar ? a.avatar + ' ' : ''}${a.name}` })), (ref) => {
        const picked = list.find((a) => a.ref === ref);
        const name = picked ? picked.name : ref;
        p.presence.desk_agent = ref;
        fetch(`/api/desk/presence/${encodeURIComponent(p.id)}`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ desk_agent: ref }),
        }).catch(() => { /* fixture already applied; presence route is best-effort in v1 */ });
        const effect = `${name} now plans for ${p.name}.`;
        _log(p, effect);
        DeskV1Kit.toast(effect);
        _rerender();
      }, { noAppendNew: true });
    });
  }

  // ── Accounts + voice ────────────────────────────────────────────────────
  function _accountRowHTML(p, a) {
    const ch = _channel(a.channel_id);
    const ceil = p.presence.ceilings[a.channel_id] || { per_week: 3, min_gap_h: 12 };
    return `
      <div class="desk-v1-presence-account-row" data-channel-id="${esc(a.channel_id)}">
        <div class="desk-v1-presence-account-head">
          ${DeskV1Kit.channelBadge(ch)}
          <span class="desk-v1-presence-account-voice">${esc(a.voice || '')}</span>
        </div>
        <div class="desk-v1-rules-hint">${esc(_voiceSample(a.voice))}</div>
        <div class="desk-v1-rules-inlinerow">Up to <input type="number" min="0" max="30" class="desk-v1-rules-numinput" data-ceiling-input="${esc(a.channel_id)}" value="${esc(ceil.per_week)}"> a week</div>
        <div class="desk-v1-rules-inlinerow">At least <input type="number" min="0" max="72" class="desk-v1-rules-numinput" data-gap-input="${esc(a.channel_id)}" value="${esc(ceil.min_gap_h)}"> hours apart</div>
        <div class="desk-v1-rules-pop-preview" data-ceiling-preview="${esc(a.channel_id)}" hidden></div>
      </div>`;
  }

  function _bindAccountRows(el, p) {
    el.querySelectorAll('.desk-v1-presence-account-row').forEach((row) => {
      const chId = row.dataset.channelId;
      const ch = _channel(chId);
      const previewEl = row.querySelector(`[data-ceiling-preview="${chId}"]`);
      const ceilInput = row.querySelector(`[data-ceiling-input="${chId}"]`);
      const gapInput = row.querySelector(`[data-gap-input="${chId}"]`);
      if (ceilInput) ceilInput.addEventListener('change', () => {
        const prev = (p.presence.ceilings[chId] || {}).per_week || 0;
        const next = parseInt(ceilInput.value, 10) || 0;
        if (next === prev) return;
        const widening = next > prev;
        const runningCount = _campaignsForProject(p.id)
          .filter((c) => (c.state === 'active' || c.state === 'proposed') && (c.plan.accounts || []).includes(chId)).length;
        const effect = `${ch ? ch.label : chId} ceiling ${widening ? 'raised' : 'lowered'} to ${next}/wk — was ${prev}` +
          (!widening && runningCount ? ` (clamps ${runningCount} running campaign${runningCount === 1 ? '' : 's'} using it)` : '') + '.';
        _stageChange(previewEl, () => {
          p.presence.ceilings[chId] = p.presence.ceilings[chId] || {};
          p.presence.ceilings[chId].per_week = next;
        }, effect, widening, () => { ceilInput.value = String(prev); }, p);
      });
      if (gapInput) gapInput.addEventListener('change', () => {
        const prev = (p.presence.ceilings[chId] || {}).min_gap_h || 0;
        const next = parseInt(gapInput.value, 10) || 0;
        if (next === prev) return;
        const widening = next < prev; // a tighter gap allows MORE frequent posting
        const effect = `${ch ? ch.label : chId} minimum spacing set to ${next}h — was ${prev}h.`;
        _stageChange(previewEl, () => {
          p.presence.ceilings[chId] = p.presence.ceilings[chId] || {};
          p.presence.ceilings[chId].min_gap_h = next;
        }, effect, widening, () => { gapInput.value = String(prev); }, p);
      });
    });
  }

  function _bindAddAccount(el, p) {
    const btn = el.querySelector('[data-addacct-trigger]');
    if (!btn) return;
    const unbound = _channels().filter((ch) => !p.presence.accounts.some((a) => a.channel_id === ch.id));
    DeskV1Kit.bindAddToTrigger(btn, () => unbound.map((ch) => ({ id: ch.id, label: ch.label })), (chId) => {
      const ch = _channel(chId);
      const voice = DEFAULT_VOICE_BY_CHANNEL[chId] || (ch ? ch.label : 'New voice');
      const effect = `${ch ? ch.label : chId} can now publish for ${p.name} — voice: ${voice}.`;
      DeskV1Kit.openConfirmSheet({
        title: `This widens what ${p.name} can publish`,
        body: effect,
        note: 'An authorized user must confirm. Continue?',
        onConfirm: () => {
          p.presence.accounts.push({ channel_id: chId, voice });
          p.presence.ceilings[chId] = p.presence.ceilings[chId] || { per_week: 3, min_gap_h: 12 };
          _log(p, effect);
          DeskV1Kit.toast(effect);
          _rerender();
        },
      });
    }, { noAppendNew: true });
  }

  // ── Audience / strategy (plain text, immediate apply on change — neither
  // widens authority, so no confirm sheet). ──────────────────────────────
  function _bindAudienceStrategy(el, p) {
    const aInput = el.querySelector('[data-audience-input]');
    if (aInput) aInput.addEventListener('change', () => {
      const next = aInput.value.trim();
      if (next === (p.presence.audience || '')) return;
      p.presence.audience = next;
      _log(p, 'Audience updated.');
      _refreshLog(el, p);
    });
    const sInput = el.querySelector('[data-strategy-input]');
    if (sInput) sInput.addEventListener('change', () => {
      const next = sInput.value.trim();
      if (next === (p.presence.strategy || '')) return;
      p.presence.strategy = next;
      _log(p, 'Strategy updated.');
      _refreshLog(el, p);
    });
  }

  // ── Presence budget (§2.1: bigger budget widens; renamed from
  // `presence.production` to `presence.budget` and `per_period` to `amount`
  // — IA revision 2 §5.3/§8 R2-1: `amount` covers production AND publishing
  // for the period, `per_job`/`kinds` stay safety caps, not money pools). ──
  function _budgetHTML(p) {
    const budget = p.presence.budget = p.presence.budget || { per_job: 0, amount: 0, period: 'month', kinds: [] };
    return `
      <div class="desk-v1-rules-inlinerow">$<input type="number" min="0" class="desk-v1-rules-numinput" data-budget-perjob-input value="${esc(budget.per_job || 0)}"> per job</div>
      <div class="desk-v1-rules-inlinerow">$<input type="number" min="0" class="desk-v1-rules-numinput" data-budget-perperiod-input value="${esc(budget.amount || 0)}"> per ${esc(budget.period || 'month')}</div>
      <div class="desk-v1-rules-hint">${budget.kinds && budget.kinds.length ? 'Allowed: ' + esc(budget.kinds.join(', ')) : 'No video generation allowed yet.'}</div>
      <div class="desk-v1-rules-pop-preview" data-budget-preview hidden></div>`;
  }
  function _bindBudget(el, p) {
    const budget = p.presence.budget;
    const previewEl = el.querySelector('[data-budget-preview]');
    const perJob = el.querySelector('[data-budget-perjob-input]');
    const perPeriod = el.querySelector('[data-budget-perperiod-input]');
    if (perJob) perJob.addEventListener('change', () => {
      const prev = budget.per_job || 0;
      const next = parseFloat(perJob.value) || 0;
      if (next === prev) return;
      const widening = next > prev;
      const effect = `Per-job production budget set to $${next} — was $${prev}.`;
      _stageChange(previewEl, () => { budget.per_job = next; }, effect, widening, () => { perJob.value = String(prev); }, p);
    });
    if (perPeriod) perPeriod.addEventListener('change', () => {
      const prev = budget.amount || 0;
      const next = parseFloat(perPeriod.value) || 0;
      if (next === prev) return;
      const widening = next > prev;
      const effect = `Production budget set to $${next} per ${budget.period || 'month'} — was $${prev}.`;
      _stageChange(previewEl, () => { budget.amount = next; }, effect, widening, () => { perPeriod.value = String(prev); }, p);
    });
  }

  // ── Measurement (adding a measurement source doesn't widen authority — no
  // confirm sheet, applies at once). ──────────────────────────────────────
  function _measurementHTML(p) {
    const list = p.presence.measurement || [];
    return `
      <div id="desk-v1-presence-measurement-list">${list.length
        ? list.map((m) => `<div class="desk-v1-rules-hint">${esc(m.event)} — ${esc(m.source)}</div>`).join('')
        : '<div class="desk-v1-stub-empty">None yet — goal shows ⚠ not tracked yet.</div>'}</div>
      <div class="desk-v1-rules-inlinerow">
        <input type="text" class="desk-v1-rules-textinput" data-measurement-event placeholder="Conversion event">
        <input type="text" class="desk-v1-rules-textinput" data-measurement-source placeholder="Source">
        <button type="button" class="btn-secondary" data-measurement-add>+ Add</button>
      </div>`;
  }
  function _bindMeasurement(el, p) {
    const btn = el.querySelector('[data-measurement-add]');
    if (!btn) return;
    btn.onclick = () => {
      const eIn = el.querySelector('[data-measurement-event]');
      const sIn = el.querySelector('[data-measurement-source]');
      const event = (eIn.value || '').trim();
      const source = (sIn.value || '').trim();
      if (!event) return;
      p.presence.measurement = p.presence.measurement || [];
      p.presence.measurement.push({ event, source });
      _log(p, `Measurement added: ${event}${source ? ' via ' + source : ''}.`);
      _rerender();
    };
  }

  function deskV1RenderPresence(el, params) {
    const projectId = (params || {}).projectId;
    _lastMount = { el, params };
    const p = _project(projectId);
    if (!p) { el.innerHTML = '<div class="desk-v1-stub"><div class="desk-v1-stub-body">Project not found.</div></div>'; return; }
    p.presence = p.presence || {};
    p.presence.accounts = p.presence.accounts || [];
    p.presence.ceilings = p.presence.ceilings || {};

    const accountsHTML = p.presence.accounts.map((a) => _accountRowHTML(p, a)).join('') ||
      '<div class="desk-v1-stub-empty">No accounts bound yet.</div>';
    const unbound = _channels().filter((ch) => !p.presence.accounts.some((a) => a.channel_id === ch.id));

    el.innerHTML = `
      <div class="desk-v1-presence">
        ${_agentHTML(p)}
        <div class="desk-v1-rules-group" id="desk-v1-presence-accounts">
          <div class="desk-v1-rules-group-title">Accounts + voice ${DeskV1Kit.infoIconHTML('accounts')}</div>
          <div id="desk-v1-presence-accounts-list">${accountsHTML}</div>
          ${unbound.length
            ? `<button type="button" class="desk-v1-project-newcamp-btn" data-addacct-trigger>+ Add account</button>`
            : '<div class="desk-v1-stub-empty">Every workspace account is already connected.</div>'}
        </div>
        <div class="desk-v1-rules-group">
          <div class="desk-v1-rules-group-title">Audience</div>
          <textarea class="desk-v1-rules-textarea" data-audience-input rows="2">${esc(p.presence.audience || '')}</textarea>
        </div>
        <div class="desk-v1-rules-group">
          <div class="desk-v1-rules-group-title">Strategy</div>
          <textarea class="desk-v1-rules-textarea" data-strategy-input rows="3">${esc(p.presence.strategy || '')}</textarea>
        </div>
        <div class="desk-v1-rules-group">
          <div class="desk-v1-rules-group-title">Replies</div>
          <div class="desk-v1-rules-hint">${esc(_repliesCopy(p.presence.replies))} — automated replies aren't available in v1.</div>
        </div>
        <div class="desk-v1-rules-group">
          <div class="desk-v1-rules-group-title">Production budget</div>
          ${_budgetHTML(p)}
        </div>
        <div class="desk-v1-rules-group">
          <div class="desk-v1-rules-group-title">Measurement</div>
          ${_measurementHTML(p)}
        </div>
        <div class="desk-v1-rules-group" id="desk-v1-presence-log">
          <div class="desk-v1-rules-group-title">Recent changes</div>
          <div id="desk-v1-presence-log-list">${_logHTML(p)}</div>
        </div>
      </div>`;

    DeskV1Kit.bindInfoIcons(el, {
      accounts: 'Which workspace accounts this project may publish through, and the voice each carries for this project.',
      agent: 'Who plans and drafts for this project — the writer dispatched off signals and triage picks.',
    });
    _bindAgent(el, p);
    _bindAccountRows(el, p);
    _bindAddAccount(el, p);
    _bindAudienceStrategy(el, p);
    _bindBudget(el, p);
    _bindMeasurement(el, p);
  }

  window.deskV1RenderPresence = deskV1RenderPresence;
})();

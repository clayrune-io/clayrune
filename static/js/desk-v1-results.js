// Desk v1 (MC-977) — R2-4 (IA revision 2 §4.1 row "① Goal", §8): the
// measurable goal editor + effectiveness panel that IS the ① stop's tab
// body. Absorbs the old T7 Results tab whole (§4.1 Absorbs column) — this
// rewrite REPLACES that file's content, not extends it: Posy's read +
// proposed experiment, the per-version "what went out" breakdown, the full
// cost table and the diagnostics line are all dropped, because none of them
// are named in the R2-4 row's scope (goal editor: metric/target/baseline/
// horizon/deadline/source incl. manual entry; effectiveness panel: progress/
// pace/cost per outcome/per-term rows). Reported as a spec gap in the R2-4
// ticket report rather than silently kept or silently deleted.
//
// Window-bridged module, no `import` (ground rule 1). The outer
// `.desk-v1-results` wrapper class is kept even though the internals are new
// — other smokes (`desk-v1-campaign.mjs`, `desk-v1-exit.mjs`) wait on it as
// the panel's mount signal, not on anything this ticket touched.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function _fx() { return window.DeskV1Fixtures || {}; }
  function _campaign(id) { return (_fx().campaigns || []).find((c) => c.id === id) || null; }
  function _money(n) { return n == null ? 'n/a' : '$' + (Math.round(n * 100) / 100).toFixed(2); }

  // ── goal shape (IA revision 2 §5.1's `camp.goal`, additive onto the old
  // `plan.goal` the summary bar still reads — R2-4 is the first ticket to
  // actually read/write it): {current, metric, target, baseline, unit,
  // horizon:'short'|'long', deadline, source: ''|'manual', entries:
  // [{at,value}]}. `goal.current` stays derived except for `manual` sources,
  // which store dated entries and derive `current` as the latest one (§4.1
  // bullet list, last line). ─────────────────────────────────────────────
  const HORIZONS = { short: 'Short — one term', long: 'Long — several terms' };

  function _goalOf(camp) { camp.goal = camp.goal || {}; return camp.goal; }

  function _latestEntry(goal) {
    const entries = goal.entries || [];
    if (!entries.length) return null;
    return entries.reduce((a, b) => (new Date(b.at).getTime() > new Date(a.at).getTime() ? b : a));
  }
  function _currentValue(goal) {
    if (goal.source === 'manual') {
      const last = _latestEntry(goal);
      return last ? last.value : (goal.current || 0);
    }
    return goal.current || 0;
  }

  function _fmtDateInput(iso) { return iso ? String(iso).slice(0, 10) : ''; }
  function _todayISODate() { return new Date().toISOString().slice(0, 10); }

  // ── Goal editor (§4.1 row: "metric, target number, baseline, unit,
  // horizon, deadline, measurement source"). Adding a measurement source (or
  // a manual entry) doesn't widen any launch approval — same "applies at
  // once, no confirm sheet" precedent as Presence's own measurement list
  // (`desk-v1-presence.js` `_measurementHTML`). ────────────────────────────
  function _editorHTML(goal) {
    return `
      <div class="desk-v1-goal-editor">
        <div class="desk-v1-goal-row">
          <label class="desk-v1-goal-label">Metric</label>
          <input type="text" class="desk-v1-rules-textinput" data-goal-field="metric" value="${esc(goal.metric || '')}" placeholder="e.g. tester signups">
        </div>
        <div class="desk-v1-goal-row">
          <label class="desk-v1-goal-label">Target</label>
          <input type="number" class="desk-v1-rules-numinput" data-goal-field="target" value="${esc(goal.target != null ? goal.target : '')}">
          <label class="desk-v1-goal-label">Baseline</label>
          <input type="number" class="desk-v1-rules-numinput" data-goal-field="baseline" value="${esc(goal.baseline != null ? goal.baseline : '')}">
          <label class="desk-v1-goal-label">Unit</label>
          <input type="text" class="desk-v1-rules-textinput desk-v1-goal-unitinput" data-goal-field="unit" value="${esc(goal.unit || '')}" placeholder="signups">
        </div>
        <div class="desk-v1-goal-row">
          <label class="desk-v1-goal-label">Horizon</label>
          <select class="desk-v1-goal-select" data-goal-field="horizon">
            ${Object.keys(HORIZONS).map((h) => `<option value="${esc(h)}" ${goal.horizon === h ? 'selected' : ''}>${esc(HORIZONS[h])}</option>`).join('')}
          </select>
          <label class="desk-v1-goal-label">Deadline</label>
          <input type="date" class="desk-v1-goal-dateinput" data-goal-field="deadline" value="${esc(_fmtDateInput(goal.deadline))}">
        </div>
        <div class="desk-v1-goal-row">
          <label class="desk-v1-goal-label">Source</label>
          <select class="desk-v1-goal-select" data-goal-field="source">
            <option value="" ${!goal.source ? 'selected' : ''}>— none —</option>
            <option value="manual" ${goal.source === 'manual' ? 'selected' : ''}>Manual entry</option>
          </select>
        </div>
        ${goal.source === 'manual' ? _manualEntryHTML(goal) : ''}
        ${!goal.source ? '<div class="desk-v1-goal-editor-hint">⚠ Not measured — pick a source to start tracking progress.</div>' : ''}
      </div>`;
  }

  // §9 Q1 (binding, kit.js `_goalMissing`): manual is the one source that
  // exists today (R1-E's automatic feed read is `source:'feed'`, not built).
  function _manualEntryHTML(goal) {
    const entries = (goal.entries || []).slice().sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime());
    return `
      <div class="desk-v1-goal-manual">
        <div class="desk-v1-goal-manual-list">${entries.length
          ? entries.slice(0, 5).map((e) => `<div class="desk-v1-rules-hint">${esc(_fmtDateInput(e.at))} — ${esc(e.value)}</div>`).join('')
          : '<div class="desk-v1-stub-empty">No entries yet.</div>'}</div>
        <div class="desk-v1-rules-inlinerow">
          <input type="date" class="desk-v1-goal-dateinput" data-manual-entry-date value="${esc(_todayISODate())}">
          <input type="number" class="desk-v1-rules-numinput" data-manual-entry-value placeholder="Value">
          <button type="button" class="btn-secondary" data-manual-entry-add>+ Add entry</button>
        </div>
      </div>`;
  }

  // ── Effectiveness panel (§4.1 "shown on ① once Active"; bullet list):
  // progress = current/target, pace = progress ÷ fraction of horizon
  // elapsed -> On track (>=0.9) / Behind (<0.9) / Ahead (>1.2); cost per
  // outcome = spend to date ÷ current, only when spend > 0; per-term
  // breakdown for `long` horizons; an untracked source shows
  // `⚠ Not measured: add a source`, never a `0 of 30`. ─────────────────────
  function _paceState(pace) {
    if (pace == null) return null;
    if (pace > 1.2) return { key: 'ahead', glyph: '▲', word: 'Ahead' };
    if (pace >= 0.9) return { key: 'on_track', glyph: '●', word: 'On track' };
    return { key: 'behind', glyph: '▼', word: 'Behind' };
  }
  function _fractionElapsed(term) {
    if (!term || !term.starts || !term.ends) return null;
    const start = new Date(term.starts).getTime();
    const end = new Date(term.ends).getTime();
    if (!(end > start)) return null;
    return Math.max(0, Math.min(1, (Date.now() - start) / (end - start)));
  }
  // Cost per outcome, same MET-02 honesty rule the old §7 table used: hidden
  // (not a fabricated partial number) unless every cost category is
  // measured. Reads the same `RESULTS` fixture keyed by campaignId that
  // carried the old Costs table — spend data has no other home yet.
  function _costPerOutcome(campaignId, current) {
    const results = _fx().results;
    if (!results || results.campaignId !== campaignId || !results.costs || !(current > 0)) return null;
    const costs = results.costs;
    const keys = Object.keys(costs);
    if (!keys.every((k) => costs[k] != null)) return null;
    const spend = keys.reduce((s, k) => s + (costs[k] || 0), 0);
    return spend > 0 ? spend / current : null;
  }
  function _termProgress(goal, term) {
    const target = term.target != null ? term.target : goal.target;
    const current = term.current != null ? term.current : _currentValue(goal);
    return { index: term.index, current, target };
  }

  function _effectivenessHTML(camp, goal) {
    if (!goal.source) {
      return `<div class="desk-v1-goal-effectiveness">
        <div class="desk-v1-goal-untracked">⚠ Not measured: add a source</div>
      </div>`;
    }
    const current = _currentValue(goal);
    const target = goal.target;
    const progressPct = target ? Math.max(0, Math.min(100, Math.round((current / target) * 100))) : 0;
    const progressRatio = target ? current / target : 0;
    const elapsed = _fractionElapsed(camp.term);
    const pace = (elapsed != null && elapsed > 0) ? progressRatio / elapsed : null;
    const paceSt = _paceState(pace);
    const costPerOutcome = _costPerOutcome(camp.id, current);

    const terms = (camp.terms && camp.terms.length) ? camp.terms : (camp.term ? [camp.term] : []);
    const termRowsHTML = (goal.horizon === 'long' && terms.length)
      ? `<div class="desk-v1-goal-terms">${terms.map((t) => {
          const tp = _termProgress(goal, t);
          return `<div class="desk-v1-goal-term-row">Term ${esc(tp.index)}: ${esc(tp.current)} of ${esc(tp.target)}</div>`;
        }).join('')}</div>`
      : '';

    return `
      <div class="desk-v1-goal-effectiveness">
        <div class="desk-v1-goal-progress-top">
          <span class="desk-v1-goal-progress-number">${esc(current)}</span>
          <span class="desk-v1-goal-progress-target">of ${esc(target)} ${esc(goal.metric || '')}</span>
        </div>
        <div class="desk-v1-goal-progress-bar"><div class="desk-v1-goal-progress-fill" style="width:${progressPct}%"></div></div>
        ${paceSt ? `<div class="desk-v1-goal-pace" data-pace="${esc(paceSt.key)}"><span aria-hidden="true">${paceSt.glyph}</span> ${esc(paceSt.word)}</div>` : ''}
        ${costPerOutcome != null ? `<div class="desk-v1-goal-cost">Cost per outcome: ${esc(_money(costPerOutcome))}</div>` : ''}
        ${termRowsHTML}
      </div>`;
  }

  function _bindEditor(el, camp) {
    const goal = _goalOf(camp);
    el.querySelectorAll('[data-goal-field]').forEach((input) => {
      input.addEventListener('change', () => {
        const field = input.dataset.goalField;
        let val = input.value;
        if (input.type === 'number') val = (val === '' ? null : Number(val));
        else if (val === '') val = null;
        goal[field] = val;
        _renderAll(el, camp);
      });
    });
    const addBtn = el.querySelector('[data-manual-entry-add]');
    if (addBtn) addBtn.onclick = () => {
      const dateIn = el.querySelector('[data-manual-entry-date]');
      const valIn = el.querySelector('[data-manual-entry-value]');
      const at = dateIn.value || _todayISODate();
      const value = Number(valIn.value);
      if (!Number.isFinite(value)) return;
      goal.entries = goal.entries || [];
      goal.entries.push({ at, value });
      DeskV1Kit.toast(`Entry added: ${value} on ${at}`);
      _renderAll(el, camp);
    };
  }

  function _renderAll(el, camp) {
    const goal = _goalOf(camp);
    el.innerHTML = `
      <div class="desk-v1-results">
        <div class="desk-v1-goal">
          ${_editorHTML(goal)}
          ${camp.state === 'active' ? _effectivenessHTML(camp, goal) : ''}
        </div>
      </div>`;
    _bindEditor(el, camp);
  }

  function deskV1RenderResults(el, params) {
    const campaignId = (params || {}).campaignId;
    const camp = _campaign(campaignId);
    if (!camp) {
      el.innerHTML = '<div class="desk-v1-stub"><div class="desk-v1-stub-body">Campaign not found.</div></div>';
      return;
    }
    _renderAll(el, camp);
  }

  window.deskV1RenderResults = deskV1RenderResults;
})();

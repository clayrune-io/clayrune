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

  function _fx() { return window.DeskV1Store.state(); }
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
  // null = no data. A goal nobody has measured is not "0 of 30" (MET-01); a typed
  // 0 stays 0. The server derives the same number for live mode (M11).
  function _currentValue(goal) {
    if (goal.source === 'manual') {
      const last = _latestEntry(goal);
      return last ? last.value : (goal.current != null ? goal.current : null);
    }
    return goal.current != null ? goal.current : null;
  }

  function _fmtDateInput(iso) { return iso ? String(iso).slice(0, 10) : ''; }
  function _todayISODate() { return new Date().toISOString().slice(0, 10); }

  // ── Goal editor (§4.1 row: "metric, target number, baseline, unit,
  // horizon, deadline, measurement source"). Adding a measurement source (or
  // a manual entry) doesn't widen any launch approval — same "applies at
  // once, no confirm sheet" precedent as the measurement list that was
  // on the retired Presence screen. ────────────────────────────
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
  function _costPerOutcome(costs, current) {
    if (!costs || !(current > 0)) return null;
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

  // ── Where the effectiveness numbers come from (R1-W S3).
  //   desk_v1_live OFF  DEMO: derived here from the fixture campaign, exactly as
  //                     before (the fixture `results` rows still carry its costs).
  //   desk_v1_live ON   the SERVER's answer, `GET /api/desk/campaigns/<id>/results`
  //                     (M11): current, per-term readings and ledger spend are
  //                     derived there, null where nothing is measured. Until it
  //                     answers, or if it fails, the panel says so; it never
  //                     falls back to the goal object's own `current` (that is a
  //                     number the server no longer stores) or to the fixtures.
  // The editor above stays on `camp.goal`: those are the fields the user types.
  // campaignId -> { phase: 'loading'|'ready'|'error', data, error, writing? }.
  // `writing` marks the placeholder a goal edit leaves while its PATCH is in
  // flight: nothing may read M11 until the PATCH has landed, or it would read
  // the value the edit is replacing.
  const _live = new Map();
  const _seq = new Map();    // campaignId -> the newest read; an older answer is dropped

  // Reads M11. A panel that already shows an answer keeps showing it while the
  // newer one loads; the newest answer (or error) replaces it. Resolves once the
  // panel has been repainted.
  function _fetchLive(el, camp) {
    const seq = (_seq.get(camp.id) || 0) + 1;
    _seq.set(camp.id, seq);
    const prev = _live.get(camp.id);
    if (!prev || prev.phase !== 'ready') _live.set(camp.id, { phase: 'loading', data: null, error: null });
    let result;
    return (window.deskV1AfterCampaignSaved ? window.deskV1AfterCampaignSaved(camp.id) : Promise.resolve())
      .then(() => window.DeskV1Store.api('GET', '/api/desk/campaigns/' + encodeURIComponent(camp.id) + '/results'))
      .then((data) => { result = { phase: 'ready', data, error: null }; })
      .catch((e) => { result = { phase: 'error', data: null, error: e && e.message ? e.message : String(e) }; })
      .then(() => {
        if (_seq.get(camp.id) !== seq) return;
        _live.set(camp.id, result);
        if (el.isConnected) _renderAll(el, camp);
      });
  }

  // The panel's numbers as one shape, whichever mode: {status, source, current,
  // target, terms:[{index,current,target}], costs}. `status` is 'ready' unless a
  // live read is still loading or failed.
  function _viewOf(el, camp, goal) {
    if (!window.DeskV1Store.live()) {
      const results = _fx().results;
      const costs = results && results.campaignId === camp.id ? results.costs : null;
      const terms = (camp.terms && camp.terms.length) ? camp.terms : (camp.term ? [camp.term] : []);
      return { status: 'ready', source: goal.source, current: _currentValue(goal), target: goal.target,
        terms: terms.map((t) => _termProgress(goal, t)), costs };
    }
    let entry = _live.get(camp.id);
    if (!entry) { _fetchLive(el, camp); entry = _live.get(camp.id); }
    if (entry.phase !== 'ready') return { status: entry.phase, error: entry.error };
    const d = entry.data || {};
    const g = d.goal || {};
    return { status: 'ready', source: g.source, current: g.current != null ? g.current : null,
      target: g.target != null ? g.target : null, terms: d.terms || [], costs: d.costs };
  }

  function _termRowText(tp) {
    const has = tp.current != null;
    const of = tp.target != null;
    if (!has) return `Term ${tp.index}: no data${of ? ` (target ${tp.target})` : ''}`;
    return `Term ${tp.index}: ${tp.current}${of ? ` of ${tp.target}` : ''}`;
  }

  function _effectivenessHTML(el, camp, goal) {
    const view = _viewOf(el, camp, goal);
    if (view.status === 'loading') {
      return `<div class="desk-v1-goal-effectiveness"><div class="desk-v1-stub-empty desk-v1-goal-loading">Loading results…</div></div>`;
    }
    if (view.status === 'error') {
      return `<div class="desk-v1-goal-effectiveness">
        <div class="desk-v1-goal-untracked desk-v1-goal-loaderror">⚠ Could not load results: ${esc(view.error)}</div>
        <button type="button" class="btn-secondary" data-goal-retry>Try again</button>
      </div>`;
    }
    if (!view.source) {
      return `<div class="desk-v1-goal-effectiveness">
        <div class="desk-v1-goal-untracked">⚠ Not measured: add a source</div>
      </div>`;
    }
    const current = view.current;
    const target = view.target;
    const terms = (goal.horizon === 'long' && view.terms.length)
      ? `<div class="desk-v1-goal-terms">${view.terms.map((tp) => `<div class="desk-v1-goal-term-row">${esc(_termRowText(tp))}</div>`).join('')}</div>`
      : '';
    // A measured source with nothing recorded yet: say so, never "0 of 30".
    if (current == null) {
      return `<div class="desk-v1-goal-effectiveness">
        <div class="desk-v1-stub-empty desk-v1-goal-nodata">No data yet: add an entry to start tracking${target != null ? ` toward ${esc(target)} ${esc(goal.metric || '')}` : ''}.</div>
        ${terms}
      </div>`;
    }
    const progressPct = target ? Math.max(0, Math.min(100, Math.round((current / target) * 100))) : 0;
    const progressRatio = target ? current / target : 0;
    const elapsed = _fractionElapsed(camp.term);
    const pace = (elapsed != null && elapsed > 0) ? progressRatio / elapsed : null;
    const paceSt = _paceState(pace);
    const costPerOutcome = _costPerOutcome(view.costs, current);

    return `
      <div class="desk-v1-goal-effectiveness">
        <div class="desk-v1-goal-progress-top">
          <span class="desk-v1-goal-progress-number">${esc(current)}</span>
          <span class="desk-v1-goal-progress-target">of ${esc(target)} ${esc(goal.metric || '')}</span>
        </div>
        <div class="desk-v1-goal-progress-bar"><div class="desk-v1-goal-progress-fill" style="width:${progressPct}%"></div></div>
        ${paceSt ? `<div class="desk-v1-goal-pace" data-pace="${esc(paceSt.key)}"><span aria-hidden="true">${paceSt.glyph}</span> <span class="desk-v1-goal-pace-word">${esc(paceSt.word)}</span></div>` : ''}
        ${costPerOutcome != null ? `<div class="desk-v1-goal-cost">Cost per outcome: ${esc(_money(costPerOutcome))}</div>` : ''}
        ${terms}
      </div>`;
  }

  // One goal edit. `change(goal)` makes it, `revert(goal)` puts it back.
  //   desk_v1_live OFF  DEMO: made in place and repainted, nothing sent (as ever).
  //   desk_v1_live ON   through DeskV1Store.run: the whole goal is PATCHed (the
  //                     server keeps only the fields a client may write and
  //                     derives `current` itself), then M11 is read again. A
  //                     refusal reverts the edit and toasts the server's reason.
  //                     Until the PATCH lands the panel reads "Loading", never the
  //                     number the edit is about to replace.
  function _editGoal(el, camp, label, change, revert) {
    const goal = _goalOf(camp);
    const repaint = () => _renderAll(el, camp);
    if (!window.DeskV1Store.live()) { change(goal); repaint(); return; }
    let prevRead;
    // Only an active campaign shows the panel that reads M11; for any other the
    // placeholder just goes away.
    const reread = () => { if (camp.state === 'active') _fetchLive(el, camp); else _live.delete(camp.id); };
    const patch = () => window.deskV1PatchCampaign(camp, ['goal']);
    window.DeskV1Store.run({
      label,
      apply: () => {
        prevRead = _live.get(camp.id);
        _seq.set(camp.id, (_seq.get(camp.id) || 0) + 1);   // a read already in flight is now stale
        _live.set(camp.id, { phase: 'loading', data: null, error: null, writing: true });
        change(goal);
        repaint();
      },
      unapply: () => {
        if (prevRead) _live.set(camp.id, prevRead); else _live.delete(camp.id);
        revert(goal);
      },
      repaint,
      request: () => patch().then((out) => { reread(); return out; }),
      undoRequest: () => patch().then((out) => { reread(); return out; }),
    });
  }

  function _bindEditor(el, camp) {
    const goal = _goalOf(camp);
    el.querySelectorAll('[data-goal-field]').forEach((input) => {
      input.addEventListener('change', () => {
        const field = input.dataset.goalField;
        let val = input.value;
        if (input.type === 'number') val = (val === '' ? null : Number(val));
        else if (val === '') val = null;
        const prev = goal[field];
        _editGoal(el, camp, `Goal ${field} changed`, (g) => { g[field] = val; }, (g) => { g[field] = prev; });
      });
    });
    const addBtn = el.querySelector('[data-manual-entry-add]');
    if (addBtn) addBtn.onclick = () => {
      const dateIn = el.querySelector('[data-manual-entry-date]');
      const valIn = el.querySelector('[data-manual-entry-value]');
      const at = dateIn.value || _todayISODate();
      const value = Number(valIn.value);
      if (!Number.isFinite(value)) return;
      const prevEntries = goal.entries ? goal.entries.slice() : undefined;
      _editGoal(el, camp, `Entry ${value} on ${at}`,
        (g) => { g.entries = g.entries || []; g.entries.push({ at, value }); },
        (g) => { g.entries = prevEntries; });
      if (!window.DeskV1Store.live()) DeskV1Kit.toast(`Entry added: ${value} on ${at}`);
    };
    const retry = el.querySelector('[data-goal-retry]');
    if (retry) retry.onclick = () => { _live.delete(camp.id); _renderAll(el, camp); };
  }

  // R2-15 (§10.4): the Retro section mounts at the foot of this same panel,
  // through a backward-compatible seam (desk-v1-retro.js, undefined until
  // that file loads) — same "own file, own hook" convention as the Proposed/
  // Draft seams desk-v1-campaign.js already uses. This file never branches
  // on retro internals.
  function _renderAll(el, camp) {
    const goal = _goalOf(camp);
    el.innerHTML = `
      <div class="desk-v1-results">
        <div class="desk-v1-goal">
          ${_editorHTML(goal)}
          ${camp.state === 'active' ? _effectivenessHTML(el, camp, goal) : ''}
        </div>
        <div class="desk-v1-retro-mount" id="desk-v1-retro-mount"></div>
      </div>`;
    _bindEditor(el, camp);
    if (typeof window.deskV1RenderRetroSection === 'function') {
      window.deskV1RenderRetroSection(document.getElementById('desk-v1-retro-mount'), camp);
    }
  }

  function deskV1RenderResults(el, params) {
    const campaignId = (params || {}).campaignId;
    const camp = _campaign(campaignId);
    if (!camp) {
      el.innerHTML = '<div class="desk-v1-stub"><div class="desk-v1-stub-body">Campaign not found.</div></div>';
      return;
    }
    // Live: every opening of the stop reads M11 afresh (entries and terms may
    // have changed elsewhere), unless an edit's PATCH is still in flight.
    if (window.DeskV1Store.live() && camp.state === 'active') {
      const cur = _live.get(camp.id);
      if (!(cur && cur.writing)) _fetchLive(el, camp);
    }
    _renderAll(el, camp);
  }

  window.deskV1RenderResults = deskV1RenderResults;
})();

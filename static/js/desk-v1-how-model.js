// Desk v1: the Model (and Effort) pickers on a campaign's Brief, under the Agent
// picker (Ron 2026-11-18). Window-bridged, no `import`; desk-v1-how.js only gives
// this module a host node and calls `mount`.
//
// The pick is stored on the campaign beside its agent: `how.engine = {provider?,
// model?, effort?}`. No key (or no `how.engine`) is "Agent default": the agent's
// own pinned engine, else the project default, exactly what ran before. The
// catalog is the live `GET /api/engine/preferences` (providers[].models and
// .efforts), the same source the server validates a save against, so the picker
// never offers what a save would refuse. A native <select> with <optgroup>s: it
// is the control that already works at 390px.
//
// No substitution: a stored pick the catalog no longer offers stays visible as
// such ("(no longer offered)") instead of the picker quietly showing the default.
(function () {
  'use strict';
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const CATALOG_TTL_MS = 30000;
  let _catalog = null;   // {at, promise: Promise<providers[]>}
  function _loadCatalog() {
    if (_catalog && Date.now() - _catalog.at < CATALOG_TTL_MS) return _catalog.promise;
    const promise = window.DeskV1Store.api('GET', '/api/engine/preferences').then((d) => (d && d.providers) || []);
    _catalog = { at: Date.now(), promise };
    promise.catch(() => { if (_catalog && _catalog.promise === promise) _catalog = null; });
    return promise;
  }

  const _defaults = {};   // `${projectId}|${agentRef}` -> Promise<{provider, model, effort}>
  function _agentDefault(project, ref) {
    const key = project.id + '|' + ref;
    if (_defaults[key]) return _defaults[key];
    const p = window.DeskV1Store.api('GET', `/api/characters?project_id=${encodeURIComponent(project.id)}`).then((list) => {
      const rec = (list || []).find((c) => `${c.scope || 'global'}:${c.name}` === ref);
      return window.DeskV1Store.api('POST', '/api/engine/preview', { project_id: project.id, character: (rec && rec.engine) || null });
    });
    _defaults[key] = p;
    p.catch(() => { delete _defaults[key]; });
    return p;
  }

  function _provider(cat, name) { return (cat || []).find((p) => p.name === name) || null; }
  function _modelLabel(cat, provider, model) {
    const p = _provider(cat, provider);
    const m = p && (p.models || []).find((x) => x.id === model);
    return (m && m.label) || model;
  }
  function _pickText(pick, cat) {
    const parts = [];
    if (pick.model) parts.push(_modelLabel(cat, pick.provider, pick.model));
    if (pick.effort) parts.push(`${pick.effort} effort`);
    return parts.join(' · ');
  }
  function _selectable(p) { return p.model_selection && (p.models || []).length > 0; }

  // The one write: sets (or clears, with null) the campaign's own `how.engine`,
  // saved through the Store write / PATCH with Undo, like the Agent picker.
  function _setEngine(camp, next, label) {
    const prev = camp.how.engine ? Object.assign({}, camp.how.engine) : null;
    const put = (v) => { if (v && Object.keys(v).length) camp.how.engine = v; else delete camp.how.engine; };
    const render = () => { if (typeof window.deskV1Render === 'function') window.deskV1Render(); };
    window.DeskV1Store.write({
      label,
      apply: () => { put(next); render(); },
      unapply: () => put(prev),
      repaint: render,
      request: () => window.deskV1PatchCampaign(camp, ['plan', 'how']),
      undoRequest: () => window.deskV1PatchCampaign(camp, ['plan', 'how']),
    });
  }

  function _hostHTML() {
    return `<div class="desk-v1-rules-group" data-how-model>
      <div class="desk-v1-rules-group-title">Model</div>
      <select class="desk-v1-goal-select desk-v1-how-model-select" data-how-model-select aria-label="Model" disabled><option>Loading models…</option></select>
      <div data-how-effort-row hidden>
        <div class="desk-v1-rules-group-title desk-v1-how-model-effort-title">Effort</div>
        <select class="desk-v1-goal-select desk-v1-how-model-select" data-how-effort-select aria-label="Effort" disabled></select>
      </div>
      <div class="desk-v1-rules-hint" data-how-model-hint></div>
    </div>`;
  }

  function mount(host, camp, project) {
    if (!host) return;
    host.innerHTML = _hostHTML();
    const mSel = host.querySelector('[data-how-model-select]');
    const eSel = host.querySelector('[data-how-effort-select]');
    const eRow = host.querySelector('[data-how-effort-row]');
    const hint = host.querySelector('[data-how-model-hint]');
    const ref = project && ((camp.how && camp.how.agent) || (project.presence || {}).desk_agent);
    if (!project || !ref) {
      mSel.innerHTML = '<option>Pick an agent first</option>';
      hint.textContent = 'The model is chosen for the agent, so pick the agent above first.';
      return;
    }
    if (!window.DeskV1Store.live()) {
      mSel.innerHTML = '<option>Not available here</option>';
      return;
    }
    Promise.all([_loadCatalog(), _agentDefault(project, ref)]).then(([cat, dflt]) => {
      if (!host.isConnected) return;
      _paint(camp, cat, dflt, mSel, eSel, eRow, hint);
    }).catch((e) => {
      if (!host.isConnected) return;
      mSel.innerHTML = '<option>Could not load models</option>';
      hint.textContent = `The model list could not be read (${e && e.message ? e.message : e}). Your campaign keeps the choice it had.`;
    });
  }

  function _paint(camp, cat, dflt, mSel, eSel, eRow, hint) {
    const pick = (camp.how && camp.how.engine) || {};
    const val = (provider, model) => `${provider}|${model}`;
    const groups = cat.filter(_selectable).map((p) => `<optgroup label="${esc(p.display_name || p.name)}">${
      p.models.map((m) => `<option value="${esc(val(p.name, m.id))}">${esc(m.label || m.id)}</option>`).join('')}</optgroup>`).join('');
    const cur = pick.provider && pick.model ? val(pick.provider, pick.model) : '';
    const offered = !cur || cat.some((p) => _selectable(p) && p.name === pick.provider && p.models.some((m) => m.id === pick.model));
    const gone = offered ? '' : `<option value="${esc(cur)}">${esc(pick.model)} (no longer offered)</option>`;
    mSel.innerHTML = `<option value="">Agent default (${esc(_modelLabel(cat, dflt.provider, dflt.model))})</option>${gone}${groups}`;
    mSel.value = cur;
    mSel.disabled = false;

    const effProvider = pick.provider || dflt.provider;
    const efforts = (_provider(cat, effProvider) || {}).efforts || [];
    eRow.hidden = !efforts.length;
    const same = effProvider === dflt.provider;
    const stale = pick.effort && !efforts.includes(pick.effort)
      ? `<option value="${esc(pick.effort)}">${esc(pick.effort)} (not offered here)</option>` : '';
    eSel.innerHTML = `<option value="">Agent default${same && dflt.effort ? ` (${esc(dflt.effort)})` : ''}</option>${stale}${
      efforts.map((x) => `<option value="${esc(x)}">${esc(x)}</option>`).join('')}`;
    eSel.value = pick.effort || '';
    eSel.disabled = false;
    hint.textContent = 'Applies to this campaign only. Plans, drafts and revisions run on this model; if its provider is signed out or out of allowance they stop with that reason rather than switching.';

    mSel.onchange = () => {
      const next = {};
      let note = '';
      if (mSel.value) {
        const i = mSel.value.indexOf('|');
        next.provider = mSel.value.slice(0, i);
        next.model = mSel.value.slice(i + 1);
      }
      const newProvider = next.provider || dflt.provider;
      const offers = (_provider(cat, newProvider) || {}).efforts || [];
      if (pick.effort) {
        if (offers.includes(pick.effort)) next.effort = pick.effort;
        else note = `Effort “${pick.effort}” is not offered by ${(_provider(cat, newProvider) || {}).display_name || newProvider}, so it was reset to the agent default.`;
      }
      _setEngine(camp, next, mSel.value ? `Set campaign model to ${_modelLabel(cat, next.provider, next.model)}` : 'Set campaign model to the agent default');
      if (note && window.DeskV1Kit) window.DeskV1Kit.toast(note);
    };
    eSel.onchange = () => {
      const next = Object.assign({}, pick);
      if (eSel.value) next.effort = eSel.value; else delete next.effort;
      _setEngine(camp, next, eSel.value ? `Set campaign effort to ${eSel.value}` : 'Set campaign effort to the agent default');
    };
  }

  // The right-hand agent box: "<agent> · <model label>", added after the agent's
  // name when the campaign has its own pick. Nothing is added otherwise.
  function paintBox(root, camp) {
    const name = root && root.querySelector('.desk-v1-posy-box .desk-thread-name');
    if (!name) return;
    root.querySelectorAll('[data-posy-engine]').forEach((n) => n.remove());
    const pick = camp && camp.how && camp.how.engine;
    if (!pick || !(pick.model || pick.effort)) return;
    const span = document.createElement('span');
    span.className = 'desk-thread-engine';
    span.setAttribute('data-posy-engine', '');
    name.after(span);
    const set = (cat) => { span.textContent = ' · ' + _pickText(pick, cat); };
    set(null);
    if (window.DeskV1Store.live()) _loadCatalog().then(set).catch(() => {});
  }

  window.DeskV1HowModel = { mount, paintBox };
})();

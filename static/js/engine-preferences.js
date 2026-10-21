// One preference editor for global and project scopes. Effective labels come
// from the same server resolver that starts work; no parallel JS inheritance.
const views = new Map();
const requests = new Map();
const previews = new Map();
let activeProject = '';
const loadedAt = new Map();
const revisions = new Map();
const TTL = 30000;
let generation = 0;

function revision(projectId) {
  const p = typeof allProjects === 'undefined' ? {} : allProjects.find(p => p.id === projectId) || {};
  const cfg = typeof _globalConfig === 'undefined' ? {} : _globalConfig || {};
  return JSON.stringify([p.provider, p.provider_overrides, p.agent_model, p.agent_effort,
    cfg.default_provider, cfg.provider_defaults, cfg.agent_model, cfg.agent_effort, cfg.auto_model_enabled]);
}

function stale(projectId) {
  return views.has(projectId) && (Date.now() - loadedAt.get(projectId) > TTL || revisions.get(projectId) !== revision(projectId));
}

async function load(projectId = '', force = false) {
  if (force || stale(projectId)) { views.delete(projectId); requests.delete(projectId); }
  if (views.has(projectId)) return views.get(projectId);
  if (!requests.has(projectId)) {
    const epoch = generation, fingerprint = revision(projectId);
    requests.set(projectId, (async () => {
      const response = await fetch(API_BASE + '/api/engine/preferences' +
        (projectId ? '?project_id=' + encodeURIComponent(projectId) : ''));
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Could not load provider preferences');
      if (epoch === generation) {
        views.set(projectId, data); loadedAt.set(projectId, Date.now()); revisions.set(projectId, fingerprint);
      }
      return data;
    })());
  }
  return requests.get(projectId);
}

function invalidate() { generation++; views.clear(); requests.clear(); previews.clear(); loadedAt.clear(); revisions.clear(); }

function effective(projectId, provider, onReady) {
  if (stale(projectId)) { views.delete(projectId); requests.delete(projectId); }
  if (views.has(projectId)) return views.get(projectId).providers.find(p => p.name === provider)?.effective || null;
  if (!requests.has(projectId)) load(projectId).then(() => onReady?.()).catch(() => {});
  return null;
}

function preview(projectId, character = {}, onReady) {
  const body = {project_id: projectId, character: {
    provider: character.provider || '', model: character.model || '', effort: character.effort || ''}};
  const encoded = JSON.stringify(body);
  const key = encoded + '::' + revision(projectId) + '::' + Math.floor(Date.now() / TTL);
  if (!previews.has(key)) {
    previews.set(key, {pending: true});
    if (previews.size > 256) previews.delete(previews.keys().next().value);
    const epoch = generation;
    fetch(API_BASE + '/api/engine/preview', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: encoded})
      .then(async response => {
        const data = await response.json();
        if (epoch !== generation) return;
        previews.set(key, response.ok ? {engine: data} : {error: data.error || 'Could not resolve engine'});
        onReady?.();
      }).catch(() => { previews.set(key, {error: 'Could not load effective engine'}); onReady?.(); });
  }
  return previews.get(key);
}

function modelLabel(provider, value) {
  if (!value) return 'Provider-managed default';
  if (value.startsWith('tier:')) {
    const tier = value.slice(5), target = provider.tiers?.[tier];
    return 'Auto-update ' + tier + ': ' + (target && !target.startsWith('tier:')
      ? modelLabel(provider, target) : 'no model mapped');
  }
  const label = provider.models?.find(m => m.id === value)?.label;
  return label && label !== value ? label + ' (' + value + ')' : value;
}

function field(provider, projectId, key, label, compact = false) {
  const entry = projectId ? provider.overrides : provider.defaults;
  const canInherit = projectId || (key !== 'model' && key !== 'effort');
  const inherited = canInherit && !Object.prototype.hasOwnProperty.call(entry, key);
  const value = entry[key] ?? '';
  const choices = key === 'effort'
    ? [['', 'Provider-managed default'], ...provider.efforts.map(v => [v, v.charAt(0).toUpperCase() + v.slice(1)])]
    : [['', 'Provider-managed default'], ...Object.keys(provider.tiers).map(t => ['tier:' + t, modelLabel(provider, 'tier:' + t)]),
       ...provider.models.map(m => [m.id, modelLabel(provider, m.id)])];
  if (projectId) choices.unshift(['__inherit__', 'Use global (' + (key === 'effort'
    ? provider.defaults.effort || 'provider default' : modelLabel(provider, provider.defaults.model || '')) + ')']);
  else if (canInherit) choices.unshift(['__inherit__', key === 'background_model' ? 'CR automatic (recommended)' : 'Use CR automatic / background override']);
  if (value && !choices.some(([v]) => v === value)) choices.push([value, value + ' (saved)']);
  const id = ['engine-pref', projectId || 'global', provider.name, key].join('-');
  const selected = inherited ? '__inherit__' : value;
  return `<div class="settings-row engine-preference-row">
    <label class="settings-label" for="${esc(id)}">${compact ? '' : esc(provider.display_name) + ' · '}${esc(label)}</label>
    <div class="engine-preference-control">
      <select id="${esc(id)}" class="settings-select" data-engine-field="${esc(key)}" data-provider="${esc(provider.name)}"
        onchange="EnginePreferences.choose('${esc(projectId)}','${esc(provider.name)}','${esc(key)}',this)">
        ${choices.map(([v, l]) => `<option value="${esc(v)}"${selected === v ? ' selected' : ''}>${esc(l)}</option>`).join('')}
        ${key === 'effort' ? '' : '<option value="__custom__">Custom model…</option>'}
      </select>
      ${key === 'effort' ? '' : `<input class="settings-input engine-custom-model" hidden aria-label="${esc(provider.display_name)} custom ${esc(label)}" placeholder="Exact model ID"
        onchange="EnginePreferences.saveField('${esc(projectId)}','${esc(provider.name)}','${esc(key)}',this.value)"/>`}
    </div>
  </div>`;
}

function providerControls(p, projectId = '', compact = false) {
  const entry = projectId ? p.overrides : p.defaults;
  const roleFields = ['scribe_model','condense_model','distiller_model', ...(p.name === 'claude' ? ['auto_model_classifier_model'] : [])];
  const customTasks = ['background_model', ...roleFields].some(key => Object.hasOwn(p.defaults, key));
  const automatic = p.automatic_background_model;
  return `<div class="engine-vendor-defaults">
    ${p.model_selection === false ? '<p class="settings-hint">Model selection is managed by this provider.</p>' : field(p, projectId, 'model', compact ? 'Default model' : 'Model', compact)}
    ${p.efforts.length || entry.effort ? field(p, projectId, 'effort', 'Effort', compact) : ''}
    ${entry.effort && !p.efforts.includes(entry.effort) ? '<p class="settings-hint">This saved effort is not applied by the adapter. Choose the provider-managed default to clear it.</p>' : ''}
    <p class="settings-hint engine-effective">Model requested at launch: ${esc(modelLabel(p, p.effective.model))} · from ${esc(p.effective.model_source)}${p.effective.effort ? ' · effort ' + esc(p.effective.effort) : ''}</p>
    ${projectId || p.model_selection === false ? '' : `<details class="engine-provider-advanced" id="engine-advanced-${esc(p.name)}"><summary>Advanced${customTasks ? ' (custom task models)' : ''}</summary>
      <p class="settings-hint engine-tracking-help">Auto-update follows a tier's current model mapping when work starts. The model ID is shown in each option. Provider aliases such as opus or names ending in -latest can change versions; choose a versioned model ID to keep a specific version.</p>
      <p class="settings-hint engine-task-policy">${automatic === undefined ? 'CR manages internal-task model defaults.' : automatic
        ? 'CR automatic uses the lightweight model ' + esc(modelLabel(p, automatic)) + ' for internal tasks. Some task-specific policies may choose a different model.'
        : 'No lightweight model mapping is available. CR uses the provider default; its cost is unknown.'} Chat model choices do not change these task defaults.</p>
      <p class="settings-hint">Optional overrides below can increase token cost or usage substantially. Leaving them on CR automatic is recommended.${customTasks ? ' Existing custom task overrides are active.' : ''}</p>
      ${field(p, '', 'background_model', 'Internal-task default', true)}
      ${roleFields.map(key => `<div class="engine-role-override">
        ${field(p, '', key, key === 'auto_model_classifier_model' ? 'Task classifier' : key.split('_')[0], true)}
        <button type="button" class="btn-browse" onclick="EnginePreferences.saveField('','${esc(p.name)}','${key}',null)">Use CR automatic / background override</button>
      </div>`).join('')}
      <button type="button" class="btn-browse" onclick="EnginePreferences.resetTasks('${esc(p.name)}')">Use CR automatic for all internal tasks</button>
    </details>`}
  </div>`;
}

function renderConnection(provider) {
  const entry = views.get('')?.providers.find(p => p.name === provider);
  return entry ? providerControls(entry, '', true)
    : '<p class="settings-hint">Model preferences are unavailable. Close and reopen Settings to retry.</p>';
}

function render(projectId = '') {
  const data = views.get(projectId);
  if (!data) return '<div class="settings-hint">Provider preferences are unavailable. Close and reopen to retry.</div>';
  const providerChoice = projectId ? `<div class="settings-row">
    <label class="settings-label" for="project-provider-default">Default provider</label>
    <select class="settings-select" id="project-provider-default" onchange="EnginePreferences.saveProjectProvider('${esc(projectId)}',this.value)">
      <option value="">Use global (${esc(data.default_provider || 'not selected')})</option>
      ${data.project_provider && !data.providers.some(p => p.name === data.project_provider) ? `<option value="${esc(data.project_provider)}" selected>${esc(data.project_provider)} (not registered)</option>` : ''}
      ${data.providers.map(p => `<option value="${esc(p.name)}"${data.project_provider === p.name ? ' selected' : ''}>${esc(p.display_name)}</option>`).join('')}
    </select></div>` : '';
  return `${providerChoice}<p class="settings-hint">${projectId
    ? 'New unpinned work inherits global provider preferences. Each provider keeps its own overrides.'
    : 'Each provider keeps its own model and effort defaults.'}</p>
    <p class="settings-hint engine-tracking-help">Auto-update follows a tier's current model mapping when work starts. The model ID is shown in each option. Provider aliases such as opus or names ending in -latest can change versions; choose a versioned model ID to keep a specific version.</p>
    ${data.providers.map(p => `<fieldset class="engine-preference-group"><legend>${esc(p.display_name)}</legend>${providerControls(p, projectId)}</fieldset>`).join('')}`;
}

function choose(projectId, provider, key, select) {
  const input = select.parentElement.querySelector('.engine-custom-model');
  if (select.value === '__custom__') { input.hidden = false; input.focus(); return; }
  if (input) input.hidden = true;
  return saveField(projectId, provider, key, select.value === '__inherit__' ? null : select.value);
}

async function write(projectId, body) {
  if (projectId) {
    const response = await fetch(API_BASE + '/api/project/' + encodeURIComponent(projectId), {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not save project provider preferences');
  } else {
    const result = await humanProofFetch(API_BASE + '/api/config', {method: 'PUT', body: JSON.stringify(body)}, {
      title: 'Save provider preference', description: 'Re-enter your dashboard passcode to change provider defaults.'});
    if (!result) return false;
    if (!result.ok) throw new Error(result.body.error || 'Could not save provider preferences');
  }
  invalidate();
  const response = await fetch(API_BASE + '/api/config');
  if (response.ok) Object.assign(_globalConfig, await response.json());
  if (typeof refreshSilent === 'function') await refreshSilent();
  return true;
}

async function repaint(projectId) {
  const expanded = [...document.querySelectorAll('.engine-provider-advanced[open]')].map(el => el.id);
  await load(projectId, true);
  if (projectId && activeProject === projectId && document.getElementById('agent-settings-overlay')?.classList.contains('visible')) await openProject(projectId);
  else if (document.getElementById('settings-body')) await window._renderSettings();
  expanded.forEach(id => { const panel = document.getElementById(id); if (panel) panel.open = true; });
}

async function resetTasks(provider) {
  const fields = ['background_model','scribe_model','condense_model','distiller_model','auto_model_classifier_model'];
  try {
    const saved = await write('', {provider_defaults: {[provider]: Object.fromEntries(fields.map(key => [key, null]))}});
    if (saved) showToast('CR automatic internal-task models restored');
  } catch (error) { showToast(error.message || 'Could not restore internal-task defaults', 6000); }
  try { await repaint(''); } catch (error) { showToast(error.message); }
}

async function saveField(projectId, provider, key, value) {
  if (typeof value === 'string') value = value.trim();
  try {
    const saved = await write(projectId, {[projectId ? 'provider_overrides' : 'provider_defaults']: {[provider]: {[key]: value}}});
    if (saved) showToast('Provider preference saved');
  } catch (error) { showToast(error.message || 'Could not save provider preference', 6000); }
  // Restore the authoritative value after success, refusal or cancelled proof.
  try { await repaint(projectId); } catch (error) { showToast(error.message); }
}

async function saveProjectProvider(projectId, provider) {
  try { await write(projectId, {provider}); await repaint(projectId); }
  catch (error) { showToast(error.message); await repaint(projectId); }
}

async function saveProjectMode(projectId, value) {
  try { await write(projectId, {use_streaming_agent: value === '' ? null : value === 'b'}); await repaint(projectId); }
  catch (error) { showToast(error.message); await repaint(projectId); }
}

async function openProject(projectId) {
  activeProject = projectId;
  const box = document.getElementById('agent-settings-box');
  const overlay = document.getElementById('agent-settings-overlay');
  if (!box || !overlay) return;
  overlay.classList.add('visible');
  box.innerHTML = '<div class="settings-hint">Loading project providers…</div>';
  try {
    await load(projectId);
    const project = allProjects.find(p => p.id === projectId);
    const mode = views.get(projectId).project_streaming;
    box.innerHTML = `<div class="mc-dialog-header"><span>Providers · ${esc(project?.name || projectId)}</span>
      <button class="mc-dialog-close" onclick="closeAgentSettingsDialog()" aria-label="Close providers">&#10005;</button></div>
      <div class="engine-project-body">${render(projectId)}
        <details><summary>Advanced runtime</summary>
          <div class="settings-row"><label class="settings-label" for="project-runtime-mode">Process lifetime</label>
            <select class="settings-select" id="project-runtime-mode" onchange="EnginePreferences.saveProjectMode('${esc(projectId)}',this.value)">
              <option value=""${mode == null ? ' selected' : ''}>Use global</option>
              <option value="b"${mode === true ? ' selected' : ''}>Persistent where supported</option>
              <option value="a"${mode === false ? ' selected' : ''}>Start per turn</option>
            </select></div></details>
        <p class="settings-hint">Agent identity and working defaults are on the Floor.</p>
        <button class="btn-browse" onclick="openFloorPreferences()">Floor defaults</button>
      </div><div class="mc-dialog-footer"><button class="btn-add" onclick="closeAgentSettingsDialog()">Done</button></div>`;
  } catch (error) {
    box.innerHTML = `<p role="alert">${esc(error.message)}</p><button class="btn-browse" onclick="closeAgentSettingsDialog()">Close</button>`;
  }
}

function dismissProject() { activeProject = ''; }
window.EnginePreferences = {load, effective, preview, invalidate, render, renderConnection, modelLabel, choose, saveField, resetTasks, saveProjectProvider, saveProjectMode, openProject, dismissProject};

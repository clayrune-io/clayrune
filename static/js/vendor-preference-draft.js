// Vendor settings are a reviewable draft. The existing server gate is used
// once for the explicit Save; no passcode or unlock permission is cached.
const TOP_DEFAULTS = {default_provider:'', cli_auto_update_enabled:true,
  auto_model_enabled:false, engine_fallback_order:[], use_streaming_agent:false,
  agent_remote_control:false, agent_channels:''};
let baseline = null;
let modelBaseline = {};
const settings = new Map();
const models = new Map();
let saving = false;
let refreshing = false;
let status = '';
const copy = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
const same = (a,b) => JSON.stringify(a) === JSON.stringify(b);
const count = () => settings.size + [...models.values()].reduce((n,fields) => n + Object.keys(fields).length,0);

function begin(config, providers) {
  if (count() || (saving && baseline)) return;
  baseline = copy(config);
  modelBaseline = Object.fromEntries(providers.map(p => [p.name,copy(p.defaults)]));
}
function configView(config) { return {...config, ...Object.fromEntries(settings)}; }
function providerView(provider) {
  const defaults = {...provider.defaults};
  for (const [key,value] of Object.entries(models.get(provider.name) || {})) {
    if (value === null) delete defaults[key]; else defaults[key] = value;
  }
  return {...provider,defaults};
}
function handles(key) { return Object.hasOwn(TOP_DEFAULTS,key); }
function settingDirty(key) { return settings.has(key); }
function modelDirty(provider,key) { return Object.hasOwn(models.get(provider) || {},key); }
function mount() {
  const content = document.querySelector('.mc-settings-modal');
  if (!content) return;
  const settingsBody = document.getElementById('settings-body');
  const busy = saving || refreshing;
  if (settingsBody) settingsBody.inert = busy;
  const nav = document.getElementById('bottom-tab-bar');
  const navHeight = nav && getComputedStyle(nav).display !== 'none' ? nav.getBoundingClientRect().height : 0;
  content.style.setProperty('--vendor-draft-bottom-inset',navHeight + 'px');
  let bar = document.getElementById('vendor-draft-bar');
  if (!bar) {
    bar = document.createElement('div'); bar.id = 'vendor-draft-bar';
    bar.className = 'vendor-draft-bar'; content.appendChild(bar);
  }
  bar.hidden = !count() && !busy;
  const signature = JSON.stringify([count(),saving,refreshing,status]);
  if (bar.dataset.signature !== signature) {
  bar.dataset.signature = signature;
  bar.innerHTML = `<div><strong>${refreshing ? 'Reloading saved preferences' : count() + ' unsaved vendor ' + (count() === 1 ? 'change' : 'changes')}</strong>
    <div class="settings-hint">Changes take effect after Save.</div>
    <div class="vendor-draft-status" role="status">${esc(status)}</div></div>
    <div class="vendor-draft-actions"><button type="button" class="btn-browse" id="vendor-draft-cancel" onclick="VendorPreferenceDraft.discard()" ${busy ? 'disabled' : ''}>Cancel</button>
    <button type="button" class="btn-add" id="vendor-draft-save" onclick="VendorPreferenceDraft.save()" ${busy ? 'disabled' : ''}>${saving ? 'Saving…' : 'Save changes'}</button></div>`;
  }
  const win = content.closest('.modal-window');
  if (win?.getBoundingClientRect().bottom > innerHeight) window._positionSettingsModal(win);
}
async function repaint() { await window.EnginePreferences.repaint(''); mount(); }
async function stageSetting(key,value) {
  if (saving || refreshing || !handles(key)) return;
  const original = baseline?.[key] ?? TOP_DEFAULTS[key];
  if (same(original,value)) settings.delete(key); else settings.set(key,copy(value));
  status = ''; mount(); await repaint();
}
async function stageModels(provider,patch) {
  if (saving || refreshing) return;
  const original = modelBaseline[provider] || {};
  const fields = {...models.get(provider)};
  for (const [key,value] of Object.entries(patch)) {
    const old = Object.hasOwn(original,key) ? original[key] : (['model','effort'].includes(key) ? '' : undefined);
    if (value === null ? !Object.hasOwn(original,key) : same(old,value)) delete fields[key];
    else fields[key] = value;
  }
  if (Object.keys(fields).length) models.set(provider,fields); else models.delete(provider);
  status = ''; mount(); await repaint();
}
async function discard() {
  if (saving || refreshing) return;
  refreshing = true;
  settings.clear(); models.clear(); status = ''; baseline = null;
  mount();
  try { await repaint(); }
  finally { refreshing = false; mount(); }
}
function conflicting(current,patch) {
  for (const key of settings.keys()) {
    if (!same(current[key] ?? TOP_DEFAULTS[key],baseline[key] ?? TOP_DEFAULTS[key])) return true;
  }
  for (const [provider,fields] of Object.entries(patch.provider_defaults || {})) {
    for (const key of Object.keys(fields)) {
      if (!same(current.provider_defaults?.[provider]?.[key],baseline.provider_defaults?.[provider]?.[key])) return true;
      if (!current.provider_defaults && !baseline.provider_defaults) {
        const legacy = key === 'model' ? 'agent_model' : key === 'effort' ? 'agent_effort' : key;
        if (!same(current[legacy],baseline[legacy])) return true;
      }
    }
  }
  return false;
}
async function save() {
  if (saving || refreshing || !count()) return;
  saving = true; status = ''; mount();
  const patch = copy(Object.fromEntries(settings));
  if (models.size) patch.provider_defaults = copy(Object.fromEntries(models));
  let committed = false;
  try {
    const before = await fetch(API_BASE + '/api/config');
    if (!before.ok) throw new Error('Could not check current preferences. Your draft is kept.');
    if (conflicting(await before.json(),patch)) throw new Error('These preferences changed elsewhere. Your draft is kept; Cancel reloads the saved values.');
    const result = await humanProofFetch(API_BASE + '/api/config', {method:'PUT',body:JSON.stringify(patch)}, {
      title:'Save vendor preferences', description:`Re-enter your dashboard passcode once to apply ${count()} vendor preference changes.`});
    if (!result) { status = 'Save canceled. Your draft is kept.'; return; }
    if (!result.ok) throw new Error(result.body.error || 'Could not save vendor preferences.');
    committed = true; settings.clear(); models.clear(); baseline = null;
    window.EnginePreferences.invalidate();
    const response = await fetch(API_BASE + '/api/config');
    if (!response.ok) throw new Error('Saved. Reload the dashboard to refresh the displayed values.');
    Object.assign(_globalConfig,await response.json());
    if (Object.hasOwn(patch,'default_provider')) {
      _agentProviders = null;
      await _ensureAgentProviders();
      if (typeof refreshAuthStatus === 'function') refreshAuthStatus();
    }
    showToast('Vendor preferences saved');
  } catch (error) {
    status = error.message || 'Could not save vendor preferences.';
    showToast(status,6000);
  } finally {
    if (committed) { try { await repaint(); } catch (_) { showToast('Saved. Reload to refresh vendor preferences.',6000); } }
    saving = false; mount();
  }
}
window.addEventListener('beforeunload', event => {
  if (count()) { event.preventDefault(); event.returnValue = ''; }
});
window.VendorPreferenceDraft = {begin,configView,providerView,handles,settingDirty,modelDirty,mount,stageSetting,stageModels,discard,save};

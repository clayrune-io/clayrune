// Working preferences live beside the agents they affect. These are global
// defaults for untyped agents; a figure's own name and a type's pins stay local.
const ID = '__floor_preferences';
const FIELDS = [
  {key: 'agent_name', label: 'Default agent name', type: 'text', section: 'Identity'},
  {key: 'agent_avatar', label: 'Default agent face', type: 'text', section: 'Identity', hint: 'An emoji or a figure such as fig:navigator. Hired agents keep their own faces.'},
  {key: 'agent_max_turns', label: 'Maximum turns', type: 'number', section: 'Working defaults', hint: '0 means unlimited.'},
  {key: 'agent_permission_mode', label: 'Tool permissions', type: 'select', section: 'Working defaults', choices: [['','Provider default'],['default','Ask'],['plan','Plan'],['auto-edit','Auto-edit'],['full-auto','Full auto']]},
  {key: 'brief', label: 'Brief replies', type: 'select', section: 'Replies', choices: [['off','Off'],['phone','Phone'],['all','Everywhere']]},
  {key: 'agent_emojis_enabled', label: 'Agents may use emojis', type: 'checkbox', section: 'Replies', hint: 'Individual agent types can override this preference.'},
];
const PROFILE_FIELDS = [
  {key: 'user_name', label: 'Your name', type: 'text', section: 'Profile', hint: 'Shown in agent context.'},
  {key: 'user_timezone', label: 'Your timezone', type: 'text', section: 'Profile', hint: 'Blank uses this computer’s timezone. For example: America/New_York.'},
];

function control(field, value) {
  const id = 'floor-pref-' + field.key;
  let input;
  if (field.type === 'select') input = `<select class="settings-select" id="${id}">${field.choices.map(([v,l]) => `<option value="${v}"${value === v ? ' selected' : ''}>${l}</option>`).join('')}</select>`;
  else if (field.type === 'checkbox') input = `<input type="checkbox" id="${id}"${value ? ' checked' : ''}>`;
  else input = `<input class="settings-input" id="${id}" type="${field.type}" value="${esc(value ?? '')}"${field.type === 'number' ? ' min="0" step="1"' : ''}>`;
  return `<div class="settings-row"><div><label class="settings-label" for="${id}">${field.label}</label>${field.hint ? `<div class="settings-hint">${field.hint}</div>` : ''}</div>${input}</div>`;
}

async function open(profileOnly = false) {
  window.LearnEngine?.dismissOffer?.();
  if (openModals.has(ID)) closeModalById(ID);
  const response = await fetch(API_BASE + '/api/config');
  if (!response.ok) { showToast('Could not load preferences'); return; }
  const original = await response.json();
  original.brief = original.brief_replies_always_enabled ? 'all' : original.mobile_brief_replies_enabled ? 'phone' : 'off';
  const fields = profileOnly ? PROFILE_FIELDS : FIELDS;
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = ID;
  win.setAttribute('role', 'dialog');
  win.setAttribute('aria-labelledby', 'floor-preferences-title');
  win.innerHTML = `<form class="modal-content floor-preferences-modal">
    <div class="modal-header"><span id="floor-preferences-title">${profileOnly ? 'Your profile' : 'Floor defaults'}</span>
      <button type="button" class="modal-close" onclick="closeModalById('${ID}')" aria-label="Close preferences">&#10005;</button></div>
    <div class="floor-preferences-body">
      ${profileOnly ? '' : `<p class="settings-hint">Global identity and working defaults. Figure labels and hired agent types keep their own identities. Model defaults live in Providers.</p>`}
      ${[...new Set(fields.map(f => f.section))].map(section => `<section class="settings-section"><h3 class="settings-section-title">${section}</h3>${fields.filter(f => f.section === section).map(f => control(f, original[f.key])).join('')}
      ${section === 'Identity' ? '<div id="agent-face-preview"></div><div class="settings-fig-row" id="agent-face-figs"></div>' : ''}</section>`).join('')}
      ${profileOnly ? '' : `<section class="settings-section"><h3 class="settings-section-title">Related controls</h3>
        <button type="button" class="btn-browse" onclick="SettingsOwnership.openProviders()">Provider defaults</button>
        <button type="button" class="btn-browse" onclick="sidebarNav('scheduler')">Scheduled runs</button>
        <button type="button" class="btn-browse" onclick="SettingsOwnership.openSecurity()">Unattended safety</button>
        <p class="settings-hint">Edit a hired agent from its Bench card. Project engine overrides are in the project menu under Providers.</p></section>`}
      <p role="status" id="floor-preferences-status"></p>
    </div><div class="floor-preferences-footer"><button type="button" class="btn-browse" onclick="closeModalById('${ID}')">Cancel</button><button class="btn-add" type="submit">Save</button></div></form>`;
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(ID, {projectId: null, element: win, minimized: false, zIndex: z});
  centerModalElement(win);
  focusModal(ID);
  if (!profileOnly) hydrateFaces(win);
  win.querySelector('form').onsubmit = async event => {
    event.preventDefault();
    const body = {};
    for (const field of fields) {
      const el = win.querySelector('#floor-pref-' + field.key);
      let value = field.type === 'checkbox' ? el.checked : el.value.trim();
      if (field.type === 'number') {
        value = Number(value);
        if (!Number.isInteger(value) || value < 0) { el.reportValidity(); return; }
      }
      if (value === (original[field.key] ?? (field.type === 'checkbox' ? false : ''))) continue;
      if (field.key === 'brief') {
        body.mobile_brief_replies_enabled = value === 'phone';
        body.brief_replies_always_enabled = value === 'all';
      } else body[field.key] = value;
    }
    const status = win.querySelector('#floor-preferences-status');
    if (!Object.keys(body).length) { status.textContent = 'No changes to save.'; return; }
    const button = win.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const result = await humanProofFetch(API_BASE + '/api/config', {method: 'PUT', body: JSON.stringify(body)}, {
        title: 'Save preferences', description: 'Re-enter your dashboard passcode to change these defaults.'});
      if (!result) { status.textContent = 'Changes have not been saved.'; return; }
      if (!result.ok) { status.textContent = result.body.error || 'Could not save preferences.'; return; }
      Object.assign(_globalConfig, body);
      if (window.EnginePreferences) window.EnginePreferences.invalidate();
      closeModalById(ID);
      if (window.refreshFloor && openModals.has('__floor')) await window.refreshFloor();
      if (document.getElementById('settings-body')) await window._renderSettings();
      showToast('Preferences saved');
    } catch (error) { status.textContent = error.message || 'Could not save preferences.'; }
    finally { button.disabled = false; }
  };
}

async function hydrateFaces(win) {
  try {
    const response = await fetch(API_BASE + '/api/avatars');
    if (!response.ok || !win.isConnected) return;
    const data = await response.json();
    const host = win.querySelector('#agent-face-figs');
    const input = win.querySelector('#floor-pref-agent_avatar');
    const paint = () => {
      win.querySelector('#agent-face-preview').innerHTML = window.avatarHTML(input.value, 34);
      host.querySelectorAll('button').forEach(button => button.classList.toggle('sel', button.dataset.face === input.value));
    };
    host.innerHTML = (data.figures || []).map(name => `<button type="button" class="settings-fig" data-face="${esc('fig:' + name)}" aria-label="${esc(name)}">${window.avatarHTML('fig:' + name, 34)}</button>`).join('');
    host.querySelectorAll('button').forEach(button => button.onclick = () => {
      input.value = input.value === button.dataset.face ? '' : button.dataset.face;
      paint();
    });
    input.addEventListener('input', paint);
    paint();
  } catch (_) { /* Optional face strip; the text field remains available. */ }
}

window.openFloorPreferences = () => open(false);
window.openUserProfile = () => open(true);

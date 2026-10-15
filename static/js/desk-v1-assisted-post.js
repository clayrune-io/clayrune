// Assisted posting, as the person sees it (one unit, one file).
//
// An account with capability `assisted` (Instagram today) is never published to by the Desk.
// On an approved version for it, this module offers one button, "Fill in on Instagram": it asks
// for the dashboard passcode (human-only, like Approve), calls
// POST /api/desk/pieces/<piece>/versions/<version>/assisted-post, shows that it is working, and
// then shows the server's own message (ready for you, or the step it stopped at). Clayrune never
// presses the final button; this screen cannot, because the route cannot.
//
// It also owns the one-time switch for an existing read-only account of such a platform:
// PATCH capability 'assisted', passcode-gated. Wording comes from the shared copy set
// (desk-v1-connect-copy.js `words`).
import { ConnectCopy as C } from './desk-v1-connect-copy.js';

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const W = () => C.words;
const NAMES = { instagram: 'Instagram' };
const name = ch => NAMES[ch?.platform] || (ch?.platform ? ch.platform[0].toUpperCase() + ch.platform.slice(1) : '');
const isAssisted = ch => !!ch && ch.capability === 'assisted';
const canEnable = ch => !!ch && ch.capability === 'none' && ch.platform in NAMES;
const fillable = v => !!v && (v.state === 'approved' || v.state === 'scheduled');
const busy = new Set();

function _say(root, state, text) {
  const out = root.querySelector('[data-assisted-status]');
  if (!out) return;
  out.dataset.state = state;
  out.textContent = text;
}

async function _send(url, method, proof, body = {}) {
  if (typeof window.humanProofFetch !== 'function') throw new Error('the passcode prompt is not available');
  return window.humanProofFetch(url, { method, body: JSON.stringify(body) }, proof);
}

// The button for one version, or '' when this version/account does not take it.
function fillHTML(channel, version) {
  if (!isAssisted(channel) || !fillable(version)) return '';
  return `<div class="desk-v1-assisted" data-assisted-post="${esc(version.id)}">
    <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-assisted-fill>${esc(`${W().assistedFillOn} ${name(channel)}`)}</button>
    <span role="status" aria-live="polite" data-assisted-status></span>
  </div>`;
}

function bindFill(root, family, version, channel) {
  const box = root.querySelector(`[data-assisted-post="${CSS.escape(version.id)}"]`);
  const button = box && box.querySelector('[data-assisted-fill]');
  if (!button) return;
  const url = `/api/desk/pieces/${encodeURIComponent(family.id)}/versions/${encodeURIComponent(version.id)}/assisted-post`;
  button.disabled = busy.has(version.id);
  button.onclick = async () => {
    if (busy.has(version.id)) return;
    busy.add(version.id);
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    _say(box, 'working', W().assistedWorking);
    try {
      const res = await _send(url, 'POST', { title: W().assistedProofTitle, description: W().assistedProofText, busyText: W().assistedWorking });
      if (res === null) _say(box, 'cancelled', W().assistedNoPasscode);
      else if (res.ok) _say(box, 'ready', `${res.body.message || W().assistedDone}${res.body.final_button_present === false ? ` ${W().assistedNoFinal}` : ''}`);
      else _say(box, 'failed', (res.body && (res.body.error || res.body.message)) || `${W().assistedFailed} (HTTP ${res.status})`);
    } catch (e) {
      _say(box, 'failed', `${W().assistedFailed} ${e && e.message ? e.message : ''}`.trim());
    } finally {
      busy.delete(version.id);
      button.disabled = false;
      button.removeAttribute('aria-busy');
    }
  };
}

// Put the button into `host` (a node already on screen) and wire it.
function mount(host, family, version, channel) {
  if (!host) return;
  const html = fillHTML(channel, version);
  if (!html) return;
  host.insertAdjacentHTML('beforeend', html);
  bindFill(host, family, version, channel);
}

// Connections: the word and line for an assisted account, and the one-time switch for a read-only one.
function statusWord(ch) { return isAssisted(ch) ? W().assistedStatus : null; }

function accountHTML(ch) {
  if (isAssisted(ch)) {
    return `<div class="desk-v1-conn-publish" data-conn-publish data-ready="true" data-assisted-account><span class="desk-v1-how-field-label">Publishing</span> <span data-conn-publish-text>${esc(W().assistedLine)}</span></div>`;
  }
  if (!canEnable(ch)) return '';
  return `<div class="desk-v1-conn-publish" data-assisted-enable-box>
    <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-assisted-enable>${esc(W().assistedEnable)}</button>
    <span role="status" aria-live="polite" data-assisted-status></span>
  </div>`;
}

function bindAccount(row, ch, onDone) {
  const button = row.querySelector('[data-assisted-enable]');
  if (!button) return;
  button.onclick = async () => {
    const box = button.closest('[data-assisted-enable-box]');
    button.disabled = true;
    _say(box, 'working', W().assistedEnabling);
    try {
      const res = await _send(`/api/desk/accounts/${encodeURIComponent(ch.id)}`, 'PATCH',
        { title: W().assistedEnableProofTitle, description: W().assistedEnableProofText }, { capability: 'assisted' });
      if (res === null) _say(box, 'cancelled', W().assistedNoPasscode);
      else if (res.ok) { ch.capability = 'assisted'; if (res.body && res.body.publish) ch.publish = res.body.publish; if (onDone) onDone(); return; }
      else _say(box, 'failed', (res.body && res.body.error) || `${W().assistedEnableFailed} (HTTP ${res.status})`);
    } catch (e) {
      _say(box, 'failed', `${W().assistedEnableFailed} ${e && e.message ? e.message : ''}`.trim());
    }
    button.disabled = false;
  };
}

window.DeskV1AssistedPost = Object.freeze({ isAssisted, canEnable, fillHTML, bindFill, mount, statusWord, accountHTML, bindAccount });

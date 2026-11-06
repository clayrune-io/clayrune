// First-run setup step: "Help count Clayrune installs" (daily update-check ping,
// mc/update_ping.py). Its own module so first-run.js only lists it in SETUP_STEPS
// and calls the three hooks below.
//
// ON by default (Ron, 2026-11-05), but nothing is sent until the user has seen
// the text. Leaving the step posts the answer to /api/update-ping/choice, which
// also records the disclosure as seen. That save is awaited: a failed save keeps
// the person on this step with the error shown, never advancing as if it worked
// (a failed OFF would otherwise leave the server ON while the UI moves on).
import { UPDATE_PING_DISCLOSURE, UPDATE_PING_HOWTO_OFF, updatePingChoose } from './update-ping-notice.js';

let _on = true;
let _saving = false;
let _error = '';

// Called by startFirstRun: reflect what is already saved (a re-run from Settings).
export function resetUpdatePingStep() {
  _on = !(typeof _globalConfig === 'object' && _globalConfig && _globalConfig.update_ping_enabled === false);
  _saving = false;
  _error = '';
}

function _statusHTML() {
  return `<div class="setup-footer-note" id="setup-ping-status" role="status"${_error ? ' style="color:var(--red-text,#c0392b)"' : ''}>${esc(_error)}</div>`;
}

function _paintStatus() {
  const el = document.getElementById('setup-ping-status');
  if (!el) return;
  el.textContent = _saving ? 'Saving…' : _error;
  el.style.color = _error && !_saving ? 'var(--red-text,#c0392b)' : '';
}

function _lockButtons(locked) {
  document.querySelectorAll('#setup-overlay .wt-actions button').forEach(b => { b.disabled = locked; });
}

export const UPDATE_PING_SETUP_STEP = {
  id: 'update-ping',
  title: 'Help count Clayrune installs',
  wide: true,
  body: () => `<div class="setup-lead">${esc(UPDATE_PING_DISCLOSURE)}</div>
      <div class="setup-section" style="display:flex;align-items:center;justify-content:space-between;gap:12px">
        <div class="setup-section-label" style="margin:0">Send the daily update check</div>
        <div class="settings-toggle ${_on ? 'on' : ''}" id="setup-ping-toggle" role="switch" aria-checked="${_on}" tabindex="0" onclick="setupTogglePing(this)"></div>
      </div>
      <div class="setup-footer-note">${esc(UPDATE_PING_HOWTO_OFF)}</div>
      ${_statusHTML()}`,
};

// Leaving the step. Resolves true only when the answer was saved; the caller
// must not advance on false. A second call while a save is in flight is refused.
export async function commitUpdatePingStep() {
  if (_saving) return false;
  _saving = true;
  _error = '';
  _lockButtons(true);
  _paintStatus();
  let saved = false;
  try { saved = await updatePingChoose(_on); } catch (_) { saved = false; }
  _saving = false;
  _lockButtons(false);
  if (!saved) {
    _error = 'Could not save your choice. Try again.';
    _paintStatus();
    return false;
  }
  _error = '';
  return true;
}

function setupTogglePing(el) {
  if (_saving) return;
  _on = !_on;
  _error = '';
  el.classList.toggle('on', _on);
  el.setAttribute('aria-checked', String(_on));
  _paintStatus();
}

window.setupTogglePing = setupTogglePing;       // interop: step toggle generated onclick

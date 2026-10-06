// ── Human-proof guard — shared passcode modal (MC-995) ──────────────────────
//
// Every route in docs/HUMAN_PROOF_GUARD_SPEC.md §1a/§1b now requires the
// retyped LOCAL DASHBOARD PASSCODE server-side (`_require_human_passcode`,
// secrets_routes.py) in addition to whatever Origin-derived check it already
// had — an agent's own curl can forge an Origin header, it cannot produce a
// value that only exists in a human's head. This file is the ONE modal every
// dashboard call site uses to collect that passcode, so the UI half of
// MC-995 is one implementation, not 27 copies.
//
// Modeled directly on conversation.js's attendOnceSession/
// submitAttendOncePasscode (the first route this pattern shipped for,
// MC-994 follow-up) and secrets-panel.js's vault-lock forms — same DOM
// shape, same never-cache-the-passcode discipline. Duplicated rather than
// imported: these are separate top-level modules and the pattern is small
// enough that copying it once more into a shared file (instead of a fourth
// copy inline) is the generalization this task asked for.
//
// Usage:
//   const result = await humanProofFetch(url, {
//     method: 'POST', // or PUT/DELETE/PATCH
//     body: { ...whatever the route expects, WITHOUT a passcode key... },
//   }, {
//     title: 'Delete workflow',
//     description: 'Re-enter your dashboard passcode to delete this workflow.',
//   });
//   if (result === null) return; // user cancelled — no request was sent
//   if (!result.ok) { /* result.status, result.body.error */ }
//   else { /* result.body is the parsed JSON the route returned */ }
//
// A route that answers with a stream (the MCP URL installer's SSE) passes
// `stream: true` in the third argument: once the passcode is accepted the
// still-unread Response comes back as `result.response` to read itself. The
// guard's own refusals are plain JSON on every route, so they still re-prompt.
//
// The passcode never leaves this function's local scope: it is read from the
// DOM once per submit, merged into the outgoing body, and the input is
// cleared immediately after — never written to localStorage, sessionStorage,
// or a variable that outlives the call.

let _hpModalSeq = 0;

function _hpErrorText(out) {
  if (!out) return 'Request failed.';
  if (out.error === 'bad_passcode') return 'Wrong dashboard passcode.';
  if (out.error === 'too_many_attempts') return out.message || 'Too many attempts — wait a few minutes and try again.';
  return out.message || out.error || 'Request failed.';
}

// Renders either the normal "re-enter your passcode" form, or — when the
// server has no passcode configured at all yet — a "set one now" form
// (spec §3 Option A "fresh install, no passcode set" case, MC-995 build
// step 3). Both post through the same modal so the caller never has to
// special-case a fresh install.
function _hpRenderBody(modalId, mode, description, errorText) {
  const errHtml = errorText
    ? `<div style="font-size:11px;color:var(--danger,#c94a3a)">${esc(errorText)}</div>` : '';
  if (mode === 'set') {
    return `
      <div style="font-size:11px;color:var(--text-faint);line-height:1.55">
        No dashboard passcode is set yet. This action needs one to prove a
        human (not an agent) is confirming it — set one now and this action
        will continue right after.
      </div>
      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">New dashboard passcode</label>
        <input type="password" id="hp-new-${modalId}" autocomplete="new-password"
          style="width:100%;padding:7px 10px;font-size:13px;background:var(--surface2);
                 border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)"
          onkeydown="if(event.key==='Enter')_hpSubmit('${modalId}')">
      </div>
      ${errHtml}
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn-secondary" onclick="_hpCancel('${modalId}')">Cancel</button>
        <button class="btn-add" onclick="_hpSubmit('${modalId}')">Set passcode &amp; continue</button>
      </div>`;
  }
  return `
    <div style="font-size:11px;color:var(--text-faint);line-height:1.55">${esc(description)}</div>
    <div>
      <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Dashboard passcode</label>
      <input type="password" id="hp-passcode-${modalId}" autocomplete="current-password"
        style="width:100%;padding:7px 10px;font-size:13px;background:var(--surface2);
               border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)"
        onkeydown="if(event.key==='Enter')_hpSubmit('${modalId}')">
    </div>
    ${errHtml}
    <div style="display:flex;gap:8px;justify-content:flex-end">
      <button class="btn-secondary" onclick="_hpCancel('${modalId}')">Cancel</button>
      <button class="btn-add" onclick="_hpSubmit('${modalId}')">Confirm</button>
    </div>`;
}

const _hpPending = new Map(); // modalId -> { url, fetchOptions, bodyObj, title, description, mode, resolve }

// Drives `body.human-proof-active .modal-layer` (app.css) so the prompt's
// own layer outranks whatever fixed overlay summoned it (persona editor,
// workflow-builder menus, ...) — see Fenn R1. Keyed off _hpPending.size
// rather than a bool so it survives more than one prompt ever existing at
// once without going stale.
function _hpSyncBodyClass() {
  document.body.classList.toggle('human-proof-active', _hpPending.size > 0);
}

function _hpShow(modalId) {
  const p = _hpPending.get(modalId);
  if (!p) return;
  _hpSyncBodyClass();
  let win = document.querySelector(`[data-modal-id="${modalId}"]`);
  if (!win) {
    win = document.createElement('div');
    win.className = 'modal-window';
    win.dataset.modalId = modalId;
    const content = document.createElement('div');
    content.className = 'modal-content';
    _clampModalSize(content, 420);
    win.appendChild(content);
    document.getElementById('modal-layer').appendChild(win);
    const z = nextModalZ++;
    win.style.zIndex = z;
    openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
    centerModalElement(win);
  }
  const content = win.querySelector('.modal-content');
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">&#x1F512; ${esc(p.title)}</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-close" onclick="_hpCancel('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div id="hp-body-${modalId}" style="padding:4px 24px 20px 28px;display:flex;flex-direction:column;gap:14px">
      ${_hpRenderBody(modalId, p.mode, p.description, p.errorText)}
    </div>`;
  focusModal(modalId);
  const focusEl = document.getElementById(p.mode === 'set' ? `hp-new-${modalId}` : `hp-passcode-${modalId}`);
  if (focusEl) focusEl.focus();
}

// Checks current passcode-configured state before showing anything, so a
// fresh install goes straight to "set one" instead of asking for a passcode
// that cannot exist yet (avoids a pointless round trip through bad_passcode).
async function humanProofFetch(url, fetchOptions, opts) {
  // Learn practice (static/js/learn-practice.js) answers or refuses every /api
  // call in this tab, so nothing sent from here can reach the server. There is
  // no gate to prove a human to, and a passcode prompt would only teach a
  // lesson to type one. The server-side guard is untouched.
  if (window.LearnPractice && window.LearnPractice.active) {
    const resp = await fetch(url, Object.assign({}, fetchOptions || {}, {
      method: (fetchOptions && fetchOptions.method) || 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, (fetchOptions && fetchOptions.headers) || {}),
    }));
    return { ok: resp.ok, status: resp.status, body: await resp.json().catch(() => ({})) };
  }
  const modalId = '__human-proof-' + (++_hpModalSeq);
  const title = (opts && opts.title) || 'Confirm';
  const description = (opts && opts.description)
    || 'Re-enter your dashboard passcode to confirm this is you, not an agent.';
  let mode = 'passcode';
  try {
    const st = await (await fetch(API_BASE + '/api/local-auth/status')).json();
    if (!st.configured) mode = 'set';
  } catch (_) {
    // Can't reach the status route — fall back to the normal form; the
    // server-side check on submit is the real gate either way.
  }
  return new Promise((resolve) => {
    _hpPending.set(modalId, {
      url, fetchOptions: fetchOptions || {}, title, description, mode,
      stream: !!(opts && opts.stream), errorText: '', resolve,
    });
    _hpShow(modalId);
  });
}
window.humanProofFetch = humanProofFetch;

function _hpCleanup(modalId, result) {
  const p = _hpPending.get(modalId);
  _hpPending.delete(modalId);
  _hpSyncBodyClass();
  closeModalById(modalId);
  if (p) p.resolve(result);
}

function _hpCancel(modalId) {
  _hpCleanup(modalId, null);
}
window._hpCancel = _hpCancel;

// Fenn R2: Escape and other generic close paths (Home, mobile back) call
// closeModalById directly, never _hpCancel — without this hook the pending
// promise was left unresolved forever and the caller (Save, a settings
// toggle, ...) stayed stuck in its busy/disabled state. modal-manager.js
// calls this from closeModalById for any '__human-proof-' modal id.
// _hpPending is deleted BEFORE closeModalById runs in _hpCleanup above, so
// when THAT path re-enters here the map lookup below is already empty and
// this is a no-op — safe against the two paths calling each other.
function _hpTeardown(modalId) {
  const p = _hpPending.get(modalId);
  if (!p) return;
  _hpPending.delete(modalId);
  _hpSyncBodyClass();
  p.resolve(null);
}
window._hpTeardown = _hpTeardown;

async function _hpSubmit(modalId) {
  const p = _hpPending.get(modalId);
  if (!p) return;
  if (p.mode === 'set') {
    const input = document.getElementById(`hp-new-${modalId}`);
    const newPasscode = (input && input.value) || '';
    if (input) input.value = '';
    if (!newPasscode) { p.errorText = 'A passcode is required.'; _hpShow(modalId); return; }
    let setResp;
    try {
      setResp = await fetch(API_BASE + '/api/local-auth/set', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ passcode: newPasscode }),
      });
    } catch (e) {
      p.errorText = 'Could not reach the server: ' + e.message;
      _hpShow(modalId);
      return;
    }
    const setBody = await setResp.json().catch(() => ({}));
    if (!setResp.ok) {
      p.errorText = setBody.error === 'passcode_too_short'
        ? `Passcode must be at least ${setBody.min || 6} characters.`
        : (setBody.message || setBody.error || 'Could not set a passcode.');
      _hpShow(modalId);
      return;
    }
    // Passcode is now configured — fall through to the normal confirm step
    // with it pre-filled, so the user isn't asked to type it twice.
    p.mode = 'passcode';
    p.errorText = '';
    _hpShow(modalId);
    const passInput = document.getElementById(`hp-passcode-${modalId}`);
    if (passInput) passInput.value = newPasscode;
    return;
  }

  const passcodeInput = document.getElementById(`hp-passcode-${modalId}`);
  const passcode = (passcodeInput && passcodeInput.value) || '';
  if (passcodeInput) passcodeInput.value = ''; // read once, forget immediately
  if (!passcode) { p.errorText = 'Dashboard passcode required.'; _hpShow(modalId); return; }

  let bodyObj = {};
  if (p.fetchOptions.body) {
    try { bodyObj = JSON.parse(p.fetchOptions.body); } catch (_) { bodyObj = {}; }
  }
  bodyObj = Object.assign({}, bodyObj, { passcode });

  let resp;
  try {
    resp = await fetch(p.url, Object.assign({}, p.fetchOptions, {
      method: p.fetchOptions.method || 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, p.fetchOptions.headers || {}),
      body: JSON.stringify(bodyObj),
    }));
  } catch (e) {
    p.errorText = 'Could not reach the server: ' + e.message;
    _hpShow(modalId);
    return;
  }
  if (p.stream && resp.ok) {
    _hpCleanup(modalId, { ok: true, status: resp.status, body: {}, response: resp });
    return;
  }
  const body = await resp.json().catch(() => ({}));
  // Only the guard's OWN refusal re-prompts here — it always answers with
  // exactly these (status, error) pairs (`_require_human_passcode`). Any
  // other response (2xx, or a downstream business-logic status like the
  // team-card's 409 name conflict) means the passcode was accepted and the
  // real route ran, so it must reach the caller, not be swallowed as an
  // auth failure.
  const isGuardFailure =
    (resp.status === 403 && (body.error === 'passcode_required' || body.error === 'bad_passcode')) ||
    (resp.status === 429 && body.error === 'too_many_attempts');
  if (isGuardFailure) {
    if (body.error === 'passcode_required') {
      // Passcode was cleared between the initial status check and this
      // submit (a race, or the status fetch failed silently) — recover by
      // switching to the set-passcode form instead of a dead-end error.
      p.mode = 'set';
      p.errorText = '';
      _hpShow(modalId);
      return;
    }
    p.errorText = _hpErrorText(body);
    _hpShow(modalId);
    return;
  }
  _hpCleanup(modalId, { ok: resp.ok, status: resp.status, body });
}
window._hpSubmit = _hpSubmit;

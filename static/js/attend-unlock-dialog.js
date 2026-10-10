// MC-1084: dialog markup lives separately from the conversation renderer.
// The existing server-side passcode gate and submit/re-arm handlers own grants.
// Re-entry is verified by _require_human_passcode server-side on every grant;
// an Origin header alone was forgeable by an agent's curl (MC-994 review).
export function openAttendUnlockDialog(sid) {
  const modalId = '__attend-once_' + sid;
  if (openModals.has(modalId)) { focusModal(modalId); return; }
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content modal-compact';
  _clampModalSize(content, 420);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">&#x1F513; Unlock this chat</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div style="padding:4px 24px 20px 28px;display:flex;flex-direction:column;gap:14px">
      <div style="font-size:11px;color:var(--text-faint);line-height:1.55">
        Re-enter your dashboard passcode. <b>Allow once</b> lets the next
        eligible fence-blocked action in this chat through, then the fence re-arms.
        <b>Allow 10 min</b> lets eligible blocked actions through until the time is
        up, or until you Re-arm.
      </div>
      <div class="attend-unlock-limits" style="font-size:11px;color:var(--text);line-height:1.55">
        An unlock cannot cover browser-pane or other HTTP calls made with curl
        or similar tools, shell commands with chaining or special shell symbols,
        or commands run through another shell or an interpreter.
        Open a fresh chat yourself for those actions.
      </div>
      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Dashboard passcode</label>
        <input ${window.MaskedInput.attrs()} id="ao-passcode-${esc(sid)}"
          style="width:100%;padding:7px 10px;font-size:13px;background:var(--surface2);
                 border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)"
          onkeydown="if(event.key==='Enter')submitAttendOncePasscode('${modalId}','${esc(sid)}')">
      </div>
      <div id="ao-status-${esc(sid)}" style="font-size:11px;color:var(--danger,#c94a3a);min-height:14px"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap">
        <button class="btn-secondary" onclick="closeModalById('${modalId}')">Cancel</button>
        <button class="btn-secondary attend-window-btn" onclick="submitAttendOncePasscode('${modalId}','${esc(sid)}','window')">Allow 10 min</button>
        <button class="btn-add" onclick="submitAttendOncePasscode('${modalId}','${esc(sid)}','once')">Allow once</button>
      </div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
  document.getElementById(`ao-passcode-${sid}`).focus();
}

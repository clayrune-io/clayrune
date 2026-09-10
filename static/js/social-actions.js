// ── Social approvals queue actions ──────────────────────────────────────────
// Mirrors backlog-actions.js: same reload-after-mutation shape, same
// patch-then-refetch pattern. Phase 1 only — Release marks a draft approved
// and hands Ron the copy; nothing here calls an outbound posting API.

async function _reloadSocialAfterMutation(projectId) {
  if (typeof refreshProjectSocialQueue === 'function') await refreshProjectSocialQueue(projectId);
  await refreshSilent();
}

function setSocialFilter(projectId, filter) {
  socialFilterMap[projectId] = filter;
  refreshModal();
}

async function patchSocialItem(projectId, itemId, data) {
  await fetch(API_BASE + `/api/project/${projectId}/social/queue/${itemId}`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data)
  });
  await _reloadSocialAfterMutation(projectId);
}

async function saveSocialBody(e, projectId, itemId) {
  const body = e.target.innerText.trim();
  if (!body) return;
  await patchSocialItem(projectId, itemId, {body});
}

// "Edit" opens a real modal window rather than focusing the inline
// contenteditable field. That field used to be the whole edit surface, but it
// lives inside a container (#asl-list / the project modal's Social tab) that
// gets replaced wholesale on every repaint — progressive hydration, project
// polls, the Desk's in-flight poll (see deferRepaintWhileTyping in
// cross-social.js, the stopgap this replaces). A modal outside that container
// no longer shares a lifetime with the list, so a repaint mid-edit can't
// destroy it.
function editSocialItem(e, projectId, itemId) {
  e.stopPropagation();
  openSocialEditModal(projectId, itemId);
}

// Same modal-window construction as deskNewCampaign/deskVoices (desk.js):
// `.modal-window` + `.modal-content`, registered in `openModals` so drag,
// zoom (Ctrl+Scroll), minimize and z-order all come for free from the
// delegated listeners in interactions.js/modal-manager.js — never hand-roll
// a floating div here.
function openSocialEditModal(projectId, itemId) {
  const proj = (typeof allProjects !== 'undefined' ? allProjects : []).find(p => p.id === projectId);
  const item = proj && Array.isArray(proj.social_queue)
    ? proj.social_queue.find(i => i.id === itemId) : null;
  if (!item) {
    if (typeof showToast === 'function') showToast('Could not find this draft — try reopening the queue.', 4000);
    return;
  }

  const modalId = `__social_edit_${itemId}`;
  if (openModals.has(modalId)) { focusModal(modalId); return; }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  // `modal-fit` drops the inherited 80vh height so the box hugs its content —
  // the campaign form left dead space under the button before this.
  content.className = 'modal-content modal-fit';
  _clampModalSize(content, 640);
  content.innerHTML = `
    <div class="modal-header" style="padding:18px 24px 10px 28px;position:relative">
      <div class="modal-window-controls" style="position:absolute;top:14px;right:16px;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
      <h2 style="margin:0;font-size:17px;font-weight:700;color:var(--text)">Edit draft</h2>
      <div style="font-size:11px;color:var(--text-faint);margin-top:2px">${esc(item.platform || 'unspecified')}</div>
    </div>
    <div style="padding:6px 28px 22px;display:flex;flex-direction:column;gap:12px;overflow-y:auto">
      <div class="form-group" style="margin:0">
        <label>Body</label>
        <textarea id="social-edit-body-${esc(itemId)}" rows="12"
          spellcheck="true" style="min-height:280px">${esc(item.body || '')}</textarea>
      </div>
      ${item.teaching ? `<div class="social-teaching">${esc(item.teaching)}</div>` : ''}
      <div class="form-group" style="margin:0">
        <label>Note back to the agent</label>
        <input type="text" id="social-edit-note-${esc(itemId)}" placeholder="Note back to the agent"
          value="${esc(item.note || '')}">
      </div>
      <div id="social-edit-error-${esc(itemId)}" class="social-attr-warn" style="display:none"></div>
      <button class="btn-add" style="width:100%" onclick="saveSocialEditModal('${esc(projectId)}','${esc(itemId)}')">Save</button>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
  const ta = document.getElementById(`social-edit-body-${itemId}`);
  if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
}

// Saves through the SAME endpoint the inline field always used
// (patchSocialItem -> PATCH /api/project/<pid>/social/queue/<id>) — this is
// the Desk's learning loop (mc/blueprints/project_routes.py calls
// desk.record_edit when the body changes), so there is no separate write path
// to keep in sync.
async function saveSocialEditModal(projectId, itemId) {
  const errEl = document.getElementById(`social-edit-error-${itemId}`);
  const show = (m) => { if (errEl) { errEl.textContent = m; errEl.style.display = 'block'; } };
  const bodyEl = document.getElementById(`social-edit-body-${itemId}`);
  const noteEl = document.getElementById(`social-edit-note-${itemId}`);
  const body = bodyEl ? bodyEl.value.trim() : '';
  if (!body) { show('The draft cannot be empty.'); return; }
  const note = noteEl ? noteEl.value.trim() : '';
  await patchSocialItem(projectId, itemId, {body, note});
  closeModalById(`__social_edit_${itemId}`);
  if (typeof showToast === 'function') showToast('Draft saved.');
}

async function releaseSocialItem(e, projectId, itemId) {
  e.stopPropagation();
  const res = await fetch(API_BASE + `/api/project/${projectId}/social/queue/${itemId}/approve`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({decided_by: 'user'})
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast(data.error || 'Could not release this draft.', 4000);
    return;
  }
  await _reloadSocialAfterMutation(projectId);
}

// "Mark as posted" — the receipt. Released means Ron approved the copy; POSTED
// means it actually went out, and only this writes the story ledger.
//
// Until 2026-09-10 nothing did: `approve` flipped the status and the ledger was
// written by its own unit tests and nothing else. That silently disabled the
// repetition guard (nothing to compare against), left the Calendar and Ledger
// surfaces permanently empty, and gave engagement no row to attach to.
//
// The permalink is asked for but not required. It is what makes reactions
// readable later, so a skipped one is reported rather than silently accepted.
async function markSocialItemPosted(e, projectId, itemId) {
  e.stopPropagation();
  const url = (prompt(
    'Paste the link to the post.\n\n' +
    'Leave blank if you do not have it — the post is still recorded, but its ' +
    'reactions can never be read back without a link.') || '').trim();

  const res = await fetch(API_BASE + `/api/project/${projectId}/social/queue/${itemId}/posted`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({url}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast(data.error || 'Could not record this as posted.', 4000);
    return;
  }
  if (data.already) showToast('Already recorded.');
  else if (!data.ledger_written) showToast('Marked posted, but the ledger write failed.', 5000);
  else if (!data.reactions_readable) showToast('Recorded. No link, so reactions cannot be read later.', 5000);
  else showToast('Recorded in the ledger.');
  await _reloadSocialAfterMutation(projectId);
}

// Push back MUST capture the note as part of the click, not read it from a
// field the user may never have touched. Measured 2026-09-10 on
// mission_control queue item 9b8bb91e: pushed back via the old inline
// `social-note-<id>` field, landed in needs_changes with note: '' — Posy was
// told "wrong" and given no instruction. Same modal-window construction as
// openSocialEditModal (commit 0adb8ed); this one exists to force the note,
// not to edit the body.
function pushBackSocialItem(e, projectId, itemId) {
  e.stopPropagation();
  openPushBackModal(projectId, itemId);
}

function openPushBackModal(projectId, itemId) {
  const proj = (typeof allProjects !== 'undefined' ? allProjects : []).find(p => p.id === projectId);
  const item = proj && Array.isArray(proj.social_queue)
    ? proj.social_queue.find(i => i.id === itemId) : null;
  if (!item) {
    if (typeof showToast === 'function') showToast('Could not find this draft — try reopening the queue.', 4000);
    return;
  }

  const modalId = `__social_pushback_${itemId}`;
  if (openModals.has(modalId)) { focusModal(modalId); return; }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content modal-fit';
  _clampModalSize(content, 480);
  content.innerHTML = `
    <div class="modal-header" style="padding:18px 24px 10px 28px;position:relative">
      <div class="modal-window-controls" style="position:absolute;top:14px;right:16px;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
      <h2 style="margin:0;font-size:17px;font-weight:700;color:var(--text)">Push back to the writer</h2>
      <div style="font-size:11px;color:var(--text-faint);margin-top:2px">${esc(item.platform || 'unspecified')}</div>
    </div>
    <div style="padding:6px 28px 22px;display:flex;flex-direction:column;gap:12px">
      <div class="backlog-text" style="max-height:120px;overflow-y:auto;opacity:.7;cursor:default">${esc(item.body || '')}</div>
      <div class="form-group" style="margin:0">
        <label>What should change (required)</label>
        <textarea id="social-pushback-note-${esc(itemId)}" rows="4"
          placeholder="Tell the writer what's wrong and what to do instead">${esc((item.note || '').trim())}</textarea>
      </div>
      <div id="social-pushback-error-${esc(itemId)}" class="social-attr-warn" style="display:none"></div>
      <button class="btn-social-pushback" style="width:100%" onclick="submitPushBack('${esc(projectId)}','${esc(itemId)}')">Push back</button>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
  const ta = document.getElementById(`social-pushback-note-${itemId}`);
  if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
}

async function submitPushBack(projectId, itemId) {
  const errEl = document.getElementById(`social-pushback-error-${itemId}`);
  const show = (m) => { if (errEl) { errEl.textContent = m; errEl.style.display = 'block'; } };
  const noteEl = document.getElementById(`social-pushback-note-${itemId}`);
  const note = noteEl ? noteEl.value.trim() : '';
  if (!note) { show("Say what should change — an empty push-back tells the writer nothing."); return; }
  const res = await fetch(API_BASE + `/api/project/${projectId}/social/queue/${itemId}/reject`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({note, decided_by: 'user'})
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    show(data.error || 'Could not push this draft back.');
    return;
  }
  closeModalById(`__social_pushback_${itemId}`);
  if (typeof showToast === 'function') showToast('Pushed back with your note.');
  await _reloadSocialAfterMutation(projectId);
}

// A pushed-back draft used to vanish from every queue render — it still
// existed in social_queue but no view showed `needs_changes`, so an
// accidental push-back read as destroyed. Restores via the same PATCH the
// inline field already uses; no new endpoint.
async function restoreSocialItem(e, projectId, itemId) {
  e.stopPropagation();
  await patchSocialItem(projectId, itemId, {status: 'pending'});
  if (typeof showToast === 'function') showToast('Restored to pending.');
}

// ── Interop: re-expose for inline onclick/onblur handlers generated in the
//    Social tab HTML (render-core.js). Same rationale as backlog-actions.js. ──
window.setSocialFilter = setSocialFilter;
window.patchSocialItem = patchSocialItem;
window.saveSocialBody = saveSocialBody;
window.editSocialItem = editSocialItem;
window.openSocialEditModal = openSocialEditModal;
window.saveSocialEditModal = saveSocialEditModal;
window.releaseSocialItem = releaseSocialItem;
window.markSocialItemPosted = markSocialItemPosted;
window.pushBackSocialItem = pushBackSocialItem;
window.openPushBackModal = openPushBackModal;
window.submitPushBack = submitPushBack;
window.restoreSocialItem = restoreSocialItem;

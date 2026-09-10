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

// "Edit" just focuses the already-editable body field — the field is
// contenteditable and auto-saves on blur (same interaction as a backlog
// item's text), so there is no separate edit mode to enter.
function editSocialItem(e, projectId, itemId) {
  e.stopPropagation();
  const el = document.getElementById(`social-body-${itemId}`);
  if (el) el.focus();
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

async function pushBackSocialItem(e, projectId, itemId) {
  e.stopPropagation();
  const noteInput = document.getElementById(`social-note-${itemId}`);
  const note = noteInput ? noteInput.value.trim() : '';
  const res = await fetch(API_BASE + `/api/project/${projectId}/social/queue/${itemId}/reject`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({note, decided_by: 'user'})
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast(data.error || 'Could not push this draft back.', 4000);
    return;
  }
  await _reloadSocialAfterMutation(projectId);
}

// ── Interop: re-expose for inline onclick/onblur handlers generated in the
//    Social tab HTML (render-core.js). Same rationale as backlog-actions.js. ──
window.setSocialFilter = setSocialFilter;
window.patchSocialItem = patchSocialItem;
window.saveSocialBody = saveSocialBody;
window.editSocialItem = editSocialItem;
window.releaseSocialItem = releaseSocialItem;
window.markSocialItemPosted = markSocialItemPosted;
window.pushBackSocialItem = pushBackSocialItem;

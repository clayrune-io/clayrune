// ── Cross-project Social Approvals view ─────────────────────────────────────
// The Social queue is per-project (mc/blueprints/project_routes.py), same as
// Backlog, and this view mirrors cross-backlog.js's shape exactly: fetch every
// project's items on open rather than on every 30s poll, mark the project
// `_socialQueueFull` so `_preserveOpenSocial` (index.html) keeps them across a
// refresh, and re-render in place while open.
//
// Scoped to PENDING drafts only — the ask this shipped from ("one pane listing
// every project's pending drafts") and also the honest limit of what
// /api/projects tells us up front: it ships `social_pending_count`, not a
// total, so hydration below can only gate on "this project has pending work"
// without silently under-fetching a Decided/All view for a project that has
// zero pending items but does have decided ones. Add a `social_total_count`
// summary field first if this needs to grow into Backlog's Open/Done/All shape.
let _allSocialFilter = { search: '' };

let _allSocialHydrating = false;
async function _hydrateAllSocial() {
  if (_allSocialHydrating) return;
  _allSocialHydrating = true;
  try {
    const need = allProjects.filter(p => !p._socialQueueFull &&
                                         (p.social_pending_count || 0) > 0);
    for (let i = 0; i < need.length; i += 4) {
      await Promise.all(need.slice(i, i + 4).map(async p => {
        try {
          const res = await fetch(API_BASE +
            `/api/project/${encodeURIComponent(p.id)}/social/queue`);
          if (!res.ok) return;
          p.social_queue = await res.json();
          p._socialQueueFull = true;
        } catch (e) {}
      }));
      if (openModals.has('__all_social')) renderAllSocial();  // progressive fill
    }
  } finally {
    _allSocialHydrating = false;
  }
}

async function openAllSocial() {
  const modalId = '__all_social';
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    renderAllSocial();
    _hydrateAllSocial();
    return;
  }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 860);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">Social — Pending Across Projects</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div style="padding:4px 24px 20px 28px">
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
        <input type="text" id="asl-search" placeholder="Search draft text..." value="${esc(_allSocialFilter.search)}"
          style="flex:1;min-width:180px;padding:6px 10px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text)"
          oninput="_allSocialFilter.search=this.value;renderAllSocial()">
        <span id="asl-count" style="font-size:11px;color:var(--text-faint)"></span>
      </div>
      <div id="asl-list" style="max-height:65vh;overflow-y:auto"></div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);

  renderAllSocial();
  _hydrateAllSocial();
}

function renderAllSocial() {
  const container = document.getElementById('asl-list');
  const countEl = document.getElementById('asl-count');
  if (!container) return;
  const q = (_allSocialFilter.search || '').trim().toLowerCase();
  const rows = [];
  for (const p of allProjects) {
    if (!p._socialQueueFull || !Array.isArray(p.social_queue)) continue;
    for (const item of p.social_queue) {
      if (item.status !== 'pending') continue;
      if (q && !(item.body || '').toLowerCase().includes(q)) continue;
      rows.push({ p, item });
    }
  }
  rows.sort((a, b) => (b.item.created_at || '').localeCompare(a.item.created_at || ''));
  if (countEl) countEl.textContent = `${rows.length} pending draft${rows.length===1?'':'s'}`;
  if (!rows.length) {
    const msg = _allSocialHydrating ? 'Loading pending drafts…' : 'Nothing pending across any project.';
    container.innerHTML = `<div style="padding:40px 12px;text-align:center;color:var(--text-faint);font-size:12px">${msg}</div>`;
    return;
  }
  // The teaching block (docs/THE_DESK_SPEC.md, "Teacher") renders UNDER the body
  // rather than in a tooltip or a detail view on purpose: Ron reads it while
  // DECIDING, and that is the only moment at which it teaches anything. Absent
  // on human-authored and pre-Desk drafts, which is why it is optional.
  //
  // Same markup as a project modal's Social tab (render-core.js) — same
  // .backlog-item/.social-item classes, same edit/release/push-back actions,
  // each scoped to `p.id` so a row acts on the RIGHT project regardless of
  // which one happens to be open elsewhere on screen.
  container.innerHTML = rows.map(({ p, item }) => {
    const missingAttribution = item.originated && !(item.body || '').includes(SOCIAL_ATTRIBUTION_LINE);
    return `
    <div class="backlog-item social-item status-${esc(item.status)}" data-item-id="${esc(item.id)}">
      <div style="flex:1;min-width:0">
        <div class="social-item-head">
          ${socialProjectBadgeHTML(item)}
          <span class="status-badge social-platform-badge">${esc(item.platform || 'unspecified')}</span>
          ${!item.originated ? '<span class="backlog-source">reply</span>' : ''}
        </div>
        <div class="backlog-text" id="social-body-${esc(item.id)}" contenteditable="true" spellcheck="true"
          onblur="saveSocialBody(event,'${esc(p.id)}','${esc(item.id)}')"
        >${esc(item.body)}</div>
        ${item.teaching ? `<div class="social-teaching">${esc(item.teaching)}</div>` : ''}
        ${missingAttribution ? `<div class="social-attr-warn">Missing the line "${esc(SOCIAL_ATTRIBUTION_LINE)}" — Release will be refused until it's added.</div>` : ''}
        <div class="note-input-row">
          <input type="text" id="social-note-${esc(item.id)}" placeholder="Note back to the agent"
            value="${esc(item.note || '')}"
            onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur()}">
        </div>
      </div>
      <div class="backlog-meta social-item-actions">
        <button class="btn-social-edit" onclick="editSocialItem(event,'${esc(p.id)}','${esc(item.id)}')" title="Edit the draft">Edit</button>
        <button class="btn-social-release" onclick="releaseSocialItem(event,'${esc(p.id)}','${esc(item.id)}')" title="Approve and hand off the copy">Release</button>
        <button class="btn-social-pushback" onclick="pushBackSocialItem(event,'${esc(p.id)}','${esc(item.id)}')" title="Send back with the note above">Push back</button>
      </div>
    </div>`;
  }).join('');
}

// ── Interop: re-expose for inline/cross-module callers. Same rationale as
//    cross-backlog.js's block below. ──
//   • _allSocialFilter — object-identity bridge for the generated oninput
//     handler (`_allSocialFilter.search=this.value; renderAllSocial()`).
//   • openAllSocial — sidebarNav('social') (runtime).
//   • renderAllSocial — the central render() calls it, guarded by
//     `openModals.has('__all_social')`; also called by openAllSocial and by
//     _hydrateAllSocial's progressive fill.
//   • _hydrateAllSocial — the Desk's Queue surface (desk.js) hosts these same
//     rows, and ES modules do not share top-level names across files.
window._hydrateAllSocial = _hydrateAllSocial;
window._allSocialFilter = _allSocialFilter;
window.openAllSocial = openAllSocial;
window.renderAllSocial = renderAllSocial;

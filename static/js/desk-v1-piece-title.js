// Piece names share the family's title everywhere; edits use the Desk undo bus.
// The title itself is the control: click (or Enter/Space on it) swaps it for an
// input in place. Enter or leaving the field saves, Esc cancels.
(function () {
  // familyId -> { text, status, saving, focus }; present only while editing, so
  // an open edit and a failed save both survive a header repaint.
  const drafts = new Map();
  let refocusTitle = null; // familyId whose title button should take focus after the next paint
  const esc = (s) => window.esc(String(s == null ? '' : s));
  const generic = (fam) => /^New (post|image|video|article|piece)$/i.test(fam.title || '');

  function html(fam) {
    if (fam.archivedAt) return `<div class="desk-v1-piece-name"><h1 class="desk-v1-piece-title">${esc(fam.title)}</h1></div>`;
    const d = drafts.get(fam.id);
    const title = d
      ? `<h1 class="desk-v1-piece-title" data-editing><input class="desk-v1-piece-title-input" data-piece-name-input maxlength="200" required
          value="${esc(d.text)}" placeholder="Give this a name" aria-label="Name" aria-describedby="desk-v1-piece-name-status" ${d.saving ? 'disabled' : ''}></h1>`
      : `<h1 class="desk-v1-piece-title"><button type="button" class="desk-v1-piece-title-btn" data-piece-title-edit
          ${generic(fam) ? 'data-generic' : ''} title="Click to rename" aria-label="${esc(`Name: ${fam.title}. Click to rename`)}">${esc(fam.title)}</button></h1>`;
    return `<div class="desk-v1-piece-name">${title}
      <span id="desk-v1-piece-name-status" class="desk-v1-piece-name-status" data-piece-name-status role="status" aria-live="polite">${esc(d ? d.status : '')}</span></div>`;
  }

  function bind(el, fam, repaint) {
    el.dataset.pieceNameId = fam.id;
    const start = el.querySelector('[data-piece-title-edit]');
    const input = el.querySelector('[data-piece-name-input]');
    const status = el.querySelector('[data-piece-name-status]');
    if (!start && !input) return;
    const current = () => (window.DeskV1Store.state().families || []).find((f) => f.id === fam.id) || fam;
    const refresh = () => {
      const mounted = document.getElementById('desk-v1-piece-header');
      const target = el.isConnected ? el : mounted?.dataset.pieceNameId === fam.id ? mounted : null;
      if (!target) return;
      if (target === el) repaint();
      else window.deskV1FillPieceHeader(target, { familyId: fam.id });
      // The shell and Posy stay mounted while the piece header repaints.
      const crumb = target.closest('.desk-v1-shell')?.querySelector('.desk-v1-crumb-title');
      if (crumb) crumb.textContent = current().title;
      const scope = document.querySelector('#desk-v1-piece-rightcol [data-scope-trigger]');
      if (scope) scope.textContent = `About: ${current().title} ▾`;
    };
    if (start) {
      start.onclick = () => {
        drafts.set(fam.id, { text: generic(fam) ? '' : fam.title, status: '', focus: true });
        refresh();
      };
      if (refocusTitle === fam.id) { refocusTitle = null; start.focus({ preventScroll: true }); }
      return;
    }
    const d = drafts.get(fam.id);
    if (d.focus && !d.saving) { input.focus({ preventScroll: true }); if (!d.status) input.select(); }
    let settled = false; // Enter/Esc already decided; the blur that follows must not decide again
    const close = (viaKey) => { drafts.delete(fam.id); if (viaKey) refocusTitle = fam.id; refresh(); };
    const commit = async (viaKey) => {
      if (settled || d.saving) return;
      const next = input.value.trim();
      if (next === fam.title) { settled = true; close(viaKey); return; }
      if (!next || next.length > 200) {
        d.text = input.value; d.status = 'Enter a name of 1–200 characters.'; status.textContent = d.status;
        return;
      }
      settled = true;
      const prev = fam.title;
      Object.assign(d, { text: next, status: 'Saving…', saving: true, focus: false });
      input.disabled = true;
      status.textContent = 'Saving…';
      const patch = (title) => () => window.deskV1QueuePiece(fam.id, () =>
        window.DeskV1Store.api('PATCH', `/api/desk/pieces/${encodeURIComponent(fam.id)}`, { title }));
      const result = await window.DeskV1Store.write({
        label: `Renamed “${prev}”`,
        apply: () => { current().title = next; },
        unapply: () => { current().title = prev; },
        request: patch(next), undoRequest: patch(prev), repaint: refresh,
      });
      if (result.ok) { drafts.delete(fam.id); if (viaKey) refocusTitle = fam.id; }
      else drafts.set(fam.id, { text: next, status: `Name not saved: ${result.error}`, focus: true });
      refresh();
    };
    input.oninput = () => { d.text = input.value; if (d.status) { d.status = ''; status.textContent = ''; } };
    input.onkeydown = (e) => {
      if (e.key === 'Enter') { e.preventDefault(); commit(true); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); settled = true; close(true); }
    };
    input.onblur = () => commit(false);
  }
  window.DeskV1PieceTitle = { html, bind };
})();

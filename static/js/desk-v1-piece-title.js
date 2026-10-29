// Piece names share the family's title everywhere; edits use the Desk undo bus.
(function () {
  const drafts = new Map();
  const esc = (s) => window.esc(String(s == null ? '' : s));
  const generic = (fam) => /^New (post|image|video|article|piece)$/i.test(fam.title || '');

  function html(fam) {
    const d = drafts.get(fam.id);
    const open = !fam.archivedAt && (!!d || generic(fam));
    return `<div class="desk-v1-piece-name">
      <div class="desk-v1-piece-name-heading"><h1 class="desk-v1-piece-title">${esc(fam.title)}</h1>
        ${!fam.archivedAt ? '<button type="button" class="btn-secondary" data-piece-name-edit aria-expanded="' + open + '">Edit name</button>' : ''}</div>
      ${!fam.archivedAt ? `<form data-piece-name-form ${open ? '' : 'hidden'}>
        <label for="desk-v1-piece-name">Post name</label>
        <div class="desk-v1-piece-name-controls"><input id="desk-v1-piece-name" data-piece-name-input maxlength="200" required
          value="${esc(d ? d.text : generic(fam) ? '' : fam.title)}" placeholder="Give this post a name" aria-describedby="desk-v1-piece-name-help" ${d?.saving ? 'disabled' : ''}>
          <button class="btn-add" type="submit" data-piece-name-save ${d?.saving ? 'disabled' : ''}>Save name</button></div>
        <p id="desk-v1-piece-name-help">A name you can recognize in What, Where and the calendar.</p>
        <span data-piece-name-status role="status" aria-live="polite">${esc(d ? d.status : '')}</span>
      </form>` : ''}</div>`;
  }

  function bind(el, fam, repaint) {
    el.dataset.pieceNameId = fam.id;
    const form = el.querySelector('[data-piece-name-form]');
    if (!form) return;
    const input = form.querySelector('input');
    const button = form.querySelector('button');
    const status = form.querySelector('[data-piece-name-status]');
    const current = () => (window.DeskV1Store.state().families || []).find((f) => f.id === fam.id) || fam;
    el.querySelector('[data-piece-name-edit]').onclick = () => {
      form.hidden = false;
      el.querySelector('[data-piece-name-edit]').setAttribute('aria-expanded', 'true');
      input.focus();
      input.select();
    };
    input.oninput = () => { drafts.set(fam.id, { text: input.value, status: 'Unsaved name' }); status.textContent = 'Unsaved name'; };
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
    form.onsubmit = async (e) => {
      e.preventDefault();
      if (button.disabled || drafts.get(fam.id)?.saving) return;
      const next = input.value.trim();
      if (!next || next.length > 200) { status.textContent = 'Enter a name of 1–200 characters.'; return; }
      if (next === fam.title) { drafts.delete(fam.id); refresh(); return; }
      const prev = fam.title;
      drafts.set(fam.id, { text: next, status: 'Saving…', saving: true });
      button.disabled = input.disabled = true;
      status.textContent = 'Saving…';
      const patch = (title) => () => window.deskV1QueuePiece(fam.id, () =>
        window.DeskV1Store.api('PATCH', `/api/desk/pieces/${encodeURIComponent(fam.id)}`, { title }));
      const result = await window.DeskV1Store.write({
        label: `Renamed “${prev}”`,
        apply: () => { current().title = next; },
        unapply: () => { current().title = prev; },
        request: patch(next), undoRequest: patch(prev), repaint: refresh,
      });
      if (result.ok) drafts.delete(fam.id);
      else drafts.set(fam.id, { text: next, status: `Name not saved: ${result.error}` });
      refresh();
    };
  }
  window.DeskV1PieceTitle = { html, bind };
})();

// Piece-page draft copy. Articles keep their Studio editor; versions keep their
// own review flow. Drafts survive piece repaints and failed writes in this tab.
(function () {
  const MAX_BODY = 100000; // mc/desk_pieces.py MAX_BODY
  const MAX_FILE_BYTES = 4 * 1024 * 1024;
  const drafts = new Map();
  let repainting = false;
  const esc = (s) => window.esc(String(s == null ? '' : s));
  const state = () => window.DeskV1Store.state();
  const channel = (id) => (state().channels || []).find((c) => c.id === id);
  const editable = (fam) => ['post', 'image', 'video'].includes(fam.kind) && !fam.archivedAt;

  function draft(fam) {
    const body = String(fam.body || '');
    let d = drafts.get(fam.id);
    if (!d) {
      d = { text: body, saved: body, status: '', failed: false, saving: null, requested: false };
      drafts.set(fam.id, d);
    } else if (!d.saving && d.text === d.saved && body !== d.saved) {
      d.text = d.saved = body;
      d.status = '';
    }
    return d;
  }

  function editorHTML(fam) {
    const d = draft(fam);
    return `<div class="desk-v1-piece-copy" data-piece-copy="${esc(fam.id)}">
      <label class="desk-v1-piece-copy-label" for="desk-v1-piece-copy-text">Draft your post</label>
      <textarea id="desk-v1-piece-copy-text" data-piece-copy-text rows="7" maxlength="${MAX_BODY}"
        placeholder="Start typing your post…" aria-describedby="desk-v1-piece-copy-help desk-v1-piece-copy-status">${esc(d.text)}</textarea>
      <div class="desk-v1-piece-copy-actions">
        <button type="button" class="btn-secondary" data-piece-copy-import>Import from file</button>
        <input type="file" data-piece-copy-file accept=".txt,.md,.markdown,.html,.htm" hidden>
        <button type="button" class="btn-add" data-piece-copy-save>Save</button>
        <span id="desk-v1-piece-copy-status" data-piece-copy-status role="status" aria-live="polite" ${d.failed ? 'data-failed="true"' : ''}>${esc(d.status)}</span>
      </div>
      <div id="desk-v1-piece-copy-help" class="desk-v1-piece-copy-help">Text, Markdown or HTML · Up to 100,000 characters. Imported text stays unsaved until you save or leave the field.</div>
    </div>`;
  }

  function html(fam) {
    const rows = (fam.versions || []).map((v) => {
      const ch = channel(v.channelId);
      const badge = ch ? window.DeskV1Kit.channelBadge(ch, {}) : '<span class="desk-v1-camp-nochannel">No channel</span>';
      const claims = (v.claims || []).length;
      const retry = window.DeskV1Store.live() && (v.state === 'held' || v.state === 'failed');
      const action = v.state === 'needs_review' || retry
        ? `<button type="button" class="desk-v1-camp-card-primary" data-review-btn="${esc(v.id)}">${retry ? 'Review and retry' : fam.kind === 'video' ? 'Watch & review' : 'Review'}</button>` : '';
      const assisted = window.DeskV1AssistedPost ? window.DeskV1AssistedPost.fillHTML(ch, v) : '';
      return `<div class="desk-v1-piece-facetrow" data-copy-row="${esc(v.id)}">
        <span>${badge}${claims ? ` · ${esc(claims)} claim${claims === 1 ? '' : 's'}` : ''}</span>
        <span class="desk-v1-piece-rowend">${window.DeskV1PostDelete.stateHTML(v)}${action}${assisted}</span>
      </div>`;
    }).join('');
    let copy;
    if (editable(fam)) copy = editorHTML(fam);
    else {
      const body = String(fam.body || '').trim();
      const excerpt = (state().contentPreview || {})[fam.id] || (body.length > 400 ? body.slice(0, 400).trimEnd() + '…' : body);
      copy = fam.kind === 'article' && !fam.archivedAt
        ? '<button type="button" class="btn-secondary" data-piece-edit-article>Edit article</button>' : '';
      if (excerpt) copy += `<div class="desk-v1-piece-excerpt" data-piece-excerpt>${esc(excerpt)}</div>`;
    }
    const camp = (state().campaigns || []).find((c) => c.id === fam.campaignId);
    return copy + (rows || `<div class="desk-v1-piece-empty">${camp
      ? '<button type="button" class="btn-secondary" data-piece-copy-where>Add a version from the Where board ›</button>'
      : 'Add this piece to a campaign in What before creating a version. <button type="button" class="btn-secondary" data-piece-copy-campaign>Choose a campaign ›</button>'}</div>`);
  }

  // Repainting through the existing piece hook must not drop typing/caret when
  // a save finishes while the user is already working on their next change.
  function repaintKeepingFocus(repaint) {
    const active = document.activeElement;
    const focused = active && active.matches('[data-piece-copy-text]');
    const selection = focused ? [active.selectionStart, active.selectionEnd, active.scrollTop] : null;
    repainting = true;
    try { repaint(); } finally { repainting = false; }
    const next = focused && document.querySelector('[data-piece-copy-text]');
    if (next) {
      next.focus({ preventScroll: true });
      next.setSelectionRange(selection[0], selection[1]);
      next.scrollTop = selection[2];
    }
  }

  async function save(fam, d, repaint, update) {
    if (d.saving) { d.requested = true; return d.saving; }
    if (d.text === d.saved) return;
    if (d.text.length > MAX_BODY) {
      d.failed = true;
      d.status = 'Not saved: body is too long (max 100000 characters).';
      update();
      return;
    }
    const submitted = d.text;
    d.failed = false;
    d.status = 'Saving…';
    update();
    d.saving = (async () => {
      try {
        const store = window.DeskV1Store;
        const piece = store.live()
          ? await store.api('PATCH', `/api/desk/pieces/${encodeURIComponent(fam.id)}`, { body: submitted })
          : { body: submitted };
        d.saved = typeof piece.body === 'string' ? piece.body : submitted;
        if (d.text === submitted) d.text = d.saved;
        fam.body = d.saved;
        let gate = null;
        if (store.live()) gate = await store.load({ force: true });
        d.status = d.text !== d.saved ? 'Unsaved changes' : store.live() ? 'Saved' : 'Saved in demo only';
        if (gate) {
          d.failed = true;
          d.status = `Saved, but workspace refresh failed: ${gate.message}`;
        }
      } catch (e) {
        d.failed = true;
        d.status = `Not saved: ${e && e.message ? e.message : String(e)}`;
      }
    })();
    await d.saving;
    d.saving = null;
    update();
    const again = d.requested && !d.failed && d.text !== d.saved;
    d.requested = false;
    repaintKeepingFocus(repaint);
    if (again) return save(fam, d, repaint, update);
  }

  function htmlText(text) {
    const doc = new DOMParser().parseFromString(text, 'text/html');
    doc.querySelectorAll('script,style,template,noscript').forEach((el) => el.remove());
    doc.querySelectorAll('br').forEach((el) => el.replaceWith('\n'));
    doc.querySelectorAll('p,div,section,article,li,h1,h2,h3,h4,h5,h6,tr').forEach((el) => el.append('\n'));
    return (doc.body.textContent || '').replace(/\n{3,}/g, '\n\n').trim();
  }

  function bind(el, fam, repaint) {
    el.querySelector('[data-piece-copy-where]')?.addEventListener('click', () => window.deskV1GotoCampaignPanel('where', { campaignId: fam.campaignId }));
    el.querySelector('[data-piece-copy-campaign]')?.addEventListener('click', () => window.deskV1Nav('home', {}));
    const root = el.querySelector('[data-piece-copy]');
    if (!root || !editable(fam)) return;
    const d = draft(fam);
    const textarea = root.querySelector('[data-piece-copy-text]');
    const file = root.querySelector('[data-piece-copy-file]');
    const currentRoot = () => {
      const mounted = el.querySelector('[data-piece-copy]');
      return mounted && mounted.dataset.pieceCopy === fam.id ? mounted : null;
    };
    const update = () => {
      const mounted = currentRoot();
      if (!mounted) return;
      const status = mounted.querySelector('[data-piece-copy-status]');
      status.textContent = d.status;
      status.toggleAttribute('data-failed', d.failed);
      mounted.querySelector('[data-piece-copy-save]').disabled = !!d.saving || d.text === d.saved;
      mounted.querySelector('[data-piece-copy-text]').setAttribute('aria-invalid', String(d.text.length > MAX_BODY));
    };
    textarea.oninput = () => {
      d.text = textarea.value;
      d.failed = false;
      d.status = d.text === d.saved ? '' : 'Unsaved changes';
      update();
    };
    textarea.onblur = () => { if (!repainting) save(fam, d, repaint, update); };
    const button = root.querySelector('[data-piece-copy-save]');
    // Keep the field focused so blur + click cannot submit the same text twice.
    button.onmousedown = (e) => e.preventDefault();
    button.onclick = () => { save(fam, d, repaint, update); };
    root.querySelector('[data-piece-copy-import]').onclick = () => file.click();
    file.onchange = async () => {
      const chosen = file.files[0];
      if (!chosen) return;
      const before = d.text;
      try {
        if (!/\.(txt|md|markdown|html|htm)$/i.test(chosen.name)) throw new Error('Choose a text, Markdown or HTML file.');
        if (chosen.size > MAX_FILE_BYTES) throw new Error('The file exceeds the 4 MB import limit.');
        const raw = await chosen.text();
        const text = /\.html?$/i.test(chosen.name) ? htmlText(raw) : raw;
        if (text.length > MAX_BODY) throw new Error('The file exceeds 100,000 characters.');
        if (d.text !== before) throw new Error('Your text changed while reading the file. Import it again when ready.');
        d.text = text;
        d.failed = false;
        d.status = 'Imported · Not saved';
        const mounted = currentRoot();
        if (mounted) {
          const input = mounted.querySelector('[data-piece-copy-text]');
          input.value = text;
          input.focus({ preventScroll: true });
        }
      } catch (e) {
        d.failed = true;
        d.status = `Import failed: ${e && e.message ? e.message : String(e)}`;
      } finally {
        file.value = '';
        update();
      }
    };
    update();
  }

  function beforeRepaint(el) {
    const textarea = el.querySelector('[data-piece-copy-text]');
    if (textarea) textarea.onblur = null;
  }

  window.DeskV1PieceCopy = { html, bind, beforeRepaint };
})();

// Article source adapter: the piece editor's importer, persisted by existing M15.
(function () {
  function html() {
    return `<div data-what-article-import>
      <p>Import text, Markdown or HTML into this article. Up to 4 MB and 100,000 characters.</p>
      <label class="desk-v1-what-import-file">Choose a file
        <input type="file" data-what-article-file accept="${window.DeskV1TextImport.ACCEPT}">
      </label>
      <p data-what-import-status role="status" aria-live="polite"></p>
    </div>`;
  }

  function wire(root, { fam }, done) {
    const file = root.querySelector('[data-what-article-file]');
    if (!file) return;
    file.onchange = async () => {
      const chosen = file.files[0];
      if (!chosen) return;
      const status = root.querySelector('[data-what-import-status]');
      const before = { body: fam.body || '', wordCount: fam.wordCount };
      const modal = window.DeskV1WhatAddModal;
      modal.busy(true);
      status.textContent = 'Importing…';
      try {
        const body = await window.DeskV1TextImport.read(chosen);
        const words = body.split(/\s+/).filter(Boolean).length;
        const store = window.DeskV1Store;
        const patch = (text, count) => window.deskV1QueuePiece(fam.id, () => store.api('PATCH',
          '/api/desk/pieces/' + encodeURIComponent(fam.id), { body: text, word_count: count == null ? null : count }));
        const r = await store.write({
          label: `Imported “${chosen.name}”`,
          apply: () => { fam.body = body; fam.wordCount = words; },
          unapply: () => { Object.assign(fam, before); },
          request: () => patch(body, words),
          undoRequest: () => patch(before.body, before.wordCount),
          repaint: () => window.deskV1RepaintWhat(),
        });
        if (!r.ok) throw new Error(r.error);
        done();
      } catch (e) {
        // A refusal may repaint the card; update the currently mounted status.
        const current = document.querySelector('[data-what-import-status]') || status;
        current.textContent = `Import failed: ${e.message || e}`;
      } finally {
        file.value = '';
        modal.busy(false);
      }
    };
  }
  window.DeskV1WhatArticleImport = { html, wire };
})();

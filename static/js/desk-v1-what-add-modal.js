// What's add-piece presentation and source registry. Existing handlers own work.
(function () {
  const SOURCES = {
    video: [
      { id: 'record', label: 'Record from the product', hint: 'Record a live run of your product', glyph: '📹' },
      { id: 'upload', label: 'Upload', hint: 'Choose a video from your files', glyph: '⬆' },
      { id: 'online', label: 'Online source', hint: 'Use a video from a link', glyph: '🌐' },
      { id: 'create', label: 'Create new', hint: 'Build a video in Studio', glyph: '🎬' },
    ],
    article: [
      { id: 'browse', label: 'Browse existing', hint: 'Find an article in your blog or library', glyph: '📚' },
      { id: 'write', label: 'Write new', hint: 'Open the article writer', glyph: '✎' },
      { id: 'import', label: 'Import from file', hint: 'Bring in text, Markdown or HTML', glyph: '⬆' },
    ],
    post: [
      { id: 'write', label: 'Write new', hint: 'Start with a blank post', glyph: '✎' },
      { id: 'reuse', label: 'Reuse an existing post', hint: 'Make a copy of its text and media', glyph: '↗' },
    ],
    image: [
      { id: 'upload', label: 'Upload', hint: 'Choose a picture from your files', glyph: '⬆' },
      { id: 'library', label: 'From library', hint: 'Browse your saved pictures', glyph: '🖼' },
      { id: 'generate', label: 'Generate', hint: 'Create an image in Studio', glyph: '✨' },
    ],
  };
  const esc = (s) => window.esc(String(s == null ? '' : s));
  let dialog = null, closeHandler = null, currentKind = null, observer = null, ownerContent = null;

  function choicesHTML(kind) {
    return `<div class="desk-v1-what-sources" role="group" aria-label="Choose a source">${SOURCES[kind].map((s) => `
      <button type="button" class="desk-v1-what-source" data-what-source="${s.id}">
        <span class="desk-v1-what-source-glyph" aria-hidden="true">${s.glyph}</span>
        <span class="desk-v1-what-source-name">${esc(s.label)}</span>
        <span class="desk-v1-what-source-hint">${esc(s.hint)}</span>
      </button>`).join('')}</div>`;
  }

  function hide(restore = false) {
    if (!dialog) return;
    const kind = currentKind;
    observer?.disconnect(); observer = null;
    dialog.close(); dialog.remove(); dialog = null;
    closeHandler = null;
    if (restore) document.querySelector(`[data-what-type="${kind}"]`)?.focus({ preventScroll: true });
  }
  function dismiss() {
    if (dialog?.dataset.busy === 'true') return;
    const fn = closeHandler;
    const kind = currentKind;
    hide();
    if (fn) fn();
    document.querySelector(`[data-what-type="${kind}"]`)?.focus({ preventScroll: true });
  }
  function busy(value) {
    if (!dialog) return;
    dialog.dataset.busy = String(value);
    dialog.setAttribute('aria-busy', String(value));
    dialog.querySelectorAll('button,input,select,textarea').forEach((el) => { el.disabled = value; });
  }
  function render(kind, body, onClose, owner) {
    if (!dialog) {
      dialog = document.createElement('dialog');
      dialog.className = 'desk-v1-what-add-modal';
      dialog.dataset.whatAddModal = '';
      dialog.setAttribute('aria-labelledby', 'what-add-heading');
      dialog.setAttribute('aria-modal', 'true');
      document.body.appendChild(dialog);
      observer = new MutationObserver(() => { if (!ownerContent?.isConnected) hide(); });
      observer.observe(document.body, { childList: true, subtree: true });
      dialog.addEventListener('cancel', (e) => { e.preventDefault(); dismiss(); });
      // A press that starts inside and ends outside is not a backdrop click.
      let backdrop = false;
      const outside = (e) => {
        const r = dialog.getBoundingClientRect();
        return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
      };
      dialog.addEventListener('pointerdown', (e) => { backdrop = e.target === dialog && outside(e); });
      dialog.addEventListener('click', (e) => { if (backdrop && e.target === dialog && outside(e)) dismiss(); backdrop = false; });
      dialog.addEventListener('keydown', (e) => {
        // Keep shell shortcuts from acting through this modal. Native dialog
        // supplies top-layer inertness; explicit Tab wrap keeps focus on controls.
        e.stopPropagation();
        if (e.key !== 'Tab') return;
        const all = [...dialog.querySelectorAll('button,input,select,textarea,a[href],[tabindex="0"]')]
          .filter((el) => !el.disabled && el.getClientRects().length && !el.hidden);
        const first = all[0], last = all[all.length - 1];
        if (!first) { e.preventDefault(); return; }
        if (e.shiftKey && (document.activeElement === first || document.activeElement === dialog)) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      });
    }
    const active = document.activeElement;
    const focusAttr = active && [...active.attributes].find((a) => a.name.startsWith('data-what-'));
    const wasOpen = dialog.open;
    const wasBusy = dialog.dataset.busy === 'true';
    currentKind = kind; closeHandler = onClose; ownerContent = owner.querySelector('[data-what]');
    dialog.innerHTML = `<header class="desk-v1-what-add-heading"><div><span>Add a piece</span>
      <h2 id="what-add-heading">${esc(kind[0].toUpperCase() + kind.slice(1))}</h2></div>
      <button type="button" data-what-modal-close aria-label="Close add piece">✕</button></header>${body}`;
    dialog.querySelector('[data-what-modal-close]').onclick = dismiss;
    if (!wasOpen) dialog.showModal();
    busy(wasBusy);
    const focus = wasOpen && focusAttr ? dialog.querySelector(`[${focusAttr.name}="${CSS.escape(focusAttr.value)}"]`) : dialog.querySelector('[data-what-title]');
    (focus || dialog.querySelector('[data-what-modal-close]')).focus({ preventScroll: true });
    if (!wasOpen) dialog.querySelector('[data-what-title]')?.select();
    return dialog;
  }
  window.DeskV1WhatAddModal = { sources: SOURCES, choicesHTML, render, hide, busy };
})();

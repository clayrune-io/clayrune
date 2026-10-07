// Desk v1 — a strip of library pictures with the shared "Pick from library" button
// (desk-v1-library-picker.js), for the places that take a few pictures: an image's
// start / reference pictures (the New image page), the pictures in an article.
// Window-bridged module, no `import` (ground rule 1).
//
//   window.DeskV1LibraryRefs.html(refs, opts)       the strip's markup
//   window.DeskV1LibraryRefs.wire(root, refs, opts) wire it; `refs` is changed in place
//
// `refs` is `[{path, title, src}]`: library files, REFERENCED (the path the library
// lists, relative to data/uploads), never copied. opts: `{ head, hint, max, blocked,
// attr, onChange(refs, what) }`:
//   head      the strip's heading
//   hint      one line under it
//   max       how many it may hold (a full strip disables the button and says so)
//   blocked   a sentence: this target takes none (shown instead of the button)
//   attr      a data attribute stamped on the strip, so a smoke finds it
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function html(refs, opts) {
    opts = opts || {};
    const full = opts.max != null && refs.length >= opts.max;
    const items = refs.map((r, i) => `<div class="desk-v1-lr-item" data-lr-item="${i}">
        <span class="desk-v1-lr-thumb">${r.src ? `<img src="${esc(r.src)}" alt="${esc(r.title)}">` : ''}</span>
        <span class="desk-v1-lr-name">${esc(r.title)}</span>
        <button type="button" class="desk-v1-lr-remove" data-lr-remove="${i}" aria-label="Remove ${esc(r.title)}" title="Remove">✕</button>
      </div>`).join('');
    const control = opts.blocked
      ? `<div class="desk-v1-lr-note" data-lr-blocked>${esc(opts.blocked)}</div>`
      : `<div class="desk-v1-lr-row">${items}${window.DeskV1LibraryPicker
        ? window.DeskV1LibraryPicker.buttonHTML(`data-lr-pick${full ? ' disabled' : ''}`, 'btn-secondary') : ''}</div>
        ${full ? `<div class="desk-v1-lr-note" data-lr-full>This takes at most ${opts.max}. Remove one to pick another.</div>` : ''}`;
    return `<div class="desk-v1-lr" data-lr${opts.attr ? ' ' + opts.attr : ''}>
      ${opts.head ? `<div class="desk-v1-lr-head">${esc(opts.head)}</div>` : ''}
      ${opts.hint ? `<div class="desk-v1-sc-hint">${esc(opts.hint)}</div>` : ''}
      ${control}
    </div>`;
  }

  function wire(root, refs, opts) {
    opts = opts || {};
    const box = root.querySelector('[data-lr]');
    if (!box) return;
    const pick = box.querySelector('[data-lr-pick]');
    // A host that repaints on a field's `change` (New image) would swallow this click: the
    // mousedown blurs the field, `change` repaints, the button under the pointer is gone by
    // mouseup. Keeping focus where it is lets the click through.
    if (pick) pick.onmousedown = (e) => e.preventDefault();
    if (pick) pick.onclick = () => {
      if (!window.DeskV1LibraryPicker) return;
      window.DeskV1LibraryPicker.open({ kinds: ['image'], returnFocus: pick, onPick: (it) => {
        if (opts.max != null && refs.length >= opts.max) return;
        if (refs.some((r) => r.path === it.path)) { window.DeskV1Kit.toast(`“${it.title}” is already here.`); return; }
        refs.push({ path: it.path, title: it.title, src: it.src });
        if (typeof opts.onChange === 'function') opts.onChange(refs, 'add', it);
      } });
    };
    box.querySelectorAll('[data-lr-remove]').forEach((b) => {
      b.onclick = () => {
        const [gone] = refs.splice(Number(b.dataset.lrRemove), 1);
        if (typeof opts.onChange === 'function') opts.onChange(refs, 'remove', gone);
      };
    });
  }

  window.DeskV1LibraryRefs = { html, wire };
})();

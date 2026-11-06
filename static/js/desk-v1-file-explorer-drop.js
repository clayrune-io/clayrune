// Studio file explorer: drag a file from the PC results onto the library-folder
// block at the top to copy it in. HTML5 drag only; touch devices keep the
// "Copy to folder" button. The copy itself is the explorer's own copyFiles, so
// busy/error/partial-failure handling is the button's, not a second copy of it.
(function () {
  const MAX = 50; // same cap as selection
  const KEY = 'data-fe-pick';

  // A dragged file copies the whole selection when it is part of it, else only itself.
  function filesFor(st, key) {
    if (st.selected.has(key)) return [...st.selected.values()];
    const [root, path] = JSON.parse(key);
    return [{ root, path }];
  }

  function attach({ el, st, copyFiles }) {
    const results = el.querySelector('[data-fe-results]');
    const target = el.querySelector('[data-fe-saved-box]');
    let dragKey = null;
    const live = () => dragKey !== null && !st.busy;
    const light = (on) => target.classList.toggle('desk-fe-drop-on', on);

    results.addEventListener('dragstart', (e) => {
      const b = e.target.closest && e.target.closest(`[${KEY}]`);
      if (!b || st.busy) { e.preventDefault(); return; }
      dragKey = b.getAttribute(KEY);
      if (e.dataTransfer) {
        e.dataTransfer.effectAllowed = 'copy';
        e.dataTransfer.setData('text/plain', b.querySelector('.desk-fe-name')?.textContent || '');
      }
      target.classList.add('desk-fe-drop-ready');
    });
    const end = () => { dragKey = null; light(false); target.classList.remove('desk-fe-drop-ready'); };
    results.addEventListener('dragend', end);

    target.addEventListener('dragenter', (e) => {
      if (!live()) return;
      e.preventDefault();
      target.open = true; // a collapsed or empty block still takes the drop
      light(true);
    });
    target.addEventListener('dragover', (e) => {
      if (!live()) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
      light(true);
    });
    target.addEventListener('dragleave', (e) => {
      if (!target.contains(e.relatedTarget)) light(false);
    });
    target.addEventListener('drop', (e) => {
      if (!live()) return;
      e.preventDefault();
      const files = filesFor(st, dragKey);
      end();
      if (files.length > MAX) { window.DeskV1Kit.toast(`Choose up to ${MAX} files at a time.`); return; }
      copyFiles(files);
    });
  }

  window.DeskV1FileExplorerDrop = { attach };
})();

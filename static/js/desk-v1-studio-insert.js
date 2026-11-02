// Boundary controls and native material drops. Keep controls out of the flex
// strip: scene widths must continue to match the ruler exactly.
(function () {
  const MATERIAL = 'application/x-clayrune-material';
  function clear(root = document) {
    root.querySelectorAll('[data-insert-marker]').forEach(marker => { marker.hidden = true; });
    root.querySelectorAll('.desk-v1-insert-active').forEach(button => button.classList.remove('desk-v1-insert-active'));
    root.querySelectorAll('[data-sb-timeline].pd-drop-hover').forEach(strip => strip.classList.remove('pd-drop-hover'));
  }
  document.addEventListener('dragend', () => clear(), true);
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || !document.querySelector('[data-insert-marker]:not([hidden])')) return;
    e.preventDefault(); e.stopPropagation();
    clear();
  }, true);

  function wire(root, api, material) {
    const strip = root.querySelector('[data-sb-timeline]');
    const scroll = strip.closest('[data-timebar-scroll]');
    const tiles = Array.from(strip.querySelectorAll('.desk-v1-video-scene'));
    const lane = document.createElement('div');
    lane.className = 'desk-v1-insert-lane';
    lane.setAttribute('role', 'group');
    lane.setAttribute('aria-label', 'Insert a scene from the library');
    const marker = document.createElement('div');
    marker.className = 'desk-v1-insert-marker';
    marker.setAttribute('data-insert-marker', '');
    marker.setAttribute('aria-hidden', 'true');
    marker.hidden = true;
    strip.appendChild(marker);
    strip.after(lane);
    const buttons = [...tiles, null].map((tile, i) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'desk-v1-insert-gap';
      button.dataset.insertBefore = tile ? tile.dataset.sceneId : '';
      button.dataset.insertIndex = i;
      const scene = tile && api.scenes().find(s => s.id === tile.dataset.sceneId);
      button.title = scene ? `Insert scene before scene ${i + 1}: ${scene.label}`
        : tiles.length ? 'Insert scene after the last scene' : 'Insert the first scene';
      button.setAttribute('aria-label', button.title);
      button.innerHTML = '<span aria-hidden="true">+</span>';
      button.onclick = () => window.DeskV1LibraryPicker.open({ kinds: ['image', 'video'], returnFocus: button,
        onPick: it => material(it, button.dataset.insertBefore).then(() => {
          // The command repaints the timeline, replacing the original button.
          root.querySelector(`[data-insert-before="${CSS.escape(button.dataset.insertBefore)}"]`)?.focus();
        }).catch(e => window.DeskV1Kit.toast(e.message || String(e))) });
      lane.appendChild(button);
      return button;
    });
    let boundaries = [];
    function layout() {
      const origin = strip.getBoundingClientRect().left;
      boundaries = tiles.map(tile => tile.getBoundingClientRect().left - origin);
      boundaries.push(tiles.length ? tiles.at(-1).getBoundingClientRect().right - origin : 0);
      const ends = [];
      buttons.forEach((button, i) => {
        const left = Math.max(0, Math.min(strip.clientWidth - 44, boundaries[i] - 22));
        // Short scenes still have individual, non-overlapping keyboard/touch
        // targets. Stack only crowded controls; never stretch the time axis.
        let row = ends.findIndex(end => left >= end + 2);
        if (row < 0) row = ends.length;
        ends[row] = left + 44;
        button.style.left = `${left}px`;
        button.style.top = `${row * 46}px`;
      });
      lane.style.height = `${ends.length * 46}px`;
      if (!marker.hidden) marker.style.left = `${boundaries[Number(marker.dataset.index)]}px`;
    }
    layout();
    const observer = new ResizeObserver(() => {
      if (!strip.isConnected) { observer.disconnect(); return; }
      layout();
    });
    [strip, ...tiles].forEach(node => observer.observe(node));

    const accepts = e => Array.from(e.dataTransfer?.types || []).some(type => type === MATERIAL || type === 'Files');
    const position = e => {
      const button = e.target.closest('[data-insert-before]');
      if (button && lane.contains(button)) return Number(button.dataset.insertIndex);
      const x = e.clientX - strip.getBoundingClientRect().left;
      const index = boundaries.findIndex((start, i) => i < tiles.length && x < (start + boundaries[i + 1]) / 2);
      return index < 0 ? tiles.length : index;
    };
    scroll.addEventListener('dragover', e => {
      if (!accepts(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
      const index = position(e);
      clear(root);
      marker.dataset.index = index;
      marker.style.left = `${boundaries[index]}px`;
      marker.hidden = false;
      buttons[index].classList.add('desk-v1-insert-active');
      strip.classList.add('pd-drop-hover');
    });
    scroll.addEventListener('dragleave', e => { if (!scroll.contains(e.relatedTarget)) clear(root); });
    scroll.addEventListener('drop', async e => {
      if (!accepts(e)) return;
      e.preventDefault();
      const beforeId = buttons[position(e)].dataset.insertBefore;
      clear(root);
      try {
        const files = Array.from(e.dataTransfer.files || []);
        if (files.length) { await api.files(files, beforeId); return; }
        const item = JSON.parse(e.dataTransfer.getData(MATERIAL));
        if (!['image', 'video'].includes(item.kind) || !item.path) throw new Error('Scene material must be a library picture or clip.');
        await material(item, beforeId);
      } catch (err) { window.DeskV1Kit.toast(`Could not add scene material: ${err.message || err}`); }
    });
  }
  window.DeskV1StudioInsert = { wire };
})();

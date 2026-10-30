// Studio's scene timeline. VideoStrip owns PointerDrag reorder and edge trim;
// the storyboard bridge owns persistence, Undo, selection and material uploads.
(function () {
  const MATERIAL = 'application/x-clayrune-material';
  function html(detail) {
    const scenes = detail.scenes.filter(s => !s.placeholder);
    const total = scenes.reduce((n, s) => n + (s.durationSec || 0), 0);
    return `<div class="desk-v1-sb-timeline-title">Timeline · ${total}s</div>
      <div class="desk-v1-video-scenestrip desk-v1-sb-timeline" data-sb-timeline aria-label="Scene timeline. Drag to reorder; arrow keys move a scene.">
        ${window.DeskV1VideoStrip.html(scenes, { trim: true, durations: true }) || '<span class="desk-v1-timeline-empty">Drop pictures here to add scenes</span>'}
      </div>
      <button type="button" class="desk-v1-sb-editbtn" data-timeline-material>Pick from library</button>
      <span class="desk-v1-timeline-hint">Drop pictures or drag scenes here. Drag scene edges to change duration.</span>`;
  }
  function wire(root, api) {
    const strip = root.querySelector('[data-sb-timeline]');
    if (!strip) return;
    const trim = (id, duration) => {
      const scene = api.scenes().find(s => s.id === id);
      if (!scene || scene.durationSec === duration) return;
      const prev = scene.durationSec;
      return api.command({ label: `Changed scene duration to ${duration}s`,
        do: () => { scene.durationSec = duration; api.paint(); },
        undo: () => { scene.durationSec = prev; api.paint(); } });
    };
    const material = (it, beforeId) => {
      const scene = { id: api.newId(), label: it.title || 'New scene', line: '', durationSec: 3,
        picture: { path: it.path, title: it.title, kind: 'image' }, thumb: it.src || '', source: '' };
      const i = api.detail.scenes.findIndex(s => s.id === beforeId);
      const index = i < 0 ? api.detail.scenes.length : i;
      return api.command({ label: 'Added scene from the Material library',
        do: () => { api.detail.scenes.splice(index, 0, scene); api.paint(); },
        undo: () => { api.detail.scenes = api.detail.scenes.filter(s => s.id !== scene.id); api.paint(); } });
    };
    window.DeskV1VideoStrip.wire(strip, api.move, {
      trashSel: '[data-sb-trash]', onTrash: api.remove, onSelect: api.select,
      activeBodyClass: 'desk-v1-sb-drag-active', ghostClass: 'pd-ghost desk-v1-video-scene-ghost',
      scenes: api.scenes, onTrim: trim, maxDuration: 3600,
    });
    strip.addEventListener('keydown', e => {
      const tile = e.target.closest('.desk-v1-video-scene');
      if (!tile) return;
      const scene = api.scenes().find(s => s.id === tile.dataset.sceneId);
      if (!scene) return;
      const edge = e.target.closest('[data-trim-edge]');
      const delta = ['ArrowLeft', 'ArrowUp'].includes(e.key) ? -1 : ['ArrowRight', 'ArrowDown'].includes(e.key) ? 1 : 0;
      if (!delta) return;
      e.preventDefault();
      if (edge) {
        trim(scene.id, Math.min(3600, Math.max(1, scene.durationSec + delta)));
        root.querySelector(`[data-sb-timeline] [data-scene-id="${CSS.escape(scene.id)}"] [data-trim-edge="${edge.dataset.trimEdge}"]`)?.focus({ preventScroll: true });
        return;
      }
      const scenes = api.scenes();
      const other = scenes[scenes.indexOf(scene) + delta];
      if (other) api.move(scene.id, other.id);
      root.querySelector(`[data-sb-timeline] .desk-v1-video-scene[data-scene-id="${CSS.escape(scene.id)}"]`)?.focus({ preventScroll: true });
    });
    const add = root.querySelector('[data-timeline-material]');
    if (add) add.onclick = () => window.DeskV1LibraryPicker.open({ kinds: ['image'], returnFocus: add, onPick: it => material(it) });
    strip.addEventListener('dragover', e => { e.preventDefault(); strip.classList.add('pd-drop-hover'); });
    strip.addEventListener('dragleave', e => { if (!strip.contains(e.relatedTarget)) strip.classList.remove('pd-drop-hover'); });
    strip.addEventListener('drop', async e => {
      e.preventDefault(); strip.classList.remove('pd-drop-hover');
      const tile = e.target.closest('.desk-v1-video-scene');
      const beforeId = tile && tile.dataset.sceneId;
      try {
        const files = Array.from(e.dataTransfer.files || []);
        if (files.length) {
          if (files.some(f => !/^image\//.test(f.type))) throw new Error('Scene material must be a PNG, JPG, GIF or WebP picture.');
          await api.files(files, beforeId); return;
        }
        const raw = e.dataTransfer.getData(MATERIAL);
        if (!raw) throw new Error('Drop a picture from your computer or the Material library.');
        const item = JSON.parse(raw);
        if (item.kind !== 'image' || !item.path) throw new Error('Scene material must be a library picture.');
        await material(item, beforeId);
      } catch (err) { window.DeskV1Kit.toast(`Could not add scene material: ${err.message || err}`); }
    });
  }
  // Native drag from the shared library chooser; hide the overlay while keeping
  // its source node alive until dragend (removing it cancels Chromium's drag).
  function materialDrag(node, item, overlay, close) {
    if (item.kind !== 'image') return;
    node.draggable = true;
    node.addEventListener('dragstart', e => {
      e.dataTransfer.setData(MATERIAL, JSON.stringify({ kind: item.kind, path: item.path, title: item.title, src: item.src }));
      e.dataTransfer.effectAllowed = 'copy';
      setTimeout(() => { if (overlay.isConnected) { overlay.style.opacity = '0'; overlay.style.pointerEvents = 'none'; } }, 0);
    });
    node.addEventListener('dragend', close);
  }
  window.DeskV1StudioTimeline = { html, wire, materialDrag };
})();

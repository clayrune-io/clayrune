// Studio view state only: never stored with the storyboard or changed by edits.
// Ruler and tiles share one scroll coordinate system, without tile minimums.
(function () {
  const format = seconds => {
    const rounded = Math.round(seconds * 100) / 100;
    const [whole, fraction] = String(Math.round((rounded % 60) * 100) / 100).split('.');
    return `${Math.floor(rounded / 60)}:${whole.padStart(2, '0')}${fraction ? '.' + fraction : ''}`;
  };
  function html(tiles) {
    return `<div class="desk-v1-timebar-head">
      <div class="desk-v1-sb-timeline-title" data-timebar-title></div>
      <div class="desk-v1-timebar-zoom" role="group" aria-label="Timeline zoom">
        <button type="button" data-timebar-zoom="out" aria-label="Zoom timeline out">−</button>
        <button type="button" data-timebar-zoom="in" aria-label="Zoom timeline in">+</button>
        <button type="button" data-timebar-zoom="fit">Fit</button>
      </div></div>
      <div class="desk-v1-timebar-scroll" data-timebar-scroll tabindex="0" aria-label="Scrollable timeline">
        <div class="desk-v1-timebar-track" data-timebar-track>
          <div class="desk-v1-timebar-ruler" data-timebar-ruler aria-label="Time ruler"></div>
          ${tiles}
        </div>
      </div><output class="desk-v1-timebar-badge" data-timebar-badge hidden></output>`;
  }
  function wire(root, scenes, view) {
    const scroll = root.querySelector('[data-timebar-scroll]');
    const track = root.querySelector('[data-timebar-track]');
    const ruler = root.querySelector('[data-timebar-ruler]');
    const badge = root.querySelector('[data-timebar-badge]');
    const total = () => scenes.reduce((n, s) => n + (s.durationSec || 0), 0);
    const fit = () => Math.max(0.001, scroll.clientWidth / Math.max(1, total()));
    if (!view.scale) view.scale = fit();
    function paint(sceneId, duration, pointer) {
      const length = total() + (sceneId ? duration - scenes.find(s => s.id === sceneId).durationSec : 0);
      const contentWidth = length * view.scale;
      const width = Math.max(scroll.clientWidth, contentWidth);
      track.style.width = `${width}px`;
      scroll.dataset.pxPerSecond = view.scale;
      root.querySelector('[data-timebar-title]').textContent = `Timeline · ${format(length)} total`;
      root.querySelectorAll('[data-sb-timeline] .desk-v1-video-scene').forEach(tile => {
        const scene = scenes.find(s => s.id === tile.dataset.sceneId);
        const seconds = scene.id === sceneId ? duration : scene.durationSec;
        tile.style.flexBasis = `${seconds * view.scale}px`;
        const description = `${scene.label} · ${format(seconds)}`;
        tile.title = description;
        tile.setAttribute('aria-label', description + '. Drag to reorder; arrow keys move scene.');
        tile.querySelector('.desk-v1-video-scene-duration').textContent = format(seconds);
        const edge = tile.querySelector('[data-trim-edge="r"]');
        edge.setAttribute('aria-valuenow', seconds);
        edge.setAttribute('aria-valuetext', format(seconds));
      });
      // At least ~70px per label. Drawing only visible ticks keeps hour-long
      // scenes cheap even at maximum zoom; the full-width axis stays exact.
      const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
      const interval = steps.find(s => s * view.scale >= 70) || 3600 * Math.ceil(70 / view.scale / 3600);
      const first = Math.max(0, Math.floor(scroll.scrollLeft / view.scale / interval) * interval);
      const end = Math.min(length, (scroll.scrollLeft + scroll.clientWidth) / view.scale + interval);
      let ticks = '';
      for (let s = first; s <= end; s += interval) {
        ticks += `<span class="desk-v1-timebar-tick" data-time="${s}" style="left:${s * view.scale}px">${format(s)}</span>`;
      }
      ruler.innerHTML = ticks;
      badge.hidden = !pointer;
      if (pointer) {
        badge.textContent = format(duration);
        badge.style.left = `${Math.max(4, Math.min(window.innerWidth - 80, pointer.x + 12))}px`;
        badge.style.top = `${Math.max(4, pointer.y - 42)}px`;
      }
    }
    paint();
    scroll.scrollLeft = view.scrollLeft || 0;
    paint();
    let preview = [];
    scroll.addEventListener('scroll', () => {
      // Focus/Undo can replace this timeline before its queued scroll fires.
      // An old listener must not paint the new tiles with its old scene list.
      if (!scroll.isConnected) return;
      view.scrollLeft = scroll.scrollLeft; paint(...preview);
    });
    root.querySelectorAll('[data-timebar-zoom]').forEach(button => {
      button.onclick = () => {
        const start = scroll.scrollLeft / view.scale;
        const mode = button.dataset.timebarZoom;
        view.scale = mode === 'fit' ? fit() : Math.min(1000, Math.max(0.001, view.scale * (mode === 'in' ? 1.5 : 1 / 1.5)));
        paint();
        scroll.scrollLeft = mode === 'fit' ? 0 : start * view.scale;
        view.scrollLeft = scroll.scrollLeft;
        paint();
      };
    });
    return {
      scale: () => view.scale,
      preview: (id, duration, pointer) => { preview = pointer ? [id, duration, pointer] : []; paint(...preview); },
    };
  }
  window.DeskV1StudioTimebar = { html, wire, format };
})();

// Local storyboard playback, with an always-visible stage and independent export.
(function () {
  const mounted = new WeakMap();
  const format = seconds => window.DeskV1StudioTimebar.format(seconds);
  function html() {
    return `<section class="desk-v1-sb-preview" data-sb-preview aria-label="Storyboard preview">
      <div data-preview-player>
        <div class="desk-v1-sb-preview-stage" data-preview-stage>
          <video data-preview-video playsinline preload="auto" hidden></video>
          <img data-preview-image alt="" hidden>
          <div class="desk-v1-sb-preview-card" data-preview-card><strong data-preview-title></strong><p data-preview-line></p></div>
          <button type="button" class="desk-v1-sb-preview-overlay" data-preview-play aria-label="Play preview">Play</button>
          <span class="desk-v1-sb-preview-empty" data-preview-empty>Add a scene to preview</span>
        </div>
        <div class="desk-v1-sb-preview-controls">
          <button type="button" class="desk-v1-sb-editbtn" data-preview-pause hidden>Pause</button>
          <input type="range" data-preview-seek min="0" max="0" value="0" step="0.01" aria-label="Preview position">
          <output data-preview-time aria-live="off">0:00 / 0:00</output>
          ${window.DeskV1StudioExport.html()}
        </div>
        <p class="desk-v1-sb-preview-status" data-preview-status role="status"></p>
      </div>
    </section>`;
  }
  function mount(root, api) {
    mounted.get(root)?.destroy();
    const host = root.querySelector('[data-sb-preview]');
    if (!host) return;
    const find = name => host.querySelector(`[data-preview-${name}]`);
    const play = find('play'), pauseButton = find('pause'), seek = find('seek');
    const video = find('video'), image = find('image'), card = find('card'), status = find('status');
    let entries = [], total = 0, elapsed = 0, active = -1, running = false, frame = 0, last = 0, failed = false;
    let generation = 0, destroyed = false;
    function highlight(id) {
      root.querySelectorAll('[data-sb-timeline] .desk-v1-video-scene').forEach(tile => {
        const current = tile.dataset.sceneId === id;
        tile.classList.toggle('desk-v1-preview-current', current);
        if (current) {
          tile.setAttribute('aria-current', 'true');
          // Follow the active tile horizontally without jumping the page.
          const scroll = tile.closest('[data-timebar-scroll]');
          const box = tile.getBoundingClientRect(), viewport = scroll.getBoundingClientRect();
          if (box.left < viewport.left) scroll.scrollLeft += box.left - viewport.left;
          else if (box.right > viewport.right) scroll.scrollLeft += Math.min(box.right - viewport.right, box.left - viewport.left);
        } else tile.removeAttribute('aria-current');
      });
    }
    function pause() {
      running = false;
      cancelAnimationFrame(frame);
      video.pause();
      const hadFocus = document.activeElement === pauseButton;
      play.hidden = false; pauseButton.hidden = true;
      if (hadFocus) play.focus({ preventScroll: true });
      play.textContent = elapsed >= total && total ? 'Replay' : 'Play';
      play.setAttribute('aria-label', play.textContent + ' preview');
    }
    function release() {
      generation++;
      video.onloadedmetadata = video.onerror = image.onerror = null;
      video.pause();
      video.removeAttribute('src');
      video.load();
      image.removeAttribute('src');
    }
    function mediaError(message) {
      failed = true;
      video.pause(); video.hidden = image.hidden = true; card.hidden = false;
      status.textContent = message;
    }
    function startClip() {
      if (video.hidden || failed) return;
      const mine = generation;
      video.play().catch(error => {
        if (destroyed || mine !== generation || !running || error.name === 'AbortError') return;
        pause();
        status.textContent = `Clip playback stopped: ${error.message || error}. Press Play to retry.`;
      });
    }
    function draw(forceSeek = false) {
      seek.value = String(elapsed);
      seek.setAttribute('aria-valuetext', `${format(elapsed)} of ${format(total)}`);
      find('time').textContent = `${format(elapsed)} / ${format(total)}`;
      const index = entries.findIndex(entry => elapsed < entry.end);
      const next = index < 0 ? entries.length - 1 : index;
      const entry = entries[next];
      if (!entry) return;
      if (active !== next) {
        release();
        active = next; failed = false;
        status.textContent = '';
        const scene = entry.scene, picture = scene.picture;
        const src = picture && (picture.src || picture.play);
        host.dataset.previewScene = scene.id;
        find('title').textContent = scene.label || `Scene ${next + 1}`;
        find('line').textContent = scene.line || '';
        video.hidden = image.hidden = true; card.hidden = false;
        if (src && picture.kind === 'video') {
          video.hidden = false; card.hidden = true;
          video.setAttribute('aria-label', scene.label || 'Scene clip');
          video.onloadedmetadata = () => { alignClip(); };
          video.onerror = () => mediaError('This clip cannot play in this browser. Showing the scene card.');
          video.src = src;
          if (running) startClip();
        } else if (src) {
          image.hidden = false; card.hidden = true;
          image.alt = picture.title || scene.label || 'Scene picture';
          image.onerror = () => mediaError('This picture could not load. Showing the scene card.');
          image.src = src;
        }
        highlight(scene.id);
      }
      if (forceSeek) alignClip();
    }
    function alignClip() {
      if (video.hidden || !video.readyState || failed) return;
      // The editor trims only the right edge: every clip begins at source 0.
      video.currentTime = Math.min(Math.max(0, elapsed - entries[active].start), Math.max(0, video.duration - 0.001));
    }
    function tick(now) {
      if (!host.isConnected) { destroy(); return; }
      if (!running) return;
      const delta = (now - last) / 1000;
      last = now;
      // Buffering must not consume a clip's allotted time. An exhausted source
      // holds its final frame if an older board gives it a longer scene length.
      const pictureReady = image.hidden || image.complete;
      if (pictureReady && (video.hidden || failed || video.ended || (video.readyState >= 3 && !video.seeking && !video.paused))) {
        elapsed = Math.min(total, elapsed + delta);
        draw();
      }
      if (elapsed >= total) { pause(); return; }
      frame = requestAnimationFrame(tick);
    }
    function start() {
      if (api.beforeOpen && api.beforeOpen() === false) return;
      if (!entries.length) return;
      if (elapsed >= total) { elapsed = 0; draw(true); }
      running = true;
      host.dataset.previewStarted = 'true';
      const hadFocus = document.activeElement === play;
      play.hidden = true; pauseButton.hidden = false;
      if (hadFocus) pauseButton.focus({ preventScroll: true });
      if (!failed) status.textContent = '';
      startClip();
      last = performance.now();
      frame = requestAnimationFrame(tick);
    }
    function refresh() {
      pause(); release();
      total = 0; elapsed = 0; active = -1;
      delete host.dataset.previewStarted;
      entries = api.scenes().filter(scene => !scene.placeholder).map(scene => {
        const duration = Number(scene.durationSec);
        const start = total;
        total += Number.isFinite(duration) && duration > 0 ? duration : 3;
        return { start, end: total, scene: { ...scene, picture: scene.picture && { ...scene.picture } } };
      });
      play.disabled = seek.disabled = !entries.length;
      find('empty').hidden = !!entries.length;
      seek.max = String(total); seek.value = '0';
      pause();
      status.textContent = '';
      highlight(null);
      if (!entries.length) {
        video.hidden = image.hidden = card.hidden = true;
        delete host.dataset.previewScene;
        find('time').textContent = '0:00 / 0:00';
      } else draw(true);
    }
    function close() {
      pause(); release(); active = -1;
      delete host.dataset.previewScene;
      highlight(null);
    }
    function destroy() {
      destroyed = true; close(); observer.disconnect();
      mounted.delete(root);
    }
    const observer = new MutationObserver(() => { if (!host.isConnected) destroy(); });
    observer.observe(document.body, { childList: true, subtree: true });
    play.onclick = () => { if (running) pause(); else start(); };
    pauseButton.onclick = pause;
    seek.oninput = () => {
      host.dataset.previewStarted = 'true';
      elapsed = Math.min(total, Math.max(0, Number(seek.value)));
      draw(true);
      last = performance.now();
      if (!running || elapsed >= total) pause();
    };
    mounted.set(root, { refresh, destroy });
    refresh();
  }
  window.DeskV1StudioPreview = { html, mount, refresh: root => mounted.get(root)?.refresh() };
})();

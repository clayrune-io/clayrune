// Browser decoding owns compatibility. No filename/extension allowlist.
(function () {
  const esc = s => window.esc(String(s == null ? '' : s));
  const lengths = new Map();
  const errors = new Set();
  function probe(src, kind) {
    return new Promise((resolve, reject) => {
      const media = document.createElement(kind === 'video' ? 'video' : 'img');
      let timer;
      const finish = (err) => {
        clearTimeout(timer);
        const duration = media.duration;
        media.onload = media.onloadeddata = media.onerror = null;
        media.removeAttribute('src');
        if (kind === 'video') media.load();
        if (!err && kind === 'video') lengths.set(src, duration);
        err ? reject(err) : resolve({ kind, durationSec: kind === 'video' ? duration : 3 });
      };
      media.onerror = () => finish(new Error(`This browser cannot ${kind === 'video' ? 'play that clip' : 'open that picture'}.`));
      if (kind === 'video') {
        media.muted = true; media.preload = 'auto';
        media.onloadeddata = () => finish(Number.isFinite(media.duration) && media.duration > 0 && media.duration <= 3600
          ? null : new Error('A clip needs a finite duration of at most 3600 seconds.'));
      } else media.onload = () => finish(null);
      timer = setTimeout(() => finish(new Error('This browser could not open that media within 15 seconds.')), 15000);
      media.src = src;
    });
  }
  async function inspectFile(file) {
    const src = URL.createObjectURL(file);
    try {
      if (file.type.startsWith('image/')) return { kind: 'image', durationSec: 3 };
      if (file.type.startsWith('video/')) return await probe(src, 'video');
      try { return await probe(src, 'video'); } catch (_) { return await probe(src, 'image'); }
    } finally { URL.revokeObjectURL(src); }
  }
  async function upload(owner, file) {
    const meta = await inspectFile(file);
    // Empty/incorrect native MIME hints must not block a browser-decodable file.
    const typed = file.type.startsWith(meta.kind + '/') ? file
      : new File([file], file.name, { type: meta.kind + '/octet-stream' });
    const ref = await window.DeskV1Store.storyboard.uploadPicture(owner, typed);
    if (meta.kind === 'video') lengths.set(ref.src, meta.durationSec);
    return { ref, durationSec: meta.durationSec };
  }
  function thumb(scene) {
    const ref = scene.picture;
    if (ref?.kind === 'video') return `<video data-scene-clip src="${esc(ref.src || ref.play || '')}" muted playsinline preload="auto" aria-label="${esc(ref.title || 'Uploaded clip')}"></video>`;
    return scene.thumb ? `<img src="${esc(scene.thumb)}" alt="" draggable="false">` : '';
  }
  function wire(root) {
    root.querySelectorAll('video[data-scene-clip]').forEach(video => {
      video.muted = true;
      video.onloadedmetadata = () => { if (Number.isFinite(video.duration) && video.duration > 0) lengths.set(video.getAttribute('src'), video.duration); };
      video.onerror = () => {
        const src = video.getAttribute('src');
        if (video.isConnected && !errors.has(src)) {
          errors.add(src);
          window.DeskV1Kit.toast(`This browser cannot play “${video.getAttribute('aria-label')}”. Replace the clip with a playable file.`);
        }
      };
    });
  }
  async function fromLibrary(item) {
    const ref = { path: item.path, kind: item.kind || 'image', title: item.title, src: item.play || item.src || '' };
    const meta = await probe(ref.src, ref.kind);
    return { ref, durationSec: meta.durationSec };
  }
  async function seed(owner, family, detail, board = {}, current) {
    if (owner.kind !== 'piece' || board.assets_seeded || detail.scenes.length) return;
    const assets = (family.assets || []).filter(a => a.kind === 'video' && a.path);
    if (!assets.length) return;
    const scenes = [];
    for (const asset of assets) {
      let durationSec = 3;
      const ref = { path: asset.path, kind: 'video', title: asset.title || 'Uploaded clip', src: asset.play || asset.src || '' };
      try { durationSec = (await probe(ref.src, 'video')).durationSec; }
      catch (e) { if (current()) window.DeskV1Kit.toast(`${ref.title}: ${e.message}`); }
      scenes.push({ id: 'clip-' + scenes.length + '-' + Math.random().toString(36).slice(2, 10), label: ref.title, line: '', durationSec,
        picture: ref, thumb: '', source: '', edited: false });
    }
    if (!current()) return;
    detail.scenes = scenes;
    try { await window.DeskV1Store.storyboard.save(owner, detail, { assets_seeded: true }); }
    catch (e) {
      // A competing tab wins the revision guard. Adopt it; never retry the seed.
      await window.DeskV1Store.storyboard.load(owner, detail);
      if (e.status !== 409) throw e;
    }
  }
  const maxDuration = scene => scene.picture?.kind === 'video' ? lengths.get(scene.picture.src || scene.picture.play) || 3600 : 3600;
  window.DeskV1StudioClips = { upload, thumb, wire, fromLibrary, seed, maxDuration };
})();

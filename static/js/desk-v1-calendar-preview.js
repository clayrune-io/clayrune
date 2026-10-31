// Calendar metadata around the existing post renderer, never a second media/copy renderer.
(function () {
  const esc = (s) => window.esc(String(s == null ? '' : s));
  const selector = '[data-cal-preview-version], [data-cal-preview-family]';
  let popup = null, timer = null;
  function hide() { clearTimeout(timer); if (popup) popup.remove(); popup = null; }
  function button(title) { return `<button type="button" class="desk-v1-cal-info" data-cal-info aria-label="Preview ${esc(title)}" title="Post info">ⓘ</button>`; }
  function show(node) {
    if (!node.isConnected || document.body.classList.contains('pd-drag-active')) return;
    hide();
    const fx = window.DeskV1Store.state();
    const fam = fx.families.find((f) => f.id === node.dataset.calPreviewFamily || f.versions.some((v) => v.id === node.dataset.calPreviewVersion));
    if (!fam) return;
    const v = fam.versions.find((v) => v.id === node.dataset.calPreviewVersion) || null;
    const versions = v ? [v] : window.DeskV1ComposerChannels.versions(fam);
    const ch = fx.channels.find((c) => c.id === v?.channelId);
    // Plain text before slicing: imported HTML must not leave broken tags in the excerpt.
    const doc = new DOMParser().parseFromString(String(v?.body || fam.body || versions.find((ver) => ver.body)?.body || ''), 'text/html');
    const body = [...(doc.body.textContent || '')].slice(0, 140).join('');
    const times = versions.map((ver) => {
      const time = window.deskV1CalendarVersionWhen(ver);
      const channel = fx.channels.find((c) => c.id === ver.channelId);
      return `${channel?.label || channel?.identity || 'Channel'}: ${time ? time.toLocaleString() : 'Unscheduled'}`;
    });
    popup = document.createElement('aside'); popup.className = 'desk-v1-cal-preview';
    popup.dataset.calPreview = ''; popup.setAttribute('role', 'dialog'); popup.setAttribute('aria-label', `Preview ${fam.title}`);
    popup.innerHTML = `<button type="button" class="desk-v1-cal-detail-close" data-cal-preview-close aria-label="Close preview">×</button>
      <strong>${esc(fam.title)}</strong>
      ${window.DeskV1PostPreview.render({ ...fam, body }, v ? { ...v, body } : null, ch, { literal: true })}
      <div class="desk-v1-cal-badges">${versions.map((ver) => {
        const channel = fx.channels.find((c) => c.id === ver.channelId);
        return channel ? window.DeskV1Kit.channelBadge(channel) : '';
      }).join('')}</div><p>${esc(times.join(' · ') || 'Unscheduled')}</p>
      <p>${versions.map((ver) => window.DeskV1Kit.stateLabelHTML(fx.channels.find((c) => c.id === ver.channelId)?.health === 'held' ? 'held' : ver.state)).join(' ') || 'Draft'}</p>`;
    node.closest('.desk-v1-calendar').appendChild(popup);
    popup.querySelector('[data-cal-preview-close]').onclick = hide;
    window.DeskV1Kit.placePopover(popup, node);
    const panel = popup;
    const place = () => { if (panel.isConnected && node.isConnected) window.DeskV1Kit.placePopover(panel, node); };
    panel.addEventListener('load', place, true);
    panel.addEventListener('loadedmetadata', place, true);
    popup.onpointerenter = () => clearTimeout(timer);
    popup.onpointerleave = () => { timer = setTimeout(hide, 180); };
  }
  function bind(root) {
    hide();
    if (root.dataset.calPreviewBound) return;
    root.dataset.calPreviewBound = 'true';
    root.addEventListener('pointerover', (e) => {
      const node = e.target.closest(selector);
      if (e.pointerType !== 'mouse' || !node || node.contains(e.relatedTarget)) return;
      clearTimeout(timer); timer = setTimeout(() => show(node), 350);
    });
    root.addEventListener('pointerout', (e) => {
      const node = e.target.closest(selector);
      if (node && !node.contains(e.relatedTarget)) { clearTimeout(timer); timer = setTimeout(hide, 180); }
    });
    root.addEventListener('click', (e) => {
      const info = e.target.closest('[data-cal-info]');
      if (info) { e.stopPropagation(); show(info.closest(selector)); }
    });
    root.addEventListener('pointerdown', (e) => { if (!e.target.closest('[data-cal-info], [data-cal-preview]')) hide(); });
  }
  document.addEventListener('keydown', (e) => { if (popup && e.key === 'Escape') { e.stopPropagation(); hide(); } }, true);
  document.addEventListener('pointerdown', (e) => { if (popup && !popup.contains(e.target) && !e.target.closest('[data-cal-info]')) hide(); }, true);
  window.DeskV1CalendarPreview = { bind, button, hide };
})();

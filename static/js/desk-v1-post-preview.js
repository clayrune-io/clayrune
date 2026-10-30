// Desk v1 — post preview (Ron 2026-10-30): hover a message tile on Where and a
// small popup shows that message the way it will look on its platform; click the
// popup for a larger modal. Built as a reusable unit so any Desk stop can wire its
// own tiles with `bind()`; Where is the first caller.
//
//   bind(root, resolve)  delegated listeners on a container that repaints; `resolve(node)` returns
//                        { fam, versionId, entries: [{v, ch}] } for a tile, or null.
//   buttonHTML(title)    the tap-to-preview eye button a tile carries (touch has no hover)
//
// What is drawn is only what the post holds: its title, its copy (`version.body ||
// fam.body`, the same rule as review.js `_liveBody`), its attached assets, and the
// account's name. Platform chrome lives in desk-v1-post-preview-skins.js. A message
// not yet on a channel is drawn plain; the modal offers one tab per version it has.
//
// Input: a mouse hover (after a short delay) or keyboard focus shows the popup; a touch
// long-press is the drag gesture (PointerDrag), so touch taps the eye button instead.
// Window-bridged module, no `import` (ground rule 1).
(function () {
  const SEL = '[data-where-message], [data-where-version]';
  const HOVER_MS = 350;
  const LEAVE_MS = 160;
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const PATHS = {
    heart: '<path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>',
    message: '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>',
    repeat: '<path d="M17 1l4 4-4 4M3 11V9a4 4 0 0 1 4-4h14M7 23l-4-4 4-4M21 13v2a4 4 0 0 1-4 4H3"/>',
    send: '<path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/>',
    share: '<path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8M16 6l-4-4-4 4M12 2v13"/>',
    bookmark: '<path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>',
    thumb: '<path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/>',
    eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
  };
  const svg = (n) => `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PATHS[n] || ''}</svg>`;

  function buttonHTML(title) {
    return `<button type="button" class="desk-v1-pv-eye" data-post-preview aria-haspopup="dialog" aria-label="${esc(`Preview ${title}`)}" title="Preview">${svg('eye')}</button>`;
  }

  // ── content ──────────────────────────────────────────────────────────────
  // A piece body may be HTML (an imported article); show its text, not its markup.
  function plain(body) {
    const s = String(body || '');
    if (!/<[a-z!/][\s\S]*>/i.test(s)) return s.trim();
    const doc = new DOMParser().parseFromString(s.replace(/<\/(p|div|h[1-6]|li|blockquote)>|<br\s*\/?>/gi, '\n'), 'text/html');
    return (doc.body.textContent || '').replace(/\n{3,}/g, '\n\n').trim();
  }

  // One tab of the preview: the post as it will look on `ch`'s platform (or plain).
  function build(fam, v, ch) {
    const plat = ch && ch.platform ? ch.platform : '';
    return {
      platform: plat, word: PLAT_WORD[plat] || plat, ch,
      name: ch ? (ch.identity || ch.label || '') : '', identity: ch ? (ch.identity || '') : '',
      title: fam.title || '', body: plain((v && v.body) || fam.body || ''),
      media: (fam.assets || []).filter((a) => a && (a.src || a.play)).map((a) => ({ kind: a.kind, src: a.src, play: a.play, title: a.title || '' })),
    };
  }
  const PLAT_WORD = { x: 'X', linkedin: 'LinkedIn', youtube: 'YouTube', discord: 'Discord', reddit: 'Reddit', blog: 'Blog', instagram: 'Instagram', tiktok: 'TikTok' };

  function helpers(m, large) {
    const clampStyle = (n) => (n ? ` style="-webkit-line-clamp:${n}"` : '');
    return {
      esc,
      mark: () => (window.DeskV1ServiceIcons && m.ch
        ? window.DeskV1ServiceIcons.markHTML({ platform: m.platform, brand: m.platform, name: m.name }, false) : ''),
      text: (clamp) => (m.body
        ? `<span class="desk-v1-pv-copy${clamp ? ' is-clamped' : ''}"${clampStyle(clamp)}>${esc(m.body)}</span>`
        : '<span class="desk-v1-pv-none">No copy written yet.</span>'),
      icons: (names, labelled) => `<div class="desk-v1-pv-actions${labelled ? ' is-labelled' : ''}" aria-hidden="true">${names.map((n) => `<span>${svg(n)}</span>`).join('')}</div>`,
      media: (o) => {
        const items = m.media.slice(0, o.limit || 1);
        if (!items.length) return o.required ? `<div class="desk-v1-pv-nomedia" style="aspect-ratio:${o.ratio || '16 / 9'}">${esc(o.required)}</div>` : '';
        const more = m.media.length - items.length;
        const one = (a) => (a.kind === 'video' && a.play
          ? `<video src="${esc(a.play)}" preload="metadata" muted playsinline ${large ? 'controls' : ''}></video>${large ? '' : '<span class="desk-v1-pv-play" aria-hidden="true">▶</span>'}`
          : `<img src="${esc(a.src || '')}" alt="${esc(a.title)}">`);
        return `<div class="desk-v1-pv-media${o.grid && items.length > 1 ? ' is-grid' : ''}${o.plain ? ' is-plain' : ''}"${o.ratio ? ` style="aspect-ratio:${o.ratio}"` : ''}>${items.map((a) => `<span class="desk-v1-pv-m">${one(a)}</span>`).join('')}${more > 0 && !large ? `<span class="desk-v1-pv-more">+${more}</span>` : ''}</div>`;
      },
    };
  }

  function cardHTML(m, large) {
    return `<article class="desk-v1-pv-card${large ? ' is-large' : ''}" data-pv-platform="${esc(m.platform || 'plain')}">${window.DeskV1PostPreviewSkins.render({ ...m, large }, helpers(m, large))}</article>`;
  }

  // The tabs a tile can show: the plain message, then each live version.
  function tabsFor(info) {
    const { fam, entries } = info;
    const tabs = [{ id: 'message', label: 'Message', m: build(fam, null, null) }];
    entries.forEach(({ v, ch }) => tabs.push({ id: v.id, label: `${PLAT_WORD[ch && ch.platform] || (ch && ch.platform) || 'Account'}${ch && ch.identity ? ' · ' + ch.identity : ''}`, m: build(fam, v, ch) }));
    return tabs;
  }
  function startTab(info) { return info.versionId && info.entries.some((e) => e.v.id === info.versionId) ? info.versionId : 'message'; }

  // ── popup ────────────────────────────────────────────────────────────────
  let pop = null; // { el, node, pinned, info, onDoc, leave }
  let enterTimer = null;
  let leaveTimer = null;
  let modal = null;
  let quiet = false; // returning focus to a tile after the modal must not pop its preview up again

  function hidePopup() {
    clearTimeout(enterTimer); clearTimeout(leaveTimer);
    if (!pop) return;
    document.removeEventListener('pointerdown', pop.onDoc, true);
    window.removeEventListener('scroll', pop.onScroll, true);
    pop.el.remove();
    pop = null;
  }

  function place(el, node) {
    const r = node.getBoundingClientRect();
    const w = el.offsetWidth, h = el.offsetHeight, vw = window.innerWidth, vh = window.innerHeight, gap = 8;
    let left, top;
    if (vw <= 699) { left = Math.max(gap, (vw - w) / 2); top = r.bottom + gap + h <= vh ? r.bottom + gap : r.top - gap - h; }
    else {
      left = r.right + gap + w <= vw ? r.right + gap : r.left - gap - w >= 0 ? r.left - gap - w : Math.max(gap, Math.min(r.left, vw - w - gap));
      top = r.top;
    }
    el.style.left = Math.round(Math.max(gap, Math.min(left, vw - w - gap))) + 'px';
    el.style.top = Math.round(Math.max(gap, Math.min(top, vh - h - gap))) + 'px';
  }

  function showPopup(node, info, pinned) {
    if (!info || document.body.classList.contains('pd-drag-active')) return;
    if (pop && pop.node === node) { pop.pinned = pop.pinned || pinned; return; }
    hidePopup();
    const tabs = tabsFor(info);
    const tab = tabs.find((t) => t.id === startTab(info)) || tabs[0];
    const el = document.createElement('div');
    el.className = 'desk-v1-pv-pop';
    el.setAttribute('role', 'button'); el.setAttribute('tabindex', '0'); el.setAttribute('data-post-preview-pop', '');
    el.setAttribute('aria-label', `Preview of ${info.fam.title}${tab.m.word ? ' on ' + tab.m.word : ''}. Press Enter for a larger view`);
    el.innerHTML = `${cardHTML(tab.m, false)}<div class="desk-v1-pv-hint">Click to enlarge</div>`;
    el.style.visibility = 'hidden';
    document.body.appendChild(el);
    place(el, node);
    el.style.visibility = '';
    // A picture or video that loads after placement makes the popup taller: place it again.
    const replace = () => { if (pop && pop.el === el) place(el, node); };
    el.addEventListener('load', replace, true);
    el.addEventListener('loadedmetadata', replace, true);
    const state = { el, node, pinned: !!pinned, info };
    state.onDoc = (e) => { if (!el.contains(e.target) && !node.contains(e.target)) hidePopup(); };
    state.onScroll = () => { if (!state.pinned) hidePopup(); };
    pop = state;
    document.addEventListener('pointerdown', state.onDoc, true);
    window.addEventListener('scroll', state.onScroll, true);
    el.addEventListener('pointerenter', () => clearTimeout(leaveTimer));
    el.addEventListener('pointerleave', () => { if (!state.pinned) leaveTimer = setTimeout(hidePopup, LEAVE_MS); });
    el.addEventListener('click', () => { const i = state.info, n = state.node; hidePopup(); openModal(i, n); });
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); el.click(); }
    });
    if (pinned) el.focus({ preventScroll: true });
  }

  // ── modal ────────────────────────────────────────────────────────────────
  function closeModal() {
    if (!modal) return;
    document.removeEventListener('keydown', modal.onKey, true);
    modal.el.remove();
    const back = modal.returnFocus;
    modal = null;
    if (back && back.isConnected && typeof back.focus === 'function') { quiet = true; back.focus({ preventScroll: true }); quiet = false; }
  }

  function openModal(info, node) {
    closeModal();
    const tabs = tabsFor(info);
    let cur = startTab(info);
    const el = document.createElement('div');
    el.className = 'desk-v1-pv-overlay';
    el.setAttribute('role', 'dialog'); el.setAttribute('aria-modal', 'true'); el.setAttribute('aria-label', `Preview of ${info.fam.title}`);
    el.setAttribute('data-post-preview-modal', '');
    const paint = () => {
      const tab = tabs.find((t) => t.id === cur) || tabs[0];
      el.innerHTML = `<div class="desk-v1-pv-box">
        <div class="desk-v1-pv-bar"><span class="desk-v1-pv-bar-title">${esc(info.fam.title)}</span>
          <button type="button" class="desk-v1-pv-close" data-pv-close aria-label="Close preview" title="Close (Esc)">✕</button></div>
        ${tabs.length > 1 ? `<div class="desk-v1-pv-tabs" role="tablist">${tabs.map((t) => `<button type="button" role="tab" class="desk-v1-pv-tab" data-pv-tab="${esc(t.id)}" aria-selected="${t.id === tab.id}">${esc(t.label)}</button>`).join('')}</div>` : ''}
        <div class="desk-v1-pv-stage">${cardHTML(tab.m, true)}</div></div>`;
      el.querySelector('[data-pv-close]').onclick = closeModal;
      el.querySelectorAll('[data-pv-tab]').forEach((b) => { b.onclick = () => { cur = b.dataset.pvTab; paint(); el.querySelector(`[data-pv-tab="${CSS.escape(cur)}"]`).focus({ preventScroll: true }); }; });
    };
    paint();
    let downOnBackdrop = false;
    el.addEventListener('mousedown', (e) => { downOnBackdrop = e.target === el; });
    el.addEventListener('click', (e) => { if (e.target === el && downOnBackdrop) closeModal(); });
    const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeModal(); } };
    document.addEventListener('keydown', onKey, true);
    document.body.appendChild(el);
    modal = { el, onKey, returnFocus: node || document.activeElement };
    el.querySelector('[data-pv-close]').focus({ preventScroll: true });
  }

  // ── binding ──────────────────────────────────────────────────────────────
  // One set of listeners per container; the container repaints, its tiles do not
  // survive, so the resolver is re-pointed on every bind() instead of re-listening.
  function bind(root, resolve) {
    hidePopup();
    root.__pvResolve = resolve;
    if (root.__pvBound) return;
    root.__pvBound = true;
    const info = (node) => (root.__pvResolve ? root.__pvResolve(node) : null);
    const tile = (e) => (e.target.closest ? e.target.closest(SEL) : null);

    root.addEventListener('pointerover', (e) => {
      if (e.pointerType !== 'mouse') return;
      const t = tile(e);
      if (!t || (e.relatedTarget && t.contains(e.relatedTarget))) return;
      clearTimeout(leaveTimer); clearTimeout(enterTimer);
      enterTimer = setTimeout(() => showPopup(t, info(t), false), HOVER_MS);
    });
    root.addEventListener('pointerout', (e) => {
      if (e.pointerType !== 'mouse') return;
      const t = tile(e);
      if (!t || (e.relatedTarget && (t.contains(e.relatedTarget) || (pop && pop.el.contains(e.relatedTarget))))) return;
      clearTimeout(enterTimer);
      if (pop && !pop.pinned) leaveTimer = setTimeout(hidePopup, LEAVE_MS);
    });
    root.addEventListener('pointerdown', (e) => { if (!e.target.closest('[data-post-preview]')) { clearTimeout(enterTimer); if (pop && !pop.pinned) hidePopup(); } });
    root.addEventListener('focusin', (e) => {
      const t = tile(e);
      if (!quiet && t && t === e.target && t.matches(':focus-visible')) showPopup(t, info(t), false);
    });
    root.addEventListener('focusout', (e) => {
      const t = tile(e);
      if (t && pop && !pop.pinned && !(e.relatedTarget && pop.el.contains(e.relatedTarget))) hidePopup();
    });
    root.addEventListener('click', (e) => {
      const btn = e.target.closest ? e.target.closest('[data-post-preview]') : null;
      if (!btn) return;
      e.stopPropagation();
      const t = btn.closest(SEL);
      if (t) { clearTimeout(enterTimer); if (pop) hidePopup(); showPopup(t, info(t), true); }
    });
  }
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && pop) hidePopup(); }, true);

  window.DeskV1PostPreview = { bind, buttonHTML, open: openModal, close: closeModal, hide: hidePopup };
})();

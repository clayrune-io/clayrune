// The whole story card resizes, so preview stays below it in normal flow.
(function () {
  function attach(panel) {
    if (!panel || panel.querySelector('[data-story-resize]')) return;
    const edge = document.createElement('div');
    edge.className = 'desk-v1-story-resize';
    edge.dataset.storyResize = '';
    edge.tabIndex = 0;
    edge.setAttribute('role', 'separator');
    edge.setAttribute('aria-orientation', 'horizontal');
    edge.setAttribute('aria-label', 'Resize the story card');
    edge.setAttribute('aria-valuemin', '220');
    edge.setAttribute('aria-valuemax', '1200');
    panel.append(edge);
    const height = () => panel.getBoundingClientRect().height;
    const scale = () => height() / panel.offsetHeight || 1;
    const floor = () => {
      const style = getComputedStyle(panel);
      const rows = Array.from(panel.children).filter(el => el !== edge && el.getClientRects().length);
      const chrome = rows.filter(el => !el.hasAttribute('data-sb-story')).reduce((sum, el) => sum + el.offsetHeight, 0);
      const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom)
        + parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
      return Math.max(220, Math.ceil(chrome + padding + 64 + parseFloat(style.rowGap) * (rows.length - 1)));
    };
    const sync = () => {
      if (!panel.isConnected) { observer.disconnect(); return; }
      if (panel.closest('[data-story-dialog]')) { panel.style.removeProperty('min-height'); return; }
      const min = floor();
      panel.style.minHeight = min + 'px';
      edge.setAttribute('aria-valuemin', String(min));
      edge.setAttribute('aria-valuenow', String(panel.offsetHeight));
    };
    // Generation choices and save errors can add wrapped rows. They set the
    // card's minimum so neither the bottom edge nor preview covers a control.
    const observer = new ResizeObserver(sync);
    Array.from(panel.children).forEach(el => { if (el !== edge) observer.observe(el); });
    observer.observe(panel);
    sync();
    const set = px => {
      const next = Math.max(floor(), Math.min(1200, px));
      panel.style.height = next + 'px';
      edge.setAttribute('aria-valuenow', String(Math.round(next)));
    };
    edge.setAttribute('aria-valuenow', String(Math.round(panel.offsetHeight)));
    let drag = null;
    edge.addEventListener('pointerdown', e => {
      if (e.button !== 0 || drag) return;
      e.preventDefault();
      drag = { id: e.pointerId, y: e.clientY, height: panel.offsetHeight, scale: scale() };
      edge.setPointerCapture(e.pointerId);
    });
    edge.addEventListener('pointermove', e => {
      if (drag && e.pointerId === drag.id) set(drag.height + (e.clientY - drag.y) / drag.scale);
    });
    const end = e => { if (drag && e.pointerId === drag.id) drag = null; };
    edge.addEventListener('pointerup', end);
    edge.addEventListener('pointercancel', end);
    edge.addEventListener('lostpointercapture', end);
    edge.addEventListener('keydown', e => {
      if (!['ArrowUp', 'ArrowDown'].includes(e.key)) return;
      e.preventDefault();
      set(panel.offsetHeight + (e.key === 'ArrowDown' ? 1 : -1) * (e.shiftKey ? 96 : 24));
    });
  }
  window.DeskV1StoryResize = { attach };
})();

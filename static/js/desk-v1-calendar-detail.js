// When owns this sheet; opening a scheduled piece never routes to an empty Review.
(function () {
  const esc = (s) => window.esc(String(s == null ? '' : s));
  let mounted = null;
  function close() { if (mounted) { const { el, trigger } = mounted; mounted = null; el.remove(); if (trigger.isConnected) trigger.focus({ preventScroll: true }); } }
  function open(trigger, fam, version, repaint) {
    close(); window.DeskV1CalendarPreview.hide();
    const ch = window.DeskV1Store.state().channels.find((c) => c.id === version.channelId);
    const when = window.deskV1CalendarVersionWhen(version);
    const local = when ? new Date(when.getTime() - when.getTimezoneOffset() * 60000).toISOString().slice(0, 16) : '';
    const locked = !!fam.archivedAt || window.DeskV1ComposerChannels.sent(version);
    const el = document.createElement('section');
    el.className = 'desk-v1-cal-detail desk-v1-cal-kbd-reschedule'; el.dataset.calDetail = '';
    el.setAttribute('role', 'dialog'); el.setAttribute('aria-label', `When: ${fam.title}`);
    el.innerHTML = `<button type="button" class="desk-v1-cal-detail-close" data-cal-detail-close aria-label="Close details">×</button>
      <h2>${esc(fam.title)}</h2>${ch ? window.DeskV1Kit.channelBadge(ch) : ''}
      <p>${window.DeskV1Kit.stateLabelHTML(ch?.health === 'held' ? 'held' : version.state)}</p>
      <label>Date and time <input type="datetime-local" data-kbd-when value="${local}" ${locked ? 'disabled' : ''}></label>
      <p class="desk-v1-composer-hint">Times use this device’s time zone.</p>
      <div class="desk-v1-cal-detail-actions"><button type="button" class="btn-add" data-kbd-move ${locked ? 'disabled' : ''}>Set time</button>
      <button type="button" class="btn-secondary" data-cal-unschedule ${locked || !when ? 'disabled' : ''}>Unschedule</button>
      <button type="button" class="btn-secondary" data-cal-edit-post>Edit post</button>
      ${version.state === 'needs_review' ? '<button type="button" class="btn-secondary" data-cal-open-review>Open review</button>' : ''}</div>
      <p data-cal-detail-status role="status" aria-live="polite"></p>`;
    trigger.closest('.desk-v1-calendar').appendChild(el);
    mounted = { el, trigger };
    const status = el.querySelector('[data-cal-detail-status]');
    const reportFailure = (message) => {
      // Optimistic calendar writes repaint its contents before the response.
      // Reopen against the replacement chip so a refusal stays visible.
      if (!el.isConnected) {
        const next = [...document.querySelectorAll(`[data-chip-version="${CSS.escape(version.id)}"]`)].find((chip) => chip.getClientRects().length);
        if (next) { open(next, fam, version, repaint); mounted.el.querySelector('[data-cal-detail-status]').textContent = message; }
      } else status.textContent = message;
    };
    const save = el.querySelector('[data-kbd-move]');
    save.onclick = async () => {
      const input = el.querySelector('[data-kbd-when]');
      if (!input.value || !input.validity.valid) { status.textContent = 'Choose a date and time.'; return; }
      save.disabled = true;
      const result = await window.deskV1CalendarRescheduleVersion(version.id, new Date(input.value));
      if (result?.ok) close();
      else { reportFailure(result === true ? 'Time unchanged' : `Time not saved: ${result?.error || 'this version cannot be moved'}`); save.disabled = false; }
    };
    el.querySelector('[data-cal-unschedule]').onclick = async () => {
      const result = await window.DeskV1CalendarUnschedule.clear(version.id, repaint);
      if (result?.ok) close(); else reportFailure(`Time not cleared: ${result?.error || 'this version cannot be changed'}`);
    };
    el.querySelector('[data-cal-edit-post]').onclick = () => { close(); window.DeskV1Composer.open(window.DeskV1Store.state().campaigns.find((c) => c.id === fam.campaignId), fam); };
    const review = el.querySelector('[data-cal-open-review]');
    if (review) review.onclick = () => { close(); window.deskV1Nav('review', { campaignId: fam.campaignId, versionId: version.id }); };
    el.querySelector('[data-cal-detail-close]').onclick = close;
    window.DeskV1Kit.placePopover(el, trigger);
    el.querySelector('[data-cal-detail-close]').focus({ preventScroll: true });
  }
  document.addEventListener('pointerdown', (e) => { if (mounted && !mounted.el.contains(e.target) && !mounted.trigger.contains(e.target)) close(); }, true);
  document.addEventListener('keydown', (e) => { if (mounted && e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); } }, true);
  window.DeskV1CalendarDetail = { open, close };
})();

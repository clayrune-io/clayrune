// One unscheduled card per family. Destination creation is the composer's sync
// command, bounded by this family's campaign; timing is the calendar command.
(function () {
  const esc = (s) => window.esc(String(s == null ? '' : s));
  const state = () => window.DeskV1Store.state();
  const versions = (fam) => window.DeskV1ComposerChannels.versions(fam);
  const when = (v) => window.deskV1CalendarVersionWhen(v);
  let drag = null;
  function items(families) {
    return families.filter((f) => !f.archivedAt && (!versions(f).length || versions(f).some((v) => !when(v) && !window.DeskV1ComposerChannels.sent(v))));
  }
  function html(families) {
    return `<div class="desk-v1-cal-unscheduled pd-drop-target" data-cal-unschedule-tray>
      <div class="desk-v1-cal-unscheduled-label">Unscheduled · drag a scheduled chip here to remove its time</div>
      <div class="desk-v1-cal-unscheduled-tray">${items(families).map((fam) => {
        const first = versions(fam).find((v) => !when(v));
        return `<div class="desk-v1-cal-family-card" data-cal-preview-family="${esc(fam.id)}">
          <button type="button" class="desk-v1-cal-unscheduled-card" data-unsched-family="${esc(fam.id)}" ${first ? `data-unsched-version="${esc(first.id)}"` : ''} aria-label="Schedule ${esc(fam.title)}">
          <span>${esc(fam.title)}</span><span class="desk-v1-cal-badges">${versions(fam).map((v) => {
            const ch = state().channels.find((c) => c.id === v.channelId);
            return ch ? window.DeskV1Kit.channelBadge(ch) : '';
          }).join('') || 'No destinations yet'}</span></button>${window.DeskV1CalendarPreview.button(fam.title)}</div>`;
      }).join('') || '<span class="desk-v1-composer-hint">All posts have a time.</span>'}</div></div>`;
  }
  function reason(fam, channelId) {
    const camp = state().campaigns.find((c) => c.id === fam.campaignId);
    const ch = state().channels.find((c) => c.id === channelId);
    if (!ch || !(camp?.plan.accounts || []).includes(channelId)) return 'Outside this post’s campaign accounts';
    if (fam.archivedAt) return 'This post is archived';
    if (ch.health === 'held') return window.DeskV1Kit.channelCapabilityCopy(ch);
    const v = versions(fam).find((v) => v.channelId === channelId);
    if (v && window.DeskV1ComposerChannels.sent(v)) return 'This destination has already been sent';
    if (v && when(v)) return 'This destination already has a time; move its calendar chip';
    if (ch.platform === 'youtube') return window.DeskV1YouTubePost.errors(v?.body || fam.body, fam.assets)[0] || '';
    // MediaFormats validates uploads, not platform destinations. No shared
    // per-platform media-duration refusal exists; unknown cases are allowed.
    return '';
  }
  async function schedule(fam, channelId, at, repaint) {
    const refusal = reason(fam, channelId);
    if (refusal) { window.DeskV1Kit.toast(refusal); return { ok: false, error: refusal }; }
    if (!at || isNaN(at)) return { ok: false, error: 'Choose a date and time.' };
    try {
      await window.deskV1QueuePiece(fam.id, async () => {
        // Recheck after an earlier write settles; a double drop must not create duplicates.
        const refusal = reason(fam, channelId);
        if (refusal) throw new Error(refusal);
        const camp = state().campaigns.find((c) => c.id === fam.campaignId);
        const selected = new Set(versions(fam).map((v) => v.channelId)); selected.add(channelId);
        await window.DeskV1ComposerChannels.sync(camp, { fam, selected, repaint });
      });
      const v = versions(fam).find((v) => v.channelId === channelId);
      const result = await window.deskV1CalendarRescheduleVersion(v.id, at);
      repaint();
      return result;
    } catch (e) {
      const error = e.message || String(e); window.DeskV1Kit.toast(`Time not saved: ${error}`); repaint();
      return { ok: false, error };
    }
  }
  function keyboard(card, fam, repaint) {
    const existing = card.parentElement.querySelector('[data-cal-tray-picker]');
    if (existing) { existing.remove(); return; }
    const camp = state().campaigns.find((c) => c.id === fam.campaignId);
    const pop = document.createElement('div'); pop.dataset.calTrayPicker = ''; pop.className = 'desk-v1-cal-tray-picker';
    pop.innerHTML = `<label>Channel <select data-cal-tray-channel>${window.DeskV1ComposerChannels.accounts(camp).map((ch) => {
      const refusal = reason(fam, ch.id);
      return `<option value="${esc(ch.id)}" ${refusal ? 'disabled' : ''}>${esc(ch.label || ch.identity || ch.platform)}${refusal ? ` — ${esc(refusal)}` : ''}</option>`;
    }).join('')}</select></label><label>Date and time <input type="datetime-local" data-cal-tray-time></label>
      <button type="button" data-cal-tray-save>Set time</button><button type="button" data-cal-tray-cancel>Cancel</button><p role="status"></p>`;
    card.parentElement.appendChild(pop); window.DeskV1Kit.placePopover(pop, card);
    pop.querySelector('[data-cal-tray-cancel]').onclick = () => { pop.remove(); card.focus(); };
    pop.onkeydown = (e) => { if (e.key === 'Escape') { e.stopPropagation(); pop.remove(); card.focus(); } };
    pop.querySelector('[data-cal-tray-save]').onclick = async (e) => {
      const input = pop.querySelector('[data-cal-tray-time]');
      if (!input.value || !input.validity.valid) { pop.querySelector('[role="status"]').textContent = 'Choose a date and time.'; return; }
      e.target.disabled = true;
      const result = await schedule(fam, pop.querySelector('select').value, new Date(input.value), repaint);
      if (!result?.ok && pop.isConnected) { pop.querySelector('[role="status"]').textContent = result?.error || 'Time unchanged'; e.target.disabled = false; }
    };
    pop.querySelector('select').focus();
  }
  function bind(el, repaint) {
    const cells = () => [...el.querySelectorAll('.desk-v1-cal-cell[data-channel-id]')];
    const clear = () => cells().forEach((cell) => {
      cell.classList.remove('pd-drop-hover', 'desk-v1-cal-target-ok', 'desk-v1-cal-target-refused');
      cell.removeAttribute('title'); const p = cell.querySelector('.desk-v1-cal-cell-preview'); if (p) p.textContent = '';
    });
    el.querySelectorAll('[data-unsched-family]').forEach((card) => {
      const fam = state().families.find((f) => f.id === card.dataset.unschedFamily);
      card.onclick = () => { if (card.dataset.dragged) { delete card.dataset.dragged; return; } keyboard(card, fam, repaint); };
      card.addEventListener('pointerdown', (e) => {
        if (e.target.closest('[data-cal-info]')) return;
        window.PointerDrag.begin(card, e, {
          isDragActive: () => !!drag, getDragState: () => drag, setDragState: (s) => { drag = s; }, data: { familyId: fam.id },
          draggingClass: 'desk-v1-cal-unscheduled-dragging', ghostClass: 'pd-ghost desk-v1-cal-chip-ghost', ghostHTML: () => card.innerHTML,
          ghostRotationDeg: -3, ghostOffsetX: 18, ghostOffsetY: 18,
          onActivate: () => {
            card.dataset.dragged = 'true'; window.DeskV1CalendarPreview.hide();
            cells().forEach((cell) => {
              const refusal = reason(fam, cell.dataset.channelId);
              cell.classList.add(refusal ? 'desk-v1-cal-target-refused' : 'desk-v1-cal-target-ok');
              cell.title = refusal || 'Schedule on this channel';
            });
          },
          onMove: (s, x, y) => {
            cells().forEach((c) => { c.classList.remove('pd-drop-hover'); c.querySelector('.desk-v1-cal-cell-preview').textContent = ''; });
            const cell = document.elementFromPoint(x, y)?.closest('.desk-v1-cal-cell[data-channel-id]');
            if (!cell || !el.contains(cell)) return;
            const refusal = reason(fam, cell.dataset.channelId);
            cell.classList.add('pd-drop-hover'); cell.querySelector('.desk-v1-cal-cell-preview').textContent = refusal || `Schedule ${cell.dataset.dayKey}`;
          },
          onDrop: (s, x, y) => {
            const cell = document.elementFromPoint(x, y)?.closest('.desk-v1-cal-cell[data-channel-id]');
            return cell && el.contains(cell) ? { channelId: cell.dataset.channelId, day: cell.dataset.dayKey } : null;
          },
          afterDrop: (s, target) => {
            if (target) schedule(fam, target.channelId, new Date(target.day + 'T12:00:00'), repaint);
          },
          onTeardown: clear,
          onEnd: () => { setTimeout(() => { delete card.dataset.dragged; }, 0); },
        });
      });
    });
  }
  window.DeskV1CalendarTray = { html, bind, items, reason };
})();

// Same schedule command as the campaign calendar, including its confirmation
// and approval withdrawal. Keep different per-channel times visible individually.
(function () {
  const esc = (s) => window.esc(String(s == null ? '' : s));
  function localValue(date) {
    if (!date || isNaN(date)) return '';
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }
  function render(el, fam) {
    const versions = window.DeskV1ComposerChannels.versions(fam);
    el.innerHTML = `<strong>When</strong><p class="desk-v1-composer-hint">Times use this device’s time zone. These are the same times shown in the campaign’s When calendar.</p>${versions.map((v) => {
      const ch = window.DeskV1Store.state().channels.find((c) => c.id === v.channelId);
      return `<label class="desk-v1-composer-time">${esc(ch?.label || ch?.identity || v.channelId)}
        <input type="datetime-local" data-composer-time="${esc(v.id)}" aria-label="When for ${esc(ch?.label || v.channelId)}" value="${localValue(window.deskV1CalendarVersionWhen(v))}" ${window.DeskV1ComposerChannels.sent(v) ? 'disabled' : ''}>
        <button type="button" class="btn-secondary" data-composer-time-save="${esc(v.id)}" ${window.DeskV1ComposerChannels.sent(v) ? 'disabled' : ''}>Set time</button></label>`;
    }).join('')}<p data-composer-time-status role="status" aria-live="polite"></p>`;
    el.querySelectorAll('[data-composer-time-save]').forEach((b) => b.onclick = async () => {
      const input = el.querySelector(`[data-composer-time="${CSS.escape(b.dataset.composerTimeSave)}"]`);
      const status = el.querySelector('[data-composer-time-status]');
      if (!input.value || !input.validity.valid) { status.textContent = 'Choose a date and time.'; return; }
      b.disabled = true;
      try {
        const result = await window.deskV1CalendarRescheduleVersion(b.dataset.composerTimeSave, new Date(input.value));
        if (result?.ok) status.textContent = 'Time saved';
        else if (result === true) status.textContent = 'Time unchanged'; // confirmation cancelled
        else status.textContent = `Time not saved${result?.error ? ': ' + result.error : '.'}`;
        const v = fam.versions.find((v) => v.id === b.dataset.composerTimeSave);
        input.value = localValue(window.deskV1CalendarVersionWhen(v));
      } catch (e) { status.textContent = `Time not saved: ${e.message || e}`; }
      finally { b.disabled = false; }
    });
  }
  window.DeskV1ComposerWhen = { render };
})();

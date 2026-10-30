// A campaign whose plan end date has passed. Renew opens the next TERM, but the plan's end
// date gates posting first (mc/desk_tick.py), so Renew cannot get a post out. The fix is the
// existing control: Brief > Limits > Ends, then Launch > Approve the new bounds. This module
// only says that and takes the person to that field; it extends nothing and approves nothing.
// Server twin: mc/desk_campaign_hold.py (same words, same cutoff: the end of the end date, UTC).
(function () {
  const CODE = 'campaign_ended';
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // The plan end date (YYYY-MM-DD) once it has fully passed, else null.
  function endedOn(camp) {
    const d = camp && camp.plan && camp.plan.end && camp.plan.end.date;
    if (!d) return null;
    const iso = String(d).slice(0, 10);
    return iso < new Date().toISOString().slice(0, 10) ? iso : null;
  }
  function message(date) { return `This campaign ended on ${date}. Extend its end date, then approve to keep posting.`; }

  // Opens the campaign's own Ends field in Brief and puts the cursor there.
  function extend(campaignId) {
    if (typeof window.deskV1GotoCampaignPanel !== 'function') return;
    window.deskV1GotoCampaignPanel('how', { campaignId });
    let tries = 0;
    const focus = () => {
      const f = document.querySelector('[data-how-limit="end_date"]');
      if (f) { f.scrollIntoView({ block: 'center' }); f.focus({ preventScroll: true }); } else if (++tries < 20) setTimeout(focus, 50);
    };
    setTimeout(focus, 0);
  }
  function buttonHTML(campaignId) {
    return `<button type="button" class="desk-v1-camp-pause-btn" data-extend-end-date="${esc(campaignId)}">Extend end date</button>`;
  }
  function bind(root) {
    if (!root) return;
    root.querySelectorAll('[data-extend-end-date]').forEach((b) => { b.onclick = () => extend(b.getAttribute('data-extend-end-date')); });
  }

  // A held version the publisher refused because the plan end passed: a button in place of "Renew".
  function failed(version) { return !!(version && version.failure && version.failure.code === CODE); }
  function linkHTML(version, campaignId) { return failed(version) && campaignId ? ` ${buttonHTML(campaignId)}` : ''; }

  window.DeskV1CampaignEnded = { CODE, endedOn, message, extend, buttonHTML, bind, failed, linkHTML };
})();

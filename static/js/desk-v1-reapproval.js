// Re-approval, made visible (MC-1116). A started campaign whose limits were
// widened after the human approved them cannot publish until the human approves
// them again on Launch. That used to be silent: Review refused with a toast and
// no way forward, a widening edit said nothing, and Launch did not say what had
// changed. This module owns the four pieces that fix it:
//
//   changes(camp)    each widened limit, old -> new (Launch's list, the notes)
//   awaiting(camp)   a started campaign with at least one such change
//   noteHTML/fillSlots   the inline "this widens the campaign" note at a field
//   badgeHTML        the "Awaiting approval" badge linking to Launch
//   refusalHTML      Review's "Go to Launch to approve" block
//
// It decides nothing and approves nothing. The only approve control stays the
// Launch panel's "Approve the new bounds" (desk-v1-campaign.js _approveBounds):
// every link here is navigation to it.
//
// `diff` mirrors mc/desk.py bounds_widen dimension by dimension (the server is the
// authority: it is what holds the publisher). tests/test_desk_reapproval_parity.py
// pins the two together. Two known differences, both pinned there too: the kit
// (and so this file) treats a lower `min_gap_h` as a widening and the server does
// not; the server treats own -> project budget source as one and the kit did not.
// This file reports the union, so a hold the server applies is never invisible.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const NOTE_TEXT = 'This widens the campaign; it won\'t post until you approve it on Launch.';
  const LINK_TEXT = 'Go to Launch to approve';

  function _idOf(a) { return typeof a === 'string' ? a : a && a.channel_id; }
  function _ms(s) { return new Date(s).getTime(); }
  function _money(n) { return `$${(Math.round(n * 100) / 100).toFixed(2).replace(/\.00$/, '')}`; }
  function _date(s) {
    const d = /^\d{4}-\d{2}-\d{2}$/.test(String(s)) ? new Date(`${s}T00:00:00`) : new Date(s);
    if (Number.isNaN(d.getTime())) return String(s);
    try { return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(d); } catch (e) { return String(s); }
  }

  // The widened limits between two bounds objects (`window.deskV1CampaignBounds`
  // shape), as rows { key, label, from, to }. `nameOf` turns an account id into
  // what the person calls it.
  function diff(prev, next, nameOf) {
    prev = prev || {}; next = next || {};
    const name = nameOf || ((id) => id);
    const rows = [];
    const prevIds = (prev.accounts || []).map(_idOf);
    const nextIds = (next.accounts || []).map(_idOf);
    if (nextIds.some((id) => !prevIds.includes(id))) {
      rows.push({ key: 'accounts', label: 'Accounts', from: prevIds.map(name).join(', ') || 'none', to: nextIds.map(name).join(', ') });
    }
    const pc = prev.cadence || {}; const nc = next.cadence || {};
    if (pc.per_week != null && (nc.per_week == null || nc.per_week > pc.per_week)) {
      rows.push({ key: 'per_week', label: 'Posts a week', from: String(pc.per_week), to: nc.per_week == null ? 'no limit' : String(nc.per_week) });
    }
    if (pc.min_gap_h != null && nc.min_gap_h != null && nc.min_gap_h < pc.min_gap_h) {
      rows.push({ key: 'min_gap_h', label: 'Minimum gap', from: `${pc.min_gap_h}h`, to: `${nc.min_gap_h}h` });
    }
    const pe = prev.end || {}; const ne = next.end || {};
    if (pe.date && (!ne.date || _ms(ne.date) > _ms(pe.date))) {
      rows.push({ key: 'end_date', label: 'End date', from: _date(pe.date), to: ne.date ? _date(ne.date) : 'no end date' });
    }
    if (pe.post_cap != null && (ne.post_cap == null || ne.post_cap > pe.post_cap)) {
      rows.push({ key: 'post_cap', label: 'Post cap', from: String(pe.post_cap), to: ne.post_cap == null ? 'no cap' : String(ne.post_cap) });
    }
    const pt = prev.term || {}; const nt = next.term || {};
    if (pt.ends && (!nt.ends || _ms(nt.ends) > _ms(pt.ends))) {
      rows.push({ key: 'term_ends', label: 'Term ends', from: _date(pt.ends), to: nt.ends ? _date(nt.ends) : 'no end' });
    }
    const pb = prev.budget || {}; const nb = next.budget || {};
    if ((nb.amount || 0) > (pb.amount || 0)) {
      rows.push({ key: 'budget', label: 'Budget', from: _money(pb.amount || 0), to: _money(nb.amount || 0) });
    } else if (pb.source === 'own' && nb.source === 'project') {
      rows.push({ key: 'budget', label: 'Budget', from: 'this campaign\'s own', to: 'the project pool' });
    }
    return rows;
  }

  function _channelName(id) {
    const list = (window.DeskV1Store && window.DeskV1Store.state().channels) || [];
    const ch = list.find((c) => c.id === id);
    return (ch && (ch.label || ch.identity)) || id;
  }

  function changes(camp) {
    const ap = camp && camp.approval;
    if (!ap || !ap.bounds || typeof window.deskV1CampaignBounds !== 'function') return [];
    return diff(ap.bounds, window.deskV1CampaignBounds(camp), _channelName);
  }

  // A campaign that is live or paused and has a change waiting for approval:
  // the same two states the server calls "awaiting" (mc/desk.py awaiting_approval).
  function awaiting(camp) {
    return !!camp && (camp.state === 'active' || camp.state === 'paused') && changes(camp).length > 0;
  }

  function _goLaunch(campaignId) {
    if (typeof window.deskV1GotoCampaignPanel === 'function') window.deskV1GotoCampaignPanel('launch', { campaignId });
  }

  function bindLinks(root) {
    root.querySelectorAll('[data-reapproval-launch]').forEach((b) => {
      b.onclick = (e) => { e.preventDefault(); _goLaunch(b.getAttribute('data-reapproval-launch')); };
    });
  }

  // ── Launch: "Budget: $0 → $1", one line per widened limit ──────────────
  function changesListHTML(camp) {
    const rows = changes(camp);
    if (!rows.length) return '';
    return `<ul class="desk-v1-reapproval-changes" data-reapproval-changes>${rows.map((r) =>
      `<li data-reapproval-change="${esc(r.key)}"><span class="desk-v1-reapproval-changelabel">${esc(r.label)}:</span> ${esc(r.from)} → ${esc(r.to)}</li>`).join('')}</ul>`;
  }

  // ── inline note at an edited field ─────────────────────────────────────
  // A page puts an empty slot beside each bound field once; `fillSlots` fills
  // the ones whose limit is currently widened and empties the rest, so one call
  // after any edit keeps every note true.
  function noteHTML(key) { return `<div class="desk-v1-reapproval-slot" data-reapproval-slot="${esc(key)}"></div>`; }

  function fillSlots(root, camp) {
    if (!root) return;
    const live = camp && camp.state !== 'draft' && camp.state !== 'proposed';
    const keys = live ? changes(camp).map((r) => r.key) : [];
    root.querySelectorAll('[data-reapproval-slot]').forEach((slot) => {
      slot.innerHTML = keys.includes(slot.getAttribute('data-reapproval-slot'))
        ? `<div class="desk-v1-reapproval-note" data-reapproval-note role="status">${esc(NOTE_TEXT)} <button type="button" class="desk-v1-reapproval-link" data-reapproval-launch="${esc(camp.id)}">${esc(LINK_TEXT)} ›</button></div>`
        : '';
    });
    bindLinks(root);
    _syncSummary(camp);
  }

  // The summary bar is outside the page that was edited; keep its badge true.
  function _syncSummary(camp) {
    const el = document.getElementById('desk-v1-camp-summary');
    if (!el || !camp || typeof window.deskV1FillCampaignSummary !== 'function') return;
    if (!!el.querySelector('[data-reapproval-badge]') === awaiting(camp)) return;
    window.deskV1FillCampaignSummary(el, { campaignId: camp.id });
  }

  // ── badge ──────────────────────────────────────────────────────────────
  function badgeHTML(camp) {
    if (!awaiting(camp)) return '';
    return `<button type="button" class="desk-v1-reapproval-badge" data-reapproval-badge data-reapproval-launch="${esc(camp.id)}" title="${esc(NOTE_TEXT)}">⚠ Awaiting approval</button>`;
  }

  // ── Review: the server refused because the campaign awaits approval ────
  const REFUSAL_CODE = 'campaign_awaiting_approval';
  function isRefusal(err) { return !!err && err.code === REFUSAL_CODE; }

  function refusalHTML(campaignId) {
    if (!campaignId) return '';
    return `<div class="desk-v1-reapproval-refusal" data-reapproval-refusal role="alert">
      <div>Not approved: this campaign's limits changed since you approved them, so nothing in it can post until you approve them again on Launch.</div>
      <button type="button" class="desk-v1-reapproval-launchbtn" data-reapproval-launch="${esc(campaignId)}" data-reapproval-goto>${esc(LINK_TEXT)}</button>
    </div>`;
  }

  window.DeskV1Reapproval = {
    NOTE_TEXT, LINK_TEXT, REFUSAL_CODE,
    diff, changes, awaiting, changesListHTML, noteHTML, fillSlots, badgeHTML, refusalHTML, isRefusal, bindLinks,
  };
})();

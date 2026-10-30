// Desk v1 — the Where screen's SOURCES tiles and the mark on a column header
// (Ron 2026-10-16: "logos and appearance must be redone with proper logos,
// organized, no text running out of frames"). Split out of desk-v1-where.js (one
// unit, one file): that module owns the campaign accounts and overview, this one owns how a
// connected account LOOKS.
//
// Every tile is the same four-row card — mark + name, status chip, reason, action —
// so tiles in one grid are the same size and the Connect button sits on one
// baseline. Nothing is ever allowed to run out of its card: the name and the
// reason are clipped (ellipsis / 2-line clamp) and the full text rides on `title`.
// The mark comes from the one source of marks, desk-v1-service-icons.js (vendor
// SVGs in static/desk-icons/); the account name stays beside it (UX-02).
//
// Window-bridged module, no `import` (ground rule 1). Pure string builders: the
// caller (desk-v1-where.js) wires the clicks onto the nodes it gets.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // The mark for an account. The account name rides along so a platform with no
  // vendor logo (a blog) gets its monogram from the name, never a bare "?".
  function markHTML(ch) {
    const it = { platform: ch && ch.platform, brand: ch && ch.platform, name: (ch && (ch.identity || ch.label)) || '' };
    return window.DeskV1ServiceIcons ? window.DeskV1ServiceIcons.markHTML(it, false) : '';
  }

  // tone: ok (usable) | warn (needs action) | dim (nothing to do here)
  function chipHTML(word, tone, extra) {
    return `<span class="desk-v1-where-chip" data-tone="${esc(tone)}"${extra || ''}>${esc(word)}</span>`;
  }
  // Use Connections' account-to-project rule for saved read evidence, too.
  function homeProjectId(ch) {
    const fx = window.DeskV1Store.state();
    const camp = (fx.campaigns || []).find((c) => c.projectId && (c.plan?.accounts || []).includes(ch.id));
    return camp?.projectId || fx.projects?.[0]?.id || null;
  }
  function accountStatus(ch) {
    const cs = window.DeskV1ConnectionStatus;
    return cs.accountStatus(ch, cs.coverageRow(homeProjectId(ch), ch.platform, ch.id));
  }
  function loadStatus(repaint) {
    if (window.DeskV1Store.live()) window.DeskV1ConnectionStatus?.loadCoverage(
      (window.DeskV1Store.state().channels || []).filter((ch) => !ch.preview).map(homeProjectId), repaint);
  }
  function statusChipHTML(ch) {
    if (!ch || !ch.publish || !window.DeskV1ConnectionStatus) return '';
    const st = accountStatus(ch);
    return chipHTML(st.word, st.kind === 'ready' ? 'ok' : 'warn');
  }
  function recoveryHTML(ch) {
    if (!ch || !ch.publish || !window.DeskV1ConnectionStatus ||
        accountStatus(ch).kind === 'ready') return '';
    return '<button type="button" class="desk-v1-where-connect" data-where-connect>Open Connections ›</button>';
  }
  function previewChipHTML(ch) {
    return ch && ch.preview ? chipHTML('Preview', 'dim', ' data-preview data-short="Preview" title="Preview · not connected"') : '';
  }

  // One card. `cls`/`attrs` carry the caller's wiring hooks; `status` is the chip
  // row, `reason` the optional clamped line, `action` the optional bottom button.
  function _tile(ch, plat, o) {
    const handle = o.handle != null ? o.handle : ch.identity;
    return `<div class="desk-v1-where-source${o.cls || ''}" ${o.attrs} data-channel-id="${esc(ch.id)}" data-platform="${esc(ch.platform)}"${o.title ? ` title="${esc(o.title)}"` : ''}>
      <div class="desk-v1-where-tile-head">
        ${markHTML(ch)}
        <div class="desk-v1-where-tile-id">
          <div class="desk-v1-where-source-handle" title="${esc(handle)}">${esc(handle)}</div>
          <div class="desk-v1-where-source-plat">${esc(plat.word)}</div>
        </div>
      </div>
      <div class="desk-v1-where-tile-status">${o.status}${previewChipHTML(ch)}</div>
      ${o.reason ? `<div class="desk-v1-where-source-reason" ${o.reasonAttr} title="${esc(o.reason)}">${esc(o.reason)}</div>` : ''}
      <div class="desk-v1-where-tile-foot">${o.action || ''}</div>
    </div>`;
  }

  // A connected account the campaign can use (click or Enter adds its column).
  function sourceHTML(ch, plat, used) {
    return _tile(ch, plat, {
      cls: used ? ' is-used' : '',
      attrs: `role="button" tabindex="0" data-where-source aria-label="${esc(`${ch.identity} on ${plat.word}${used ? ', already on this campaign' : '. Press Enter to add its column'}`)}"`,
      status: (used ? chipHTML('On campaign', 'dim') : '') +
        (statusChipHTML(ch) || (used ? '' : chipHTML('Ready', 'ok'))),
      action: recoveryHTML(ch),
    });
  }

  // A live account carries `publish` (R1-W S5): the tile names the account and says
  // why it cannot be used, the reason the server computed (no token in the vault,
  // LinkedIn app review pending). A demo tile has neither and stays as it was.
  function offHTML(ch, plat) {
    const pub = ch.publish || null;
    return _tile(ch, plat, {
      cls: ' desk-v1-where-source-off',
      attrs: 'role="button" tabindex="0" data-where-source data-where-source-off',
      handle: ch.identity || ch.label,
      title: pub && pub.reason ? pub.reason : '',
      status: statusChipHTML(ch) || chipHTML('Not connected', 'warn'),
      reason: pub && pub.reason ? pub.reason : '',
      reasonAttr: 'data-where-reason',
      action: '<button type="button" class="desk-v1-where-connect" data-where-connect>Connect ›</button>',
    });
  }

  // A read-only account (the Desk reads it, never publishes there): same card, no
  // Connect button and no drag handle, since it is not a place to post.
  function readOnlyHTML(ch, plat) {
    return _tile(ch, plat, {
      cls: ' desk-v1-where-source-off',
      attrs: 'role="button" tabindex="0" data-where-source data-where-source-readonly',
      title: 'The Desk reads this account and does not publish there',
      status: chipHTML('Read only', 'dim', ' data-where-readonly') + statusChipHTML(ch),
      action: recoveryHTML(ch),
    });
  }

  window.DeskV1WhereTiles = { markHTML, sourceHTML, offHTML, readOnlyHTML, previewChipHTML, statusChipHTML, loadStatus };
})();

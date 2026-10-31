// Desk v1 - serial default names for unnamed pieces (Ron, 2026-10-31): Video1,
// Video2, Post1, ... The SERVER owns the name in live mode (mc/desk_piece_names.py;
// the create response carries `title` and `defaultTitle`). This is the same rule for
// DEMO mode only, where no request is made, so a create-card still gets a name.
// Window-bridged, no `import`.
(function () {
  function prefix(kind) { const k = String(kind || 'piece').toLowerCase(); return k.charAt(0).toUpperCase() + k.slice(1); }

  // `<Kind><n>`, n one above the highest serial of `kind` among the campaign's pieces.
  function next(families, campaignId, kind) {
    const re = new RegExp('^' + prefix(kind) + '(\d+)$', 'i');
    let top = 0;
    (families || []).forEach((f) => {
      if (f.campaignId !== campaignId || f.kind !== kind) return;
      [f.title, f.defaultTitle].forEach((n) => { const m = re.exec(String(n || '').trim()); if (m) top = Math.max(top, Number(m[1])); });
    });
    return prefix(kind) + (top + 1);
  }

  window.DeskV1PieceNames = { next };
})();

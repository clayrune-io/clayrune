// Desk v1 — Connect wizard: a saved X account that already holds a sign-in for the saved app keeps it (MC-1114).
// Window-bridged, no `import`. Asks GET /api/desk/connect/signin-reuse/<account> once per account (metadata only: no
// token is read) and remembers the answer. The API setup screen uses it to skip "Sign in and approve your app" and to
// Save without a new sign-in; the server decides again at Save (mc/desk_connect/signin_reuse.py), so a stale answer
// here costs a sign-in, never a wrong write.
(function () {
  const _seen = new Map();          // account id -> { reusable, reason } | 'pending'

  function check(accountId, repaint) {
    if (!accountId || _seen.has(accountId)) return;
    _seen.set(accountId, 'pending');
    fetch(`/api/desk/connect/signin-reuse/${encodeURIComponent(accountId)}`).then((r) => r.json()).then((d) => {
      _seen.set(accountId, { reusable: !!(d && d.reusable), reason: (d && d.reason) || '' });
      if (repaint) repaint();
    }).catch(() => { _seen.set(accountId, { reusable: false, reason: '' }); });
  }
  function reusable(accountId) { const v = _seen.get(accountId); return !!(v && v !== 'pending' && v.reusable); }
  function forget(accountId) { if (accountId) _seen.delete(accountId); else _seen.clear(); }
  function noteHTML(accountId) {
    return reusable(accountId)
      ? '<div class="desk-v1-cfw-fact-text" data-api-reuse>This account is already signed in with this app. Save keeps that sign-in: nothing is signed in again, and only your permission choices change.</div>' : '';
  }

  window.DeskV1ConnectSigninReuse = { check, reusable, forget, noteHTML };
})();

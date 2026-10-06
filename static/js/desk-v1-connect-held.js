// Desk v1 — Connections: the browser sign-in, done on the Details step and HELD for the Save
// (Ron 2026-10-05: "if step 3 is the login details, all options should be covered there"; Dave's
// pick, option B). Before this, "Sign in with Higgsfield" only appeared on the Result step after
// Save, because the sign-in needed a saved account to attach its token to.
//
// Now the button is here. POST /api/desk/connect/<service>/start-held (dashboard passcode) opens
// the sign-in in its named browser pane exactly like the Result step used to, but the token the
// vendor hands back is NOT written: the server holds it in memory (mc/desk_oauth_hold.py) and the
// Save on the Review step claims it in the same passcode commit that writes the account and the
// vault entry. Backing out, changing the X app, or leaving the panel cancels it (the server drops
// it and revokes it at the vendor); a restart forgets it and the person signs in again.
//
// What this module keeps: the flow id and the `claim` the start answered with (the claim is the
// proof the Save and the cancel present, never a vendor token), the pane profile and the state.
// It never sees a token or a password. Window-bridged module, no `import` (ground rule 1).
// Server side: mc/blueprints/desk_held_signin_routes.py.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const POLL_LIMIT_MS = 10 * 60 * 1000;      // the spec's ten-minute sign-in ceiling
  const APP_KEYS = ['client_id', 'client_secret'];   // X's app: the sign-in is made WITH these, so changing them voids it
  let H = null;     // { service, method, label, ctx, phase: idle|starting|waiting|held|failed, message, flowId, claim, profile, authUrl, ttl }
  let _token = 0;   // a newer sign-in, a cancel or a reset ends an older poll

  function _pollMs() { return window.__deskGuidePollMs || 1500; }

  // Called each time the Details step is drawn: the same service+method keeps its sign-in, anything else drops it.
  function begin(service, method, label, ctx, accountId = null) {
    if (H && H.service === service && H.method === method && H.accountId === accountId) { H.ctx = ctx; H.label = label; return; }
    cancel();
    H = { service, method, label, ctx, accountId, phase: 'idle', message: '', flowId: '', claim: '', profile: '', authUrl: '', ttl: 0 };
  }

  // Drop what is held: tell the server (it revokes at the vendor), forget the claim. Safe to call any time.
  function cancel() {
    _token++;
    const old = H;
    H = null;
    if (old && old.claim && old.flowId && (old.phase === 'waiting' || old.phase === 'held') && old.ctx && old.ctx.api) {
      Promise.resolve(old.ctx.api('POST', `/api/desk/connect/flows/${encodeURIComponent(old.flowId)}/cancel`, { claim: old.claim })).catch(() => { /* the server expires it by itself */ });
    }
  }

  // The Save landed and consumed the held sign-in: forget it, no cancel (nothing is left to drop).
  function consumed() { _token++; H = null; }

  function held() { return !!(H && H.phase === 'held'); }
  function profile() { return H && H.phase === 'waiting' ? H.profile : ''; }     // the pane the sign-in page is open in

  // The Save request's `held` member, or null.
  function draft() { return held() ? { flow_id: H.flowId, claim: H.claim } : null; }

  async function _openPane() {
    if (!H) return;
    try {
      if (typeof window.openBrowserPane === 'function') await window.openBrowserPane(H.authUrl, null, null, H.profile);
    } catch (_) { /* the pane failing to open does not end the sign-in: "Open the sign-in page again" retries */ }
  }

  // The app the sign-in is made with, as typed in the details above (X's Client ID/Secret). Only these
  // two ever leave the form, and only in the passcode-gated start request.
  function _app() {
    const A = window.DeskV1ConnectAdapter;
    const r = A && A.mounted() ? A.read() : { fields: {} };
    if (r.error) return { error: `Fill in the details above first. ${r.error}` };
    const hold = {};
    APP_KEYS.forEach((k) => { if (r.fields && r.fields[k]) hold[k] = r.fields[k]; });
    return { hold };
  }

  async function _poll(mine, token) {
    const deadline = Date.now() + POLL_LIMIT_MS;
    while (Date.now() < deadline && token === _token && H === mine) {
      await new Promise((r) => setTimeout(r, mine.phase === 'held' ? _pollMs() * 8 : _pollMs()));
      if (token !== _token || H !== mine) return;
      let f;
      try { f = await mine.ctx.api('GET', `/api/desk/connect/flows/${encodeURIComponent(mine.flowId)}`); } catch (_) { continue; }
      if (f.status === 'held') {
        const was = mine.phase;
        mine.phase = 'held'; mine.ttl = f.held_ttl_s || 0; mine.message = '';
        if (was !== 'held') mine.ctx.repaint();
      } else if (f.status === 'error' || f.status === 'unknown') {
        mine.phase = 'failed';
        mine.message = f.message || 'The sign-in is no longer waiting. Sign in again.';
        mine.claim = ''; mine.flowId = '';
        mine.ctx.repaint();
        return;
      }
    }
    if (token === _token && H === mine && mine.phase === 'waiting') {
      mine.phase = 'failed'; mine.message = 'The sign-in took too long. Sign in again.'; mine.ctx.repaint();
    }
  }

  async function _signIn(root) {
    const mine = H;
    if (!mine || mine.phase === 'starting') return;
    const app = _app();
    if (app.error) { mine.phase = 'failed'; mine.message = app.error; mine.ctx.repaint(); return; }
    const before = { flowId: mine.flowId, claim: mine.claim, phase: mine.phase };
    _token++;
    mine.phase = 'starting'; mine.message = ''; mine.ctx.repaint();
    let res;
    try {
      // Opening a sign-in needs the dashboard passcode: the one proof an agent cannot forge.
      res = await window.humanProofFetch(`/api/desk/connect/${encodeURIComponent(mine.service)}/start-held`,
        { method: 'POST', body: JSON.stringify({ hold: app.hold, ...(mine.accountId ? { account_id: mine.accountId } : {}) }) },
        { title: 'Sign in', description: `Re-enter your dashboard passcode to sign in to ${mine.label}.` });
    } catch (e) {
      res = { ok: false, status: 0, body: { error: e && e.message ? e.message : 'could not reach the server' } };
    }
    if (H !== mine) return;
    if (res === null) {                    // cancelled at the passcode: nothing was sent, what was there stays
      Object.assign(mine, before); mine.ctx.repaint();
      if (mine.phase === 'waiting' || mine.phase === 'held') _poll(mine, ++_token);       // the poll was paused for the prompt
      return;
    }
    const out = res.body || {};
    if (!res.ok) {
      mine.phase = 'failed'; mine.message = out.error || out.message || `The sign-in could not start (HTTP ${res.status}).`;
      if (out.code === 'vault_locked' && window.DeskV1VaultGate) window.DeskV1VaultGate.attach(root);
      mine.ctx.repaint();
      return;
    }
    mine.flowId = out.flow_id; mine.claim = out.claim; mine.profile = out.profile; mine.authUrl = out.auth_url;
    mine.phase = 'waiting'; mine.message = '';
    mine.ctx.repaint();
    _openPane();
    _poll(mine, ++_token);
  }

  function _minutes(s) { return Math.max(1, Math.round((s || 0) / 60)); }

  // The "In the browser" option's body: the button for the phase the sign-in is in.
  function html() {
    if (!H) return '';
    const L = esc(H.label);
    let state = '', buttons = '';
    if (H.phase === 'idle' || H.phase === 'failed') {
      buttons = `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cf-primary" data-cfh-start>Sign in with ${L}</button>`;
    } else if (H.phase === 'starting') {
      buttons = '<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cf-primary" data-cfh-start disabled>Opening the sign-in…</button>';
    } else if (H.phase === 'waiting') {
      state = '<div class="desk-v1-cf-msg" data-cf-msg="ok" data-cfh-state="waiting" role="status">The sign-in page is open in its own browser pane. Finish signing in there; this page updates by itself.</div>';
      buttons = '<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cfh-open>Open the sign-in page again</button>'
        + '<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cfh-cancel>Cancel</button>';
    } else {
      state = `<div class="desk-v1-cf-msg" data-cf-msg="ok" data-cfh-state="held" role="status">Signed in to ${L}. It is kept in memory only, for about ${_minutes(H.ttl)} more minutes, until you press Save on the next step. Backing out discards it.</div>`;
      buttons = '<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cfh-start>Sign in again</button>'
        + '<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cfh-cancel>Discard it</button>';
    }
    const err = H.phase === 'failed' && H.message ? `<div class="desk-v1-cf-msg" data-cf-msg="error" data-cfh-state="failed" role="alert">${esc(H.message)}</div>` : '';
    return `<div class="desk-v1-cs-opt-body" data-cfh>
        <div class="desk-v1-rules-hint">Opens the ${L} sign-in in its own browser pane. Nothing is saved yet: the Save on step 4 stores it.</div>
        ${state}${err}
        <div class="desk-v1-cs-fill-row">${buttons}</div>
      </div>`;
  }

  // One row for Review: whether the sign-in is done, in words.
  function reviewRowHTML() {
    if (!H) return '';
    const done = H.phase === 'held';
    return `<div><dt>Sign-in</dt><dd data-cfh-review="${done ? 'held' : 'none'}">${done
      ? `Done in the browser: ${esc(H.label)} is signed in and held in memory. Save stores it.`
      : `Not done yet. Save opens the ${esc(H.label)} sign-in afterwards, or go back to step 3 and sign in first.`}</dd></div>`;
  }

  function bind(root) {
    if (!H) return;
    const mine = H;
    const start = root.querySelector('[data-cfh-start]');
    if (start && mine.phase !== 'starting') start.addEventListener('click', () => _signIn(root));
    const open = root.querySelector('[data-cfh-open]');
    if (open) open.addEventListener('click', () => _openPane());
    const gone = root.querySelector('[data-cfh-cancel]');
    if (gone) gone.addEventListener('click', () => {
      const ctx = mine.ctx;
      const keep = { service: mine.service, method: mine.method, label: mine.label, accountId: mine.accountId };
      cancel();
      begin(keep.service, keep.method, keep.label, ctx, keep.accountId);
      ctx.repaint();
    });
    // The sign-in was made with the X app typed above: change that app and the held token is no longer the one
    // the Save would pair it with, so it is dropped and the person signs in again.
    const host = root.querySelector('[data-cfa-host]');
    if (host && !host.dataset.cfhWatch) {
      host.dataset.cfhWatch = '1';
      host.addEventListener('input', (e) => {
        const key = e.target && e.target.dataset ? e.target.dataset.cfaField : '';
        if (!H || APP_KEYS.indexOf(key) < 0 || (H.phase !== 'waiting' && H.phase !== 'held')) return;
        const ctx = H.ctx, keep = { service: H.service, method: H.method, label: H.label, accountId: H.accountId };
        cancel();
        begin(keep.service, keep.method, keep.label, ctx, keep.accountId);
        H.phase = 'failed'; H.message = 'You changed the app details, so the sign-in was discarded. Sign in again.';
        ctx.repaint();
      });
    }
  }

  window.DeskV1ConnectHeld = { begin, cancel, consumed, held, profile, draft, html, bind, reviewRowHTML };
})();

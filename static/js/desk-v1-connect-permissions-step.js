// Desk v1 — Connections: the Connect wizard's PERMISSIONS screen (MC-1062 ticket 08,
// docs/desk_v1/connect_flow_tickets/08-permissions-screen.md; frame and rules in desk-v1-connect-wizard.js).
// Registers ONE Permissions screen through the window bridge; read-support owns the availability rule.
// The frame owns nothing here, and the old purpose editor is not touched.
//
// WHAT IT IS. Permissions are their own step, after Setup. Four different things are called "permission"
// and this screen keeps them apart, each with its own owner line:
//   Desk permission      Read / Post consent on the account (mc/desk_connect/permission_policy.py, ticket 05),
//                        enforced at the executors by tickets 06a-d. Written by POST .../permissions/commit.
//   (Browser sign-in)    NOT a row of its own since 2026-10-28 (Ron): when the account is read through a browser
//                        sign-in, ticking the ONE Read row and saving also puts the service's OWN domain on that
//                        sign-in's agent-read list (DeskV1ConnectBrowserPermission.apply). No site is typed or picked.
//   Credential           the saved login's own unattended-use setting. Shown as a fact: it is set in Secrets,
//                        and password fill stays human-started whatever it says.
//   MCP reach            one project or all projects. Under Dave's Q2 = A a custom server gets whole-server
//                        approval ("Use this server's tools"): there is NO Read/Post switch here, because
//                        nothing enforces one.
//
// ONE LIST FOR EVERY ACCOUNT (Ron 2026-10-28). Read this account's activity, and Publish: the same two rows with the
// same words whatever the connection (API, browser sign-in, no known route). What the chosen route cannot do stays
// on screen, disabled, with one sentence saying why.
//
// WHAT IT OFFERS. Only what the chosen route can actually run and the profile says it covers
// (variant.runtime.purposes + variant.coverage, the same two facts permission_policy._clean checks; the server stays
// the authority and refuses anything else). Read covers exactly the capabilities the route covers; Post covers
// `post` and nothing else, so no Reply, media, Article or broad listening is inferred. A reference-only record
// grants nothing. New grants start OFF; a saved account's existing scopes are shown as they are and kept unless
// the person changes that row. An account with no policy yet ("legacy") keeps working as it did until a row is
// touched: then the choices are recorded and what was left off stops.
//
// WHEN IT WRITES. Never on this screen. Choosing records a wish; the Review step calls `apply(accountId)` after
// the connection itself is saved. The Desk write and the browser-site write are two operations with two
// passcode prompts (no passcode is replayed); each reports its own status, so "Connection saved; permissions
// unchanged" can be said truthfully. `outcome()` gives the words.
//
// ONE FACT IT STATES AND DOES NOT FIX. 06d confirms a post by reading it back with `read_own/own_posts` on the
// same route. Post Allowed without that Read leaves the post "Submitted": the Desk cannot confirm it went out.
// The notice sits next to the choices; nothing here grants the missing Read for the person.
//
// WHICH ACCOUNT. Chosen by an earlier screen (not part of this ticket), handed over like the sign-in screen's:
//     setTarget({ kind, account: { id } | { new: { identity, ... } }, identity? })   // after api.select({ account })
// The screen matches an account type only once it has that target; MCP and reference-only match always.
import { readUnavailable } from './desk-v1-connect-read-support.js';
(function () {
  const W = window.DeskV1ConnectWizard;
  if (!W) return;
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const COVERED = ['documented', 'claimed'];                  // mc/desk_connect/route_readiness.COVERED
  const CAP_LABEL = { own_posts: 'Your posts', mentions: 'Mentions', replies: 'Replies', post_metrics: 'Post metrics', post: 'Post', reply: 'Reply', media: 'Media', article: 'Article', search: 'Search' };
  const VIA_WORD = { pane: 'through the browser sign-in', api: 'through the API' };
  // 06d (desk_publish.verify_post): the Read that lets the Desk confirm a post sent through a route.
  const CONFIRMS_POST = { x: { route_id: 'x-oauth', purpose: 'read_own', capability: 'own_posts' } };
  const PAID_API_READ = 'API reads may cost money. Choose this explicitly; a failed browser read never switches to the API.';
  const CONFIRM_NOTICE = 'Post is allowed, but reading your posts through the API is not. Clayrune cannot confirm a post went out, so it stays Submitted.';

  function _fresh() {
    return { loaded: '', loading: false, failed: false, policy: null, bound: null, bperm: null, vault: undefined,
      wish: { read: null, post: null, site: null }, touched: false, reach: { scope: 'project', projectId: '' },
      requestId: null, requestFp: '', applying: false, info: null, sel: null };
  }
  let M = _fresh();
  let _target = null;
  let _ctx = null;
  let _seq = 0;
  const sections = [];
  const sectionContext = api => ({ api, target: _target, profile: _profile() });
  function registerSection(section) { sections.push(section); }

  // ── what the frame is showing ───────────────────────────────────────────
  function _label(info) { return info && info.service && info.service.label ? info.service.label : 'the service'; }
  function _service(info) { return info && info.service ? info.service.id : ''; }
  function _variant(sel, info) {
    // A saved account reopens as type 'account' with its route's id as the variant (desk-v1-connect-saved-route.js).
    if (sel.type === 'account') return ((info && info.picker) || []).flatMap((x) => x.variants).find((v) => v.id === sel.variant) || null;
    const t = ((info && info.picker) || []).find((x) => x.id === sel.type);
    if (!t) return null;
    if (sel.variant) return t.variants.find((v) => v.id === sel.variant) || null;
    return t.variants.length === 1 ? t.variants[0] : null;
  }
  function _covered(v, purpose, kind) {
    return (v.coverage || []).filter((c) => c.purpose === purpose && c.account_kind === kind && COVERED.indexOf(c.status) >= 0).map((c) => c.capability);
  }

  // The Desk choices this route can honestly offer: `{ read, reads, post }`, each null or { op, purpose, route_id, via, caps, paid }.
  // `reads` is EVERY read route the one Read tick commits: this route's, plus (given `info`) the other routes of the service that
  // the account is configured to read through (a legacy X account signed in by OAuth but read through the browser needs the
  // browser route's scopes too, or its mentions and metrics fail the permission check). `read` is `reads[0]`.
  function _offers(v, kind, info) {
    const out = { read: null, reads: [], post: null };
    if (!v || !v.route_id || v.transport === 'mcp' || !kind) return out;
    const via = (v.runtime && v.runtime.purposes) || {};
    const basis = v.cost && v.cost.basis;
    if (via.read_own) {
      const caps = _covered(v, 'read_own', kind);
      if (caps.length) out.reads.push({ op: 'read', purpose: 'read_own', route_id: v.route_id, via: via.read_own, caps, paid: via.read_own === 'api' && basis !== 'free' });
    }
    if (via.publish && _covered(v, 'publish', kind).indexOf('post') >= 0) out.post = { op: 'post', purpose: 'publish', route_id: v.route_id, via: via.publish, caps: ['post'], paid: false };
    const want = _readVia();
    if (want && info) ((info.picker || []).flatMap((t) => t.variants || [])).forEach((x) => {
      if (!x || !x.route_id || x.route_id === v.route_id || ((x.runtime && x.runtime.purposes) || {}).read_own !== want) return;
      const e = _offers(x, kind).reads[0];
      if (e && !out.reads.some((o) => o.route_id === e.route_id)) out.reads.push(e);
    });
    out.read = out.reads[0] || null;
    return out;
  }

  function _isBrowser(v) { return !!v && v.transport === 'browser'; }
  // The Read row also grants the service's own domain on the sign-in when the Desk reads the account through a browser
  // sign-in: a browser route, or a saved account whose reading setting is the browser with a profile, even when the route
  // being reopened is the API one (Ron 2026-10-28: LinkedIn "Clayrune", profile linked_ronlevy, had no way to allow linkedin.com).
  function _readsByBrowser(v) {
    if (_isBrowser(v)) return true;
    return _readVia() === 'pane' && !!_profile();
  }
  function _channel() {
    const id = _target && _target.account && _target.account.id;
    return id && _ctx && _ctx.channels ? (_ctx.channels() || []).find((c) => c.id === id) || null : null;
  }
  // The reading a section has drafted but not saved yet ({ via, profile }). Sections apply AFTER the Desk and site writes, so
  // the grant has to be aimed at what the account will have once this save lands, not at what it had when the screen opened.
  function _drafted() {
    for (const s of sections) { const r = typeof s.reading === 'function' ? s.reading() : null; if (r) return r; }
    return null;
  }
  // 'api' | 'pane' | '' (nothing configured): how the account's activity is read once this save lands.
  function _readVia() {
    const d = _drafted();
    if (d && d.via) return d.via;
    const ch = _channel();
    return ch ? (ch.read_via || (ch.browser_profile ? 'pane' : '')) : '';
  }
  function _site(info) {
    const B = window.DeskV1ConnectBrowserPermission;
    return B ? B.siteOf(String((info && info.host) || '').replace(/^www\./i, '')) : '';
  }
  function _loginStep() { const L = window.DeskV1ConnectLoginStep; return L && typeof L.summary === 'function' ? L.summary() : null; }
  function _loginName() { const s = _loginStep(); return s && s.login ? s.login : ''; }
  // The sign-in the account will have AFTER save: the Setup step's choice, else a section's drafted profile, else the saved one.
  function _profile() {
    const s = _loginStep();
    if (s && s.profile) return String(s.profile).trim().toLowerCase();
    const d = _drafted();
    if (d && d.via === 'pane') return String(d.profile || '').trim().toLowerCase();       // a cleared field is a cleared profile, not the old one
    return _savedProfile();
  }
  function _savedProfile() {
    const s = _loginStep();
    if (s && s.profile) return String(s.profile).trim().toLowerCase();
    const ch = _channel();
    return ch && ch.browser_profile ? String(ch.browser_profile).trim().toLowerCase() : '';
  }

  // 'mcp' | 'reference' | 'account' (a Desk or browser choice exists) | 'none' (the type grants nothing).
  function _mode(sel, info) {
    if (sel.type === 'mcp') return _variant(sel, info)?.setup?.via === 'provider' ? 'none' : 'mcp';
    if (sel.type === 'reference') return 'reference';
    return _target ? 'account' : 'none';
  }

  // ── scopes: what is allowed, and what saving would record ───────────────
  const _key = (s) => `${s.purpose}|${s.capability}|${s.route_id}`;
  const _fp = (list) => list.map(_key).sort().join(',');
  function _base() { return M.policy && M.policy.state === 'explicit' ? (M.policy.scopes || []) : []; }
  function _mine(o, s) { return s.purpose === o.purpose && s.route_id === o.route_id && (o.op === 'read' || s.capability === 'post'); }
  function _initial(o) { return _base().filter((s) => _mine(o, s)); }
  function _checked(o) { const w = M.wish[o.op]; return w === null ? _initial(o).length > 0 : w; }
  // A row nobody touched keeps exactly the scopes it already has; a touched row is replaced by what it now offers.
  function _final(base, offers) {
    let out = base.slice();
    offers.reads.concat([offers.post]).forEach((o) => {
      if (!o || M.wish[o.op] === null) return;
      out = out.filter((s) => !_mine(o, s));
      if (M.wish[o.op]) o.caps.forEach((c) => out.push({ purpose: o.purpose, capability: c, route_id: o.route_id }));
    });
    return out;
  }
  function _confirmNotice(service, scopes) {
    const c = CONFIRMS_POST[service];
    if (!c) return '';
    const has = (p, cap, r) => scopes.some((s) => s.purpose === p && s.capability === cap && s.route_id === r);
    return has('publish', 'post', c.route_id) && !has(c.purpose, c.capability, c.route_id) ? CONFIRM_NOTICE : '';
  }
  // Granted for the sign-in the account will have after save: what was read for another sign-in (the profile field was edited
  // since) says nothing about this one, so it counts as not allowed until Save checks it.
  function _siteGranted() { return !!(M.bperm && M.bperm.exists && M.bperm.granted && M.bperm.profile === _profile()); }
  function _siteOn() { return !readUnavailable(M.info, _target?.kind) && (M.wish.site === null ? _siteGranted() : M.wish.site); }

  // The ONE Read row = the Desk read the route offers (if any) + the service's own domain on the browser sign-in (when the
  // account is read through one). `can.site`: the domain part applies; `can.reason`: why the row is disabled ('' = it works).
  const NEED_SIGNIN = 'Choose a browser sign-in first.';
  const READ_UNAVAILABLE = 'Reading is not available through this connection yet.';
  const POST_UNAVAILABLE = 'Posting is not available through this connection yet.';
  const READ_UNFINISHED = 'Reading through your sign-in is not switched on yet. Tick this and save to finish it.';
  const READ_DETAIL ="Agents read this account's own mentions, replies and post stats. They cannot click, type or post.";
  function _siteApplies(v, info) { return !!(window.DeskV1ConnectBrowserPermission && _readsByBrowser(v) && _profile() && _site(info)); }
  function _readCan(v, info, offers) {
    const unavailable = readUnavailable(info, _target?.kind);
    if (unavailable) return { site: false, reason: unavailable };
    const site = _siteApplies(v, info);
    if (offers.read || site) return { site, reason: '' };
    return { site, reason: _readsByBrowser(v) && !_profile() ? NEED_SIGNIN : READ_UNAVAILABLE };
  }
  // What the row shows when nobody has touched it: on only when every part that applies is already granted.
  function _readParts(can, offers) {
    return offers.reads.map((o) => _initial(o).length > 0).concat(can.site ? [_siteGranted()] : []);
  }
  // Some read route has only some of the capabilities it offers; or one read route is allowed and another is not.
  function _readPartial(offers) {
    const n = offers.reads.map((o) => _initial(o).length);
    return offers.reads.some((o, i) => n[i] > 0 && n[i] < o.caps.length) || (n.some((x) => x > 0) && n.some((x) => x === 0));
  }
  function _readInitial(can, offers) {
    if (can.reason) return false;
    return _readParts(can, offers).every(Boolean);
  }
  // Some parts allowed and some not (the Desk read is saved but the sign-in's domain is not): shown unticked and half-ticked,
  // so a person is not told Read is on while the card still says Reading is off.
  function _readMixed(can, offers) {
    const parts = _readParts(can, offers);
    return !can.reason && parts.some(Boolean) && !parts.every(Boolean);
  }
  function _readOn(can, offers) { return M.wish.read === null ? _readInitial(can, offers) : M.wish.read; }

  function _pending(sel, info) {
    const offers = _offers(_variant(sel, info), _target.kind, info);
    const base = _base(), scopes = _final(base, offers);
    const state = M.policy ? M.policy.state : 'new';
    return { offers, scopes, deskChange: _fp(scopes) !== _fp(base) || ((state === 'legacy' || state === 'invalid') && M.touched),
      siteChange: M.wish.site !== null && M.wish.site !== _siteGranted() };
  }

  // ── loading: one read of what is saved, names and flags only ────────────
  function _needsFetch(sel, info) {
    return !!(_target && ((_target.account && _target.account.id) || (_readsByBrowser(_variant(sel, info)) && _profile()) || _loginName()));
  }
  function _loadKey(info) { return [_service(info), (_target.account && _target.account.id) || '', _savedProfile(), _site(info), _loginName()].join('|'); }   // not the drafted profile: typing one repaints nothing, Continue would stall on "Loading"

  function _load(api, force) {
    if (!_target || !['signin', 'api', 'account'].includes(api.sel.type)) return;
    const key = _loadKey(api.info);
    if (M.loading || (!force && M.loaded === key)) return;
    if (!_needsFetch(api.sel, api.info)) { M.loaded = key; return; }
    M.loading = true; M.failed = false;
    const n = ++_seq, ctx = api.ctx, id = (_target.account && _target.account.id) || '';
    const B = window.DeskV1ConnectBrowserPermission, v = _variant(api.sel, api.info), login = _loginName();
    Promise.all([
      id ? ctx.api('GET', `/api/desk/connect/permissions/${encodeURIComponent(id)}`).then((r) => r.policy, () => 'failed') : null,
      id ? ctx.api('POST', '/api/desk/connect/purposes', { service: _service(api.info) }).then((r) => (r.accounts || []).find((a) => a.id === id) || null, () => null) : null,
      B && _readsByBrowser(v) && _profile() && _site(api.info) ? B.load(_profile(), _site(api.info), { channels: ctx.channels ? ctx.channels() : [], selfId: id }).catch(() => 'failed') : null,
      login ? ctx.api('GET', '/api/secrets').then((r) => (r.secrets || []).find((s) => s.name === login) || null, () => null) : undefined,
    ]).then(([policy, bound, bperm, vault]) => {
      if (n !== _seq) return;                                   // discarded or reloaded meanwhile
      M.loading = false; M.loaded = key;
      M.failed = policy === 'failed' || bperm === 'failed';
      M.policy = policy === 'failed' ? null : policy; M.bound = bound; M.bperm = bperm === 'failed' ? null : bperm; M.vault = vault;
      api.repaint();
    });
  }

  // ── drawing ─────────────────────────────────────────────────────────────
  function _capText(caps) { return caps.map((c) => CAP_LABEL[c] || c).join(', '); }

  function _optionHTML(id, owner, label, detail, checked, blocked) {
    return `<label class="desk-v1-cfw-option desk-v1-cfp-option" data-cfw-option="${id}"${blocked ? ' data-cfp-blocked' : ''}>
        <input type="checkbox" data-cfp-box="${id}" ${checked ? 'checked' : ''}${blocked ? ' disabled' : ''}>
        <span class="desk-v1-cfp-text"><span class="desk-v1-cfw-option-label">${esc(label)}</span><span class="desk-v1-cfp-owner" data-cfp-owner>${esc(owner)}</span>
          <span class="desk-v1-cfw-fact-text">${detail}</span></span>
      </label>`;
  }

  function _readRowHTML(can, offers) {
    const label = "Read this account's activity";
    if (can.reason) return _optionHTML('read', 'Desk permission', label, esc(can.reason), false, true);
    const now = offers.reads.flatMap(_initial), partial = M.wish.read === null && _readPartial(offers);
    const unfinished = M.wish.read === null && _readMixed(can, offers) && can.site && !_siteGranted();
    const detail = `${esc(READ_DETAIL)}${partial ? ` Allowed now: ${esc(_capText(Array.from(new Set(now.map((s) => s.capability)))))}.` : ''}${unfinished ? ` ${esc(READ_UNFINISHED)}` : ''}${offers.reads.some((o) => o.paid) ? ` <span data-cfp-paid>${esc(PAID_API_READ)}</span>` : ''}`;
    return _optionHTML('read', 'Desk permission', label, detail, _readOn(can, offers));
  }
  function _postRowHTML(o) {
    if (!o) return _optionHTML('post', 'Desk permission', 'Publish', esc(POST_UNAVAILABLE), false, true);
    return _optionHTML('post', 'Desk permission', 'Publish', 'Covers: Post only. Not Reply, media or Article.', _checked(o));
  }

  function _vaultHTML() {
    const name = _loginName();
    if (!name) return '';
    const v = M.vault;
    const say = v && typeof v.allow_unattended === 'boolean'
      ? `Unattended agents ${v.allow_unattended ? 'may' : 'may not'} use "${esc(name)}". Change this in saved credentials.` : `The unattended-use setting for "${esc(name)}" is chosen in saved credentials.`;
    return `<div class="desk-v1-cfp-fact" data-cfp-vault><span class="desk-v1-cfp-owner" data-connect-copy>Saved login (Vault)</span><span class="desk-v1-cfw-fact-text">${say} Filling the password always starts with you.</span></div>`;
  }

  function _stateHTML() {
    const st = M.policy ? M.policy.state : '';
    if (st === 'legacy') return '<div class="desk-v1-cfw-msg desk-v1-cfp-state" data-cfp-state="legacy">No permission is recorded for this account, so it works as it did before. Choosing below records your choices; anything left off stops.</div>';
    if (st === 'invalid') return '<div class="desk-v1-cfw-msg desk-v1-cfp-state" data-cfp-state="invalid" role="alert">The saved permissions could not be read, so everything is off. Saving rewrites them.</div>';
    return '';
  }

  function _notesHTML(sel, info) {
    if (!_target) return '';
    const p = _pending(sel, info);
    const notice = _confirmNotice(_service(info), p.scopes);
    return `${notice ? `<div class="desk-v1-cfw-msg desk-v1-cfp-notice" data-cfp-confirm role="status">${esc(notice)}</div>` : ''}
      ${p.deskChange || p.siteChange ? '<div class="desk-v1-cfw-fact-text" data-cfp-pending>Applied after you save the connection, each with its own passcode check.</div>' : ''}`;
  }

  function _projects() { try { return (window.DeskV1Store && window.DeskV1Store.state().projects) || []; } catch (_) { return []; } }
  function _reachHTML() {
    const R = M.reach, list = _projects();
    const opts = ['<option value="">Choose a project…</option>'].concat(list.map((p) => `<option value="${esc(p.id)}"${R.projectId === p.id ? ' selected' : ''}>${esc(p.name || p.id)}</option>`));
    return `<fieldset class="desk-v1-cfp-reach" data-cfp-reach><legend class="desk-v1-cfw-dtitle">Use this server's tools</legend>
        <div class="desk-v1-cfw-options" role="radiogroup" aria-label="Who can use this server">
          <label class="desk-v1-cfw-option" data-cfw-option="project"><input type="radio" name="cfp-reach" value="project" data-cfp-scope ${R.scope === 'project' ? 'checked' : ''}><span class="desk-v1-cfw-option-label">In one project</span></label>
          <label class="desk-v1-cfw-option" data-cfw-option="global"><input type="radio" name="cfp-reach" value="global" data-cfp-scope ${R.scope === 'global' ? 'checked' : ''}><span class="desk-v1-cfw-option-label">In all projects</span><span class="desk-v1-cfw-tag" data-cfp-wide>Wider reach</span></label>
        </div>
        ${R.scope === 'project' ? `<label class="desk-v1-conn-add-field">Project<select class="desk-v1-rules-textinput" data-cfp-project>${opts.join('')}</select></label>` : '<div class="desk-v1-cfw-fact-text" data-cfp-wide-note>Agents in every project can use it.</div>'}
      </fieldset>`;
  }

  function _bodyHTML(api) {
    M.info = api.info; M.sel = api.sel; _ctx = api.ctx;
    const mode = _mode(api.sel, api.info);
    if (mode === 'mcp' && !(api.info?.picker || []).flatMap(t => t.variants).some(v => v.id === api.sel.variant && v.setup?.via === 'provider')) return _reachHTML();
    if (mode !== 'account') return `<p>${esc(window.DeskV1ConnectCopy.words.noPermission)}</p>`;
    if (_needsFetch(api.sel, api.info) && M.loaded !== _loadKey(api.info)) {
      return M.failed ? '' : '<div class="desk-v1-cfw-fact-text" data-cfp-loading>Loading what is saved…</div>';
    }
    if (M.failed) return failedHTML();
    const v = _variant(api.sel, api.info), offers = _offers(v, _target.kind, api.info);
    const rows = _readRowHTML(_readCan(v, api.info, offers), offers) + _postRowHTML(offers.post);
    return `${sections.map(s => s.html(sectionContext(api))).join('')}${_stateHTML()}<div class="desk-v1-cfw-options" data-cfp-rows aria-label="Permissions">${rows}</div>${_vaultHTML()}<div data-cfp-notes aria-live="polite">${_notesHTML(api.sel, api.info)}</div>`;
  }
  function failedHTML() {
    return '<div class="desk-v1-cfw-msg" data-cfp-failed role="alert">What is saved could not be read. <button type="button" class="desk-v1-cf-link" data-cfp-retry>Try again</button>.</div>';
  }

  // Details: exact coverage, what is not covered, what is kept as it is, and the browser-site facts.
  function _detailItems(api) {
    const items = [];
    const mode = _mode(api.sel, api.info);
    if (mode === 'mcp') {
      items.push({ head: 'No Read or Post switch', text: 'A server\'s tools cannot be made read-only by filtering their names, so approval covers the whole server. Campaign approval and LinkedIn\'s closed posting gate are unchanged.' });
      return items;
    }
    if (mode === 'none' && !_target) return [{ head: 'Permissions', text: window.DeskV1ConnectCopy.words.noPermission }];
    if (mode === 'reference') { items.push({ head: 'Vault policy', text: 'A credential keeps the scope and unattended-use setting it has in the Vault. Saving a reference does not change them.' }); return items; }
    const v = _variant(api.sel, api.info);
    const siteFacts = () => {
      if (_readsByBrowser(v) && window.DeskV1ConnectBrowserPermission && M.bperm && M.bperm.profile && M.bperm.site) {
        window.DeskV1ConnectBrowserPermission.facts(M.bperm, M.wish.site).forEach((t) => items.push({ head: 'Browser sign-in', text: t }));
      }
    };
    if (!v) { siteFacts(); return items; }
    const offers = _offers(v, _target.kind, api.info);
    offers.reads.concat([offers.post]).forEach((o) => {
      if (o) items.push({ head: `${o.op === 'post' ? 'Post' : 'Read'} covers`, text: `${_capText(o.caps)}, ${VIA_WORD[o.via] || o.via} (${o.route_id}). Exactly these and nothing implied.` });
    });
    const offeredCaps = new Set([].concat(...offers.reads.map((o) => o.caps), offers.post ? offers.post.caps : []));
    const rest = Array.from(new Set((v.coverage || []).filter((c) => c.account_kind === _target.kind).map((c) => c.capability))).filter((c) => !offeredCaps.has(c));
    if (rest.length || offers.post) items.push({ head: 'Not covered by Read or Post', text: `${rest.length ? `${_capText(rest)}. ` : ''}Reply, media upload, Article and broad listening need their own later permission. Allowing Read never allows Post.` });
    if (v.runtime && v.runtime.note) items.push({ head: 'Desk support', text: v.runtime.note });
    siteFacts();
    const kept = _base().filter((s) => !offers.reads.some((o) => _mine(o, s)) && !(offers.post && _mine(offers.post, s)));
    if (kept.length) items.push({ head: 'Kept as they are', text: kept.map((s) => `${CAP_LABEL[s.capability] || s.capability} (${s.purpose === 'publish' ? 'post' : 'read'}) via ${s.route_id}`).join('; ') + '. Saving here does not change them.' });
    const ch = _target.account && _target.account.id && _ctx && _ctx.channels ? (_ctx.channels() || []).find((c) => c.id === _target.account.id) : null;
    if (ch && ch.read_via) items.push({ head: 'Reading settings', text: 'The reading method and activity addresses chosen here are saved separately from connection permissions.' });
    if (M.bound && M.bound.bound && M.bound.bound.length) items.push({ head: 'Existing routes', text: M.bound.bound.map((b) => `${b.route_title}: ${_capText(b.capabilities)}`).join('; ') + '. Split routes stay as they are.' });
    return items;
  }
  function _detailsHTML(api) {
    const items = _detailItems(api);
    if (!items.length) return '';
    return `<div class="desk-v1-cfw-dgroup" data-cfp-details>${api.paged('details', items,
      (it) => `<div class="desk-v1-cfw-fact"><div class="desk-v1-cfw-fact-head">${esc(it.head)}</div><div class="desk-v1-cfw-fact-text">${esc(it.text)}</div></div>`)}</div>`;
  }

  // ── the screen ──────────────────────────────────────────────────────────
  function _problem(sel, info) {
    const mode = sel.type === 'mcp' ? 'mcp' : sel.type === 'reference' ? 'reference' : 'account-ish';
    if (mode === 'mcp' && _variant(sel, info)?.setup?.via === 'provider') return '';
    if (mode === 'mcp') return M.reach.scope === 'project' && !_projects().some(p => p.id === M.reach.projectId) ? 'Choose a project.' : '';
    if (mode === 'reference') return '';
    if (!_target && ['x', 'linkedin'].includes(_service(info))) return 'Choose the account first.';
    if (_needsFetch(sel, info) && M.loaded !== _loadKey(info)) return M.failed ? 'What is saved could not be read.' : 'Loading what is saved.';
    if (M.failed) return 'What is saved could not be read.';
    const why = sections.map(s => s.problem(sectionContext({ sel, info, ctx: _ctx }))).find(Boolean);
    if (why) return why;
    return '';
  }

  W.registerScreen({
    id: 'permissions-step', step: 'permissions',
    match: (sel) => sel.type === 'mcp' || (sel.type === 'reference' && !window.DeskV1ConnectReferenceStep) || (['signin', 'api', 'account'].includes(sel.type) && (!!_target || (window.DeskV1ConnectWizard.enabled() && !['x', 'linkedin'].includes(window.DeskV1ConnectWizard.state().sel.service)))),
    title: (api) => { const m = _mode(api.sel, api.info); return m === 'account' ? `Permissions for ${_label(api.info)}` : 'Permissions'; },
    copy: (api) => {
      const m = _mode(api.sel, api.info);
      if (m === 'mcp') return 'Choose who can use this server. Its tools may read or change data.';
      if (m === 'account') return 'Choose what agents may do with this account. Posting still needs campaign approval.';
      return 'Saving information grants no connection permissions. Credential access follows its existing vault policy.';
    },
    body: _bodyHTML,
    details: _detailsHTML,
    primary: (api) => { _ctx = api.ctx; return ({
      label: 'Continue', disabled: !!_problem(api.sel, api.info),
      run: async () => {
        const why = _problem(api.sel, api.info);
        if (why) { api.error(why); return false; }
        return true;                                           // Continue writes nothing: `apply` is called by Review
      },
    }); },
    bind: (root, api) => {
      M.info = api.info; M.sel = api.sel; _ctx = api.ctx;
      _load(api);
      sections.forEach(s => s.bind(root, sectionContext(api)));
      const primary = root.querySelector('[data-cfw-primary]');
      const sync = () => {
        const notes = root.querySelector('[data-cfp-notes]');
        if (notes && _target) notes.innerHTML = _notesHTML(api.sel, api.info);
        if (primary) primary.disabled = !!_problem(api.sel, api.info);
      };
      const retry = root.querySelector('[data-cfp-retry]');
      if (retry) retry.addEventListener('click', () => { M.failed = false; M.loaded = ''; _load(api, true); api.repaint(); });
      root.querySelectorAll('[data-cfp-box]').forEach((box) => {
        const id = box.dataset.cfpBox;
        const v = _target ? _variant(api.sel, api.info) : null, offers = _target ? _offers(v, _target.kind, api.info) : { read: null, reads: [], post: null };
        const o = offers[id];
        if (id === 'read' && _target && M.wish.read === null) {
          const can = _readCan(v, api.info, offers);
          box.indeterminate = _readMixed(can, offers) || _readPartial(offers);
        } else if (o && M.wish[id] === null) { const n = _initial(o).length; box.indeterminate = n > 0 && n < o.caps.length; }
        box.addEventListener('change', () => {
          box.indeterminate = false;
          if (id === 'read') {
            if (_readCan(v, api.info, offers).reason) { box.checked = false; return; }
            // One tick, two parts: the Desk read the route offers and the service's own domain on the sign-in. Back at what
            // the row showed = no wish, so ticking and unticking sends nothing (unless the row started half-ticked).
            const can = _readCan(v, api.info, offers);
            const partial = _readMixed(can, offers) || _readPartial(offers);
            M.wish.read = !partial && box.checked === _readInitial(can, offers) ? null : box.checked;
            M.wish.site = can.site && M.wish.read !== null ? M.wish.read : null;
            if (offers.read) M.touched = true;               // a domain-only change must not record Desk scopes on a legacy account
          } else { M.wish.post = box.checked; M.touched = true; }
          sync();
        });
      });
      root.querySelectorAll('[data-cfp-scope]').forEach((r) => r.addEventListener('change', () => { M.reach.scope = r.value; api.repaint(); }));
      const proj = root.querySelector('[data-cfp-project]');
      if (proj) proj.addEventListener('change', () => { M.reach.projectId = proj.value; sync(); });
      sync();
    },
    discard: () => { _seq++; M = _fresh(); _target = null; sections.forEach(s => s.discard()); },
  });

  function setTarget(t) {
    if (t == null) { _target = null; return; }
    if (!t || typeof t !== 'object' || !t.kind || !t.account || (!t.account.id && !(t.account.new && t.account.new.identity))) throw new Error('setTarget: kind and an account (id, or new with an identity) are required');
    _target = { kind: String(t.kind), account: JSON.parse(JSON.stringify(t.account)), identity: t.identity ? String(t.identity) : '' };
  }

  // ── what Review needs ───────────────────────────────────────────────────
  // The MCP branch's reach, for tickets 10 and 11: { scope: 'project'|'global', project_id? }.
  function reach() { return M.reach.scope === 'global' ? { scope: 'global' } : { scope: 'project', project_id: M.reach.projectId }; }

  // The non-secret facts of what is chosen, one line each, for a Review row. `pending` = saving would send something.
  function summary() {
    if (!M.sel || !M.info) return null;
    const sel = M.sel, info = M.info;
    if (sel.type === 'mcp' && _variant(sel, info)?.setup?.via === 'provider') return { kind: 'reference', pending: false, lines: [window.DeskV1ConnectCopy.words.noPermission] };
    if (sel.type === 'mcp') return { kind: 'mcp', pending: false, lines: [M.reach.scope === 'global' ? 'Use this server\'s tools in all projects.' : 'Use this server\'s tools in one project.'] };
    if (sel.type === 'reference' || !_target) return { kind: 'reference', pending: false, lines: ['No connection permission is granted.'] };
    const p = _pending(sel, info), lines = [];
    p.offers.reads.concat([p.offers.post]).forEach((o) => { const op = o && o.op; if (o && p.scopes.some((s) => _mine(o, s))) lines.push(`Desk: ${op === 'post' ? 'Post' : `Read ${_capText(p.scopes.filter((s) => _mine(o, s)).map((s) => s.capability))}`} ${VIA_WORD[o.via] || ''}`.trim()); });
    if (M.bperm && M.bperm.site && _siteOn()) lines.push(`Browser sign-in: read this account's activity on ${M.bperm.site} through "${_profile() || M.bperm.profile}"`);
    if (!lines.length) lines.push(sel.type === 'account' ? window.DeskV1ConnectCopy.words.permissionsUnchanged : 'No permissions are allowed.');
    const extra = sections.map(s => s.summary());
    extra.forEach(s => lines.push(...s.lines));
    const notice = _confirmNotice(_service(info), p.scopes);
    return { kind: 'account', pending: p.deskChange || p.siteChange || extra.some(s => s.pending), permissionPending: p.deskChange || p.siteChange, lines, notice };
  }

  const _uuid = () => (window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() : `pp-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;

  // The Desk write: one passcode-gated request, built from the account's CURRENT saved scopes so another change made
  // meanwhile is not overwritten. -> { status: 'saved'|'unchanged'|'cancelled'|'failed', error? }
  async function _applyDesk(accountId) {
    const info = M.info, sel = M.sel;
    const offers = _offers(_variant(sel, info), _target.kind, info);
    if (!M.touched && ['read', 'post'].every((op) => !offers[op] || M.wish[op] === null)) return { status: 'unchanged' };
    let view;
    try { view = await _ctx.api('GET', `/api/desk/connect/permissions/${encodeURIComponent(accountId)}`); }
    catch (e) { return { status: 'failed', error: `Could not read the saved permissions: ${(e && e.message) || 'unreachable'}.` }; }
    const pol = (view && view.policy) || {}, base = pol.state === 'explicit' ? (pol.scopes || []) : [];
    const scopes = _final(base, offers);
    if (_fp(scopes) === _fp(base) && !((pol.state === 'legacy' || pol.state === 'invalid') && M.touched)) return { status: 'unchanged' };
    scopes.sort((a, b) => (_key(a) < _key(b) ? -1 : 1));
    const draft = { account_id: accountId, service: _service(info), account_kind: _target.kind,
      read: scopes.some((s) => s.purpose === 'read_own'), post: scopes.some((s) => s.purpose === 'publish'), scopes };
    const fp = JSON.stringify(draft);
    if (!M.requestId || M.requestFp !== fp) { M.requestId = _uuid(); M.requestFp = fp; }      // same draft, same id: a retry is a replay
    const title = draft.read && !draft.post ? 'Allow reading' : 'Apply permissions';
    let res;
    try {
      res = await window.humanProofFetch('/api/desk/connect/permissions/commit', { method: 'POST', body: JSON.stringify({ request_id: M.requestId, draft }) },
        { title, description: `Re-enter your dashboard passcode to change what agents may do through ${_label(info)}: ${scopes.length ? scopes.map((s) => `${CAP_LABEL[s.capability] || s.capability} (${s.purpose === 'publish' ? 'post' : 'read'})`).join(', ') : 'nothing allowed'}.` });
    } catch (e) { return { status: 'failed', error: (e && e.message) || 'could not reach the server' }; }
    if (res === null) return { status: 'cancelled' };
    if (!res.ok) { const b = res.body || {}; return { status: 'failed', error: b.error || b.message || `The save failed (HTTP ${res.status}).` }; }
    M.policy = (res.body && res.body.policy) || null; M.wish.read = null; M.wish.post = null; M.touched = false; M.requestId = null;
    return { status: 'saved' };
  }

  async function _applySite() {
    const B = window.DeskV1ConnectBrowserPermission, info = M.info;
    if (readUnavailable(info, _target?.kind)) return { status: 'unchanged' };
    if (!B || M.wish.site === null) return { status: 'unchanged' };
    // Aimed at the sign-in the account will have AFTER this save (a drafted profile change is applied after this write),
    // never at the one the screen loaded with: the grant on the old profile would be a grant the account no longer uses.
    const profile = _profile(), site = _site(info), want = M.wish.site;
    const opts = { channels: _ctx && _ctx.channels ? _ctx.channels() : [], selfId: (_target.account && _target.account.id) || '' };
    const r = await B.apply(profile, site, want, opts);
    if (r.status === 'saved' || r.status === 'unchanged') {
      // what the screen now shows is what that profile says, and success is only what that profile actually allows
      const now = await B.load(profile, site, opts).catch(() => null);
      if (!now) return { status: 'failed', error: `Could not check what the sign-in "${profile}" now allows.` };
      M.bperm = now;
      if (want && !now.granted) return { status: 'failed', error: `The sign-in "${profile}" still does not allow reading ${site}.` };
      if (!want && now.granted && !r.stays) return { status: 'failed', error: `The sign-in "${profile}" still allows reading ${site}.` };
      M.wish.site = null;
    }
    return r;
  }

  // Apply what was chosen to the saved account: the Desk choice, then the browser-site choice, each with its own passcode
  // prompt. Never throws. A cancelled first prompt stops the second (`site.status` 'not_attempted'); call again to retry.
  // -> { ok, pending, desk, site }   ok: nothing failed or was cancelled   pending: the site grant waits for the sign-in
  async function apply(accountId) {
    const none = { status: 'unchanged' };
    if (!_target || !M.sel || !M.info || M.sel.type === 'mcp' || M.sel.type === 'reference') return { ok: true, pending: false, desk: none, site: none };
    const id = String(accountId || (_target.account && _target.account.id) || '');
    if (!id) return { ok: false, pending: false, desk: { status: 'failed', error: 'There is no saved account to apply permissions to yet.' }, site: none };
    if (M.applying) return { ok: false, pending: false, desk: { status: 'failed', error: 'A save is already running.' }, site: none };
    M.applying = true;
    try {
      const desk = await _applyDesk(id).catch((e) => ({ status: 'failed', error: (e && e.message) || 'That did not work.' }));
      const site = desk.status === 'cancelled' ? { status: 'not_attempted' } : await _applySite().catch((e) => ({ status: 'failed', error: (e && e.message) || 'That did not work.' }));
      const extra = [];
      for (const s of sections) extra.push(desk.status === 'cancelled' ? { status: 'not_attempted' } : await s.apply(id));
      return { ok: ['saved', 'unchanged'].indexOf(desk.status) >= 0 && ['saved', 'unchanged', 'deferred'].indexOf(site.status) >= 0 && extra.every(r => ['saved', 'unchanged'].includes(r.status)), pending: site.status === 'deferred', desk, site, extra };
    } finally { M.applying = false; }
  }

  // The words for the Result when the connection is saved and a permission was not (never "connected" when a part failed).
  function outcome(connectionSaved, result) {
    const r = result || { desk: { status: 'unchanged' }, site: { status: 'unchanged' } };
    const lines = [];
    if (r.desk.status === 'failed') lines.push(`Connection saved; permissions unchanged: ${r.desk.error}`);
    else if (r.desk.status === 'cancelled') lines.push('Connection saved; permissions unchanged: the passcode was not entered.');
    else if (r.desk.status === 'saved') lines.push('Desk permissions are saved.');
    const B = window.DeskV1ConnectBrowserPermission;
    if (B && r.site.status !== 'not_attempted') { const o = B.outcome(connectionSaved, r.site); if (o.text) lines.push(o.text); }
    if (r.site.status === 'deferred') lines.push('Finish sign-in to apply Read permission.');
    (r.extra || []).forEach((result, i) => { const line = sections[i].outcome(result); if (line) lines.push(line); });
    return { kind: lines.length ? (r.ok && !r.pending ? 'ok' : 'partial') : 'ok', lines };
  }

  window.DeskV1ConnectPermissionsStep = { setTarget, summary, apply, outcome, reach, registerSection, restoreReach:d => {M.reach={scope:d.scope,projectId:d.project_id || ''};} };
})();

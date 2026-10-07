// Desk v1 (MC-977, Ron 2026-10-01) — Connections: THE one place anything
// external is connected. A Desk route at home level (like Studio and
// Engagement), reached from the Desk header; Where's "not connected" tiles and
// Studio's online sources route here instead of running their own connect flow.
// Window-bridged module, no `import` (ground rule 1).
//
//   Social accounts  a grid of one tile per account (status pill; the tile grid and
//                    its single selection are static/js/desk-v1-connections-tiles.js).
//                    Selecting a tile opens that account's detail below the grid:
//                    status, Connect / Reconnect, and how the Desk READS it: the
//                    browser pane (free) + the signed-in browser profile for any site,
//                    or, for X only, the platform API (paid). YouTube / Instagram /
//                    TikTok accounts are read-only: read, never published to.
//                    Voice is NOT here: a campaign sets it on its Where board.
//   Content sources  the cloud drives Studio's online sources read from.
//   Generation engines  live: each engine, connected or not by vault name, and the per-job USD
//                    limit the user sets for it (static/js/desk-v1-engines.js, MC-1019).
//
// Demo mode (desk_v1_live OFF): Connect / Reconnect flips the fixture account's
// state (with Undo) and authenticates nothing. Read via is real there: it
// PATCHes /api/desk/presence/<pid>/accounts/<channel>/read, which stores per
// project, so the account is filed under the project that uses it (the first
// campaign placing it), else the first project.
//
// Live (R1-W S5): accounts are the workspace's (M2-M5, mc/desk_accounts.py). A
// row shows what the SERVER derived, `account.publish`: "Publishing: connected",
// or "not connected" with its reason. X gets a step-by-step Connect (create the
// X app, paste its Client ID, sign in: static/js/desk-v1-guides.js) that ends in
// a verified Connected; LinkedIn says in one line what to apply for. The fixture
// Connect button is gone: it would claim a connection nothing made.
// Read via goes to M4 (`PATCH /api/desk/accounts/<id>`), still filed under the
// project that uses the account so engagement has something to poll. Add and
// Remove are M3/M5. Discord / Reddit and the cloud drives stay
// placeholder tiles.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function _fx() { return window.DeskV1Store.state(); }
  function _channels() { return _fx().channels || []; }
  function _projects() { return _fx().projects || []; }
  function _campaigns() { return _fx().campaigns || []; }

  const _xOpen = new Set();   // account ids whose X steps are open; survives a repaint
  let _connOv = null;          // GET /api/desk/connect/status, dropped when a sign-in changes

  // Only X has a paid API read route, so only X offers the choice. Every other site (LinkedIn
  // included: it has no API read) is read through the browser pane only. A blog is published by
  // hand: nothing to read.
  const API_READ_PLATFORMS = ['x'];
  const NO_READ_PLATFORMS = ['blog'];
  function _canRead(ch) { return !!ch.platform && NO_READ_PLATFORMS.indexOf(ch.platform) < 0; }

  function _homeProjectId(ch) {
    const camp = _campaigns().find((c) => c.projectId && (c.plan && c.plan.accounts || []).includes(ch.id));
    if (camp) return camp.projectId;
    const first = _projects()[0];
    return first ? first.id : null;
  }
  function _readUrl(ch) {
    return `/api/desk/presence/${encodeURIComponent(_homeProjectId(ch))}/accounts/${encodeURIComponent(ch.id)}/read`;
  }
  function _api(method, url, body) { return window.DeskV1Store.api(method, url, body); }
  // A workspace account carries the server's derived `publish`; a fixture does not.
  function _isLiveRow(ch) { return !!ch.publish; }

  // Connected / Not connected / Needs re-auth. A `held` account is one whose
  // login lapsed (the fixture's LinkedIn page); a preview account never
  // authenticates at all. A live account is exactly what `publish.ready` says,
  // with no Connect / Reconnect: nothing on this screen can connect it.
  function _status(ch) {
    if (_isLiveRow(ch)) {
      // MC-1062/13b: a live account's word comes from its routes (desk-v1-connection-status.js), not from publishing alone.
      const CS = window.DeskV1ConnectionStatus;
      if (CS) return CS.accountStatus(ch, CS.coverageRow(_homeProjectId(ch), ch.platform));
      if (ch.preview) return { key: 'preview', word: 'Preview · not connected', action: null };
      // A read-only site (YouTube, Instagram, TikTok) has no publishing to connect: it is read.
      if (ch.capability === 'none') return { key: 'ok', word: 'Read only', action: null };
      return ch.publish.ready ? { key: 'ok', word: 'Connected', action: null }
                              : { key: 'off', word: 'Not connected', action: null };
    }
    if (ch.connected === false) return { key: 'off', word: 'Not connected', action: 'Connect' };
    if (ch.preview) return { key: 'preview', word: 'Preview · not connected', action: null };
    if (ch.health === 'held') return { key: 'reauth', word: 'Needs re-auth', action: 'Reconnect' };
    return { key: 'ok', word: 'Connected', action: 'Reconnect' };
  }

  function _readViaHTML(ch) {
    if (!_canRead(ch)) return '';
    const hasApi = API_READ_PLATFORMS.indexOf(ch.platform) >= 0;
    // An account stored as read through an API its site does not have (a LinkedIn one saved
    // before that option was removed) is not quietly moved to the pane: it asks for a choice.
    const needsChoice = !hasApi && ch.read_via === 'api';
    const via = needsChoice ? null : hasApi && ch.read_via === 'api' ? 'api' : 'pane';
    const apiLabel = 'X API (paid, ~$0.005 per read)';
    const btn = (v, label) => `<button type="button" data-readvia="${v}" aria-pressed="${via === v}">${esc(label)}</button>`;
    const choice = hasApi
      ? `<div class="desk-v1-conn-readvia-seg" role="group" aria-label="How the Desk reads this account">
            ${btn('pane', 'Browser pane (no charge)')}${btn('api', apiLabel)}
          </div>`
      : needsChoice
        ? `<span data-readvia-needs-choice>This account was set to read through an API, which ${esc(ch.platform === 'linkedin' ? 'LinkedIn' : 'this site')} does not offer.
            ${btn('pane', 'Read through the browser pane (no charge)')}</span>`
        : '<span data-readvia-fixed>Browser pane (no charge)</span>';
    const profile = via === 'pane'
      ? `<label class="desk-v1-conn-readvia-profile">Signed-in browser profile
           <input type="text" class="desk-v1-rules-textinput" data-readvia-profile maxlength="64"
             placeholder="name of the saved profile" value="${esc(ch.browser_profile || '')}"></label>`
      : '';
    return `
        <div class="desk-v1-conn-readvia" data-readvia-row="${esc(ch.id)}" data-platform="${esc(ch.platform)}">
          <span class="desk-v1-how-field-label">Read via</span>
          ${choice}
          ${profile}
          ${via === 'pane' && window.DeskV1ConnectAgentRead ? window.DeskV1ConnectAgentRead.html(ch) : ''}
          ${via === 'pane' && window.DeskV1ReadPages ? window.DeskV1ReadPages.html(ch) : ''}
          <div class="desk-v1-rules-hint" data-readvia-status></div>
        </div>`;
  }

  // The live "Publishing" line: what the server derived and why. A token that
  // exists but may not be used unattended means scheduled posts will hold, which
  // is said here rather than found out at the first one.
  function _publishHTML(ch) {
    const pub = ch.publish;
    if (!pub || ch.preview) return '';
    const isX = ch.platform === 'x' && ch.capability !== 'manual';
    const open = isX && _xOpen.has(ch.id);
    // A reason that names the vault is for the server log; the page says it in plain words.
    const reason = pub.reason && !/vault/i.test(pub.reason) ? pub.reason : '';
    if (ch.capability === 'none') {
      return `
        <div class="desk-v1-conn-publish" data-conn-publish data-ready="false" data-readonly>
          <span class="desk-v1-how-field-label">Publishing</span>
          <span data-conn-publish-text>not available: the Desk reads this account and does not publish there</span>
        </div>`;
    }
    const locked = !!pub.vault_locked;      // the sign-in is saved; the vault needs unlocking, not a new sign-in
    const fix = isX && !window.DeskV1ConnectWizard?.enabled()
      ? `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-conn-x-guide="${esc(ch.id)}" aria-expanded="${open}">${open ? 'Hide steps' : (pub.ready ? 'Manage' : (locked ? 'Unlock the vault' : 'Connect X'))}</button>`
      : '';
    const note = pub.ready && pub.unattended_ok === false
      ? '<div class="desk-v1-rules-hint" data-conn-unattended>Scheduled posts will be held: the saved sign-in is not allowed for unattended use. “Approve now” still posts.</div>' : '';
    const li = !window.DeskV1ConnectWizard?.enabled() && ch.platform === 'linkedin' && !pub.ready && window.DeskV1Guides ? window.DeskV1Guides.linkedinHTML() : '';
    return `
        <div class="desk-v1-conn-publish" data-conn-publish data-ready="${pub.ready ? 'true' : 'false'}">
          <span class="desk-v1-how-field-label">Publishing</span>
          <span data-conn-publish-text>${pub.ready ? 'connected' : locked ? 'not connected (your vault is locked; your sign-in is still saved)' : `not connected${reason ? ` (${esc(reason)})` : ''}`}</span>
          ${fix}
        </div>${note}${li}${open ? `<div data-conn-x-wizard="${esc(ch.id)}">${_connOv ? window.DeskV1Guides.xWizardHTML(_connOv) : '<div class="desk-v1-stub-empty">Loading…</div>'}</div>` : ''}`;
  }

  function _accountHTML(ch) {
    const st = _status(ch);
    const live = _isLiveRow(ch);
    const readable = live ? _canRead(ch) && !ch.preview : (st.key === 'ok' || st.key === 'reauth');
    return `
      <div class="desk-v1-conn-row" data-conn-account="${esc(ch.id)}" data-platform="${esc(ch.platform)}" data-conn-state="${st.key}">
        <div class="desk-v1-conn-head">
          ${DeskV1Kit.channelBadge(ch)}
          <span class="desk-v1-conn-status" data-conn-status data-state="${st.key}">${esc(st.word)}</span>
          ${st.action ? `<button type="button" class="desk-v1-conn-btn" data-conn-action="${esc(ch.id)}">${st.action}</button>` : ''}
          ${live ? `<button type="button" class="desk-v1-conn-btn${st.action ? ' desk-v1-conn-btn-inline' : ''}" data-conn-remove="${esc(ch.id)}" aria-label="${esc(`Remove ${ch.label}`)}">Remove</button>` : ''}
        </div>
        ${live && window.DeskV1ConnectionStatus ? window.DeskV1ConnectionStatus.accountHTML(ch, window.DeskV1ConnectionStatus.coverageRow(_homeProjectId(ch), ch.platform)) : ''}
        ${live ? _publishHTML(ch) : ''}
        ${readable && !(live && window.DeskV1ConnectionStatus && window.DeskV1ConnectionStatus.replacesReadVia()) ? _readViaHTML(ch) : ''}
      </div>`;
  }

  // A connected cloud drive's detail. (One that is not connected is not on this
  // screen at all, and Clayrune cannot connect one today: see desk-v1-add-service.js.)
  function _sourceHTML(a) {
    return `
      <div class="desk-v1-conn-row" data-conn-source="${esc(a.id)}">
        <div class="desk-v1-conn-head">
          <span class="desk-v1-channel-badge">${esc(a.label)}</span>
          <span class="desk-v1-conn-status" data-state="ok">Connected${a.account ? ` · ${esc(a.account)}` : ''}</span>
        </div>
      </div>`;
  }

  // Unique cloud sources across the video and image lists, first seen wins.
  function _contentSources() {
    const online = (_fx().studio || {}).online || {};
    const seen = {};
    const out = [];
    (online.video || []).concat(online.image || []).forEach((a) => { if (!seen[a.id]) { seen[a.id] = 1; out.push(a); } });
    return out;
  }

  // The coverage line the server computes (never silenced: "Not connected (…)"
  // when the chosen route can't read). Empty when the route is reading fine.
  function _showCoverage(row, ch, patch) {
    const out = row.querySelector('[data-readvia-status]');
    const pid = _homeProjectId(ch);
    if (!out || !pid) return;
    fetch(`/api/desk/engagement/coverage/${encodeURIComponent(pid)}`).then((r) => r.json()).then((d) => {
      const c = ((d || {}).coverage || []).find((x) => x.platform === ch.platform);
      out.textContent = c ? (c.message || '') : '';
      out.dataset.state = c ? c.state : '';
      // Live only: the page addresses are saved through the account route, which the demo has not got.
      if (_isLiveRow(ch) && window.DeskV1ReadPages) window.DeskV1ReadPages.show(row, ch, c ? c.pages : null, patch);
    }).catch(() => { out.textContent = ''; });
  }

  function _bindReadVia(row, ch, repaint) {
    const readRow = row.querySelector('[data-readvia-row]');
    if (!readRow) return;
    const out = readRow.querySelector('[data-readvia-status]');
    // Live: M4, filed under the project that uses the account (`project_id`) so
    // engagement has a presence to poll; demo: the presence route, as before.
    const live = _isLiveRow(ch);
    const patch = (body) => fetch(live ? `/api/desk/accounts/${encodeURIComponent(ch.id)}` : _readUrl(ch), {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(live ? Object.assign({ project_id: _homeProjectId(ch) || undefined }, body)
                                : Object.assign({ platform: ch.platform }, body)),
    }).then((r) => r.json().then((j) => ({ ok: r.ok, j }))).then(({ ok, j }) => {
      if (!ok) { out.textContent = (j && j.error) || 'could not save'; return false; }
      return j;
    }).catch(() => { out.textContent = 'could not save'; return false; });

    readRow.querySelectorAll('[data-readvia]').forEach((b) => b.addEventListener('click', () => {
      const next = b.dataset.readvia;
      if ((ch.read_via === 'api' ? 'api' : 'pane') === next) return;
      patch({ read_via: next }).then((acc) => {
        if (!acc) return;
        ch.read_via = acc.read_via;
        DeskV1Kit.toast(`${ch.label} is now read via ${next === 'api' ? 'the X API (paid per read)' : 'the browser pane (no charge)'}.`);
        repaint();
      });
    }));
    const prof = readRow.querySelector('[data-readvia-profile]');
    if (prof) prof.addEventListener('change', () => {
      patch({ browser_profile: prof.value.trim() }).then((acc) => {
        if (!acc) return;
        ch.browser_profile = acc.browser_profile || '';
        _showCoverage(row, ch, patch);
      });
    });
    _showCoverage(row, ch, patch);
    if (window.DeskV1ConnectAgentRead) window.DeskV1ConnectAgentRead.bind(row, ch);
  }

  // Fixture-only Connect / Reconnect: flips the account's state, authenticates
  // nothing, and Undo puts it back.
  function _bindAction(btn, ch, repaint) {
    btn.onclick = () => {
      const prev = { connected: ch.connected, health: ch.health, holdReason: ch.holdReason };
      const wasOff = ch.connected === false;
      DeskV1Kit.commandBus.run({
        label: `${wasOff ? 'Connected' : 'Reconnected'} ${ch.label} (preview: nothing was authenticated)`,
        do: () => { ch.connected = true; ch.health = 'ok'; delete ch.holdReason; repaint(); },
        undo: () => { ch.connected = prev.connected; ch.health = prev.health; if (prev.holdReason) ch.holdReason = prev.holdReason; repaint(); },
      });
    };
  }

  // Live: the vault is the human's. Opening it is all this screen does about a
  // missing token; "Check again" re-reads the derived state once they are back.
  function _recheck(el, repaint) {
    return _api('GET', '/api/desk/accounts').then((rows) => {
      const have = _channels();
      (rows || []).forEach((row) => {
        const mine = have.find((c) => c.id === row.id);
        if (mine) Object.assign(mine, row);
        else have.push(row);
      });
      repaint();
    }).catch((e) => DeskV1Kit.toast(`Could not re-check the accounts: ${e && e.message ? e.message : e}`));
  }

  function _accountSnapshot(ch) {
    const snap = { id: ch.id, platform: ch.platform, identity: ch.identity, label: ch.label, capability: ch.capability, voice: ch.voice || '' };
    const read = {};
    if (ch.read_via) read.read_via = ch.read_via;
    if (ch.browser_profile) read.browser_profile = ch.browser_profile;
    if (ch.read_pages && ch.read_pages.length) read.read_pages = ch.read_pages;
    return { snap, read };
  }

  // M5. The row goes at once and comes back with the server's reason if it is
  // refused (a campaign still places it, a version is still on it). Undo
  // re-creates it, read settings included; its presence copies are gone, so the
  // read route is filed again the next time the user sets it.
  function _removeAccount(ch, repaint) {
    const list = _channels();
    const idx = list.indexOf(ch);
    const { snap, read } = _accountSnapshot(ch);
    window.DeskV1Store.write({
      label: `Removed ${ch.label}`,
      destructive: true,
      apply: () => { const i = list.indexOf(ch); if (i >= 0) list.splice(i, 1); repaint(); },
      unapply: () => { if (list.indexOf(ch) < 0) list.splice(Math.min(idx, list.length), 0, ch); },
      repaint,
      request: () => _api('DELETE', `/api/desk/accounts/${encodeURIComponent(ch.id)}`),
      undoRequest: async () => {
        await _api('POST', '/api/desk/accounts', snap);
        if (Object.keys(read).length) await _api('PATCH', `/api/desk/accounts/${encodeURIComponent(ch.id)}`, read);
      },
    });
  }

  let _enginesCache = null;   // live: GET /api/desk/engines, refetched when a limit or a key changes
  let _enginesError = null;

  // Refetch the engines after one changed (a limit, a key, a sign-in). The list on
  // screen stays until the answer arrives, so the grid and the open panel do not blink.
  // An engine picked on the Add service panel that is now connected moves to its own
  // tile: it is something the user has, not something being added any more.
  function _reloadEngines(repaint) {
    return window.DeskV1Engines.list(null, { force: true }).then((e) => {
      _enginesCache = e; _enginesError = null;
      const picked = window.DeskV1AddService.pickedEngine();
      const hit = picked && e.find((x) => x.id === picked);
      if (hit && window.DeskV1Engines.tileState(hit) && window.DeskV1ConnTiles.selected() === 'add') {
        window.DeskV1AddService.reset();
        window.DeskV1ConnTiles.select(`engine:${hit.id}`);
      }
      repaint();
    }).catch((err) => { _enginesError = err && err.message ? err.message : String(err); repaint(); });
  }

  // What is on the grid: the user's connected things, and anything that needs them
  // (re-auth). An account that is only a preview, a fixture that was never connected,
  // an engine that is not connected: none of those is shown. Live, an account the
  // user added but has not connected yet IS shown, amber, because it needs them.
  function _showAccount(ch, live, sel) {
    if (ch.id === sel) return true;          // routed here from Where's "Connect ›"
    const st = _status(ch).key;
    if (st === 'preview') return false;
    if (st === 'off') return live || !!ch.userAdded;
    return true;
  }

  function _items(live, sel) {
    const Tiles = window.DeskV1ConnTiles;
    const items = [];
    _channels().filter((ch) => _showAccount(ch, live, sel)).forEach((ch) => items.push({
      key: ch.id, kind: 'account', platform: ch.platform, brand: ch.platform, mark: Tiles.mark(ch.platform),
      name: Tiles.plainName(ch.label) || ch.identity || ch.id, kindLabel: 'Social account', status: _status(ch),
    }));
    _contentSources().filter((a) => a.connected).forEach((a) => items.push({
      key: `source:${a.id}`, kind: 'source', brand: a.id, mark: a.glyph || String(a.label || '?').charAt(0).toUpperCase(),
      name: a.label, kindLabel: 'Content source', status: { key: 'ok', word: 'Connected' },
    }));
    if (live) (_enginesCache || []).forEach((e) => {
      const st = window.DeskV1Engines.tileState(e);
      if (st) items.push({ key: `engine:${e.id}`, kind: 'engine', brand: e.id, mark: String(e.label || '?').charAt(0).toUpperCase(),
        name: e.label, kindLabel: 'Generation engine', status: st });
    });
    if (live && window.DeskV1ConnectionStatus) window.DeskV1ConnectionStatus.mcpItems().forEach((m) => items.push(m));
    (window.DeskV1Services.rows() || []).forEach((s) => items.push({
      key: `service:${s.id}`, kind: 'service', mark: String(s.name || '?').charAt(0).toUpperCase(),
      name: s.name, kindLabel: 'Saved service', status: window.DeskV1Services.STATUS,
    }));
    return items;
  }

  // The panel for the selected key, or null when the key matches nothing (an item
  // that is gone keeps its key: a refused Remove, or its Undo, brings it back).
  function _detail(sel) {
    const Tiles = window.DeskV1ConnTiles;
    if (!sel) return null;
    if (sel === 'add') {
      return Tiles.detailHTML('add', 'Add service', window.DeskV1AddService.panelHTML({ engines: _enginesCache, engineError: _enginesError }));
    }
    if (sel.indexOf('engine:') === 0) {
      const e = (_enginesCache || []).find((x) => `engine:${x.id}` === sel);
      return e ? Tiles.detailHTML(sel, e.label, window.DeskV1ConnectSavedCheck.html(e, window.DeskV1Engines.rowHTML(e))) : null;
    }
    if (sel.indexOf('source:') === 0) {
      const a = _contentSources().find((x) => `source:${x.id}` === sel);
      return a ? Tiles.detailHTML(sel, a.label, _sourceHTML(a)) : null;
    }
    if (sel.indexOf('service:') === 0) {
      const sv = window.DeskV1Services.byId(sel.slice(8));
      return sv ? Tiles.detailHTML(sel, sv.name, window.DeskV1Services.detailHTML(sv)) : null;
    }
    if (sel.indexOf('mcp:') === 0 && window.DeskV1ConnectionStatus) {
      const m = window.DeskV1ConnectionStatus.mcpByKey(sel);
      return m ? Tiles.detailHTML(sel, m.server_name, window.DeskV1ConnectionStatus.mcpDetailHTML(m)) : null;
    }
    const ch = _channels().find((c) => c.id === sel);
    return ch ? Tiles.detailHTML(ch.id, ch.label || ch.identity, _accountHTML(ch)) : null;
  }

  function deskV1RenderConnections(el, params) {
    const repaint = () => { if (el.isConnected) deskV1RenderConnections(el); };
    const channels = _channels();
    const live = window.DeskV1Store.live();
    const Tiles = window.DeskV1ConnTiles;
    // Routed here for one account (Where's "Connect ›"): open it.
    if (params && params.account) Tiles.select(params.account);
    const sel = Tiles.selected();
    const prevScroll = (el.querySelector('[data-connections]') || {}).scrollTop || 0;
    el.innerHTML = `
      <div class="desk-v1-connections" data-connections>
        <div class="desk-v1-conn-top">
          <p class="desk-v1-conn-lede">Everything external is connected here and nowhere else. A campaign only picks from what is connected.</p>
          ${live ? '<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-conn-recheck>Check again</button>' : ''}
        </div>
        ${Tiles.gridHTML({ items: _items(live, sel) })}
        ${_detail(sel) || ''}
      </div>`;
    const scroller = el.querySelector('[data-connections]');
    if (scroller && prevScroll) scroller.scrollTop = prevScroll;
    // Every change of selection starts the Add service panel over at its list.
    Tiles.bind(el, repaint, () => window.DeskV1AddService.reset());
    channels.forEach((ch) => {
      const row = el.querySelector(`[data-conn-account="${CSS.escape(ch.id)}"]`);
      if (!row) return;
      const btn = row.querySelector('[data-conn-action]');
      if (btn) _bindAction(btn, ch, repaint);
      const rm = row.querySelector('[data-conn-remove]');
      if (rm) rm.onclick = () => _removeAccount(ch, repaint);
      const xg = row.querySelector('[data-conn-x-guide]');
      if (xg) xg.onclick = () => {
        if (_xOpen.has(ch.id)) _xOpen.delete(ch.id); else { _xOpen.add(ch.id); _connOv = null; }
        repaint();
      };
      const xw = row.querySelector('[data-conn-x-wizard]');
      if (xw && _connOv) {
        window.DeskV1Guides.bindXWizard(xw, { onChange: () => { _connOv = null; _recheck(el, repaint); } });
      } else if (xw) {
        window.DeskV1Guides.overview({ force: true }).then((o) => { _connOv = o; repaint(); }).catch((err) => {
          xw.innerHTML = `<div class="desk-v1-stub-empty">Could not load the steps: ${esc(err && err.message ? err.message : err)}</div>`;
        });
      }
      _bindReadVia(row, ch, repaint);
      if (window.DeskV1ConnectionStatus) window.DeskV1ConnectionStatus.bindAccount(row, ch, repaint);
    });
    const recheck = el.querySelector('[data-conn-recheck]');
    if (recheck) recheck.onclick = () => { if (window.DeskV1ConnectionStatus) window.DeskV1ConnectionStatus.invalidate(); _recheck(el, repaint); };
    if (sel === 'add') {
      window.DeskV1AddService.bind(el, {
        repaint, channels: _channels, api: _api, engines: _enginesCache,
        onEnginesChanged: () => _reloadEngines(repaint),
      });
    } else if (sel && sel.indexOf('engine:') === 0 && _enginesCache) {
      const e = _enginesCache.find((x) => `engine:${x.id}` === sel);
      if (e) { window.DeskV1Engines.bindConnections(el, [e], () => _reloadEngines(repaint)); window.DeskV1ConnectSavedCheck.bind(el, e, repaint); }
    } else if (sel && sel.indexOf('service:') === 0) {
      const sv = window.DeskV1Services.byId(sel.slice(8));
      if (sv) window.DeskV1Services.bindDetail(el, sv, repaint);
    } else if (sel && sel.indexOf('mcp:') === 0 && window.DeskV1ConnectionStatus) {
      const m = window.DeskV1ConnectionStatus.mcpByKey(sel);
      if (m) window.DeskV1ConnectionStatus.bindMcp(el, m, repaint);
    }
    // Live only: engines and saved services are the server's, fetched once and kept
    // until something changes them. A failed load leaves an empty list (and the
    // reason, on the Add service panel) rather than asking again on every repaint.
    if (live) {
      if (!_enginesCache) {
        window.DeskV1Engines.list(null, { force: true }).then((e) => { _enginesCache = e; _enginesError = null; repaint(); })
          .catch((err) => { _enginesCache = []; _enginesError = err && err.message ? err.message : String(err); repaint(); });
      }
      if (window.DeskV1Services.rows() === null) window.DeskV1Services.load().then(repaint);
      const CS = window.DeskV1ConnectionStatus;      // local reads only: saved approvals and the no-network coverage route
      if (CS) {
        if (CS.mcpRows() === null) CS.loadMcp().then(repaint);
        CS.loadCoverage(channels.map(_homeProjectId), repaint);
      }
    }
  }

  window.deskV1RenderConnections = deskV1RenderConnections;
})();

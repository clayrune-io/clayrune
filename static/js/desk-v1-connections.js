// Connections grid and selected detail. Each kind owns its own renderer; readiness vocabulary is shared.
import { ConnectionWords as W } from './desk-v1-connection-words.js';
import { ConnectionRemoval as Remove } from './desk-v1-connection-remove.js';
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function _fx() { return window.DeskV1Store.state(); }
  function _channels() { return _fx().channels || []; }
  function _projects() { return _fx().projects || []; }
  function _campaigns() { return _fx().campaigns || []; }

  function _homeProjectId(ch) {
    const camp = _campaigns().find((c) => c.projectId && (c.plan && c.plan.accounts || []).includes(ch.id));
    if (camp) return camp.projectId;
    const first = _projects()[0];
    return first ? first.id : null;
  }
  function _api(method, url, body) { return window.DeskV1Store.api(method, url, body); }
  // A workspace account carries the server's derived `publish`; a fixture does not.
  function _isLiveRow(ch) { return !!ch.publish; }

  // Saved account readiness. A `held` account is one whose
  // login lapsed (the fixture's LinkedIn page); a preview account never
  // authenticates at all. A live account is exactly what `publish.ready` says,
  // with no Connect / Reconnect: nothing on this screen can connect it.
  function _status(ch) {
    const CS=window.DeskV1ConnectionStatus;
    if(_isLiveRow(ch) && CS) return CS.accountStatus(ch,CS.coverageRow(_homeProjectId(ch),ch.platform));
    return W.state(ch.preview || ch.connected===false?'unchecked':ch.health==='held'?'signin':'ready');
  }

  function _accountHTML(ch) {
    const st=_status(ch);
    return `<div class="desk-v1-conn-row" data-conn-account="${esc(ch.id)}" data-platform="${esc(ch.platform)}" data-conn-state="${st.key}">
      <div class="desk-v1-conn-head"><span class="desk-v1-channel-badge">${esc(W.serviceName(ch.platform))} · ${esc(window.DeskV1ConnTiles.plainName(ch.label) || ch.identity)}</span>
      <span class="desk-v1-conn-status" data-conn-status data-state="${st.key}">${esc(st.word)}</span></div>
      ${window.DeskV1ConnectionStatus.accountHTML(ch,window.DeskV1ConnectionStatus.coverageRow(_homeProjectId(ch),ch.platform),st)}
    </div>`;
  }

  // A connected cloud drive's detail. (One that is not connected is not on this
  // screen at all, and Clayrune cannot connect one today: see desk-v1-add-service.js.)
  function _sourceHTML(a) {
    return `
      <div class="desk-v1-conn-row" data-conn-source="${esc(a.id)}">
        <div class="desk-v1-conn-head">
          <span class="desk-v1-channel-badge">${esc(a.label)}${a.account ? ` · ${esc(a.account)}` : ''}</span>
          <span class="desk-v1-conn-status" data-state="ok">${W.status.ready}</span>
          ${Remove.html(a.label)}
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

  // Live: the vault is the human's. Opening it is all this screen does about a
  // missing token; "Check now" re-reads the derived state once they are back.
  function _recheck(el, repaint) {
    return _api('GET', '/api/desk/accounts').then((rows) => {
      window.DeskV1ConnectionStatus?.invalidate();
      const have = _channels();
      (rows || []).forEach((row) => {
        const mine = have.find((c) => c.id === row.id);
        if (mine) Object.assign(mine, row);
        else have.push(row);
      });
      repaint();
    }).catch((e) => DeskV1Kit.toast(`Could not re-check the accounts: ${e && e.message ? e.message : e}`));
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
    if(ch.preview || !live && ch.connected===false && !ch.userAdded)return false;
    const st = _status(ch).key;
    if (st === 'preview') return false;
    if (st === 'off') return live || !!ch.userAdded;
    return true;
  }

  function _items(live, sel) {
    const Tiles = window.DeskV1ConnTiles;
    const items = [];
    _channels().filter((ch) => _showAccount(ch, live, sel)).forEach((ch) => items.push({
      key: ch.id, kind: 'account', platform: ch.platform, brand: ch.platform,
      name: Tiles.plainName(ch.label) || ch.identity || ch.id, kindLabel: 'Social account', status: _status(ch),
    }));
    _contentSources().filter((a) => a.connected).forEach((a) => items.push({
      key: `source:${a.id}`, kind: 'source', brand: a.id,
      name: a.label, kindLabel: 'Content source', status: { key: 'ok', word: W.status.ready },
    }));
    if (live) (_enginesCache || []).forEach((e) => {
      const st = window.DeskV1Engines.tileState(e);
      if (st) items.push({ key: `engine:${e.id}`, kind: 'engine', brand: e.id,
        name: e.label, kindLabel: 'Generation engine', status: st });
    });
    if (live && window.DeskV1ConnectionStatus) window.DeskV1ConnectionStatus.mcpItems().forEach((m) => items.push(m));
    (window.DeskV1Services.rows() || []).filter(s => !s.account_id || !_channels().some(c => c.id === s.account_id)).forEach((s) => items.push({
      key: `service:${s.id}`, kind: 'service', link: s.link,
      name: s.name, kindLabel: s.browser_connection ? 'Browser sign-in' : 'Saved service', status: s.browser_connection ? window.DeskV1BrowserService.status(s) : window.DeskV1Services.STATUS,
    }));
    return items;
  }

  // The panel for the selected key, or null when the key matches nothing (an item
  // that is gone keeps its key: a refused Remove, or its Undo, brings it back).
  function _detail(sel) {
    const Tiles = window.DeskV1ConnTiles;
    if (!sel) return null;
    if (sel === 'add') {
      return Tiles.detailHTML('add', 'Add service', window.DeskV1AddService.panelHTML({ engines: _enginesCache, engineError: _enginesError, channels: _channels, api: _api }));
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
          ${live ? '<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-conn-recheck>${W.buttons.check}</button>' : ''}
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
      Remove.bind(row, {name:ch.label, key:ch.id, url:`/api/desk/accounts/${encodeURIComponent(ch.id)}`, onRemoved:() => {
        const list = _channels(), index = list.indexOf(ch);
        if (index >= 0) list.splice(index, 1);
        Tiles.select(null); repaint();
      }});
      if (window.DeskV1AssistedPost) window.DeskV1AssistedPost.bindAccount(row, ch, repaint);
      if (window.DeskV1ConnectionStatus) window.DeskV1ConnectionStatus.bindAccount(row, ch, repaint);
    });
    const recheck = el.querySelector('[data-conn-recheck]');
    if (recheck) recheck.onclick = () => { if (window.DeskV1ConnectionStatus) window.DeskV1ConnectionStatus.invalidate(); _recheck(el, repaint); };
    if (sel === 'add') {
      window.DeskV1AddService.bind(el, {
        repaint, channels: _channels, api: _api, engines: _enginesCache,
        onEnginesChanged: () => _reloadEngines(repaint),
        // A save makes an account and a saved service on the server: re-read both now, never wait for "Check now".
        onConnectionsChanged: () => Promise.all([_recheck(el, repaint), window.DeskV1Services.load(true).then(repaint)]),
      });
    } else if (sel && sel.indexOf('engine:') === 0 && _enginesCache) {
      const e = _enginesCache.find((x) => `engine:${x.id}` === sel);
      if (e) { window.DeskV1Engines.bindConnections(el, [e], () => _reloadEngines(repaint)); window.DeskV1ConnectSavedCheck.bind(el, e, repaint); }
    } else if (sel && sel.indexOf('service:') === 0) {
      const sv = window.DeskV1Services.byId(sel.slice(8));
      if (sv) window.DeskV1Services.bindDetail(el, sv, repaint);
    } else if (sel && sel.indexOf('source:') === 0) {
      const source = _contentSources().find(x => `source:${x.id}` === sel);
      const row = el.querySelector('[data-conn-source]');
      if (source && row) Remove.bind(row, {name:source.label, key:sel, preview:true, onRemoved:() => {
        const online = (_fx().studio || {}).online || {};
        (online.video || []).concat(online.image || []).filter(x => x.id === source.id).forEach(x => { x.connected = false; });
        Tiles.select(null); repaint();
      }});
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

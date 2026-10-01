// Desk v1 (MC-977, Ron 2026-10-01) — Connections: THE one place anything
// external is connected. A Desk route at home level (like Studio and
// Engagement), reached from the Desk header; Where's "not connected" tiles and
// Studio's online sources route here instead of running their own connect flow.
// Window-bridged module, no `import` (ground rule 1).
//
//   Social accounts  every workspace account: status, Connect / Reconnect, and
//                    for X + LinkedIn how the Desk READS it (Browser pane, free,
//                    or the platform API, paid) + the signed-in browser profile.
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
// or "not connected" with its reason (no X API token in the vault, LinkedIn app
// review pending) and, when a vault entry would fix it, `Open Secrets ›` (a
// human creates the credential there; nothing on this screen types one). The
// fixture Connect button is gone: it would claim a connection nothing made.
// Read via goes to M4 (`PATCH /api/desk/accounts/<id>`), still filed under the
// project that uses the account so engagement has something to poll. Add and
// Remove are M3/M5. YouTube / Discord / Reddit and the cloud drives stay
// placeholder tiles.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function _fx() { return window.DeskV1Store.state(); }
  function _channels() { return _fx().channels || []; }
  function _projects() { return _fx().projects || []; }
  function _campaigns() { return _fx().campaigns || []; }

  // Only X and LinkedIn have a read route; every other platform shows no control.
  const READ_VIA_PLATFORMS = ['x', 'linkedin'];

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
      if (ch.preview) return { key: 'preview', word: 'Preview · not connected', action: null };
      return ch.publish.ready ? { key: 'ok', word: 'Connected', action: null }
                              : { key: 'off', word: 'Not connected', action: null };
    }
    if (ch.connected === false) return { key: 'off', word: 'Not connected', action: 'Connect' };
    if (ch.preview) return { key: 'preview', word: 'Preview · not connected', action: null };
    if (ch.health === 'held') return { key: 'reauth', word: 'Needs re-auth', action: 'Reconnect' };
    return { key: 'ok', word: 'Connected', action: 'Reconnect' };
  }

  function _readViaHTML(ch) {
    if (READ_VIA_PLATFORMS.indexOf(ch.platform) < 0) return '';
    const via = ch.read_via === 'api' ? 'api' : 'pane';
    const apiLabel = ch.platform === 'x' ? 'X API (paid, ~$0.005 per read)' : 'LinkedIn API (paid)';
    const btn = (v, label) => `<button type="button" data-readvia="${v}" aria-pressed="${via === v}">${esc(label)}</button>`;
    const profile = via === 'pane' && ch.platform === 'x'
      ? `<label class="desk-v1-conn-readvia-profile">Signed-in browser profile
           <input type="text" class="desk-v1-rules-textinput" data-readvia-profile maxlength="64"
             placeholder="name of the saved profile" value="${esc(ch.browser_profile || '')}"></label>`
      : '';
    return `
        <div class="desk-v1-conn-readvia" data-readvia-row="${esc(ch.id)}" data-platform="${esc(ch.platform)}">
          <span class="desk-v1-how-field-label">Read via</span>
          <div class="desk-v1-conn-readvia-seg" role="group" aria-label="How the Desk reads this account">
            ${btn('pane', 'Browser pane (no charge)')}${btn('api', apiLabel)}
          </div>
          ${profile}
          <div class="desk-v1-rules-hint" data-readvia-status></div>
        </div>`;
  }

  // The live "Publishing" line: what the server derived and why. A token that
  // exists but may not be used unattended means scheduled posts will hold, which
  // is said here rather than found out at the first one.
  function _publishHTML(ch) {
    const pub = ch.publish;
    if (!pub || ch.preview) return '';
    const fix = !pub.ready && pub.secret
      ? `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-conn-secrets="${esc(ch.id)}">Open Secrets ›</button>` : '';
    const note = pub.ready && pub.unattended_ok === false
      ? `<div class="desk-v1-rules-hint" data-conn-unattended>Scheduled posts will be held: the vault entry <code>${esc(pub.secret)}</code> does not allow unattended use. “Approve now” still posts.</div>` : '';
    return `
        <div class="desk-v1-conn-publish" data-conn-publish data-ready="${pub.ready ? 'true' : 'false'}">
          <span class="desk-v1-how-field-label">Publishing</span>
          <span data-conn-publish-text>${pub.ready ? 'connected' : `not connected${pub.reason ? ` (${esc(pub.reason)})` : ''}`}</span>
          ${fix}
        </div>${note}`;
  }

  function _accountHTML(ch) {
    const st = _status(ch);
    const live = _isLiveRow(ch);
    const readable = live ? READ_VIA_PLATFORMS.indexOf(ch.platform) >= 0 && !ch.preview : (st.key === 'ok' || st.key === 'reauth');
    return `
      <div class="desk-v1-conn-row" data-conn-account="${esc(ch.id)}" data-platform="${esc(ch.platform)}" data-conn-state="${st.key}">
        <div class="desk-v1-conn-head">
          ${DeskV1Kit.channelBadge(ch)}
          <span class="desk-v1-conn-status" data-conn-status data-state="${st.key}">${esc(st.word)}</span>
          ${st.action ? `<button type="button" class="desk-v1-conn-btn" data-conn-action="${esc(ch.id)}">${st.action}</button>` : ''}
          ${live ? `<button type="button" class="desk-v1-conn-btn${st.action ? ' desk-v1-conn-btn-inline' : ''}" data-conn-remove="${esc(ch.id)}" aria-label="${esc(`Remove ${ch.label}`)}">Remove</button>` : ''}
        </div>
        ${live ? _publishHTML(ch) : ''}
        ${readable ? _readViaHTML(ch) : ''}
      </div>`;
  }

  // Live only: the channels that are not here yet. Placeholder tiles, nothing to
  // connect (each real connector is its own backlog item).
  const PLACEHOLDER_CHANNELS = [
    { id: 'youtube', label: 'YouTube' }, { id: 'discord', label: 'Discord' }, { id: 'reddit', label: 'Reddit' },
  ];
  const PLACEHOLDER_SOURCES = [
    { id: 'gdrive', label: 'Google Drive', connected: false }, { id: 'dropbox', label: 'Dropbox', connected: false },
  ];
  function _placeholderHTML(p) {
    return `
      <div class="desk-v1-conn-row" data-conn-placeholder="${esc(p.id)}">
        <div class="desk-v1-conn-head">
          <span class="desk-v1-channel-badge">${esc(p.label)}</span>
          <span class="desk-v1-conn-status" data-state="off">Not available yet</span>
        </div>
      </div>`;
  }

  // Live only: M3. Which platform, whose handle, an optional label. A credential
  // is never asked for here: the account will name the vault entry it needs.
  function _addFormHTML() {
    return `
        <form class="desk-v1-conn-add" data-conn-add autocomplete="off">
          <div class="desk-v1-rules-group-title">Add an account</div>
          <label class="desk-v1-conn-add-field">Platform
            <select data-conn-add-platform class="desk-v1-rules-textinput">
              <option value="x">X (Ron voice)</option>
              <option value="linkedin">LinkedIn Company Page (Clayrune voice)</option>
              <option value="blog">Blog (you publish it)</option>
            </select></label>
          <label class="desk-v1-conn-add-field">Handle or name
            <input type="text" class="desk-v1-rules-textinput" data-conn-add-identity maxlength="80" placeholder="@handle, or the page name"></label>
          <label class="desk-v1-conn-add-field">Label (optional)
            <input type="text" class="desk-v1-rules-textinput" data-conn-add-label maxlength="80"></label>
          <div class="desk-v1-conn-add-actions">
            <button type="submit" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-conn-add-submit>Add account</button>
            <span class="desk-v1-rules-hint" data-conn-add-status role="status"></span>
          </div>
        </form>`;
  }

  function _sourceHTML(a) {
    return `
      <div class="desk-v1-conn-row" data-conn-source="${esc(a.id)}">
        <div class="desk-v1-conn-head">
          <span class="desk-v1-channel-badge">${esc(a.label)}</span>
          <span class="desk-v1-conn-status" data-state="${a.connected ? 'ok' : 'off'}">${a.connected ? `Connected · ${esc(a.account || '')}` : 'Not connected'}</span>
          ${a.connected ? '' : `<button type="button" class="desk-v1-conn-btn" data-conn-source-connect="${esc(a.id)}" disabled aria-disabled="true">Connect</button>`}
        </div>
        ${a.connected ? '' : '<div class="desk-v1-rules-hint">Connecting is not available yet: each real connector is its own backlog item.</div>'}
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
  function _showCoverage(row, ch) {
    const out = row.querySelector('[data-readvia-status]');
    const pid = _homeProjectId(ch);
    if (!out || !pid) return;
    fetch(`/api/desk/engagement/coverage/${encodeURIComponent(pid)}`).then((r) => r.json()).then((d) => {
      const c = ((d || {}).coverage || []).find((x) => x.platform === ch.platform);
      out.textContent = c ? (c.message || '') : '';
      out.dataset.state = c ? c.state : '';
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
        DeskV1Kit.toast(`${ch.label} is now read via ${next === 'api' ? 'the ' + (ch.platform === 'x' ? 'X' : 'LinkedIn') + ' API (paid per read)' : 'the browser pane (no charge)'}.`);
        repaint();
      });
    }));
    const prof = readRow.querySelector('[data-readvia-profile]');
    if (prof) prof.addEventListener('change', () => {
      patch({ browser_profile: prof.value.trim() }).then((acc) => {
        if (!acc) return;
        ch.browser_profile = acc.browser_profile || '';
        _showCoverage(row, ch);
      });
    });
    _showCoverage(row, ch);
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

  // M3. The account appears with a provisional `publish` and takes the server's
  // answer (its real derived state) when it arrives.
  function _bindAddForm(el, repaint) {
    const form = el.querySelector('[data-conn-add]');
    if (!form) return;
    const status = form.querySelector('[data-conn-add-status]');
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const platform = form.querySelector('[data-conn-add-platform]').value;
      const identity = form.querySelector('[data-conn-add-identity]').value.trim();
      const label = form.querySelector('[data-conn-add-label]').value.trim();
      if (!identity) { status.textContent = 'Enter the handle or page name.'; return; }
      status.textContent = '';
      const id = 'acct-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
      const body = { id, platform, identity, capability: platform === 'blog' ? 'manual' : 'direct' };
      if (label) body.label = label;
      const acc = Object.assign({ label: label || identity, voice: '', connected: false,
        publish: { ready: false, reason: 'checking…', secret: null, unattended_ok: null } }, body);
      const list = _channels();
      window.DeskV1Store.write({
        label: `Added ${acc.label}`,
        apply: () => { list.push(acc); repaint(); },
        unapply: () => { const i = list.indexOf(acc); if (i >= 0) list.splice(i, 1); },
        repaint,
        request: () => _api('POST', '/api/desk/accounts', body).then((saved) => { Object.assign(acc, saved); repaint(); return saved; }),
        undoRequest: () => _api('DELETE', `/api/desk/accounts/${encodeURIComponent(id)}`),
      });
    });
  }

  let _enginesCache = null;   // live: GET /api/desk/engines, cleared when a limit changes

  function deskV1RenderConnections(el) {
    const repaint = () => { if (el.isConnected) deskV1RenderConnections(el); };
    const channels = _channels();
    const live = window.DeskV1Store.live();
    const sources = _contentSources();
    const sourceRows = sources.length ? sources : (live ? PLACEHOLDER_SOURCES : []);
    el.innerHTML = `
      <div class="desk-v1-connections" data-connections>
        <p class="desk-v1-conn-lede">Everything external is connected here and nowhere else. A campaign only picks from what is connected.</p>
        <section class="desk-v1-rules-group" data-conn-section="social">
          <div class="desk-v1-rules-group-title">Social accounts${live ? ' <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-conn-recheck>Check again</button>' : ''}</div>
          ${channels.length ? channels.map(_accountHTML).join('') : '<div class="desk-v1-stub-empty">No accounts yet.</div>'}
          ${live ? PLACEHOLDER_CHANNELS.map(_placeholderHTML).join('') + _addFormHTML() : ''}
        </section>
        <section class="desk-v1-rules-group" data-conn-section="sources">
          <div class="desk-v1-rules-group-title">Content sources</div>
          ${sourceRows.length ? sourceRows.map(_sourceHTML).join('') : '<div class="desk-v1-stub-empty">No sources.</div>'}
        </section>
        <section class="desk-v1-rules-group" data-conn-section="engines">
          <div class="desk-v1-rules-group-title">Generation engines</div>
          ${live ? window.DeskV1Engines.connectionsHTML(_enginesCache) : ''}
        </section>
      </div>`;
    channels.forEach((ch) => {
      const row = el.querySelector(`[data-conn-account="${CSS.escape(ch.id)}"]`);
      if (!row) return;
      const btn = row.querySelector('[data-conn-action]');
      if (btn) _bindAction(btn, ch, repaint);
      const rm = row.querySelector('[data-conn-remove]');
      if (rm) rm.onclick = () => _removeAccount(ch, repaint);
      const sec = row.querySelector('[data-conn-secrets]');
      if (sec) sec.onclick = () => { if (typeof window.openSecretsVault === 'function') window.openSecretsVault(); };
      _bindReadVia(row, ch, repaint);
    });
    const recheck = el.querySelector('[data-conn-recheck]');
    if (recheck) recheck.onclick = () => _recheck(el, repaint);
    _bindAddForm(el, repaint);
    if (live) {
      if (_enginesCache) window.DeskV1Engines.bindConnections(el, _enginesCache, () => { _enginesCache = null; repaint(); });
      else window.DeskV1Engines.list(null, { force: true }).then((e) => { _enginesCache = e; repaint(); }).catch((err) => {
        const box = el.querySelector('[data-conn-section="engines"]');
        if (box) box.insertAdjacentHTML('beforeend', `<div class="desk-v1-stub-empty">Could not load the engines: ${esc(err && err.message ? err.message : err)}</div>`);
      });
    }
  }

  window.deskV1RenderConnections = deskV1RenderConnections;
})();

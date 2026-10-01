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
//   Generation engines  placeholder heading only (video + image engines); a follow-up ticket fills it.
//
// Connect / Reconnect is fixture-only like the rest of R0: it flips the fixture
// account's state (with Undo) and authenticates nothing. Read via is real: it
// PATCHes /api/desk/presence/<pid>/accounts/<channel>/read, which still stores
// per project, so the account is filed under the project that uses it (the
// first campaign placing it), else the first project.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function _fx() { return window.DeskV1Fixtures || {}; }
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

  // Connected / Not connected / Needs re-auth. A `held` account is one whose
  // login lapsed (the fixture's LinkedIn page); a preview account never
  // authenticates at all.
  function _status(ch) {
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

  function _accountHTML(ch) {
    const st = _status(ch);
    const readable = st.key === 'ok' || st.key === 'reauth';
    return `
      <div class="desk-v1-conn-row" data-conn-account="${esc(ch.id)}" data-platform="${esc(ch.platform)}" data-conn-state="${st.key}">
        <div class="desk-v1-conn-head">
          ${DeskV1Kit.channelBadge(ch)}
          <span class="desk-v1-conn-status" data-conn-status data-state="${st.key}">${esc(st.word)}</span>
          ${st.action ? `<button type="button" class="desk-v1-conn-btn" data-conn-action="${esc(ch.id)}">${st.action}</button>` : ''}
        </div>
        ${readable ? _readViaHTML(ch) : ''}
      </div>`;
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
    const patch = (body) => fetch(_readUrl(ch), {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ platform: ch.platform }, body)),
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

  function deskV1RenderConnections(el) {
    const repaint = () => { if (el.isConnected) deskV1RenderConnections(el); };
    const channels = _channels();
    const sources = _contentSources();
    el.innerHTML = `
      <div class="desk-v1-connections" data-connections>
        <p class="desk-v1-conn-lede">Everything external is connected here and nowhere else. A campaign only picks from what is connected.</p>
        <section class="desk-v1-rules-group" data-conn-section="social">
          <div class="desk-v1-rules-group-title">Social accounts</div>
          ${channels.length ? channels.map(_accountHTML).join('') : '<div class="desk-v1-stub-empty">No accounts yet.</div>'}
        </section>
        <section class="desk-v1-rules-group" data-conn-section="sources">
          <div class="desk-v1-rules-group-title">Content sources</div>
          ${sources.length ? sources.map(_sourceHTML).join('') : '<div class="desk-v1-stub-empty">No sources.</div>'}
        </section>
        <section class="desk-v1-rules-group" data-conn-section="engines">
          <div class="desk-v1-rules-group-title">Generation engines</div>
        </section>
      </div>`;
    channels.forEach((ch) => {
      const row = el.querySelector(`[data-conn-account="${CSS.escape(ch.id)}"]`);
      if (!row) return;
      const btn = row.querySelector('[data-conn-action]');
      if (btn) _bindAction(btn, ch, repaint);
      _bindReadVia(row, ch, repaint);
    });
  }

  window.deskV1RenderConnections = deskV1RenderConnections;
})();

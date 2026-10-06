// Desk v1 — Connections: "Add your own MCP server" and its approval card
// (docs/DESK_SERVICE_PROFILES_SPEC.md sections 6.1 and 6.2; slice U2a). A section of the Connect
// flow's Method step (static/js/desk-v1-connect-flow.js). Window-bridged module, no `import`
// (ground rule 1). The server side is mc/desk_connect/custom_connection_*.py and
// mc/blueprints/desk_connect_custom_routes.py.
//
//   POST /api/desk/connect/custom/review   read the npm package without running it; returns the card.
//                                          Writes nothing.
//   POST /api/desk/connect/custom/connections   every approved server with its state now; the card lists the
//                                          npm ones and, when a package file moved since the approval, which
//                                          paths (detect only: nothing is blocked; MC-1054).
//   POST /api/desk/connect/custom/commit   the one Save, passcode-gated (humanProofFetch). Carries only
//                                          {request_id, fingerprint}: the command that runs is the one the
//                                          Review stored, never one typed here.
//
// The card is the server's own text (the exact command, the pin, the reach); nothing on it is a
// package's or a README's own wording. Any edit to the form drops the card, so what is approved is
// always the form as it stands. Global reach is a choice made on the form and named on the card; the
// default is one project. A saved server is never called Connected: the result says what it is
// (registered, or saved but not runnable yet) in the server's words. Holds no credential: a
// credential is the name of an environment variable and the name of a Secrets entry.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const STATE_WORD = { registered: 'Registered', pending_runtime: 'Saved, cannot run yet', setup_failed: 'Saved, setup failed' };

  function _fresh() {
    return { open: false, pkg: '', name: '', entry: '', args: '', creds: [], scope: 'project', projectId: '',
      busy: false, conns: null, card: null, approved: false, error: '', status: '', saving: false, result: null };
  }
  let U = _fresh();

  function reset() { U = _fresh(); }

  function _projects() {
    try { return (window.DeskV1Store && window.DeskV1Store.state().projects) || []; } catch (_) { return []; }
  }

  // The card belongs to the form it was made from: any edit drops it and the approval with it.
  function _touch() { U.card = null; U.approved = false; U.error = ''; U.result = null; }

  function _body() {
    const body = { package: U.pkg.trim(), scope: U.scope };
    if (U.name.trim()) body.server_name = U.name.trim();
    if (U.entry.trim()) body.entry = U.entry.trim();
    const args = U.args.split('\n').map((a) => a.trim()).filter(Boolean);
    if (args.length) body.args = args;
    const creds = U.creds.filter((c) => c.env.trim() || c.vault.trim()).map((c) => ({ env: c.env.trim(), vault: c.vault.trim() }));
    if (creds.length) body.credentials = creds;
    if (U.scope === 'project') body.project_id = U.projectId;
    return body;
  }

  function _size(bytes) { return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`; }

  // ── the form ────────────────────────────────────────────────────────────
  function _formHTML() {
    const projects = _projects();
    const projectOpts = projects.length
      ? projects.map((p) => `<option value="${esc(p.id)}"${U.projectId === p.id ? ' selected' : ''}>${esc(p.name || p.id)}</option>`).join('')
      : '<option value="">No project</option>';
    const creds = U.creds.map((c, i) => `
          <div class="desk-v1-cu-cred" data-cu-cred="${i}">
            <input type="text" class="desk-v1-rules-textinput" data-cu-cred-env="${i}" value="${esc(c.env)}" placeholder="VARIABLE_NAME" maxlength="64" autocapitalize="characters" spellcheck="false" aria-label="Environment variable the server reads">
            <input type="text" class="desk-v1-rules-textinput" data-cu-cred-vault="${i}" value="${esc(c.vault)}" placeholder="Secrets entry name" maxlength="120" autocapitalize="off" spellcheck="false" aria-label="Secrets entry that holds it">
            <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cu-cred-remove="${i}">Remove</button>
          </div>`).join('');
    return `
        <div class="desk-v1-cu-form" data-cu-form>
          <label class="desk-v1-conn-add-field">npm package
            <input type="text" class="desk-v1-rules-textinput" data-cu-package value="${esc(U.pkg)}" placeholder="@scope/name or name@1.2.3" maxlength="214" autocapitalize="off" spellcheck="false"></label>
          <label class="desk-v1-conn-add-field">Server name (optional)
            <input type="text" class="desk-v1-rules-textinput" data-cu-name value="${esc(U.name)}" maxlength="64" autocapitalize="off" spellcheck="false"></label>
          <label class="desk-v1-conn-add-field">Start file inside the package (optional)
            <input type="text" class="desk-v1-rules-textinput" data-cu-entry value="${esc(U.entry)}" maxlength="200" autocapitalize="off" spellcheck="false"></label>
          <label class="desk-v1-conn-add-field">Arguments, one per line (optional)
            <textarea class="desk-v1-rules-textinput" data-cu-args rows="2" spellcheck="false">${esc(U.args)}</textarea></label>
          <div class="desk-v1-conn-add-field">Credentials, by name only (optional)
            ${creds}
            <div class="desk-v1-rules-hint">Each one is an environment variable the server reads and the Secrets entry that holds its value. The value is never typed here and is never written into the MCP configuration.</div>
            <div><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cu-cred-add>Add a credential</button></div>
          </div>
          <fieldset class="desk-v1-cu-scope" data-cu-scope>
            <legend>Who can use it</legend>
            <label class="desk-v1-cf-check"><input type="radio" name="cu-scope" value="project" data-cu-scope-opt ${U.scope === 'project' ? 'checked' : ''}>
              <span>One project (recommended)
                <select class="desk-v1-rules-textinput" data-cu-project ${U.scope === 'project' ? '' : 'disabled'}>${projectOpts}</select></span></label>
            <label class="desk-v1-cf-check"><input type="radio" name="cu-scope" value="global" data-cu-scope-opt ${U.scope === 'global' ? 'checked' : ''}>
              <span>Global: agents in every project can use its tools</span></label>
          </fieldset>
          ${U.error ? `<div class="desk-v1-cf-msg" data-cf-msg="error" data-cu-error role="alert">${esc(U.error)}</div>` : ''}
          <div class="desk-v1-cf-actions desk-v1-cf-actions-flat">
            <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cu-close>Close</button>
            <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cf-primary" data-cu-review ${U.busy || !U.pkg.trim() || (U.scope === 'project' && !U.projectId) ? 'disabled' : ''}>${U.busy ? 'Reading the package…' : 'Review'}</button>
          </div>
        </div>`;
  }

  // ── approved servers: state now, and package files that moved since the approval ──
  function _driftHTML(f) {
    const rows = []
      .concat((f.changed || []).map((p) => ['changed', p]), (f.added || []).map((p) => ['added', p]), (f.removed || []).map((p) => ['removed', p]));
    const why = f.reason && f.reason !== 'files_differ' ? `<div data-cu-drift-reason="${esc(f.reason)}">The file record could not be used (${esc(f.reason.replace(/_/g, ' '))}).</div>` : '';
    return `${why}<ul class="desk-v1-cu-changes" data-cu-drift>${rows.map(([k, p]) => `<li data-cu-drift-path="${esc(k)}">${esc(k)}: <code>${esc(p)}</code></li>`).join('')}</ul>
            ${f.more ? `<div class="desk-v1-rules-hint" data-cu-drift-more>and ${esc(f.more)} more</div>` : ''}`;
  }

  function _approvedHTML() {
    const list = (U.conns || []).filter((c) => c.ecosystem === 'npm');
    if (!list.length) return '';
    return `<div class="desk-v1-cu-approved" data-cu-approved>
        <div class="desk-v1-rules-group-title">Approved npm servers</div>
        ${list.map((c) => {
    const f = c.package_files || {};
    const drift = c.code === 'package_files_changed';
    const note = f.status === 'not_recorded' ? '<div class="desk-v1-rules-hint" data-cu-files-note="not_recorded">The package files were not recorded when this was approved, so they are not checked. Approving it again records them.</div>' : '';
    return `<div class="desk-v1-cf-msg" data-cf-msg="${c.state === 'registered' ? 'ok' : 'warn'}" data-cu-approved-row="${esc(c.server_name)}" data-cu-approved-state="${esc(c.state)}" role="status"><strong>${esc(c.server_name)}</strong>: ${esc(STATE_WORD[c.state] || (c.state === 'changed' ? 'Changed' : c.state))}. ${esc(c.message || '')}
            ${drift ? _driftHTML(f) : ''}${note}</div>`;
  }).join('')}
      </div>`;
  }

  // Fills the list in place: a repaint here would replace the form under someone already typing in it.
  async function _loadConns(ctx) {
    try {
      const out = await ctx.api('POST', '/api/desk/connect/custom/connections', {});
      U.conns = (out && out.connections) || [];
    } catch (_) { U.conns = []; }
    const el = document.querySelector('[data-cu="open"]');  // the live node: the repaint after the click replaced the one bound
    if (!el) return;                                    // closed meanwhile
    const old = el.querySelector('[data-cu-approved]');
    if (old) old.remove();
    const block = _approvedHTML();
    const title = el.querySelector('.desk-v1-rules-group-title');
    if (block && title) title.insertAdjacentHTML('afterend', block);
  }

  // ── the approval card ───────────────────────────────────────────────────
  function _cardHTML(c) {
    const argv = [c.command.command].concat(c.command.args || []);
    const changes = (c.changes || []).length
      ? `<div class="desk-v1-cf-msg" data-cf-msg="warn" data-cu-reask role="status">This server was approved before. It needs a new approval because it changed:
            <ul class="desk-v1-cu-changes">${c.changes.map((x) => `<li data-cu-change="${esc(x.field)}">${esc(x.field)}: <code>${esc(x.from)}</code> to <code>${esc(x.to)}</code></li>`).join('')}</ul></div>` : '';
    const limits = (c.limitations || []).map((l) => `<div class="desk-v1-cf-msg" data-cf-msg="warn" data-cu-limit="${esc(l.code)}" role="status">${esc(l.message)}</div>`).join('');
    const creds = (c.credentials || []).length
      ? c.credentials.map((x) => `<div data-cu-credential>Secrets entry <code>${esc(x.vault)}</code> is given to the server as <code>${esc(x.env)}</code> when it starts.</div>`).join('')
      : 'None';
    const p = c.package;
    return `
        <section class="desk-v1-cf-install desk-v1-cu-card" data-cu-card="${esc(c.fingerprint)}" aria-label="Approve this MCP server">
          <div class="desk-v1-rules-group-title">${esc(c.title)}</div>
          <div class="desk-v1-cf-badge" data-cu-origin="${esc(c.origin.code)}">${esc(c.origin.label)}</div>
          ${changes}
          <dl class="desk-v1-cf-facts">
            <div><dt>Command</dt><dd><ol class="desk-v1-cu-argv" data-cu-command>${argv.map((a) => `<li><code>${esc(a)}</code></li>`).join('')}</ol></dd></div>
            <div><dt>Version pin</dt><dd data-cu-pin>${esc(p.version)}, pinned: it never updates by itself. Digest <code class="desk-v1-cf-wrap">${esc(p.integrity)}</code></dd></div>
            <div><dt>Source</dt><dd data-cu-source>${esc(p.registry)}: ${esc(p.source)}</dd></div>
            <div><dt>Publisher</dt><dd data-cu-publisher>${esc(p.publisher.name || 'not stated')} (${esc(p.publisher.status)}; ownership is not endorsement)</dd></div>
            <div><dt>Licence, size</dt><dd data-cu-size>${esc(p.licence || 'not stated')}; ${esc(_size(p.size_bytes))} download, ${esc(_size(p.unpacked_bytes))} unpacked</dd></div>
            <div><dt>Reach</dt><dd data-cu-reach="${esc(c.reach.scope)}">${esc(c.reach.who)} ${esc(c.reach.local_code)}</dd></div>
            <div><dt>Credentials</dt><dd data-cu-credentials>${creds}</dd></div>
            <div><dt>Registered as</dt><dd data-cu-server>MCP server <code>${esc(c.server_name)}</code> (${esc(c.protocol)})</dd></div>
            <div><dt>Working folder</dt><dd>${esc(c.working_directory)}</dd></div>
            <div><dt>First start</dt><dd>${esc(c.first_start)}</dd></div>
          </dl>
          <ul class="desk-v1-cf-perms" data-cu-risks>${c.risks.map((r) => `<li data-cu-risk="${esc(r.code)}">${esc(r.label)}</li>`).join('')}</ul>
          <div class="desk-v1-rules-hint" data-cu-install-note>${esc(c.install_note)}</div>
          ${limits}
          <label class="desk-v1-cf-check"><input type="checkbox" data-cu-approve ${U.approved ? 'checked' : ''}>
            <span>I have read this and approve saving it.</span></label>
          <div class="desk-v1-rules-hint">Saving asks for your dashboard passcode once. Nothing has been written yet.</div>
          <div class="desk-v1-cf-actions desk-v1-cf-actions-flat">
            <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cf-primary" data-cu-save ${U.saving || !U.approved ? 'disabled' : ''}>${U.saving ? 'Saving…' : 'Save'}</button>
          </div>
        </section>`;
  }

  function _resultHTML(r) {
    const word = STATE_WORD[r.state] || 'Saved';
    const kind = r.state === 'registered' ? 'ok' : 'warn';
    return `
        <div class="desk-v1-cu-result" data-cu-result="${esc(r.state)}">
          <div class="desk-v1-cf-msg" data-cf-msg="${kind}" role="status"><strong>${esc(word)}</strong>: ${esc(r.message || '')}</div>
          ${r.notice ? `<div class="desk-v1-cf-msg" data-cf-msg="warn" data-cu-notice role="status">${esc(r.notice)}</div>` : ''}
          <div class="desk-v1-rules-hint">Saving does not start the server or check that it works. It starts the first time an agent session uses it.</div>
          ${r.state === 'registered' ? '<div class="desk-v1-cf-actions desk-v1-cf-actions-flat"><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cu-done>Done</button></div>' : ''}
        </div>`;
  }

  function html(info) {
    if (!info) return '';
    if (!U.open) {
      return `<div class="desk-v1-cu" data-cu="closed">
          <div class="desk-v1-rules-hint">Know an MCP server that is not listed? You can add it yourself, from an npm package. Clayrune shows exactly what will run before anything is saved.</div>
          <div class="desk-v1-cf-actions desk-v1-cf-actions-flat"><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cu-open>Add your own MCP server</button></div>
        </div>`;
    }
    return `<div class="desk-v1-cu" data-cu="open">
        <div class="desk-v1-rules-group-title">Add your own MCP server</div>
        ${_approvedHTML()}
        ${U.result ? _resultHTML(U.result) : ''}
        ${U.result && U.result.state === 'registered' ? '' : _formHTML()}
        ${U.card && !(U.result && U.result.state === 'registered') ? _cardHTML(U.card) : ''}
        ${U.card && U.status ? `<div class="desk-v1-cf-msg" data-cf-msg="error" data-cu-status role="alert">${esc(U.status)}</div>` : ''}
      </div>`;
  }

  // ── actions ─────────────────────────────────────────────────────────────
  async function _review(ctx) {
    if (U.busy) return;
    _touch(); U.busy = true; U.status = ''; ctx.repaint();
    let card = null, error = '';
    try {
      card = await ctx.api('POST', '/api/desk/connect/custom/review', _body());
    } catch (e) {
      error = (e && e.message) || 'The review failed.';
    }
    U.busy = false;
    if (card && card.fingerprint) U.card = card; else U.error = error || 'The review failed.';
    ctx.repaint();
  }

  async function _save(ctx) {
    const c = U.card;
    if (U.saving || !c || !U.approved) return;
    U.saving = true; U.status = ''; ctx.repaint();
    let result;
    try {
      result = await window.humanProofFetch('/api/desk/connect/custom/commit', {
        method: 'POST', body: JSON.stringify({ request_id: c.request_id, fingerprint: c.fingerprint }),
      }, { title: 'Save MCP server', description: `Re-enter your dashboard passcode to save the MCP server “${c.server_name}”, which Clayrune has not reviewed.` });
    } catch (e) {
      result = { ok: false, status: 0, body: { error: e && e.message ? e.message : 'could not reach the server' } };
    }
    U.saving = false;
    if (result === null) { ctx.repaint(); return; }          // cancelled at the passcode: nothing was sent, the card stays
    if (!result.ok) {
      U.status = (result.body && (result.body.error || result.body.message)) || `The save failed (HTTP ${result.status}).`;
      if (result.body && (result.body.code === 'review_expired' || result.body.code === 'changed_since_review')) { U.card = null; U.approved = false; U.error = U.status; U.status = ''; }
      ctx.repaint();
      return;
    }
    const saved = result.body || {};
    U.result = saved; U.card = null; U.approved = false; U.status = '';
    ctx.repaint();
  }

  // ctx: { api(method, url, body), repaint() }
  function bind(root, info, ctx) {
    const el = root.querySelector('[data-cu]');
    if (!info || !el) return;
    const open = el.querySelector('[data-cu-open]');
    if (open) open.addEventListener('click', () => { U.open = true; if (!U.projectId) { const p = _projects()[0]; U.projectId = p ? p.id : ''; } ctx.repaint(); _loadConns(ctx); });
    el.querySelectorAll('[data-cu-close], [data-cu-done]').forEach((b) => b.addEventListener('click', () => { reset(); ctx.repaint(); }));
    const sync = () => {
      const r = el.querySelector('[data-cu-review]');
      if (r) r.disabled = U.busy || !U.pkg.trim() || (U.scope === 'project' && !U.projectId);
    };
    const drop = () => { _touch(); sync(); el.querySelectorAll('[data-cu-card], [data-cu-status], [data-cu-result]').forEach((n) => n.remove()); };
    const field = (sel, key) => {
      const n = el.querySelector(sel);
      if (n) n.addEventListener('input', () => { U[key] = n.value; drop(); });
    };
    field('[data-cu-package]', 'pkg'); field('[data-cu-name]', 'name'); field('[data-cu-entry]', 'entry'); field('[data-cu-args]', 'args');
    el.querySelectorAll('[data-cu-cred-env]').forEach((n) => n.addEventListener('input', () => { U.creds[+n.dataset.cuCredEnv].env = n.value; drop(); }));
    el.querySelectorAll('[data-cu-cred-vault]').forEach((n) => n.addEventListener('input', () => { U.creds[+n.dataset.cuCredVault].vault = n.value; drop(); }));
    el.querySelectorAll('[data-cu-cred-remove]').forEach((n) => n.addEventListener('click', () => { U.creds.splice(+n.dataset.cuCredRemove, 1); _touch(); ctx.repaint(); }));
    const add = el.querySelector('[data-cu-cred-add]');
    if (add) add.addEventListener('click', () => { U.creds.push({ env: '', vault: '' }); _touch(); ctx.repaint(); });
    el.querySelectorAll('[data-cu-scope-opt]').forEach((n) => n.addEventListener('change', () => { if (n.checked) { U.scope = n.value; _touch(); ctx.repaint(); } }));
    const proj = el.querySelector('[data-cu-project]');
    if (proj) proj.addEventListener('change', () => { U.projectId = proj.value; _touch(); ctx.repaint(); });
    const review = el.querySelector('[data-cu-review]');
    if (review) review.addEventListener('click', () => _review(ctx));
    const approve = el.querySelector('[data-cu-approve]');
    const save = el.querySelector('[data-cu-save]');
    if (approve) approve.addEventListener('change', () => { U.approved = approve.checked; if (save) save.disabled = U.saving || !U.approved; });
    if (save) save.addEventListener('click', () => _save(ctx));
  }

  window.DeskV1ConnectCustom = { html, bind, reset };
})();

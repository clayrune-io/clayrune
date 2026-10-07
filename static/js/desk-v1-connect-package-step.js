// Desk v1 — Connections: the Connect wizard's PACKAGE MCP screens (MC-1062 ticket 10,
// docs/desk_v1/connect_flow_tickets/10-package-screen.md; frame and rules in desk-v1-connect-wizard.js).
// Window-bridged module, no `import` (ground rule 1). It registers three screens through `registerScreen`
// for the branch `type = mcp, variant = custom-npm` (the type projection's "MCP server: npm package"), and
// owns nothing else: the old "Add your own MCP server" section (desk-v1-connect-custom.js) is untouched.
//
//   Setup        M1p "MCP package"      the package, Detect parameters (read-only), Continue.
//                M2p "Package details"  the editable detected settings (start file, arguments, credential names) and,
//                                       under Details, the optional server name and where each suggestion came from.
//   (Permissions is ticket 08's screen: for an MCP server it is the project / all-projects reach, nothing else.)
//   Review       M5p "Review package"   the immutable facts the server built: exact command, pin, source, reach,
//                                       credential names, the unreviewed-code notice and every material risk. Publisher,
//                                       licence, size and the dependency inventory are under the one Details.
//                M6p "Install steps"    only when the package has install scripts: at most four per page, every full
//                                       body visible, each OFF until ticked. Ticking one reviews again.
//                M7  "Approve connection" the existing approval box and Save, which asks for the dashboard passcode.
//   Result       the server's own state word and message: Registered / Saved, cannot run yet / Saved, setup failed.
//
// WHAT IT PRESERVES. Everything the card carries comes from POST /api/desk/connect/custom/review and is the
// server's text; the Save sends only {request_id, fingerprint} to /custom/commit through humanProofFetch, so the
// command that runs is the one the Review stored, never one typed here. Any edit to the package, a setting, the reach
// or a script tick drops the card and its approval: a new Review is a new card. A script whose body cannot be shown
// exactly has no checkbox. Nothing is installed, approved or run before Save; "No hidden install steps" is the server's
// statement (`install_note`) and is shown as it is.
//
// WHAT IT DOES NOT DO. A PyPI package can be read for suggestions (POST /api/desk/connect/detect, kind "pypi") but
// Clayrune has no Python package installer, so the screen says so and offers no way forward: it never calls the npm
// review for one and never says anything was installed. It writes no credential value (a credential is the NAME of
// an environment variable and the NAME of a Secrets entry) and no secret is in this module's state.
//
// ACTIVATION. The wizard is disabled until ticket 14, and the Package/Server-address choice that sets
// `variant = custom-npm` is not part of this ticket: until it exists, the screens are reachable only by
// `api.select({ variant: 'custom-npm' })`.
(function () {
  const W = window.DeskV1ConnectWizard;
  if (!W) return;
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const VARIANT = 'custom-npm';
  const STATE_WORD = { registered: 'Registered', pending_runtime: 'Saved, cannot run yet', setup_failed: 'Saved, setup failed' };
  const FLAG_WORD = {
    detected_from_untrusted_evidence: 'Read from a README or listing someone else wrote.',
    unpinned_package: 'No exact version was named; the review will pin the one it resolves.',
    install_scripts: 'The package declares install scripts. They are off unless you tick them in Review.',
    runs_arbitrary_code: 'The detected command runs a shell or inline code.',
    source_build_required: 'It needs a build step from source.',
    credential_like_text_not_copied: 'Text that looked like a secret was left out.',
  };
  const PYPI_NOTICE = 'This is a PyPI package. Clayrune can read it to suggest settings, but it has no Python package installer, so it cannot run one. Nothing was installed or saved.';

  let _gen = 0;                                                // bumped when the branch is dropped: an answer that arrives later is ignored

  function _fresh() {
    return { view: 'identify', git:null, command:'', staging:false, pkg: '', entry: '', args: '', name: '', creds: [], refocus: false,
      det: { busy: false, answer: null, alt: null, kind: '', error: '' },
      rv: { view: 'facts', key: '', busy: false, card: null, scripts: [], error: '', saving: false },
      result: null };
  }
  let P = _fresh();

  // ── what the person typed ───────────────────────────────────────────────
  const _spec = () => P.pkg.trim().replace(/^npm:/i, '');
  function _kind() { const t = P.pkg.trim(); return /^(pypi|pip):/i.test(t) || /^https?:\/\/(www\.)?pypi\.org\//i.test(t) ? 'pypi' : 'npm'; }
  function _isPypi() { return !!P.pkg.trim() && _kind() === 'pypi'; }
  function _creds() { return P.creds.map((c) => ({ env: c.env.trim(), vault: c.vault.trim() })); }
  function _halfCredential() { return _creds().find((c) => (c.env || c.vault) && !(c.env && c.vault)) || null; }
  function _pkgProblem() {
    const t = P.pkg.trim();
    if (!t) return window.DeskV1ConnectCopy.words.packageHint;
    if(window.DeskV1ConnectGithub.matches(t)) return '';
    if (_isPypi()) return PYPI_NOTICE;
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(_spec()) || /\s/.test(_spec())) return 'Enter the package name, such as @scope/name or name@1.2.3.';
    return '';
  }

  // ── Detect parameters (read-only; nothing is approved, installed or saved) ──
  function _altOf(answer, kind) {
    return (answer.alternatives || []).find((a) => a.package && a.package.ecosystem === kind) || null;
  }
  async function _detect(api) {
    const text = P.pkg.trim();
    if (!text || P.det.busy) return;
    const kind = _kind(), gen = _gen;
    P.det = { busy: true, answer: null, alt: null, kind, error: '' };
    api.repaint();
    let answer = null, error = '';
    try { answer = await api.ctx.api('POST', '/api/desk/connect/detect', { kind, input: kind === 'npm' ? _spec() : text }); }
    catch (e) { error = (e && e.message) || 'The detection failed.'; }
    if (gen !== _gen) return;
    const alt = answer ? _altOf(answer, kind) : null;
    P.det = { busy: false, answer, alt, kind, error: error || (answer && !alt ? 'Nothing usable was found for that package.' : '') };
    if (kind === 'npm' && alt) {                               // a suggestion fills only what is still empty; the person's own edits win
      const mine = (answer.alternatives || []).filter((a) => a.package && a.package.ecosystem === 'npm');
      // Arguments only from an alternative that runs the package's own start command: a README's `npx -y pkg` arguments are
      // for npx, and this flow never runs npx, so they are not suggestions here.
      const own = mine.find((a) => { const p = a.package || {}, c = a.fields && a.fields.command && a.fields.command.value; return c && (p.bin || []).indexOf(c) >= 0; });
      const args = own && own.fields.args && own.fields.args.value;
      if (!P.args.trim() && Array.isArray(args) && args.length) P.args = args.join('\n');
      const withEnv = mine.find((a) => (a.credentials || []).some((c) => c.placement === 'env' && c.name));
      if (!P.creds.length && withEnv) withEnv.credentials.filter((c) => c.placement === 'env' && c.name).slice(0, 6).forEach((c) => P.creds.push({ env: c.name, vault: '' }));
    }
    api.repaint();
  }

  function _detectLineHTML() {
    const d = P.det;
    if (d.busy) return '<div class="desk-v1-cfw-fact-text" data-pk-detect-state="busy" role="status">Reading the registry. Nothing is installed.</div>';
    if (d.error) return `<div class="desk-v1-cfw-msg" data-pk-detect-state="error" role="alert">${esc(d.error)}</div>`;
    if (!d.answer || !d.alt) return '';
    const p = d.alt.package || {};
    const what = `${esc(p.name || '')}${p.resolved_version ? ` ${esc(p.resolved_version)}` : ''}`;
    const word = d.kind === 'pypi' ? `Found <strong>${what}</strong> on PyPI.` : `Found <strong>${what}</strong> on npm. The settings on the next page are suggestions, not approved.`;
    const incomplete = d.answer.status && d.answer.status !== 'complete' ? ' The answer is incomplete; check every field.' : '';
    return `<div class="desk-v1-cfw-fact-text" data-pk-detect-state="${esc(d.answer.status || 'done')}" role="status">${word}${esc(incomplete)}</div>`;
  }

  // ── Setup ───────────────────────────────────────────────────────────────
  function _credRowsHTML() {
    return P.creds.map((c, i) => `
        <div class="desk-v1-cfk-cred" data-pk-cred="${i}">
          <input type="text" class="desk-v1-rules-textinput" data-pk-cred-env="${i}" value="${esc(c.env)}" placeholder="VARIABLE_NAME" maxlength="64" autocapitalize="characters" spellcheck="false" aria-label="Environment variable the server reads">
          <input type="text" class="desk-v1-rules-textinput" data-pk-cred-vault="${i}" value="${esc(c.vault)}" placeholder="Vault entry name" maxlength="120" autocapitalize="off" spellcheck="false" aria-label="Vault entry that holds it">
          <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-pk-cred-remove="${i}">Remove</button>
        </div>`).join('');
  }

  function _identifyHTML() {
    const pypi = _isPypi();
    return `<label class="desk-v1-conn-add-field">npm package
        <input type="text" class="desk-v1-rules-textinput" data-pk-package data-cfw-focus value="${esc(P.pkg)}" placeholder="@scope/name or name@1.2.3" maxlength="214" autocapitalize="off" spellcheck="false"></label>
      <div class="desk-v1-cfw-fact-text">${window.DeskV1ConnectCopy.words.packageHint}</div>
      <div class="desk-v1-cfk-actions"><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-pk-detect ${P.pkg.trim() && !P.det.busy ? '' : 'disabled'}>${P.det.busy ? 'Reading…' : 'Detect parameters'}</button></div>
      <div data-pk-detect-slot aria-live="polite">${_detectLineHTML()}</div>
      ${pypi ? `<div class="desk-v1-cfw-msg" data-pk-pypi role="alert">${esc(PYPI_NOTICE)}</div>` : ''}`;
  }

  function _detailsViewHTML() {
    return `${P.git?window.DeskV1ConnectGithub.commandHTML(P.command,P.git.manual_command):''}<div class="desk-v1-cfk-pkgline" data-pk-pkgline><span><strong>${esc(_spec())}</strong></span>
        <button type="button" class="desk-v1-cf-link" data-pk-change>Change package</button></div>
      <label class="desk-v1-conn-add-field">Start file inside the package (optional)
        <input type="text" class="desk-v1-rules-textinput" data-pk-entry data-cfw-focus value="${esc(P.entry)}" maxlength="200" autocapitalize="off" spellcheck="false"></label>
      <label class="desk-v1-conn-add-field">Arguments, one per line (optional)
        <textarea class="desk-v1-rules-textinput" data-pk-args rows="2" spellcheck="false">${esc(P.args)}</textarea></label>
      <div class="desk-v1-conn-add-field" data-pk-creds>Credentials, by name only (optional)
        ${_credRowsHTML()}
        <div class="desk-v1-cfw-fact-text">Each is an environment variable the server reads and the Vault entry that holds its value. The value is never typed here.</div>
        <div class="desk-v1-cfk-actions"><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-pk-cred-add>Add a credential</button></div>
      </div>`;
  }

  function _provenanceItems() {
    const out = [];
    const a = P.det.answer, alt = P.det.alt;
    if (!a || !alt) { out.push({ head: 'Detection', text: 'Detection was not run, so every field is what you typed. Nothing is approved until the Review step.' }); return out; }
    out.push({ head: 'Detected from untrusted text', text: a.warning || 'Read from a README or listing someone else wrote. Nothing here is approved, installed or connected.' });
    (a.evidence || []).forEach((e) => out.push({ head: `Evidence: ${e.label}`, text: `${e.source ? `from ${e.source}` : 'as typed'}${e.chars ? `, ${e.chars} characters read` : ''}${e.read_by_isolated_reader ? ' by a reader with no tools' : ''}.` }));
    const p = alt.package || {};
    const bins = (p.bin || []).join(', ');
    if (bins) out.push({ head: 'Start command the package lists', text: `${bins}. The review chooses the file that runs; name a start file here only if it says more than one.` });
    (alt.risk_flags || []).forEach((f) => { if (FLAG_WORD[f]) out.push({ head: 'Flag', text: FLAG_WORD[f] }); });
    (a.problems || []).forEach((q) => out.push({ head: 'Detection problem', text: q.message || q.code }));
    const other = (alt.credentials || []).filter((c) => c.placement !== 'env' && c.name).map((c) => c.name);
    if (other.length) out.push({ head: 'Credential names not used here', text: `${other.join(', ')} are not environment variables, so they were not filled in.` });
    return out;
  }
  function _provenanceHTML(api) {
    return api.paged('prov', _provenanceItems(), (it) => `<div class="desk-v1-cfw-fact"><div class="desk-v1-cfw-fact-head">${esc(it.head)}</div><div class="desk-v1-cfw-fact-text">${esc(it.text)}</div></div>`);
  }

  // ── Review: the request, the card, the views ────────────────────────────
  function _reach() {
    const S = window.DeskV1ConnectPermissionsStep;
    return S && typeof S.reach === 'function' ? S.reach() : null;
  }
  // { body, problem }. `approve_scripts` is added by the caller: it is not part of what invalidates a card.
  function _requestBody() {
    const r = _reach();
    if (!r) return { body: null, problem: 'The Permissions step has not chosen who can use this server.' };
    if (r.scope === 'project' && !r.project_id) return { body: null, problem: 'Choose a project on the Permissions step.' };
    const body = P.git ? {stage_id:P.git.stage_id,command:P.command.trim(),scope:r.scope} : {package:_spec(),scope:r.scope};
    if (r.scope === 'project') body.project_id = r.project_id;
    if (P.name.trim()) body.server_name = P.name.trim();
    if (P.entry.trim() && !P.git) body.entry = P.entry.trim();
    const args = P.args.split('\n').map((a) => a.trim()).filter(Boolean);
    if (args.length) body.args = args;
    const creds = _creds().filter((c) => c.env || c.vault);
    if (creds.length) body.credentials = creds;
    return { body, problem: '' };
  }

  async function _review(api, body) {
    const R = P.rv, gen = _gen;
    R.busy = true; R.error = ''; R.card = null;
    api.repaint();
    let card = null, error = '';
    try { card = await api.ctx.api('POST', '/api/desk/connect/custom/review', Object.assign({}, body, R.scripts.length ? { approve_scripts: R.scripts.slice() } : {})); }
    catch (e) { error = (e && e.message) || 'The review failed.'; }
    if (gen !== _gen) return;
    R.busy = false;
    if (card && card.fingerprint) { R.card = card; R.scripts = (card.scripts || []).filter((s) => s.approved).map((s) => s.id); }
    else R.error = error || 'The review failed.';
    api.repaint();
  }

  // Called from the Review screens' bind: a changed request is a new review, an unchanged one keeps its card.
  function _ensureReview(api) {
    const R = P.rv, { body } = _requestBody();
    if (!body) return;
    const key = JSON.stringify(body);
    if (R.key !== key) { R.key = key; R.card = null; R.scripts = []; R.error = ''; R.view = 'facts'; R.saving = false; }
    if (!R.card && !R.busy && !R.error) _review(api, body);
  }

  function _views() { return ['facts'].concat(P.rv.card && (P.rv.card.scripts || []).length ? ['scripts'] : [], ['approve']); }
  function _viewIndex() { const v = _views(); return Math.max(0, v.indexOf(P.rv.view)); }
  function _approved(api) { const R = P.rv; return !!R.card && api.approval('card') === R.card.fingerprint; }
  function _size(bytes) { return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round((bytes || 0) / 1024))} KB`; }

  function _notReadyHTML() {
    const R = P.rv;
    const problem = _requestBody().problem;
    if (problem) return `<div class="desk-v1-cfw-msg" data-pk-problem role="alert">${esc(problem)}</div>`;
    if (R.busy) return '<div class="desk-v1-cfw-fact-text" data-pk-reviewing role="status">Reading the package. Nothing is installed or saved.</div>';
    if (R.error) return `<div class="desk-v1-cfw-msg" data-pk-review-error role="alert">${esc(R.error)} <button type="button" class="desk-v1-cf-link" data-pk-rereview>Review again</button></div>`;
    return '<div class="desk-v1-cfw-fact-text" role="status">Reading the package…</div>';
  }

  function _publicTitle(c) { return window.DeskV1ConnectCopy.isEnabled() ? window.DeskV1ConnectCopy.words.package : c.title; }
  function _factsHTML(c) {
    const argv = [c.command.command].concat(c.command.args || []);
    const changes = (c.changes || []).length
      ? `<div class="desk-v1-cfw-msg" data-pk-reask role="status">This server was approved before. It needs a new approval because it changed:
          <ul class="desk-v1-cfk-list">${c.changes.map((x) => `<li data-pk-change-field="${esc(x.field)}">${esc(x.field)}: <code>${esc(x.from)}</code> to <code>${esc(x.to)}</code></li>`).join('')}</ul></div>` : '';
    const limits = (c.limitations || []).map((l) => `<div class="desk-v1-cfw-msg" data-pk-limit="${esc(l.code)}" role="status">${esc(l.message)}</div>`).join('');
    const creds = (c.credentials || []).length
      ? c.credentials.map((x) => `<div data-pk-credential>Vault entry <code>${esc(x.vault)}</code> is given to the server as <code>${esc(x.env)}</code> when it starts.</div>`).join('') : 'None';
    const p = c.package || {};
    const fact = (head, html, attr) => `<div class="desk-v1-cfw-fact"${attr ? ` ${attr}` : ''}><div class="desk-v1-cfw-fact-head">${esc(head)}</div><div class="desk-v1-cfw-fact-text">${html}</div></div>`;
    return `${changes}${limits}
      <div class="desk-v1-cfk-origin" data-pk-origin="${esc(c.origin && c.origin.code)}"><span class="desk-v1-cfw-tag">${esc(c.origin ? c.origin.label : 'User supplied; not reviewed by Clayrune')}</span> <strong>${esc(_publicTitle(c))}</strong></div>
      ${fact('Command', `<ol class="desk-v1-cu-argv" data-pk-command>${argv.map((a) => `<li><code>${esc(a)}</code></li>`).join('')}</ol>`)}
      ${c.command.starts?fact('Command started after the file check',`<ol class="desk-v1-cu-argv">${[c.command.starts.command,...c.command.starts.args].map(a=>`<li><code>${esc(a)}</code></li>`).join('')}</ol>`):''}
      ${fact('Version pin', `${esc(p.version)}, pinned: it never updates by itself. Digest <code class="desk-v1-cf-wrap" data-pk-digest>${esc(p.integrity)}</code>`, 'data-pk-pin')}
      ${fact('Source', `${esc(p.registry)}: <code>${esc(p.source)}</code>`, 'data-pk-source')}
      ${fact('Reach', `${esc(c.reach.who)} ${esc(c.reach.local_code)}`, `data-pk-reach="${esc(c.reach.scope)}"`)}
      ${fact('Credentials', creds, 'data-pk-credentials')}
      <ul class="desk-v1-cfk-list" data-pk-risks>${(c.risks || []).map((r) => `<li data-pk-risk="${esc(r.code)}">${esc(r.label)}</li>`).join('')}</ul>`;
  }

  // Details of the facts view: publisher, licence, size, the dependency inventory and the rest of the evidence.
  function _inventoryItems(c) {
    const p = c.package || {}, items = [];
    const row = (head, attr, html) => `<div class="desk-v1-cfw-fact" ${attr}><div class="desk-v1-cfw-fact-head">${esc(head)}</div><div class="desk-v1-cfw-fact-text">${html}</div></div>`;
    if(c.repository_checks) items.push({html:`<pre>${esc(JSON.stringify(c.repository_checks,null,2))}</pre>`});
    items.push({ html: [
      row('Publisher', 'data-pk-publisher', `${esc((p.publisher && p.publisher.name) || 'not stated')} (${esc(p.publisher && p.publisher.status)}; ownership is not endorsement)`),
      row('Licence, size', 'data-pk-size', `${esc(p.licence || 'not stated')}; ${esc(_size(p.size_bytes))} download, ${esc(_size(p.unpacked_bytes))} unpacked`),
      row('Registered as', 'data-pk-server', `MCP server <code>${esc(c.server_name)}</code> (${esc(c.protocol)}). Working folder: ${esc(c.working_directory)} First start: ${esc(c.first_start)}`),
      row('What is installed', 'data-pk-install-note', esc(c.install_note))].join('') });
    const D = window.DeskV1ConnectCustomDeps && window.DeskV1ConnectCustomDeps.parts;
    if (D && (c.dependencies || []).length) {
      items.push({ html: `<div class="desk-v1-cfw-fact" data-pk-dependencies-head><div class="desk-v1-cfw-fact-head">Dependencies</div><div class="desk-v1-cfw-fact-text">${esc(D.depSummary(c))}. Each is installed at exactly this version and only if its archive has exactly this digest.</div></div>` });
      c.dependencies.forEach((d) => items.push({ html: `<ul class="desk-v1-cu-dep-list desk-v1-cfk-deplist">${D.dep(d)}</ul>` }));
    }
    if (D) { const n = D.notRun(c); if (n) items.push({ html: n }); }
    return items;
  }
  function _factsDetailsHTML(api, c) { return `<div class="desk-v1-cfw-dgroup" data-pk-inventory>${api.paged('inventory', _inventoryItems(c), (it) => it.html)}</div>`; }

  function _scriptItemHTML(s) {
    const D = window.DeskV1ConnectCustomDeps && window.DeskV1ConnectCustomDeps.parts;
    return `<div class="desk-v1-cfk-script" data-cfw-alt><ul class="desk-v1-cu-script-list">${D ? D.script(s) : ''}</ul></div>`;
  }
  function _scriptsHTML(api, c) {
    const note = c.scripts_note ? `<div class="desk-v1-cfw-fact-text" data-pk-scripts-note>${esc(c.scripts_note)}</div>` : '';
    return `${note}<div data-pk-scripts>${api.paged('scripts', c.scripts || [], _scriptItemHTML)}</div>`;
  }

  function _approveHTML(api, c) {
    const ticked = (c.scripts || []).filter((s) => s.approved).length;
    const p = c.package || {};
    const lines = [`<li data-pk-sum="package">${esc(_publicTitle(c))}, version ${esc(p.version)}, pinned.</li>`,
      `<li data-pk-sum="reach">${esc(c.reach.who)}</li>`,
      ticked ? `<li data-pk-sum="scripts">${ticked} install script${ticked === 1 ? '' : 's'} you ticked run${ticked === 1 ? 's' : ''} once, at Save, with this account's file and network access.</li>` : '<li data-pk-sum="scripts">No install script runs.</li>'];
    return `<ul class="desk-v1-cfk-list" data-pk-summary>${lines.join('')}</ul>
      <label class="desk-v1-cf-check desk-v1-cfk-approve"><input type="checkbox" data-pk-approve ${_approved(api) ? 'checked' : ''}>
        <span>I have read this and approve saving it.</span></label>
      <div class="desk-v1-cfw-fact-text">Saving asks for your dashboard passcode once. Nothing has been written yet.</div>
      ${P.rv.error ? `<div class="desk-v1-cfw-msg" data-pk-save-error role="alert">${esc(P.rv.error)}</div>` : ''}`;
  }

  const REVIEW_TITLE = { facts: 'Review package', scripts: 'Install steps', approve: 'Approve connection' };
  const REVIEW_COPY = {
    facts: 'This runs local code. Review the exact command and package before approving.',
    scripts: 'Approved install steps can fetch or write files that are not pinned.',
    approve: 'Save exactly what you reviewed. Changes require a new approval.',
  };
  const _isMine = (sel) => sel.type === 'mcp' && sel.variant === VARIANT;

  // The passcode-gated Save. -> true (saved: go to Result) | false (stay: cancelled, refused, or the card went stale)
  async function _save(api) {
    const R = P.rv, c = R.card;
    if (R.saving || !c || !_approved(api)) return false;
    R.saving = true; R.error = '';
    let res;
    try {
      res = await window.humanProofFetch('/api/desk/connect/custom/commit', { method: 'POST', body: JSON.stringify({ request_id: c.request_id, fingerprint: c.fingerprint }) },
        { title: 'Save MCP server', description: `Re-enter your dashboard passcode to save the MCP server “${c.server_name}”, which Clayrune has not reviewed.` });
    } catch (e) { res = { ok: false, status: 0, body: { error: (e && e.message) || 'could not reach the server' } }; }
    R.saving = false;
    if (res === null) { api.repaint(); return false; }                       // cancelled at the passcode: nothing was sent, the card stays
    if (!res.ok) {
      const b = res.body || {}, msg = b.error || b.message || `The save failed (HTTP ${res.status}).`;
      if (b.code === 'review_expired' || b.code === 'changed_since_review') { const cur = _requestBody().body; R.card = null; R.key = cur ? JSON.stringify(cur) : ''; R.view = 'facts'; R.error = `${msg} Review again to get a new card.`; }   // key kept: no silent re-review, the person sees why
      else R.error = msg;
      api.repaint();
      return false;
    }
    P.result = res.body || {};
    R.card = null; R.key = ''; R.scripts = []; R.error = '';
    return true;
  }

  // ── the screens ─────────────────────────────────────────────────────────
  function _focusTitle(root) { if (P.refocus) { P.refocus = false; const t = root.querySelector('[data-cfw-title]'); if (t) t.focus({ preventScroll: false }); } }

  W.registerScreen({
    id: 'package-setup', step: 'setup', match: _isMine,
    substep: () => [P.view === 'identify' ? 1 : 2, 2],
    title: () => (P.view === 'identify' ? 'MCP package' : 'Package details'),
    copy: () => (P.view === 'identify' ? 'Enter the package you want to use.' : 'Review the detected settings and name any credentials in the Vault.'),
    body: () => (P.view === 'identify' ? _identifyHTML() : _detailsViewHTML()),
    details: (api) => (P.view === 'identify' ? '' : `<label class="desk-v1-conn-add-field">Server name (optional)
        <input type="text" class="desk-v1-rules-textinput" data-pk-name value="${esc(P.name)}" maxlength="64" autocapitalize="off" spellcheck="false"></label>
      <div class="desk-v1-cfw-dgroup" data-pk-provenance><div class="desk-v1-cfw-dtitle">Where the suggestions came from</div>${_provenanceHTML(api)}</div>`),
    primary: (api) => ({
      label: 'Continue', disabled: P.staging || (P.view === 'identify' ? !!_pkgProblem() : P.git && !P.command.trim()),
      run: async () => {
        if (P.view === 'identify') { const why = _pkgProblem(); if (why) { api.error(why); return false; }
          if(window.DeskV1ConnectGithub.matches(P.pkg)) { const generation=_gen;P.staging=true;api.repaint();try {const result=await window.DeskV1ConnectGithub.stage(api,P.pkg);if(generation!==_gen)return false;P.git=result;P.command=result.command || '';P.args=(result.args||[]).join('\n');P.creds=(result.credentials||[]).map(c=>({env:c.env,vault:''}));} catch(e) {if(generation===_gen)api.error(e.message);return false;} finally {if(generation===_gen){P.staging=false;api.repaint();}} }
          P.view = 'details'; P.refocus = true; api.repaint(); return false; }
        const half = _halfCredential();
        if (half) { api.error(`Name both the variable and the Vault entry${half.env ? ` for ${half.env}` : ''}, or remove the row.`); return false; }
        return true;                                                         // Continue writes nothing: the Review step reads the package
      },
    }),
    bind: (root, api) => {
      _focusTitle(root);
      if(P.git) root.querySelector('[data-pk-entry]')?.closest('label').setAttribute('hidden','');
      const primary = root.querySelector('[data-cfw-primary]');
      const field = (sel, key) => { const n = root.querySelector(sel); if (n) n.addEventListener('input', () => { P[key] = n.value; if(key==='command' && primary) primary.disabled=!P.command.trim();if (key === 'pkg') { _gen++;P.staging=false;P.git=null;P.command='';P.det = { busy: false, answer: null, alt: null, kind: '', error: '' }; sync(); } }); };
      const sync = () => {
        const d = root.querySelector('[data-pk-detect]');
        if (d) d.disabled = !P.pkg.trim() || P.det.busy;
        if (primary && P.view === 'identify') primary.disabled = !!_pkgProblem();
        const slot = root.querySelector('[data-pk-detect-slot]');
        if (slot) slot.innerHTML = _detectLineHTML();
        const form = root.querySelector('[data-cfw-body]');
        const note = form && form.querySelector('[data-pk-pypi]');
        if (form && P.view === 'identify') {
          if (_isPypi() && !note) form.insertAdjacentHTML('beforeend', `<div class="desk-v1-cfw-msg" data-pk-pypi role="alert">${esc(PYPI_NOTICE)}</div>`);
          else if (!_isPypi() && note) note.remove();
        }
      };
      field('[data-pk-command-input]','command');field('[data-pk-package]', 'pkg'); field('[data-pk-entry]', 'entry'); field('[data-pk-args]', 'args'); field('[data-pk-name]', 'name');
      root.querySelectorAll('[data-pk-cred-env]').forEach((n) => n.addEventListener('input', () => { P.creds[+n.dataset.pkCredEnv].env = n.value; }));
      root.querySelectorAll('[data-pk-cred-vault]').forEach((n) => n.addEventListener('input', () => { P.creds[+n.dataset.pkCredVault].vault = n.value; }));
      root.querySelectorAll('[data-pk-cred-remove]').forEach((n) => n.addEventListener('click', () => { P.creds.splice(+n.dataset.pkCredRemove, 1); api.repaint(); }));
      const add = root.querySelector('[data-pk-cred-add]');
      if (add) add.addEventListener('click', () => { P.creds.push({ env: '', vault: '' }); api.repaint(); });
      const det = root.querySelector('[data-pk-detect]');
      if (det) det.addEventListener('click', async () => {if(window.DeskV1ConnectGithub.matches(P.pkg)) {const generation=++_gen;P.staging=true;api.repaint();try{const r=await window.DeskV1ConnectGithub.stage(api,P.pkg);if(generation!==_gen)return;P.git=r;P.command=r.command||'';P.args=(r.args||[]).join('\n');P.creds=(r.credentials||[]).map(c=>({env:c.env,vault:''}));P.view='details';}catch(e){if(generation===_gen)api.error(e.message);}finally{if(generation===_gen){P.staging=false;api.repaint();}}}else _detect(api);});
      const change = root.querySelector('[data-pk-change]');
      if (change) change.addEventListener('click', () => { P.view = 'identify'; P.refocus = true; api.repaint(); });
    },
    discard: () => { _gen++; P = _fresh(); },
  });

  W.registerScreen({
    id: 'package-review', step: 'review', match: _isMine,
    substep: () => { const v = _views(); return [_viewIndex() + 1, v.length]; },
    title: () => REVIEW_TITLE[P.rv.view] || REVIEW_TITLE.facts,
    copy: () => REVIEW_COPY[P.rv.view] || REVIEW_COPY.facts,
    body: (api) => {
      const R = P.rv, c = R.card;
      if (!c) return _notReadyHTML();
      if (!_views().includes(R.view)) R.view = 'facts';
      const prev = R.view === 'facts' ? '' : `<div class="desk-v1-cfk-pkgline"><button type="button" class="desk-v1-cf-link" data-pk-prev>‹ Back to ${R.view === 'approve' && _views().includes('scripts') ? 'install steps' : 'the package facts'}</button></div>`;
      if (R.view === 'scripts') return `${prev}${_scriptsHTML(api, c)}`;
      if (R.view === 'approve') return `${prev}${_approveHTML(api, c)}`;
      return _factsHTML(c);
    },
    details: (api) => (P.rv.card && P.rv.view === 'facts' ? _factsDetailsHTML(api, P.rv.card) : ''),
    primary: (api) => {
      const R = P.rv, ready = !!R.card && !R.busy && !_requestBody().problem;
      if (ready && R.view === 'approve') return { label: R.saving ? 'Saving…' : 'Save', disabled: R.saving || !_approved(api), run: () => _save(api) };
      return {
        label: 'Continue', disabled: !ready,
        run: async () => { const v = _views(), at = _viewIndex(); if (at < v.length - 1) { R.view = v[at + 1]; P.refocus = true; api.repaint(); } return false; },
      };
    },
    bind: (root, api) => {
      _ensureReview(api);
      _focusTitle(root);
      const R = P.rv;
      const again = root.querySelector('[data-pk-rereview]');
      if (again) again.addEventListener('click', () => { R.error = ''; R.key = ''; api.repaint(); });
      const prev = root.querySelector('[data-pk-prev]');
      if (prev) prev.addEventListener('click', () => { const v = _views(), at = _viewIndex(); R.view = v[Math.max(0, at - 1)]; P.refocus = true; api.repaint(); });
      root.querySelectorAll('[data-cu-script-tick]').forEach((b) => b.addEventListener('change', () => {
        const id = b.dataset.cuScriptTick, set = new Set(R.scripts);
        if (b.checked) set.add(id); else set.delete(id);
        R.scripts = Array.from(set);
        const { body } = _requestBody();
        if (body) _review(api, body);                                       // the server builds a new card with these approved: new fingerprint, approval cleared
      }));
      const approve = root.querySelector('[data-pk-approve]'), primary = root.querySelector('[data-cfw-primary]');
      if (approve) approve.addEventListener('change', () => {
        if (R.card) api.approve('card', approve.checked ? R.card.fingerprint : null);
        if (primary) primary.disabled = R.saving || !_approved(api);
      });
    },
    discard: () => { _gen++; P = _fresh(); },
  });

  W.registerScreen({
    id: 'package-result', step: 'result', match: _isMine,
    title: () => STATE_WORD[(P.result || {}).state] || 'Saved',
    copy: () => 'Your saved server and its status are shown below.',
    body: () => {
      const r = P.result || {};
      return `<div class="desk-v1-cfw-msg" data-pk-result="${esc(r.state || '')}" role="status"><strong>${esc(STATE_WORD[r.state] || 'Saved')}</strong>: ${esc(r.message || '')}</div>
        ${r.notice ? `<div class="desk-v1-cfw-msg" data-pk-notice role="status">${esc(r.notice)}</div>` : ''}
        <div class="desk-v1-cfw-fact-text">Saving does not start the server or check that it works. It starts the first time an agent session uses it.</div>`;
    },
    // The common panel refreshes saved connection tiles without inventing a service record.
    primary: (api) => ({ label: 'Done', run: async () => { api.finish(null, {}); api.repaint(); return false; } }),
    bind: (root) => _focusTitle(root),
    discard: () => { _gen++; P = _fresh(); },
  });

  // For the Review/Result tickets: non-secret facts of what is set up so far. null when nothing is.
  function summary() {
    if (!P.pkg.trim()) return null;
    const c = P.rv.card;
    return { kind: 'mcp-package', package: _spec(), saved: !!P.result, state: P.result ? P.result.state : null,
      lines: [`npm package ${_spec()}`].concat(c ? [`Version ${c.package && c.package.version}, pinned`, c.reach && c.reach.who] : []).filter(Boolean) };
  }
  window.DeskV1ConnectPackageStep = { summary, VARIANT };
})();

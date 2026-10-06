// Desk v1 — Connections: the Connect wizard's REVIEW and RESULT screens for an account connection
// (MC-1062 ticket 13a, docs/desk_v1/connect_flow_tickets/13a-review-result.md; frame and rules in
// desk-v1-connect-wizard.js). Window-bridged module, no `import` (ground rule 1). It registers two screens
// through `registerScreen`: Review (the one Save) and Result.
//
// WHEN IT SHOWS. The chosen connection type is Sign in (a browser sign-in, ticket 04) or API (a provider's
// own setup, ticket 09). Package MCP (ticket 10) keeps its own Review and Result, because its Review is the
// immutable approval card; this module never matches it. A branch that wants these screens plugs in with
// `registerBranch` (see the marked registration point at the bottom).
//
// WHAT REVIEW IS. The facts of the ONE selected connection, no more: service, type, account, the login and
// browser profile or the app fields, the permissions chosen on the Permissions screen, and the material risks.
// It states, in order, which separate checks the Save will ask for. A password or secret is never shown: the
// typed login is "entered, hidden", a stored one is named. Save is the only write the wizard makes.
//
// WHAT SAVE DOES (and does not). It is not one operation. It runs, in order, each with its OWN passcode prompt:
//   1. the connection save    LoginStep.commit() / ApiStep.commit() (their own passcode-gated request);
//   2. permissions            PermissionsStep.apply(accountId): the Desk write, then the browser-site write,
//                             each with its own prompt. Called only AFTER step 1 succeeded.
// No passcode is cached here or replayed across endpoints: every prompt is `humanProofFetch` inside the step that
// owns the request, and this module never sees the code. If step 1 fails or is cancelled nothing else runs and
// the person stays on Review. If step 1 succeeds the wizard moves to Result whatever happens to step 2, and the
// Result says so: "Connection saved; permissions unchanged", never a success. Retrying step 2 from the Result
// repeats only that operation (PermissionsStep keeps the request identity of an unchanged draft).
//
// WHAT RESULT IS. Four facts kept apart, each in its own row, none implying another:
//   Connection   saved / already saved (what the server wrote)      Permissions  saved / unchanged / NOT applied
//   Login/Sign-in what was stored; whether the profile exists; the   Verified     only a check that passed, and a
//                server's own status word for an API connection                   failed check is shown as failed
// A stored login is not a signed-in profile, a signed-in profile is not a verified connection, and a failed check
// is never shown as a pass. Result offers only what exists already: Open sign-in, Sign in with the saved login
// (the existing passcode-gated fill), Refresh status (read-only), Check it now (the existing free read-only
// check, API only, and only for the states the old Result offered it in). It adds no probe, makes no atomicity
// claim, and does not manage tiles (13b).
//
// WHICH ACCOUNT. Like the sign-in, API and Permissions screens, the account is chosen by an earlier screen that
// is not part of this ticket, and handed over in the same shape AFTER `api.select({ account })`:
//     setTarget({ kind, account: { id } | { new: { identity, label? } }, identity? })
// With no target Review says so and Save stays off.
(function () {
  const W = window.DeskV1ConnectWizard;
  if (!W) return;
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const FILL_KEY = 'summary-fill';
  const COST_BEARING = ['published', 'account_specific'];     // type_view: the cost bases that mean "may cost money"
  const CAN_CHECK = ['key_stored', 'signed_in', 'verified', 'check_failed', 'unknown'];   // the states the existing Result offered "Check it now" in
  const KIND_WORD = { account: 'Account', member: 'Personal profile', organization: 'Company Page' };
  const REVIEW_COPY = 'Save these connection details. Sign-in and permission changes keep their own passcode checks.';

  function _fresh() {
    return { saving: false, result: null, perms: null, requested: [], status: null, check: null, busy: '', applying: false, info: null, sel: null };
  }
  let R = _fresh();
  let _target = null;
  let _seq = 0;

  const _uuid = () => (window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() : `sm-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  void _uuid;

  // ── what the frame is showing ───────────────────────────────────────────
  function _label(info) { return info && info.service && info.service.label ? info.service.label : (info && info.host) || 'the service'; }
  function _service(info) { return info && info.service ? info.service.id : ''; }
  function _variant(sel, info) {
    const t = ((info && info.picker) || []).find((x) => x.id === sel.type);
    if (!t) return null;
    if (sel.variant) return t.variants.find((v) => v.id === sel.variant) || null;
    return t.variants.length === 1 ? t.variants[0] : null;
  }
  function _provider(sel, info) {
    const v = _variant(sel, info);
    return v && v.setup && v.setup.mode === 'full' && v.setup.via === 'provider' ? v : null;
  }
  function _signinUrl(info, routeId) {
    for (const t of ((info && info.picker) || [])) for (const v of (t.variants || [])) { if (v.route_id === routeId && v.setup && v.setup.signin && v.setup.signin.url) return v.setup.signin.url; }
    return '';
  }
  function _accountText(info) {
    if (!_target) return '';
    const kind = _target.kind === 'account' ? '' : `${KIND_WORD[_target.kind] || 'Account'}: `;     // the row is already called Account
    const a = _target.account;
    return a.id ? `${kind}saved account ${esc(_target.identity || a.id)}` : `${kind}${esc(a.new.identity)} (new)`;
  }

  function _rows(rows) {
    const list = rows.filter(Boolean).map(([k, v, key]) => `<div data-sum-row="${esc(key || k.toLowerCase().replace(/[^a-z]+/g, '-'))}"><dt>${esc(k)}</dt><dd>${v}</dd></div>`);
    return list.length ? `<dl class="desk-v1-cf-facts" data-sum-facts>${list.join('')}</dl>` : '';
  }

  // ── the branches ────────────────────────────────────────────────────────
  // A branch: { id, word, match(sel, info), problem(info) -> '' | why Save is off, save() -> the commit outcome,
  //   accountId(result) -> the saved account's id, factsHTML(info) -> rows for Review, risks(sel, info) -> [text],
  //   install?() -> the install card Review must have approved,
  //   result: { rows(res, st, info) -> rows, extra(res, st, info) -> html, actions(res, st) -> [[id, label]], bind(root, res, api) } }
  const _branches = [];
  function registerBranch(def) {
    if (!def || !def.id || typeof def.match !== 'function' || typeof def.save !== 'function' || !def.result) throw new Error('registerBranch: a branch needs an id, match, save and result');
    const at = _branches.findIndex((b) => b.id === def.id);
    if (at >= 0) _branches[at] = def; else _branches.push(def);
  }
  function _branch(sel, info) {
    return _branches.find((b) => { try { return !!b.match(sel, info); } catch (e) { console.warn('[connect-summary] match failed for ' + b.id + ': ' + e); return false; } }) || null;
  }

  // ── Review ──────────────────────────────────────────────────────────────
  function _perm() { return window.DeskV1ConnectPermissionsStep || null; }
  function _permSummary() { const P = _perm(); return P && typeof P.summary === 'function' ? P.summary() : null; }

  function _problem(br, info) {
    if (!_target) return 'Choose the account first.';
    if (!_permSummary()) return 'Choose the permissions first. Allowing nothing is a choice.';
    const why = br.problem ? br.problem(info) : '';
    if (why) return why;
    const card = br.install ? br.install() : null;
    if (card && !(window.DeskV1ConnectInstall && window.DeskV1ConnectInstall.approved(card))) return 'Approve the install first.';
    return '';
  }

  function _permHTML() {
    const s = _permSummary();
    if (!s) return '<span data-sum-perm-none>Not chosen yet.</span>';
    const lines = s.lines.map((l) => `<div data-sum-perm-line>${esc(l)}</div>`).join('');
    return `${lines}${s.pending ? '<div class="desk-v1-cfw-fact-text" data-sum-perm-pending>Applied after the connection is saved, with its own passcode check.</div>' : ''}`;
  }

  function _risks(br, sel, info) {
    const out = br.risks ? br.risks(sel, info) : [];
    const s = _permSummary();
    if (s && s.notice) out.push(s.notice);
    return out;
  }

  function _stepsHTML() {
    const s = _permSummary();
    const items = ['Save the connection. It asks for your passcode.'];
    if (s && s.pending) items.push('Apply permissions. Each change asks for your passcode again.');
    return `<ol class="desk-v1-cfsum-steps" data-sum-steps aria-label="What happens when you save">${items.map((t) => `<li>${esc(t)}</li>`).join('')}</ol>`;
  }

  // An approval is for the card as it was read: Back dropped the frame's record of it, so the card's own flag goes too.
  // The frame asks for `primary` before `body`, so both call this first.
  function _syncApproval(api, br) {
    const card = br.install ? br.install() : null, I = window.DeskV1ConnectInstall;
    if (card && I && !api.approval('install') && I.approved(card)) I.clear();
  }

  function _reviewBody(api) {
    R.info = api.info; R.sel = api.sel;
    const br = _branch(api.sel, api.info);
    if (!br) return '';
    _syncApproval(api, br);
    const card = br.install ? br.install() : null;
    const I = window.DeskV1ConnectInstall;
    const rows = _rows([['Service', esc(_label(api.info))], ['Connection', esc(br.word)], ['Account', _accountText(api.info) || '<span data-sum-noaccount>Not chosen yet.</span>', 'account']]);
    const risks = _risks(br, api.sel, api.info);
    const why = _problem(br, api.info);
    return `${rows}${br.factsHTML ? br.factsHTML(api.info) : ''}
      ${_rows([['Permissions', _permHTML(), 'permissions']])}
      ${risks.length ? `<ul class="desk-v1-cfsum-risks" data-sum-risks aria-label="Material risks">${risks.map((t) => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}
      ${card && I ? I.html(card) : ''}
      ${_stepsHTML()}
      ${why ? `<div class="desk-v1-cfw-fact-text" data-sum-problem>${esc(why)}</div>` : ''}`;
  }

  function _reviewDetails(api) {
    const items = [
      ['Separate passcode checks', 'Saving the connection, allowing reading or posting, and a browser-site grant each ask for the passcode on their own. None is reused for another.'],
      ['Nothing is signed in or checked', 'Saving does not sign you in, send anything to the service or verify the connection. Those are separate, later actions.'],
      ['Passwords', 'A password you typed goes to Secrets with the save. It is shown nowhere, not even here.'],
    ];
    return `<div class="desk-v1-cfw-dgroup" data-sum-details>${api.paged('details', items,
      (it) => `<div class="desk-v1-cfw-fact"><div class="desk-v1-cfw-fact-head">${esc(it[0])}</div><div class="desk-v1-cfw-fact-text">${esc(it[1])}</div></div>`)}</div>`;
  }

  // Step 1, then step 2. -> true (moved on: the connection is saved) | false (stay on Review: it was not).
  async function _save(api) {
    const br = _branch(api.sel, api.info);
    if (!br || R.saving) return false;
    const why = _problem(br, api.info);
    if (why) { api.error(why); return false; }
    R.saving = true;
    const n = _seq;
    try {
      const requested = (_permSummary() || { lines: [] }).lines.slice();
      const c = await br.save();
      if (n !== _seq) return false;                              // the wizard was closed or changed meanwhile: nothing here is for this branch any more
      if (c.cancelled) { R.saving = false; api.error('The passcode was not entered. Nothing was sent.'); return false; }     // `saving` first: the error repaints, and Save must be live again in it
      if (!c.ok) { R.saving = false; api.error(c.error || 'The save did not work.', 'Permissions were not changed. Fix it and save again.'); return false; }
      const accountId = String(br.accountId(c.result) || '');
      R.result = c.result; R.requested = requested; R.status = c.result.status || null;
      R.perms = await _applyPerms(accountId);
      return n === _seq;
    } finally { R.saving = false; }
  }

  // Step 2. Never throws and never claims more than happened. The permissions step keeps its own passcode prompts.
  async function _applyPerms(accountId) {
    const P = _perm();
    if (!P || typeof P.apply !== 'function') return { ok: true, pending: false, desk: { status: 'unchanged' }, site: { status: 'unchanged' } };
    try { return await P.apply(accountId); }
    catch (e) { return { ok: false, pending: false, desk: { status: 'failed', error: (e && e.message) || 'That did not work.' }, site: { status: 'not_attempted' } }; }
  }

  W.registerScreen({
    id: 'summary-review', step: 'review',
    match: (sel, info) => !!_branch(sel, info),
    title: (api) => `Review ${_label(api.info)}`,
    copy: () => REVIEW_COPY,
    body: _reviewBody,
    details: _reviewDetails,
    primary: (api) => {
      const br = _branch(api.sel, api.info);
      if (br) _syncApproval(api, br);
      return { label: 'Save', disabled: !br || R.saving || !!_problem(br, api.info), run: () => _save(api) };
    },
    bind: (root, api) => {
      const br = _branch(api.sel, api.info);
      if (!br) return;
      const primary = root.querySelector('[data-cfw-primary]');
      const card = br.install ? br.install() : null;
      if (card && window.DeskV1ConnectInstall) {
        window.DeskV1ConnectInstall.bind(root, card, () => {
          api.approve('install', window.DeskV1ConnectInstall.approved(card) ? true : undefined);
          if (primary) primary.disabled = R.saving || !!_problem(br, api.info);
        });
      }
    },
    discard: () => { _seq++; const Sg = window.DeskV1ConnectSignin; if (Sg && Sg.clearFill) Sg.clearFill(FILL_KEY); R = _fresh(); _target = null; },
  });

  // ── Result ──────────────────────────────────────────────────────────────
  // 'ok' | 'partial': a requested permission that is not applied, or one still waiting for the sign-in, is never green.
  function _outcome() {
    const P = _perm();
    if (!R.perms || !P || typeof P.outcome !== 'function') return { kind: 'ok', lines: [] };
    const o = P.outcome(true, R.perms);
    const bad = !R.perms.ok || R.perms.pending;
    const lines = (o.lines || []).slice();
    if (bad && !lines.length) lines.push('Connection saved; permissions unchanged: that did not work.');
    return { kind: bad ? 'partial' : 'ok', lines };
  }

  function _permResultHTML() {
    const o = _outcome(), p = R.perms;
    if (!p) return '<span data-sum-perm-state="unchanged">Unchanged. None were chosen.</span>';
    if (o.kind === 'partial') return `<span data-sum-perm-state="not_applied">Not applied.</span> ${o.lines.map((l) => `<div data-sum-perm-line>${esc(l)}</div>`).join('')}`;
    const changed = p.desk.status === 'saved' || p.site.status === 'saved';
    return `<span data-sum-perm-state="${changed ? 'saved' : 'unchanged'}">${changed ? 'Saved.' : 'Unchanged.'}</span> ${R.requested.map((l) => `<div data-sum-perm-line>${esc(l)}</div>`).join('')}`;
  }

  function _verifiedHTML() {
    const st = R.status || {};
    if (st.state === 'verified') return `<span data-sum-verified="yes">Yes.</span> ${esc(st.capability || '')}${st.identity ? ` as <code>${esc(st.identity)}</code>` : ''}${st.at ? `, ${esc(String(st.at).replace('T', ' ').slice(0, 16))} UTC` : ''}`;
    if (st.state === 'check_failed') return `<span data-sum-verified="failed">No: the check failed.</span> ${esc(R.check && R.check.text ? R.check.text : (st.message || ''))}`;
    return '<span data-sum-verified="no">Not verified.</span>';
  }

  function _resultBody(api) {
    R.info = api.info; R.sel = api.sel;
    const br = _branch(api.sel, api.info);
    if (!br || !R.result) return '<div class="desk-v1-cfw-msg" role="alert">There is no saved connection to show.</div>';
    const o = _outcome();
    const banner = o.kind === 'partial'
      ? `<div class="desk-v1-cfw-msg desk-v1-cfsum-banner" data-sum-banner="partial" role="alert">${o.lines.map((l) => `<div>${esc(l)}</div>`).join('')}</div>` : '';
    const rows = br.result.rows(R.result, R.status, api.info);
    const extra = br.result.extra ? br.result.extra(R.result, R.status, api.info) : '';
    const actions = [];
    if (o.kind === 'partial' && R.perms) actions.push(['perms', R.perms.pending ? 'Apply permissions now' : 'Try permissions again']);
    (br.result.actions ? br.result.actions(R.result, R.status) : []).forEach((a) => actions.push(a));
    const buttons = actions.slice(0, 4).map(([id, text]) => `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-sum-act="${esc(id)}" ${R.busy || R.applying ? 'disabled' : ''}>${esc(R.busy === id ? 'Working…' : text)}</button>`).join('');
    const note = R.check && R.check.text && R.status && R.status.state !== 'check_failed'
      ? `<div class="desk-v1-cfw-msg" data-sum-check-note role="status">${esc(R.check.text)}</div>` : '';
    return `${banner}${_rows(rows.concat([['Permissions', _permResultHTML(), 'permissions'], ['Verified', _verifiedHTML(), 'verified']]))}${extra}${note}
      ${buttons ? `<div class="desk-v1-cfsum-actions" data-sum-actions>${buttons}</div>` : ''}`;
  }

  async function _retryPerms(api) {
    const br = _branch(api.sel, api.info);
    if (!br || !R.result || R.applying) return;
    R.applying = true; api.repaint();
    const n = _seq;
    const res = await _applyPerms(String(br.accountId(R.result) || ''));
    if (n !== _seq) return;
    R.perms = res; R.applying = false; api.repaint();
  }

  // The existing read-only status read (`check: false`) or the existing free check (`check: true`). Nothing else.
  async function _verify(api, check) {
    const res = R.result;
    if (!res || R.busy) return;
    R.busy = check ? 'check' : 'refresh'; R.check = null; api.repaint();
    const n = _seq;
    try {
      const body = { service: res.service && res.service.id ? res.service.id : _service(api.info), method: res.method, check: !!check };
      if (res.account_id) body.account_id = res.account_id;
      const out = await api.ctx.api('POST', '/api/desk/connect/verify', body);
      if (n !== _seq) return;
      R.status = out; R.check = check || (out && out.message) ? { text: (out && out.message) || '' } : null;
    } catch (e) {
      if (n !== _seq) return;
      R.check = { text: e && e.message ? e.message : 'The check could not run.', failed: true };
    }
    R.busy = ''; api.repaint();
  }

  function _anyPermSaved() { return !!R.perms && (R.perms.desk.status === 'saved' || R.perms.site.status === 'saved'); }
  function _resultTitle(api) {
    if (!R.result) return 'Not saved';
    if (_outcome().kind !== 'partial') return `${_label(api.info)} saved`;
    return _anyPermSaved() ? 'Connection saved; some permissions not applied' : 'Connection saved; permissions unchanged';
  }
  function _resultCopy() {
    if (!R.result) return 'Nothing was saved. Go back to the start and try again.';
    return _outcome().kind === 'partial' ? 'Your connection is saved. A permission you chose is not applied yet.' : 'Your connection is saved. Its status is shown below.';
  }

  W.registerScreen({
    id: 'summary-result', step: 'result',
    match: (sel, info) => !!_branch(sel, info),
    title: _resultTitle,
    copy: _resultCopy,
    body: _resultBody,
    primary: (api) => ({
      label: 'Done',
      run: async () => {
        const res = R.result || {};
        const label = _outcome().kind === 'partial' ? (_anyPermSaved() ? 'connection saved, some permissions not applied' : 'connection saved, permissions unchanged') : ((R.status && R.status.label) || 'saved');
        const svc = res.service && typeof res.service === 'object' && res.service.id ? res.service : { id: _service(api.info), label: _label(api.info) };
        api.finish(svc, { provider: true, status: { label } });
        return false;
      },
    }),
    bind: (root, api) => {
      const br = _branch(api.sel, api.info);
      if (!br) return;
      root.querySelectorAll('[data-sum-act]').forEach((b) => b.addEventListener('click', () => {
        const id = b.dataset.sumAct;
        if (id === 'perms') _retryPerms(api);
        else if (id === 'refresh') _verify(api, false);
        else if (id === 'check') _verify(api, true);
      }));
      if (br.result.bind) br.result.bind(root, R.result, api);
    },
    discard: () => { _seq++; const Sg = window.DeskV1ConnectSignin; if (Sg && Sg.clearFill) Sg.clearFill(FILL_KEY); R = _fresh(); _target = null; },
  });

  // ── built-in branch: Sign in (a browser sign-in, ticket 04) ─────────────
  function _L() { return window.DeskV1ConnectLoginStep || null; }
  const _profileWord = { new: 'is new: not signed in yet.', exists: 'exists. Clayrune has not checked that it is signed in.', unknown: 'could not be looked up.' };

  registerBranch({
    id: 'signin', word: 'Sign in (username/password)',
    match: (sel) => sel.type === 'signin' && !!_L(),
    problem: () => (_L().summary() ? '' : 'Go back and choose how to sign in.'),
    save: () => _L().commit(),
    accountId: (res) => res.account_id,
    factsHTML: () => {
      const s = _L() && _L().summary();
      if (!s) return '';
      const login = s.mode === 'saved' ? `Saved login <code>${esc(s.login)}</code>. Its password stays in Secrets.`
        : s.mode === 'new' ? `New login <code>${esc(s.login)}</code>${s.username ? `, username ${esc(s.username)}` : ''}. Password ${s.hasPassword ? 'entered, hidden' : 'not entered'}. Stored when you save.`
          : 'None stored. You sign in yourself in the browser.';
      return _rows([['Login', login, 'login'], ['Browser profile', `<code>${esc(s.profile)}</code> (${s.newProfile ? 'new' : 'existing: it keeps the sign-in it already has'})`, 'profile']]);
    },
    risks: () => [],
    result: {
      rows: (res, st, info) => {
        const prof = `<code>${esc(res.browser_profile)}</code> ${esc(_profileWord[res.profile_state] || _profileWord.unknown)}`;
        const shared = (res.shared_with || []).length ? `<div class="desk-v1-cfw-fact-text" data-sum-shared>Also used by: ${esc(res.shared_with.join(', '))}.</div>` : '';
        const signed = res.profile_state === 'new' ? `<span data-sum-signin="not_yet">Not yet.</span> Sign in to the browser profile <code>${esc(res.browser_profile)}</code>.`
          : `<span data-sum-signin="not_checked">Not checked.</span> Clayrune cannot tell whether <code>${esc(res.browser_profile)}</code> is signed in.`;
        return [
          ['Connection', res.unchanged ? 'Already saved. Nothing changed.' : (res.account_created ? 'Saved. A new account was created.' : 'Saved to the existing account.'), 'connection'],
          ['Login', res.login ? `<span data-sum-login="stored">${res.login_created ? 'Stored in Secrets now' : 'Saved login in use'}:</span> <code>${esc(res.login)}</code>` : '<span data-sum-login="none">None stored.</span> You sign in yourself in the browser.', 'login'],
          ['Browser profile', `${prof}${shared}`, 'profile'],
          ['Signed in', signed, 'signin'],
        ];
      },
      extra: (res, st, info) => {
        const Sg = window.DeskV1ConnectSignin;
        const url = _signinUrl(info, res.route_id);
        const fill = Sg && res.login ? Sg.fillHTML(FILL_KEY, { url, login: res.login, profile: res.browser_profile, hint: '', why: '' }) : '';
        return url || fill ? `<div class="desk-v1-cfsum-fill" data-sum-fill>${fill || `<div class="desk-v1-cs-fill-row"><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-sum-open="${esc(url)}">Open sign-in</button></div>`}</div>` : '';
      },
      actions: () => [],
      bind: (root, res, api) => {
        const Sg = window.DeskV1ConnectSignin;
        const url = _signinUrl(api.info, res.route_id);
        root.querySelectorAll('[data-sum-open]').forEach((b) => b.addEventListener('click', () => {
          try { if (typeof window.openBrowserPane === 'function') window.openBrowserPane(b.dataset.sumOpen, null, null, res.browser_profile || null); } catch (_) { /* the pane shows its own failure */ }
        }));
        // "Sign in with the saved login": the existing passcode-gated fill, its own prompt, the account's own saved login.
        if (Sg && res.login) Sg.bindFill(root, FILL_KEY, { url, profile: res.browser_profile }, api.ctx,
          () => ({ service: res.service, route_id: res.route_id, account_id: res.account_id, profile: res.browser_profile }));
      },
    },
  });

  // ── built-in branch: API (a provider's own setup, ticket 09) ────────────
  function _A() { return window.DeskV1ConnectApiStep || null; }

  registerBranch({
    id: 'api', word: 'API / developer app',
    match: (sel, info) => sel.type === 'api' && !!_A() && !!_provider(sel, info),
    problem: () => '',
    save: () => _A().commit(),
    accountId: (res) => res.account_id,
    install: () => (_A() && _A().installCard ? _A().installCard() : null),
    factsHTML: () => `<div class="desk-v1-cfsum-api" data-sum-api>${_A().reviewHTML()}</div>`,
    risks: (sel, info) => { const v = _provider(sel, info); return v && COST_BEARING.indexOf(v.cost && v.cost.basis) >= 0 ? ['API use may cost money.'] : []; },
    result: {
      rows: (res, st, info) => {
        const signed = st && (st.state === 'signed_in' || st.state === 'verified');
        const state = res.signin && !signed ? { state: 'sign_in_required', label: 'Sign-in required' } : null;
        const failed = res.setup && res.setup.state === 'failed' && !(st && (st.state === 'signed_in' || st.state === 'verified'));
        const word = state || (failed ? { state: 'setup_failed', label: 'Saved; setup failed' } : { state: (st && st.state) || 'not_connected', label: (st && st.label) || 'Not connected' });
        const stored = (res.stored || []).length ? res.stored.map((n) => `<code>${esc(n)}</code>`).join(', ') : 'Nothing new. What was already in Secrets is used as it is.';
        const acct = res.account ? esc(res.account.label || res.account.identity || '') : '';
        const note = failed && res.setup.message ? `<div class="desk-v1-cfw-fact-text" data-sum-setup>${esc(res.setup.message)}</div>` : '';
        return [
          ['Connection', res.duplicate ? 'Already saved. Nothing changed.' : 'Saved.', 'connection'],
          acct ? ['Account', acct, 'account'] : null,
          ['Stored in Secrets', stored, 'stored'],
          ['Status', `<span data-sum-status="${esc(word.state)}">${esc(word.label)}</span>${note}`, 'status'],
        ];
      },
      actions: (res, st) => {
        const out = [];
        if (res.signin && res.signin.auth_url) out.push(['signin', 'Open the sign-in']);
        out.push(['refresh', 'Refresh status']);
        if (st && CAN_CHECK.indexOf(st.state) >= 0) out.push(['check', 'Check it now']);
        return out;
      },
      bind: (root, res) => {
        root.querySelectorAll('[data-sum-act="signin"]').forEach((b) => b.addEventListener('click', () => {
          try { if (typeof window.openBrowserPane === 'function') window.openBrowserPane(res.signin.auth_url, null, null, res.signin.profile || null); } catch (_) { /* the pane shows its own failure */ }
        }));
      },
    },
  });

  // ── REGISTRATION POINT: other branches ──────────────────────────────────
  // The remote MCP step (ticket 11) and any later branch that wants this Review and Result calls
  //     DeskV1ConnectSummaryStep.registerBranch({ id, word, match(sel, info), problem(info), save(), accountId(result),
  //       factsHTML(info), risks(sel, info), install?(), result: { rows(res, st, info), extra?, actions?, bind? } })
  // from its own file. Nothing here knows about it: no remote or reference code exists in this module, and the
  // package branch is not registered because it owns its own Review and Result.

  // ── the account this connection is for ──────────────────────────────────
  function setTarget(t) {
    if (t == null) { _target = null; return; }
    if (!t || typeof t !== 'object' || !t.kind || !t.account || (!t.account.id && !(t.account.new && t.account.new.identity))) throw new Error('setTarget: kind and an account (id, or new with an identity) are required');
    _target = { kind: String(t.kind), account: JSON.parse(JSON.stringify(t.account)), identity: t.identity ? String(t.identity) : (t.account.new ? String(t.account.new.identity) : '') };
  }

  // For the smoke and for ticket 14: what this module holds, non-secret.
  function state() {
    return { saved: !!R.result, saving: R.saving, perms: R.perms ? { ok: R.perms.ok, pending: R.perms.pending, desk: R.perms.desk.status, site: R.perms.site.status } : null, outcome: R.result ? _outcome().kind : null };
  }

  window.DeskV1ConnectSummaryStep = { setTarget, registerBranch, state };
})();

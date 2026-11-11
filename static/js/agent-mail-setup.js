// ── Agent mail SETUP step (MC-1166 unit 7e-2) ─────────────────────────────────
// One component, two homes:
//   first-run  — a step in the first-run wizard, listed there through ONE hook
//                (window.AgentMailSetupStep, read by first-run.js SETUP_STEPS);
//   settings   — later enrollment, mounted by agent-mail-actions.js into the
//                Controls area whenever nothing is enrolled yet.
//
// DECLINE IS FREE. "No thanks" sends nothing: no address, no request, no config
// or ping change, and nothing is written to storage. The only request this step
// ever makes is Continue, which is the passcode-gated `start` POST to this
// computer's own server (agent-mail-actions.js `act`), and only after the human
// typed an address and pressed Continue.
//
// `start` needs a root-signed service manifest. Nothing in this install produces
// or fetches one, so Continue reads window.AgentMailManifestSource.get() if a
// later unit provides it, and until then says plainly that setup cannot begin.
// No signing path lives here.
//
// The step only appears in the first-run wizard when the enrollment route answers
// "not enrolled" (it 404s while agent_mail_enrollment_enabled is off). Escaped
// text only; the typed address is read from the field, never from any answer.
// Window-bridged, no `import`.
(function () {
  const URL_PATH = '/api/agent-mail/enrollment';
  const M = () => window.AgentMailActionsModel;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // phase: intro | declined | form | started.  `message` is first-run only (in Settings
  // the Controls banner already says what the last request answered).
  const S = { phase: 'intro', owner: '', error: '', message: null, working: false };
  let ctx = { context: 'settings', host: null, onDecline: null };
  let enrollable = false;   // first-run gate, set by reset()'s status read

  const sourceReady = () => !!(window.AgentMailManifestSource && typeof window.AgentMailManifestSource.get === 'function');

  function render() {
    const host = ctx.host;
    if (!host || !host.isConnected) return;
    const settings = ctx.context === 'settings';
    // first-run owns the one accent button per step (Next); only Settings gets a primary here
    const prim = settings ? ' am-primary' : '';
    let inner;
    if (S.phase === 'declined') {
      inner = `<div class="am-setup-lead" data-am-setup="declined">Agent mail stays off. Nothing was sent and nothing was changed.</div>
        <div class="am-setup-actions"><button type="button" class="am-act-btn" data-am-setup="begin">Set up agent mail</button></div>`;
    } else if (S.phase === 'started') {
      inner = `<div class="am-setup-lead" data-am-setup="started">Setup started. You can finish it now or later in Settings, Connectivity, Agent mail: it picks up where it stopped, including after a restart.</div>`;
    } else if (S.phase === 'form') {
      const blocked = !sourceReady();
      inner = `<div class="am-setup-field">
          <label class="am-label" for="am-setup-owner">Owner mailbox</label>
          <input id="am-setup-owner" type="email" class="am-input" data-am-setup-input="owner" value="${esc(S.owner)}"
            autocomplete="off" autocapitalize="none" spellcheck="false" maxlength="254"${S.working ? ' disabled' : ''}>
          <div class="am-act-hint">Only this address can approve what your agents do by mail. Use one you control.
            It is sent to the Clayrune mail service when you press Continue, and not before.</div>
        </div>
        ${blocked ? `<div class="am-setup-blocked" role="status" data-am-setup="blocked">Setup cannot begin yet: this install has no way
          to get the signed service manifest that starting needs. Nothing has been sent.</div>` : ''}
        ${S.error ? `<div class="am-setup-error" role="alert" data-am-setup="error">${esc(S.error)}</div>` : ''}
        ${S.message ? `<div class="am-outcome" role="status" data-am-setup="message" data-tone="${esc(S.message.tone)}">${esc(S.message.text)}</div>` : ''}
        <div class="am-setup-actions">
          <button type="button" class="am-act-btn${prim}" data-am-setup="continue"${blocked || S.working ? ' disabled' : ''}>${S.working ? 'Working…' : 'Continue'}</button>
          <button type="button" class="am-act-btn" data-am-setup="back"${S.working ? ' disabled' : ''}>Back</button>
        </div>`;
    } else {
      inner = `<div class="am-setup-lead">Give your agents an inbox they can send mail from and receive replies in. Replies are
          only ever data: nothing in a reply can approve anything. You can turn it off at any time.</div>
        <ul class="am-setup-facts">
          <li>Off until you finish setup here and turn it on.</li>
          <li>Continuing sends the owner address you enter to the Clayrune mail service, which reserves an inbox and asks you to prove you own that address with a code.</li>
          <li><b>Declining sends nothing:</b> no address, no check-in, no change to any setting.</li>
        </ul>
        <div class="am-setup-actions">
          <button type="button" class="am-act-btn${prim}" data-am-setup="begin">Set up agent mail</button>
          <button type="button" class="am-act-btn" data-am-setup="decline">No thanks</button>
        </div>`;
    }
    host.innerHTML = `<div class="am-setup" data-am-setup-phase="${esc(S.phase)}" data-am-setup-context="${esc(ctx.context)}">
        <div class="am-setup-title">${settings ? 'Set up agent mail' : 'Agent mail'} <span class="am-optional">optional</span></div>
        ${inner}</div>`;
  }

  async function onContinue() {
    if (S.working) return;
    const input = ctx.host && ctx.host.querySelector('[data-am-setup-input="owner"]');
    const owner = ((input && input.value) || S.owner || '').trim().toLowerCase();
    S.owner = owner; S.error = ''; S.message = null;
    if (!M().validOwner(owner)) { S.error = 'Enter a valid email address.'; render(); return; }
    let signed = null;
    try { signed = sourceReady() ? await window.AgentMailManifestSource.get() : null; } catch (_) { signed = null; }
    if (!signed || !Array.isArray(signed.manifest) || typeof signed.signature !== 'string') {
      S.error = 'The signed service manifest could not be obtained, so nothing was sent.'; render(); return;
    }
    S.working = true; render();
    const r = await window.AgentMailActions.act('start', { owner, manifest: signed.manifest, signature: signed.signature });
    S.working = false;
    if (r && r.ok) { S.phase = ctx.context === 'settings' ? 'intro' : 'started'; S.owner = ''; S.error = ''; S.message = null; }
    else if (r === null) { S.message = null; }
    else { S.message = ctx.context === 'first-run' ? M().outcome('start', r) : null; }
    render();
  }

  function onClick(ev) {
    const b = ev.target && ev.target.closest && ev.target.closest('[data-am-setup]');
    if (!b || !ctx.host || !ctx.host.contains(b) || b.disabled) return;
    const what = b.getAttribute('data-am-setup');
    if (what === 'begin') { S.phase = 'form'; S.error = ''; S.message = null; render(); const i = ctx.host.querySelector('input'); if (i) i.focus(); }
    else if (what === 'back') { S.phase = 'intro'; S.error = ''; S.message = null; render(); }
    else if (what === 'decline') {
      S.phase = 'declined'; S.owner = ''; S.error = ''; S.message = null;
      if (typeof ctx.onDecline === 'function') ctx.onDecline(); else render();
    } else if (what === 'continue') onContinue();
  }

  function onInput(ev) {
    const t = ev.target;
    if (t && t.getAttribute && t.getAttribute('data-am-setup-input') === 'owner') S.owner = t.value;
  }

  // Delegated once on document: hosts are re-created by every repaint.
  document.addEventListener('click', onClick);
  document.addEventListener('input', onInput);

  function mount(host, opts) {
    ctx = { context: (opts && opts.context) || 'settings', host, onDecline: (opts && opts.onDecline) || null };
    render();
  }

  // ── first-run wizard registration ──────────────────────────────────────────
  // reset() runs when the wizard starts: forget a previous run and read the status
  // once. The step is skipped unless the server says nothing is enrolled yet
  // (a 404 while the feature is off, a 503, an unreadable answer: all skipped).
  async function reset() {
    S.phase = 'intro'; S.owner = ''; S.error = ''; S.message = null; S.working = false;
    enrollable = false;
    try {
      const base = typeof API_BASE === 'string' ? API_BASE : '';
      const res = await fetch(base + URL_PATH, { method: 'GET', cache: 'no-store' });
      const body = res.status === 200 ? await res.json().catch(() => null) : null;
      const p = M().plan({ status: res.status, body });
      enrollable = p.controls.includes('setup');
    } catch (_) { enrollable = false; }
  }

  window.AgentMailSetup = { mount };
  window.AgentMailSetupStep = {            // interop: first-run.js SETUP_STEPS + startFirstRun
    id: 'agent-mail',
    title: 'Agent mail (optional)',
    wide: true,
    body: () => '<div id="agent-mail-setup-step-host"></div>',
    onEnter: () => mount(document.getElementById('agent-mail-setup-step-host'), {
      context: 'first-run', onDecline: () => { if (typeof window.setupNext === 'function') window.setupNext(); } }),
    skip: () => !enrollable,
    reset,
  };
})();

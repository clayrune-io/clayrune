// ── Settings > Connectivity > Agent mail: the MUTATING controls (MC-1166 unit 7e-2) ──
// Sits under the 7e-1 status card as its own host (#agent-mail-controls-host), so
// the status card itself stays read-only. It listens for the `agent-mail-status`
// event agent-mail-status.js dispatches after every paint, and draws only the
// controls that status allows (static/js/agent-mail-actions-model.js `plan`).
//
// Every mutation goes through window.humanProofFetch (human-proof-modal.js): the
// dashboard passcode is retyped for each call, merged into the body there, and
// never read, kept or logged here. The server enforces it (agent_mail_enrollment_
// routes.py, _require_human_passcode); this file never decides who is human.
// After every completed request, success or refusal, the 7e-1 status is read
// again. Disable is in every enrolled list and is also offered when the status
// cannot be read, so the kill switch never depends on a read working.
//
// `start` (and the owner / key / repin identity changes) need a root-signed
// service manifest; nothing in this install produces one, so the setup step
// (agent-mail-setup.js) reads it from window.AgentMailManifestSource if some
// future unit provides one, and says plainly that setup cannot begin otherwise.
//
// All text is escaped. The only values drawn are validated booleans, integers,
// closed codes, and the review disclosure (validated field by field).
// Window-bridged, no `import`.
(function () {
  const BASE = '/api/agent-mail/enrollment';
  const M = () => window.AgentMailActionsModel;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const api = () => (typeof API_BASE === 'string' ? API_BASE : '');

  let last = null;        // the last status answer { status, body }
  let working = '';       // action in flight, '' when idle
  let outcome = null;     // { tone, text, blockers, code, action } of the last finished request
  let review = null;      // reviewView() result while the human is reading it

  // One passcode-gated POST. Resolves { ok, status, body }, or null when the human
  // closed the prompt (nothing was sent). Never rejects.
  async function run(action, body) {
    const a = M().ACTIONS[action];
    if (typeof window.humanProofFetch !== 'function' || !a) return { ok: false, status: 0, body: { error: 'prompt_unavailable' } };
    try {
      return await window.humanProofFetch(api() + BASE + '/' + action,
        { method: 'POST', body: JSON.stringify(body || {}) },
        { title: a.title, description: a.ask, busyText: 'Working…' });
    } catch (_) {
      return { ok: false, status: 0, body: {} };
    }
  }

  // A control's request, then the status read again. Used by this panel and the setup step.
  async function act(action, body) {
    if (working) return null;
    working = action; outcome = null; paint();
    const r = await run(action, body);
    working = '';
    outcome = Object.assign(M().outcome(action, r), { action });
    if (r && r.ok && action === 'review') {
      review = M().reviewView(r.body);
      if (!review) outcome = { tone: 'bad', action, code: 'review_shape', blockers: [],
        text: 'The review answer was not in the expected shape, so nothing can be confirmed from it.' };
    } else if (r && r.ok && (action === 'confirm' || action === 'disable')) {
      review = null;
    }
    if (r !== null) { try { await window.refreshAgentMailStatus(); } catch (_) { /* paint below */ } }
    paint();
    return r;
  }

  function hostEl() {
    const status = document.getElementById('agent-mail-status-host');
    if (!status) return null;
    let host = document.getElementById('agent-mail-controls-host');
    if (!host || host.previousElementSibling !== status) {
      if (host) host.remove();
      host = document.createElement('div');
      if (!working) { outcome = null; review = null; }   // a fresh Settings screen starts without the last banner or a stale review
      host.id = 'agent-mail-controls-host';
      host.className = 'am-actions-host';
      status.insertAdjacentElement('afterend', host);
    }
    return host;
  }

  const btn = (action, extra) => {
    const a = M().ACTIONS[action];
    const busy = working === action;
    return `<button type="button" class="am-act-btn${extra || ''}" data-am-action="${esc(action)}"${working ? ' disabled' : ''}>`
      + `${esc(busy ? 'Working…' : a.label)}</button>`;
  };
  const block = (key, title, hint, inner) => `
    <div class="am-act" data-am-control="${esc(key)}">
      <div class="am-act-title">${esc(title)}</div>
      ${hint ? `<div class="am-act-hint">${esc(hint)}</div>` : ''}
      <div class="am-act-body">${inner}</div>
    </div>`;

  function controlHTML(c) {
    if (c === 'resume') return block(c, 'Reserve the inbox',
      'Asks the mail service to reserve an inbox for this install. Safe to repeat after a restart or a failure.', btn('resume', ' am-primary'));
    if (c === 'review') {
      if (review) {
        const rows = review.rows.map(([k, v]) => `<div class="am-rv-row"><div class="am-label">${esc(k)}</div>`
          + `<div class="am-value am-mono">${esc(v)}</div></div>`).join('');
        return block(c, 'Check these details',
          'Only confirm if the owner mailbox and the service are the ones you expect. Confirming does not turn mail on.',
          `<div class="am-review" data-am="review">${rows}</div>${btn('confirm', ' am-primary')}`);
      }
      return block(c, 'Review the details', 'Shows the owner mailbox, the mail service and the keys involved, for you to confirm.',
        btn('review', ' am-primary'));
    }
    if (c === 'verify') {
      return block(c, 'Enter the verification code',
        'The mail service sent a code to the owner mailbox. Type it here. It is sent once and not kept or shown again.',
        `<div class="am-code-row"><input type="text" class="am-input am-mono" data-am-input="code" maxlength="256" size="24"`
        + ` autocomplete="off" autocapitalize="none" autocorrect="off" spellcheck="false" aria-label="Verification code"`
        + `${working ? ' disabled' : ''}>${btn('verify', ' am-primary')}</div>`);
    }
    if (c === 'proof') return block(c, 'Delivery proof',
      'Starts the check that the mail service can deliver to this computer. It must be answered before mail can be enabled.', btn('proof'));
    if (c === 'enable') return block(c, 'Turn agent mail on',
      'Needs the checklist above to be ready and no unattended agent session to be running.', btn('enable', ' am-primary'));
    if (c === 'retry-revocations') return block(c, 'Revocation waiting',
      'The mail service has not yet confirmed a revocation. Retry asks it again.', btn('retry-revocations'));
    if (c === 'disable') return block(c, 'Turn agent mail off',
      'Switches mail off on this computer and asks the service to revoke it. Always available, even while agent sessions run.',
      btn('disable', ' am-danger'));
    if (c === 'setup') return '<div id="agent-mail-setup-slot" data-am-control="setup"></div>';
    return '';
  }

  function outcomeHTML() {
    if (!outcome) return '';
    const list = outcome.blockers && outcome.blockers.length
      ? `<ul class="am-blockers" data-am="blockers">${outcome.blockers.map((b) =>
        `<li>${esc(b.session)} <span class="am-trigger">(${esc(b.trigger)})</span></li>`).join('')}</ul>` : '';
    return `<div class="am-outcome" role="status" data-am="outcome" data-tone="${esc(outcome.tone)}" data-code="${esc(outcome.code)}"`
      + ` data-action="${esc(outcome.action)}">${esc(outcome.text)}${list}</div>`;
  }

  function paint() {
    const host = hostEl();
    if (!host) return;
    const p = M().plan(last);
    if (p.state === 'off' || !last) { host.innerHTML = ''; return; }
    if (!p.controls.includes('review')) review = null;
    host.innerHTML = `<div class="am-actions" id="agent-mail-actions" data-am-actions-state="${esc(p.state)}">`
      + `<div class="am-actions-title">Controls</div>`
      + (p.state === 'unavailable' || p.state === 'error'
        ? '<div class="am-act-hint">The status could not be read, so only the off switch is offered.</div>' : '')
      + outcomeHTML() + p.controls.map(controlHTML).join('') + '</div>';
    const slot = host.querySelector('#agent-mail-setup-slot');
    if (slot && window.AgentMailSetup) window.AgentMailSetup.mount(slot, { context: 'settings' });
    else if (slot) slot.innerHTML = '<div class="am-act-hint">Setup is not available.</div>';
  }

  document.addEventListener('click', (ev) => {
    const b = ev.target && ev.target.closest && ev.target.closest('#agent-mail-controls-host [data-am-action]');
    if (!b || b.disabled) return;
    const action = b.getAttribute('data-am-action');
    if (action === 'verify') {
      const input = document.querySelector('#agent-mail-controls-host [data-am-input="code"]');
      const code = input ? input.value.trim() : '';
      if (!M().validCode(code)) {
        outcome = { tone: 'bad', action, code: 'invalid_code', blockers: [], text: M().refusal('invalid_code') };
        paint();
        return;
      }
      if (input) input.value = '';   // read once, forget; the prompt below asks for the passcode
      act('verify', { code });
    } else if (action === 'confirm') {
      if (review) act('confirm', { confirmation: review.raw });
    } else {
      act(action, {});
    }
  });

  document.addEventListener('agent-mail-status', (ev) => {
    last = ev && ev.detail ? ev.detail : null;
    paint();
  });

  window.AgentMailActions = { run, act };   // interop: agent-mail-setup.js
})();

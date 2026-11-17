// ── Settings > Connectivity > Agent mail: held messages (Release / Reject) ──────
// A held reply keeps every later reply in the same chat queued behind it, so a hold
// needs a way out that is not a script. This lists the holds and offers the two
// review actions. It sits under the status card and the controls (its own host,
// #agent-mail-holds-host) and re-reads on every `agent-mail-status` event.
//
// METADATA ONLY. GET /api/agent-mail/holds returns closed fields (ids, a reason code,
// times, a size, how many replies are queued behind); no reply text exists in the
// answer, so none can be drawn. Every value is escaped, the reason is looked up in a
// closed table, and anything unknown is shown as its code with underscores removed.
//
// Both actions go through window.humanProofFetch (human-proof-modal.js): the dashboard
// passcode is retyped for each call and merged into the body there, never read, kept
// or logged here. The server enforces it (agent_mail_holds_routes.py); this file never
// decides who is human. Release hands the message back to the pipeline, which checks
// it again before anything reaches an agent. Reject drops it for good.
// Window-bridged, no `import`.
(function () {
  const BASE = '/api/agent-mail/holds';
  const CODE = /^[a-z0-9_]{1,48}$/;
  const ID = /^[A-Za-z0-9_.:-]{1,128}$/;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const api = () => (typeof API_BASE === 'string' ? API_BASE : '');
  const int = (v) => (Number.isInteger(v) && v >= 0 ? v : 0);

  // Why a message is held, in words. Keys are the closed reason codes the store and
  // the delivery checks use; an unlisted code falls back to the code itself.
  const REASON = {
    conversation_identity_changed: 'The chat is now running as a different conversation than the one this reply was addressed to (usually after a context rollover), so it was not delivered.',
    persona_changed: 'The agent persona in this chat changed since the reply was addressed to it.',
    execution_context_changed: 'The folder, permissions or settings this chat runs with changed since the reply was addressed to it.',
    project_identity_changed: 'The project this chat belongs to changed.',
    project_generation_changed: 'The project was reset or recreated after this reply was addressed to it.',
    session_closed: 'The chat this reply was addressed to has been closed.',
    session_unavailable: 'The chat this reply was addressed to is not available.',
    native_history_missing: 'The chat history needed to continue the conversation could not be found.',
    rollover_required: 'The chat has grown too large and has to roll over before it can take another message. Open the chat once and send it a message, then release this reply.',
    provider_quota_held: 'The agent engine is out of allowance right now.',
    duplicate_conversation_owner: 'Two chats claim the same conversation, so it is unclear which should get the reply.',
    recovery_held: 'The chat is recovering from an earlier failure.',
    provider_recovery_held: 'The agent engine is recovering from an earlier failure.',
    worktree_unavailable: 'The working copy this chat uses is not available.',
    thread_unavailable: 'The mail thread this reply belongs to is no longer available.',
    gemini_mail_wake_unsupported: 'This agent engine cannot be continued by mail.',
    incognito_not_persistent: 'The chat is incognito, so mail cannot continue it.',
    injection_flagged: 'The reply looked like it was trying to give the agent instructions, so it was stopped for you to read.',
    retry_exhausted: 'Delivery was tried several times and kept failing.',
    reply_oversize: 'The reply was too long to hand to an agent.',
    oversize_body: 'The reply was too long to keep. Its text is gone, so it can only be rejected.',
    conflicting_message_id: 'Two different messages arrived under the same message ID.',
    missing_message_id: 'The reply had no message ID.',
  };
  const reasonText = (c) => REASON[c] || (window.AgentMailStatusModel && window.AgentMailStatusModel.why
    ? window.AgentMailStatusModel.why(c) : String(c || '').replace(/_/g, ' ')) || 'unknown reason';

  const ACTIONS = {
    release: { label: 'Release', title: 'Release the held message',
      ask: 'Re-enter your dashboard passcode to release this message. It is checked again before it reaches the agent.' },
    reject: { label: 'Reject', title: 'Reject the held message',
      ask: 'Re-enter your dashboard passcode to reject this message. It is dropped for good and its text is deleted.' },
  };
  const REFUSAL = {
    not_held: 'That message is no longer held, so nothing was changed.',
    unknown_event: 'That message no longer exists.',
    nothing_to_release: 'The text of this message was not kept, so it cannot be released. Reject it instead.',
    human_required: 'A human must do this from the dashboard; it is not available to an agent session.',
    storage_unavailable: 'The mail store is unavailable. Nothing was changed.',
    passcode_required: 'Set a dashboard passcode in Settings > Connectivity first.',
  };

  let holds = [];
  let working = '';     // "<action>:<event id>" in flight
  let outcome = null;   // { tone, text }

  function when(sec) {
    return typeof sec === 'number' && sec > 0
      ? new Date(sec * 1000).toISOString().replace('T', ' ').slice(0, 16) + ' UTC' : '';
  }

  function view(raw) {
    if (!raw || !Array.isArray(raw.holds)) return [];
    return raw.holds.filter((h) => h && typeof h.event_id === 'string' && ID.test(h.event_id)).slice(0, 50).map((h) => ({
      id: h.event_id,
      reason: typeof h.reason === 'string' && CODE.test(h.reason) ? h.reason : '',
      chat: typeof h.session_id === 'string' && ID.test(h.session_id) ? h.session_id : '',
      project: typeof h.project_id === 'string' && ID.test(h.project_id) ? h.project_id : '',
      at: when(h.state_at), received: when(h.received_at),
      size: int(h.body_len), behind: int(h.queued_behind),
    }));
  }

  function hostEl() {
    const status = document.getElementById('agent-mail-status-host');
    if (!status) return null;
    let host = document.getElementById('agent-mail-holds-host');
    if (!host || !host.isConnected) {
      host = document.createElement('div');
      host.id = 'agent-mail-holds-host';
      host.className = 'am-holds-host';
      if (!working) outcome = null;     // a fresh Settings screen starts without the last banner
      const controls = document.getElementById('agent-mail-controls-host');
      (controls || status).insertAdjacentElement('afterend', host);
    }
    return host;
  }

  function paint() {
    const host = hostEl();
    if (!host) return;
    if (!holds.length && !outcome) { host.innerHTML = ''; return; }
    const items = holds.map((h) => {
      const busy = (a) => working === a + ':' + h.id;
      const btn = (a, extra) => `<button type="button" class="am-act-btn${extra}" data-am-hold-action="${a}"`
        + ` data-am-hold-id="${esc(h.id)}"${working ? ' disabled' : ''}>${esc(busy(a) ? 'Working…' : ACTIONS[a].label)}</button>`;
      return `<div class="am-hold" data-am-hold="${esc(h.id)}" data-reason="${esc(h.reason)}">
        <div class="am-act-title" data-am="hold-reason">${esc(reasonText(h.reason))}</div>
        <div class="am-act-hint">${esc([h.project && 'Project ' + h.project, h.chat && 'chat ' + h.chat,
          h.at && 'held ' + h.at, h.size && h.size + ' bytes'].filter(Boolean).join(' · '))}</div>
        <div class="am-act-hint" data-am="hold-behind">${h.behind
          ? esc(h.behind + (h.behind === 1 ? ' later reply is' : ' later replies are') + ' waiting behind it in this chat.')
          : 'Nothing else is waiting behind it.'}</div>
        <div class="am-hold-btns">${btn('release', ' am-primary')}${btn('reject', ' am-danger')}</div>
      </div>`;
    }).join('');
    const out = outcome ? `<div class="am-outcome" role="status" data-am="hold-outcome" data-tone="${esc(outcome.tone)}">${esc(outcome.text)}</div>` : '';
    host.innerHTML = `<div class="am-actions" id="agent-mail-holds">
      <div class="am-actions-title">Held messages</div>
      <div class="am-act-hint">A held reply stays out of the agent&#39;s chat, and so does every reply behind it, until you release or reject it.</div>
      ${out}${items}</div>`;
  }

  async function load() {
    try {
      const res = await fetch(api() + BASE, { method: 'GET', cache: 'no-store' });
      holds = res.ok ? view(await res.json().catch(() => null)) : [];
    } catch (_) {
      holds = [];
    }
    paint();
  }

  async function act(action, id) {
    if (working || !ACTIONS[action] || !ID.test(id)) return;
    if (typeof window.humanProofFetch !== 'function') return;
    working = action + ':' + id; outcome = null; paint();
    let r = null;
    try {
      r = await window.humanProofFetch(api() + BASE + '/' + encodeURIComponent(id) + '/' + action,
        { method: 'POST', body: JSON.stringify({}) },
        { title: ACTIONS[action].title, description: ACTIONS[action].ask, busyText: 'Working…' });
    } catch (_) { r = { ok: false, status: 0, body: {} }; }
    working = '';
    if (r === null) { paint(); return; }          // the human closed the prompt; nothing was sent
    const err = r.body && typeof r.body.error === 'string' && CODE.test(r.body.error) ? r.body.error : '';
    outcome = r.ok
      ? { tone: 'good', text: action === 'release'
        ? 'Released. It will be checked again before it reaches the agent.' : 'Rejected. The message was dropped and its text deleted.' }
      : { tone: 'bad', text: REFUSAL[err] || (r.status === 403 ? 'The passcode was not accepted, so nothing was changed.'
        : 'That did not go through, so nothing was changed.') };
    await load();
    try { if (typeof window.refreshAgentMailStatus === 'function') await window.refreshAgentMailStatus(); } catch (_) { /* cosmetic */ }
  }

  document.addEventListener('click', (ev) => {
    const b = ev.target && ev.target.closest && ev.target.closest('#agent-mail-holds-host [data-am-hold-action]');
    if (!b || b.disabled) return;
    act(b.getAttribute('data-am-hold-action'), b.getAttribute('data-am-hold-id') || '');
  });
  document.addEventListener('agent-mail-status', () => { load(); });

  window.AgentMailHolds = { view, reasonText, act, load };
})();

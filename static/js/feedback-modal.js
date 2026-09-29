// ── Send feedback modal (MC-904, backlog f638e8d9) ──────────────────────────
//
// Sidebar footer + mobile drawer footer entry point. Follows the
// numbered-inputs modal convention (feedback-modal-multi-input-flow memory):
// number top-down, only the final action gets the btn-add accent. Modeled on
// human-proof-modal.js's lightweight modal-window/modal-content pattern —
// this doesn't need the full project-chat modal-manager machinery.
//
// PRIVACY CONSTRAINT (backlog item (c), non-negotiable): the version/OS line
// is prefilled into the textarea as VISIBLE, EDITABLE text the user can
// delete — never appended silently. The payload this modal sends is exactly
// what the textarea and email field show; there is no hidden field beyond
// the honeypot (which is never rendered for a human to see).

const FEEDBACK_MODAL_ID = '__feedback';

function _fbPrefillLine(ctx) {
  const version = (ctx && ctx.version) || 'unknown';
  const os = (ctx && ctx.os) || 'unknown OS';
  return `Clayrune ${version} on ${os}`;
}

async function openFeedbackModal() {
  let ctx = { version: 'unknown', os: 'unknown OS' };
  try {
    const r = await fetch(API_BASE + '/api/feedback/context');
    if (r.ok) ctx = await r.json();
  } catch (_) {
    // Prefill is best-effort — an unreachable context endpoint shouldn't
    // block the form; the line is still editable either way.
  }

  let win = document.querySelector(`[data-modal-id="${FEEDBACK_MODAL_ID}"]`);
  if (!win) {
    win = document.createElement('div');
    win.className = 'modal-window';
    win.dataset.modalId = FEEDBACK_MODAL_ID;
    const content = document.createElement('div');
    content.className = 'modal-content';
    _clampModalSize(content, 460);
    win.appendChild(content);
    document.getElementById('modal-layer').appendChild(win);
    const z = nextModalZ++;
    win.style.zIndex = z;
    openModals.set(FEEDBACK_MODAL_ID, { projectId: null, element: win, minimized: false, zIndex: z });
    centerModalElement(win);
  }

  const content = win.querySelector('.modal-content');
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">Send feedback</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-close" onclick="closeModalById('${FEEDBACK_MODAL_ID}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div style="padding:4px 24px 20px 28px;display:flex;flex-direction:column;gap:14px">
      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">1. Message — a bug, a question, anything. Nothing leaves this machine except what's in this box.</label>
        <textarea id="fb-message" rows="7"
          style="width:100%;padding:9px 10px;font-size:13px;background:var(--surface2);
                 border:1px solid var(--border);border-radius:4px;color:var(--text);
                 font-family:inherit;resize:vertical">${esc(_fbPrefillLine(ctx))}\n\n</textarea>
      </div>
      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">2. Reply-to email (optional)</label>
        <input type="email" id="fb-reply-to" placeholder="you@example.com"
          style="width:100%;padding:7px 10px;font-size:13px;background:var(--surface2);
                 border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:inherit">
      </div>
      <!-- Honeypot: never shown to a human. A real browser leaves this empty. -->
      <div style="position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden" aria-hidden="true">
        <label for="fb-topic">Topic</label>
        <input type="text" id="fb-topic" name="fb-topic" tabindex="-1" autocomplete="off">
      </div>
      <div id="fb-status" style="font-size:11px;line-height:1.5"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn-secondary" onclick="closeModalById('${FEEDBACK_MODAL_ID}')">Cancel</button>
        <button class="btn-add" id="fb-send-btn" onclick="_fbSend()">Send</button>
      </div>
    </div>`;
  focusModal(FEEDBACK_MODAL_ID);
  const ta = document.getElementById('fb-message');
  if (ta) { ta.focus(); ta.selectionStart = ta.selectionEnd = ta.value.length; }
}
window.openFeedbackModal = openFeedbackModal;

function _fbErrorText(err) {
  const map = {
    empty_message: 'Write a message first.',
    message_too_long: 'That message is too long — please trim it.',
    email_too_long: 'That email address is too long.',
    too_many_attempts: 'Too many messages sent recently — try again in a bit.',
    mailer_not_configured: 'Feedback sending is not set up on this install yet.',
    vault_locked: 'The secrets vault is locked — unlock it and try again.',
    send_failed: 'Could not send — try again in a moment.',
  };
  return map[err] || 'Could not send feedback.';
}

async function _fbSend() {
  const btn = document.getElementById('fb-send-btn');
  const status = document.getElementById('fb-status');
  const message = (document.getElementById('fb-message') || {}).value || '';
  const replyTo = (document.getElementById('fb-reply-to') || {}).value || '';
  const hpTopic = (document.getElementById('fb-topic') || {}).value || '';

  if (!message.trim()) {
    if (status) { status.textContent = _fbErrorText('empty_message'); status.style.color = 'var(--danger,#c94a3a)'; }
    return;
  }

  if (btn) btn.disabled = true;
  if (status) { status.textContent = 'Sending…'; status.style.color = 'var(--text-faint)'; }

  let resp, body;
  try {
    resp = await fetch(API_BASE + '/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, reply_to: replyTo, hp_topic: hpTopic }),
    });
    body = await resp.json().catch(() => ({}));
  } catch (e) {
    if (status) { status.textContent = 'Could not reach the server: ' + e.message; status.style.color = 'var(--danger,#c94a3a)'; }
    if (btn) btn.disabled = false;
    return;
  }

  if (resp.ok && body.ok) {
    if (status) { status.textContent = 'Sent. Thank you.'; status.style.color = 'var(--green,#3a9a5c)'; }
    setTimeout(() => closeModalById(FEEDBACK_MODAL_ID), 1200);
    return;
  }

  if (status) { status.textContent = _fbErrorText(body.error); status.style.color = 'var(--danger,#c94a3a)'; }
  if (btn) btn.disabled = false;
}
window._fbSend = _fbSend;

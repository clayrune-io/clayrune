// ── Send feedback modal (MC-904, backlog f638e8d9) ──────────────────────────
//
// Sidebar footer + mobile drawer footer entry point. Modeled on
// human-proof-modal.js's lightweight modal-window/modal-content pattern —
// this doesn't need the full project-chat modal-manager machinery.
//
// REWORK (2026-09-29, Ron): mailto now, a Cloudflare Worker send-in-app path
// later. There is no credential that ships with — or can be assumed present
// on — a stranger's install that would let their machine send mail; see
// position `position_howastrangerinstalldeliversinappsendfeedbacktohe`. So
// this modal makes ZERO server call to send: it builds a mailto: link from
// exactly the textarea text and hands it to the OS mail client. The user's
// own From address IS the reply-to in a mailto, so there is no reply-to
// field here.
//
// PRIVACY CONSTRAINT (backlog item (c), still binding): the version/OS line
// is prefilled into the textarea as VISIBLE, EDITABLE text the user can
// delete — never appended silently. The mailto body is exactly the textarea
// text, nothing appended.

const FEEDBACK_MODAL_ID = '__feedback';
const FEEDBACK_TO = 'hello@clayrune.io';
const FEEDBACK_SUBJECT = 'Clayrune feedback';
// mailto: links with an over-long encoded URL silently fail to open in some
// mail clients/OSes instead of erroring — stay well under common ~2000-char
// URL limits rather than find that out from a bug report.
const FEEDBACK_MAILTO_MAX = 1800;

function _fbPrefillLine(ctx) {
  const version = (ctx && ctx.version) || 'unknown';
  const os = (ctx && ctx.os) || 'unknown OS';
  return `Clayrune ${version} on ${os}`;
}

function _fbMailtoHref(subject, body) {
  const params = body !== null ? `subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`
                                : `subject=${encodeURIComponent(subject)}`;
  return `mailto:${FEEDBACK_TO}?${params}`;
}

function _fbOpenMailto(href) {
  window.location.href = href;
}

function _fbShowCopyButton() {
  const btn = document.getElementById('fb-copy-btn');
  if (btn) btn.style.display = '';
}

async function _fbCopy() {
  const status = document.getElementById('fb-status');
  const message = (document.getElementById('fb-message') || {}).value || '';
  try {
    await navigator.clipboard.writeText(message);
    if (status) { status.textContent = 'Copied to clipboard.'; status.style.color = 'var(--green,#3a9a5c)'; }
  } catch (e) {
    if (status) { status.textContent = 'Could not copy — select the text and copy manually.'; status.style.color = 'var(--danger,#c94a3a)'; }
  }
}
window._fbCopy = _fbCopy;

function _fbSend() {
  const status = document.getElementById('fb-status');
  const message = (document.getElementById('fb-message') || {}).value || '';

  if (!message.trim()) {
    if (status) { status.textContent = 'Write a message first.'; status.style.color = 'var(--danger,#c94a3a)'; }
    return;
  }

  const fullHref = _fbMailtoHref(FEEDBACK_SUBJECT, message);
  if (fullHref.length > FEEDBACK_MAILTO_MAX) {
    _fbOpenMailto(_fbMailtoHref(FEEDBACK_SUBJECT, null));
    if (status) {
      status.textContent = "That message is too long for a mail link — your mail app opened with just the address and subject. Use Copy message and paste it into the email.";
      status.style.color = 'var(--text-faint)';
    }
    _fbShowCopyButton();
    return;
  }

  _fbOpenMailto(fullHref);
  if (status) {
    status.textContent = "Your mail app should open with your message. Nothing was sent by Clayrune.";
    status.style.color = 'var(--text-faint)';
  }
  _fbShowCopyButton();
}
window._fbSend = _fbSend;

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
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Message — a bug, a question, anything. Send opens your mail app addressed to ${FEEDBACK_TO}; nothing leaves this machine until you send that email.</label>
        <textarea id="fb-message" rows="9"
          style="width:100%;padding:9px 10px;font-size:13px;background:var(--surface2);
                 border:1px solid var(--border);border-radius:4px;color:var(--text);
                 font-family:inherit;resize:vertical">${esc(_fbPrefillLine(ctx))}\n\n</textarea>
      </div>
      <div id="fb-status" style="font-size:11px;line-height:1.5"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn-secondary" id="fb-copy-btn" onclick="_fbCopy()" style="display:none">Copy message</button>
        <button class="btn-secondary" onclick="closeModalById('${FEEDBACK_MODAL_ID}')">Cancel</button>
        <button class="btn-add" id="fb-send-btn" onclick="_fbSend()">Send</button>
      </div>
    </div>`;
  focusModal(FEEDBACK_MODAL_ID);
  const ta = document.getElementById('fb-message');
  if (ta) { ta.focus(); ta.selectionStart = ta.selectionEnd = ta.value.length; }
}
window.openFeedbackModal = openFeedbackModal;

// Enrollment code mail (MC-1166 7d-2e-2, contract section 2.4). `Enrollment.begin` returns the
// one-time code to its caller and says the caller must mail it to the stored owner only. This module
// is that caller's mailer: owner-only text mail in the shape of outbound.js `constructMail` (no
// client-chosen From/To/body, base64 body, Auto-Submitted, X-Clayrune-Agent-Mail), sent through the
// Worker's existing `send` seam.
//
// The code leaves the Worker only inside this mail. It is never logged, never returned over HTTP and
// never stored beyond the HMAC the core keeps. A failed or ambiguous send is NEVER retried (same rule
// as outbound.js): the flow is invalidated and the human starts a fresh one.
import { bytes, sha256 } from './signing.js';
import { parseAddrSpec } from './mailbox.js';

// Initial values; the 200/day global send budget in ledger.js is unrelated and untouched.
export const OWNER_MAILS_PER_DAY = 3;
export const GLOBAL_MAILS_PER_DAY = 200;
const SUBJECT = 'Clayrune agent mail confirmation code';
const b64 = (s) => btoa(String.fromCharCode(...bytes(s)));

export async function constructCodeMail({ owner, code, flowId, config }) {
  const to = parseAddrSpec(owner); const from = parseAddrSpec(config.from);
  if (!to || !from || from.domain !== config.domain || !/^[a-z0-9.-]+$/.test(config.domain)
      || !/^[a-f0-9]{32}$/.test(code)) throw new Error('invalid_service_address');
  const digest = await sha256(bytes(`${flowId}:enrollment-code`));
  return { from: config.from, to: owner,
    raw: [`From: ${config.from}`, `To: ${owner}`, `Message-ID: <${digest}@${config.domain}>`, `Subject: ${SUBJECT}`,
      'MIME-Version: 1.0', 'Content-Type: text/plain; charset=utf-8',
      'Content-Transfer-Encoding: base64', 'Auto-Submitted: auto-generated', 'X-Clayrune-Agent-Mail: 1', '',
      b64(['Your Clayrune agent mail confirmation code is:', '', code, '',
        'Enter it in Clayrune to finish connecting this mailbox. It expires in 15 minutes.',
        'If you did not start this, ignore this message.', ''].join('\r\n')).match(/.{1,76}/g).join('\r\n'), ''].join('\r\n') };
}

// -> { ok: true } | { ok: false, status, code }. On any failure the flow is invalidated (code wiped) so a
// retry begins a fresh flow. Budgets are charged before the send; the owner bucket first so one owner
// cannot spend the global allowance.
export async function sendEnrollmentCode({ ledger, flowId, owner, code, config, send, now }) {
  const invalidate = () => ledger.run(`UPDATE mail_enrollment_flows SET state='invalidated', code_digest=''
    WHERE flow_id=? AND state='pending'`, flowId);
  let mail;
  try { mail = await constructCodeMail({ owner, code, flowId, config }); }
  catch { await invalidate(); return { ok: false, status: 503, code: 'code_mail_failed' }; }
  if (!await ledger.limit(`enroll-mail:owner:${await sha256(bytes(owner))}`, OWNER_MAILS_PER_DAY, now)
      || !await ledger.limit('enroll-mail:global', GLOBAL_MAILS_PER_DAY, now)) {
    await invalidate();
    return { ok: false, status: 429, code: 'mail_limit' };
  }
  try { await send(mail); }
  catch {
    // A throw can follow provider acceptance. Never retry an ambiguous send.
    console.error('[agent-mail] enrollment_code_mail_failed');
    await invalidate();
    return { ok: false, status: 503, code: 'code_mail_failed' };
  }
  return { ok: true };
}

// ── Agent-mail actions: the pure model (MC-1166 unit 7e-2) ───────────────────
// No DOM, no fetch. Three jobs:
//   plan(result)            which controls the current status answer allows
//   outcome(action, r)      one human sentence (plus blockers) for what a POST answered
//   reviewView(raw)         the review disclosure, validated, or null
//
// `result` is the shape agent-mail-status.js already uses: { status, body } from
// ONE GET /api/agent-mail/enrollment. Only known keys are read, and only
// booleans / integers / closed codes come out, so a stray field in an answer
// (`reply_text`, `body`, `last_error`, ...) cannot reach a screen. Nothing here
// treats text from a reply as an instruction or an approval: the only inputs that
// start an action are button clicks and the passcode the human types.
// Window-bridged, no `import` (static/js modules do not share scope).
(function () {
  const CODE = /^[a-z0-9_]{1,48}$/;
  const SID = /^[A-Za-z0-9_.:-]{1,64}$/;
  const HEX64 = /^[a-f0-9]{64}$/;
  const OWNER = /^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,63}$/;
  const STAGES = ['not_enrolled', 'preparing', 'pending', 'delivery_pending', 'active', 'disabled'];
  const int = (v) => (Number.isInteger(v) && v >= 0 ? v : 0);
  const code = (v) => (typeof v === 'string' && CODE.test(v) ? v : '');
  const isObj = (v) => !!v && typeof v === 'object' && !Array.isArray(v);

  // What each action is called, and what the passcode prompt says it is for.
  const ACTIONS = {
    start: { label: 'Continue', title: 'Start agent mail setup',
      ask: 'Re-enter your dashboard passcode to start agent mail setup with this address.' },
    resume: { label: 'Resume setup', title: 'Resume agent mail setup',
      ask: 'Re-enter your dashboard passcode to resume agent mail setup.' },
    review: { label: 'Review details', title: 'Review agent mail details',
      ask: 'Re-enter your dashboard passcode to show the enrollment details for review.' },
    confirm: { label: 'Confirm these details', title: 'Confirm agent mail details',
      ask: 'Re-enter your dashboard passcode to confirm the reviewed details.' },
    verify: { label: 'Verify code', title: 'Verify the owner mailbox',
      ask: 'Re-enter your dashboard passcode to submit the verification code.' },
    proof: { label: 'Arm delivery proof', title: 'Arm delivery proof',
      ask: 'Re-enter your dashboard passcode to arm the delivery proof.' },
    enable: { label: 'Enable agent mail', title: 'Enable agent mail',
      ask: 'Re-enter your dashboard passcode to turn agent mail on.' },
    disable: { label: 'Disable agent mail', title: 'Disable agent mail',
      ask: 'Re-enter your dashboard passcode to turn agent mail off and revoke it.' },
    'retry-revocations': { label: 'Retry revocation', title: 'Retry revocation',
      ask: 'Re-enter your dashboard passcode to retry the pending revocation.' },
  };

  const SUCCESS = {
    start: 'Setup started. The next step is shown below.',
    resume: 'Resumed. The status above shows where enrollment stands.',
    review: 'Details loaded. Check them below, then confirm.',
    confirm: 'Confirmed. Now enter the code sent to the owner mailbox.',
    verify: 'Ownership verified. Mail stays off until you enable it.',
    proof: 'Delivery proof armed. It counts once the mail service answers.',
    enable: 'Agent mail is enabled.',
    disable: 'Agent mail is disabled on this computer. Any revocation the service has not confirmed is retried.',
    'retry-revocations': 'Retry finished. Anything still waiting is listed under Revocations above.',
  };

  // A 409 carries one closed refusal code. Plain-English text for each; anything
  // else that is a well-formed code is shown with underscores removed.
  const REFUSAL = {
    confirmation_mismatch: 'The details no longer match what was reviewed. Review them again.',
    confirmation_required: 'Review and confirm the details first.',
    delivery_proof_unavailable: 'Delivery proof is not available on this install.',
    disable_before_identity_change: 'An enrollment with other details already exists. Disable it first.',
    enrollment_disabled: 'Agent mail enrollment is switched off.',
    enrollment_required: 'There is no enrollment in progress.',
    generation_changed: 'The enrollment changed while this ran. Check the status and try again.',
    identity_material_invalid: 'The identity key for this enrollment is invalid. Disable it and start again.',
    identity_material_unavailable: 'The identity key for this enrollment is not available. Disable it and start again.',
    identity_mismatch: 'The identity key does not match this enrollment. Disable it and start again.',
    identity_unavailable_reenrollment_required: 'The identity key is gone. Disable it and start again.',
    invalid_code: 'That code is not in the expected format.',
    invalid_flow: 'The enrollment record is invalid. Disable it and start again.',
    invalid_install: 'The enrollment record is invalid. Disable it and start again.',
    invalid_owner: 'That address is not valid.',
    lifecycle_raced: 'Something else changed the enrollment at the same moment. Try again.',
    lifecycle_schema_corrupt: 'The enrollment record is damaged.',
    lifecycle_state_corrupt: 'The enrollment record is damaged.',
    lifecycle_state_missing: 'The enrollment record is missing. Contact support before starting again.',
    manifest_evidence_invalid: 'The saved service manifest failed verification.',
    manifest_rollback: 'That manifest is older than one this install already accepted.',
    manifest_stale: 'The service manifest expired or was replaced. Start again with a current one.',
    manifest_state_corrupt: 'The saved manifest state is damaged.',
    manifest_state_raced: 'Another change to the manifest state happened at the same moment. Try again.',
    manifest_signature: 'The service manifest signature did not verify.',
    manifest_time: 'The service manifest is not valid at this time.',
    manifest_shape: 'The service manifest is malformed.',
    manifest_fields: 'The service manifest has an invalid field.',
    manifest_encoding: 'The service manifest is not encoded correctly.',
    manifest_size: 'The service manifest is too large.',
    manifest_configuration: 'This install is not configured to verify a service manifest.',
    manifest_disabled: 'Service manifest checking is switched off.',
    manifest_retired_key: 'The service manifest lists its own key as retired.',
    ownership_expired: 'The ownership check ran out. Verify again with a new code.',
    ownership_required: 'Verify ownership of the mailbox first.',
    ownership_scope_mismatch: 'The mail service answered for a different inbox or owner.',
    pending_identity_changed: 'The mail service answered with a different inbox than this enrollment.',
    pending_scope_mismatch: 'The mail service answered with a different inbox than this enrollment.',
    prerequisites_pending: 'Something agent mail needs is not ready yet. See the checklist above.',
    registration_conflict: 'This install already has a registration for that inbox.',
    remote_begin_retryable: 'The mail service could not be reached to reserve the inbox. Try Resume again.',
    remote_cancel_retryable: 'The mail service did not confirm the cancel. It will be retried.',
    remote_revoke_retryable: 'The mail service did not confirm the revocation. It will be retried.',
    remote_cancel_scope_mismatch: 'The mail service answered the cancel for a different install.',
    remote_verify_retryable: 'The mail service did not accept the code or could not be reached. Check the code and try again.',
    revocation_pending: 'A revocation is still waiting for the mail service. Retry it first.',
    service_reservation_pending: 'The mail service has not reserved the inbox yet. Resume first.',
    vault_key_invalid: 'The vault key for the mail identity is not usable. Unlock the vault and try again.',
    vault_locked_or_unconfigured: 'The vault is locked or not set up, so the mail identity key cannot be used. Unlock the vault and try again.',
  };
  const refusal = (c) => {
    if (REFUSAL[c]) return REFUSAL[c];
    const why = window.AgentMailStatusModel && window.AgentMailStatusModel.why;
    const text = why ? why(c) : '';
    return text ? 'Refused: ' + text + '.' : (c ? 'Refused: ' + c.replace(/_/g, ' ') + '.' : 'The server refused this.');
  };

  // ── which controls does this status allow ────────────────────────────────
  // Returns { state, controls: [action, ...] }. `controls` holds action names, or
  // 'setup' (the setup step, for a not-yet-enrolled or finished-disabled install).
  // Disable is in EVERY enrolled list, and is also offered when the status could
  // not be read at all (503 / error): the kill switch must not depend on a read.
  function plan(input) {
    const status = input && input.status;
    const body = input && input.body;
    if (status === 404) return { state: 'off', controls: [] };
    if (status !== 200 || !isObj(body) || !STAGES.includes(body.stage)) {
      return { state: status === 503 ? 'unavailable' : 'error', controls: ['disable'] };
    }
    const stage = body.stage;
    const now = int(body.now_ms);
    const enabled = body.enabled === true;
    const owned = body.ownership === true;
    const leaseLive = owned && int(body.ownership_expires_ms) > now;
    const pendingRev = int(body.revocations_pending);
    const proofState = isObj(body.proof) ? code(body.proof.state) : '';
    const c = [];
    if (stage === 'not_enrolled') {
      c.push(pendingRev > 0 ? 'retry-revocations' : 'setup');
    } else if (stage === 'disabled') {
      c.push(pendingRev > 0 ? 'retry-revocations' : 'setup');
    } else {
      if (stage === 'preparing' || stage === 'pending') {
        if (body.assigned !== true) c.push('resume');
        else if (body.confirmed !== true) c.push('review');
        else c.push('verify');
      } else if (stage === 'delivery_pending') {
        if (!leaseLive) c.push('verify');
      }
      if ((stage === 'delivery_pending' || stage === 'active') && leaseLive) {
        if (!enabled && proofState !== 'armed' && proofState !== 'proved') c.push('proof');
        if (!enabled) c.push('enable');
      }
      if (pendingRev > 0) c.push('retry-revocations');
      c.push('disable');
    }
    return { state: stage, controls: c };
  }

  // ── what did a POST answer ───────────────────────────────────────────────
  // `r` is null (the human closed the passcode prompt: nothing was sent) or
  // { ok, status, body }. Blockers carry only validated ids/trigger codes.
  function outcome(action, r) {
    if (r === null) return { tone: 'neutral', text: 'Cancelled. Nothing was sent.', blockers: [], code: 'cancelled' };
    const status = r && r.status;
    const body = isObj(r && r.body) ? r.body : {};
    const err = code(body.error);
    const done = (tone, text, c, blockers) => ({ tone, text, blockers: blockers || [], code: c || err });
    if (r && r.ok && status === 200) return done('good', SUCCESS[action] || 'Done.', 'ok');
    if (status === 403 && err === 'human_required') {
      const blockers = (Array.isArray(body.blocking_sessions) ? body.blocking_sessions : []).slice(0, 10)
        .filter(isObj).map((b) => ({
          session: typeof b.session_id === 'string' && SID.test(b.session_id) ? b.session_id : 'unnamed session',
          trigger: code(b.trigger) || 'unknown' }));
      return done('warn', 'Not started: unattended agent sessions are running. Stop them or wait until they finish, then try again. '
        + 'Disable and Retry revocation are never blocked by this.', 'human_required', blockers);
    }
    if (status === 404) return done('neutral', 'Agent mail is switched off on this install, so this did nothing.', 'not_found');
    if (status === 400) return done('bad', 'The server did not accept that request. Nothing was changed.', err || 'invalid_request');
    if (status === 409) return done('bad', refusal(err), err || 'refused');
    if (status === 503 && err === 'operational_adapter_unavailable') {
      return done('warn', 'Agent mail is switched on, but its service adapter is not installed on this install. This cannot run, and nothing was changed.',
        err);
    }
    if (status === 503) {
      return done('warn', 'The mail store could not be read or written, so this was not acknowledged'
        + (action === 'disable' ? ' and mail may still be on' : '') + '. Check the status above before trying again.',
        err || 'storage_unavailable');
    }
    if (status === 429) return done('warn', 'Too many passcode attempts. Wait a few minutes and try again.', err || 'too_many_attempts');
    if (!status) return done('bad', 'Could not reach the server. Nothing was confirmed; check the status above.', 'unreachable');
    return done('bad', 'The server answered ' + int(status) + '. Check the status above.', err || 'http_' + int(status));
  }

  // ── the review disclosure (POST review -> exact dict that confirm must echo) ──
  // Validated field by field; any miss returns null so a malformed answer can
  // never be confirmed. The owner address is the one the human typed, shown in
  // full here (the status card only ever shows a masked hint).
  function reviewView(raw) {
    if (!isObj(raw)) return null;
    const keys = ['owner', 'install', 'generation', 'public_key', 'service_fingerprint', 'service_origin',
      'manifest_sequence', 'manifest_digest'];
    if (Object.keys(raw).length !== keys.length || keys.some((k) => !(k in raw))) return null;
    const short = /^[A-Za-z0-9_.:-]{1,128}$/;
    if (typeof raw.owner !== 'string' || raw.owner.length > 254 || !OWNER.test(raw.owner)) return null;
    if (typeof raw.install !== 'string' || !short.test(raw.install)) return null;
    if (!Number.isInteger(raw.generation) || raw.generation < 1) return null;
    if (typeof raw.public_key !== 'string' || !short.test(raw.public_key)) return null;
    if (typeof raw.service_fingerprint !== 'string' || !HEX64.test(raw.service_fingerprint)) return null;
    if (typeof raw.service_origin !== 'string' || !/^https:\/\/[a-z0-9.-]{1,253}$/.test(raw.service_origin)) return null;
    if (!Number.isInteger(raw.manifest_sequence) || raw.manifest_sequence < 1) return null;
    if (typeof raw.manifest_digest !== 'string' || !HEX64.test(raw.manifest_digest)) return null;
    return { raw, rows: [
      ['Owner mailbox', raw.owner], ['Mail service', raw.service_origin],
      ['Service key fingerprint', raw.service_fingerprint.match(/.{1,16}/g).join(' ')],
      ['Manifest', '#' + raw.manifest_sequence + ', digest ' + raw.manifest_digest.slice(0, 16) + '…'],
      ['This install’s inbox', raw.install + ' (generation ' + raw.generation + ')'],
      ['This install’s mail key', raw.public_key],
    ] };
  }

  const validOwner = (s) => typeof s === 'string' && s.length <= 254 && OWNER.test(s);
  const validCode = (s) => typeof s === 'string' && /^[A-Za-z0-9_-]{1,256}$/.test(s);

  window.AgentMailActionsModel = { ACTIONS, plan, outcome, reviewView, validOwner, validCode, refusal };
})();

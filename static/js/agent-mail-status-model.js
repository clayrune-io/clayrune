// ── Agent-mail status panel: the pure model (MC-1166 unit 7e-1) ───────────────
// Turns ONE GET /api/agent-mail/enrollment answer (or its 404/503/error) into
// the rows the panel draws. No DOM, no fetch, no clock of its own: "now" comes
// from the server's own `now_ms` so a wrong browser clock cannot make a lease
// look alive. READ-ONLY by construction: nothing here names a mutating route.
//
// The model only ever reads known keys and only ever emits booleans, integers,
// a masked owner hint and closed reason codes. Any other field in the payload
// (an unexpected `body`, `reply_text`, `last_error`, ...) is never copied, so a
// future server field cannot reach the screen by accident, and nothing derived
// from mail text can be shown as an instruction or approval.
// Window-bridged, no `import` (static/js modules do not share scope).
(function () {
  const CODE = /^[a-z0-9_]{1,48}$/;
  const HINT = /^[^\s@*]\*\*\*@[A-Za-z0-9.-]{1,253}$/;
  const STAGES = ['not_enrolled', 'preparing', 'pending', 'delivery_pending', 'active', 'disabled'];

  const REASON_TEXT = {
    remote_adapter_unavailable: 'the mail service connection is not available',
    remote_unreachable: 'the mail service could not be reached',
    remote_not_acknowledged: 'the mail service did not acknowledge',
    remote_response_unsigned: 'the mail service reply could not be verified',
    remote_response_invalid: 'the mail service sent an unusable reply',
    remote_scope_mismatch: 'the mail service answered for a different install',
    remote_cancel_retryable: 'cancelling at the mail service will be retried',
    remote_revoke_retryable: 'revoking at the mail service will be retried',
    remote_cancel_scope_mismatch: 'the mail service cancel did not match this install',
    remote_renew_retryable: 'renewing ownership will be retried',
    remote_activation_retryable: 'activation will be retried',
    remote_proof_retryable: 'the delivery proof will be retried',
    worker_reconcile_failed: 'the mail worker check failed',
    ownership_expired: 'the ownership lease expired',
    renewal_cap: 'the ownership lease reached its renewal limit',
    delivery_pending: 'waiting for the delivery proof',
    deletion_pending: 'a session or project deletion is still being applied',
    vault_locked: 'the vault is locked',
    store_held: 'the mail store is on hold',
    store_missing: 'the mail store is missing', store_corrupt: 'the mail store is damaged',
    store_version: 'the mail store version is not supported', store_unavailable: 'the mail store is unavailable',
    marker_missing: 'the store marker is missing', marker_corrupt: 'the store marker is damaged',
    marker_version: 'the store marker version is not supported',
    marker_inconsistent: 'the store marker does not match the store',
    seal_missing: 'the identity seal is missing', not_configured: 'mail is not configured',
    lifecycle_state_corrupt: 'the enrollment record is damaged', manifest_stale: 'the service manifest is stale',
    lifecycle_raced: 'two changes raced', fence_token_missing: 'a cancel token is missing',
    backoff: 'waiting before the next retry', step_failed: 'a recovery step failed',
    operational_adapter_unavailable: 'the mail service adapter is not installed on this install',
    storage_unavailable: 'the mail store is unavailable', not_enrolled: 'not enrolled', stage_disabled: 'mail is off',
  };
  const STEP_TEXT = { store: 'store', deletion: 'deletion', revocations: 'revocation', flows: 'enrollment',
    renew: 'lease renewal', activation: 'activation', replay: 'worker check', inbound: 'inbound mail',
    cleanup: 'cleanup', retention: 'retention', supervisor: 'recovery loop' };

  const code = (v) => (typeof v === 'string' && CODE.test(v) ? v : '');
  const int = (v) => (Number.isInteger(v) && v >= 0 ? v : 0);
  const why = (c) => REASON_TEXT[c] || (c ? c.replace(/_/g, ' ') : '');

  // 1500 -> "2 min", 7200000 -> "2 h". Always positive; the caller says in/ago.
  function span(ms) {
    const s = Math.max(0, Math.round(ms / 1000));
    if (s < 60) return s + ' s';
    if (s < 5400) return Math.round(s / 60) + ' min';
    if (s < 129600) return Math.round(s / 3600) + ' h';
    return Math.round(s / 86400) + ' d';
  }
  function when(ts, nowMs) {
    if (!ts) return '';
    const d = ts - nowMs;
    return d > 0 ? 'in ' + span(d) : span(-d) + ' ago';
  }
  function stamp(ms) { return new Date(ms).toISOString().replace('T', ' ').slice(0, 16) + ' UTC'; }

  const row = (key, label, value, tone) => ({ key, label, value, tone: tone || 'neutral' });
  const yes = (b) => (b ? 'ready' : 'not ready');

  // The shell: every non-200 outcome of the GET.
  function unavailable(status, body) {
    const err = code(body && body.error);
    if (status === 404) return { state: 'off', tone: 'neutral', headline: 'Agent mail is off',
      detail: 'This install has agent mail enrollment switched off, so nothing is enrolled and no mail is read or sent.',
      rows: [], notes: [] };
    if (status === 503) return { state: 'unavailable', tone: 'warn', headline: 'Agent mail is unavailable',
      detail: 'Agent mail is switched on, but its service cannot answer right now' + (why(err) ? ': ' + why(err) + '.' : '.')
        + ' Nothing is sent while it is unavailable.', rows: [], notes: [] };
    return { state: 'error', tone: 'bad', headline: 'Could not read agent mail status',
      detail: status ? 'The server answered ' + int(status) + '.' : 'The server could not be reached.', rows: [], notes: [] };
  }

  function describe(input) {
    const status = input && input.status;
    const body = input && input.body;
    if (status !== 200 || !body || typeof body !== 'object' || Array.isArray(body)) return unavailable(status, body);
    const stage = STAGES.includes(body.stage) ? body.stage : '';
    if (!stage) return unavailable(0, null);
    const now = int(body.now_ms);
    const enabled = body.enabled === true;
    const owned = body.ownership === true;
    const expires = int(body.ownership_expires_ms);
    const leaseLive = owned && expires > now;
    const pendingRev = int(body.revocations_pending);
    const rec = body.recovery && typeof body.recovery === 'object' ? body.recovery : {};
    const mail = body.mail && typeof body.mail === 'object' ? body.mail : {};
    const prereq = body.prerequisites && typeof body.prerequisites === 'object' ? body.prerequisites : {};
    const proof = body.proof && typeof body.proof === 'object' ? body.proof : null;
    const steps = Array.isArray(rec.steps) ? rec.steps : [];

    // Local vs remote, kept apart: "off here" never claims "gone at the service".
    let local = 'Off on this computer';
    let remote = 'No registration';
    let tone = 'neutral', headline = 'Not enrolled', detail = 'Mail is off. Nothing is enrolled, read or sent.';
    if (stage === 'preparing' || stage === 'pending') {
      remote = 'Pending, not verified'; tone = 'warn'; headline = 'Enrollment in progress';
      detail = 'An enrollment was started and is not finished. Mail stays off until it is verified and enabled.';
    } else if (stage === 'delivery_pending') {
      remote = leaseLive ? 'Owner verified, not enabled' : 'Verification lease expired'; tone = 'warn';
      headline = leaseLive ? 'Verified, waiting to be enabled' : 'Verification expired';
      detail = leaseLive ? 'Ownership is verified. Mail stays off until the delivery proof is done and it is enabled.'
        : 'The verification lease ran out before mail was enabled. It has to be verified again.';
    } else if (stage === 'active') {
      local = enabled ? 'On' : 'Off (record active, not enabled)';
      remote = leaseLive ? 'Active' : 'Active, ownership lease expired';
      tone = enabled && leaseLive ? 'good' : 'warn';
      headline = enabled ? (leaseLive ? 'Agent mail is on' : 'Agent mail is on, lease expired') : 'Enrolled, not enabled';
      detail = enabled && leaseLive ? 'Mail is enabled for this install.'
        : 'Sending is blocked until the ownership lease is renewed.';
    } else if (stage === 'disabled') {
      remote = pendingRev > 0 ? 'Revocation pending at the service' : 'Revoked';
      tone = pendingRev > 0 ? 'warn' : 'neutral';
      headline = pendingRev > 0 ? 'Disabled here, revocation pending' : 'Disabled and revoked';
      detail = pendingRev > 0
        ? 'Mail is off on this computer. The service has not yet confirmed the revocation (' + pendingRev + ' waiting); it will be retried.'
        : 'Mail is off on this computer and the service has confirmed the revocation.';
    } else if (pendingRev > 0) {
      remote = 'Revocation pending at the service'; tone = 'warn';
    }

    const rows = [row('local', 'On this computer', local, enabled ? 'good' : 'neutral'),
      row('remote', 'At the mail service', remote, tone)];
    const hint = typeof body.owner_hint === 'string' && HINT.test(body.owner_hint) ? body.owner_hint : '';
    if (hint || stage !== 'not_enrolled') rows.push(row('owner', 'Owner mailbox', hint || 'not shown'));

    if (stage === 'preparing' || stage === 'pending' || stage === 'delivery_pending') {
      let v;
      if (stage === 'preparing') v = 'Not started at the service yet';
      else if (body.assigned !== true) v = 'Waiting for the service to reserve an inbox';
      else if (body.confirmed !== true) v = 'Waiting for you to review and confirm';
      else if (!owned) v = 'Waiting for the code sent to the owner mailbox';
      else v = leaseLive ? 'Verified' : 'Verification expired';
      rows.push(row('verification', 'Verification', v + '. Resumable after a restart.', owned && leaseLive ? 'good' : 'warn'));
    }
    if (owned) rows.push(row('lease', 'Ownership lease',
      expires > now ? 'Expires ' + when(expires, now) + ' (' + stamp(expires) + ')' : 'Expired ' + when(expires, now),
      leaseLive ? 'good' : 'bad'));

    for (const [key, label] of [['tunnel', 'Remote tunnel'], ['claude_extraction', 'Claude reply extraction'],
      ['machine_custody', 'Delivery custody proof']]) {
      rows.push(row('prereq_' + key, label, yes(prereq[key] === true), prereq[key] === true ? 'good' : 'warn'));
    }
    if (proof) {
      const ps = code(proof.state);
      const act = code(proof.activation);
      const label = { none: 'none yet', armed: 'armed, waiting for the service', proved: 'proved',
        invalidated: 'invalidated', consumed: 'used for activation' }[ps] || 'unknown';
      const exp = int(proof.expires_ms);
      rows.push(row('proof', 'Delivery proof', label + (exp && (ps === 'armed' || ps === 'proved')
        ? ', expires ' + when(exp, now) : '') + (act === 'pending' ? '; activation pending' : act === 'acked' ? '; activation acknowledged' : ''),
        ps === 'proved' || ps === 'consumed' ? 'good' : (ps === 'invalidated' ? 'bad' : 'neutral')));
    }

    const blocked = int(mail.blocked), uncertain = int(mail.uncertain), retry = int(mail.retryable), outU = int(mail.outbound_uncertain);
    const reasons = Object.entries(mail.hold_reasons && typeof mail.hold_reasons === 'object' ? mail.hold_reasons : {})
      .filter(([k, v]) => code(k) && int(v) > 0).slice(0, 8).map(([k, v]) => why(k) + ' (' + int(v) + ')');
    const holds = rec.holds && typeof rec.holds === 'object' ? Object.entries(rec.holds)
      .filter(([k, v]) => code(k) && int(v) > 0).slice(0, 8).map(([k, v]) => why(k) + ' (' + int(v) + ')') : [];
    rows.push(row('holds', 'Holds', blocked + holds.length === 0 ? 'none'
      : [blocked ? blocked + ' held message' + (blocked === 1 ? '' : 's') + (reasons.length ? ': ' + reasons.join(', ') : '') : '',
        holds.length ? 'recovery holds: ' + holds.join(', ') : ''].filter(Boolean).join('; '),
      blocked + holds.length ? 'warn' : 'good'));
    rows.push(row('uncertain', 'Uncertain', uncertain + outU === 0 ? 'none'
      : [uncertain ? uncertain + ' inbound handoff' + (uncertain === 1 ? '' : 's') + ' may or may not have reached an agent' : '',
        outU ? outU + ' outbound message' + (outU === 1 ? '' : 's') + ' may or may not have been sent' : ''].filter(Boolean).join('; ')
        + '. Neither is repeated automatically.', uncertain + outU ? 'warn' : 'good'));
    if (retry) rows.push(row('retryable', 'Waiting to retry', retry + ' inbound message' + (retry === 1 ? '' : 's'), 'neutral'));

    const del = steps.find((s) => s && s.name === 'deletion' && s.state === 'pending');
    rows.push(row('deletion', 'Deletion', del ? 'Pending: ' + int(del.count) + ' session or project deletion'
      + (int(del.count) === 1 ? '' : 's') + ' still being applied' : 'none pending', del ? 'warn' : 'good'));
    rows.push(row('revocations', 'Revocations', pendingRev ? pendingRev + ' waiting for the mail service' : 'none waiting',
      pendingRev ? 'warn' : 'good'));

    // Recovery pass + last worker failure. The first failing step names the cause.
    const recState = code(rec.state);
    const bad = steps.find((s) => s && (s.state === 'failed' || s.state === 'held'))
      || steps.find((s) => s && s.state === 'pending' && s.name !== 'deletion');
    let recText;
    if (recState === 'not_running' || !recState) recText = 'Not running in this process';
    else if (recState === 'off') recText = 'Off';
    else recText = ({ ok: 'Healthy', degraded: 'Degraded', dead: 'Stopped unexpectedly', starting: 'Starting',
      stopped: 'Stopped', unknown: 'Unknown' }[recState] || 'Unknown');
    if (typeof rec.last_success === 'number' && rec.last_success > 0) recText += '; last success ' + when(rec.last_success * 1000, now);
    else if (recState === 'ok' || recState === 'degraded') recText += '; no success yet';
    rows.push(row('recovery', 'Background recovery', recText, recState === 'ok' ? 'good'
      : (recState === 'degraded' || recState === 'dead' ? 'bad' : 'neutral')));
    const failCode = bad ? code(bad.reason) : code(rec.reason);
    rows.push(row('failure', 'Last worker failure', bad || failCode
      ? (bad ? 'In ' + (STEP_TEXT[bad.name] || 'recovery') + ': ' : '') + (why(failCode) || 'unspecified')
        + (int(rec.consecutive_failures) ? ' (' + int(rec.consecutive_failures) + ' pass' + (int(rec.consecutive_failures) === 1 ? '' : 'es') + ' in a row)' : '')
      : 'none', bad || failCode ? 'bad' : 'good'));

    return { state: stage, tone, headline, detail, rows,
      notes: ['This panel only shows status. It cannot change anything.'] };
  }

  window.AgentMailStatusModel = { describe, why, span };
})();

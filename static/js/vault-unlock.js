// ── Vault unlock — the one request helper ───────────────────────────────────
//
// The browser half of `GET /api/secrets/vault-lock` and
// `POST /api/secrets/vault-lock/unlock` (mc/blueprints/secrets_routes.py). Every
// place that unlocks the vault from a form goes through `VaultUnlock.unlock`, so
// the call shape lives once: the Secrets panel's lock bar (secrets-panel.js) and
// the Desk's inline unlock (desk-v1-vault-gate.js).
//
// Unlocking stays human-only and is the server's decision: it needs the retyped
// dashboard passcode with the passphrase (or recovery key) in the same request.
// Nothing here keeps either value: the caller reads them from its inputs, hands
// them to `unlock`, and clears the inputs.
(function () {
  function _base() { return typeof API_BASE === 'string' ? API_BASE : ''; }

  // `message` carries the friendlier server text for passcode_required /
  // too_many_attempts; bad_passcode has none, so it gets one here.
  function errorText(out) {
    if (!out) return 'Request failed.';
    if (out.error === 'bad_passcode') return 'Wrong dashboard passcode.';
    return out.message || out.error || 'Request failed.';
  }

  // True only when the server says the vault is configured AND locked. A status that
  // cannot be read is "not known to be locked": the server still refuses every write.
  async function isLocked() {
    try {
      const res = await fetch(_base() + '/api/secrets/vault-lock');
      if (!res.ok) return false;
      const out = await res.json();
      return !!out && out.state === 'locked';
    } catch (_) { return false; }
  }

  // body: { passphrase | recovery_key, passcode }. Resolves { ok, status, body }; rejects
  // only when the server cannot be reached.
  async function unlock(body) {
    const res = await fetch(_base() + '/api/secrets/vault-lock/unlock', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return { ok: res.ok, status: res.status, body: await res.json().catch(() => ({})) };
  }

  window.VaultUnlock = { errorText, isLocked, unlock };
})();

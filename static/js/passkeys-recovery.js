// ── Passkeys: the states that are not "working", and the two ways out ────────
// (docs/PASSKEYS_SPEC.md, "Slice 2 prerequisites").
//
// Copy for each refusal the server can give instead of a passkey list, and the
// two passcode-only controls:
//   recover  lost every passkey: revokes them all (POST /api/passkeys/recover)
//   reset    the registry is refused (unsigned, tampered, unreadable): moves it
//            aside so a passkey can be enrolled again (POST /api/passkeys/reset)
// Both ask for the dashboard passcode through humanProofFetch, never directly,
// and both are host-only server-side; this file only reports what the server said.
// The `confirm` strings must equal the ones in mc/blueprints/passkey_routes.py.
//
// UI copy here uses no em-dashes.

(function () {
  const RECOVER_CONFIRM = 'revoke-all-passkeys';
  const RESET_CONFIRM = 'reset-passkey-registry';

  // Keyed by the server's error code (503 bodies) and, with the `passkey_`
  // prefix added, by `enroll_blocked_reason` on a 200 listing.
  const STATES = {
    passkey_vault_locked: {
      text: 'Unlock your vault to use passkeys.',
      action: { act: 'vault-open', label: 'Open the vault' },
    },
    passkey_vault_not_configured: {
      text: 'Passkeys need a vault passphrase.',
      action: { act: 'vault-set', label: 'Set a vault passphrase' },
    },
    passkey_store_unsigned: {
      text: 'The passkey registry has no signature, so it is being refused. It may have been saved before '
        + 'signing existed, or put there from outside Clayrune; Clayrune cannot tell which. '
        + 'Reset it and enrol again.',
      resettable: true,
    },
    passkey_store_tampered: {
      text: 'The passkey registry failed its integrity check, so it is being refused. '
        + 'It may have been changed outside Clayrune. Reset it and enrol again.',
      resettable: true,
    },
    passkey_store_unreadable: {
      text: 'The passkey registry could not be read, so it is being refused. Reset it and enrol again.',
      resettable: true,
    },
  };

  // A server error code or a `vault_*` blocked reason -> its state, or null.
  function describe(code) {
    if (!code) return null;
    const key = STATES[code] ? code : 'passkey_' + code;
    const s = STATES[key];
    return s ? Object.assign({ code: key }, s) : null;
  }

  function _needHumanProof() {
    if (typeof window.humanProofFetch !== 'function') throw new Error('the passcode prompt is not available');
    return window.humanProofFetch;
  }

  // Each resolves to a status line for the panel, or null when the user backed out.
  async function recover() {
    if (!window.confirm('Revoke EVERY registered passkey? They stop counting right away and you will '
      + 'have to enrol again. Use this only if you can no longer use any of them.')) return null;
    const res = await _needHumanProof()(API_BASE + '/api/passkeys/recover',
      { method: 'POST', body: JSON.stringify({ confirm: RECOVER_CONFIRM }) },
      { title: 'Revoke all passkeys', description: 'Re-enter your dashboard passcode to revoke every passkey.' });
    if (res === null) return null;
    if (res.ok) {
      const n = res.body && res.body.revoked;
      return 'Revoked ' + (typeof n === 'number' ? n : 'all') + ' passkey' + (n === 1 ? '' : 's')
        + '. The dashboard passcode applies again; you can enrol a new passkey.';
    }
    return _why(res);
  }

  async function reset() {
    if (!window.confirm('Reset the passkey registry? This deletes every enrolled passkey from this '
      + 'server (the files are moved aside, not erased) and you will have to enrol again.')) return null;
    const res = await _needHumanProof()(API_BASE + '/api/passkeys/reset',
      { method: 'POST', body: JSON.stringify({ confirm: RESET_CONFIRM }) },
      { title: 'Reset passkeys', description: 'Re-enter your dashboard passcode to reset the passkey registry.' });
    if (res === null) return null;
    return res.ok ? 'The passkey registry was reset. You can enrol a passkey again.' : _why(res);
  }

  function _why(r) {
    const b = (r && r.body) || {};
    const known = describe(b.error);
    return (known && known.text) || b.message || b.error || ('HTTP ' + (r ? r.status : '?'));
  }

  // Buttons that leave the panel: both pages already exist and are window-bridged.
  function openVault(act) {
    const fn = act === 'vault-set' ? window.openVaultSetPassphrase : window.openSecretsVault;
    if (typeof fn === 'function') fn();
  }

  window.PasskeysRecovery = { describe, recover, reset, openVault, why: _why };
})();

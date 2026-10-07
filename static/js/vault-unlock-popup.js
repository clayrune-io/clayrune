// One global human-only unlock surface. The existing gate owns the form/request.
(function () {
  const HASH = '#unlock-vault';
  let dialog = null, returnFocus = null;

  function locked(value) {
    if (!value) return false;
    if (typeof value === 'object') {
      if (value.vault_locked === true) return true;
      return ['code', 'error', 'state', 'reason', 'message', 'body'].some((k) => locked(value[k]));
    }
    return /\bvault (?:is )?locked\b|^(?:passkey_)?vault_locked$/i.test(String(value));
  }
  function buttonHTML(value) {
    return locked(value) ? '<button type="button" class="vault-unlock-link" data-open-vault-unlock>Unlock vault</button>' : '';
  }
  function close() {
    if (!dialog) return;
    dialog.close(); dialog.remove(); dialog = null;
    // Clear the command so tapping the same deep link can open it again.
    if (location.hash === HASH) history.replaceState(null, '', location.pathname + location.search);
    if (returnFocus && returnFocus.isConnected) returnFocus.focus();
  }
  async function openVaultUnlock() {
    if (dialog) { dialog.querySelector('[data-vg-pass], [data-vault-close]')?.focus(); return; }
    returnFocus = document.activeElement;
    const d = document.createElement('dialog');
    dialog = d;
    d.className = 'vault-unlock-popup';
    d.setAttribute('aria-labelledby', 'vault-unlock-title');
    d.innerHTML = '<div class="vault-unlock-header"><h2 id="vault-unlock-title">Unlock vault</h2><button type="button" data-vault-close aria-label="Close unlock vault">×</button></div><div data-vault-popup-body><p role="status">Checking the vault…</p></div>';
    document.body.appendChild(d); d.showModal();
    d.querySelector('[data-vault-close]').onclick = close;
    d.addEventListener('cancel', (e) => { e.preventDefault(); close(); });
    const body = d.querySelector('[data-vault-popup-body]');
    try {
      // Unlike isLocked's best-effort check, an unreachable status is not called unlocked.
      const res = await fetch((typeof API_BASE === 'string' ? API_BASE : '') + '/api/secrets/vault-lock');
      if (!res.ok) throw new Error('Could not check the vault (HTTP ' + res.status + ').');
      const state = await res.json();
      if (dialog !== d) return;
      if (state.state === 'unlocked' || state.state === 'unconfigured') {
        if (state.state === 'unlocked') window.dispatchEvent(new Event('vault-unlocked'));
        close(); window.showToast(state.state === 'unlocked' ? 'Vault is already unlocked.' : 'The vault has no passphrase lock configured.'); return;
      }
      if (state.state !== 'locked') throw new Error('Could not determine whether the vault is locked.');
      body.replaceChildren();
      window.DeskV1VaultGate.mount(body, { hint: 'Enter your vault passphrase and dashboard passcode to use your saved credentials. Then retry the action that was held.', onUnlocked: () => { if (dialog === d) close(); } });
      body.querySelector('[data-vg-pass]').focus();
    } catch (e) {
      if (dialog === d) body.textContent = e.message || String(e);
    }
  }
  window.openVaultUnlock = openVaultUnlock;
  window.VaultUnlockUI = { locked, buttonHTML };
  document.addEventListener('click', (e) => {
    const trigger = e.target.closest('[data-open-vault-unlock]');
    if (trigger) { e.preventDefault(); openVaultUnlock(); }
  });
  window.addEventListener('hashchange', () => { if (location.hash === HASH) openVaultUnlock(); });
  if (location.hash === HASH) openVaultUnlock();
})();

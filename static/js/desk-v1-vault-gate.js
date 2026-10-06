// Desk v1 — an inline vault unlock for the Connections flows that store a secret.
//
// Signing in, saving a login and pasting a key all end in a write to the vault. With
// the vault locked that write is refused, and used to be refused only AFTER the person
// had finished at the vendor ("Signed in, but the sign-in could not be saved: vault is
// locked", Ron 2026-10-05). So a flow asks first: `attach(el)` checks the lock state and,
// when the vault is locked, shows a passphrase + passcode form right there. Unlocking
// does not touch the flow underneath, so he carries on where he was.
//
// The server decides everything (`VaultUnlock.unlock`, one request shared with the
// Secrets panel). Nothing here remembers a passphrase or a passcode: both inputs are
// emptied as soon as they are sent. Window-bridged module, no `import`.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function _formHTML() {
    return `
      <div class="desk-v1-vg-title">The vault is locked</div>
      <div class="desk-v1-rules-hint">Signing in and saving a login store a secret, and a locked vault refuses that. Unlock it here, then carry on: nothing you have done in this panel is lost.</div>
      <div class="desk-v1-vg-fields">
        <label class="desk-v1-conn-add-field">Vault passphrase
          <input type="password" class="desk-v1-rules-textinput" data-vg-pass autocomplete="off" spellcheck="false"></label>
        <label class="desk-v1-conn-add-field">Dashboard passcode
          <input type="password" class="desk-v1-rules-textinput" data-vg-passcode autocomplete="off" spellcheck="false"></label>
      </div>
      <div class="desk-v1-conn-add-actions">
        <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-vg-unlock>Unlock</button>
        <span class="desk-v1-rules-hint" data-vg-status role="status"></span>
      </div>`;
  }

  function _bind(gate, opts) {
    const pass = gate.querySelector('[data-vg-pass]');
    const code = gate.querySelector('[data-vg-passcode]');
    const btn = gate.querySelector('[data-vg-unlock]');
    const status = gate.querySelector('[data-vg-status]');
    const submit = async () => {
      const passphrase = pass.value.trim();
      const passcode = code.value.trim();
      if (!passphrase) { status.textContent = 'Enter the vault passphrase.'; return; }
      if (!passcode) { status.textContent = 'Enter the dashboard passcode.'; return; }
      pass.value = ''; code.value = '';
      btn.disabled = true; status.textContent = 'Unlocking…';
      try {
        const r = await window.VaultUnlock.unlock({ passphrase, passcode });
        if (!r.ok) { status.textContent = window.VaultUnlock.errorText(r.body); pass.focus(); return; }
      } catch (e) {
        status.textContent = 'Unlock failed: ' + (e && e.message ? e.message : String(e));
        return;
      } finally { btn.disabled = false; }
      status.textContent = '';
      gate.hidden = true;
      if (window.DeskV1Kit && window.DeskV1Kit.toast) window.DeskV1Kit.toast('Vault unlocked. Carry on where you were.');
      if (opts && opts.onUnlocked) opts.onUnlocked();
    };
    btn.addEventListener('click', submit);
    [pass, code].forEach((i) => i.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } }));
  }

  // Put (or find) the gate for `el`, then show it only while the vault is locked. One gate per
  // Add service panel however many cards inside it call this. Safe to call again at any time,
  // e.g. after a sign-in answered `vault_locked`: it re-reads the state. Resolves the gate.
  async function attach(el, opts) {
    if (!el || !window.VaultUnlock) return null;
    const scope = el.closest('[data-add-service]') || el;
    let gate = scope.querySelector('[data-vault-gate]');
    if (!gate) {
      gate = document.createElement('div');
      gate.className = 'desk-v1-vg';
      gate.dataset.vaultGate = '';
      gate.hidden = true;
      const head = el.firstElementChild;       // under a panel's title (its close button sits beside it), else first
      el.insertBefore(gate, head && head.classList.contains('desk-v1-rules-group-title') ? head.nextSibling : el.firstChild);
    }
    const locked = await window.VaultUnlock.isLocked();
    if (!gate.isConnected) return gate;
    if (locked && !gate.firstChild) { gate.innerHTML = _formHTML(); _bind(gate, opts); }   // no password box exists while unlocked
    gate.hidden = !locked;
    return gate;
  }

  window.DeskV1VaultGate = { attach };
})();

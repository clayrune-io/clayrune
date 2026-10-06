// ── Masked input — a secret prompt the browser will not offer to save ───────
//
// The dashboard passcode and the vault passphrase are typed into the app again
// and again (every Allow-once, every human-proof confirm, every vault unlock).
// As <input type="password"> each one made Chrome / Edge / Firefox / Safari
// offer "Save password?", and pair it with whatever text field sat nearest as
// the "username" (2026-10-06: "Drop shipping company"). Browsers decide that a
// field is a credential from `type="password"`; `autocomplete="off"` is
// ignored on it by design, and `autocomplete="current-password"` /
// "new-password" actively invite the manager.
//
// So these prompts are not password fields. They are `type="text"` rendered
// with `-webkit-text-security: disc` (masked, never shown in clear), with
// autocomplete off and the password-manager opt-out attributes, and no <form>
// around them. The server-side check and what the field holds are unchanged.
//
// A browser that cannot mask text-type inputs keeps `type="password"` instead
// (feature-detected, never UA-sniffed): a save offer there beats showing a
// passcode in clear. The same opt-out attributes still apply.
//
// Not for secret VALUE fields (API keys, tokens, vault entries): those are
// supposed to stay type=password with the reveal toggle in secret-form.js.
//
// Usage, inside an HTML template string — do NOT also add your own type or
// autocomplete attribute, and keep your own `class`:
//   <input ${window.MaskedInput.attrs()} id="x" class="settings-input">
//
// Window-bridged module, no `import`.
(function () {
  // Prefixed in every engine that has it; the bare name is future-proofing.
  const CAN_MASK = !!(window.CSS && CSS.supports
    && (CSS.supports('-webkit-text-security', 'disc') || CSS.supports('text-security', 'disc')));

  // The attribute is the CSS hook (masked-input.css), so a call site's own
  // `class` is never in the way.
  const COMMON = 'autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" '
    + 'data-lpignore="true" data-1p-ignore="true" data-bwignore="true" data-form-type="other" data-mc-masked';

  function attrs() {
    return (CAN_MASK ? 'type="text" ' : 'type="password" ') + COMMON;
  }

  // A type=password field refuses copy and cut; a masked text field would not,
  // so the dots could be copied out as the real value. Keep the old behaviour.
  function _blockCopy(e) {
    const t = e.target;
    if (t && t.hasAttribute && t.hasAttribute('data-mc-masked')) e.preventDefault();
  }
  document.addEventListener('copy', _blockCopy, true);
  document.addEventListener('cut', _blockCopy, true);

  window.MaskedInput = { attrs, canMask: CAN_MASK };
})();

// Desk v1 — Connections: the install card of a curated MCP package (docs/DESK_CONNECT_BY_URL_SPEC.md,
// slice 4). The Review step of a method that installs software shows what will be installed and
// asks for an explicit approval; the Save request then carries the pins that were shown
// (`{approved, package, version, integrity}`) and the server refuses it if they have changed.
// Every fact on the card is the server's reviewed catalogue entry (mc/desk_connect/mcp_catalogue.json);
// nothing on it is text a package or a page supplied. Holds no credential. Window-bridged
// module, no `import` (ground rule 1).
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  let _approvedFor = '';           // the pins the human approved, as one string; '' = not approved

  function _pinKey(card) { return card ? `${card.pins.package}@${card.pins.version}#${card.pins.integrity}` : ''; }
  function _size(bytes) { return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`; }

  function html(card) {
    if (!card) return '';
    const ok = _approvedFor === _pinKey(card);
    return `
        <section class="desk-v1-cf-install" data-cf-install aria-label="Software that will be installed">
          <div class="desk-v1-rules-group-title">Software this installs</div>
          <dl class="desk-v1-cf-facts">
            <div><dt>Package</dt><dd data-cf-i-package><code>${esc(card.name)}</code> (npm)</dd></div>
            <div><dt>Version</dt><dd data-cf-i-version>${esc(card.version)}, pinned: never updates by itself</dd></div>
            <div><dt>Checksum</dt><dd data-cf-i-integrity><code class="desk-v1-cf-wrap">${esc(card.integrity)}</code></dd></div>
            <div><dt>Source</dt><dd data-cf-i-source>${esc(card.source)}</dd></div>
            <div><dt>Licence</dt><dd data-cf-i-licence>${esc(card.licence)}</dd></div>
            <div><dt>Size</dt><dd data-cf-i-size>${esc(_size(card.unpacked_bytes))} unpacked</dd></div>
            <div><dt>Purpose</dt><dd data-cf-i-purpose>${esc(card.purpose)}</dd></div>
            <div><dt>Registered as</dt><dd data-cf-i-server>MCP server <code>${esc(card.server_name)}</code>, for every project</dd></div>
            <div><dt>Credential</dt><dd data-cf-i-cred>${esc(card.credential.label)}, kept in Secrets as <code>${esc(card.credential.vault)}</code> and passed to the server as <code>${esc(card.credential.env)}</code> when it starts. It is never written into the MCP configuration.</dd></div>
          </dl>
          <ul class="desk-v1-cf-perms" data-cf-i-perms>${card.permissions.map((p) => `<li>${esc(p)}</li>`).join('')}</ul>
          <div class="desk-v1-rules-hint">Nothing is downloaded or run when you save. The first time an agent session uses the server, Node.js fetches exactly this version and checks it against the checksum above.</div>
          <label class="desk-v1-cf-check"><input type="checkbox" data-cf-install-approve ${ok ? 'checked' : ''}>
            <span>I have read this and approve installing it.</span></label>
        </section>`;
  }

  function bind(root, card, onChange) {
    const box = root.querySelector('[data-cf-install-approve]');
    if (!box || !card) return;
    box.addEventListener('change', () => { _approvedFor = box.checked ? _pinKey(card) : ''; if (onChange) onChange(); });
  }

  function approved(card) { return !!card && _approvedFor === _pinKey(card); }

  // The consent for the Save request: exactly the pins that were shown, or null.
  function read(card) {
    return approved(card) ? { approved: true, package: card.pins.package, version: card.pins.version, integrity: card.pins.integrity } : null;
  }

  function clear() { _approvedFor = ''; }

  window.DeskV1ConnectInstall = { html, bind, approved, read, clear };
})();

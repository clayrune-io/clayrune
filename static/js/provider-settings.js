// ── Provider Settings section ─────────────────────────────────────────────
// Renders the Providers category inside _renderSettings(). Every provider —
// Claude included — is one row from the SAME component first-run setup's Agent
// connections step uses (provider-auth.js _renderProviderRow), so vendor
// setup is reachable from Settings at any time (F1) and no vendor is
// special-cased in how it looks.
function _renderProviderSettings(cfg) {
  // Fall back to a synthetic claude entry if the providers endpoint hasn't
  // resolved — the Claude sign-in card must never silently disappear.
  let provs = _agentProviders || [];
  if (provs.length === 0) {
    provs = [{ name: 'claude', display_name: 'Claude Code', installed: true,
               auth_status: 'unknown' }];
  }

  const defProv = cfg.default_provider || 'claude';
  const rows = provs.map(p => window._renderProviderRow(p, {
    mode: 'settings',
    defaultName: defProv,
    signInWhenOk: true,   // Settings keeps re-sign-in / account switching
    keyEntry: true,       // API-key vendors get their key field in the row
  })).join('');

  // Section toolbar: batch install of the ticked not-installed rows (one
  // terminal, prerequisite handled once — F7) and a forced re-probe of every
  // vendor (F8).
  const anyMissing = provs.some(p => !p.installed);
  return `
    <div class="settings-section" id="settings-providers-section">
      <div style="display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px"><!-- not a .settings-row: keeps the section a search "custom-content" unit so vendor names match -->
        <div>
          <div class="settings-label">Vendors</div>
          <div class="settings-hint">Install, sign in and pick the default for every vendor. The default is used for new chats; change it per chat in the composer.</div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          ${anyMissing ? `<button type="button" class="btn-add" id="settings-prov-install-selected"
                  onclick="settingsInstallSelectedProviders(this)">Install selected</button>` : ''}
          <button type="button" class="btn-add" style="background:var(--surface3);color:var(--text)"
                  id="settings-prov-check-status" onclick="providerRefreshAll()">Check setup status</button>
        </div>
      </div>
      <div class="prov-install-policy-note" style="font-size:11px;color:var(--text-faint);margin-top:6px"></div>
      <div class="prov-rows" style="display:flex;flex-direction:column;gap:8px;margin-top:8px">${rows}</div>
    </div>`;
}



// ── Interop: re-expose for the cross-module caller. `_renderProviderSettings`
//    is interpolated into _renderSettings() by settings-drill.js (module 6)
//    at render time (runtime) — resolves the window prop. Its row component
//    (`_renderProviderRow`) and handlers come from provider-auth.js as
//    window props; `_agentProviders` is an inline global
//    resolved at call time. ──
window._renderProviderSettings = _renderProviderSettings;

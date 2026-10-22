// Ownership of relocated Settings controls. Data stays in its original
// store; only its editor moves. Existing passcode and unattended gates apply.
async function openProviders() { await openSettings(); drillSettings('providers'); }
async function openSecurity() {
  await openSettings(); drillSettings('system');
  const sections = Array.from(document.querySelector('[data-cat="system"]').children).filter(el => el.classList.contains('settings-section'));
  const index = sections.findIndex(el => el.querySelector('.settings-section-title')?.textContent.trim() === 'Security');
  if (index >= 0) drillSettingsSub(index);
}
function systemExtras(cfg, {toggle}) { return `
  <div class="settings-section"><div class="settings-section-title">Runtime and availability</div>
      <div class="settings-row">
        <div>
          <div class="settings-label">Keep machine awake</div>
          <div class="settings-hint">Prevents automatic system sleep while Clayrune is running, even with no agents working. Works on Windows, macOS (battery or plugged in), and Linux with systemd-inhibit. The display can still sleep. Off by default; changes take effect within about 20 seconds without a restart. Cannot wake a sleeping or powered-off computer. Lid closure and manual sleep follow OS policy; on Linux, turn this off before manually suspending.</div>
        </div>
        ${toggle('keep_awake_enabled', cfg.keep_awake_enabled)}
      </div>      <div class="settings-row">
        <div>
          <div class="settings-label">Sticky settings</div>
          <div class="settings-hint">Bakes brevity + model/effort into each chat at spawn (cached, fewer tokens per turn) instead of re-sending every message. Changing one of those mid-chat resumes the agent so it takes effect. Experimental.</div>
        </div>
        ${toggle('sticky_agent_settings', cfg.sticky_agent_settings)}
      </div>      <div class="settings-row">
        <div>
          <div class="settings-label">Desk v1</div>
          <div class="settings-hint">Manage campaigns, content and publishing in the live Desk workspace. The legacy Desk stays read-only while this is on.</div>
        </div>
        ${toggle('desk_v1', cfg.desk_v1)}
      </div></div>`; }
function securityRow(cfg, {toggle}) { return `      <div class="settings-row" id="codex-sandbox-row">
        <div>
          <div class="settings-label">Sandbox unattended Codex runs</div>
          <div class="settings-hint">Scheduled, workflow, dispatched and hivemind Codex runs are confined to their working folder (workspace-write, no network). Off lets those runs bypass the sandbox entirely. Chats you start yourself are never sandboxed. Changing it needs your dashboard passcode; an agent cannot change it.</div>
        </div>
        ${toggle('codex_unattended_sandbox', cfg.codex_unattended_sandbox !== false)}
      </div>`; }
function appearanceRow(cfg, {toggle}) { return `      <div class="settings-row">
        <div>
          <div class="settings-label">Live activity states</div>
          <div class="settings-hint">Shows what the agent is actually doing right now — a spinner while it thinks, dots while it writes the reply — instead of one generic bubble. Claude only. Experimental; off reverts to the plain dots.</div>
        </div>
        ${toggle('activity_states_enabled', cfg.activity_states_enabled)}
      </div>`; }
function providerRuntime(cfg, {toggle, textInput, engineFallbackHTML}) { return `
  <div class="settings-section" id="engine-fallback-section"><div class="settings-section-title">Advanced</div>
    <div class="settings-label">Engine fallback</div>${engineFallbackHTML}
    <div class="settings-row"><div><div class="settings-label">Claude task routing</div><div class="settings-hint">Auto-selects a Claude tier per task. Explicit chat and agent-type model choices take priority.</div></div>${toggle('auto_model_enabled', cfg.auto_model_enabled)}</div>
    <details><summary>CLI integration</summary>
      <div class="settings-row">
        <div><div class="settings-label">Streaming (Mode B)</div><div class="settings-hint">One persistent process per chat.</div></div>
        ${toggle('use_streaming_agent', cfg.use_streaming_agent)}
      </div>      <div class="settings-row">
        <div>
          <div class="settings-label">Remote control <span style="font-size:10px;font-weight:600;padding:1px 6px;border-radius:6px;background:var(--accent-dim);color:var(--accent);margin-left:6px;letter-spacing:.3px">EXPERIMENTAL</span></div>
          <div class="settings-hint">Headless sessions don't reach claude.ai yet — use Push notifications.</div>
        </div>
        ${toggle('agent_remote_control', cfg.agent_remote_control)}
      </div>      <div class="settings-row">
        <div><div class="settings-label">Channels</div><div class="settings-hint">MCP plugin channels (e.g. plugin:telegram@…).</div></div>
        ${textInput('agent_channels', cfg.agent_channels)}
      </div></details></div>`; }
const moved = [
  ['Agent name face identity maximum turns permissions brief replies emojis', 'Floor defaults', 'openFloorPreferences()'],
  ['User name timezone profile', 'Your profile', 'openUserProfile()'],
  ['Model effort condense housekeeping fallback provider engine', 'Providers', 'SettingsOwnership.openProviders()'],
  ['Pause all scheduled runs', 'Scheduler', "sidebarNav('scheduler')"],
  ['Sandbox unattended Codex runs', 'System → Security', 'SettingsOwnership.openSecurity()'],
];
function search(q) {
  const host = document.getElementById('settings-moved-results');
  if (!host) return false;
  const hits = q ? moved.filter(([terms]) => terms.toLowerCase().includes(q)) : [];
  host.classList.toggle('settings-hidden', !hits.length);
  host.innerHTML = hits.map(([terms,label,action]) => `<button class="settings-list-row" type="button" onclick="${esc(action)}"><span class="settings-list-txt"><span class="settings-list-title">${esc(label)}</span><span class="settings-list-sub">${esc(terms)}</span></span><span aria-hidden="true">›</span></button>`).join('');
  return hits.length > 0;
}
window.SettingsOwnership = {openProviders, openSecurity, systemExtras, securityRow, appearanceRow, providerRuntime, search};

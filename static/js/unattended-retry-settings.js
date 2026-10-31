// Wait budgets and capability labels belong together; all durations are minutes.
let current = {};
let saving = false;
const labels = [
  ['max_wait', 'Maximum retry wait', 30],
  ['max_usage_limit_wait', 'Maximum usage-limit wait', 120],
  ['usage_limit_recheck', 'Usage-limit recheck interval', 5],
];

function render(cfg, {toggle}) {
  current = {...Object.fromEntries(labels.map(([key, , value]) => [key, value])),
    ...(cfg.unattended_retry || {})};
  const rows = (cfg.unattended_retry_capabilities || []).map(row =>
    `<div class="settings-row"><div><div class="settings-label">${esc(row.engine)}: ${esc(row.status)}</div>`
    + `<div class="settings-hint">${esc(row.detail)}</div></div></div>`).join('');
  return `<div class="settings-section" id="unattended-retry-section">
    <div class="settings-section-title">Unattended retry waits</div>
    <div class="settings-hint">Wait-only budgets in minutes. Applies to new turns in scheduled, dispatched, workflow and hivemind runs. Productive work has no deadline. Recheck cadence applies only where supported.</div>
    ${labels.map(([key, label]) => `<div class="settings-row"><label class="settings-label" for="retry-${key}">${label} (min)</label>
      <input id="retry-${key}" class="settings-input" type="number" min="1" ${key === 'usage_limit_recheck' ? 'max="180"' : ''} step="1" value="${esc(String(current[key]))}" onchange="UnattendedRetrySettings.save('${key}',this)"></div>`).join('')}
    <div class="settings-hint" id="retry-save-status" role="status"></div>
    ${rows || '<div class="settings-hint">Capability audit unavailable.</div>'}
    <div class="settings-row"><div><div class="settings-label">Fresh Claude prompt on resume</div>
    <div class="settings-hint">Off by default pending cache-cost measurement. Requires Claude Code 2.1.267+; applies on next resume.</div></div>
    ${toggle('claude_resume_fresh_prompt', cfg.claude_resume_fresh_prompt === true)}</div>
  </div>`;
}

async function save(key, input) {
  const status = document.getElementById('retry-save-status');
  if (saving) { input.value = current[key]; return; }
  const value = Number(input.value);
  if (!Number.isSafeInteger(value * 60000) || !Number.isInteger(value) || value <= 0
      || (key === 'usage_limit_recheck' && value > 180)) {
    status.textContent = key === 'usage_limit_recheck'
      ? 'Enter whole minutes between 1 and 180.' : 'Enter positive whole minutes.';
    return;
  }
  const next = {...current, [key]: value};
  saving = true;
  input.disabled = true;
  try {
    const result = await humanProofFetch(API_BASE + '/api/config', {method: 'PUT',
      body: JSON.stringify({unattended_retry: next})},
      {title: 'Save retry budgets', description: 'Re-enter your dashboard passcode to change retry budgets.'});
    if (result === null) { input.value = current[key]; status.textContent = 'Save cancelled.'; return; }
    if (!result.ok) throw new Error(result.body.error || 'Could not save retry budgets.');
    current = next;
    status.textContent = 'Saved. Native limits apply on next launch; observed Qwen waits use current budgets.';
  } catch (error) {
    status.textContent = error.message;
    input.value = current[key];
  } finally { saving = false; input.disabled = false; }
}

window.UnattendedRetrySettings = {render, save};

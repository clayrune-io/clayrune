// Session billing presentation. Values are fixed labels or formatted numbers;
// no secret, ledger entry, or server error text enters this markup.
window.ApiCreditsSession = {
  scheduleSelect(value = 'auto') {
    return `<label for="sched-billing">Billing</label>
      <select id="sched-billing">
        <option value="auto"${value !== 'subscription' ? ' selected' : ''}>Auto (API credits)</option>
        <option value="subscription"${value === 'subscription' ? ' selected' : ''}>Subscription</option>
      </select>`;
  },
  badge(billing) {
    return billing === 'credits'
      ? '<span class="provider-badge api-credits-badge" title="This session uses API credits">API credits</span>' : '';
  },
  spend(cycle = {}) {
    const usd = value => Number.isFinite(value) ? value.toFixed(2) : '0.00';
    return `<div class="settings-hint" id="apc-spend-split">This cycle: background calls $${usd(cycle.background_spent_usd)} · agent sessions $${usd(cycle.session_spent_usd)}</div>`;
  },
};

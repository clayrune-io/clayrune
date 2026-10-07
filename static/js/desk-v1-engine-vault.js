// Vault recovery for mounted Studio cards. Only status/price reads are retried.
const cards = new Map();

export function coalesceVaultPrompts(host) {
  const prompts = host.querySelectorAll('[data-eng-vault-message]');
  prompts.forEach((prompt, i) => { if (i) prompt.remove(); });
}

export function watchEngineVault(host, opts) {
  for (const [previousHost, previous] of cards) if (!previous.alive()) cards.delete(previousHost);
  const { state, alive, paint, estimate } = opts;
  cards.set(host, { alive, refresh: async () => {
    if (!opts.getEngines()) return;
    opts.clearTimer(); state.seq = (state.seq || 0) + 1;
    state.estimate = null; state.priceConfirmation = null;
    state.estimating = true; state.estimateError = null; state.error = null;
    paint(opts.getEngines());
    const seq = state.seq;
    try {
      const engines = await opts.loadEngines();
      if (!alive() || state.seq !== seq) return;
      opts.setEngines(engines); estimate(engines);
    } catch (e) {
      if (!alive() || state.seq !== seq) return;
      state.estimating = false; state.estimateError = e; paint(opts.getEngines());
    }
  } });
}

function refreshCards(onlyGated) {
  for (const [host, opts] of cards) {
    if (!opts.alive()) { cards.delete(host); continue; }
    if (!host.getClientRects().length || (onlyGated && !host.querySelector('[data-eng-vault-message]'))) continue;
    opts.refresh();
  }
}

window.addEventListener('vault-unlocked', () => refreshCards(false));
// Returning from a different tab/device can leave a held card stale. Reuse browser
// lifecycle events, rather than adding another poll against the vault.
window.addEventListener('focus', () => refreshCards(true));
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') refreshCards(true);
});

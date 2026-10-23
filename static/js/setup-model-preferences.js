// Setup uses the same adapter-owned capability metadata as Settings.
export function supportsSetupTier(providers, providerName, tier) {
  const provider = (providers || []).find(p => p.name === providerName);
  return !!(provider && provider.model_selection !== false && provider.tiers?.[tier]);
}

export function setupModelControl(providers, providerName, selectedTier, labels) {
  const tiers = Object.keys(labels).filter(tier => supportsSetupTier(providers, providerName, tier));
  if (!tiers.length) return '';
  return `<div class="setup-section">
    <div class="setup-section-label">Default model</div>
    <div class="setup-section-hint">Applies to the selected default provider. Projects, agent types and chats can override it. Balanced is recommended when available.</div>
    <div class="mc-seg" id="setup-model-tier-seg">${tiers.map(tier =>
      `<button type="button" class="${selectedTier === tier ? 'active' : ''}" data-tier="${tier}" onclick="setupPickModelTier('${tier}',this)">${labels[tier]}</button>`).join('')}</div>
  </div>`;
}

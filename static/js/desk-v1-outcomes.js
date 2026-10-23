// Receipt evidence is projected by the server's single suppression rule.
export function receiptOutcomesHTML(receipts, esc) {
  if (!receipts || !receipts.length) return '';
  return `<div class="desk-v1-retro-grid" data-receipt-outcomes>
    <div class="desk-v1-retro-grid-head">Published receipt outcomes</div>
    ${receipts.map((r) => `<div class="desk-v1-retro-grid-row" data-receipt-outcome="${esc(r.id)}">
      <span class="desk-v1-retro-grid-date">${esc(String(r.published_at || '').slice(0, 10))}</span>
      <span>${esc(r.platform || '')} · ${esc(r.body || '')}</span>
      <span>Impressions: ${r.impressions == null ? 'unknown' : esc(r.impressions)}</span>
      <span>Replies: ${r.replies == null ? 'unknown' : esc(r.replies)}</span>
      <span>${r.possibly_suppressed === true ? 'possibly suppressed' : r.possibly_suppressed === false ? 'Reach meets threshold' : 'Suppression: unknown'}</span>
    </div>`).join('')}
  </div>`;
}

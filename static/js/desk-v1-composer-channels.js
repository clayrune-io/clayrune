// Composer destinations are piece versions. The campaign's accounts remain the
// approval bound; this picker never adds to that bound or calls approval routes.
(function () {
  const esc = (s) => window.esc(String(s == null ? '' : s));
  const active = (v) => !['archived', 'skipped'].includes(v.state);
  const sent = (v) => ['sending', 'submitted', 'verified_published', 'you_reported', 'unknown_outcome', 'failed'].includes(v.state);
  const versions = (fam) => (fam?.versions || []).filter(active);
  const accounts = (camp) => (window.DeskV1Store.state().channels || []).filter((c) => (camp.plan.accounts || []).includes(c.id));

  function html(camp, d) {
    const all = accounts(camp);
    return `<div class="desk-v1-composer-channels" role="group" aria-label="Post to">${all.map((ch) => {
      const v = versions(d.fam).find((v) => v.channelId === ch.id);
      const locked = !!d.fam?.archivedAt || (v && sent(v));
      const name = ch.label || ch.identity || ch.platform;
      return `<button type="button" class="desk-v1-composer-account" data-composer-channel="${esc(ch.id)}" aria-pressed="${d.selected.has(ch.id)}" ${locked ? 'disabled' : ''}>
        <span class="desk-v1-composer-avatar" data-platform="${esc(ch.platform)}" aria-hidden="true">${esc(ch.platform === 'x' ? 'X' : String(ch.platform || name).slice(0, 2).toUpperCase())}</span>
        <span>${esc(name)}</span><span aria-hidden="true">${d.selected.has(ch.id) ? '✓' : '+'}</span></button>`;
    }).join('')}</div>${all.length ? '' : '<p class="desk-v1-composer-hint">Choose this campaign’s accounts in Where before sending a post to review.</p>'}`;
  }

  // Called inside the composer's per-piece queue. Each successful version write
  // updates the local model immediately: a later refusal can be retried without
  // duplicating an earlier successful destination.
  async function sync(camp, d) {
    const fam = d.fam;
    const store = window.DeskV1Store;
    const base = '/api/desk/pieces/' + encodeURIComponent(fam.id) + '/versions';
    for (const ch of accounts(camp)) {
      const v = versions(fam).find((v) => v.channelId === ch.id);
      if (d.selected.has(ch.id) && !v) {
        const ver = { id: 'cv-' + Date.now().toString(36) + Math.random().toString(36).slice(2), channelId: ch.id, state: 'drafting', revision: 0 };
        const result = await store.write({
          label: 'Added a post destination',
          apply: () => { fam.versions.push(ver); },
          unapply: () => { fam.versions = fam.versions.filter((v) => v.id !== ver.id); },
          request: () => store.api('POST', base, { id: ver.id, account_id: ch.id }),
          undoRequest: () => window.deskV1QueuePiece(fam.id, () => store.api('PATCH', base + '/' + encodeURIComponent(ver.id), { state: 'archived' })),
        });
        if (!result.ok) throw new Error(result.error);
      } else if (!d.selected.has(ch.id) && v && !sent(v)) {
        const prev = { state: v.state, approved: v.approved };
        const result = await store.write({
          label: 'Removed a post destination', destructive: true,
          apply: () => { v.state = 'archived'; v.approved = null; },
          unapply: () => Object.assign(v, prev),
          request: () => store.api('PATCH', base + '/' + encodeURIComponent(v.id), { state: 'archived' }),
          undoRequest: () => window.deskV1QueuePiece(fam.id, async () => {
            const state = ['approved', 'scheduled'].includes(prev.state) ? 'needs_review' : prev.state;
            await store.api('PATCH', base + '/' + encodeURIComponent(v.id), { state });
            v.state = state;
            if (state !== prev.state) v.approved = null;
          }),
        });
        if (!result.ok) throw new Error(result.error);
      }
    }
  }
  window.DeskV1ComposerChannels = { html, sync, accounts, versions, sent };
})();

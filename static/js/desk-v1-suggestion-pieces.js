// A suggestion is a proposal, never a placed version or an invented saved id.
(function () {
  const pending = new WeakMap();
  async function ensure(fam) {
    if (!fam || !window.DeskV1Store.live()) return fam;
    let proposal = fam.suggestion;
    if (!proposal && String(fam.id).startsWith('fam-suggest-')) {
      const camp = window.DeskV1Store.state().campaigns.find((c) => c.id === fam.campaignId);
      proposal = ((camp && camp.how && camp.how.suggested && camp.how.suggested.what) || []).find((s) => s.title === fam.title);
    }
    if (!proposal) return fam;
    if (pending.has(fam)) return pending.get(fam);
    const promise = window.DeskV1Store.api('POST', '/api/desk/campaigns/' + encodeURIComponent(fam.campaignId) + '/materialize',
      { title: proposal.title, channel_id: proposal.channelId }).then((saved) => {
      // Keep optimistic arrays owned by the caller's in-flight command. The
      // server id/source are authoritative; no suggestion channel is copied.
      fam.id = saved.id; fam.source = saved.source;
      delete fam.suggestion;
      return fam;
    }).finally(() => pending.delete(fam));
    pending.set(fam, promise);
    return promise;
  }
  async function act(fam, fn) {
    try { await ensure(fam); return await fn(); }
    catch (e) { window.DeskV1Kit.toast('Could not save this piece: ' + e.message); return false; }
  }
  async function accept(camp, items) {
    const store = window.DeskV1Store;
    for (const proposal of items) {
      let fam = store.state().families.find((f) => f.campaignId === camp.id && f.suggestion && f.suggestion.title === proposal.title && f.suggestion.channelId === proposal.channelId);
      if (!fam) {
        fam = { id: 'fam-suggest-' + Date.now().toString(36) + '-' + store.state().families.length,
          campaignId: camp.id, title: proposal.title, kind: proposal.kind || 'post', suggestion: proposal, versions: [], assets: [] };
        store.state().families.push(fam);
      }
      if (!(await act(fam, () => true))) return;
      const list = store.state().families;
      if (list.some((f) => f !== fam && f.id === fam.id)) list.splice(list.indexOf(fam), 1);
    }
    window.deskV1RepaintWhat();
    window.DeskV1Kit.toast('Saved the suggested pieces in Posts. Open a post in the composer to choose its accounts.');
  }
  window.DeskV1SuggestionPieces = { ensure, act, accept };
})();

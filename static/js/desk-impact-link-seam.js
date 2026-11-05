// MC-1152 P2 (DARK): the tracked-link seam for the legacy Desk queue.
// Spec: docs/desk_v1/CHANNEL_IMPACT_SPEC.md sections 6 and 12.
//
// The legacy queue's "Tracked link" used to append `?ref=desk-<id>`. Desk-made
// links are UTM links now (mc/desk_impact_links.py), and the legacy tag is never
// emitted beside them. A queue item belongs to no campaign, account or version,
// so it has nothing to build a UTM link from. While the server flag
// `desk_impact_links_enabled` is on, the queue button says so instead of
// writing a tag. While it is off (the default) nothing here changes behaviour.
(function () {
  let cached = null;

  async function enabled() {
    if (cached !== null) return cached;
    try {
      const base = typeof API_BASE === 'string' ? API_BASE : '';
      const res = await fetch(base + '/api/desk/impact/links/status');
      cached = res.ok && (await res.json()).enabled === true;
    } catch (e) {
      cached = false;        // unreachable server: keep the legacy behaviour
    }
    return cached;
  }

  window.DeskImpactLinkSeam = {
    enabled,
    LEGACY_QUEUE_MESSAGE: 'Tracked links are made from a campaign post, where Clayrune knows the '
      + 'campaign, the account and the version. This queue item belongs to none of those, '
      + 'so it cannot carry one.',
  };
})();

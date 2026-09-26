// Desk v1 (MC-977) — T7: Results (docs/desk_v1_r0_plan.md; THE_DESK_V1_UI.md
// §7). Window-bridged module, no `import` (ground rule 1). Replaces the T0a
// stub whole.
//
// No frame is drawn for this surface (gap map C6) — every layout call below
// is made from §7's text alone, listed in full in the ticket's final report
// rather than invented silently (ground rule 8). Reached via
// `deskV1Nav('results', {campaignId})` — the campaign summary's goal/
// progress will link here once T2a lands (out of scope for this file; T2a
// owns desk-v1-campaign.js).
//
// Fixtures only (ground rule 3): RESULTS (T0a) already carries goal/
// forecast/per-version outcomes/costs/diagnostics in full — this file reads
// it, it doesn't add to it. The one thing §7 needs that T0a's RESULTS fixture
// didn't model — Posy's read + one proposed experiment — is added as its own
// `resultsInsight` key in desk-v1-fixtures.js's own T7 section (never edited
// inside RESULTS itself, same "own key" convention T3/T5/T6 established).
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function _fx() { return window.DeskV1Fixtures || {}; }
  function _campaign(id) { return (_fx().campaigns || []).find((c) => c.id === id) || null; }
  function _channel(id) { return (_fx().channels || []).find((c) => c.id === id) || null; }
  function _family(id) { return (_fx().families || []).find((f) => f.id === id) || null; }
  function _versionMeta(versionId) {
    const families = _fx().families || [];
    for (const f of families) {
      const v = (f.versions || []).find((x) => x.id === versionId);
      if (v) return { family: f, version: v };
    }
    return null;
  }
  function _money(n) {
    return n == null ? 'n/a' : '$' + (Math.round(n * 100) / 100).toFixed(2);
  }

  // ── outcome vocabulary (§7's own 4 words, LOCAL to this file — same
  // "small, ticket-only vocabulary doesn't belong in the T0b kit" convention
  // T4/T6 already used for their own local tables). Glyph + word together
  // (A15 greyscale), never color alone. ───────────────────────────────────
  const OUTCOME_STATES = {
    verified_published: { glyph: '✓', word: 'Verified published' },  // ✓
    you_reported:       { glyph: '✋', word: 'You reported' },        // ✋
    submitted_verifying:{ glyph: '⟳', word: 'Submitted · verifying' }, // ⟳
    unknown:            { glyph: '?', word: 'Unknown outcome' },
  };
  function _outcomeHTML(outcome) {
    const o = OUTCOME_STATES[outcome] || OUTCOME_STATES.unknown;
    return `<span class="desk-v1-results-outcome" data-outcome="${esc(outcome || 'unknown')}">` +
      `<span class="desk-v1-results-outcome-glyph" aria-hidden="true">${esc(o.glyph)}</span>` +
      `<span class="desk-v1-results-outcome-word">${esc(o.word)}</span></span>`;
  }

  // ── Goal (§7: "big number / target, progress, source + freshness behind
  // ⓘ") ─────────────────────────────────────────────────────────────────────
  function _goalHTML(campaign, results) {
    const g = results.goal || {};
    const metricLabel = (campaign.goal && campaign.goal.metric) || 'goal';
    const pct = g.target ? Math.max(0, Math.min(100, Math.round((g.current / g.target) * 100))) : 0;
    return `
      <div class="desk-v1-results-goal">
        <div class="desk-v1-results-goal-top">
          <div>
            <span class="desk-v1-results-goal-number">${esc(g.current)}</span>
            <span class="desk-v1-results-goal-target">of ${esc(g.target)} ${esc(metricLabel)}</span>
          </div>
          <div class="desk-v1-results-goal-meta">
            ${esc(g.source || 'source unknown')}
            ${window.DeskV1Kit ? window.DeskV1Kit.infoIconHTML('results-goal-freshness') : ''}
          </div>
        </div>
        <div class="desk-v1-results-goal-bar"><div class="desk-v1-results-goal-fill" style="width:${pct}%"></div></div>
        ${_forecastHTML(results.forecast)}
      </div>`;
  }

  // Never "on pace for" (§7, A10, kit's BANNED_PHRASES) — always "Projected
  // N of M · <label>" with the estimate disclosed via ⓘ, not implied.
  function _forecastHTML(forecast) {
    if (!forecast) return '';
    const cls = forecast.projected >= forecast.target ? 'on-target' : 'below-target';
    return `
      <div class="desk-v1-results-forecast" data-forecast="${esc(cls)}">
        Projected ${esc(forecast.projected)} of ${esc(forecast.target)} · ${esc(forecast.label)}
        <span class="desk-v1-results-forecast-est">
          estimate ${window.DeskV1Kit ? window.DeskV1Kit.infoIconHTML('results-forecast-est') : 'ⓘ'}
        </span>
      </div>`;
  }

  // ── Posy's read (§7: "what worked, and ONE proposed next experiment with
  // one variable. Set up experiment / Not now. Accepting creates a planned
  // piece on Content."). Content (T2a) isn't merged yet, so "creates a
  // planned piece" is simulated the same way T1's shelf drops already are —
  // a real client-side family pushed onto the shared DeskV1Fixtures.families
  // array via the command bus, with Undo removing it (ground rule 3: R0 runs
  // entirely on fixtures with Undo). ──────────────────────────────────────
  function _experimentCommand(campaign, insight) {
    const exp = insight.experiment;
    const famId = 'fam-experiment-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
    const family = {
      id: famId, campaignId: campaign.id, kind: 'post', title: exp.title,
      versions: [{ id: famId + '-v1', channelId: exp.channelId, state: 'planned', revision: 0 }],
    };
    return {
      label: `Added “${exp.title}” to Content as a planned piece`,
      famId,
      do: () => { _fx().families.push(family); },
      undo: () => { const arr = _fx().families; const i = arr.findIndex((f) => f.id === famId); if (i >= 0) arr.splice(i, 1); },
    };
  }

  function _posyHTML(campaign, insight, st) {
    if (!insight) return '';
    if (st.dismissed) return '';
    const exp = insight.experiment;
    const channel = exp && _channel(exp.channelId);
    return `
      <div class="desk-v1-results-posy">
        <div class="desk-v1-results-posy-head">Posy</div>
        <div class="agent-output desk-v1-results-posy-text"><div class="agent-line">${esc(insight.read)}</div></div>
        ${st.acceptedFamId ? `
          <div class="desk-v1-results-posy-confirm">Added “${esc(exp.title)}” to Content as a planned piece.</div>
        ` : `
          <div class="desk-v1-results-posy-experiment">
            Next experiment (${esc(exp.variable)}): ${esc(exp.description)}${channel ? ` — ${esc(channel.label)}` : ''}
          </div>
          <div class="desk-v1-results-posy-actions">
            <button type="button" class="desk-v1-results-primary" data-results-experiment-accept>Set up experiment</button>
            <button type="button" class="desk-v1-results-secondary" data-results-experiment-dismiss>Not now</button>
          </div>`}
      </div>`;
  }

  // ── What went out (§7: per-VERSION rows — channel · title · status ·
  // metrics · goal contribution. Missing data reads delayed/n/a, never a
  // fabricated 0, MET-01/E12). ─────────────────────────────────────────────
  function _versionRowHTML(row, reviewMode) {
    const meta = _versionMeta(row.versionId);
    const version = meta && meta.version;
    const family = meta && meta.family;
    const channel = version ? _channel(version.channelId) : null;
    const metricText = row.metric != null
      ? `${esc(row.metric)} ${esc(row.metricLabel || '')}`
      : esc(row.metricLabel || 'n/a'); // never a bare "0" for a null metric
    const contribution = row.metric != null
      ? `<span class="desk-v1-results-version-contrib">+${esc(row.metric)} toward goal</span>`
      : `<span class="desk-v1-results-version-contrib desk-v1-results-version-contrib-dim">—</span>`;
    return `
      <div class="desk-v1-results-version-row">
        <div class="desk-v1-results-version-main">
          ${channel ? window.DeskV1Kit.channelBadge(channel, { reviewMode }) : '<span class="desk-v1-results-version-nochannel">No channel</span>'}
          <span class="desk-v1-results-version-title">${esc(family ? family.title : row.versionId)}</span>
        </div>
        ${_outcomeHTML(row.outcome)}
        <span class="desk-v1-results-version-metric">${metricText}</span>
        ${contribution}
      </div>`;
  }

  // ── Costs (§7: ads / video renders / API / other, kept separate;
  // cost-per-outcome only when EVERY category is measured, MET-02). ────────
  const COST_LABELS = { ads: 'Ads', video_renders: 'Video renders', api: 'API', other: 'Other' };
  function _costsHTML(results) {
    const costs = results.costs || {};
    const keys = Object.keys(COST_LABELS);
    const allMeasured = keys.every((k) => costs[k] != null);
    const totalMeasured = keys.reduce((sum, k) => sum + (costs[k] || 0), 0);
    const measuredOutcomes = (results.versions || []).filter((v) => v.metric != null);
    const totalOutcomes = measuredOutcomes.reduce((sum, v) => sum + v.metric, 0);
    return `
      <div class="desk-v1-results-costs">
        ${keys.map((k) => `
          <div class="desk-v1-results-cost-row"><span>${esc(COST_LABELS[k])}</span><span>${_money(costs[k])}</span></div>
        `).join('')}
        ${allMeasured && totalOutcomes > 0
          ? `<div class="desk-v1-results-cost-row desk-v1-results-cost-row-total"><span>Cost per outcome</span><span>${_money(totalMeasured / totalOutcomes)}</span></div>`
          : `<div class="desk-v1-results-cost-note">Cost per outcome hidden until every category above is measured.</div>`}
      </div>`;
  }

  // ── Diagnostics (§7: small, last, never a goal or gate). ─────────────────
  function _diagnosticsHTML(results) {
    const d = results.diagnostics;
    if (!d) return '';
    return `<div class="desk-v1-results-diagnostics">You edited ${esc(d.edited)} of ${esc(d.reviewed)} before approval.</div>`;
  }

  let _st = { dismissed: false, acceptedFamId: null };

  function _renderAll(el, campaign, results, insight) {
    el.innerHTML = `
      <div class="desk-v1-results">
        ${_goalHTML(campaign, results)}
        ${_posyHTML(campaign, insight, _st)}
        <div class="desk-v1-results-section">
          <div class="desk-v1-results-section-title">What went out</div>
          <div class="desk-v1-results-versions">
            ${(results.versions || []).map((r) => _versionRowHTML(r, campaign.rules && campaign.rules.reviewMode)).join('') ||
              '<div class="desk-v1-results-empty">Nothing has gone out yet.</div>'}
          </div>
        </div>
        <div class="desk-v1-results-section">
          <div class="desk-v1-results-section-title">Costs</div>
          ${_costsHTML(results)}
        </div>
        ${_diagnosticsHTML(results)}
      </div>`;

    const acceptBtn = el.querySelector('[data-results-experiment-accept]');
    if (acceptBtn) acceptBtn.onclick = () => {
      const cmd = _experimentCommand(campaign, insight);
      window.DeskV1Kit.commandBus.run({
        label: cmd.label,
        do: () => { cmd.do(); _st.acceptedFamId = cmd.famId; _renderAll(el, campaign, results, insight); },
        undo: () => { cmd.undo(); _st.acceptedFamId = null; _renderAll(el, campaign, results, insight); },
      });
    };
    const dismissBtn = el.querySelector('[data-results-experiment-dismiss]');
    if (dismissBtn) dismissBtn.onclick = () => { _st.dismissed = true; _renderAll(el, campaign, results, insight); };

    if (window.DeskV1Kit) {
      window.DeskV1Kit.bindInfoIcons(el, {
        'results-goal-freshness': results.goal && results.goal.freshness ? `As of ${results.goal.freshness}` : 'Freshness not reported yet.',
        'results-forecast-est': 'A projection from the current pace, not a guarantee.',
      });
    }
  }

  function deskV1RenderResults(el, params) {
    const campaignId = (params || {}).campaignId;
    const campaign = _campaign(campaignId);
    const results = _fx().results;
    if (!campaign || !results || results.campaignId !== campaignId) {
      el.innerHTML = '<div class="desk-v1-stub"><div class="desk-v1-stub-body">No results for this campaign yet.</div></div>';
      return;
    }
    _st = { dismissed: false, acceptedFamId: null };
    const insight = (_fx().resultsInsight || {})[campaignId] || null;
    _renderAll(el, campaign, results, insight);
  }

  window.deskV1RenderResults = deskV1RenderResults;
})();

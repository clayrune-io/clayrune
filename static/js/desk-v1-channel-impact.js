// MC-1152 P4 (DARK): the Channel impact module on the campaign Goal screen.
// Spec: docs/desk_v1/CHANNEL_IMPACT_SPEC.md sections 10 and 12 (P4); D1 = A.
//
// Window-bridged, no `import`. desk-v1-results.js owns one empty mount node and
// calls `window.deskV1RenderChannelImpact(mount, camp)`; nothing else here is
// reachable from that file. While the server flag `desk_impact_compare_enabled`
// is off (the default) this draws NOTHING: the status route answers
// `{enabled:false}` and the mount stays empty. It also draws nothing for the
// fixture store (no server behind it).
//
// What it shows (D1 = A): one row per campaign channel/account with that
// channel's own cached post metrics; the conversion column reads the server's
// "Not attributable by this source"; the GitHub outcomes sit in ONE separate
// block, "Repository context", and are never divided across the rows.
// A missing number is written as its reason, never as 0. Every state is text,
// so nothing depends on hover or colour.
(function () {
  const API = () => (typeof API_BASE === 'string' ? API_BASE : '');
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  let _enabled = null;
  const _seq = new Map();            // campaign id -> latest request number: a stale answer never repaints

  async function _isEnabled() {
    if (_enabled !== null) return _enabled;
    try {
      const res = await fetch(API() + '/api/desk/impact/compare/status');
      _enabled = res.ok && (await res.json()).enabled === true;
    } catch (e) { _enabled = false; }
    return _enabled;
  }

  async function _get(url) {
    const res = await fetch(API() + url);
    let body = null;
    try { body = await res.json(); } catch (e) { /* non-JSON error page */ }
    if (!res.ok) throw new Error((body && body.error) || ('HTTP ' + res.status));
    return body;
  }

  const _num = (n) => (typeof n === 'number' ? n.toLocaleString('en-US') : String(n));
  const _day = (iso) => (iso ? String(iso).slice(0, 10) : '');
  const _when = (iso) => (iso ? String(iso).replace('T', ' ').replace(/:\d\d(\.\d+)?(Z|\+00:00)$/, ' UTC') : '');
  const _words = (key) => String(key).replace(/_/g, ' ');

  // One value-or-reason cell. `cell` is {value, reason}; a null value prints its reason.
  function _cellText(cell, fallback) {
    if (cell && cell.value != null) return _num(cell.value);
    return (cell && cell.reason) || fallback || 'Not read yet';
  }

  function _windowText(w) {
    if (!w) return '';
    return `${_day(w.start)} to ${_day(w.end)} (UTC${w.granularity === 'day' ? ', whole days; the end day is not included' : ''})`;
  }

  // The two post-metric columns. 'impressions' and 'views' are the reach column;
  // everything else a post reported is the engagement breakdown.
  const REACH = ['impressions', 'views'];

  function _reachHTML(row) {
    const m = row.post_metrics || {};
    const keys = REACH.filter((k) => m[k]);
    if (!keys.length) return `<span class="desk-v1-ci-missing">${esc((row.post_metrics_coverage || {}).reason || 'No cached read')}</span>`;
    return keys.map((k) => `<div><span class="desk-v1-ci-num">${esc(_cellText(m[k]))}</span> <span class="desk-v1-ci-unit">${esc(_words(k))}</span>${m[k].value == null ? '' : _partialNote(m[k])}</div>`).join('');
  }
  function _partialNote(c) { return c.reason ? ` <span class="desk-v1-ci-missing">${esc(c.reason)}</span>` : ''; }

  function _engagementHTML(row) {
    const m = row.post_metrics || {};
    const keys = Object.keys(m).filter((k) => !REACH.includes(k));
    if (!keys.length) return `<span class="desk-v1-ci-missing">${esc(row.published_posts ? 'No cached engagement read' : 'No posts published in this period')}</span>`;
    return keys.map((k) => `<div><span class="desk-v1-ci-unit">${esc(_words(k))}</span> ${m[k].value == null ? `<span class="desk-v1-ci-missing">${esc(m[k].reason || 'Not read yet')}</span>` : `<span class="desk-v1-ci-num">${esc(_num(m[k].value))}</span>`}</div>`).join('');
  }

  function _coverageHTML(row) {
    const reads = Object.values(row.post_metrics || {}).map((c) => c.last_read_at).filter(Boolean).sort();
    if (row.platform_clicks && row.platform_clicks.last_read_at) reads.push(row.platform_clicks.last_read_at);
    const posts = row.published_posts;
    const read = reads.length ? `Oldest read ${_when(reads.sort()[0])}` : 'No cached read';
    return `${posts} published ${posts === 1 ? 'post' : 'posts'} · ${esc(read)}`;
  }

  function _badges(row) {
    const out = [];
    if (row.removed) out.push('Removed account — history kept');
    else if (!row.in_plan) out.push('Not in the plan');
    return out.map((t) => `<span class="desk-v1-ci-badge">${esc(t)}</span>`).join('');
  }

  function _rowHTML(row, convHead) {
    const conv = row.conversions || {};
    return `<tr data-ci-row="${esc(row.account_id || row.label)}">
      <th scope="row" data-label="Channel"><span class="desk-v1-ci-name">${esc(row.label)}</span>${row.platform ? ` <span class="desk-v1-ci-unit">${esc(row.platform)}</span>` : ''} ${_badges(row)}</th>
      <td data-label="Published posts">${esc(_num(row.published_posts))}</td>
      <td data-label="Impressions / views">${_reachHTML(row)}</td>
      <td data-label="Engagement">${_engagementHTML(row)}</td>
      <td data-label="Platform clicks">${esc(_cellText(row.platform_clicks))}</td>
      <td data-label="${esc(convHead)}" data-ci-conversion><span class="desk-v1-ci-missing">${esc(_cellText(conv))}</span></td>
      <td data-label="Coverage">${_coverageHTML(row)}</td>
    </tr>`;
  }

  function _definitionLine(d) {
    if (!d) return 'No conversion definition is set for this campaign.';
    return `Conversion being measured: ${_words(d.metric)}${d.unit ? ' (' + d.unit + ')' : ''} from ${d.source_id} — ${d.label || ''}`.replace(/ — $/, '');
  }

  function _repoHTML(ctx) {
    if (!ctx) return '';
    const t = ctx.target || {};
    const obs = ctx.observations || [];
    const body = obs.length ? obs.map((o) => `
      <li data-ci-repo-metric="${esc(o.metric)}">
        <span class="desk-v1-ci-name">${esc(_words(o.metric))}</span>:
        ${o.value == null ? `<span class="desk-v1-ci-missing">${esc(o.reason || 'Not available for this period')}</span>` : `<span class="desk-v1-ci-num">${esc(_num(o.value))}</span> <span class="desk-v1-ci-unit">${esc(o.unit || '')}</span>`}
        ${o.latest_total != null && o.value == null ? ` <span class="desk-v1-ci-unit">latest total ${esc(_num(o.latest_total))}</span>` : ''}
        <div class="desk-v1-ci-note">${esc(o.label || '')}</div>
        <div class="desk-v1-ci-note">Read ${esc(_when(o.observed_at))}${o.stale ? ' · <strong>Stale</strong>: older than the end of this period or the last read failed' : ''}</div>
      </li>`).join('') : `<li><span class="desk-v1-ci-missing">${esc(ctx.reason || 'No successful cached observations')}</span></li>`;
    const st = ctx.status;
    return `<section class="desk-v1-ci-repo" aria-labelledby="desk-v1-ci-repo-h" data-ci-repo>
      <h4 id="desk-v1-ci-repo-h">${esc(ctx.label)}</h4>
      <div class="desk-v1-ci-note">GitHub ${esc(t.owner || '')}/${esc(t.repo || '')} · ${esc(_windowText(ctx.requested_window))}. These are outcomes for the whole repository, from every source of visits. They are shown once, here, and are not split across the channels above.</div>
      ${st && st.status && st.status !== 'ok' ? `<div class="desk-v1-ci-warn" role="status">Source status: ${esc(st.status)}${st.reason ? ' — ' + esc(st.reason) : ''}</div>` : ''}
      <ul class="desk-v1-ci-repolist">${body}</ul>
    </section>`;
  }

  function _html(data) {
    const d = data.definition;
    const convHead = d ? `Conversions (${_words(d.metric)})` : 'Conversions';
    const rows = data.channels || [];
    const conn = data.connection || {};
    return `<div class="desk-v1-ci" data-ci>
      <div class="desk-v1-ci-head">
        <h3 id="desk-v1-ci-h">Channel impact</h3>
        <button type="button" class="btn-secondary" data-ci-refresh ${conn.state === 'available' && data.historical ? '' : 'hidden'}>Read the source now</button>
      </div>
      <div class="desk-v1-ci-note">${esc(_windowText(data.requested_window))} · ${esc(data.window_note || '')}${data.historical ? '' : ' This period is still open, so its numbers are partial.'}</div>
      <div class="desk-v1-ci-note">${esc(_definitionLine(d))}</div>
      ${conn.state && conn.state !== 'available' ? `<div class="desk-v1-ci-warn" role="status" data-ci-connection="${esc(conn.state)}">${esc(conn.message)}</div>` : ''}
      <div class="desk-v1-ci-status" role="status" aria-live="polite" data-ci-status></div>
      ${rows.length ? `<div class="desk-v1-ci-scroll"><table class="desk-v1-ci-table" aria-labelledby="desk-v1-ci-h">
        <thead><tr><th scope="col">Channel</th><th scope="col">Published posts</th><th scope="col">Impressions / views</th><th scope="col">Engagement</th><th scope="col">Platform clicks</th><th scope="col">${esc(convHead)}</th><th scope="col">Coverage</th></tr></thead>
        <tbody>${rows.map((r) => _rowHTML(r, convHead)).join('')}</tbody></table></div>
        <div class="desk-v1-ci-note">${esc(data.post_metrics_label || '')}. Counts are totals so far, not growth inside the period. No rates, rankings or winners are computed.</div>`
        : '<div class="desk-v1-ci-missing">This campaign has no channels yet.</div>'}
      ${_repoHTML(data.repository_context)}
    </div>`;
  }

  async function _refresh(mount, camp, data) {
    const status = mount.querySelector('[data-ci-status]');
    const btn = mount.querySelector('[data-ci-refresh]');
    if (typeof window.humanProofFetch !== 'function') { status.textContent = 'The passcode prompt is not available.'; return; }
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
    status.textContent = 'Reading…';
    try {
      const w = data.requested_window || {};
      const res = await window.humanProofFetch(API() + '/api/desk/impact/refresh/' + encodeURIComponent(camp.id),
        { method: 'POST', body: JSON.stringify({ start: w.start, end: w.end }) },
        { title: 'Read the source', description: 'Re-enter your dashboard passcode to read this campaign’s conversion source now. It uses the connection already saved; it does not widen it.', busyText: 'Reading…' });
      if (res === null) { status.textContent = 'Cancelled. Nothing was read.'; return; }
      if (!res.ok) throw new Error((res.body && res.body.error) || ('HTTP ' + res.status));
      await render(mount, camp);
    } catch (e) {
      status.textContent = 'Could not read the source: ' + e.message;
      btn.disabled = false;
      btn.removeAttribute('aria-busy');
    }
  }

  async function render(mount, camp) {
    if (!mount || !camp) return;
    const n = (_seq.get(camp.id) || 0) + 1;
    _seq.set(camp.id, n);
    if (!window.DeskV1Store || !window.DeskV1Store.live() || !(await _isEnabled())) { mount.innerHTML = ''; return; }
    mount.innerHTML = '<div class="desk-v1-ci-note" role="status">Loading channel impact…</div>';
    let data;
    try { data = await _get('/api/desk/impact/compare/' + encodeURIComponent(camp.id)); }
    catch (e) {
      if (_seq.get(camp.id) !== n || !mount.isConnected) return;
      mount.innerHTML = `<div class="desk-v1-ci"><h3>Channel impact</h3><div class="desk-v1-ci-warn" role="alert">Could not load channel impact: ${esc(e.message)}</div><button type="button" class="btn-secondary" data-ci-retry>Try again</button></div>`;
      const retry = mount.querySelector('[data-ci-retry]');
      if (retry) retry.onclick = () => render(mount, camp);
      return;
    }
    if (_seq.get(camp.id) !== n || !mount.isConnected) return;
    mount.innerHTML = _html(data);
    const btn = mount.querySelector('[data-ci-refresh]');
    if (btn) btn.onclick = () => _refresh(mount, camp, data);
  }

  window.deskV1RenderChannelImpact = render;
})();

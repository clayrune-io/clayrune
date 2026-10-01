// Desk v1 (MC-977) — R2-15 (IA revision 2 §4.1 row "① Goal", §8 R2-15, §10
// outcome learning loop): the Retro section that lives at the foot of the
// Goal stop's effectiveness panel. Mounted by desk-v1-results.js through a
// backward-compatible seam (`window.deskV1RenderRetroSection`), same
// "undefined until this file loads" convention desk-v1-rules.js/-how.js
// already established for their own campaign-page hooks — desk-v1-results.js
// itself never branches on retro internals.
//
// Fixtures only (ground rule 3, same as desk-v1-campaign.js's own banner):
// every Confirm/Edit/Reject/Don't-suggest-again mutates the in-memory
// `DeskV1Fixtures.playbook` object directly through DeskV1Kit.commandBus.
// The real `/api/desk/findings/*` routes (R1-L, merged) exist for whenever a
// later ticket wires the Desk off fixtures entirely; this ticket's ids
// ('F1'..'F4') are frontend-only and were never posted through them, so
// calling those routes here would 404 against an empty server-side store.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function _fx() { return window.DeskV1Store.state(); }
  function _campaign(id) { return (_fx().campaigns || []).find((c) => c.id === id) || null; }
  function _playbook() { const fx = _fx(); fx.playbook = fx.playbook || { findings: [], rejections: [] }; return fx.playbook; }
  function _finding(id) { return (_playbook().findings || []).find((f) => f.id === id) || null; }
  function _money(n) { return n == null ? 'n/a' : '$' + (Math.round(n * 100) / 100).toFixed(2); }

  // §10.4: "the Retro section (desk-v1-retro.js) looks up `camp.id + ':' +
  // camp.term.index`" (desk-v1-fixtures.js's own RETROS comment) — one key
  // per closed OR in-progress term, so a `long`-horizon campaign's later
  // term never collides with an earlier one's retro.
  function _retroKey(camp) { return camp.id + ':' + ((camp.term && camp.term.index) || 1); }
  function _retroFor(camp) { return (_fx().retros || {})[_retroKey(camp)] || null; }

  // Ledger rows this retro's per-post grid covers — same campaign + term,
  // published order (oldest first, matching the order a CSV export would
  // list them in, so a pasted column lines up with the rows on screen).
  function _ledgerRows(camp, retro) {
    const term = (retro && retro.term) || (camp.term && camp.term.index) || 1;
    return (_fx().ledger || [])
      .filter((r) => r.campaign_id === camp.id && r.term === term)
      .slice()
      .sort((a, b) => (a.published_at < b.published_at ? -1 : 1));
  }
  function _outcomeValue(row, metric) {
    const hit = (row.outcomes || []).slice().reverse().find((o) => o.metric === metric);
    return hit ? hit.value : null;
  }

  // ── §10.5 guardrail 1 (authority guard), the Desk-specific half: a
  // machine-written `maybe_why` may never smuggle in a bounds instruction.
  // Mirrors the spec's own regex description ("`raise|increase|more` near
  // `cadence|budget|spend|cap|ceiling|accounts|approval`") — checked in both
  // word orders since "cadence... could go higher" reads the same threat as
  // "raise the cadence". A hit drops the line entirely rather than showing a
  // redacted stub, so nothing on screen hints at what was withheld.
  const _BOUNDS_WORDS = 'cadence|budget|spend|cap|ceiling|accounts|approval';
  const _RAISE_WORDS = 'raise|increase|more';
  const _BOUNDS_PATTERN = new RegExp(
    `(?:${_RAISE_WORDS})[\\s\\S]{0,60}(?:${_BOUNDS_WORDS})|(?:${_BOUNDS_WORDS})[\\s\\S]{0,60}(?:${_RAISE_WORDS})`, 'i');
  function _safeMaybeWhy(text) {
    if (!text) return null;
    return _BOUNDS_PATTERN.test(text) ? null : text;
  }

  // 'x:ron' -> "𝕏 (Ron)"; 'linkedin:clayrune_page' -> "in (Clayrune Page)" —
  // presentation only, local to this file (the finding schema's `account`
  // field, §10.2, is a plain string, not a channel id).
  const _PLATFORM_GLYPH = { x: '\u{1D54F}', linkedin: 'in', blog: 'Blog' };
  function _accountLabel(account) {
    if (!account) return null;
    const [platform, who] = String(account).split(':');
    const glyph = _PLATFORM_GLYPH[platform] || platform;
    const name = who ? who.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()) : '';
    return name ? `${glyph} (${name})` : glyph;
  }

  // §10.2's own worked example, reproduced: "On 𝕏 (Ron), Tue/Thu 08-10 got
  // 2.1× the clicks per post of other slots (3 campaigns, n=41, medium)."
  // Code renders the sentence from the structured finding; the model never
  // supplies wording beyond the optional `maybe_why` (§10.1: "the model only
  // words the retro summary and an optional one-line maybe_why").
  function _findingSentence(f) {
    const dir = (f.effect && f.effect.direction) || 'a>b';
    const winner = dir === 'a>b' ? f.arms.a : f.arms.b;
    const loser = dir === 'a>b' ? f.arms.b : f.arms.a;
    const ratio = f.effect && f.effect.ratio;
    const campaignCount = new Set((f.evidence || []).map((e) => e.campaign_id)).size;
    const acct = _accountLabel(f.account);
    const scope = acct ? `On ${acct}, ` : '';
    return `${scope}${winner} got ${esc(ratio)}× the ${esc(f.metric || 'clicks')} per post of ${loser} ` +
      `(${campaignCount} campaign${campaignCount === 1 ? '' : 's'}, n=${esc(f.n_total)}, ${esc(f.confidence)}).`;
  }

  function _evidenceKey(evidence) {
    const pairs = Array.from(new Set((evidence || []).map((e) => `${e.campaign_id}:${e.term}`))).sort();
    return pairs.join(',');
  }

  // ── dimension table (§10.1) — reads the retro's OWN precomputed rows
  // (`RETRO_DIMENSIONS` on kit.js supplies only the label/note; the verdict,
  // arms and effect are the retro's, same "code computes, this file only
  // renders" split the whole outcome loop is built on). ────────────────────
  function _dimensionRowHTML(dim) {
    const meta = (DeskV1Kit.RETRO_DIMENSIONS && DeskV1Kit.RETRO_DIMENSIONS[dim.dimension]) || { label: dim.dimension };
    let detail;
    if (dim.verdict === 'finding') {
      const dir = (dim.effect && dim.effect.direction) || 'a>b';
      const winner = dir === 'a>b' ? dim.arms.a : dim.arms.b;
      const loser = dir === 'a>b' ? dim.arms.b : dim.arms.a;
      detail = `${esc(winner)} ${esc(dim.effect && dim.effect.ratio)}× ${esc(loser)} (n=${esc(dim.n_total)}, ${esc(dim.confidence)})`;
    } else {
      detail = esc(dim.text || '');
    }
    return `<div class="desk-v1-retro-dim-row">
      <span class="desk-v1-retro-dim-label">${esc(meta.label)}</span>
      <span class="desk-v1-retro-dim-detail">${detail}</span>
      ${meta.note ? `<span class="desk-v1-retro-dim-note">${esc(meta.note)}</span>` : ''}
    </div>`;
  }

  // ── per-post number grid (§10.7: "a grid, one row per published post ...
  // paste-from-CSV accepted"). Values fill in ledger-row (published) order;
  // extra pasted values past the row count are ignored, short input leaves
  // the remaining rows at "No per-post numbers yet" (never invents a 0,
  // MET-01's same rule the rest of the Desk already keeps). ────────────────
  function _gridHTML(camp, retro) {
    const rows = _ledgerRows(camp, retro);
    if (!rows.length) return '';
    const metric = retro.metric || 'clicks';
    const rowsHTML = rows.map((r) => {
      const v = _outcomeValue(r, metric);
      return `<div class="desk-v1-retro-grid-row">
        <span class="desk-v1-retro-grid-date">${esc(String(r.published_at).slice(0, 10))}</span>
        <span class="desk-v1-retro-grid-format">${esc(r.format || '')}</span>
        <span class="desk-v1-retro-grid-value">${v == null ? 'No per-post numbers yet' : esc(v)}</span>
      </div>`;
    }).join('');
    return `
      <div class="desk-v1-retro-grid" data-retro-grid>
        <div class="desk-v1-retro-grid-head">Per-post ${esc(metric)} · ${rows.length} posts</div>
        ${rowsHTML}
        <div class="desk-v1-retro-paste">
          <textarea class="desk-v1-rules-textarea desk-v1-retro-pastearea" data-retro-paste
            placeholder="Paste ${rows.length} numbers, one per line or comma-separated, in the order above."></textarea>
          <button type="button" class="desk-v1-retro-btn" data-retro-paste-fill>Fill grid</button>
        </div>
      </div>`;
  }

  function _fillGridFromPaste(camp, retro, text, hostEl) {
    const rows = _ledgerRows(camp, retro);
    const metric = retro.metric || 'clicks';
    const values = String(text || '').split(/[\n,]+/).map((s) => s.trim()).filter((s) => s !== '').map(Number);
    if (!values.length) return;
    const n = Math.min(values.length, rows.length);
    const prevOutcomes = rows.slice(0, n).map((r) => (r.outcomes || []).slice());
    DeskV1Kit.commandBus.run({
      label: `Filled ${n} per-post ${metric} value${n === 1 ? '' : 's'}`,
      do: () => {
        for (let i = 0; i < n; i++) {
          const row = rows[i];
          row.outcomes = row.outcomes || [];
          const existing = row.outcomes.find((o) => o.metric === metric);
          if (existing) existing.value = values[i];
          else row.outcomes.push({ metric, value: values[i], at: row.published_at, source: 'manual' });
        }
        _renderRetroSection(hostEl, camp);
      },
      undo: () => {
        for (let i = 0; i < n; i++) rows[i].outcomes = prevOutcomes[i];
        _renderRetroSection(hostEl, camp);
      },
    });
  }

  // ── proposed findings (§10.2/§10.4: Confirm / Edit / Reject / Don't
  // suggest again). `retro.findings` names ids still `state:'proposed'` at
  // load time (fixtures.js's own comment on RETRO_CLOSED_1) — filtered again
  // here defensively so a finding this session already confirmed/rejected
  // (mutated in place, same object the array id points at) drops off the
  // list on the very next render without needing the id list rewritten. ────
  function _proposedFindings(retro) {
    return (retro.findings || []).map(_finding).filter((f) => f && f.state === 'proposed');
  }

  function _findingCardHTML(f, editing) {
    const maybeWhy = _safeMaybeWhy(f.maybe_why);
    return `<div class="desk-v1-retro-finding" data-finding-id="${esc(f.id)}">
      ${editing
        ? `<textarea class="desk-v1-rules-textarea desk-v1-retro-finding-editarea" data-finding-editarea>${esc(f.edited_text || _findingSentence(f).replace(/<[^>]+>/g, ''))}</textarea>
           <div class="desk-v1-retro-finding-actions">
             <button type="button" class="desk-v1-retro-btn desk-v1-retro-btn--primary" data-finding-edit-save>Save & Confirm</button>
             <button type="button" class="desk-v1-retro-finding-cancel" data-finding-edit-cancel>Cancel</button>
           </div>`
        : `<div class="desk-v1-retro-finding-text" data-finding-text>${_findingSentence(f)}</div>
           ${maybeWhy ? `<div class="desk-v1-retro-finding-why">${esc(maybeWhy)}</div>` : ''}
           <div class="desk-v1-retro-finding-actions">
             <button type="button" class="desk-v1-retro-btn desk-v1-retro-btn--primary" data-finding-confirm>Confirm</button>
             <button type="button" class="desk-v1-retro-btn" data-finding-edit>Edit</button>
             <button type="button" class="desk-v1-retro-btn desk-v1-retro-btn--danger" data-finding-reject>Reject</button>
             <button type="button" class="desk-v1-retro-btn" data-finding-dontsuggest>Don’t suggest again</button>
           </div>`}
    </div>`;
  }

  let _editingId = null;

  function _findingsHTML(retro) {
    const proposed = _proposedFindings(retro);
    if (!proposed.length) {
      return `<div class="desk-v1-retro-findings"><div class="desk-v1-stub-inline">0 proposed findings.</div></div>`;
    }
    return `<div class="desk-v1-retro-findings">
        <div class="desk-v1-retro-findings-head">${proposed.length} proposed finding${proposed.length === 1 ? '' : 's'}</div>
        ${proposed.map((f) => _findingCardHTML(f, f.id === _editingId)).join('')}
      </div>`;
  }

  function _confirmFinding(f, editedText, hostEl, camp) {
    const prev = { state: f.state, origin: f.origin, decided_at: f.decided_at, decided_by: f.decided_by, edited_text: f.edited_text };
    DeskV1Kit.commandBus.run({
      label: `Confirmed finding ${f.id}`,
      do: () => {
        f.state = 'confirmed'; f.origin = 'interactive';
        f.decided_at = new Date().toISOString(); f.decided_by = 'ron';
        if (editedText != null) f.edited_text = editedText;
        _editingId = null;
        _renderRetroSection(hostEl, camp);
      },
      undo: () => { Object.assign(f, prev); _renderRetroSection(hostEl, camp); },
    });
  }

  // Reject (durable against this evidence only) vs Don't suggest again
  // (permanent suppression) — same split as `mc.desk.reject_finding` /
  // `dont_suggest_again` (§10.5.3); both record a rejection, `permanent`
  // flags which. `Undo reject` (the only thing that lifts a permanent
  // suppression) lives on the project page's Playbook, R2-16 — not this
  // section.
  function _rejectFinding(f, hostEl, camp, permanent) {
    const prev = { state: f.state, decided_at: f.decided_at, decided_by: f.decided_by };
    const rejections = _playbook().rejections;
    let added;
    DeskV1Kit.commandBus.run({
      label: permanent ? `Won’t suggest finding ${f.id} again` : `Rejected finding ${f.id}`,
      do: () => {
        f.state = 'rejected'; f.decided_at = new Date().toISOString(); f.decided_by = 'ron';
        added = {
          project_id: f.project_id, dimension: f.dimension, arms: f.arms,
          direction: (f.effect || {}).direction, evidence_key: _evidenceKey(f.evidence),
          rejected_at: f.decided_at, permanent: !!permanent,
        };
        rejections.push(added);
        _renderRetroSection(hostEl, camp);
      },
      undo: () => {
        Object.assign(f, prev);
        const idx = rejections.indexOf(added);
        if (idx >= 0) rejections.splice(idx, 1);
        _renderRetroSection(hostEl, camp);
      },
    });
  }

  function _bindFindings(el, retro, hostEl, camp) {
    el.querySelectorAll('[data-finding-confirm]').forEach((btn) => {
      btn.onclick = () => {
        const f = _finding(btn.closest('[data-finding-id]').dataset.findingId);
        if (f) _confirmFinding(f, null, hostEl, camp);
      };
    });
    el.querySelectorAll('[data-finding-reject]').forEach((btn) => {
      btn.onclick = () => {
        const f = _finding(btn.closest('[data-finding-id]').dataset.findingId);
        if (f) _rejectFinding(f, hostEl, camp, false);
      };
    });
    el.querySelectorAll('[data-finding-dontsuggest]').forEach((btn) => {
      btn.onclick = () => {
        const f = _finding(btn.closest('[data-finding-id]').dataset.findingId);
        if (f) _rejectFinding(f, hostEl, camp, true);
      };
    });
    el.querySelectorAll('[data-finding-edit]').forEach((btn) => {
      btn.onclick = () => {
        _editingId = btn.closest('[data-finding-id]').dataset.findingId;
        _renderRetroSection(hostEl, camp);
      };
    });
    el.querySelectorAll('[data-finding-edit-cancel]').forEach((btn) => {
      btn.onclick = () => { _editingId = null; _renderRetroSection(hostEl, camp); };
    });
    el.querySelectorAll('[data-finding-edit-save]').forEach((btn) => {
      btn.onclick = () => {
        const card = btn.closest('[data-finding-id]');
        const f = _finding(card.dataset.findingId);
        const text = card.querySelector('[data-finding-editarea]').value.trim();
        if (f && text) _confirmFinding(f, text, hostEl, camp);
      };
    });
  }

  // ── section body ───────────────────────────────────────────────────────
  const _shownInterim = new Set();

  function _bodyHTML(camp, retro) {
    const goal = retro.goal || {};
    const spend = retro.spend || {};
    const closed = retro.status === 'closed';
    return `
      <div class="desk-v1-retro-goal">${esc(goal.actual)} of ${esc(goal.target)} ${esc(goal.metric || '')}</div>
      <div class="desk-v1-retro-summary">${esc(retro.summary || '')}</div>
      <div class="desk-v1-retro-spend">
        Spend: ${esc(_money(spend.total))}${spend.ceiling != null ? ` of ${esc(_money(spend.ceiling))}` : ''}
        ${spend.cost_per_outcome != null ? ` · cost per outcome ${esc(_money(spend.cost_per_outcome))}` : ''}
      </div>
      <div class="desk-v1-retro-dims">${(retro.dimensions || []).map(_dimensionRowHTML).join('')}</div>
      ${closed ? _gridHTML(camp, retro) : ''}
      ${closed ? _findingsHTML(retro) : `<div class="desk-v1-stub-inline">Interim: numbers only — findings are proposed once this term closes.</div>`}
    `;
  }

  function _renderRetroSection(hostEl, camp) {
    if (!hostEl) return;
    const retro = _retroFor(camp);
    if (!retro) { hostEl.innerHTML = ''; return; }
    const closed = retro.status === 'closed';
    if (!closed && !_shownInterim.has(camp.id)) {
      hostEl.innerHTML = `
        <div class="desk-v1-retro" data-retro-section>
          <div class="desk-v1-retro-head">
            <span class="desk-v1-retro-title">Retro</span>
            <button type="button" class="desk-v1-retro-btn desk-v1-retro-btn--primary" data-retro-run>Run retro now</button>
          </div>
        </div>`;
      hostEl.querySelector('[data-retro-run]').onclick = () => {
        _shownInterim.add(camp.id);
        _renderRetroSection(hostEl, camp);
      };
      return;
    }
    hostEl.innerHTML = `
      <div class="desk-v1-retro" data-retro-section>
        <div class="desk-v1-retro-head">
          <span class="desk-v1-retro-title">Retro · Term ${esc(retro.term)}</span>
          <span class="desk-v1-retro-status" data-status="${esc(retro.status)}">${closed ? 'Closed' : 'Interim'}</span>
        </div>
        ${_bodyHTML(camp, retro)}
      </div>`;
    const gridBtn = hostEl.querySelector('[data-retro-paste-fill]');
    if (gridBtn) gridBtn.onclick = () => {
      const ta = hostEl.querySelector('[data-retro-paste]');
      _fillGridFromPaste(camp, retro, ta.value, hostEl);
    };
    _bindFindings(hostEl, retro, hostEl, camp);
  }

  window.deskV1RenderRetroSection = _renderRetroSection;
  // Seam for the project page's Playbook (R2-16): the same code-rendered
  // sentence, or Ron's own wording when he edited it on Confirm.
  window.deskV1FindingSentence = function (f) { return f.edited_text ? esc(f.edited_text) : _findingSentence(f); };
  // Additive seam for R2-2 (Home's Needs-you column)/R2-16 (project Playbook)
  // to read without this file touching either of their own: "Retro ready: n
  // findings to confirm" (§10.4's Home row) is exactly this count.
  window.deskV1RetroFindingsToConfirm = function (campaignId) {
    const camp = _campaign(campaignId);
    if (!camp) return 0;
    const retro = _retroFor(camp);
    if (!retro) return 0;
    return _proposedFindings(retro).length;
  };
})();

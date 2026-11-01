// ── Hivemind review / roles view (BUILTINS-4 HR-8a) ──────────────────────────
// Pure HTML builders over the HR-8b `roles` projection (mc/hivemind_run_roles.py,
// spec docs/BUILTINS_4_HIVEMIND_ROSTER_SPEC.md "HR-8"). No fetches, no state:
// hivemind.js / cross-hivemind.js hand in the response they already loaded.
//
// Fail-closed display rule: the only green in this file is the `ok` tone, and
// it is reachable ONLY through stageResolve() / reviewGreen() below. "Workers
// done", changes_requested, provisional, stale and missing evidence are all
// non-green, and the words say so (glyph + label, never colour alone).
//
// A roster run is manifest.config.roster_version === 1. Legacy runs carry no
// `roles`; they are labelled as such rather than left blank.
//
// ES module -> shared through window.hmReview (like its siblings).

const E = (s) => (typeof window.esc === 'function'
  ? window.esc(s)
  : String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'));
const str = (v) => (typeof v === 'string' ? v : (v == null ? '' : String(v)));
const arr = (v) => (Array.isArray(v) ? v : []);
const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});

const STAGES = {
  workers_running: { label: 'Workers running', tone: 'info', glyph: '●',
    line: 'Workers are still running. Nothing has been integrated or reviewed.' },
  workers_complete: { label: 'Workers done · review pending', tone: 'warn', glyph: '⏳',
    line: 'All workers finished, but integration and independent review have not completed. Nothing is accepted.' },
  workers_failed: { label: 'Workers failed', tone: 'bad', glyph: '✖',
    line: 'At least one worker failed. Nothing is accepted.' },
  integrating: { label: 'Integrating', tone: 'info', glyph: '●',
    line: 'Worker branches are being merged and checked. Nothing is accepted yet.' },
  review_pending: { label: 'Review pending', tone: 'warn', glyph: '⏳',
    line: 'A merged candidate is waiting for independent review. Not accepted.' },
  held: { label: 'Held', tone: 'bad', glyph: '✖',
    line: 'Integration is held and later merges are stopped. Not accepted.' },
  accepted: { label: 'Accepted', tone: 'ok', glyph: '✔',
    line: 'Every code workstream was merged and passed independent review of the exact merged commit.' },
  analysis_only: { label: 'Analysis only · no code to review', tone: 'muted', glyph: '○',
    line: 'No workstream produced a branch, so there is no code to integrate or review.' },
  no_changes: { label: 'No changes merged', tone: 'muted', glyph: '○',
    line: 'Integration finished but no branch added any change, so there was nothing to review.' },
  unverified: { label: 'Unverified', tone: 'warn', glyph: '?',
    line: 'The run reports a finish, but the recorded evidence does not prove it was reviewed and accepted.' },
};

const OUTCOMES = {
  not_started: ['Not started', 'muted'], queued: ['Queued', 'muted'], in_progress: ['Integrating', 'info'],
  provisional: ['Merged · not accepted', 'warn'], accepted: ['Accepted', 'ok'],
  skipped: ['Skipped · no change', 'muted'], failed: ['Failed · rolled back', 'bad'],
  held: ['Held', 'bad'], unverified: ['Unverified', 'warn'], not_applicable: ['Analysis only · no branch', 'muted'],
};

const REVIEW_STATES = {
  '': ['Review not recorded', 'warn'], pending: ['Review pending', 'warn'], pass: ['Passed', 'ok'],
  changes_requested: ['Changes requested', 'bad'], held: ['Review held', 'bad'],
  timed_out: ['Review timed out', 'bad'], cancelled: ['Review cancelled', 'bad'],
};

const HOLD_KINDS = {
  smoke_failed: 'A named check failed', merge_conflict: 'Merge conflict', worker_dirty: 'Worker left uncommitted changes',
  missing_branch: 'Worker branch missing', standards: 'Standards check failed', merge_error: 'Merge error',
  git: 'Git error', changes_requested: 'Reviewer requested changes', review_timed_out: 'Review timed out',
  review_cancelled: 'Review cancelled', review_held: 'Reviewer held the merge', other: 'Held',
};

const GLYPH = { ok: '✔', warn: '⏳', bad: '✖', info: '●', muted: '○' };

function isRoster(m) { return obj(obj(m).config).roster_version === 1; }

// Resolve the stage the UI is allowed to show. `ok` survives only if the whole
// response agrees; a server-side "accepted" the evidence contradicts is shown
// as unverified, never green.
function stageResolve(roles) {
  const r = obj(roles);
  let stage = Object.prototype.hasOwnProperty.call(STAGES, r.stage) ? r.stage : 'unverified';
  const reasons = arr(r.unverified_reasons).map(str).filter(Boolean);
  if (!Object.prototype.hasOwnProperty.call(STAGES, r.stage)) reasons.push('Unrecognised review stage "' + str(r.stage) + '".');
  if (stage === 'accepted') {
    const wsBad = arr(r.workstreams).some((w) => {
      const i = obj(obj(w).integration);
      return i.stale === true || !['accepted', 'skipped', 'not_applicable'].includes(i.outcome);
    });
    if (r.accepted !== true || reasons.length || wsBad) {
      if (r.accepted !== true) reasons.push('Server did not mark the run accepted.');
      if (wsBad) reasons.push('A workstream is stale or not in an accepted outcome.');
      stage = 'unverified';
    }
  } else if (r.accepted === true) {
    reasons.push('Server flagged accepted without an accepted stage; ignored.');
  }
  return { stage, def: STAGES[stage], reasons };
}

// A single review card is green only on an exact, passing, non-stale review.
function reviewGreen(rv, ws, stage) {
  const i = obj(obj(ws).integration);
  return stage !== 'unverified' && obj(rv).state === 'accepted' && obj(rv).verdict === 'pass'
    && obj(rv).review_state === 'pass' && !!str(obj(rv).reviewed_commit) && i.stale !== true;
}

function badge(tone, text, extra) {
  return '<span class="hmr-badge hmr-tone-' + E(tone) + '"' + (extra || '') + '>'
    + '<span class="hmr-glyph" aria-hidden="true">' + (GLYPH[tone] || '') + '</span>' + E(text) + '</span>';
}

function stageBadgeHTML(roles) {
  const { stage, def } = stageResolve(roles);
  return badge(def.tone, def.label, ' data-hmr-stage="' + E(stage) + '"');
}

// The manifest status badge. For roster runs a bare "completed" is replaced:
// it only means the workers finished, which the label has to say.
function statusBadgeHTML(status, roles, effStatus) {
  const shown = effStatus || status;
  if (roles && shown === 'completed' && stageResolve(roles).stage !== 'accepted') {
    return '<span class="hm-status-badge hmr-workers-done" title="Manifest status: completed. The workers finished; this is not review acceptance.">workers done</span>';
  }
  return '<span class="hm-status-badge ' + E(shown) + '">' + E(shown) + '</span>';
}

function engineText(e) {
  const x = obj(e);
  if (!x.provider) return '';
  return [x.provider, x.model, x.effort].map(str).filter(Boolean).join(' · ');
}

function shortId(s) { s = str(s); return s.length > 12 ? s.slice(0, 12) + '…' : s; }

function fmtTime(v) {
  if (v == null || v === '') return '';
  const d = typeof v === 'number' ? new Date(v * 1000) : new Date(str(v));
  return isNaN(d.getTime()) ? str(v) : d.toISOString().replace('T', ' ').slice(0, 16) + 'Z';
}

function kv(label, valueHTML, cls) {
  if (valueHTML === '' || valueHTML == null) return '';
  return '<div class="hmr-kv-row"><dt>' + E(label) + '</dt><dd' + (cls ? ' class="' + cls + '"' : '') + '>' + valueHTML + '</dd></div>';
}

function engineChip(e) {
  const t = engineText(e);
  return t ? '<span class="hmr-engine" title="Effective engine for this attempt">' + E(t) + '</span>'
    : '<span class="hmr-engine hmr-engine-none" title="No engine has been resolved yet">engine not resolved</span>';
}

// Compact "who runs what" line for list rows.
function rosterLineHTML(hm) {
  const s = obj(hm._roles || hm.roles);
  const chars = arr(s.requested_characters).concat(arr(s.workstreams).map((w) => obj(w).requested_character))
    .map(str).filter(Boolean);
  const uniq = [...new Set(chars)];
  const reviewer = str(obj(s.reviewer).requested_character || s.reviewer_character);
  const parts = [];
  if (uniq.length) parts.push('<span class="hmr-line-part"><b>Workers</b> ' + uniq.map(E).join(', ') + '</span>');
  if (reviewer) parts.push('<span class="hmr-line-part"><b>Reviewer</b> ' + E(reviewer) + '</span>');
  const engines = [...new Set(arr(s.workstreams).map((w) => engineText(obj(w).effective_engine)).filter(Boolean))];
  if (engines.length) parts.push('<span class="hmr-line-part"><b>Engines</b> ' + engines.map(E).join(', ') + '</span>');
  return parts.length ? '<div class="hmr-line">' + parts.join('') + '</div>' : '';
}

// List row chip(s): stage for roster runs, an explicit label for legacy ones.
function listChipHTML(hm) {
  if (isRoster(hm)) {
    const roles = hm._roles || hm.roles;
    if (!roles) return '<div class="hmr-list-chip">' + badge('warn', 'Review state unavailable') + '</div>';
    return '<div class="hmr-list-chip">' + stageBadgeHTML(roles) + rosterLineHTML(hm) + '</div>';
  }
  return '<div class="hmr-list-chip">' + badge('muted', 'Legacy run · no reviewer',
    ' title="This hivemind predates roster reviews: ‘completed’ means the workers finished, nothing was independently reviewed."') + '</div>';
}

// Per-workstream outcome badge. An `accepted` outcome is green only when a
// review in the same response proves it (reviewGreen); otherwise it is shown
// as accepted-without-evidence, never green.
function outcomeBadge(w, roles) {
  const i = obj(w.integration);
  let [label, tone] = OUTCOMES[i.outcome] || ['Unknown outcome', 'warn'];
  if (i.stale === true) { label = 'Stale · ' + label; tone = 'warn'; }
  else if (i.outcome === 'accepted') {
    const { stage } = stageResolve(roles);
    const proven = arr(obj(roles).reviews).some((rv) => obj(rv).ws_id === w.id && reviewGreen(rv, w, stage));
    if (!proven) { label = 'Accepted · no passing review shown'; tone = 'warn'; }
  }
  return badge(tone, label, ' data-hmr-outcome="' + E(i.outcome) + '"');
}

function wsRow(w, roles) {
  const i = obj(w.integration);
  const stale = i.stale === true;
  return '<div class="hmr-ws" data-hmr-ws="' + E(w.id) + '">'
    + '<div class="hmr-ws-head"><span class="hmr-ws-title">' + E(w.title || w.id) + '</span>'
    + outcomeBadge(w, roles) + '</div>'
    + '<div class="hmr-ws-meta"><span class="hmr-char" title="Requested character">' + E(w.requested_character || 'no character') + '</span>'
    + engineChip(w.effective_engine)
    + (w.assignment_attempt > 1 ? '<span class="hmr-attempt">attempt ' + E(w.assignment_attempt) + '</span>' : '') + '</div>'
    + (stale ? '<div class="hmr-note hmr-tone-warn">This workstream moved after integration pinned it; the review below no longer covers its current state.</div>' : '')
    + (i.reason ? '<div class="hmr-note">' + E(i.reason) + '</div>' : '') + '</div>';
}

function reviewCard(rv, wsById, stage) {
  rv = obj(rv);
  const ws = wsById[rv.ws_id];
  const green = reviewGreen(rv, ws, stage);
  const [rlabel, rtone] = REVIEW_STATES[str(rv.review_state)] || ['Review state unknown', 'warn'];
  const head = green ? badge('ok', 'Accepted · reviewed commit passed')
    : badge(rv.state === 'provisional' || rv.state === 'accepted' ? (rtone === 'ok' ? 'warn' : rtone) : 'warn',
      rv.state === 'accepted' ? 'Not verified as accepted' : 'Provisional · not accepted');
  const checks = obj(rv.checks);
  const named = arr(checks.named);
  let checksHTML;
  if (!rv.checks) checksHTML = '<span class="hmr-muted">No checks recorded yet.</span>';
  else if (!named.length) checksHTML = '<span class="hmr-muted">' + (checks.ran ? 'Checks ran; none were named.' : 'Checks have not run.') + '</span>';
  else checksHTML = '<ul class="hmr-checks">' + named.map((c) =>
    '<li class="' + (obj(c).ok ? 'hmr-check-ok' : 'hmr-check-fail') + '"><span aria-hidden="true">' + (obj(c).ok ? '✔' : '✖') + '</span> '
    + E(obj(c).name || '(unnamed check)') + ' <b>' + (obj(c).ok ? 'passed' : 'FAILED') + '</b></li>').join('') + '</ul>'
    + (checks.ran ? '' : '<span class="hmr-muted">Checks have not run.</span>');
  if (checks.note) checksHTML += '<div class="hmr-muted">' + E(checks.note) + '</div>';
  const who = obj(rv.reviewer);
  const reviewerHTML = who.session_id || who.character
    ? E(who.character || 'reviewer') + (engineText(who.engine) ? ' · ' + E(engineText(who.engine)) : '')
      + (who.session_id ? ' <span class="hmr-sid" title="' + E(who.session_id) + '">' + E(shortId(who.session_id)) + '</span>' : '')
    : '<span class="hmr-muted">No reviewer has started this review.</span>';
  const commit = str(rv.reviewed_commit);
  return '<div class="hmr-review" data-hmr-review="' + E(rv.ws_id) + '">'
    + '<div class="hmr-ws-head"><span class="hmr-ws-title">' + E(obj(ws).title || rv.ws_id) + '</span>' + head + '</div>'
    + '<dl class="hmr-kv">'
    + kv('Reviewer', reviewerHTML)
    + kv('Verdict', rtone === 'ok' && !green
      ? badge('warn', rlabel + ' · not verified as current')
      : badge(rtone, rlabel))
    + kv('Reviewed commit', commit ? '<code class="hmr-sha">' + E(commit) + '</code>' : '<span class="hmr-muted">none — nothing was merged for this review</span>')
    + kv('Checks', checksHTML)
    + (rv.hold_reason ? kv('Hold reason', E(rv.hold_reason), 'hmr-reason') : '')
    + kv('Merged from', rv.incoming_branch ? E(rv.incoming_branch) + (rv.incoming_tip ? ' @ <code class="hmr-sha">' + E(rv.incoming_tip) + '</code>' : '') : '')
    + kv('Before merge', rv.pre_merge_sha ? '<code class="hmr-sha">' + E(rv.pre_merge_sha) + '</code>' : '')
    + kv('Candidate', rv.candidate_key ? '<code class="hmr-sha">' + E(rv.candidate_key) + '</code>' : '')
    + kv('Phase', E(rv.phase))
    + kv('Review deadline', E(fmtTime(rv.deadline_at)))
    + kv('Report', rv.report_path ? '<code class="hmr-sha">' + E(rv.report_path) + '</code>' : '')
    + kv('Recorded', E(fmtTime(rv.recorded_at)))
    + '</dl></div>';
}

function holdHTML(hold) {
  const h = obj(hold);
  const failed = arr(h.checks).filter((c) => obj(c).ok !== true);
  return '<div class="hmr-hold" role="alert"><div class="hmr-hold-head">'
    + badge('bad', 'Held · ' + (HOLD_KINDS[h.kind] || HOLD_KINDS.other), ' data-hmr-hold="' + E(h.kind) + '"') + '</div>'
    + (h.reason ? '<div class="hmr-reason">' + E(h.reason) + '</div>' : '<div class="hmr-muted">No reason was recorded.</div>')
    + (h.ws_id ? '<div class="hmr-muted">Workstream <code>' + E(h.ws_id) + '</code></div>' : '')
    + (failed.length ? '<ul class="hmr-checks">' + failed.map((c) => '<li class="hmr-check-fail"><span aria-hidden="true">✖</span> '
      + E(obj(c).name || '(unnamed check)') + ' <b>FAILED</b></li>').join('') + '</ul>' : '')
    + '</div>';
}

// Overview-tab panel for the dashboard: stage, counts (workers and reviewers
// separately), hold, per-workstream outcome, and every review card.
function overviewPanelHTML(data) {
  const d = obj(data);
  const roles = d.roles;
  if (!roles) {
    if (isRoster(d.manifest)) {
      return '<section class="hmr-panel" data-hmr-panel="unavailable"><div class="hmr-panel-head">' + badge('warn', 'Review state unavailable') + '</div>'
        + '<div class="hmr-line-text">This is a roster run, but the server returned no review state. Treat it as not reviewed.</div></section>';
    }
    return '<section class="hmr-panel" data-hmr-panel="legacy"><div class="hmr-panel-head">' + badge('muted', 'Legacy run · no reviewer') + '</div>'
      + '<div class="hmr-line-text">This hivemind predates roster reviews. “completed” means its workers finished; no reviewer checked any merged commit.</div></section>';
  }
  const r = obj(roles);
  const { stage, def, reasons } = stageResolve(roles);
  const wsList = arr(r.workstreams).map(obj);
  const wsById = {};
  wsList.forEach((w) => { wsById[w.id] = w; });
  const counts = obj(r.counts);
  const reviewer = obj(r.reviewer);
  const reviews = arr(r.reviews);
  const hold = r.hold ? holdHTML(r.hold) : '';
  const stat = (n, label) => '<span class="hmr-stat"><b>' + E(n == null ? 0 : n) + '</b> ' + E(label) + '</span>';
  return '<section class="hmr-panel" data-hmr-panel="roster" data-hmr-stage="' + E(stage) + '">'
    + '<div class="hmr-panel-head">' + badge(def.tone, def.label, ' data-hmr-stage="' + E(stage) + '"')
    + (r.as_of ? '<span class="hmr-asof">as of ' + E(fmtTime(r.as_of)) + '</span>' : '') + '</div>'
    + '<div class="hmr-line-text">' + E(def.line) + '</div>'
    + (r.reason && !hold ? '<div class="hmr-reason">' + E(r.reason) + '</div>' : '')
    + (reasons.length ? '<ul class="hmr-reasons">' + reasons.map((x) => '<li>' + E(x) + '</li>').join('') + '</ul>' : '')
    + hold
    + '<div class="hmr-stats">'
    + stat(wsList.length, 'workers (workstreams)') + stat(reviewer.count, 'reviewers')
    + stat(counts.accepted, 'accepted') + stat(counts.provisional, 'provisional')
    + stat(counts.skipped, 'skipped') + stat(counts.failed, 'failed') + '</div>'
    + (reviewer.requested_character ? '<div class="hmr-muted">Reviewer character: <span class="hmr-char">' + E(reviewer.requested_character) + '</span>'
      + (arr(reviewer.session_ids).length ? ' · ' + arr(reviewer.session_ids).map((s) => '<span class="hmr-sid" title="' + E(s) + '">' + E(shortId(s)) + '</span>').join(' ') : '') + '</div>' : '')
    + '<h4 class="hmr-h">Workers — requested character, effective engine, integration</h4>'
    + (wsList.length ? wsList.map((w) => wsRow(w, roles)).join('') : '<div class="hmr-muted">No workstreams yet.</div>')
    + '<h4 class="hmr-h">Reviews of merged commits</h4>'
    + (reviews.length ? reviews.map((rv) => reviewCard(rv, wsById, stage)).join('')
      : '<div class="hmr-muted" data-hmr-no-reviews="1">No merged commit has been reviewed.</div>')
    + '</section>';
}

// Chips for a workstream row / detail: requested character + effective engine.
function wsChipsHTML(roles, wsId) {
  const w = arr(obj(roles).workstreams).map(obj).find((x) => x.id === wsId);
  if (!w) return '';
  return '<span class="hmr-ws-chips"><span class="hmr-char" title="Requested character">' + E(w.requested_character || 'no character') + '</span>'
    + engineChip(w.effective_engine)
    + outcomeBadge(w, roles) + '</span>';
}

// Runs modal: role counts (reviewers kept apart from workers) + a switch.
function runsHeaderHTML(data, hivemindId, projectId, role) {
  const c = obj(data && data.role_counts);
  if (!data || !data.role_counts) return '';
  const btn = (key, label) => '<button type="button" class="hmr-role-btn' + (key === role ? ' active' : '') + '" data-hmr-role="' + key + '"'
    + (key === role ? ' aria-current="true" disabled' : ' onclick="openHmRunsModal(\'' + E(hivemindId) + '\',\'' + E(projectId) + '\',\'' + key + '\',\'\',\'' + label + '\')"')
    + '><b>' + E(c[key] == null ? 0 : c[key]) + '</b> ' + label + '</button>';
  return '<div class="hmr-roles" data-hmr-role-counts="1">' + btn('orchestrator', 'Orchestrator') + btn('worker', 'Worker') + btn('reviewer', 'Reviewer') + '</div>';
}

// Runs modal rows: reuse the host's row renderer, add role/character/engine.
function runRowsHTML(runs, projectId, renderRows) {
  return arr(runs).map((r) => {
    const x = obj(r);
    if (!x.run_role) return renderRows([r], projectId);
    const eng = engineText(x.effective_engine);
    return '<div class="hmr-run" data-hmr-run-role="' + E(x.run_role) + '">' + renderRows([r], projectId)
      + '<div class="hmr-run-meta"><span class="hmr-run-role hmr-run-role-' + E(x.run_role) + '">' + E(x.run_role) + '</span>'
      + '<span class="hmr-char">' + E(x.requested_character || 'no character') + '</span>'
      + (eng ? '<span class="hmr-engine">' + E(eng) + '</span>' : '<span class="hmr-engine hmr-engine-none">engine not resolved</span>') + '</div></div>';
  }).join('');
}

function reviewerRunsButtonHTML(hivemindId, manifest, roles) {
  if (!roles) return '';
  const n = obj(obj(roles).reviewer).count;
  return '<button class="secondary" data-hmr-reviewer-runs="1" onclick="openHmRunsModal(\'' + E(hivemindId) + '\',\'' + E(obj(manifest).project_id || '')
    + '\',\'reviewer\',\'\',\'Reviewer\')">Reviewer Runs (' + E(n == null ? 0 : n) + ')</button>';
}

window.hmReview = {
  isRoster, stageResolve, stageBadgeHTML, statusBadgeHTML, listChipHTML, overviewPanelHTML,
  wsChipsHTML, runsHeaderHTML, runRowsHTML, reviewerRunsButtonHTML,
};

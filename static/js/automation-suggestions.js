// ── Consent-first automation suggestions (MC-915) ─────────────────────────
//
// The agent proposes a ready-to-run scheduled job. The human accepts it (which
// creates the real schedule) or dismisses it (latched — never offered again).
// Nothing here creates a job on its own: Accept is a click, and the click is
// the only path from a suggestion to a live schedule.
//
// Lives at the top of the Scheduler modal and renders NOTHING when the queue is
// empty — a permanently-visible empty box trains people to stop looking at it.

let _autoSuggestions = [];

async function refreshAutomationSuggestions() {
  const container = document.getElementById('automation-suggestion-list');
  if (!container) return;
  const section = document.getElementById('automation-suggestion-section');
  try {
    const res = await fetch(API_BASE + '/api/automation/suggestions');
    _autoSuggestions = await res.json();
  } catch (e) {
    _autoSuggestions = [];
  }
  if (!Array.isArray(_autoSuggestions) || !_autoSuggestions.length) {
    if (section) section.style.display = 'none';
    container.innerHTML = '';
    return;
  }
  if (section) section.style.display = '';
  container.innerHTML = _autoSuggestions.map(renderAutomationSuggestion).join('');
}

function renderAutomationSuggestion(s) {
  const ev = s.evidence || {};
  const spec = s.spec || {};
  const cadence = spec.schedule_type === 'daily'
    ? `Daily at ${esc(spec.time || '09:00')} UTC`
    : esc(spec.schedule_type || 'daily');
  return `<div class="schedule-card-wrap">
    <div class="schedule-card">
      <div class="schedule-card-body">
        <div class="schedule-card-project" style="color:${window._schedProjectColor(s.project_id)}">${
          esc(s.project_name || s.project_id)}</div>
        <div class="schedule-card-task" title="${esc(spec.task || '')}">${esc(s.title || '')}</div>
        <div class="schedule-card-desc" style="font-size:11px;color:var(--text-muted);margin:2px 0 4px;font-style:italic">${
          esc(s.rationale || '')}</div>
        <div class="schedule-card-meta">
          <span>Proposed: ${cadence}</span>
          <span>${ev.count || 0} manual runs</span>
          <span>${ev.distinct_days || 0} days</span>
          <span style="color:var(--text-muted)">Nothing runs until you accept</span>
        </div>
      </div>
      <div class="schedule-card-actions">
        <button class="btn-header-action" style="padding:3px 8px;font-size:11px"
                onclick="dismissAutomationSuggestion('${esc(s.id)}')"
                title="Never offer this again">Dismiss</button>
        <button class="btn-add" style="padding:4px 12px;font-size:11px"
                onclick="acceptAutomationSuggestion('${esc(s.id)}')"
                title="Create this scheduled task">Accept</button>
      </div>
    </div>
  </div>`;
}

async function acceptAutomationSuggestion(id) {
  const s = _autoSuggestions.find(x => x.id === id);
  const label = s ? s.title : 'this suggestion';
  // Accept CREATES a live scheduled job, so it gets an explicit confirm — the
  // consent has to be a decision, not a mis-click on a crowded card.
  if (!confirm(`Create a scheduled task for:\n\n${label}\n\nIt will run on the proposed cadence until you disable it.`)) return;
  try {
    const res = await fetch(API_BASE + `/api/automation/suggestions/${encodeURIComponent(id)}/accept`,
                            { method: 'POST' });
    const body = await res.json();
    if (!res.ok) { alert(body.error || 'Could not create the schedule'); return; }
  } catch (e) {
    alert('Could not create the schedule: ' + e);
    return;
  }
  await refreshAutomationSuggestions();
  if (window.refreshScheduleList) await window.refreshScheduleList();
}

async function dismissAutomationSuggestion(id) {
  // Dismissal is LATCHED and permanent — say so before taking it.
  if (!confirm('Dismiss this suggestion? It will not be offered again.')) return;
  try {
    await fetch(API_BASE + `/api/automation/suggestions/${encodeURIComponent(id)}/dismiss`,
                { method: 'POST' });
  } catch (e) { /* the list refresh below shows whether it stuck */ }
  await refreshAutomationSuggestions();
}

// static/js/*.js are ES modules: top-level names are NOT global. Anything an
// inline onclick or another module calls has to be hung on window.
window.refreshAutomationSuggestions = refreshAutomationSuggestions;
window.acceptAutomationSuggestion = acceptAutomationSuggestion;
window.dismissAutomationSuggestion = dismissAutomationSuggestion;

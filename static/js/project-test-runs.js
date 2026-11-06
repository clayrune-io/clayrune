// MC-1148 P13: project history/discovery. TestRunData owns every request/timer.
import TestRunData from './test-run-data.js';

const TERMINAL = new Set(['passed', 'failed', 'error', 'cancelled', 'timeout', 'no_tests', 'lost']);
const OUTCOMES = ['pass', 'fail', 'skip', 'error', 'xfail', 'xpass'];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[c]);
const integer = value => Number.isSafeInteger(value) && value >= 0;
const count = value => integer(value) ? value : '\u2014';

export function projectRuns(view) {
  // Names/labels are display data, never identity. Reject another project's row.
  return (view.runs || []).filter(r => r.project_id === view.projectId)
    .slice().sort((a, b) => Number(b.created_at || 0) - Number(a.created_at || 0)
      || String(b.run_id).localeCompare(String(a.run_id)));
}

export function runHTML(run) {
  const total = run.selected_total, done = run.completed;
  const counted = ['test', 'script'].includes(run.unit);
  const measured = counted && run.capture === 'complete' && integer(total) && integer(done) && done <= total;
  const terminal = TERMINAL.has(run.state);
  const unit = run.unit === 'script' ? 'smoke scripts' : run.unit === 'test' ? 'tests' : 'units';
  const progress = measured ? `${done}/${total} ${unit} done`
    : `${count(counted ? done : null)} done / total unknown${run.capture !== 'complete' ? ' (partial capture)' : ''}`;
  const state = terminal ? run.state.replaceAll('_', ' ')
    : run.reporter_state ? 'finalizing' : run.state === 'collecting' ? 'collecting'
      : measured && total > 0 && done === total ? 'finalizing' : 'running';
  const owner = run.owner_session_id == null ? 'Ownerless' : `Conversation ${run.owner_session_id}`;
  const failures = Array.isArray(run.latest_failures) ? run.latest_failures : [];
  const bar = measured && total > 0 && !terminal
    ? `<progress max="${total}" value="${done}" aria-label="Completed ${unit}"></progress>` : '';
  const outcomes = OUTCOMES.map(k => `${count(counted ? run.counts?.[k] : null)} ${k}`).join(' \u00b7 ');
  return `<article class="ptr-run" data-run-id="${esc(run.run_id)}">
    <div class="ptr-run-head"><strong>${esc(run.display_label || 'Test run')}</strong><span class="ptr-verdict">${esc(state)}</span></div>
    <div class="ptr-meta">${esc(run.source || 'unknown source')} \u00b7 ${esc(run.runner || 'unknown runner')} \u00b7 ${esc(owner)}</div>
    <div class="ptr-measure">${esc(progress)}${bar}</div>
    <div class="ptr-counts">${outcomes}</div>
    ${run.total_recalculated ? '<div class="ptr-notice">Total recalculated; progress may move backwards.</div>' : ''}
    ${integer(run.not_run) && run.not_run > 0 ? `<div>${run.not_run} not run</div>` : ''}
    ${integer(run.collection_errors) && run.collection_errors > 0 ? `<div>${run.collection_errors} collection errors</div>` : ''}
    ${terminal ? `<div>Exit code: ${esc(run.exit_code ?? 'unknown')}${run.capture !== 'complete' ? ' \u00b7 progress incomplete' : ''}</div>` : ''}
    ${failures.length ? `<ul class="ptr-failures" aria-label="Recent failures">${failures.map(f => `<li>${esc(f.name ?? f.unit_id ?? 'Unnamed failure')}${f.phase ? ` (${esc(f.phase)})` : ''}</li>`).join('')}</ul>` : ''}
    ${integer(run.failure_details_omitted) && run.failure_details_omitted > 0 ? `<div>${run.failure_details_omitted} failure details omitted from snapshot</div>` : ''}
    <div class="ptr-id">Run ${esc(run.run_id)}${run.parent_run_id ? ` \u00b7 Parent ${esc(run.parent_run_id)}` : ''}</div>
  </article>`;
}

export function contentHTML(view) {
  const rows = projectRuns(view), active = rows.filter(r => !TERMINAL.has(r.state));
  const recent = rows.filter(r => TERMINAL.has(r.state));
  const status = view.status?.state;
  const warning = status === 'stale' || status === 'error'
    ? '<p class="ptr-notice" role="status">Test-run data unavailable; showing the last received snapshots. Updates will retry.</p>' : '';
  const group = (title, runs) => runs.length ? `<section><h3>${title} (${runs.length})</h3>${runs.map(runHTML).join('')}</section>` : '';
  return `<div class="ptr-heading"><h2>Test runs</h2><button type="button" class="ptr-refresh">Refresh</button></div>
    <p class="ptr-meta">Recent project runs, including work without a conversation. Older history may be unavailable.</p>
    ${warning}${group('Active', active)}${group('Recent', recent)}
    ${rows.length ? '' : '<p>No test runs recorded for this project.</p>'}`;
}

export function createProjectTestRuns({ data, refreshModal, root, style } = {}) {
  const projects = new Map();
  const available = pid => {
    const v = projects.get(pid)?.view;
    return data.isDisabled?.() !== true && !!v && v.status?.lastOkAt != null
      && !['disabled', 'restricted', 'missing'].includes(v.status.state);
  };
  function paint(pid) {
    const host = root(pid)?.querySelector('[data-project-test-runs]');
    if (host) {
      const focused = host.contains?.(host.ownerDocument?.activeElement);
      host.innerHTML = contentHTML(projects.get(pid).view);
      if (focused) host.querySelector('.ptr-refresh')?.focus({ preventScroll: true });
    }
  }
  function syncNavigation(pid) {
    // A modal rebuild may be deferred while its menu is open. Update only our
    // own entries in place so first discovery/restriction cannot leave stale UI.
    const element = root(pid);
    if (!element?.querySelectorAll) return;
    if (!available(pid)) {
      element.querySelectorAll('[data-test-runs-nav], [data-project-test-runs]').forEach(el => el.remove());
      return;
    }
    for (const [selector, menu] of [['.mc-tabs-in-menu', true], ['.modal-tab-bar', false]]) {
      const nav = element.querySelector(selector);
      if (nav && !nav.querySelector('[data-test-runs-nav]')) {
        nav.querySelector('[data-tab-name="workflows"]')?.insertAdjacentHTML('beforebegin', navigationHTML(pid, '', menu));
      }
    }
  }
  function attach(pid) {
    if (projects.has(pid)) return;
    const record = { view: null, unsubscribe: null, shown: false };
    projects.set(pid, record);
    record.unsubscribe = data.subscribe(pid, null, view => {
      if (projects.get(pid) !== record || view.projectId !== pid) return;
      record.view = view;
      style?.([...projects.keys()].some(available));
      // Disabled is global in the shared reader; one response hides all open
      // project entries immediately, without waiting for their next poll.
      for (const [id, current] of projects) {
        const after = available(id);
        if (current.shown !== after) {
          current.shown = after;
          refreshModal(id);
        } else if (after && id === pid) paint(id);
        syncNavigation(id);
      }
    });
  }
  function detach(pid) {
    const record = projects.get(pid);
    projects.delete(pid);
    record?.unsubscribe?.();
    style?.([...projects.keys()].some(available));
  }
  function navigationHTML(pid, activeTab, menu = false) {
    if (!available(pid)) return '';
    const cls = menu ? 'modal-menu-item' : 'modal-tab';
    return `<button type="button" class="${cls}${activeTab === 'test-runs' ? ' active' : ''}" data-test-runs-nav="${esc(pid)}">Test runs</button>`;
  }
  function panelHTML(pid, activeTab) {
    if (!available(pid) || activeTab !== 'test-runs') return '';
    return `<div class="modal-tab-content active ptr-panel" data-tab="test-runs" data-project-test-runs="${esc(pid)}">${contentHTML(projects.get(pid).view)}</div>`;
  }
  return { attach, detach, available, navigationHTML, panelHTML, refresh: pid => data.refresh(pid) };
}

if (typeof window !== 'undefined') {
  let stylesheet;
  window.ProjectTestRuns = createProjectTestRuns({
    data: TestRunData,
    root: pid => openModals.get(pid)?.element,
    refreshModal: pid => window.refreshModalById(pid),
    style: enabled => {
      if (enabled && !stylesheet) {
        stylesheet = document.createElement('link');
        stylesheet.rel = 'stylesheet'; stylesheet.href = '/static/css/project-test-runs.css';
        document.head.appendChild(stylesheet);
      } else if (!enabled && stylesheet) { stylesheet.remove(); stylesheet = null; }
    },
  });
  document.addEventListener('click', event => {
    const nav = event.target.closest?.('[data-test-runs-nav]');
    if (nav) {
      window._mcMenuClose();
      window.switchModalTab(nav.dataset.testRunsNav, 'test-runs');
    }
    const refresh = event.target.closest?.('.ptr-refresh');
    const panel = refresh?.closest('[data-project-test-runs]');
    if (panel) window.ProjectTestRuns.refresh(panel.dataset.projectTestRuns);
  });
}

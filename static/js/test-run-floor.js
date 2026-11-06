// Floor-only display of the P11 projection. Never fetches or mutates a run.
const number = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));
const active = summary => summary && (summary.available === false || number(summary.active_count) > 0);

function measurement(summary) {
  const total = number(summary.selected_total), done = number(summary.completed);
  if (summary.capture !== 'complete' || total === null || done === null || done > total) return null;
  return { total, done, percent: total > 0 ? Math.floor(100 * done / total) : null };
}

export function testRunFloorLine(summary) {
  if (!active(summary)) return '';
  if (summary.available === false) return 'testing unavailable';
  const measured = measurement(summary);
  const runs = summary.active_count > 1 ? ` · ${summary.active_count} runs` : '';
  if (measured) {
    if (measured.total > 0 && measured.done === measured.total) return `testing, finalizing${runs}`;
    return `testing ${measured.percent === null ? `${measured.done}/${measured.total}` : `${measured.percent}%`}${runs}`;
  }
  if (summary.phase === 'collecting') return 'testing, collecting';
  if (summary.active_count > 1) return `testing, multiple · ${summary.active_count} runs`;
  return 'testing, total unknown';
}

function countsHTML(counts) {
  if (!counts || typeof counts !== 'object') return '';
  return ['pass', 'fail', 'skip', 'error', 'xfail', 'xpass'].map(key => {
    const count = number(counts[key]);
    const alert = count > 0 && (key === 'fail' || key === 'error') ? ' fl-test-alert' : '';
    return count === null ? '' : `<span class="fl-test-${key}${alert}">${count} ${key}</span>`;
  }).join(' ');
}

export function testRunFloorHTML(summary, { room = false, secondary = false } = {}) {
  if (!active(summary)) return '';
  const line = testRunFloorLine(summary);
  const measured = measurement(summary);
  const rows = [];
  if (room || secondary || summary.available === false) rows.push(`<strong>${escape(line)}</strong>`);
  if (summary.available !== false) {
    if (measured) {
      const unit = summary.unit === 'script' ? 'scripts' : summary.unit === 'test' ? 'tests' : 'units';
      rows.push(`<span>${measured.done}/${measured.total} ${unit} done</span>`);
      if (measured.percent !== null) rows.push(`<progress max="${measured.total}" value="${measured.done}" aria-label="${escape(line)}"></progress>`);
    }
    const counts = countsHTML(summary.counts);
    if (counts) rows.push(`<span class="fl-test-counts">${counts}</span>`);
    // Indeterminate summaries have no aggregate counts. Show observed outcomes
    // per run, never add incompatible units or imply complete capture.
    if (!counts && Array.isArray(summary.runs)) {
      for (const run of summary.runs.slice(0, 3)) {
        if (!run || typeof run !== 'object') continue;
        rows.push(`<span class="fl-test-run" title="${escape(run.run_id)}">${escape(run.runner || 'run')} · observed ${countsHTML(run.counts) || 'counts unknown'}</span>`);
      }
      const omitted = Math.max(0, (number(summary.active_count) || 0) - 3);
      if (omitted) rows.push(`<span>+${omitted} more runs</span>`);
    }
    if (summary.capture !== 'complete') rows.push('<span>partial capture</span>');
    if (summary.total_recalculated) rows.push('<span>total recalculated</span>');
    if (room) {
      for (const [key, label] of [['ownerless_count', 'ownerless'], ['unavailable_owner_count', 'owner unavailable']]) {
        const count = number(summary[key]);
        if (count) rows.push(`<span>${count} ${label}</span>`);
      }
    }
  }
  return `<div class="fl-test${room ? ' fl-test-room' : ''}${secondary ? ' fl-test-secondary' : ''}">${rows.join('')}</div>`;
}

// ── Scheduled-runs calendar ─────────────────────────────────────────────────
//
// A week grid over the SAME /api/schedules payload the list view reads — every
// field needed (schedule_type, time, days, cron_expr, interval_minutes, run_at,
// enabled, project_name) is already on the record, so this adds no endpoint and
// no server state. It answers "which agent, on what project, runs when" at a
// glance, which the card list can't: the list is ordered by schedule, and the
// question is about time.
//
// Two rules this view must not break:
//
//   1. NEVER paint a run that cannot happen. The master kill-switch
//      (`scheduler_paused`) and the per-schedule `enabled` flag both stop
//      dispatch. A calendar that showed a full week of confident-looking runs
//      while the switch was off would be worse than no calendar — so paused and
//      disabled occurrences render struck through and dimmed, under a banner.
//   2. NEVER invent an occurrence we can't actually derive. Cron gets a real
//      but deliberately partial parser; anything it doesn't understand is NOT
//      guessed at or silently dropped — it goes to an "unscheduled here" strip
//      that shows the server's own `next_run` instead. Being visibly incomplete
//      beats being confidently wrong.

let schedCalMode = false;      // false = card list, true = week grid
let schedCalWeekOffset = 0;    // 0 = this week, -1 = last, +1 = next
let _schedCalCache = [];       // last /api/schedules payload (for toggles + re-render)

const _SCAL_DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

// ── Date helpers ────────────────────────────────────────────────────────────
// Everything is LOCAL time on purpose: the server interprets `time` / `cron_expr`
// in the host's zone (see scheduleDescription's tz suffix), so the grid has to
// agree with it or every row would sit in the wrong column.

function _scalStartOfWeek(offsetWeeks) {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - d.getDay() + (offsetWeeks * 7));   // back to Sunday
  return d;
}

function _scalIsSameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth()
      && a.getDate() === b.getDate();
}

// This codebase stores `days` as 1=Mon … 7=Sun (see scheduleDescription's
// dayNames), while JS getDay() is 0=Sun … 6=Sat. Convert, don't assume.
function _scalJsDayToSchedDay(jsDay) { return jsDay === 0 ? 7 : jsDay; }

function _scalHhmm(d) {
  return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}

// ── Cron ────────────────────────────────────────────────────────────────────
// Deliberately partial: standard 5 fields (min hour dom mon dow), supporting
// `*`, a number, `a,b,c`, `a-b` and `*/n`. That covers everything the schedule
// form can produce and the overwhelming majority of hand-written entries.
// Anything else returns null and the caller routes it to the unparsed strip —
// a wrong occurrence is a worse outcome than an absent one.
function _scalParseCronField(field, min, max) {
  if (field === '*') { const out = []; for (let v = min; v <= max; v++) out.push(v); return out; }
  const out = new Set();
  for (const part of field.split(',')) {
    let m;
    if ((m = part.match(/^\*\/(\d+)$/))) {
      const step = parseInt(m[1], 10);
      if (!step) return null;
      for (let v = min; v <= max; v += step) out.add(v);
    } else if ((m = part.match(/^(\d+)-(\d+)$/))) {
      const a = parseInt(m[1], 10), b = parseInt(m[2], 10);
      if (a < min || b > max || a > b) return null;
      for (let v = a; v <= b; v++) out.add(v);
    } else if ((m = part.match(/^(\d+)$/))) {
      const v = parseInt(m[1], 10);
      if (v < min || v > max) return null;
      out.add(v);
    } else {
      return null;   // named months/days, @weekly, step-on-range, L/W/# — not ours
    }
  }
  return [...out].sort((x, y) => x - y);
}

function _scalParseCron(expr) {
  const parts = String(expr || '').trim().split(/\s+/);
  if (parts.length !== 5) return null;
  const minutes = _scalParseCronField(parts[0], 0, 59);
  const hours = _scalParseCronField(parts[1], 0, 23);
  const dom = _scalParseCronField(parts[2], 1, 31);
  const months = _scalParseCronField(parts[3], 1, 12);
  let dow = _scalParseCronField(parts[4], 0, 7);
  if (!minutes || !hours || !dom || !months || !dow) return null;
  // cron treats both 0 and 7 as Sunday.
  dow = [...new Set(dow.map(d => (d === 7 ? 0 : d)))];
  const domRestricted = parts[2] !== '*';
  const dowRestricted = parts[4] !== '*';
  return { minutes, hours, dom, months, dow, domRestricted, dowRestricted };
}

function _scalCronHitsDay(c, day) {
  if (!c.months.includes(day.getMonth() + 1)) return false;
  const inDom = c.dom.includes(day.getDate());
  const inDow = c.dow.includes(day.getDay());
  // Real cron semantics: with BOTH day-of-month and day-of-week restricted the
  // match is a UNION, not an intersection. Getting this backwards would silently
  // drop rows on exactly the schedules people hand-write.
  if (c.domRestricted && c.dowRestricted) return inDom || inDow;
  if (c.domRestricted) return inDom;
  if (c.dowRestricted) return inDow;
  return true;
}

// ── Occurrence expansion ────────────────────────────────────────────────────
// Returns { occurrences: [{schedule, when}], unparsed: [schedule], always: [schedule] }
// for the 7 days starting at weekStart.
//
// `interval` schedules are NOT expanded into the grid: "every 5 minutes" is 288
// chips a day and would bury everything else. They get an always-running strip,
// same shape as the reference design.
function scalBuildWeek(schedules, weekStart) {
  const days = [];
  for (let i = 0; i < 7; i++) {
    const d = new Date(weekStart);
    d.setDate(d.getDate() + i);
    days.push({ date: d, items: [] });
  }
  const weekEnd = new Date(weekStart);
  weekEnd.setDate(weekEnd.getDate() + 7);
  const always = [], unparsed = [];

  for (const s of schedules) {
    const type = s.schedule_type;

    if (type === 'interval') { always.push(s); continue; }

    if (type === 'once') {
      if (!s.run_at) { unparsed.push(s); continue; }
      const at = new Date(s.run_at);
      if (isNaN(at)) { unparsed.push(s); continue; }
      const slot = days.find(d => _scalIsSameDay(d.date, at));
      if (slot) slot.items.push({ s, when: at });
      // Outside this week is not "unparsed" — it's simply another week. Silence
      // is correct here; the strip is for things we could not place at all.
      continue;
    }

    if (type === 'daily') {
      const [hh, mm] = String(s.time || '09:00').split(':').map(n => parseInt(n, 10));
      if (isNaN(hh) || isNaN(mm)) { unparsed.push(s); continue; }
      const wanted = Array.isArray(s.days) && s.days.length ? s.days : null;  // null = every day
      for (const d of days) {
        if (wanted && !wanted.includes(_scalJsDayToSchedDay(d.date.getDay()))) continue;
        const when = new Date(d.date);
        when.setHours(hh, mm, 0, 0);
        d.items.push({ s, when });
      }
      continue;
    }

    if (type === 'cron') {
      const c = _scalParseCron(s.cron_expr);
      if (!c) { unparsed.push(s); continue; }
      // A minute-level cron (*/5 etc) is the interval case wearing a different
      // hat — same reasoning, same strip.
      if (c.minutes.length * c.hours.length > 24) { always.push(s); continue; }
      for (const d of days) {
        if (!_scalCronHitsDay(c, d.date)) continue;
        for (const h of c.hours) for (const mi of c.minutes) {
          const when = new Date(d.date);
          when.setHours(h, mi, 0, 0);
          d.items.push({ s, when });
        }
      }
      continue;
    }

    unparsed.push(s);
  }

  for (const d of days) d.items.sort((a, b) => a.when - b.when);
  return { days, always, unparsed };
}

// ── Render ──────────────────────────────────────────────────────────────────

// Stable per-project accent so the same project keeps its colour across weeks.
function _scalProjectHue(projectId) {
  let h = 0;
  const str = String(projectId || '');
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) % 360;
  return h;
}

// The master switch lives in scheduler.js module scope — reachable only via its
// window bridge. Falls back to the config mirror, then to "not paused", so a
// load-order hiccup degrades to showing runs rather than hiding all of them.
function _scalPaused() {
  if (typeof window.getSchedulerPaused === 'function') return !!window.getSchedulerPaused();
  try { return !!_globalConfig.scheduler_paused; } catch (e) { return false; }
}
function _scalWillRun(s) { return !!s.enabled && !_scalPaused(); }

function _scalChipHTML(s, when) {
  const live = _scalWillRun(s);
  const hue = _scalProjectHue(s.project_id);
  const why = !s.enabled ? 'This schedule is disabled'
            : _scalPaused() ? 'All scheduled runs are paused by the master switch'
            : 'Will run at this time';
  const label = s.description || s.task || '(no task)';
  return `<div class="scal-chip${live ? '' : ' dead'}"
      style="--scal-hue:${hue}"
      title="${esc((s.project_name || s.project_id) + ' — ' + label)}\n${esc(why)}">
    <span class="scal-chip-toggle ${s.enabled ? 'on' : ''}"
       onclick="event.stopPropagation();scalToggle('${esc(s.id)}',${!s.enabled})"
       title="${s.enabled ? 'Disable this schedule' : 'Enable this schedule'}"></span>
    <span class="scal-chip-body" onclick="scalOpenSchedule('${esc(s.id)}')">
      <span class="scal-chip-title">${esc(label)}</span>
      <span class="scal-chip-meta">${esc(_scalHhmm(when))} &middot; ${esc(s.project_name || s.project_id)}</span>
    </span>
  </div>`;
}

function _scalStripHTML(cls, title, hint, schedules) {
  if (!schedules.length) return '';
  const pills = schedules.map(s => {
    const live = _scalWillRun(s);
    const hue = _scalProjectHue(s.project_id);
    return `<span class="scal-pill${live ? '' : ' dead'}" style="--scal-hue:${hue}"
        title="${esc((s.project_name || s.project_id) + ' — ' + (s.task || ''))}">
      <span class="scal-chip-toggle ${s.enabled ? 'on' : ''}"
         onclick="event.stopPropagation();scalToggle('${esc(s.id)}',${!s.enabled})"
         title="${s.enabled ? 'Disable this schedule' : 'Enable this schedule'}"></span>
      <span onclick="scalOpenSchedule('${esc(s.id)}')">${esc(s.description || s.task || s.id)}
        <span class="scal-pill-meta">${esc(typeof scheduleDescription === 'function' ? scheduleDescription(s) : (s.schedule_type || ''))}</span></span>
    </span>`;
  }).join('');
  return `<div class="scal-strip ${cls}">
    <div class="scal-strip-head">${esc(title)}<span class="scal-strip-hint">${esc(hint)}</span></div>
    <div class="scal-strip-body">${pills}</div>
  </div>`;
}

function renderScheduleCalendar() {
  const host = document.getElementById('schedule-calendar');
  if (!host) return;
  const weekStart = _scalStartOfWeek(schedCalWeekOffset);
  const { days, always, unparsed } = scalBuildWeek(_schedCalCache, weekStart);
  const today = new Date();

  const weekEnd = new Date(weekStart);
  weekEnd.setDate(weekEnd.getDate() + 6);
  const fmt = (d) => d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  const rangeLabel = schedCalWeekOffset === 0
    ? `This week &middot; ${fmt(weekStart)} – ${fmt(weekEnd)}`
    : `${fmt(weekStart)} – ${fmt(weekEnd)}`;

  // Rule 1: if nothing can fire, say so above the grid rather than letting a
  // week of struck-through chips imply it.
  const pausedBanner = _scalPaused()
    ? `<div class="scal-paused-banner">All scheduled runs are paused &mdash; nothing below will fire.
         <button class="scal-resume" onclick="toggleSchedulerPause()">Resume</button></div>`
    : '';

  const grid = days.map(d => {
    const isToday = _scalIsSameDay(d.date, today);
    const chips = d.items.length
      ? d.items.map(it => _scalChipHTML(it.s, it.when)).join('')
      : '<div class="scal-empty-day"></div>';
    return `<div class="scal-day${isToday ? ' today' : ''}">
      <div class="scal-day-head">
        <span class="scal-dow">${_SCAL_DAY_NAMES[d.date.getDay()]}</span>
        <span class="scal-dom">${d.date.getDate()}</span>
      </div>
      <div class="scal-day-body">${chips}</div>
    </div>`;
  }).join('');

  host.innerHTML = `
    ${pausedBanner}
    <div class="scal-toolbar">
      <div class="scal-range">${rangeLabel}</div>
      <div class="scal-nav">
        <button class="btn-header-action" onclick="scalShiftWeek(-1)" title="Previous week">&#8249;</button>
        <button class="btn-header-action" onclick="scalShiftWeek(0)">Today</button>
        <button class="btn-header-action" onclick="scalShiftWeek(1)" title="Next week">&#8250;</button>
      </div>
    </div>
    ${_scalStripHTML('always', 'Always running',
        'Repeat too often to place on a day', always)}
    <div class="scal-grid">${grid}</div>
    ${_scalStripHTML('unparsed', 'Not placed on the grid',
        'This view could not derive their times — the schedule itself still runs', unparsed)}`;
}

// ── Actions ─────────────────────────────────────────────────────────────────

function scalShiftWeek(delta) {
  schedCalWeekOffset = (delta === 0) ? 0 : schedCalWeekOffset + delta;
  renderScheduleCalendar();
}

// Toggling from a chip changes the SCHEDULE, not that one occurrence — so every
// chip for it must restyle at once. Optimistic locally, then reconciled from the
// server so a failed PUT can't leave the grid lying about what will run.
async function scalToggle(id, enabled) {
  const rec = _schedCalCache.find(s => s.id === id);
  if (rec) rec.enabled = enabled;
  renderScheduleCalendar();
  try {
    const res = await fetch(`${API_BASE}/api/schedules/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    if (!res.ok) throw new Error('save failed');
    if (typeof showToast === 'function') {
      showToast(enabled ? 'Schedule enabled' : 'Schedule disabled', 2200);
    }
  } catch (e) {
    if (rec) rec.enabled = !enabled;
    renderScheduleCalendar();
    if (typeof showToast === 'function') showToast('Could not change that schedule', 4000);
    return;
  }
  if (typeof refreshScheduleBanner === 'function') refreshScheduleBanner();
  await refreshScheduleCalendar({ keepWeek: true });
}

// Chip body → the card list, scrolled to that schedule. The calendar answers
// "when"; everything else (runs, edit, run-now) already lives on the card, so
// this hands off rather than duplicating those controls in a 90px chip.
function scalOpenSchedule(id) {
  schedCalMode = false;
  _scalSyncPanes();
  if (typeof refreshScheduleList === 'function') refreshScheduleList();
  setTimeout(() => {
    const list = document.getElementById('schedule-list');
    if (!list) return;
    const card = list.querySelector(`[onclick*="${id}"]`);
    const wrap = card && card.closest('.schedule-card-wrap');
    if (wrap) {
      wrap.scrollIntoView({ block: 'center', behavior: 'smooth' });
      wrap.classList.add('scal-flash');
      setTimeout(() => wrap.classList.remove('scal-flash'), 1600);
    }
  }, 120);
}

function _scalSyncPanes() {
  const list = document.getElementById('schedule-list');
  const cal = document.getElementById('schedule-calendar');
  const btn = document.getElementById('scal-mode-btn');
  if (list) list.style.display = schedCalMode ? 'none' : '';
  if (cal) cal.style.display = schedCalMode ? '' : 'none';
  if (btn) btn.textContent = schedCalMode ? 'List' : 'Calendar';
}

function toggleScheduleCalendar() {
  schedCalMode = !schedCalMode;
  _scalSyncPanes();
  if (schedCalMode) refreshScheduleCalendar();
}

// Pulls the same payload the list uses. Includes stewards deliberately: the
// question the calendar answers is "what will run and when", and a steward run
// occupies the machine exactly like a schedule does — hiding them would make the
// week look emptier than it is. The card list keeps them separate because there
// the question is "what have I configured".
async function refreshScheduleCalendar(opts) {
  if (!(opts && opts.keepWeek)) schedCalWeekOffset = 0;
  try {
    const res = await fetch(API_BASE + '/api/schedules');
    _schedCalCache = await res.json();
  } catch (e) {
    const host = document.getElementById('schedule-calendar');
    if (host) host.innerHTML = '<div class="schedule-empty">Failed to load schedules.</div>';
    return;
  }
  renderScheduleCalendar();
}

// Sidebar / drawer "Calendar" entry. Opens the same Automation modal but lands
// directly on the grid: the List/Calendar switch sits under the pause banner AND
// the whole Stewards section, so on a phone you have to know it is there and
// scroll to it. openScheduler() is a no-op when the modal is already open (it
// just focuses), so this also works as a "show me the calendar" from anywhere.
async function openSchedulerCalendar() {
  // openScheduler lives in scheduler.js module scope — reachable only through
  // its window bridge, exactly like getSchedulerPaused above.
  if (typeof window.openScheduler !== 'function') return;
  await window.openScheduler();
  schedCalMode = true;
  _scalSyncPanes();
  await refreshScheduleCalendar();
}
window.openSchedulerCalendar = openSchedulerCalendar;

// Inline on*= handlers resolve against the global object at click time, and this
// file is an ES module — every name referenced from generated HTML needs a
// bridge or it fails silently (see tools/smoke/inline-handler-scope-check.mjs).
window.toggleScheduleCalendar = toggleScheduleCalendar;
window.refreshScheduleCalendar = refreshScheduleCalendar;
window.renderScheduleCalendar = renderScheduleCalendar;
window.scalShiftWeek = scalShiftWeek;
window.scalToggle = scalToggle;
window.scalOpenSchedule = scalOpenSchedule;
window.scalBuildWeek = scalBuildWeek;          // exercised directly by the smoke test
window._scalParseCron = _scalParseCron;

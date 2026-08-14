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

let schedCalWeekOffset = 0;    // 0 = this week, -1 = last, +1 = next
let _schedCalCache = [];       // last /api/schedules payload (for toggles + re-render)
let schedCalDayIndex = new Date().getDay();  // narrow screens show ONE day (0=Sun)

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
//
// A real time grid: hours down the left, days across the top, each run placed at
// its own time. The first cut stacked runs into day buckets, which answered
// "what day" but not "when" — and "when" is the whole reason to draw a calendar
// instead of reading the card list.

const SCAL_ROW_H = 46;        // px per hour
const SCAL_EVENT_MIN_H = 26;  // a dispatch has no duration; this is a legible block
const SCAL_GUTTER = 54;       // hour-label column

// Stable per-project accent so a project keeps its colour across weeks.
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

// The block label is a TITLE, not the prompt. `description` is the human name
// when set; otherwise the task's first line, trimmed. The full prompt belongs in
// the detail sheet — a 90px block cannot hold a 400-word agent brief, and trying
// made every block look identical.
function _scalTitle(s) {
  const d = (s.description || '').trim();
  if (d) return d.length > 60 ? d.slice(0, 57) + '…' : d;
  const first = (s.task || '').trim().split(/\r?\n/)[0].trim();
  if (!first) return s.id;
  return first.length > 60 ? first.slice(0, 57) + '…' : first;
}

// Which hours to draw. Full 24h is mostly empty whitespace — clamp to the band
// the week actually uses, padded an hour either side, with an 8h floor so a
// single 03:00 job doesn't produce a two-row grid.
function _scalHourRange(days) {
  let lo = 24, hi = 0, seen = false;
  for (const d of days) for (const it of d.items) {
    seen = true;
    lo = Math.min(lo, it.when.getHours());
    hi = Math.max(hi, it.when.getHours());
  }
  if (!seen) return { start: 6, end: 20 };
  let start = Math.max(0, lo - 1);
  let end = Math.min(24, hi + 2);
  while (end - start < 8) {
    if (end < 24) end++;
    else if (start > 0) start--;
    else break;
  }
  return { start, end };
}

// Greedy column packing so two runs at the same time sit side by side instead of
// on top of each other. Nominal 30-minute footprint purely for the layout.
function _scalLayoutDay(items) {
  const cols = [];              // cols[i] = end-minute of the last event placed
  const placed = items.map((it) => {
    const mins = it.when.getHours() * 60 + it.when.getMinutes();
    let c = cols.findIndex((endMin) => endMin <= mins);
    if (c === -1) { c = cols.length; cols.push(0); }
    cols[c] = mins + 30;
    return { it, mins, col: c };
  });
  const total = Math.max(1, cols.length);
  return placed.map((p) => ({ ...p, total }));
}

function _scalBlockHTML(p, range) {
  const { it, mins, col, total } = p;
  const s = it.s;
  const live = _scalWillRun(s);
  const top = ((mins - range.start * 60) / 60) * SCAL_ROW_H;
  const width = 100 / total;
  return `<div class="scal-block${live ? '' : ' dead'}${total > 1 ? ' multi' : ''}"
      style="--scal-hue:${_scalProjectHue(s.project_id)};top:${top.toFixed(1)}px;
             left:${(col * width).toFixed(2)}%;width:calc(${width.toFixed(2)}% - 3px);
             height:${SCAL_EVENT_MIN_H}px"
      onclick="scalOpenDetail('${esc(s.id)}')"
      title="${esc(_scalTitle(s))} — ${esc(_scalHhmm(it.when))}">
    <span class="scal-block-time">${esc(_scalHhmm(it.when))}</span>
    <span class="scal-block-title">${esc(_scalTitle(s))}</span>
  </div>`;
}

function _scalStripHTML(cls, title, hint, schedules) {
  if (!schedules.length) return '';
  const pills = schedules.map((s) => {
    const live = _scalWillRun(s);
    return `<span class="scal-pill${live ? '' : ' dead'}"
        style="--scal-hue:${_scalProjectHue(s.project_id)}"
        onclick="scalOpenDetail('${esc(s.id)}')"
        title="${esc(s.project_name || s.project_id)}">${esc(_scalTitle(s))}</span>`;
  }).join('');
  return `<div class="scal-strip ${cls}">
    <span class="scal-strip-head">${esc(title)}</span>
    <span class="scal-strip-body">${pills}</span>
    <span class="scal-strip-hint">${esc(hint)}</span>
  </div>`;
}

// Phones cannot show seven time columns legibly (≈45px each), so they get ONE
// day with its own nav. Same grid, same blocks — a narrower window onto it.
function _scalIsNarrow() { return window.innerWidth <= 960; }

function renderScheduleCalendar() {
  const host = document.getElementById('schedule-calendar');
  if (!host) return;
  const weekStart = _scalStartOfWeek(schedCalWeekOffset);
  const { days, always, unparsed } = scalBuildWeek(_schedCalCache, weekStart);
  const today = new Date();
  const narrow = _scalIsNarrow();
  const shown = narrow ? [days[Math.min(6, Math.max(0, schedCalDayIndex))]] : days;
  const range = _scalHourRange(shown);
  const gridH = (range.end - range.start) * SCAL_ROW_H;

  const fmt = (d) => d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  const rangeLabel = narrow
    ? shown[0].date.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })
    : (schedCalWeekOffset === 0
        ? 'This week · ' + fmt(days[0].date) + ' – ' + fmt(days[6].date)
        : fmt(days[0].date) + ' – ' + fmt(days[6].date));

  // ONE pause notice, and only here. The Scheduled-Tasks modal has its own
  // full-size pause bar; when the calendar lived inside that modal the two
  // stacked and said the same thing twice. This surface is standalone, so it
  // carries a single compact pill in the toolbar instead of a second banner.
  const pausedPill = _scalPaused()
    ? `<button class="scal-paused-pill" onclick="toggleSchedulerPause()"
         title="Nothing below will fire. Click to resume scheduled runs.">Paused · Resume</button>`
    : '';

  const hourLabels = [];
  for (let h = range.start; h < range.end; h++) {
    hourLabels.push(`<div class="scal-hour" style="height:${SCAL_ROW_H}px">
      <span>${String(h).padStart(2, '0')}:00</span></div>`);
  }

  const cols = shown.map((d) => {
    const isToday = _scalIsSameDay(d.date, today);
    const blocks = _scalLayoutDay(d.items).map((p) => _scalBlockHTML(p, range)).join('');
    const lines = [];
    for (let h = range.start; h < range.end; h++) {
      lines.push(`<div class="scal-slot" style="height:${SCAL_ROW_H}px"></div>`);
    }
    // "Now" marker, only on the real today and only inside the drawn band.
    let nowLine = '';
    if (isToday) {
      const nowMin = today.getHours() * 60 + today.getMinutes();
      if (nowMin >= range.start * 60 && nowMin <= range.end * 60) {
        const top = ((nowMin - range.start * 60) / 60) * SCAL_ROW_H;
        nowLine = `<div class="scal-now" style="top:${top.toFixed(1)}px"></div>`;
      }
    }
    return `<div class="scal-col${isToday ? ' today' : ''}">${lines.join('')}${nowLine}${blocks}</div>`;
  }).join('');

  const heads = shown.map((d) => {
    const isToday = _scalIsSameDay(d.date, today);
    return `<div class="scal-head${isToday ? ' today' : ''}">
      <span class="scal-dow">${_SCAL_DAY_NAMES[d.date.getDay()]}</span>
      <span class="scal-dom">${d.date.getDate()}</span>
    </div>`;
  }).join('');

  const cssCols = `${SCAL_GUTTER}px repeat(${shown.length}, minmax(0, 1fr))`;
  const navFns = narrow ? ['scalShiftDay(-1)', 'scalShiftDay(0)', 'scalShiftDay(1)']
                        : ['scalShiftWeek(-1)', 'scalShiftWeek(0)', 'scalShiftWeek(1)'];

  host.innerHTML = `
    <div class="scal-toolbar">
      <span class="scal-range">${esc(rangeLabel)}</span>
      <span class="scal-nav">
        ${pausedPill}
        <button class="btn-header-action" onclick="${navFns[0]}" title="Previous">&#8249;</button>
        <button class="btn-header-action" onclick="${navFns[1]}">Today</button>
        <button class="btn-header-action" onclick="${navFns[2]}" title="Next">&#8250;</button>
      </span>
    </div>
    ${_scalStripHTML('always', 'Always running', 'repeat too often to place on a clock', always)}
    ${_scalStripHTML('unparsed', 'Not placed', 'times could not be derived here — they still run', unparsed)}
    <div class="scal-time">
      <div class="scal-time-head" style="grid-template-columns:${cssCols}">
        <div class="scal-head-corner"></div>${heads}
      </div>
      <div class="scal-time-body" id="scal-scroll">
        <div class="scal-time-cols" style="grid-template-columns:${cssCols};height:${gridH}px">
          <div class="scal-gutter">${hourLabels.join('')}</div>
          ${cols}
        </div>
      </div>
    </div>
    <div id="scal-detail-host"></div>`;

  // Open on the first run of the day rather than at the top of the band — the
  // interesting rows are usually the early ones, but not always hour zero.
  const scroller = document.getElementById('scal-scroll');
  if (scroller) {
    const firstMin = shown.reduce((acc, d) => {
      const it = d.items[0];
      if (!it) return acc;
      const m = it.when.getHours() * 60 + it.when.getMinutes();
      return acc === null ? m : Math.min(acc, m);
    }, null);
    if (firstMin !== null) {
      scroller.scrollTop = Math.max(0, ((firstMin - range.start * 60) / 60) * SCAL_ROW_H - SCAL_ROW_H);
    }
  }
}

// ── Detail sheet ────────────────────────────────────────────────────────────
// Where the full prompt lives. Everything the card list offers about one
// schedule, reachable without leaving the calendar.

function scalOpenDetail(id) {
  const s = _schedCalCache.find((x) => x.id === id);
  const host = document.getElementById('scal-detail-host');
  if (!s || !host) return;
  const desc = typeof scheduleDescription === 'function' ? scheduleDescription(s) : (s.schedule_type || '');
  const nextRun = _scalPaused() && s.enabled
    ? 'paused'
    : (s.next_run && typeof formatScheduleTime === 'function' ? formatScheduleTime(s.next_run)
       : (s.enabled ? 'calculating…' : 'disabled'));
  const lastRun = s.last_run && typeof timeAgoShort === 'function' ? timeAgoShort(s.last_run) : 'never';
  host.innerHTML = `
    <div class="scal-detail-back" onclick="scalCloseDetail()"></div>
    <div class="scal-detail" style="--scal-hue:${_scalProjectHue(s.project_id)}">
      <div class="scal-detail-head">
        <span>
          <span class="scal-detail-title">${esc(_scalTitle(s))}</span>
          <span class="scal-detail-sub">${esc(s.project_name || s.project_id)} · ${esc(desc)}</span>
        </span>
        <button class="mc-dialog-close" onclick="scalCloseDetail()" title="Close">&#10005;</button>
      </div>
      <div class="scal-detail-meta">
        <span>Next: <b>${esc(nextRun)}</b></span>
        <span>Last: <b>${esc(lastRun)}</b></span>
        <span>${s.continue_session === false ? 'Fresh session each run' : 'Continues prior session'}</span>
      </div>
      <div class="scal-detail-label">Prompt sent to the agent</div>
      <div class="scal-detail-task">${esc(s.task || '(no task)')}</div>
      <div class="scal-detail-actions">
        <span class="scal-detail-toggle">
          <span class="schedule-toggle ${s.enabled ? 'on' : ''}"
             onclick="scalToggle('${esc(s.id)}',${!s.enabled});scalOpenDetail('${esc(s.id)}')"
             title="${s.enabled ? 'Disable this schedule' : 'Enable this schedule'}"></span>
          <span>${s.enabled ? 'Enabled' : 'Disabled'}</span>
        </span>
        <button class="btn-header-action" onclick="scalRunNow('${esc(s.id)}')"
                title="Dispatch this task now">&#x25B6; Run Now</button>
        <button class="btn-header-action" onclick="scalEdit('${esc(s.id)}')">Edit</button>
      </div>
    </div>`;
}

function scalCloseDetail() {
  const host = document.getElementById('scal-detail-host');
  if (host) host.innerHTML = '';
}

// Run Now is an explicit dispatch, so it works even while the master switch is
// paused — same contract the card list has.
async function scalRunNow(id) {
  scalCloseDetail();
  if (typeof runScheduleNow === 'function') return runScheduleNow(id);
  try {
    const res = await fetch(`${API_BASE}/api/schedule/${encodeURIComponent(id)}/run-now`, { method: 'POST' });
    const d = await res.json();
    if (typeof showToast === 'function') {
      showToast(res.ok && d.ok ? 'Schedule dispatched' : ('Run failed: ' + (d.error || res.statusText)), 4000);
    }
  } catch (e) {
    if (typeof showToast === 'function') showToast('Run failed', 4000);
  }
}

// Editing is a form that lives in the Scheduled Tasks modal — open that rather
// than growing a second copy of it here.
function scalEdit(id) {
  scalCloseDetail();
  if (typeof window.openScheduler === 'function') window.openScheduler();
  setTimeout(() => { if (typeof editSchedule === 'function') editSchedule(id); }, 250);
}

// ── Actions ─────────────────────────────────────────────────────────────────

function scalShiftWeek(delta) {
  schedCalWeekOffset = (delta === 0) ? 0 : schedCalWeekOffset + delta;
  renderScheduleCalendar();
}

// Day nav on narrow screens rolls over into the neighbouring week rather than
// stopping dead at Sunday/Saturday.
function scalShiftDay(delta) {
  if (delta === 0) {
    schedCalWeekOffset = 0;
    schedCalDayIndex = new Date().getDay();
  } else {
    let i = schedCalDayIndex + delta;
    if (i < 0) { i = 6; schedCalWeekOffset -= 1; }
    else if (i > 6) { i = 0; schedCalWeekOffset += 1; }
    schedCalDayIndex = i;
  }
  renderScheduleCalendar();
}

// Toggling changes the SCHEDULE, not one occurrence — so every block for it must
// restyle at once. Optimistic, then reconciled: a failed PUT rolls back rather
// than leaving the grid claiming something will run when it won't.
async function scalToggle(id, enabled) {
  const rec = _schedCalCache.find((s) => s.id === id);
  if (rec) rec.enabled = enabled;
  renderScheduleCalendar();
  try {
    const res = await fetch(`${API_BASE}/api/schedules/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    if (!res.ok) throw new Error('save failed');
    if (typeof showToast === 'function') showToast(enabled ? 'Schedule enabled' : 'Schedule disabled', 2200);
  } catch (e) {
    if (rec) rec.enabled = !enabled;
    renderScheduleCalendar();
    if (typeof showToast === 'function') showToast('Could not change that schedule', 4000);
    return;
  }
  if (typeof refreshScheduleBanner === 'function') refreshScheduleBanner();
  if (typeof refreshScheduleList === 'function') refreshScheduleList();
}

// ── The Calendar surface ────────────────────────────────────────────────────
// Its own modal, not a mode of the Scheduled Tasks modal. In there the grid sat
// below a full-width pause bar AND the whole Autonomous Stewards section, so on
// a phone the calendar got the last ~15% of the screen and the pause message
// appeared twice. Standalone, the grid gets the window.

async function openSchedulerCalendar() {
  const modalId = '__calendar';
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    await refreshScheduleCalendar({ keepWeek: true });
    return;
  }
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 900);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">Calendar</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="btn-header-action" style="padding:3px 10px;font-size:12px"
                onclick="openScheduler()" title="Manage schedules and stewards">Manage</button>
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div class="scal-surface"><div id="schedule-calendar"></div></div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);

  // The grid's own paused pill reads the scheduler.js mirror, which is only
  // populated when that modal has been opened at least once. Prime it here or a
  // fresh session paints a live-looking week while the switch is actually on.
  if (typeof window.refreshSchedulerPause === 'function') {
    try { await window.refreshSchedulerPause(); } catch (e) {}
  }
  schedCalDayIndex = new Date().getDay();
  await refreshScheduleCalendar();
}

// Pulls the same payload the list uses. Includes stewards deliberately: a steward
// run occupies the machine exactly like a schedule does, so hiding them would
// make the week look emptier than it is.
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

// Re-render on resize so crossing the 960px line swaps week-grid and day-view
// instead of leaving seven unreadable columns on a rotated phone.
if (typeof window !== 'undefined' && !window._scalResizeBound) {
  window._scalResizeBound = true;
  let t = null;
  window.addEventListener('resize', () => {
    if (!document.getElementById('schedule-calendar')) return;
    clearTimeout(t);
    t = setTimeout(renderScheduleCalendar, 180);
  });
}

// Inline on*= handlers resolve against the global object at click time, and this
// file is an ES module — every name referenced from generated HTML needs a
// bridge or it fails silently (see tools/smoke/inline-handler-scope-check.mjs).
window.openSchedulerCalendar = openSchedulerCalendar;
window.refreshScheduleCalendar = refreshScheduleCalendar;
window.renderScheduleCalendar = renderScheduleCalendar;
window.scalShiftWeek = scalShiftWeek;
window.scalShiftDay = scalShiftDay;
window.scalToggle = scalToggle;
window.scalOpenDetail = scalOpenDetail;
window.scalCloseDetail = scalCloseDetail;
window.scalRunNow = scalRunNow;
window.scalEdit = scalEdit;
window.scalBuildWeek = scalBuildWeek;          // exercised directly by the smoke test
window._scalParseCron = _scalParseCron;
window._scalTitle = _scalTitle;

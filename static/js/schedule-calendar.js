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

// View state is (anchor date, view) rather than a week offset plus a day index.
// With four ranges to support — day / 3 days / week / month — two coupled
// counters could not express "the 3 days starting Thursday" without special
// cases at every week boundary. One anchor + a length handles all four.
const SCAL_VIEWS = [
  { id: 'day', label: 'Day', days: 1 },
  { id: '3day', label: '3 Days', days: 3 },
  { id: 'week', label: 'Week', days: 7 },
  { id: 'month', label: 'Month', days: 0 },   // 0 = derived from the month
];
let schedCalView = 'week';
let schedCalAnchor = _scalToday();
let _schedCalCache = [];       // last /api/schedules payload (for toggles + re-render)

function _scalToday() { const d = new Date(); d.setHours(0, 0, 0, 0); return d; }

// The chosen view outlives the modal — reopening the Calendar should land where
// you left it, the way every calendar app behaves.
function _scalLoadView() {
  try {
    const v = localStorage.getItem('mc_cal_view');
    if (v && SCAL_VIEWS.some(x => x.id === v)) schedCalView = v;
  } catch (e) {}
}
function _scalSaveView() {
  try { localStorage.setItem('mc_cal_view', schedCalView); } catch (e) {}
}

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
//
// `count` days from `start` — 1, 3, 7 or a whole month's worth of weeks.
function scalBuildRange(schedules, start, count) {
  const days = [];
  for (let i = 0; i < count; i++) {
    const d = new Date(start);
    d.setDate(d.getDate() + i);
    days.push({ date: d, items: [] });
  }
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

// Kept as the 7-day case so existing callers (and the smoke guard) are unchanged.
function scalBuildWeek(schedules, weekStart) {
  return scalBuildRange(schedules, weekStart, 7);
}

// ── Render ──────────────────────────────────────────────────────────────────
//
// Four ranges, the same as every phone calendar: Day / 3 Days / Week / Month.
// Day, 3-day and week share the time grid (hours down the left, each run placed
// at its own time). Month is a different question — "which days have anything"
// rather than "when exactly" — so it gets a day-cell grid instead of 720 rows of
// mostly-empty clock.
//
// Seven columns on a phone are narrow, and that is fine: it is what Google
// Calendar does and what people already read. The switcher exists so a squeezed
// week is a choice rather than the only option.

const SCAL_ROW_H = 46;        // px per hour
const SCAL_EVENT_MIN_H = 26;  // a dispatch has no duration; this is a legible block
const SCAL_GUTTER = 54;       // hour-label column
const SCAL_MONTH_MAX = 3;     // chips per month cell before "+N more"

// Stable per-project accent so a project keeps its colour across views.
// A run is coloured by the PROJECT IT BELONGS TO, using the project's own
// accent — the same `modal_color` the dashboard tile and the Floor room draw.
// It used to be a hash of the project id, which meant the calendar was the one
// surface where a project's colour was something nobody could choose and
// nothing else agreed with. Changing a project's accent now moves the tile, the
// room and its runs together.
//
// Bare `allProjects`, like every other module reads it: it is a top-level `let`
// in index.html's classic script, so it lives in the global lexical scope and
// is NOT a property of `window`.
function _scalProjectColor(projectId) {
  let projects = [];
  try { projects = (typeof allProjects === 'undefined') ? [] : (allProjects || []); } catch (e) {}
  const p = projects.find(x => x && x.id === projectId);
  // An unset project is drawn in the default accent — which is precisely what
  // its tile and its colour picker already show it as. A random hue would be
  // more distinguishable and less TRUE, and the way to tell two of them apart
  // is now to give one a colour, which finally has a visible effect here.
  return (p && p.modal_color && p.modal_color.color) || 'var(--accent)';
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
// when set; otherwise the task's first line, trimmed. The full prompt lives in
// the detail sheet — a 90px block cannot hold a 400-word agent brief, and trying
// made every block look identical.
function _scalTitle(s) {
  const d = (s.description || '').trim();
  if (d) return d.length > 60 ? d.slice(0, 57) + '…' : d;
  const first = (s.task || '').trim().split(/\r?\n/)[0].trim();
  if (!first) return s.id;
  return first.length > 60 ? first.slice(0, 57) + '…' : first;
}

function _scalViewDef() {
  return SCAL_VIEWS.find(v => v.id === schedCalView) || SCAL_VIEWS[2];
}

// The days the current view covers. Week snaps to Sunday and month snaps to the
// Sunday on/before the 1st through the Saturday on/after the last — so the month
// grid is always whole weeks, like every calendar.
function _scalRangeStart() {
  const a = new Date(schedCalAnchor);
  if (schedCalView === 'week') { a.setDate(a.getDate() - a.getDay()); return a; }
  if (schedCalView === 'month') {
    const first = new Date(a.getFullYear(), a.getMonth(), 1);
    first.setDate(first.getDate() - first.getDay());
    return first;
  }
  return a;
}

function _scalRangeCount() {
  if (schedCalView !== 'month') return _scalViewDef().days;
  const a = schedCalAnchor;
  const first = new Date(a.getFullYear(), a.getMonth(), 1);
  const last = new Date(a.getFullYear(), a.getMonth() + 1, 0);
  const lead = first.getDay();
  const trail = 6 - last.getDay();
  return lead + last.getDate() + trail;
}

// The full day, always: 00:00 through 24:00.
//
// This used to clamp to the band the range actually used (first run − 1h to last
// run + 2h). That kept the grid dense, but it also meant the calendar quietly
// decided which hours existed — a week whose runs happen to sit between 03:00
// and 12:00 rendered a calendar that simply had no evening, and no scrolling
// could reveal one. A calendar is a picture of the whole day; the empty hours
// are information too ("nothing runs after 14:00" is a thing worth seeing).
//
// Density is preserved instead by opening scrolled to the first run of the range
// (see the scroll handling in _scalRenderTimeGrid), so you still land on the
// busy part without losing the rest.
function _scalHourRange() {
  return { start: 0, end: 24 };
}

// Greedy column packing so two runs at the same time sit side by side instead of
// on top of each other. Nominal 30-minute footprint purely for the layout.
function _scalLayoutDay(items) {
  const cols = [];
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

// The face of whoever runs it. The board reads as WHO is running, the same way
// the Floor does — a grid of identical chips cannot tell you that at a glance.
function _scalWhoHTML(s) {
  const cd = s && s.character_display;
  if (!cd || cd.missing) return '';
  return `<span class="scal-block-who">${window.avatarHTML(cd.avatar, 13)}</span>`;
}

function _scalBlockHTML(p, range) {
  const { it, mins, col, total } = p;
  const s = it.s;
  const live = _scalWillRun(s);
  const top = ((mins - range.start * 60) / 60) * SCAL_ROW_H;
  const width = 100 / total;
  return `<div class="scal-block${live ? '' : ' dead'}${total > 1 ? ' multi' : ''}"
      style="--scal-color:${_scalProjectColor(s.project_id)};top:${top.toFixed(1)}px;
             left:${(col * width).toFixed(2)}%;width:calc(${width.toFixed(2)}% - 3px);
             height:${SCAL_EVENT_MIN_H}px"
      onclick="scalOpenDetail('${esc(s.id)}')"
      title="${esc(_scalTitle(s))} — ${esc(_scalHhmm(it.when))}${
        s.character_display ? ' — runs as ' + esc(s.character_display.name) : ''}">
    <span class="scal-block-time">${esc(_scalHhmm(it.when))}</span>
    ${_scalWhoHTML(s)}
    <span class="scal-block-title">${esc(_scalTitle(s))}</span>
  </div>`;
}

// Always-running schedules (intervals, minute-level crons) belong in an all-day
// band at the top of the day, exactly where Outlook and Google put all-day
// events — they ARE part of the day's routine, and a separate section above the
// grid framed them as an exception to it. Every day column gets the same chips
// because that is what "every 30 minutes" means: it runs on all of them.
function _scalAllDayChipHTML(s) {
  const live = _scalWillRun(s);
  const every = typeof scheduleDescription === 'function' ? scheduleDescription(s) : '';
  return `<div class="scal-allday-chip${live ? '' : ' dead'}"
      style="--scal-color:${_scalProjectColor(s.project_id)}"
      onclick="scalOpenDetail('${esc(s.id)}')"
      title="${esc(_scalTitle(s))}${every ? ' — ' + esc(every) : ''}">${esc(_scalTitle(s))}</div>`;
}

function _scalAllDayHTML(days, always, cssCols) {
  if (!always.length) return '';
  const chips = always.map(_scalAllDayChipHTML).join('');
  const cells = days.map(() => `<div class="scal-allday-cell">${chips}</div>`).join('');
  return `<div class="scal-allday" style="grid-template-columns:${cssCols}">
    <div class="scal-allday-gutter" title="Runs continuously — too often to place on a clock">all-day</div>
    ${cells}
  </div>`;
}

function _scalStripHTML(cls, title, hint, schedules) {
  if (!schedules.length) return '';
  const pills = schedules.map((s) => {
    const live = _scalWillRun(s);
    return `<span class="scal-pill${live ? '' : ' dead'}"
        style="--scal-color:${_scalProjectColor(s.project_id)}"
        onclick="scalOpenDetail('${esc(s.id)}')"
        title="${esc(s.project_name || s.project_id)}">${esc(_scalTitle(s))}</span>`;
  }).join('');
  return `<div class="scal-strip ${cls}">
    <span class="scal-strip-head">${esc(title)}</span>
    <span class="scal-strip-body">${pills}</span>
    <span class="scal-strip-hint">${esc(hint)}</span>
  </div>`;
}

// ── Toolbar ─────────────────────────────────────────────────────────────────

function _scalRangeLabel(days) {
  const first = days[0].date, last = days[days.length - 1].date;
  const optMD = { month: 'short', day: 'numeric' };
  if (schedCalView === 'month') {
    return schedCalAnchor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
  }
  if (schedCalView === 'day') {
    return first.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
  }
  const sameMonth = first.getMonth() === last.getMonth();
  const a = first.toLocaleDateString(undefined, optMD);
  const b = last.toLocaleDateString(undefined, sameMonth ? { day: 'numeric' } : optMD);
  return `${a} – ${b}`;
}

function _scalToolbarHTML(days) {
  // ONE pause notice, and only here. The Scheduled-Tasks modal has its own
  // full-size pause bar; when the calendar lived inside that modal the two
  // stacked and said the same thing twice.
  const pausedPill = _scalPaused()
    ? `<button class="scal-paused-pill" onclick="toggleSchedulerPause()"
         title="Nothing below will fire. Click to resume scheduled runs.">Paused · Resume</button>`
    : '';
  const views = SCAL_VIEWS.map(v =>
    `<button class="scal-view-btn${schedCalView === v.id ? ' on' : ''}"
       onclick="scalSetView('${v.id}')">${esc(v.label)}</button>`).join('');
  // "Today" appears ONLY when today is off-screen. Permanently captioned it read
  // as a label for the range you were looking at — so every day looked like
  // today, even three weeks out. As a jump-home it still has to exist (getting
  // back from six months away by tapping ‹ is not a plan), it just has no
  // business sitting there when you are already on today.
  const now = new Date();
  const showToday = !days.some(d => _scalIsSameDay(d.date, now));
  const todayBtn = showToday
    ? `<button class="btn-header-action scal-today" onclick="scalShift(0)"
         title="Jump back to today">Today</button>`
    : '';
  return `<div class="scal-toolbar">
      <span class="scal-range">${esc(_scalRangeLabel(days))}</span>
      <span class="scal-nav">
        ${pausedPill}${todayBtn}
        <button class="btn-header-action" onclick="scalShift(-1)" title="Previous">&#8249;</button>
        <button class="btn-header-action" onclick="scalShift(1)" title="Next">&#8250;</button>
      </span>
    </div>
    <div class="scal-viewbar">${views}</div>`;
}

// ── Time grid (day / 3 days / week) ─────────────────────────────────────────

function _scalRenderTimeGrid(host, days, always, unparsed) {
  const today = new Date();
  const range = _scalHourRange();
  const gridH = (range.end - range.start) * SCAL_ROW_H;

  const hourLabels = [];
  for (let h = range.start; h < range.end; h++) {
    hourLabels.push(`<div class="scal-hour" style="height:${SCAL_ROW_H}px">
      <span>${String(h).padStart(2, '0')}:00</span></div>`);
  }

  const cols = days.map((d) => {
    const isToday = _scalIsSameDay(d.date, today);
    const blocks = _scalLayoutDay(d.items).map((p) => _scalBlockHTML(p, range)).join('');
    const lines = [];
    for (let h = range.start; h < range.end; h++) {
      // data-at makes each cell an addressable point in time, which is what
      // long-press / click-to-create reads (see _scalBindNewAt).
      const at = new Date(d.date);
      at.setHours(h, 0, 0, 0);
      lines.push(`<div class="scal-slot" data-at="${at.toISOString()}" style="height:${SCAL_ROW_H}px"></div>`);
    }
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

  const heads = days.map((d) => {
    const isToday = _scalIsSameDay(d.date, today);
    return `<div class="scal-head${isToday ? ' today' : ''}"
        onclick="scalGotoDay('${d.date.toISOString()}')" title="Jump to this day">
      <span class="scal-dow">${_SCAL_DAY_NAMES[d.date.getDay()]}</span>
      <span class="scal-dom">${d.date.getDate()}</span>
    </div>`;
  }).join('');

  const cssCols = `${SCAL_GUTTER}px repeat(${days.length}, minmax(0, 1fr))`;
  host.innerHTML = `
    ${_scalToolbarHTML(days)}
    ${_scalStripHTML('unparsed', 'Not placed', 'times could not be derived here — they still run', unparsed)}
    <div class="scal-time">
      <div class="scal-time-head" style="grid-template-columns:${cssCols}">
        <div class="scal-head-corner"></div>${heads}
      </div>
      ${_scalAllDayHTML(days, always, cssCols)}
      <div class="scal-time-body" id="scal-scroll">
        <div class="scal-time-cols" style="grid-template-columns:${cssCols};height:${gridH}px">
          <div class="scal-gutter">${hourLabels.join('')}</div>
          ${cols}
        </div>
      </div>
    </div>
    <div id="scal-detail-host"></div>`;

  // Open on the first run rather than the top of the band.
  const scroller = document.getElementById('scal-scroll');
  if (scroller) {
    const firstMin = days.reduce((acc, d) => {
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

// ── Month grid ──────────────────────────────────────────────────────────────
// Deliberately NOT the time grid at 1/30th scale. A month answers "which days
// have anything and roughly what" — so each day is a cell with up to three
// title chips and a "+N more" that drills into that day.

function _scalRenderMonth(host, days, always, unparsed) {
  const today = new Date();
  const month = schedCalAnchor.getMonth();

  const dowHeads = _SCAL_DAY_NAMES.map(n => `<div class="scal-mhead">${n}</div>`).join('');
  const cells = days.map((d) => {
    const isToday = _scalIsSameDay(d.date, today);
    const outside = d.date.getMonth() !== month;
    // Collapse repeats: a daily job appears once per cell, not once per run.
    const seen = new Set();
    const uniq = [];
    for (const it of d.items) {
      if (seen.has(it.s.id)) continue;
      seen.add(it.s.id);
      uniq.push(it);
    }
    const shown = uniq.slice(0, SCAL_MONTH_MAX);
    const chips = shown.map((it) => {
      const live = _scalWillRun(it.s);
      return `<div class="scal-mchip${live ? '' : ' dead'}"
          style="--scal-color:${_scalProjectColor(it.s.project_id)}"
          onclick="event.stopPropagation();scalOpenDetail('${esc(it.s.id)}')"
          title="${esc(_scalHhmm(it.when))} · ${esc(_scalTitle(it.s))}">${esc(_scalTitle(it.s))}</div>`;
    }).join('');
    const more = uniq.length > shown.length
      ? `<div class="scal-mmore">+${uniq.length - shown.length} more</div>` : '';
    return `<div class="scal-mcell${isToday ? ' today' : ''}${outside ? ' outside' : ''}"
        onclick="scalGotoDay('${d.date.toISOString()}')" title="Open this day">
      <div class="scal-mdate">${d.date.getDate()}</div>
      ${chips}${more}
    </div>`;
  }).join('');

  host.innerHTML = `
    ${_scalToolbarHTML(days)}
    ${_scalStripHTML('always', 'Always running', 'repeat too often to place on a day', always)}
    ${_scalStripHTML('unparsed', 'Not placed', 'times could not be derived here — they still run', unparsed)}
    <div class="scal-month">
      <div class="scal-mhead-row">${dowHeads}</div>
      <div class="scal-mgrid">${cells}</div>
    </div>
    <div id="scal-detail-host"></div>`;
}

// Direction of the pending navigation: +1 forward, -1 back, 0 = no movement
// (a re-render after a toggle shouldn't slide). Consumed and cleared by the
// render so it can never leak into an unrelated repaint.
let _scalSlideDir = 0;

function _scalPlaySlide(host) {
  const dir = _scalSlideDir;
  _scalSlideDir = 0;
  if (!dir) return;
  try {
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  } catch (e) {}
  const el = host.querySelector('.scal-time, .scal-month');
  if (!el) return;
  // Re-adding the class on an element that already has it does nothing, but the
  // grid is rebuilt from innerHTML each navigation so this is always a fresh
  // node. Cheap: one transform+opacity keyframe, no layout.
  el.classList.add(dir > 0 ? 'scal-slide-next' : 'scal-slide-prev');
}

function renderScheduleCalendar() {
  const host = document.getElementById('schedule-calendar');
  if (!host) return;
  const start = _scalRangeStart();
  const { days, always, unparsed } = scalBuildRange(_schedCalCache, start, _scalRangeCount());
  if (schedCalView === 'month') _scalRenderMonth(host, days, always, unparsed);
  else _scalRenderTimeGrid(host, days, always, unparsed);
  _scalPlaySlide(host);
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
    <div class="scal-detail" style="--scal-color:${_scalProjectColor(s.project_id)}">
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

// ── Navigation ──────────────────────────────────────────────────────────────

// One step = one view's worth: a day, three days, a week, a month. delta 0 =
// back to today, which is what every "Today" button means.
function scalShift(delta) {
  if (delta === 0) {
    // Jumping home can move either way — slide toward wherever today is.
    _scalSlideDir = schedCalAnchor > _scalToday() ? -1 : (schedCalAnchor < _scalToday() ? 1 : 0);
    schedCalAnchor = _scalToday();
    renderScheduleCalendar();
    return;
  }
  const a = new Date(schedCalAnchor);
  if (schedCalView === 'month') a.setMonth(a.getMonth() + delta);
  else a.setDate(a.getDate() + delta * _scalViewDef().days);
  schedCalAnchor = a;
  _scalSlideDir = delta > 0 ? 1 : -1;
  renderScheduleCalendar();
}

// Switching view keeps the anchor, so Week → Day lands on the day you were
// looking at rather than snapping back to today.
function scalSetView(view) {
  if (!SCAL_VIEWS.some(v => v.id === view)) return;
  schedCalView = view;
  _scalSaveView();
  scalCloseDetail();
  renderScheduleCalendar();
}

// Tapping a day header (or a month cell) drills into that day — the standard
// calendar gesture, and the only way "+N more" can mean anything.
function scalGotoDay(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return;
  d.setHours(0, 0, 0, 0);
  schedCalAnchor = d;
  schedCalView = 'day';
  _scalSaveView();
  renderScheduleCalendar();
}

// ── Actions ─────────────────────────────────────────────────────────────────

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
// appeared twice.

async function openSchedulerCalendar() {
  const modalId = '__calendar';
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    await refreshScheduleCalendar({ keepRange: true });
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
  _scalLoadView();
  schedCalAnchor = _scalToday();
  const surface = content.querySelector('.scal-surface');
  _scalBindSwipe(surface);
  _scalBindNewAt(surface);
  await refreshScheduleCalendar();
}

// Pulls the same payload the list uses. Includes stewards deliberately: a steward
// run occupies the machine exactly like a schedule does, so hiding them would
// make the week look emptier than it is.
async function refreshScheduleCalendar(opts) {
  if (!(opts && opts.keepRange)) schedCalAnchor = _scalToday();
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

// ── Swipe ───────────────────────────────────────────────────────────────────
// Left/right drags move by one view's worth, the same as the ‹ › buttons. Bound
// on the SURFACE (which survives re-renders) rather than the grid, whose
// innerHTML is replaced on every navigation.
//
// The grid scrolls vertically, so a swipe must not steal a scroll: the gesture
// only fires when the horizontal travel clearly dominates. Listeners are passive
// — we never preventDefault, so vertical scrolling keeps its native feel.
const SCAL_SWIPE_MIN = 55;      // px of horizontal travel before it counts
const SCAL_SWIPE_RATIO = 1.6;   // ...and how far it must beat the vertical travel

function _scalBindSwipe(surface) {
  if (!surface || surface._scalSwipeBound) return;
  surface._scalSwipeBound = true;
  let x0 = 0, y0 = 0, tracking = false;
  surface.addEventListener('touchstart', (e) => {
    if (e.touches.length !== 1) { tracking = false; return; }
    // A drag that starts on the detail sheet is scrolling the prompt, not paging.
    if (e.target.closest && e.target.closest('.scal-detail')) { tracking = false; return; }
    tracking = true;
    x0 = e.touches[0].clientX;
    y0 = e.touches[0].clientY;
  }, { passive: true });
  surface.addEventListener('touchend', (e) => {
    if (!tracking) return;
    tracking = false;
    const t = e.changedTouches && e.changedTouches[0];
    if (!t) return;
    const dx = t.clientX - x0;
    const dy = t.clientY - y0;
    if (Math.abs(dx) < SCAL_SWIPE_MIN) return;
    if (Math.abs(dx) < Math.abs(dy) * SCAL_SWIPE_RATIO) return;
    scalShift(dx < 0 ? 1 : -1);   // drag left = go forward, like every calendar
  }, { passive: true });
}

// ── Create at a time slot ───────────────────────────────────────────────────
// Long-press an empty hour cell (tap-and-hold on touch, plain click with a
// mouse — the way every calendar works) and the scheduler opens with the form
// already pointed at that moment. Bound on the SURFACE, which survives the
// innerHTML rebuild every navigation does, and delegated so it keeps working
// across view switches.
const SCAL_LONGPRESS_MS = 500;
const SCAL_LONGPRESS_SLOP = 10;   // px of travel before it's a scroll, not a press

function _scalBindNewAt(surface) {
  if (!surface || surface._scalNewBound) return;
  surface._scalNewBound = true;
  let timer = null, pressed = null, fired = false, x0 = 0, y0 = 0;

  const cancel = () => {
    if (timer) clearTimeout(timer);
    timer = null;
    if (pressed) pressed.classList.remove('scal-slot-armed');
    pressed = null;
  };
  // Blocks, the detail sheet and all-day chips all mean something else where
  // they sit; only bare grid cells offer to create.
  const slotFrom = (el) => {
    if (!el || !el.closest) return null;
    if (el.closest('.scal-block, .scal-detail, .scal-allday-chip')) return null;
    return el.closest('.scal-slot');
  };

  surface.addEventListener('touchstart', (e) => {
    cancel();
    fired = false;
    if (e.touches.length !== 1) return;
    const slot = slotFrom(e.target);
    if (!slot || !slot.dataset.at) return;
    x0 = e.touches[0].clientX;
    y0 = e.touches[0].clientY;
    pressed = slot;
    slot.classList.add('scal-slot-armed');
    const at = slot.dataset.at;
    timer = setTimeout(() => {
      fired = true;                     // so the trailing click doesn't fire too
      cancel();
      // The press completes under the finger with no visible transition, so
      // without a tick of haptic it reads as the page having glitched.
      try { if (navigator.vibrate) navigator.vibrate(15); } catch (err) {}
      scalNewAt(at);
    }, SCAL_LONGPRESS_MS);
  }, { passive: true });

  surface.addEventListener('touchmove', (e) => {
    if (!timer) return;
    const t = e.touches[0];
    if (!t || Math.abs(t.clientX - x0) > SCAL_LONGPRESS_SLOP
           || Math.abs(t.clientY - y0) > SCAL_LONGPRESS_SLOP) cancel();
  }, { passive: true });
  surface.addEventListener('touchend', cancel, { passive: true });
  surface.addEventListener('touchcancel', cancel, { passive: true });

  surface.addEventListener('click', (e) => {
    if (fired) { fired = false; return; }   // touch already handled this one
    // Mouse only. On touch a click follows every tap, and a stray tap on the
    // grid opening a form would be maddening — there, holding is the verb.
    if (window.matchMedia && !window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;
    const slot = slotFrom(e.target);
    if (slot && slot.dataset.at) scalNewAt(slot.dataset.at);
  });
}

// Open the scheduler's create form aimed at `iso`.
async function scalNewAt(iso) {
  const at = new Date(iso);
  if (!iso || isNaN(at.getTime())) return;
  scalCloseDetail();
  if (typeof window.openScheduler !== 'function') return;
  await window.openScheduler();
  if (typeof window.showScheduleForm !== 'function') return;
  // A one-shot in the past can never fire. Pressing a slot that has already
  // gone by means "this time of day", not "this instant" — so those get the
  // recurring form on that weekday instead of a dead Once.
  // days is 1=Mon..7=Sun; getDay() is 0=Sun..6=Sat.
  const past = at.getTime() < Date.now();
  window.showScheduleForm(past
    ? { schedule_type: 'daily', time: _scalHhmm(at), days: [((at.getDay() + 6) % 7) + 1] }
    : { schedule_type: 'once', run_at: at.toISOString(), delete_after_run: true });
}

// Inline on*= handlers resolve against the global object at click time, and this
// file is an ES module — every name referenced from generated HTML needs a
// bridge or it fails silently (see tools/smoke/inline-handler-scope-check.mjs).
window.openSchedulerCalendar = openSchedulerCalendar;
window.refreshScheduleCalendar = refreshScheduleCalendar;
window.renderScheduleCalendar = renderScheduleCalendar;
window.scalShift = scalShift;
window.scalSetView = scalSetView;
window.scalGotoDay = scalGotoDay;
window.scalToggle = scalToggle;
window.scalOpenDetail = scalOpenDetail;
window.scalCloseDetail = scalCloseDetail;
window.scalRunNow = scalRunNow;
window.scalEdit = scalEdit;
window.scalBuildWeek = scalBuildWeek;          // exercised directly by the smoke test
window.scalBuildRange = scalBuildRange;
window._scalParseCron = _scalParseCron;
window._scalTitle = _scalTitle;
window._scalBindSwipe = _scalBindSwipe;   // exercised by the smoke guard
window._scalBindNewAt = _scalBindNewAt;   // exercised by the smoke guard
window.scalNewAt = scalNewAt;

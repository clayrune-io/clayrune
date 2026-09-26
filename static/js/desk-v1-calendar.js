// Desk v1 (MC-977) — T4: Calendar view (frame 12f, docs/desk_v1_r0_plan.md;
// THE_DESK_V1_UI.md §3.3). Window-bridged module, no `import` (ground rule 1).
//
// Fixtures only (R0 has no backend store, no publishing, no spend): a
// reschedule drag mutates the in-memory DeskV1Fixtures objects directly
// through DeskV1Kit.commandBus, same "client-side over fixture data with
// Undo" contract T3 already established for review.
//
// Reached today via `deskV1Nav('calendar', {campaignId})` (desk-v1-shell.js's
// pre-registered route, T0a) — that IS "a way to mount it directly" while
// T2a's own List/Calendar toggle doesn't exist yet; once T2a lands, its
// toggle can call `deskV1RenderCalendar(el, params)` straight into its
// Content-tab body slot with no change needed here (same signature the shell
// already calls).
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // ── data resolution ───────────────────────────────────────────────────────
  function _fx() { return window.DeskV1Fixtures || {}; }
  function _campaign(id) { return (_fx().campaigns || []).find((c) => c.id === id) || null; }
  function _channel(id) { return (_fx().channels || []).find((c) => c.id === id) || null; }
  function _findFamilyVersion(versionId) {
    for (const fam of (_fx().families || [])) {
      const v = (fam.versions || []).find((x) => x.id === versionId);
      if (v) return { family: fam, version: v };
    }
    return null;
  }

  // A version's calendar instant, in priority order: its own scheduled/
  // published field, T4's own additive fixture section (planned-only gap
  // fill, see desk-v1-fixtures.js), then T3's review-detail whenISO (the
  // same "when" the full-width review's right rail already shows — reusing
  // it here keeps the two surfaces honest about the same piece). No date
  // anywhere means no chip: never invented (MET-01's "never fake it" spirit).
  function _versionWhen(version) {
    const iso = version.publishedAt || version.publishAt
      || (_fx().calendarSchedule || {})[version.id]
      || ((_fx().reviewDetail || {})[version.id] || {}).whenISO;
    if (!iso) return null;
    const d = new Date(iso);
    return isNaN(d) ? null : d;
  }

  // ── timezone (ground rule: user_timezone, empty = host tz). Same local
  // pattern T3 established (no shared kit helper exists yet — "a later
  // ticket can promote it"; this is that later ticket needing one too, but
  // promoting it means editing desk-v1-kit.js, T0b's own hot file, which
  // this ticket's file list doesn't include — stays local here as well). ──
  function _userTz() {
    const cfg = (typeof _globalConfig !== 'undefined' && _globalConfig) || {};
    return cfg.user_timezone || undefined;
  }
  function _fmtTime(d) {
    try {
      return new Intl.DateTimeFormat(undefined, { timeZone: _userTz(), hour: 'numeric', minute: '2-digit' }).format(d);
    } catch (e) { return ''; }
  }
  // Y-M-D in the configured tz — used to decide WHICH day column an instant
  // belongs to and whether it's "today", so a chip lands on the calendar day
  // the user would actually call today even when that differs from the host
  // machine's date (A14). The grid's own week/month boundaries are still
  // walked in host-local steps (matching every other date-nav control in
  // this codebase, e.g. schedule-calendar.js's `_scalStartOfWeek`) — full
  // tz-native week arithmetic is out of scope here; only placement/labels
  // are tz-aware. Documented as a deliberate scope line, not a silent gap.
  function _dayKey(d) {
    try {
      const parts = new Intl.DateTimeFormat('en-CA', { timeZone: _userTz(), year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(d);
      const get = (t) => (parts.find((p) => p.type === t) || {}).value;
      return `${get('year')}-${get('month')}-${get('day')}`;
    } catch (e) {
      return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    }
  }
  function _weekdayShort(d) {
    try { return new Intl.DateTimeFormat(undefined, { timeZone: _userTz(), weekday: 'short' }).format(d); }
    catch (e) { return ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()]; }
  }
  function _isWeekend(d) {
    try {
      const w = new Intl.DateTimeFormat('en-US', { timeZone: _userTz(), weekday: 'short' }).format(d);
      return w === 'Sat' || w === 'Sun';
    } catch (e) { const w = d.getDay(); return w === 0 || w === 6; }
  }

  // ── day-bucket skeleton — reuses schedule-calendar.js's `scalBuildRange`
  // (docs/desk_v1_r0_plan.md T4: "reuse helpers only ... range/label
  // (:146,:414)"). Called with an EMPTY schedules array so none of that
  // file's cron/interval/daily expansion ever runs — the schedules loop
  // (:155-202) short-circuits immediately, and what comes back is exactly
  // the `{date, items:[]}` skeleton (:147-152) this view needs, built once
  // instead of reimplemented.
  //
  // `_scalRangeLabel` (:414) and `_scalBindSwipe` (:868) are NOT reused: both
  // read/drive schedule-calendar's OWN module-level `schedCalView`/
  // `schedCalAnchor` state (`_scalBindSwipe` calls that file's `scalShift`
  // directly, hardcoded, not a caller-supplied callback), and neither is
  // exported for outside use. Calling them from here would either do nothing
  // (unexported) or silently move the unrelated Scheduled-Tasks calendar's
  // own view out from under it — a shared-state hazard, not a clean reuse.
  // This file has its own small range-label formatter and its own swipe
  // binding below, same technique, no shared state.
  function _rangeDays(st) {
    const count = st.view === 'month'
      ? new Date(st.anchor.getFullYear(), st.anchor.getMonth() + 1, 0).getDate()
      : 7;
    // Week starts Monday (§3.3 frame 12f: "Mon 29 ... Sun 5") — Sunday's
    // getDay()===0 needs a 6-day pullback, every other day pulls back
    // (day - 1) to reach that week's Monday.
    const start = st.view === 'month'
      ? new Date(st.anchor.getFullYear(), st.anchor.getMonth(), 1)
      : (() => { const d = new Date(st.anchor); const wd = d.getDay(); d.setDate(d.getDate() - (wd === 0 ? 6 : wd - 1)); return d; })();
    if (typeof window.scalBuildRange === 'function') return window.scalBuildRange([], start, count).days;
    const days = [];
    for (let i = 0; i < count; i++) { const d = new Date(start); d.setDate(d.getDate() + i); days.push({ date: d, items: [] }); }
    return days;
  }
  function _rangeLabel(days, view) {
    const first = days[0].date, last = days[days.length - 1].date;
    if (view === 'month') return new Intl.DateTimeFormat(undefined, { timeZone: _userTz(), month: 'long', year: 'numeric' }).format(first);
    const optMD = { timeZone: _userTz(), month: 'short', day: 'numeric' };
    const sameMonth = _dayKey(first).slice(0, 7) === _dayKey(last).slice(0, 7);
    const a = new Intl.DateTimeFormat(undefined, optMD).format(first);
    const b = new Intl.DateTimeFormat(undefined, sameMonth ? { timeZone: _userTz(), day: 'numeric' } : optMD).format(last);
    return `${a} – ${b}`;
  }

  // ── approval window (R0 fixture simulation — the real binding record,
  // invalidation and time-shift window are R1 infra per the gap map's #5
  // "missing"; this simulates just enough of the RULE for the interaction
  // to be testable on fixtures). Only a 'scheduled' version carries an
  // approval at all in this fixture set — a free time-shift within the same
  // calendar day it's already scheduled for, any other day asks and
  // invalidates (THE_DESK_V1_UI.md §3.3). ─────────────────────────────────
  function _approvalDayKey(version) {
    if (version.state !== 'scheduled') return null;
    const when = _versionWhen(version);
    return when ? _dayKey(when) : null;
  }

  // ── module state — one calendar mounted at a time in R0 (route-driven);
  // preserved across a re-render of the SAME campaign (drag/undo/nav calls
  // `_render()` in place) but reset when navigating to a different one. ────
  let _state = null;
  let _mountEl = null;
  let _dragState = null;

  function _today() { const d = new Date(); d.setHours(0, 0, 0, 0); return d; }
  function _ensureState(campaignId) {
    if (!_state || _state.campaignId !== campaignId) {
      _state = { campaignId, view: 'week', anchor: _today(), scope: 'campaign' };
    }
    return _state;
  }

  function deskV1RenderCalendar(el, params) {
    const campaignId = (params || {}).campaignId;
    const campaign = _campaign(campaignId);
    _mountEl = el;
    if (!campaign) {
      el.innerHTML = '<div class="desk-v1-stub"><div class="desk-v1-stub-body">No campaign selected.</div></div>';
      return;
    }
    _ensureState(campaignId);
    _render(campaign);
  }

  function _rows(st, campaign) {
    const channels = _fx().channels || [];
    if (st.scope === 'all') {
      const ids = new Set();
      (_fx().campaigns || []).forEach((c) => (c.channelIds || []).forEach((id) => ids.add(id)));
      return channels.filter((c) => ids.has(c.id));
    }
    return (campaign.channelIds || []).map((id) => _channel(id)).filter(Boolean);
  }

  function _familiesInScope(st, campaign) {
    const all = _fx().families || [];
    return st.scope === 'all' ? all : all.filter((f) => f.campaignId === campaign.id);
  }

  // `{channelId}|{dayKey}` -> [{family, version, when}], time-sorted.
  function _buildCells(st, campaign, days) {
    const dayKeys = new Set(days.map((d) => _dayKey(d.date)));
    const cells = {};
    for (const fam of _familiesInScope(st, campaign)) {
      for (const v of (fam.versions || [])) {
        const when = _versionWhen(v);
        if (!when) continue;
        const key = _dayKey(when);
        if (!dayKeys.has(key)) continue;
        const cellKey = v.channelId + '|' + key;
        (cells[cellKey] = cells[cellKey] || []).push({ family: fam, version: v, when });
      }
    }
    Object.values(cells).forEach((arr) => arr.sort((a, b) => a.when - b.when));
    return cells;
  }

  // A chip on a HELD channel must read Held regardless of the version's own
  // state (Dave's review, point 1) — the channel being unreachable overrides
  // whatever the content's own workflow state is, same "held overrides
  // either" rule channelCapabilityCopy already applies to the row header.
  function _effectiveState(item, channel) {
    return (channel && channel.health === 'held') ? 'held' : item.version.state;
  }

  function _chipHTML(item, channel) {
    const kit = window.DeskV1Kit;
    const state = _effectiveState(item, channel);
    const stateHTML = kit ? kit.stateLabelHTML(state) : esc(state);
    const dashed = state === 'planned' ? ' desk-v1-cal-chip-dashed' : '';
    const videoGlyph = item.family.kind === 'video'
      ? '<span class="desk-v1-cal-chip-video" aria-hidden="true">▶</span> ' : '';
    return `
      <button type="button" class="desk-v1-cal-chip${dashed}" data-chip-version="${esc(item.version.id)}"
          data-state="${esc(state)}" title="${esc(item.family.title)}">
        <span class="desk-v1-cal-chip-line1">
          <span class="desk-v1-cal-chip-time">${esc(_fmtTime(item.when))}</span>
          ${stateHTML}
        </span>
        <span class="desk-v1-cal-chip-title">${videoGlyph}${esc(item.family.title)}</span>
      </button>`;
  }

  function _rowHeaderHTML(channel, reviewMode) {
    const kit = window.DeskV1Kit;
    const held = channel.health === 'held';
    const badge = kit ? kit.channelBadge(channel, { reviewMode }) : esc(channel.label || channel.identity || '');
    const holdLine = held && kit
      ? `<div class="desk-v1-cal-row-hold">${esc(kit.channelCapabilityCopy(channel, reviewMode))}</div>`
      : '';
    return `
      <div class="desk-v1-cal-rowhead${held ? ' desk-v1-cal-rowhead-held' : ''}">
        ${badge}
        ${holdLine}
      </div>`;
  }

  function _dayHeaderHTML(day, todayKey) {
    const key = _dayKey(day.date);
    const cls = ['desk-v1-cal-daycol'];
    if (key === todayKey) cls.push('desk-v1-cal-daycol-today');
    if (_isWeekend(day.date)) cls.push('desk-v1-cal-daycol-weekend');
    return `<div class="${cls.join(' ')}" data-day-key="${esc(key)}">
      <span class="desk-v1-cal-daynum">${new Intl.DateTimeFormat(undefined, { timeZone: _userTz(), day: 'numeric' }).format(day.date)}</span>
      <span class="desk-v1-cal-dayname">${esc(_weekdayShort(day.date))}</span>
    </div>`;
  }

  function _gridHTML(st, campaign, days, rows, cells, todayKey) {
    const header = `<div class="desk-v1-cal-row desk-v1-cal-row-header">
      <div class="desk-v1-cal-rowhead desk-v1-cal-rowhead-corner"></div>
      ${days.map((d) => _dayHeaderHTML(d, todayKey)).join('')}
    </div>`;
    const body = rows.map((ch) => {
      const cellsHTML = days.map((d) => {
        const key = _dayKey(d.date);
        const items = cells[ch.id + '|' + key] || [];
        const cls = ['desk-v1-cal-cell', 'pd-drop-target'];
        if (key === todayKey) cls.push('desk-v1-cal-cell-today');
        if (_isWeekend(d.date)) cls.push('desk-v1-cal-cell-weekend');
        return `<div class="${cls.join(' ')}" data-channel-id="${esc(ch.id)}" data-day-key="${esc(key)}">
          <span class="desk-v1-cal-cell-preview"></span>
          ${items.map((it) => _chipHTML(it, ch)).join('')}
        </div>`;
      }).join('');
      return `<div class="desk-v1-cal-row">${_rowHeaderHTML(ch, campaign.rules && campaign.rules.reviewMode)}${cellsHTML}</div>`;
    }).join('');
    return `<div class="desk-v1-cal-grid-wrap"><div class="desk-v1-cal-grid" style="--desk-v1-cal-cols:${days.length}">${header}${body}</div></div>`;
  }

  // Phone (§11): "Calendar on phone defaults to an agenda list grouped by
  // day." Same cell data, transposed grouping — day, then channel — which a
  // CSS reflow of the grid markup can't produce (it's a different axis), so
  // this renders its own markup; a media query (desk-v1.css) toggles which
  // of the two is visible.
  function _agendaHTML(days, rows, cells, todayKey) {
    const byChannel = {};
    rows.forEach((c) => { byChannel[c.id] = c; });
    const dayBlocks = days.map((d) => {
      const key = _dayKey(d.date);
      const items = [];
      rows.forEach((ch) => (cells[ch.id + '|' + key] || []).forEach((it) => items.push(Object.assign({ channel: ch }, it))));
      if (!items.length) return '';
      const label = new Intl.DateTimeFormat(undefined, { timeZone: _userTz(), weekday: 'long', month: 'short', day: 'numeric' }).format(d.date);
      return `<div class="desk-v1-cal-agenda-day${key === todayKey ? ' desk-v1-cal-agenda-day-today' : ''}">
        <div class="desk-v1-cal-agenda-daylabel">${esc(label)}</div>
        ${items.map((it) => {
          const state = _effectiveState(it, it.channel);
          const videoGlyph = it.family.kind === 'video' ? '▶ ' : '';
          return `
          <button type="button" class="desk-v1-cal-agenda-item" data-chip-version="${esc(it.version.id)}">
            <span class="desk-v1-cal-agenda-time">${esc(_fmtTime(it.when))}</span>
            <span class="desk-v1-cal-agenda-channel">${esc(it.channel.label || '')}</span>
            ${window.DeskV1Kit ? window.DeskV1Kit.stateLabelHTML(state) : ''}
            <span class="desk-v1-cal-agenda-title">${videoGlyph}${esc(it.family.title)}</span>
          </button>`;
        }).join('')}
      </div>`;
    }).filter(Boolean).join('');
    return `<div class="desk-v1-cal-agenda">${dayBlocks || '<div class="desk-v1-stub-empty">Nothing on the calendar this range.</div>'}</div>`;
  }

  // §3.3: "This campaign ▾ | All campaigns, ‹ week ›, Week ▾ | Month" — one
  // row, scope as a single dropdown (not two pills; Dave's review point 6).
  // The List/Calendar toggle in frame 12f's same row belongs to T2a's
  // Content-tab slot (this view renders INTO that slot per the T0a contract
  // — see the file banner comment). T2a doesn't exist yet, so this is a
  // non-interactive placeholder marking where it goes; T2a's own toggle
  // replaces it wholesale, nothing here to rewire when it lands.
  function _toolbarHTML(st, days) {
    return `
      <div class="desk-v1-cal-toolbar">
        <div class="desk-v1-cal-toolbar-left">
          <div class="desk-v1-cal-viewtoggle-placeholder" aria-hidden="true" title="List/Calendar toggle — owned by T2a's Content-tab slot, not built yet">
            <span class="desk-v1-cal-viewtoggle-btn">&#9776; List</span>
            <span class="desk-v1-cal-viewtoggle-btn on">&#9638; Calendar</span>
          </div>
          <select class="desk-v1-cal-viewselect" data-cal-scope-select>
            <option value="campaign"${st.scope === 'campaign' ? ' selected' : ''}>This campaign</option>
            <option value="all"${st.scope === 'all' ? ' selected' : ''}>All campaigns</option>
          </select>
        </div>
        <div class="desk-v1-cal-toolbar-right">
          <div class="desk-v1-cal-nav">
            <button type="button" class="desk-v1-cal-navbtn" data-cal-shift="-1" aria-label="Previous">&lsaquo;</button>
            <span class="desk-v1-cal-rangelabel">${esc(_rangeLabel(days, st.view))}</span>
            <button type="button" class="desk-v1-cal-navbtn" data-cal-shift="1" aria-label="Next">&rsaquo;</button>
          </div>
          <select class="desk-v1-cal-viewselect" data-cal-view>
            <option value="week"${st.view === 'week' ? ' selected' : ''}>Week</option>
            <option value="month"${st.view === 'month' ? ' selected' : ''}>Month</option>
          </select>
        </div>
      </div>`;
  }

  function _render(campaign) {
    const el = _mountEl;
    if (!el) return;
    const st = _state;
    const days = _rangeDays(st);
    const rows = _rows(st, campaign);
    const cells = _buildCells(st, campaign, days);
    const todayKey = _dayKey(new Date());

    el.innerHTML = `
      <div class="desk-v1-calendar">
        ${_toolbarHTML(st, days)}
        ${_gridHTML(st, campaign, days, rows, cells, todayKey)}
        ${_agendaHTML(days, rows, cells, todayKey)}
        <div class="desk-v1-cal-footnote">Calendar is a view of Content, not a separate place. Dragging a chip reschedules it (approval covers content, not time, within your rules). Click a chip to open its review.</div>
      </div>`;

    _bind(el, campaign, days, rows, cells);
  }

  function _bind(el, campaign, days, rows, cells) {
    const st = _state;

    const scopeSel = el.querySelector('[data-cal-scope-select]');
    if (scopeSel) scopeSel.onchange = () => { st.scope = scopeSel.value; _render(campaign); };
    el.querySelectorAll('[data-cal-shift]').forEach((b) => b.onclick = () => {
      const dir = parseInt(b.dataset.calShift, 10);
      const a = new Date(st.anchor);
      if (st.view === 'month') a.setMonth(a.getMonth() + dir); else a.setDate(a.getDate() + dir * 7);
      st.anchor = a;
      _render(campaign);
    });
    const viewSel = el.querySelector('[data-cal-view]');
    if (viewSel) viewSel.onchange = () => { st.view = viewSel.value; _render(campaign); };

    el.querySelectorAll('[data-chip-version], [data-chip-version]').forEach((chip) => {
      chip.onclick = (e) => {
        if (chip.classList.contains('desk-v1-cal-drag-suppress-click')) { chip.classList.remove('desk-v1-cal-drag-suppress-click'); return; }
        window.deskV1Nav('review', { campaignId: campaign.id, versionId: chip.dataset.chipVersion });
      };
      chip.onkeydown = (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _openRescheduleKeyboard(chip, campaign); }
      };
    });

    _bindDrag(el, campaign);
    _bindSwipe(el, campaign);
  }

  // ── keyboard reschedule path (§10 parity: every drag also has a click/
  // keyboard path). Enter on a focused chip opens a tiny inline popover with
  // a native datetime-local input instead of a drag — same command, same
  // approval-window gate, same Undo. ──────────────────────────────────────
  function _openRescheduleKeyboard(chip, campaign) {
    const versionId = chip.dataset.chipVersion;
    const found = _findFamilyVersion(versionId);
    if (!found) return;
    const current = _versionWhen(found.version);
    const iso = current ? new Date(current.getTime() - current.getTimezoneOffset() * 60000).toISOString().slice(0, 16) : '';
    const pop = document.createElement('div');
    pop.className = 'desk-v1-cal-kbd-reschedule';
    pop.innerHTML = `
      <label>Move to
        <input type="datetime-local" value="${esc(iso)}" data-kbd-when>
      </label>
      <button type="button" data-kbd-move>Move</button>
      <button type="button" data-kbd-cancel>Cancel</button>`;
    chip.parentElement.appendChild(pop);
    const close = () => { pop.remove(); chip.focus(); };
    pop.querySelector('[data-kbd-cancel]').onclick = close;
    pop.querySelector('[data-kbd-move]').onclick = () => {
      const val = pop.querySelector('[data-kbd-when]').value;
      if (!val) return close();
      const newWhen = new Date(val);
      close();
      _reschedule(found, newWhen, campaign);
    };
  }

  // ── reschedule command — the approval-window gate (see _approvalDayKey
  // above), then a commandBus command so it gets the same optimistic update
  // + Undo toast + announcement every other Desk v1 drop already gets. ────
  function _reschedule(found, newWhen, campaign) {
    const { family, version } = found;
    const priorIso = version.publishedAt || version.publishAt || null;
    const priorSchedule = (_fx().calendarSchedule || {})[version.id];
    const priorState = version.state;
    const approvalKey = _approvalDayKey(version);
    const movingOutsideWindow = approvalKey && approvalKey !== _dayKey(newWhen);

    if (movingOutsideWindow) {
      const proceed = window.confirm('Moving this needs approval again — continue?');
      if (!proceed) return;
    }

    window.DeskV1Kit.commandBus.run({
      label: `Moved "${family.title}" to ${_fmtTime(newWhen)} · ${new Intl.DateTimeFormat(undefined, { timeZone: _userTz(), month: 'short', day: 'numeric' }).format(newWhen)}`,
      do: () => {
        const iso = newWhen.toISOString();
        if (version.publishedAt) version.publishedAt = iso;
        else if (version.publishAt) version.publishAt = iso;
        else { window.DeskV1Fixtures.calendarSchedule = window.DeskV1Fixtures.calendarSchedule || {}; window.DeskV1Fixtures.calendarSchedule[version.id] = iso; }
        if (movingOutsideWindow) version.state = 'needs_review';
        _render(campaign);
      },
      undo: () => {
        if (version.publishedAt) version.publishedAt = priorIso;
        else if (version.publishAt) version.publishAt = priorIso;
        else if (priorSchedule !== undefined) window.DeskV1Fixtures.calendarSchedule[version.id] = priorSchedule;
        version.state = priorState;
        _render(campaign);
      },
    });
  }

  // ── drag-to-reschedule (§10, §3.3). PointerDrag (T0c) owns the mechanics;
  // this owns what a target IS (a day cell) and what a drop DOES (reschedule
  // preserving the chip's time-of-day, changing only its day, per the
  // "drag a chip to another day or time" reading where day is the axis a
  // grid drag naturally expresses). ────────────────────────────────────────
  function _bindDrag(el, campaign) {
    el.querySelectorAll('.desk-v1-cal-chip').forEach((chip) => {
      chip.addEventListener('pointerdown', (e) => {
        const versionId = chip.dataset.chipVersion;
        const found = _findFamilyVersion(versionId);
        if (!found) return;
        window.PointerDrag.begin(chip, e, {
          isDragActive: () => !!_dragState,
          getDragState: () => _dragState,
          setDragState: (s) => { _dragState = s; },
          data: { versionId },
          draggingClass: 'desk-v1-cal-chip-dragging',
          ghostClass: 'pd-ghost desk-v1-cal-chip-ghost',
          ghostHTML: () => chip.innerHTML,
          ghostRotationDeg: -3,
          ghostOffsetX: 18, ghostOffsetY: 18,
          onActivate: () => {
            chip.classList.add('desk-v1-cal-drag-suppress-click');
          },
          onMove: (st, x, y) => {
            el.querySelectorAll('.desk-v1-cal-cell.pd-drop-hover').forEach((c) => { c.classList.remove('pd-drop-hover'); const p = c.querySelector('.desk-v1-cal-cell-preview'); if (p) p.textContent = ''; });
            const target = document.elementFromPoint(x, y);
            const cell = target && target.closest && target.closest('.desk-v1-cal-cell');
            if (!cell) return;
            cell.classList.add('pd-drop-hover');
            const preview = cell.querySelector('.desk-v1-cal-cell-preview');
            if (preview) {
              const when = _versionWhen(found.version) || new Date();
              const label = new Intl.DateTimeFormat(undefined, { timeZone: _userTz(), weekday: 'short', month: 'short', day: 'numeric' }).format(new Date(cell.dataset.dayKey + 'T00:00:00'));
              preview.textContent = `Move to ${label}, ${_fmtTime(when)}`;
            }
          },
          onDrop: (st, x, y) => {
            const target = document.elementFromPoint(x, y);
            const cell = target && target.closest && target.closest('.desk-v1-cal-cell');
            return cell ? cell.dataset.dayKey : null;
          },
          afterDrop: (st, dayKey) => {
            if (!dayKey) return;
            const when = _versionWhen(found.version) || new Date();
            const [y, m, d] = dayKey.split('-').map((n) => parseInt(n, 10));
            const newWhen = new Date(when);
            newWhen.setFullYear(y, m - 1, d);
            if (dayKey === _dayKey(when)) return; // dropped back on its own day
            _reschedule(found, newWhen, campaign);
          },
          onTeardown: () => {
            el.querySelectorAll('.desk-v1-cal-cell.pd-drop-hover').forEach((c) => { c.classList.remove('pd-drop-hover'); const p = c.querySelector('.desk-v1-cal-cell-preview'); if (p) p.textContent = ''; });
          },
        });
      });
    });
  }

  // Own swipe binding, same slop/ratio technique schedule-calendar.js uses
  // (:868) but calling THIS file's own shift, not that file's `scalShift` —
  // see the _rangeDays comment above for why the original isn't reused
  // as-is. Bound to BOTH the grid-wrap and the agenda list: only one of the
  // two is visible at a time (desk-v1.css media query, §11 phone default is
  // agenda), and whichever is hidden renders at zero size, so it never
  // receives a touch — binding both means swipe works on whichever the
  // viewport actually shows instead of silently only working on desktop.
  const CAL_SWIPE_MIN = 55, CAL_SWIPE_RATIO = 1.6;
  function _bindSwipe(el, campaign) {
    el.querySelectorAll('.desk-v1-cal-grid-wrap, .desk-v1-cal-agenda').forEach((surface) => {
      let x0 = 0, y0 = 0, tracking = false;
      surface.addEventListener('touchstart', (e) => {
        if (e.touches.length !== 1) { tracking = false; return; }
        tracking = true; x0 = e.touches[0].clientX; y0 = e.touches[0].clientY;
      }, { passive: true });
      surface.addEventListener('touchend', (e) => {
        if (!tracking) return;
        tracking = false;
        const t = e.changedTouches && e.changedTouches[0];
        if (!t) return;
        const dx = t.clientX - x0, dy = t.clientY - y0;
        if (Math.abs(dx) < CAL_SWIPE_MIN || Math.abs(dx) < Math.abs(dy) * CAL_SWIPE_RATIO) return;
        const a = new Date(_state.anchor);
        const dir = dx < 0 ? 1 : -1;
        if (_state.view === 'month') a.setMonth(a.getMonth() + dir); else a.setDate(a.getDate() + dir * 7);
        _state.anchor = a;
        _render(campaign);
      }, { passive: true });
    });
  }

  window.deskV1RenderCalendar = deskV1RenderCalendar;
})();

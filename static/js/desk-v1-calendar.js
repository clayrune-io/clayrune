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
  function _project(id) { return (_fx().projects || []).find((p) => p.id === id) || null; }
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

  // ── R2-9: When stop fields (Cadence · Min gap · Term · Post cap, §4.1 ④,
  // frame 6). Cadence and min gap are inherited/clamped display only — the
  // campaign never edits them here (min gap has no campaign-level field at
  // all, §3 row 22: it moved to the project's own `presence.ceilings`); Term
  // end and Post cap are the two real editable bounds (§4.1: "Required to
  // launch: cadence, end date and/or post cap"), writing straight to
  // `plan.end` so kit.js's existing `boundsWiden`/`_endWiden` (desk-v1-
  // campaign.js `_renderLaunchPanel`) picks up a later end date as a widen
  // and an earlier one as a narrow with no new wiring needed here. ─────────
  function _effectiveMinGapH(campaign, project) {
    const ceilings = (project && project.presence && project.presence.ceilings) || {};
    const accounts = (campaign.plan && campaign.plan.accounts) || [];
    let g = null;
    accounts.forEach((chId) => {
      const c = ceilings[chId];
      if (c && c.min_gap_h != null) g = g == null ? c.min_gap_h : Math.min(g, c.min_gap_h);
    });
    return g;
  }
  // Raw project ceiling, independent of whether the campaign's own cadence
  // happens to be under/equal to it (kit.js's `_effectiveCadence` only
  // reports `fromProject` when the campaign left cadence unset — Dave review
  // pass 1: the row's required format is "≤ n/wk from <project>'s ceiling"
  // UNCONDITIONALLY whenever a ceiling exists, same pattern `_effectiveMinGapH`
  // above already uses for the read-only Min gap field).
  function _projectCadenceCeiling(campaign, project) {
    const ceilings = (project && project.presence && project.presence.ceilings) || {};
    const accounts = (campaign.plan && campaign.plan.accounts) || [];
    let c = null;
    accounts.forEach((chId) => {
      const ceil = ceilings[chId];
      if (ceil && ceil.per_week != null) c = c == null ? ceil.per_week : Math.min(c, ceil.per_week);
    });
    return c;
  }
  function _cadenceFieldText(campaign, project) {
    const result = (window.DeskV1Kit && window.DeskV1Kit.validatePlan(campaign.plan, project)) || {};
    const eff = result.effective || {};
    if (eff.cadence_per_week == null) return 'Not set';
    const ceiling = _projectCadenceCeiling(campaign, project);
    return ceiling != null
      ? `≤${eff.cadence_per_week}/wk from ${project ? project.name : 'the project'}'s ceiling`
      : `${eff.cadence_per_week}/wk`;
  }
  function _minGapFieldText(campaign, project) {
    const g = _effectiveMinGapH(campaign, project);
    return g == null ? 'Not set' : `${g}h (inherited, read-only)`;
  }
  function _fieldsHTML(campaign, project) {
    const plan = campaign.plan || {};
    const term = campaign.term || {};
    const startLabel = term.starts ? new Intl.DateTimeFormat(undefined, { timeZone: _userTz(), month: 'short', day: 'numeric' }).format(new Date(term.starts + 'T00:00:00')) : '—';
    const endDate = (plan.end && plan.end.date) || term.ends || '';
    const postCap = plan.end && plan.end.post_cap != null ? plan.end.post_cap : '';
    return `
      <div class="desk-v1-cal-fields">
        <div class="desk-v1-cal-field"><span class="desk-v1-cal-field-label">Cadence</span>
          <span class="desk-v1-cal-field-value" data-cal-field-cadence title="${esc(_cadenceFieldText(campaign, project))}"><span>${esc(_cadenceFieldText(campaign, project))}</span></span></div>
        <div class="desk-v1-cal-field"><span class="desk-v1-cal-field-label">Min gap</span>
          <span class="desk-v1-cal-field-value" data-cal-field-mingap title="${esc(_minGapFieldText(campaign, project))}"><span>${esc(_minGapFieldText(campaign, project))}</span></span></div>
        <div class="desk-v1-cal-field"><span class="desk-v1-cal-field-label">Term</span>
          <span class="desk-v1-cal-field-value">${esc(startLabel)} – <input type="date" class="desk-v1-cal-field-date" data-cal-field-end value="${esc(endDate)}"></span></div>
        <div class="desk-v1-cal-field"><span class="desk-v1-cal-field-label">Post cap</span>
          <input type="number" min="0" class="desk-v1-cal-field-num" data-cal-field-postcap placeholder="none" value="${esc(postCap)}"></div>
      </div>`;
  }
  function _bindFields(el, campaign) {
    const endEl = el.querySelector('[data-cal-field-end]');
    if (endEl) endEl.onchange = () => {
      campaign.plan.end = campaign.plan.end || {};
      campaign.plan.end.date = endEl.value || null;
    };
    const capEl = el.querySelector('[data-cal-field-postcap]');
    if (capEl) capEl.onchange = () => {
      campaign.plan.end = campaign.plan.end || {};
      campaign.plan.end.post_cap = capEl.value === '' ? null : parseInt(capEl.value, 10);
    };
  }

  // ── R2-9: own slots (`when.slots[]`, §5's data addendum, frame 6). A slot
  // the user drags onto the calendar reserves a posting window before any
  // piece exists for it — solid, "Your slot", may stay empty. An agent
  // proposal is the SAME array, `origin:'agent', state:'suggested'`, dashed,
  // until Ron accepts it. Suggest only ever fills an existing user slot (adds
  // `.filled`) or adds its own agent-origin entries — it never edits or
  // removes a `origin:'user'` slot's own id/at (§8 row: "Suggest never moves
  // or deletes a user slot"). ────────────────────────────────────────────────
  function _ownSlots(campaign) {
    campaign.when = campaign.when || {};
    campaign.when.slots = campaign.when.slots || [];
    return campaign.when.slots;
  }
  function _weekKey(d) {
    const monday = new Date(d);
    const wd = monday.getDay();
    monday.setDate(monday.getDate() - (wd === 0 ? 6 : wd - 1));
    monday.setHours(0, 0, 0, 0);
    return _dayKey(monday);
  }
  // Refuses a NEW user slot that would push the week over the effective
  // cadence, or that lands inside the effective min-gap window of another
  // slot — returns the reason string, or null if the slot is allowed.
  function _slotRefusal(at, campaign, project) {
    const slots = _ownSlots(campaign).filter((s) => s.origin === 'user');
    const result = (window.DeskV1Kit && window.DeskV1Kit.validatePlan(campaign.plan, project)) || {};
    const cap = (result.effective || {}).cadence_per_week;
    if (cap != null) {
      const wk = _weekKey(at);
      const countThisWeek = slots.filter((s) => _weekKey(new Date(s.at)) === wk).length;
      if (countThisWeek + 1 > cap) return `over ${cap}/wk`;
    }
    const minGap = _effectiveMinGapH(campaign, project);
    if (minGap != null) {
      for (const s of slots) {
        const diffH = Math.abs(new Date(s.at).getTime() - at.getTime()) / 3600000;
        if (diffH < minGap) return `within ${minGap}h of another slot`;
      }
    }
    return null;
  }
  function _showSlotRefusal(el, message) {
    const banner = el.querySelector('[data-slot-refusal]');
    if (!banner) return;
    banner.textContent = message;
    banner.classList.add('desk-v1-cal-slot-refusal-show');
  }
  function _clearSlotRefusal(el) {
    const banner = el.querySelector('[data-slot-refusal]');
    if (!banner) return;
    banner.textContent = '';
    banner.classList.remove('desk-v1-cal-slot-refusal-show');
  }
  function _createOwnSlot(dayKey, campaign, project, el) {
    const [y, m, d] = dayKey.split('-').map((n) => parseInt(n, 10));
    const at = new Date(y, m - 1, d, 14, 0, 0, 0); // fixed default creation time (frame 6: both mocked slots land at 14:00)
    const reason = _slotRefusal(at, campaign, project);
    if (reason) { _showSlotRefusal(el, reason); return; }
    const slot = { id: 'slot-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 7), at: at.toISOString(), origin: 'user' };
    window.DeskV1Kit.commandBus.run({
      label: `Added your slot ${new Intl.DateTimeFormat(undefined, { timeZone: _userTz(), weekday: 'short', month: 'short', day: 'numeric' }).format(at)} · ${_fmtTime(at)}`,
      do: () => { _clearSlotRefusal(el); _ownSlots(campaign).push(slot); _render(campaign); },
      undo: () => { const arr = _ownSlots(campaign); const i = arr.findIndex((s) => s.id === slot.id); if (i >= 0) arr.splice(i, 1); _render(campaign); },
    });
  }
  // Test/Suggest seam: fills the first unfilled user slot (or a named one)
  // with a suggested piece, WITHOUT touching that slot's id/at/origin — the
  // real ② How "Suggest" task (desk-v1-how.js) is the eventual caller; this
  // is the same contract the calendar itself needs regardless of caller.
  function deskV1CalendarSuggestFill(campaignId, fill) {
    const campaign = _campaign(campaignId);
    if (!campaign) return null;
    const slots = _ownSlots(campaign).filter((s) => s.origin === 'user');
    const target = (fill && fill.slotId) ? slots.find((s) => s.id === fill.slotId) : slots.find((s) => !s.filled);
    if (!target) return null;
    target.filled = { title: (fill && fill.title) || 'Suggested piece', platform: (fill && fill.platform) || '', channelId: (fill && fill.channelId) || null };
    if (_mountEl && _state && _state.campaignId === campaignId) _render(campaign);
    return target;
  }

  // ── R2-9: legend (§8 row: "legend `your own slot` / `agent-suggested`") —
  // the literal wording, so own-vs-suggested reads as WORDS (this text +
  // each chip's own line, `_slotChipHTML` below), never inferred from the
  // dashed border alone (§8: "not border alone"). ─────────────────────────
  function _legendHTML() {
    return `
      <div class="desk-v1-cal-legend">
        <span class="desk-v1-cal-legend-item desk-v1-cal-legend-user"><span class="desk-v1-cal-legend-dot"></span>your own slot</span>
        <span class="desk-v1-cal-legend-item desk-v1-cal-legend-agent"><span class="desk-v1-cal-legend-dot"></span>agent-suggested</span>
      </div>`;
  }

  // Same letter-glyph convention `_platformGlyph` uses in
  // desk-v1-conversations.js — duplicated locally (not shared via
  // DeskV1Kit) rather than reaching into a parallel builder's file; it's
  // two lines and the two call sites can drift independently. Reads
  // `filled.platform` directly, same as the title-line's own `· platform`
  // suffix below — NOT the resolved channel, which only exists when
  // `filled.channelId` is also set and would leave most filled slots (a
  // platform without a specific channel picked yet) with no glyph at all.
  // An unfilled slot has no platform yet, so it gets a neutral dot.
  const _SLOT_PLATFORM_GLYPH = { x: '𝕏', linkedin: 'in', blog: '≡', web: '◉' };
  function _slotGlyph(platform) { return platform ? (_SLOT_PLATFORM_GLYPH[platform] || '◉') : '•'; }

  // ── R2-9: a slot chip — solid "Your slot" (origin:'user') or dashed
  // "Agent suggested" (origin:'agent'); once filled it also shows the
  // piece + platform, and a held DESTINATION channel still overrides to
  // `⚠ held` (same rule `_effectiveState` applies to a dated version's
  // chip above, applied here to a filled slot's optional `filled.channelId`
  // — a slot with no channel yet, or a channel that isn't held, never shows
  // it). aria-label carries the same "your own slot"/"agent-suggested"
  // words as the legend, so the distinction survives without colour or
  // border for assistive tech. ─────────────────────────────────────────────
  function _slotChipHTML(slot) {
    const isUser = slot.origin === 'user';
    const at = new Date(slot.at);
    const channel = slot.filled && slot.filled.channelId ? _channel(slot.filled.channelId) : null;
    const held = !!(channel && channel.health === 'held');
    const originWord = isUser ? 'Your slot' : 'Agent suggested';
    const cls = ['desk-v1-cal-slotchip', isUser ? 'desk-v1-cal-slotchip-user' : 'desk-v1-cal-slotchip-agent'];
    if (!isUser) cls.push('desk-v1-cal-chip-dashed');
    // Dave review pass 4: line 1 is glyph + time ONLY and must never
    // truncate ("2:00… Your …" was line 1 carrying time+origin+held all
    // at once). The label — origin word when unfilled, piece title (+
    // held) once filled — is line 2, where an ellipsis is allowed. Title
    // no longer carries a "· platform" suffix (dropped, not just moved):
    // the glyph on line 1 already says platform, and mockup examples
    // ("Install video", "Retro FAQ ⚠ held") have no suffix — keeping it
    // was pushing "⚠ held" past the ellipsis at a ~98px month column
    // (measured: "Retro FAQ · li…" with held silently gone).
    const line2 = slot.filled
      ? `<span class="desk-v1-cal-slotchip-title">${esc(slot.filled.title)}${held ? ' <span class="desk-v1-cal-slotchip-held">⚠ held</span>' : ''}</span>`
      : `<span class="desk-v1-cal-slotchip-origin">${esc(originWord)}</span>`;
    return `
      <button type="button" class="${cls.join(' ')}" data-slot-id="${esc(slot.id)}" data-slot-origin="${esc(slot.origin)}"
          aria-label="${esc(isUser ? 'your own slot' : 'agent-suggested')}${held ? ' · held' : ''}, ${esc(_fmtTime(at))}">
        <span class="desk-v1-cal-slotchip-line1">
          <span class="desk-v1-cal-slotchip-glyph" aria-hidden="true">${esc(_slotGlyph(slot.filled && slot.filled.platform))}</span>
          <span class="desk-v1-cal-slotchip-time">${esc(_fmtTime(at))}</span>
        </span>
        ${line2}
      </button>`;
  }

  // ── R2-9: the "Your slots" band — its own `.desk-v1-cal-row` sharing the
  // grid's `--desk-v1-cal-cols` column template (same technique every other
  // row already uses), so it lines up under the right day regardless of
  // week/month view without touching the channel rows at all. The rowhead
  // carries the one draggable affordance ("+ New slot") the user drags onto
  // a day cell to create an own slot (§8: "the user drags on the calendar
  // to create OWN slots") — dragging FROM a day cell itself would collide
  // with the existing reschedule-drag surface those same cells already are.
  function _slotsByDay(campaign) {
    const byDay = {};
    _ownSlots(campaign).forEach((s) => { const k = _dayKey(new Date(s.at)); (byDay[k] = byDay[k] || []).push(s); });
    Object.values(byDay).forEach((arr) => arr.sort((a, b) => new Date(a.at) - new Date(b.at)));
    return byDay;
  }
  function _slotRowHTML(days, campaign) {
    const byDay = _slotsByDay(campaign);
    const cellsHTML = days.map((d) => {
      const key = _dayKey(d.date);
      const items = byDay[key] || [];
      return `<div class="desk-v1-cal-cell desk-v1-cal-slotcell pd-drop-target" data-day-key="${esc(key)}">
        <span class="desk-v1-cal-cell-preview"></span>
        <span class="desk-v1-cal-slotcell-add" data-slot-cell-add aria-label="Drag to add your own slot" title="Drag to add your own slot">+</span>
        ${items.map((s) => _slotChipHTML(s)).join('')}
      </div>`;
    }).join('');
    return `<div class="desk-v1-cal-row desk-v1-cal-row-slots">
      <div class="desk-v1-cal-rowhead desk-v1-cal-rowhead-slots">
        <span class="desk-v1-cal-slot-rowlabel">Your slots</span>
        <button type="button" class="desk-v1-cal-slot-handle" data-slot-handle aria-label="Drag to add your own slot">+ New slot</button>
      </div>
      ${cellsHTML}
    </div>`;
  }

  // ── R2-9: the Unscheduled tray (§4.1 row 141, carried into this ticket's
  // acceptance: "the Unscheduled tray stays (below the fold in frame 6)").
  // Same undated-version set A14/MET-01 already keeps OFF the grid (a date-
  // less version never gets an invented chip) — this is that same set's one
  // legitimate home: a card the user can drag onto a day to set its first
  // date, via the SAME `_reschedule` command every other drop already uses
  // (Undo/toast/announce included), just with no "moving FROM a day" side
  // to gate on approval. ────────────────────────────────────────────────────
  function _unscheduledItems(st, campaign) {
    const items = [];
    for (const fam of _familiesInScope(st, campaign)) {
      for (const v of (fam.versions || [])) {
        if (!_versionWhen(v)) items.push({ family: fam, version: v });
      }
    }
    return items;
  }
  function _unscheduledHTML(st, campaign) {
    const items = _unscheduledItems(st, campaign);
    if (!items.length) return '';
    return `
      <div class="desk-v1-cal-unscheduled">
        <div class="desk-v1-cal-unscheduled-label">Unscheduled</div>
        <div class="desk-v1-cal-unscheduled-tray">
          ${items.map((it) => `
            <button type="button" class="desk-v1-cal-unscheduled-card" data-unsched-version="${esc(it.version.id)}" title="${esc(it.family.title)}">
              ${esc(it.family.title)}
            </button>`).join('')}
        </div>
      </div>`;
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

  // R2-6: the How stop's `Suggest What / When / Where` task (desk-v1-how.js
  // via desk-v1-campaign.js's `_runSuggestTask`) writes a cadence proposal
  // into `campaign.how.suggested.when` — no dedicated accept/edit UI yet
  // (R2-9's job, same as the rest of this stop's real controls), so this
  // ticket only has to prove the suggestion reaches ④ (row acceptance:
  // "④ a cadence proposal").
  function _suggestedWhenBannerHTML(campaign) {
    const when = campaign.how && campaign.how.suggested && campaign.how.suggested.when;
    if (!when) return '';
    return `<div class="desk-v1-camp-suggested-banner">? suggested: ${esc(when.label || '')}</div>`;
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
      (_fx().campaigns || []).forEach((c) => (c.plan.accounts || []).forEach((id) => ids.add(id)));
      return channels.filter((c) => ids.has(c.id));
    }
    return (campaign.plan.accounts || []).map((id) => _channel(id)).filter(Boolean);
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

  function _rowHeaderHTML(channel) {
    const kit = window.DeskV1Kit;
    const held = channel.health === 'held';
    const badge = kit ? kit.channelBadge(channel) : esc(channel.label || channel.identity || '');
    const holdLine = held && kit
      ? `<div class="desk-v1-cal-row-hold">${esc(kit.channelCapabilityCopy(channel))}</div>`
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
      return `<div class="desk-v1-cal-row">${_rowHeaderHTML(ch)}${cellsHTML}</div>`;
    }).join('');
    // R2-9's own slots band sits right under the header, ahead of the real
    // channel rows — one more `.desk-v1-cal-row` in the same grid, so it
    // shares the column template and never has to touch `body` above.
    const slotRow = _slotRowHTML(days, campaign);
    return `<div class="desk-v1-cal-grid-wrap"><div class="desk-v1-cal-grid" style="--desk-v1-cal-cols:${days.length}">${header}${slotRow}${body}</div></div>`;
  }

  // ── R2-9b (Dave follow-up): Month is a REAL 7-column calendar grid
  // (Mon..Sun, every week of the month visible, chips inside day cells) —
  // not the week grid's per-channel-row technique stretched to ~30 columns,
  // which only ever showed ~6 days before needing a horizontal scroll the
  // modal's ~690px never gives it. A month cell has no single channel to key
  // on (frame 6 mixes @ron/LinkedIn/blog content and both own+agent slots in
  // the SAME cell), so this is a plain 2D grid instead of `_gridHTML`'s row
  // stack: 7 weekday header cells then N*7 day cells, all direct children of
  // one `grid-template-columns: repeat(7, ...)` container — CSS Grid's own
  // row-wrapping needs no JS-computed grid-row/-column math.
  //
  // Each day cell still carries `.desk-v1-cal-cell.pd-drop-target` (content
  // reschedule drop target, `_bindDrag`) AND `.desk-v1-cal-slotcell` +
  // `data-slot-cell-add` (own-slot-create drag, `_bindSlotCreate`) — both
  // existing drag flows target those classes by selector regardless of which
  // grid shape rendered them, so own-slot drag keeps working on these cells
  // with no changes to either binder.
  function _monthCellsFlat(days) {
    const first = days[0].date;
    const wd = first.getDay();
    const lead = wd === 0 ? 6 : wd - 1; // Monday-start pad, same rule _rangeDays uses
    const flat = new Array(lead).fill(null).concat(days);
    while (flat.length % 7 !== 0) flat.push(null);
    return flat;
  }
  const _MONTH_WEEKDAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  function _monthDayCellHTML(day, rows, cells, todayKey, slotsByDay) {
    if (!day) return '<div class="desk-v1-cal-cell desk-v1-cal-monthcell desk-v1-cal-monthcell-outmonth"></div>';
    const key = _dayKey(day.date);
    const entries = [];
    rows.forEach((ch) => (cells[ch.id + '|' + key] || []).forEach((it) => entries.push({ when: it.when, html: _chipHTML(it, ch) })));
    (slotsByDay[key] || []).forEach((s) => entries.push({ when: new Date(s.at), html: _slotChipHTML(s) }));
    entries.sort((a, b) => a.when - b.when);
    const cls = ['desk-v1-cal-cell', 'desk-v1-cal-monthcell', 'desk-v1-cal-slotcell', 'pd-drop-target'];
    if (key === todayKey) cls.push('desk-v1-cal-cell-today');
    if (_isWeekend(day.date)) cls.push('desk-v1-cal-cell-weekend');
    return `<div class="${cls.join(' ')}" data-day-key="${esc(key)}">
      <span class="desk-v1-cal-cell-preview"></span>
      <div class="desk-v1-cal-monthcell-head">
        <span class="desk-v1-cal-monthcell-daynum">${new Intl.DateTimeFormat(undefined, { timeZone: _userTz(), day: 'numeric' }).format(day.date)}</span>
        <span class="desk-v1-cal-slotcell-add" data-slot-cell-add aria-label="Drag to add your own slot" title="Drag to add your own slot">+</span>
      </div>
      <div class="desk-v1-cal-monthcell-items">${entries.map((e) => e.html).join('')}</div>
    </div>`;
  }
  function _monthGridHTML(campaign, days, rows, cells, todayKey) {
    const slotsByDay = _slotsByDay(campaign);
    const header = _MONTH_WEEKDAY_NAMES.map((w) => `<div class="desk-v1-cal-monthgrid-headcell">${esc(w)}</div>`).join('');
    const body = _monthCellsFlat(days).map((d) => _monthDayCellHTML(d, rows, cells, todayKey, slotsByDay)).join('');
    return `<div class="desk-v1-cal-grid-wrap desk-v1-cal-grid-wrap-month"><div class="desk-v1-cal-monthgrid-head">${header}</div><div class="desk-v1-cal-monthgrid">${body}</div></div>`;
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
    const project = _project(campaign.projectId);

    el.innerHTML = `
      <div class="desk-v1-calendar">
        ${_suggestedWhenBannerHTML(campaign)}
        ${_fieldsHTML(campaign, project)}
        <div class="desk-v1-cal-slot-refusal" data-slot-refusal></div>
        ${_toolbarHTML(st, days)}
        ${_legendHTML()}
        ${st.view === 'month' ? _monthGridHTML(campaign, days, rows, cells, todayKey) : _gridHTML(st, campaign, days, rows, cells, todayKey)}
        ${_agendaHTML(days, rows, cells, todayKey)}
        ${_unscheduledHTML(st, campaign)}
        <div class="desk-v1-cal-footnote">Calendar is a view of Content, not a separate place. Dragging a chip reschedules it (approval covers content, not time, within your rules). Click a chip to open its review.</div>
      </div>`;

    _bindFields(el, campaign);
    _bind(el, campaign, days, rows, cells, project);
  }

  function _bind(el, campaign, days, rows, cells, project) {
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
    _bindSlotCreate(el, campaign, project);
    _bindUnscheduledDrag(el, campaign);
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

  // ── R2-9: drag-to-create an own slot. Same PointerDrag mechanics as
  // `_bindDrag` above; the drop target is always scoped to
  // `.desk-v1-cal-slotcell` (the slots band only) so a slot never lands on
  // a channel row. A separate local `_slotDragState` — concurrent with
  // `_dragState` is impossible (one pointer), but keeping them apart means
  // this drag's teardown can never stomp a chip-drag's own state object.
  //
  // Two draggable sources share this mechanics (Dave review pass 1, point 2:
  // "the user drags ON THE CALENDAR to create own slots" — a single handle
  // parked at the row's far-left edge means a month view drag has to cross
  // ~30 columns to reach its target day, which isn't "on the calendar"):
  //   - the rowhead's "+ New slot" handle (kept — harmless, and in Week view
  //     with only 7 columns it's a fine single reach-any-day affordance);
  //   - a small add affix rendered INSIDE every slot cell (`_slotRowHTML`),
  //     so in Month view the drag can start on (or right next to) the day
  //     it's headed for, same as dragging directly on the calendar. ────────
  let _slotDragState = null;
  function _beginSlotCreateDrag(sourceEl, e, el, campaign, project) {
    window.PointerDrag.begin(sourceEl, e, {
      isDragActive: () => !!_slotDragState,
      getDragState: () => _slotDragState,
      setDragState: (s) => { _slotDragState = s; },
      data: {},
      draggingClass: 'desk-v1-cal-slot-handle-dragging',
      ghostClass: 'pd-ghost desk-v1-cal-slotchip-ghost',
      // Dave review pass 3, point 2 (superseded by pass 4's line1/line2
      // split, `_slotChipHTML` below): line 1 is glyph + time only, "Your
      // slot" is line 2 — matches the real chip's structure so the ghost
      // previews what will actually land.
      ghostHTML: () => '<span class="desk-v1-cal-slotchip-line1"><span class="desk-v1-cal-slotchip-glyph" aria-hidden="true">•</span><span class="desk-v1-cal-slotchip-time">New</span></span><span class="desk-v1-cal-slotchip-origin">Your slot</span>',
      ghostRotationDeg: -3,
      ghostOffsetX: 18, ghostOffsetY: 18,
      onMove: (st, x, y) => {
        el.querySelectorAll('.desk-v1-cal-slotcell.pd-drop-hover').forEach((c) => { c.classList.remove('pd-drop-hover'); const p = c.querySelector('.desk-v1-cal-cell-preview'); if (p) p.textContent = ''; });
        const target = document.elementFromPoint(x, y);
        const cell = target && target.closest && target.closest('.desk-v1-cal-slotcell');
        if (!cell) return;
        cell.classList.add('pd-drop-hover');
        const preview = cell.querySelector('.desk-v1-cal-cell-preview');
        if (preview) {
          const label = new Intl.DateTimeFormat(undefined, { timeZone: _userTz(), weekday: 'short', month: 'short', day: 'numeric' }).format(new Date(cell.dataset.dayKey + 'T00:00:00'));
          preview.textContent = `Add your slot, ${label} 14:00`;
        }
      },
      onDrop: (st, x, y) => {
        const target = document.elementFromPoint(x, y);
        const cell = target && target.closest && target.closest('.desk-v1-cal-slotcell');
        return cell ? cell.dataset.dayKey : null;
      },
      afterDrop: (st, dayKey) => {
        if (!dayKey) return;
        _createOwnSlot(dayKey, campaign, project, el);
      },
      onTeardown: () => {
        el.querySelectorAll('.desk-v1-cal-slotcell.pd-drop-hover').forEach((c) => { c.classList.remove('pd-drop-hover'); const p = c.querySelector('.desk-v1-cal-cell-preview'); if (p) p.textContent = ''; });
      },
    });
  }
  function _bindSlotCreate(el, campaign, project) {
    const handle = el.querySelector('[data-slot-handle]');
    if (handle) handle.addEventListener('pointerdown', (e) => _beginSlotCreateDrag(handle, e, el, campaign, project));
    el.querySelectorAll('[data-slot-cell-add]').forEach((affix) => {
      affix.addEventListener('pointerdown', (e) => _beginSlotCreateDrag(affix, e, el, campaign, project));
    });
  }

  // ── R2-9: the Unscheduled tray's own drag-to-schedule. Drop target is
  // scoped to the piece's OWN channel row (`data-channel-id` match) — a
  // grid drop only ever changes `data-day-key` for the channel it's already
  // in (same invariant `_bindDrag`'s reschedule already keeps), so a piece
  // with no date yet can't be dropped onto a channel it doesn't belong to.
  // Reuses `_reschedule` wholesale (Undo/toast/announce, approval gate —
  // moot here since a never-scheduled version has no `_approvalDayKey`). ───
  function _bindUnscheduledDrag(el, campaign) {
    el.querySelectorAll('.desk-v1-cal-unscheduled-card').forEach((card) => {
      card.addEventListener('pointerdown', (e) => {
        const versionId = card.dataset.unschedVersion;
        const found = _findFamilyVersion(versionId);
        if (!found) return;
        const channelId = found.version.channelId;
        const targetSel = `.desk-v1-cal-cell[data-channel-id="${channelId}"]`;
        window.PointerDrag.begin(card, e, {
          isDragActive: () => !!_dragState,
          getDragState: () => _dragState,
          setDragState: (s) => { _dragState = s; },
          data: { versionId },
          draggingClass: 'desk-v1-cal-unscheduled-dragging',
          ghostClass: 'pd-ghost desk-v1-cal-chip-ghost',
          ghostHTML: () => card.innerHTML,
          ghostRotationDeg: -3,
          ghostOffsetX: 18, ghostOffsetY: 18,
          onMove: (st, x, y) => {
            el.querySelectorAll('.desk-v1-cal-cell.pd-drop-hover').forEach((c) => { c.classList.remove('pd-drop-hover'); const p = c.querySelector('.desk-v1-cal-cell-preview'); if (p) p.textContent = ''; });
            const target = document.elementFromPoint(x, y);
            const cell = target && target.closest && target.closest(targetSel);
            if (!cell) return;
            cell.classList.add('pd-drop-hover');
            const preview = cell.querySelector('.desk-v1-cal-cell-preview');
            if (preview) {
              const label = new Intl.DateTimeFormat(undefined, { timeZone: _userTz(), weekday: 'short', month: 'short', day: 'numeric' }).format(new Date(cell.dataset.dayKey + 'T00:00:00'));
              preview.textContent = `Schedule ${label}`;
            }
          },
          onDrop: (st, x, y) => {
            const target = document.elementFromPoint(x, y);
            const cell = target && target.closest && target.closest(targetSel);
            return cell ? cell.dataset.dayKey : null;
          },
          afterDrop: (st, dayKey) => {
            if (!dayKey) return;
            const [y, m, d] = dayKey.split('-').map((n) => parseInt(n, 10));
            const newWhen = new Date(y, m - 1, d, 12, 0, 0, 0); // fixed default time, same technique as _createOwnSlot
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
  window.deskV1CalendarSuggestFill = deskV1CalendarSuggestFill;
})();

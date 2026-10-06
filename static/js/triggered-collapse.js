// Triggered chat messages collapse to a one-line indicator.
//
// A chat is mostly Ron and the agent. Turns Ron did NOT type — a dispatched
// agent's "[dispatched agent finished]" callback, a timer's scheduled prompt,
// a <task-notification>, a rollover/handoff block — used to render as a
// full right-aligned "> Ron: …" bubble each, burying the conversation. They
// now render as one muted line ("Tobin finished: completed · 10:42") that
// expands on click / Enter / Space to the original bubble, byte-for-byte what
// it was before. DISPLAY ONLY: the buffer, the transcript and the text the
// agent receives are untouched.
//
// Detection is a STRICT prefix match on the message body (after the
// "> <label>: " the server writes for every user-role line), plus one
// server-set marker: the label "[scheduled run]" (scheduler_routes live
// append, mc/triggered_turns.py for a fresh scheduled dispatch). Nothing is
// guessed from content: a line is collapsed only if it opens with one of the
// exact formats the server itself composes (see KINDS). Anything else —
// every message Ron types — returns null and renders as before.
//
// State: collapsed is the default for live lines and history alike, and the
// markup is rebuilt from the line every render, so a refreshModal rebuild can
// never leave a stale collapsed/expanded node behind. The only memory is
// `_open`, the content keys the reader expanded; it is read once when a row is
// built and never decides WHETHER a row renders (the dedupe-by-JS-set bug in
// memory discovery-askuserquestion-dom-dedup was a Set suppressing renders).
//
// ES-module cross-boundary rule: rich-text.js / resume-preview.js reach this
// through window.

const _ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const _e = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => _ESC[c]);

const _SCHEDULED_LABEL = '[scheduled run]';
const _SCHEDULED_HEADER_RE = /^\[Scheduled run · ([^\]\n]*)\]\s*/;
const _FINISHED_RE = /^\[dispatched agent finished\]\s+(.+?)\s+\(session\s+[\w-]+\)\s+ended with status=([\w-]+)/;
const _QUESTION_RE = /^\[dispatched agent asked a question\]\s+(.+?)\s+\(session\s+[\w-]+\)/;
const _HANDOFF_RE = /^=== Prior conversation, started on ([\w.-]+)/;

// Split "> <label>: <body>" the way rich-text.js isSlashCommandLine does: the
// label is user-configurable, so cut at the first ": ".
function _parse(line) {
  const t = String(line || '').trim();
  if (!t.startsWith('> ')) return null;
  const rest = t.slice(2);
  const sep = rest.indexOf(': ');
  if (sep === -1) return null;
  return { label: rest.slice(0, sep), body: rest.slice(sep + 2).trimStart() };
}

function _clip(s, n) {
  const one = String(s || '').replace(/\s+/g, ' ').trim();
  return one.length > n ? one.slice(0, n - 1) + '…' : one;
}

function _scheduledTitle(task) {
  const first = (String(task || '').split('\n').find((l) => l.trim()) || '').trim();
  const m = /^\[([^\]]+)\]/.exec(first);
  return _clip(m ? m[1] : first, 60) || 'timer';
}

// → null (not triggered) | { kind, icon, label, time } where `time` is a
// "HH:MM" string taken from the message itself when it carries one.
function triggeredInfo(line) {
  const p = _parse(line);
  if (!p) return null;
  const { label, body } = p;

  const sched = _SCHEDULED_HEADER_RE.exec(body);
  if (label === _SCHEDULED_LABEL || sched) {
    const task = sched ? body.slice(sched[0].length) : body;
    const hm = sched && /\b(\d{1,2}:\d{2})\b/.exec(sched[1]);
    return { kind: 'schedule', icon: '⏰', label: `Scheduled: ${_scheduledTitle(task)}`, time: hm ? hm[1] : '' };
  }
  let m = _FINISHED_RE.exec(body);
  if (m) return { kind: 'dispatch', icon: '↩', label: `${_clip(m[1], 40)} finished: ${m[2]}`, time: '' };
  if (/^\[dispatched agent finished\]/.test(body)) {
    return { kind: 'dispatch', icon: '↩', label: 'Dispatched agent finished', time: '' };
  }
  m = _QUESTION_RE.exec(body);
  if (m) return { kind: 'question', icon: '❓', label: `${_clip(m[1], 40)} asked a question`, time: '' };
  if (/^\[dispatched agent asked a question\]/.test(body)) {
    return { kind: 'question', icon: '❓', label: 'Dispatched agent asked a question', time: '' };
  }
  if (/^<task-notification\b/.test(body)) {
    const st = /<status>\s*([^<\s][^<]*?)\s*<\/status>/.exec(body);
    const sm = /<summary>\s*([^<]*?)\s*<\/summary>/.exec(body);
    const head = st ? `Background task ${st[1]}` : 'Background task update';
    return { kind: 'notification', icon: '🔔', label: sm && sm[1] ? `${head} — ${_clip(sm[1], 70)}` : head, time: '' };
  }
  m = _HANDOFF_RE.exec(body);
  if (m) return { kind: 'handoff', icon: '🔗', label: `Handoff: continued from ${m[1]}`, time: '' };
  if (body.startsWith('=== Mid-task rollover state ===')
      || body.startsWith('Your context was rolled over mid-task because')) {
    return { kind: 'handoff', icon: '🔗', label: 'Rollover: context handed to a fresh session', time: '' };
  }
  if (/^=== Still waiting on \d+ dispatched session/.test(body)) {
    return { kind: 'handoff', icon: '🔗', label: 'Waiting on dispatched sessions', time: '' };
  }
  return null;
}

function isTriggeredLine(line) {
  return triggeredInfo(line) !== null;
}

// `ts`: the same contract as appendAgentLine's dateHint — undefined = "this
// line is happening now", a string/Date = known, anything else = unknown.
function _timeOf(ts, own) {
  if (own) return own;
  if (ts === undefined) ts = new Date();
  if (!ts) return '';
  const d = new Date(ts);
  if (isNaN(d.getTime())) return '';
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function _key(line) {
  let h = 5381;
  const s = String(line || '').trim();
  for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
  return `${s.length}:${h.toString(36)}`;
}

const _open = new Set();  // content keys the reader expanded — see header

// Wrap the bubble HTML `innerHTML` (what escPromptWithImages built for `line`)
// as a collapsed row + hidden body. Returns it unchanged for a typed message.
function wrapTriggeredPrompt(line, innerHTML, ts) {
  const info = triggeredInfo(line);
  if (!info) return innerHTML;
  const key = _key(line);
  const open = _open.has(key);
  const time = _timeOf(ts, info.time);
  return `<div class="trig-row" role="button" tabindex="0" aria-expanded="${open}" data-trig-key="${_e(key)}" data-trig-kind="${info.kind}">`
    + `<span class="trig-ico" aria-hidden="true">${info.icon}</span>`
    + `<span class="trig-label">${_e(info.label)}</span>`
    + (time ? `<span class="trig-time">· ${_e(time)}</span>` : '')
    + `<span class="trig-chev" aria-hidden="true">▸</span></div>`
    + `<div class="trig-body"${open ? '' : ' hidden'}>${innerHTML}</div>`;
}

function _toggle(row) {
  const bubble = row.closest('.agent-line-triggered');
  const body = bubble && bubble.querySelector(':scope > .trig-body');
  if (!body) return;
  const open = row.getAttribute('aria-expanded') !== 'true';
  row.setAttribute('aria-expanded', String(open));
  body.hidden = !open;
  const key = row.dataset.trigKey;
  if (key) { if (open) _open.add(key); else _open.delete(key); }
}

// One delegated listener: rows are rebuilt from HTML strings on every render,
// so per-node handlers (and inline onclick, which would need a window global)
// would be lost with the DOM.
document.addEventListener('click', (ev) => {
  const row = ev.target && ev.target.closest && ev.target.closest('.trig-row');
  if (row) _toggle(row);
});
document.addEventListener('keydown', (ev) => {
  if (ev.key !== 'Enter' && ev.key !== ' ') return;
  const row = ev.target && ev.target.closest && ev.target.closest('.trig-row');
  if (!row || ev.target !== row) return;
  ev.preventDefault();
  _toggle(row);
});

window.triggeredInfo = triggeredInfo;
window.isTriggeredLine = isTriggeredLine;
window.wrapTriggeredPrompt = wrapTriggeredPrompt;

// Context-rollover / starting-fresh notices render as a small inline marker.
//
// When a session is handed to a fresh one, the server writes a bracketed
// system line into the chat log ("[Context rolled over mid-task — continuing
// in a fresh session]", "[Session context is 106k tokens — starting fresh]",
// ...). Long chats collected dozens, each a full centered row with 8px margins,
// breaking the reading flow (MC-1101). Each now renders as a hairline divider
// carrying a tiny pill ("↻ Fresh start · 106k"); the full original sentence is
// the pill's tooltip and expands inline on click / Enter / Space (touch has no
// hover, so the click path is the one that works at 390px). The event stays
// discoverable; it just stops costing a reading row. DISPLAY ONLY: the log line,
// transcript and agent_log are untouched.
//
// Detection is a STRICT match on the exact sentences the server composes in
// mc/blueprints/agent_routes.py (_auto_fresh_handoff, the resume-failed
// fallbacks, the mid-turn rollover ack). Nothing is guessed from content: any
// other bracketed line returns null and keeps rendering as a status line.
// rich-text.js asks isRolloverLine() from agentLineCls (row class) and
// formatAgentText (inner HTML), which are what every chat renderer shares.
//
// State: collapsed is the default, markup is rebuilt from the line on every
// render, and `_open` (content keys the reader expanded) only seeds the initial
// state of a freshly built marker, never whether it renders.
//
// ES-module cross-boundary rule: rich-text.js reaches this through window.

const _ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const _e = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => _ESC[c]);

const _DASH = '\\s*[—–-]\\s*';
const _ROLLOVER_RE = /^Context rolled over mid-task\b/;
const _TOKENS_RE = new RegExp(`^Session context is (\\d+)k tokens${_DASH}starting fresh$`);
const _SIZE_RE = new RegExp(`^Session transcript too large \\((\\d+(?:\\.\\d+)?) MB\\)${_DASH}starting fresh$`);
const _RESTART_RE = new RegExp(
  `^(?:Resume of session \\S+ failed|Resume produced no output before exiting|Resumed session process exited)${_DASH}restarting fresh$`);
const _RECOVER_RE = /^Previous rollover was interrupted before it produced a transcript\b/;

// → null (not a rollover notice) | { kind, label, text } where `text` is the
// full original sentence without its brackets.
function rolloverInfo(line) {
  const t = String(line || '').trim();
  if (t.length < 3 || t[0] !== '[' || t[t.length - 1] !== ']') return null;
  const text = t.slice(1, -1).trim();
  if (_ROLLOVER_RE.test(text)) return { kind: 'rollover', label: 'Rolled over', text };
  let m = _TOKENS_RE.exec(text);
  if (m) return { kind: 'fresh', label: `Fresh start · ${m[1]}k`, text };
  m = _SIZE_RE.exec(text);
  if (m) return { kind: 'fresh', label: `Fresh start · ${m[1]} MB`, text };
  if (_RESTART_RE.test(text)) return { kind: 'restart', label: 'Restarted fresh', text };
  if (_RECOVER_RE.test(text)) return { kind: 'recover', label: 'Recovering handoff', text };
  return null;
}

function isRolloverLine(line) {
  return rolloverInfo(line) !== null;
}

function _key(line) {
  let h = 5381;
  const s = String(line || '').trim();
  for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
  return `${s.length}:${h.toString(36)}`;
}

const _open = new Set();  // content keys the reader expanded — see header

// Inner HTML for the row `agentLineCls` classed as .agent-line-rollover.
function rolloverMarkerHTML(line) {
  const info = rolloverInfo(line);
  if (!info) return null;
  const key = _key(line);
  const open = _open.has(key);
  const tip = _e(info.text);
  return `<span class="ro-row" data-ro-kind="${info.kind}">`
    + `<button type="button" class="ro-pill" data-ro-key="${_e(key)}" aria-expanded="${open}" title="${tip}" aria-label="${tip}">`
    + `<span class="ro-ico" aria-hidden="true">↻</span><span class="ro-label">${_e(info.label)}</span></button></span>`
    + `<span class="ro-detail"${open ? '' : ' hidden'}>${tip}</span>`;
}

// One delegated listener: markers are rebuilt from HTML strings on every
// render, so per-node handlers would be lost with the DOM. A <button> already
// turns Enter / Space into click.
document.addEventListener('click', (ev) => {
  const pill = ev.target && ev.target.closest && ev.target.closest('.ro-pill');
  if (!pill) return;
  const row = pill.closest('.agent-line-rollover');
  const detail = row && row.querySelector(':scope > .ro-detail');
  if (!detail) return;
  const open = pill.getAttribute('aria-expanded') !== 'true';
  pill.setAttribute('aria-expanded', String(open));
  detail.hidden = !open;
  const key = pill.dataset.roKey;
  if (key) { if (open) _open.add(key); else _open.delete(key); }
});

window.rolloverInfo = rolloverInfo;
window.isRolloverLine = isRolloverLine;
window.rolloverMarkerHTML = rolloverMarkerHTML;

// Display-only prompt metadata. Missing line timestamps stay missing; only
// optimistic senders explicitly supply the client's current time.
function promptTimeTagHTML(ts) {
  if (!ts || !(typeof ts === 'string' || ts instanceof Date)) return '';
  const date = new Date(ts);
  if (!Number.isFinite(date.getTime())) return '';
  const time = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
  const title = date.toLocaleString().replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
  return `<time class="prompt-time-tag" dir="ltr" datetime="${date.toISOString()}" title="${title}">${time}</time>`;
}

// Mirrors mc/agent_runtime.py's clock preamble: the optional gap briefing
// belongs to the adjacent stamp, never to a standalone user-authored list.
const _TURN_CLOCK_RE = /\[Now \d{4}-\d{2}-\d{2} \d{2}:\d{2}[^\]\r\n]*\](?:\r?\nSince your last reply:(?:\r?\n- [^\r\n]*)*)?[ \t]*(?:\r?\n){0,2}/g;
function stripPromptClock(text) {
  return String(text || '').replace(_TURN_CLOCK_RE, '');
}

window.promptTimeTagHTML = promptTimeTagHTML;
window.stripPromptClock = stripPromptClock;

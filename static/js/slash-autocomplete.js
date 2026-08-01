// ── Slash-command autocomplete for the composers ────────────────────────────
//
// Typing `/` at the START of a composer opens a picker of the Claude CLI's
// built-in slash commands. Two things make the "at the start" part load-bearing
// rather than cosmetic:
//
//   * The CLI only parses a turn as a slash command when the command is the
//     FIRST bytes of that turn. MC already encodes this server-side as
//     `_SLASH_COMMAND_RE` (agent_routes.py) — the trigger here mirrors it, so
//     the picker never appears where the command wouldn't actually dispatch.
//   * A mistyped command doesn't error. It degrades to plain text that the
//     model just reads, which looks like it worked. Offering the real names is
//     the whole point.
//
// The list comes from /api/slash-commands, which reads the registry out of the
// user's OWN claude binary and returns only `supportsNonInteractive` entries
// (MC always spawns with --print). See mc/slash_commands.py.
//
// WIRING NOTE: the composers are re-rendered wholesale by refreshModal(), so
// per-element listeners would be lost on every rebuild. Everything here is
// delegated from `document` instead. keydown uses CAPTURE so it runs BEFORE the
// textarea's inline `onkeydown="handleInputEnter(...)"` — otherwise Enter would
// dispatch the message instead of accepting the highlighted command.
//
// NAMING: every module-scoped binding is `_sac`-prefixed. tools/smoke/
// inline-handler-scope-check.mjs builds one map of module-scoped names across
// all modules, so declaring a bare `esc` or `active` here made it report
// pre-existing inline handlers in OTHER files as unbridged. Don't un-prefix.

const _sacSEL = '.agent-task-input';
// Text from the start of the box to the caret is a lone leading slash-token.
// `/` alone opens the full list; `/comp` filters. A space ends it, and a
// second `/` (a unix path like /usr/bin) never matches.
const _sacTRIGGER = /^\/([A-Za-z][\w-]*)?$/;

let _sacCommands = null;    // null = not fetched yet
let _sacFetching = null;
let _sacBox = null;         // the popup element
let _sacTarget = null;      // textarea the popup belongs to
let _sacItems = [];         // currently displayed commands
let _sacActive = 0;

function _sacEsc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

async function _sacEnsure() {
  if (_sacCommands) return _sacCommands;
  if (!_sacFetching) {
    _sacFetching = fetch((window.API_BASE || '') + '/api/slash-commands')
      .then(r => (r.ok ? r.json() : { commands: [] }))
      .then(d => { _sacCommands = d.commands || []; return _sacCommands; })
      .catch(() => { _sacCommands = []; return _sacCommands; });
  }
  return _sacFetching;
}

// The token being typed, or null when the caret isn't in a leading /token.
function _sacToken(el) {
  if (!el || el.selectionStart !== el.selectionEnd) return null;
  const upToCaret = (el.value || '').slice(0, el.selectionStart);
  const m = _sacTRIGGER.exec(upToCaret);
  return m ? (m[1] || '') : null;
}

function _sacMatch(token) {
  const t = token.toLowerCase();
  if (!t) return _sacCommands.slice();
  const starts = [], contains = [];
  for (const c of _sacCommands) {
    const n = c.name.toLowerCase();
    if (n.startsWith(t)) starts.push(c);
    else if (n.includes(t)) contains.push(c);
  }
  return starts.concat(contains);
}

function _sacClose() {
  if (_sacBox) { _sacBox.remove(); _sacBox = null; }
  _sacTarget = null; _sacItems = []; _sacActive = 0;
}

function _sacRender() {
  if (!_sacBox) {
    _sacBox = document.createElement('div');
    _sacBox.className = 'slash-ac';
    _sacBox.setAttribute('role', 'listbox');
    // Commit on mousedown: a click would blur the textarea and lose the caret.
    _sacBox.addEventListener('mousedown', e => {
      const row = e.target.closest('.slash-ac-row');
      if (!row) return;
      e.preventDefault();
      _sacAccept(parseInt(row.dataset.i, 10));
    });
    document.body.appendChild(_sacBox);
  }
  _sacBox.innerHTML = _sacItems.map((c, i) => `
    <div class="slash-ac-row${i === _sacActive ? ' active' : ''}" data-i="${i}" role="option">
      <span class="slash-ac-name">/${_sacEsc(c.name)}</span>
      ${c.argumentHint ? `<span class="slash-ac-hint">${_sacEsc(c.argumentHint)}</span>` : ''}
      <span class="slash-ac-desc">${_sacEsc(c.description || '')}</span>
    </div>`).join('');
  _sacPosition();
}

// Fixed-position above the composer (below it if there's no room). The composer
// lives inside a modal that can be scrolled/dragged, so absolute-in-parent
// would drift — viewport coords recomputed on each render are stable.
function _sacPosition() {
  if (!_sacBox || !_sacTarget) return;
  const r = _sacTarget.getBoundingClientRect();
  const h = Math.min(_sacBox.scrollHeight, 260);
  const above = r.top > h + 12;
  _sacBox.style.left = Math.round(r.left) + 'px';
  _sacBox.style.width = Math.round(r.width) + 'px';
  _sacBox.style.top = above ? Math.round(r.top - h - 6) + 'px' : Math.round(r.bottom + 6) + 'px';
  _sacBox.style.maxHeight = h + 'px';
}

function _sacAccept(i) {
  const c = _sacItems[i];
  if (!c || !_sacTarget) return;
  const el = _sacTarget;
  const rest = (el.value || '').slice(el.selectionStart);
  // Trailing space either way: it separates the command from its arguments and
  // stops the next word being glued on for commands that take none.
  const insert = '/' + c.name + ' ';
  el.value = insert + rest;
  const pos = insert.length;
  el.setSelectionRange(pos, pos);
  _sacClose();
  el.focus();
  // Composers auto-size on input; tell them the value changed.
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

async function _sacOnInput(e) {
  const el = e.target;
  if (!el || !el.matches || !el.matches(_sacSEL)) return;
  if (_sacToken(el) === null) { _sacClose(); return; }
  await _sacEnsure();
  // The caret may have moved while the fetch was in flight.
  const token = _sacToken(el);
  if (token === null) { _sacClose(); return; }
  _sacItems = _sacMatch(token);
  if (!_sacItems.length) { _sacClose(); return; }
  _sacTarget = el;
  _sacActive = 0;
  _sacRender();
}

function _sacOnKeyDown(e) {
  if (!_sacBox || !_sacTarget || e.target !== _sacTarget) return;
  switch (e.key) {
    case 'ArrowDown':
    case 'ArrowUp':
      e.preventDefault(); e.stopPropagation();
      _sacActive = (_sacActive + (e.key === 'ArrowDown' ? 1 : _sacItems.length - 1)) % _sacItems.length;
      _sacRender();
      return;
    case 'Enter':
    case 'Tab':
      // Shift+Enter is "newline" in the composer — never hijack it.
      if (e.key === 'Enter' && e.shiftKey) { _sacClose(); return; }
      e.preventDefault(); e.stopPropagation();
      _sacAccept(_sacActive);
      return;
    case 'Escape':
      e.preventDefault(); e.stopPropagation();
      _sacClose();
      return;
    default:
      return;
  }
}

document.addEventListener('input', _sacOnInput);
// CAPTURE: must beat the textarea's own inline onkeydown (handleInputEnter),
// or Enter dispatches the half-typed command as a message.
document.addEventListener('keydown', _sacOnKeyDown, true);
document.addEventListener('click', e => {
  if (_sacBox && !_sacBox.contains(e.target) && e.target !== _sacTarget) _sacClose();
});
// A scrolled or resized modal must not leave the popup floating in place.
window.addEventListener('resize', () => (_sacBox ? _sacPosition() : null));
document.addEventListener('scroll', () => (_sacBox ? _sacPosition() : null), true);

// Exposed for tests / manual refresh after installing a new CLI.
window.slashAcReload = async () => {
  _sacCommands = null; _sacFetching = null;
  await fetch((window.API_BASE || '') + '/api/slash-commands?refresh=1').catch(() => {});
  return _sacEnsure();
};

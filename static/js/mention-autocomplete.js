// ── @mention autocomplete for the composers ─────────────────────────────────
//
// Typing `@` in a composer opens a picker of the hired agents, by the name they
// call themselves: `@Fe` → `@Fenn`. Accepting inserts plain text — the mention
// is a REFERENCE, not a directive.
//
// WHY IT IS ONLY TEXT. Every agent's system prompt already carries the roster
// (`_roster_block` in agent_routes.py): who each agent is, its type, and the
// two ways to call one. So "ask @Fenn to review this" already routes — the
// agent resolves the name and either runs it in-process or dispatches it as a
// real session. What was missing was never the routing; it was the affordance.
// Nothing told you the names existed, and a typo degraded silently into prose
// that looked like it had worked.
//
// It also matches how Ron works: he talks to his VPs, and they talk to
// everyone else. A mention that AUTO-DISPATCHED would turn every passing
// reference into a re-org. "@Fenn should look at this" said to Dave means
// "Dave, ask Fenn" — which is exactly what plain text means.
//
// This is a near-clone of slash-autocomplete.js and deliberately so: same
// delegated listeners, same capture-phase keydown, same fixed-position popup,
// and it borrows `.slash-ac` styling so the two pickers are one thing to learn.
// Read that file's header for why each of those is load-bearing.
//
// NAMING: every module-scoped binding is `_mac`-prefixed, for the reason
// slash-autocomplete gives — tools/smoke/inline-handler-scope-check.mjs builds
// ONE map of module-scoped names across all modules, so a bare `esc` here makes
// it misreport other files. Don't un-prefix.

const _macSEL = '.agent-task-input';
// `@` at the start of the box or after whitespace, then the name being typed.
// Anchored to the caret. Requiring the boundary keeps it out of e-mail
// addresses and any other mid-word `@`.
const _macTRIGGER = /(^|\s)@([\w-]*)$/;

const _macCache = new Map();   // projectId → agent list
let _macFetching = new Map();
let _macBox = null;
let _macTarget = null;
let _macItems = [];
let _macActive = 0;
let _macStart = 0;             // index of the `@` being replaced

function _macEsc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// The project a composer belongs to, stamped as data-project by the renderers.
// Without it we can still offer the GLOBAL pool, which is where the agents a
// mention is most likely to reach actually live.
function _macProjectOf(el) {
  return (el && el.dataset && el.dataset.project) || '';
}

async function _macEnsure(pid) {
  if (_macCache.has(pid)) return _macCache.get(pid);
  if (!_macFetching.has(pid)) {
    const url = (window.API_BASE || '') + '/api/characters'
      + (pid ? '?project_id=' + encodeURIComponent(pid) : '');
    _macFetching.set(pid, fetch(url)
      .then(r => (r.ok ? r.json() : []))
      // Only agents that have NAMED themselves are mentionable. A type still on
      // its file stem has nothing a human would type — offering `@code-reviewer`
      // teaches the wrong handle, since the roster the agent reads says "Fenn".
      .then(list => (Array.isArray(list) ? list : []).filter(c => c && c.agent_name))
      .then(list => { _macCache.set(pid, list); return list; })
      .catch(() => { _macCache.set(pid, []); return []; }));
  }
  return _macFetching.get(pid);
}

// {token, start} for the mention being typed, or null when the caret isn't in
// one. `start` is where the `@` sits, so accepting replaces only the mention
// and leaves everything typed before it alone.
function _macToken(el) {
  if (!el || el.selectionStart !== el.selectionEnd) return null;
  const upToCaret = (el.value || '').slice(0, el.selectionStart);
  const m = _macTRIGGER.exec(upToCaret);
  if (!m) return null;
  return { token: m[2] || '', start: m.index + m[1].length };
}

function _macMatch(list, token) {
  const t = token.toLowerCase();
  const key = c => String(c.agent_name || '').toLowerCase();
  if (!t) return list.slice();
  const starts = [], contains = [];
  for (const c of list) {
    const n = key(c);
    // The type name is searchable too — "reviewer" should find Fenn even
    // though nothing about "Fenn" says what he does.
    const role = String(c.display_name || c.name || '').toLowerCase();
    if (n.startsWith(t)) starts.push(c);
    else if (n.includes(t) || role.includes(t)) contains.push(c);
  }
  return starts.concat(contains);
}

function _macClose() {
  if (_macBox) { _macBox.remove(); _macBox = null; }
  _macTarget = null; _macItems = []; _macActive = 0;
}

function _macRender() {
  if (!_macBox) {
    _macBox = document.createElement('div');
    // Borrows the slash picker's shell so the two are one thing to learn.
    _macBox.className = 'slash-ac mention-ac';
    _macBox.setAttribute('role', 'listbox');
    // mousedown, not click: a click blurs the textarea and loses the caret.
    _macBox.addEventListener('mousedown', e => {
      const row = e.target.closest('.slash-ac-row');
      if (!row) return;
      e.preventDefault();
      _macAccept(parseInt(row.dataset.i, 10));
    });
    document.body.appendChild(_macBox);
  }
  _macBox.innerHTML = _macItems.map((c, i) => {
    const face = (typeof window.avatarHTML === 'function')
      ? window.avatarHTML(c.avatar, 20, 'mention-ac-face') : '';
    const role = c.display_name || c.name || '';
    return `
    <div class="slash-ac-row${i === _macActive ? ' active' : ''}" data-i="${i}" role="option">
      ${face}
      <span class="slash-ac-name">@${_macEsc(c.agent_name)}</span>
      <span class="slash-ac-hint">${_macEsc(role)}</span>
      <span class="slash-ac-desc">${_macEsc(c.description || '')}</span>
    </div>`;
  }).join('');
  _macPosition();
}

function _macPosition() {
  if (!_macBox || !_macTarget) return;
  const r = _macTarget.getBoundingClientRect();
  const h = Math.min(_macBox.scrollHeight, 260);
  const above = r.top > h + 12;
  _macBox.style.left = Math.round(r.left) + 'px';
  _macBox.style.width = Math.round(r.width) + 'px';
  _macBox.style.top = above ? Math.round(r.top - h - 6) + 'px' : Math.round(r.bottom + 6) + 'px';
  _macBox.style.maxHeight = h + 'px';
}

function _macAccept(i) {
  const c = _macItems[i];
  if (!c || !_macTarget) return;
  const el = _macTarget;
  const val = el.value || '';
  const insert = '@' + c.agent_name + ' ';
  el.value = val.slice(0, _macStart) + insert + val.slice(el.selectionStart);
  const pos = _macStart + insert.length;
  el.setSelectionRange(pos, pos);
  _macClose();
  el.focus();
  // Composers auto-size on input; tell them the value changed.
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

async function _macOnInput(e) {
  const el = e.target;
  if (!el || !el.matches || !el.matches(_macSEL)) return;
  if (_macToken(el) === null) { _macClose(); return; }
  const list = await _macEnsure(_macProjectOf(el));
  // The caret may have moved while the fetch was in flight.
  const at = _macToken(el);
  if (at === null) { _macClose(); return; }
  _macItems = _macMatch(list, at.token);
  if (!_macItems.length) { _macClose(); return; }
  _macTarget = el;
  _macStart = at.start;
  _macActive = 0;
  _macRender();
}

function _macOnKeyDown(e) {
  if (!_macBox || !_macTarget || e.target !== _macTarget) return;
  switch (e.key) {
    case 'ArrowDown':
    case 'ArrowUp':
      e.preventDefault(); e.stopPropagation();
      _macActive = (_macActive + (e.key === 'ArrowDown' ? 1 : _macItems.length - 1)) % _macItems.length;
      _macRender();
      return;
    case 'Enter':
    case 'Tab':
      // Shift+Enter is "newline" in the composer — never hijack it.
      if (e.key === 'Enter' && e.shiftKey) { _macClose(); return; }
      e.preventDefault(); e.stopPropagation();
      _macAccept(_macActive);
      return;
    case 'Escape':
      e.preventDefault(); e.stopPropagation();
      _macClose();
      return;
    default:
      return;
  }
}

document.addEventListener('input', _macOnInput);
// CAPTURE, for the same reason as the slash picker: it must beat the
// textarea's inline onkeydown or Enter sends the half-typed mention.
document.addEventListener('keydown', _macOnKeyDown, true);
document.addEventListener('click', e => {
  if (_macBox && !_macBox.contains(e.target) && e.target !== _macTarget) _macClose();
});
window.addEventListener('resize', () => (_macBox ? _macPosition() : null));
document.addEventListener('scroll', () => (_macBox ? _macPosition() : null), true);

// Hiring or renaming an agent must show up in the picker without a reload —
// the roster is edited on the Floor, several modals away from the composer.
window.mentionAcReload = (pid) => {
  if (pid === undefined) { _macCache.clear(); _macFetching.clear(); return; }
  _macCache.delete(pid || ''); _macFetching.delete(pid || '');
};

// ── Documents tab: search INSIDE project docs (MC-950 follow-up) ─────────────
// Front end for GET /api/project/<id>/docs/search (mc/doc_search.py: FTS5/BM25
// over the project's docs/ tree incl. gitignored docs and _journal/). The
// Documents tab used to list plans + agent-written docs with only a title
// filter; this adds a content search box above the list.
//
// Wiring is three one-line hooks, so this file owns everything else:
//   render-core.js  modalContentHTML      → window.docSearchBoxHTML(pid)
//   agent-log.js    renderDocumentsTab    → window.docSearchRender(pid, list, bar)
//   agent-log.js    openDocFromHistory    → returns the viewer element, and tags
//                                           each rendered line with data-line
//
// While a query (>= 2 chars) is active, results REPLACE the document list
// (renderDocumentsTab hands over the container); clearing the box hands it back.
// refreshModal() rebuilds the modal every few seconds, so the query + hits live
// in module state, and the <input type=text id=...> is restored (value + focus)
// by refreshModal's own text-input preservation.
//
// The route returns scored hits even for a nonsense query (no relevance
// threshold exists), so hits render in the order returned. Only a genuinely
// empty list says "No matches"; a failed request says so instead — it must not
// read as "nothing found".

const DEBOUNCE_MS = 250;
const MIN_CHARS = 2;
const LIMIT = 20;

const _state = {};   // projectId → { q, status: 'loading'|'ok'|'error', hits, seq, timer }

function _esc(s) {
  if (window.esc) return window.esc(s);
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// Same escaping contract as renderDocumentsTab's jsAttr: JS-escape first, then
// HTML-escape, so a path with an apostrophe can't end the inline string early.
function _jsAttr(s) {
  return _esc(String(s == null ? '' : s).replace(/\\/g, '\\\\').replace(/'/g, "\\'"));
}

function _activeQuery(projectId) {
  const st = _state[projectId];
  return st && st.q && st.q.length >= MIN_CHARS ? st : null;
}

function docSearchBoxHTML(projectId) {
  const st = _state[projectId];
  return `<div class="doc-search" id="doc-search-${_esc(projectId)}">
    <input type="text" id="doc-search-input-${_esc(projectId)}" class="doc-search-input"
      placeholder="Search inside documents..." autocomplete="off" spellcheck="false"
      value="${_esc((st && st.raw) || '')}"
      oninput="docSearchInput('${_esc(projectId)}', this.value)">
  </div>`;
}

// The server marks matches with » … « (SQLite snippet()). Escape FIRST, then
// build <mark> from the markers: the snippet is document text and is never
// injected raw. Stray markers that came from the document itself (a literal »)
// stay as text because only a balanced »…« pair becomes a mark. A snippet with
// no markers at all (the match was in the heading) falls back to marking the
// query's own terms.
function _highlight(snippet, query) {
  const text = String(snippet == null ? '' : snippet);
  if (/»[^»«]*«/.test(text)) {
    return _esc(text).replace(/»([^»«]*)«/g, '<mark>$1</mark>');
  }
  const terms = (String(query || '').toLowerCase().match(/[a-z0-9]{3,}/g) || [])
    .filter((t, i, a) => a.indexOf(t) === i)
    .map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const safe = _esc(text);
  if (!terms.length) return safe;
  return safe.replace(new RegExp('(' + terms.join('|') + ')', 'gi'), '<mark>$1</mark>');
}

function _hitHTML(projectId, h, query) {
  const file = String(h.file || '');
  const title = file.split(/[/\\]/).pop() || file;
  const head = h.heading ? `<div class="doc-search-heading">${_esc(h.heading)}</div>` : '';
  const tag = h.tier === 'journal'
    ? '<span class="doc-kind-badge doc-search-tier" title="Agent journal entry (ranked lower)">journal</span>' : '';
  const line = Number(h.line_start) || 0;
  return `
    <div class="doc-search-hit" role="button" tabindex="0"
      onclick="docSearchOpen('${_esc(projectId)}','${_jsAttr(h.path)}','${_jsAttr(file)}',${line})"
      onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();this.click();}">
      <div class="doc-search-file">${tag}<span class="doc-search-path">${_esc(file)}</span>${line ? `<span class="doc-search-line">:${line}</span>` : ''}</div>
      ${head}
      <div class="doc-search-snippet">${_highlight(h.snippet, query)}</div>
    </div>`;
}

function docSearchRender(projectId, container, toolbar) {
  const st = _activeQuery(projectId);
  if (!st || !container) return false;
  if (toolbar) toolbar.style.display = 'none';
  if (st.status === 'loading') {
    container.innerHTML = '<div class="doc-search-note">Searching...</div>';
  } else if (st.status === 'error') {
    container.innerHTML = '<div class="doc-search-note doc-search-error">Search is unavailable right now.</div>';
  } else if (!st.hits.length) {
    container.innerHTML = '<div class="doc-search-note">No matches</div>';
  } else {
    container.innerHTML = `<div class="doc-search-count">${st.hits.length} result${st.hits.length === 1 ? '' : 's'}</div>`
      + st.hits.map(h => _hitHTML(projectId, h, st.q)).join('');
  }
  return true;
}

function _repaint(projectId) {
  if (typeof window.renderDocumentsTab === 'function') window.renderDocumentsTab(projectId);
}

async function _run(projectId, seq) {
  const st = _state[projectId];
  const q = st.q;
  let hits = null;
  try {
    const res = await fetch(API_BASE + `/api/project/${encodeURIComponent(projectId)}/docs/search`
      + `?q=${encodeURIComponent(q)}&limit=${LIMIT}`);
    if (res.ok) {
      const data = await res.json();
      hits = Array.isArray(data) ? data : (data && Array.isArray(data.hits) ? data.hits : null);
    }
  } catch (e) { hits = null; }
  // A newer keystroke (or a clear) superseded this request: drop it.
  if (_state[projectId] !== st || st.seq !== seq) return;
  st.status = hits ? 'ok' : 'error';
  st.hits = hits || [];
  _repaint(projectId);
}

function docSearchInput(projectId, value) {
  const prev = _state[projectId];
  if (prev && prev.timer) clearTimeout(prev.timer);
  const raw = String(value || '');
  const q = raw.trim();
  const st = { raw, q, status: 'loading', hits: [], seq: (prev ? prev.seq : 0) + 1, timer: null };
  _state[projectId] = st;
  if (q.length < MIN_CHARS) {
    _repaint(projectId);               // back to the normal list
    return;
  }
  st.timer = setTimeout(() => {
    st.timer = null;
    _repaint(projectId);               // "Searching..." only once the debounce fires
    _run(projectId, st.seq);
  }, DEBOUNCE_MS);
}

function _scrollToLine(viewer, line) {
  if (!viewer || !line) return;
  const body = viewer.querySelector('.plan-viewer-body');
  if (!body) return;
  let target = null;
  for (const el of body.querySelectorAll('[data-line]')) {
    if (Number(el.dataset.line) > line) break;
    target = el;
  }
  if (!target) return;
  target.scrollIntoView({ block: 'start' });
  target.classList.add('doc-search-target');
  setTimeout(() => target.classList.remove('doc-search-target'), 2500);
}

async function docSearchOpen(projectId, docPath, title, line) {
  const viewer = await window.openDocFromHistory(docPath, title, projectId);
  _scrollToLine(viewer, line);
}

window.docSearchBoxHTML = docSearchBoxHTML;
window.docSearchRender = docSearchRender;
window.docSearchInput = docSearchInput;
window.docSearchOpen = docSearchOpen;

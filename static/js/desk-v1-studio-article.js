// Desk v1 — Studio's standalone article (Ron 2026-10-06: an article is possible
// for ANY project and ANY topic, even one that is not a campaign yet).
// Window-bridged module, no `import` (ground rule 1). desk-v1-studio.js owns the
// Studio home and the `studio-create` route and hands this module the article
// half of it; the article writer itself (frame 12) stays in desk-v1-studio.js and
// is called here with no campaign.
//
//   page      route `studio-create` with {kind: 'article'} (the setup form: Topic,
//             Project, Campaign) or {itemId: 'artd-…'} (the writer)
//   Recent    `rows()` are the article rows Studio's Recent list shows
//   server    mc/desk_studio_articles.py (live): saved on every change, debounced;
//             demo keeps the draft in the session only and never calls the server
//
// An article lives in Studio like a video draft does: it is listed in Recent, can
// be deleted there (desk-v1-studio-delete.js), and is added to a campaign
// afterwards with "Use in a campaign", which makes an ordinary article piece.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const ID_PREFIX = 'artd-';
  const SAVE_DELAY_MS = 800;
  const DRAFT_TAB = () => ({ id: 'tab-draft', label: 'Draft', body: '' });

  function _fx() { return window.DeskV1Store.state(); }
  function _isLive() { return window.DeskV1Store.live(); }
  function _studio() { return window.DeskV1Studio; }
  function _projects() { return _fx().projects || []; }
  function _projectName(id) { const p = _projects().find((x) => x.id === id); return p ? p.name : ''; }
  let _seq = 0;
  function _uid() { return Date.now().toString(36) + (++_seq).toString(36); }

  // The articles Studio holds, newest first. Live: what the server holds (read when
  // Studio home opens); demo: what was made this session.
  const _list = [];
  let _cur = null; // the mounted page

  function list() { return _list; }
  function find(id) { return _list.find((a) => a.id === id) || null; }

  // The page's own record for a stored article. `draft` is the shape the writer reads.
  function _fromServer(o) {
    return {
      id: o.id, kind: 'article', title: o.topic || '', projectId: o.project_id || '', campaignId: o.campaign_id || '',
      pieceId: o.piece_id || null, attached: !!o.attached, campaignTitle: o.campaign_title || '', rev: o.rev || 0,
      status: 'draft', draft: { status: 'drafting', tabs: (o.tabs && o.tabs.length ? o.tabs : [DRAFT_TAB()]).map((t) => ({ id: t.id, label: t.label, body: t.body || '' })) },
    };
  }
  function _merge(a, o) {
    a.rev = o.rev;
    a.pieceId = o.piece_id || null;
    a.attached = !!o.attached;
    a.campaignTitle = o.campaign_title || '';
  }

  function _campaignTitle(a) {
    if (a.attached && a.campaignTitle) return a.campaignTitle;
    const c = a.campaignId && _studio().campaign(a.campaignId);
    return c ? _studio().campaignTitle(c) : '';
  }

  function _meta(a) {
    const parts = ['Draft'];
    const proj = a.projectId && _projectName(a.projectId);
    if (proj) parts.push(proj);
    const camp = _campaignTitle(a);
    if (a.attached) parts.push(`in ${camp || 'a campaign'}`);
    else parts.push(a.campaignId ? `for ${camp || 'a campaign'}` : 'not attached');
    return parts.join(' · ');
  }

  // Recent rows. A row carries `campaignId` only once the article is IN a campaign
  // (then a click opens that campaign and the bin says it is attached); a draft that
  // is merely meant for one opens here.
  function rows() {
    return _list.map((a) => ({
      src: 'article', id: a.id, icon: '📄', title: a.title, kind: 'article',
      campaignId: a.attached ? a.campaignId : null, meta: _meta(a), state: 'draft',
    }));
  }

  // Live: read what the server holds. Mutates in place (the delete's Undo restores
  // into this array) and keeps the page's own object for the article it has open.
  function load() {
    if (!_isLive()) return Promise.resolve();
    return window.DeskV1Store.api('GET', '/api/desk/studio/articles').then((r) => {
      const open = _cur && _cur.a;
      const fresh = ((r && r.articles) || []).map((o) => (open && open.id === o.id ? open : _fromServer(o)));
      _list.length = 0;
      fresh.forEach((a) => _list.push(a));
    }).catch((e) => { console.warn('[desk] could not read the Studio articles:', e && e.message ? e.message : e); });
  }

  function handles(params) {
    return !!params && (params.kind === 'article' || (typeof params.itemId === 'string' && params.itemId.startsWith(ID_PREFIX)));
  }
  function label(params) {
    const a = params && params.itemId ? find(params.itemId) : null;
    return a ? a.title : 'New article';
  }

  // ── saving (live) ────────────────────────────────────────────────────────
  // One PUT at a time per article, each reading the article as it is when it runs,
  // so a fast typist never races their own rev.
  function _status(text) {
    if (_cur && _cur.el.isConnected) { const s = _cur.el.querySelector('[data-sa-status]'); if (s) s.textContent = text; }
  }
  function _put(a) {
    return window.DeskV1Store.api('PUT', `/api/desk/studio/articles/${encodeURIComponent(a.id)}`, {
      rev: a.rev, topic: a.title, project_id: a.projectId || '', campaign_id: a.campaignId || '', tabs: a.draft.tabs,
    }).then((out) => { _merge(a, out); _status('Saved'); return true; })
      .catch((e) => {
        const msg = e && e.message ? e.message : String(e);
        _status('Not saved');
        window.DeskV1Kit.toast(`The article was not saved: ${msg}`);
        return false;
      });
  }
  function _save(a) {
    if (!_isLive()) return Promise.resolve(true);
    _status('Saving…');
    a._chain = (a._chain || Promise.resolve()).then(() => _put(a));
    return a._chain;
  }
  function _flush() {
    if (!_cur || !_cur.timer) return Promise.resolve(true);
    clearTimeout(_cur.timer);
    _cur.timer = null;
    return _save(_cur.a);
  }
  function _later() {
    if (!_cur || !_isLive()) return;
    _status('Saving…');
    clearTimeout(_cur.timer);
    const mine = _cur;
    mine.timer = setTimeout(() => { mine.timer = null; _save(mine.a); }, SAVE_DELAY_MS);
  }

  // ── setup: topic, project, campaign ─────────────────────────────────────
  function _campaignChoices(projectId) {
    return _studio().campaignsToUse().filter((c) => !projectId || c.projectId === projectId);
  }

  function _setupHTML(st) {
    const camps = _campaignChoices(st.projectId);
    return `<div class="desk-v1-studio-create desk-v1-sa" data-studio-create data-kind="article" data-view="setup">
      <h2 class="desk-v1-studio-title">New article</h2>
      <div class="desk-v1-sa-form">
        <div class="desk-v1-sa-field">
          <label class="desk-v1-sc-label" for="sa-topic">Topic</label>
          <input type="text" class="desk-v1-sb-edit-input" id="sa-topic" data-sa-topic maxlength="200" value="${esc(st.topic)}" placeholder="What is the article about?" autocomplete="off">
          <span class="desk-v1-sc-hint">Required. Any topic: it does not have to be a campaign yet.</span>
        </div>
        <div class="desk-v1-sa-field">
          <label class="desk-v1-sc-label" for="sa-project">Project</label>
          <select class="desk-v1-cap-select" id="sa-project" data-sa-project>
            <option value=""${st.projectId ? '' : ' selected'}>None</option>${_projects().map((p) =>
              `<option value="${esc(p.id)}"${p.id === st.projectId ? ' selected' : ''}>${esc(p.name)}</option>`).join('')}</select>
          <span class="desk-v1-sc-hint">Optional. Any project.</span>
        </div>
        <div class="desk-v1-sa-field">
          <label class="desk-v1-sc-label" for="sa-campaign">Campaign</label>
          <select class="desk-v1-cap-select" id="sa-campaign" data-sa-campaign>
            <option value=""${st.campaignId ? '' : ' selected'}>None</option>${camps.map((c) =>
              `<option value="${esc(c.id)}"${c.id === st.campaignId ? ' selected' : ''}>${esc(_studio().campaignTitle(c))}</option>`).join('')}</select>
          <span class="desk-v1-sc-hint">Optional. An existing campaign this is meant for. You can add it to one later.</span>
        </div>
        <div class="desk-v1-sc-actions">
          <button type="button" class="btn-add" data-sa-start${st.topic.trim() ? '' : ' disabled'}>Start writing</button>
        </div>
      </div>
    </div>`;
  }

  function _paintSetup(el) {
    const st = _cur.setup;
    el.innerHTML = _setupHTML(st);
    const topic = el.querySelector('[data-sa-topic]');
    const start = el.querySelector('[data-sa-start]');
    topic.oninput = () => { st.topic = topic.value; start.disabled = !topic.value.trim(); };
    topic.onkeydown = (e) => { if (e.key === 'Enter' && !start.disabled) { e.preventDefault(); start.click(); } };
    el.querySelector('[data-sa-project]').onchange = (e) => {
      st.projectId = e.target.value;
      const c = st.campaignId && _studio().campaign(st.campaignId);
      if (c && st.projectId && c.projectId !== st.projectId) st.campaignId = '';
      _paintSetup(el);
      el.querySelector('[data-sa-project]').focus({ preventScroll: true });
    };
    el.querySelector('[data-sa-campaign]').onchange = (e) => {
      st.campaignId = e.target.value;
      // A campaign belongs to one project: choosing it fills the project in.
      const c = st.campaignId && _studio().campaign(st.campaignId);
      if (c && !st.projectId && c.projectId) st.projectId = c.projectId;
      _paintSetup(el);
      el.querySelector('[data-sa-campaign]').focus({ preventScroll: true });
    };
    start.onclick = () => _start(el);
  }

  async function _start(el) {
    const mine = _cur;
    const st = mine.setup;
    const topic = st.topic.trim();
    if (!topic) return;
    const a = {
      id: ID_PREFIX + _uid(), kind: 'article', title: topic, projectId: st.projectId || '', campaignId: st.campaignId || '',
      pieceId: null, attached: false, campaignTitle: '', rev: 0, status: 'draft', draft: { status: 'drafting', tabs: [DRAFT_TAB()] },
    };
    const start = el.querySelector('[data-sa-start]');
    if (start) start.disabled = true;
    // Live, the draft exists once the server has it: a refusal leaves the form as it was.
    if (_isLive() && !(await _put(a))) { if (start && start.isConnected) start.disabled = false; return; }
    _list.unshift(a);
    if (_cur !== mine || !el.isConnected) return; // the page moved on: the draft is saved and listed in Recent
    mine.a = a;
    mine.tab = 0;
    if (typeof window.deskV1PatchParams === 'function') window.deskV1PatchParams({ itemId: a.id, kind: null });
    _paintWriter(el);
    const body = el.querySelector('[data-writer-body]');
    if (body) body.focus({ preventScroll: true });
  }

  // ── the writer, with no campaign behind it ───────────────────────────────
  function _provenance(a) {
    const proj = a.projectId && _projectName(a.projectId);
    if (a.attached) return `A Studio draft${proj ? ' for ' + proj : ''}. A copy is in ${_campaignTitle(a) || 'a campaign'}; this one stays in Studio.`;
    return `A Studio draft${proj ? ' for ' + proj : ''}, not attached to a campaign. Write it here, then add it to a campaign when it is ready.`;
  }

  function _useLabel(a) {
    if (a.attached) return `Open in ${_campaignTitle(a) || 'the campaign'} ›`;
    return a.campaignId && _campaignTitle(a) ? `Add to ${_campaignTitle(a)} ›` : 'Use in a campaign ›';
  }

  function _paintWriter(el) {
    const a = _cur.a;
    const tab = Math.min(Math.max(_cur.tab || 0, 0), a.draft.tabs.length - 1);
    const chips = [
      a.projectId ? `Project: ${_projectName(a.projectId) || a.projectId}` : 'No project',
      a.attached ? `In ${_campaignTitle(a) || 'a campaign'}` : (a.campaignId && _campaignTitle(a) ? `For ${_campaignTitle(a)}` : 'No campaign'),
    ];
    el.innerHTML = `<div class="desk-v1-studio-create desk-v1-sa" data-studio-create data-kind="article" data-view="writer" data-item-id="${esc(a.id)}">
      <h2 class="desk-v1-studio-title">Article</h2>
      <div class="desk-v1-sa-chips" data-sa-meta>${chips.map((c) => `<span class="desk-v1-what-chip">${esc(c)}</span>`).join('')}</div>
      ${_studio().writerHTML(a, null, tab, {
        provenance: _provenance(a), saveLabel: 'Save draft', backLabel: 'Back to Studio',
        extraHTML: `<button type="button" class="btn-secondary" data-sa-use>${esc(_useLabel(a))}</button><span class="desk-v1-sa-status" role="status" aria-live="polite" data-sa-status></span>`,
      })}
    </div>`;
    const root = el.querySelector('[data-writer]');
    _studio().wireWriter(root, a, null, tab, {
      onTab: (i) => { _flush().then(() => { _cur.tab = i; _paintWriter(el); }); },
      onSave: () => { _flush().then(() => _save(a)).then((ok) => { if (ok) window.DeskV1Kit.toast(_isLive() ? 'Draft saved.' : 'Draft kept in Studio for this session.'); }); },
      onBack: () => { _flush().then(() => { if (typeof window.deskV1Back === 'function') window.deskV1Back(); else window.deskV1Nav('studio', {}); }); },
    });
    root.querySelector('[data-writer-body]').addEventListener('input', _later);
    el.querySelector('[data-sa-use]').onclick = (ev) => _use(a, ev.currentTarget);
  }

  // ── add to a campaign ────────────────────────────────────────────────────
  function _use(a, btn) {
    if (a.attached) { window.deskV1GotoCampaignPanel('what', { campaignId: a.campaignId }); return; }
    if (a.campaignId && _campaignTitle(a)) { _attach(a, a.campaignId); return; }
    const camps = _studio().campaignsToUse();
    if (!camps.length) { window.DeskV1Kit.toast('Start a campaign first, then add this article to it.'); return; }
    window.DeskV1Kit.addToMenu(btn, camps.map((c) => ({ id: c.id, label: _studio().campaignTitle(c) })), (campId) => _attach(a, campId), { noAppendNew: true });
  }

  // Makes an article piece in the campaign from the draft's text. The draft stays in
  // Studio; Undo takes the piece out again (the server's attached flag follows the
  // piece, so deleting it there is all Undo has to do).
  async function _attach(a, campId) {
    const camp = _studio().campaign(campId);
    if (!camp) return;
    if (!(await _flush())) return;
    const text = (a.draft.tabs.find((t) => t.body.trim()) || { body: '' }).body;
    const famId = 'fam-new-' + _uid();
    const fam = { id: famId, campaignId: campId, kind: 'article', title: a.title, body: text, wordCount: text.split(/\s+/).filter(Boolean).length || null, assets: [], versions: [] };
    const prev = { campaignId: a.campaignId, attached: a.attached, pieceId: a.pieceId, campaignTitle: a.campaignTitle };
    const queue = window.deskV1QueuePiece || ((id, fn) => fn());
    const title = _studio().campaignTitle(camp);
    const r = await window.DeskV1Store.write({
      label: `Added “${a.title}” to “${title}”`,
      apply: () => { _fx().families.push(fam); Object.assign(a, { campaignId: campId, attached: true, pieceId: famId, campaignTitle: title }); },
      unapply: () => {
        const i = _fx().families.indexOf(fam); if (i >= 0) _fx().families.splice(i, 1);
        Object.assign(a, prev);
      },
      repaint: () => { if (_cur && _cur.a === a && _cur.el.isConnected) _paintWriter(_cur.el); },
      request: () => queue(famId, () => window.DeskV1Store.api('POST', `/api/desk/studio/articles/${encodeURIComponent(a.id)}/attach`, { campaign_id: campId, piece_id: famId }))
        .then((out) => { if (out && out.article) _merge(a, out.article); return out; }),
      undoRequest: () => queue(famId, () => window.DeskV1Store.api('DELETE', `/api/desk/pieces/${encodeURIComponent(famId)}`)),
    });
    if (r && r.ok) window.deskV1GotoCampaignPanel('what', { campaignId: campId });
  }

  // ── entry ────────────────────────────────────────────────────────────────
  function _unavailable(el, message) {
    el.innerHTML = `<div class="desk-v1-studio-create desk-v1-sa" data-studio-create data-kind="article" data-view="missing">
      <h2 class="desk-v1-studio-title">Article</h2>
      <div class="desk-v1-camp-empty" data-sa-missing>${esc(message)}</div>
      <div class="desk-v1-sc-actions"><button type="button" class="btn-secondary" data-sa-back>Back to Studio</button></div></div>`;
    el.querySelector('[data-sa-back]').onclick = () => { if (typeof window.deskV1Back === 'function') window.deskV1Back(); else window.deskV1Nav('studio', {}); };
  }

  function render(el, params) {
    params = params || {};
    const prior = _cur;
    if (prior && prior.timer) { clearTimeout(prior.timer); prior.timer = null; _save(prior.a); }
    _cur = { el, a: null, tab: 0, timer: null, setup: { topic: '', projectId: '', campaignId: '' } };
    if (!params.itemId) { _paintSetup(el); const t = el.querySelector('[data-sa-topic]'); if (t) t.focus({ preventScroll: true }); return; }
    const mine = _cur;
    const known = find(params.itemId);
    if (known) { mine.a = known; _paintWriter(el); return; }
    if (!_isLive()) { _unavailable(el, 'That article is not in Studio any more.'); return; }
    el.innerHTML = '<div class="desk-v1-camp-empty" data-sa-loading>Loading the article…</div>';
    window.DeskV1Store.api('GET', `/api/desk/studio/articles/${encodeURIComponent(params.itemId)}`).then((o) => {
      if (_cur !== mine || !el.isConnected) return;
      mine.a = find(o.id) || _fromServer(o);
      if (!find(o.id)) _list.unshift(mine.a);
      _paintWriter(el);
    }).catch((e) => {
      if (_cur !== mine || !el.isConnected) return;
      _unavailable(el, e && e.status === 404 ? 'That article is not saved in Studio any more.' : `Could not open the article: ${e && e.message ? e.message : e}`);
    });
  }

  window.DeskV1StudioArticle = { handles, label, render, rows, list, find, load };
})();

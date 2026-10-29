// Studio writer conversation. The model only proposes; accepting and Undo use
// the server's revision/text guards. State is keyed by draft, never its title.
(function () {
  const states = new Map();
  const INPUT = 'desk-v1-article-chat-input';
  const esc = (s) => window.esc(String(s == null ? '' : s));
  const api = (...args) => window.DeskV1Store.api(...args);
  const base = (a) => `/api/desk/studio/articles/${encodeURIComponent(a.id)}/chat`;
  const state = (a) => {
    if (!states.has(a.id)) states.set(a.id, { turns: [], draft: '', pending: null, busy: false, loaded: false, error: '', view: null });
    return states.get(a.id);
  };

  function paint(st) {
    const v = st.view;
    if (!v || !v.box.isConnected) return;
    const { box, opts } = v;
    const rows = st.turns.map((t) => {
      const p = t.proposal;
      const proposal = p ? `<div class="desk-v1-article-proposal" data-article-proposal="${esc(t.id)}">
        <strong>${p.status === 'applied' ? 'Accepted' : p.status === 'undone' ? 'Undone' : 'Proposed draft'} · ${esc(p.label)}</strong>
        <details${p.status === 'proposed' ? ' open' : ''}><summary>Read proposed text</summary><div class="desk-v1-chat-text" data-proposal-text>${esc(p.body)}</div></details>
        ${p.status === 'proposed' ? `<button type="button" class="btn-add" data-article-accept="${esc(t.id)}">Accept proposal</button>` : ''}
      </div>` : '';
      return `<div class="desk-v1-chat-turn desk-v1-chat-${t.role === 'user' ? 'user' : 'agent'}" data-chat-turn="${esc(t.role)}">
        <div class="desk-v1-chat-meta">${esc(t.role === 'user' ? 'You' : t.agent || 'Studio agent')}</div>
        <div class="desk-v1-chat-text">${esc(t.text)}</div>${proposal}
        ${t.proposal_error ? `<div class="desk-v1-chat-chip" data-kind="error">No proposal: ${esc(t.proposal_error)}</div>` : ''}</div>`;
    });
    if (st.pending) rows.push(`<div class="desk-v1-chat-turn desk-v1-chat-user" data-chat-pending><div class="desk-v1-chat-meta">You</div><div class="desk-v1-chat-text">${esc(st.pending)}</div></div><div class="desk-v1-chat-turn desk-v1-chat-agent" data-chat-thinking>Thinking…</div>`);
    const output = box.querySelector('[data-chat-thread]');
    output.innerHTML = rows.join('') || '<p class="desk-v1-chat-empty">Discuss this draft, ask for a rewrite, or start with an outline. Proposed text waits for you to accept it.</p>';
    output.scrollTop = output.scrollHeight;
    box.querySelector('[data-article-chat-status]').textContent = st.error || (st.loading ? 'Reading the conversation…' : 'Rewrites are proposals. Accept one to change the draft; Undo restores it.');
    const disabled = !window.DeskV1Store.live() || !!st.pending || st.busy || st.loading;
    const input = box.querySelector('textarea');
    input.disabled = disabled;
    box.querySelector('[data-posy-send]').disabled = disabled;
    if (!st.pending && input.value !== st.draft) { input.value = st.draft; input.dispatchEvent(new Event('input')); }
    opts.lock(st.busy);
    box.querySelectorAll('[data-article-accept]').forEach((b) => {
      b.disabled = disabled;
      b.onclick = () => change(st, opts, b.dataset.articleAccept, 'accept');
    });
  }

  async function change(st, opts, id, action) {
    if (st.busy || st.pending) return;
    // Header Undo may outlive a route leave/reopen. Use the current writer's
    // save/lock/repaint bridge when this draft is mounted again.
    if (st.view && st.view.box.isConnected) opts = st.view.opts;
    const turn = st.turns.find((t) => t.id === id);
    const p = turn && turn.proposal;
    const tab = p && opts.article.draft.tabs.find((t) => t.id === p.tab_id);
    if (!p || !tab || tab.body !== (action === 'undo' ? p.body : p.before)) {
      st.error = 'The draft changed. Nothing was replaced; ask for a new proposal.';
      paint(st); window.DeskV1Kit.toast(st.error); return;
    }
    st.busy = true; st.error = ''; paint(st);
    try {
      // Flush typing first, through the writer's own serialized save chain.
      if (!(await opts.save())) throw new Error('The current draft could not be saved.');
      const res = await api('POST', `${base(opts.article)}/${encodeURIComponent(id)}`, { action, rev: opts.article.rev });
      const index = st.turns.findIndex((t) => t.id === id);
      st.turns[index] = res.turn;
      const target = st.view && st.view.box.isConnected ? st.view.opts : opts;
      target.adopt(res.article);
      if (action === 'accept') window.DeskV1Kit.commandBus.run({
        label: 'Accepted proposed article draft', do: () => {},
        undo: () => change(st, opts, id, 'undo'),
      });
      else window.DeskV1Kit.toast('Restored the previous draft.');
    } catch (e) {
      st.error = `Nothing was replaced: ${e && e.message ? e.message : e}`;
      window.DeskV1Kit.toast(st.error);
    } finally { st.busy = false; paint(st); }
  }

  async function send(st, opts, text) {
    if (st.pending || st.busy || st.loading || !window.DeskV1Store.live()) return;
    st.pending = text; st.draft = ''; st.error = ''; paint(st);
    const tab = opts.tab();
    try {
      const res = await api('POST', base(opts.article), { message: text, tab_id: tab.id, body: tab.body });
      st.turns.push(...res.turns);
      st.loaded = true;
    } catch (e) {
      st.draft = text;
      st.error = `Message not sent: ${e && e.message ? e.message : e}. Your message is back in the box.`;
    } finally { st.pending = null; paint(st); }
  }

  function mount(host, opts) {
    if (!host) return;
    const kit = window.DeskV1Kit;
    const a = opts.article;
    const st = state(a);
    const project = (window.DeskV1Store.state().projects || []).find((p) => p.id === a.projectId);
    const ref = kit.deskAgentRef({ project });
    host.innerHTML = kit.posyBoxHTML({ inputId: INPUT, scopeLabel: a.title, agentRef: ref });
    const box = host.querySelector('.desk-v1-posy-box');
    const scope = box.querySelector('[data-scope-trigger]');
    scope.textContent = 'About: ' + a.title;
    scope.disabled = true; // this conversation is always about this draft
    const name = box.querySelector('.desk-thread-name');
    // The default engine is Studio's established no-project fallback. There is
    // no character ref to resolve, so keep it outside the roster repaint path.
    if (!a.projectId) {
      name.remove();
      const label = document.createElement('span'); label.className = 'desk-v1-article-default-name'; label.textContent = 'Studio agent';
      box.querySelector('.desk-thread-head').prepend(label);
    }
    box.querySelector('.desk-v1-posy-output').outerHTML = `<div class="desk-v1-chat" data-article-chat>
      ${window.DeskV1ChatChrome ? window.DeskV1ChatChrome.barHTML() : ''}
      <div class="desk-v1-chat-thread" data-chat-thread role="log" aria-live="polite" aria-label="Draft conversation" tabindex="0"></div>
      <div class="desk-v1-article-chat-status" data-article-chat-status role="status"></div></div>`;
    box.querySelector('[data-article-chat]').append(box.querySelector('.agent-input-row'));
    if (window.DeskV1ChatChrome) window.DeskV1ChatChrome.attach(box.querySelector('[data-article-chat]'));
    const input = box.querySelector('textarea');
    input.setAttribute('aria-label', 'Message the article agent');
    input.setAttribute('data-sb-ask', ''); // shared large-view focus target
    input.placeholder = 'Discuss this draft or ask for a rewrite…';
    input.value = st.draft;
    kit.bindPosyBox(box, INPUT, (text) => send(st, opts, text));
    input.addEventListener('input', () => { st.draft = input.value; });
    st.view = { box, opts };
    if (!window.DeskV1Store.live()) st.error = 'Sample draft: conversation needs the live Desk.';
    if (window.DeskV1Store.live() && !st.loaded && !st.loading) {
      st.loading = true;
      api('GET', base(a)).then((r) => { st.turns = r.thread || []; st.loaded = true; })
        .catch((e) => { st.error = `Could not read the conversation: ${e && e.message ? e.message : e}`; })
        .finally(() => { st.loading = false; paint(st); });
    }
    paint(st);
  }

  window.DeskV1ArticleChat = { mount };
})();

// Desk v1 campaign page: the conversation with the campaign's agent
// (Ron 2026-10-06: the agent box said "Not connected to the agent yet"; he wants to
// brainstorm and sort out content problems with the agent right there, as in Studio).
// Window-bridged module, no `import` (ground rule 1). One unit, one file
// (AGENT_RULES.md): desk-v1-campaign.js paints the box head (avatar, name, scope
// picker) and calls `mount`; the thread, the turn and the suggestions handoff live
// here. Server side: mc/desk_campaign_chat.py. Same look and behaviour as the Studio
// thread (desk-v1-story-chat.js), and the same classes (desk-v1-story-chat.css).
//
//   window.DeskV1CampaignChat.mount(boxEl, opts)  -> true when it took the box over
//
// opts: { campaign, agentRef, agentName, selection:{scope,id,label}, onScopeClick,
//         onSuggested() }
//
// A turn is the agent's PROSE. This version changes nothing by itself: when the
// agent proposes pieces, times or a placement the SERVER saves them as suggestions
// (the existing M10 store) and the answer says so; the chip under the reply and the
// status line are written from that answer, never by this file. The box empties on send and a
// failed send puts the text back with the reason. The thread is saved with the campaign on the server,
// so a reload shows it; it is also kept here per campaign so a repaint of the box
// (every selection change repaints it) does not lose it, nor the draft, nor a turn
// still in flight. The text-size strip and the pop-out large view come from the shared
// desk-v1-chat-chrome.js (barHTML in the markup, attach on mount), as in the Studio thread;
// pop-out lifts this same element, so a send from it lands in the docked thread.
// Live Desk only: the sample (demo) campaigns have no agent behind them, so `mount` returns false and the old box stays.
(function () {
  const INPUT_ID = 'desk-v1-camp-posy-input';   // desk-v1-how.js's Suggest button looks for this id
  const threads = {};   // campaignId -> { turns, loaded, loading, pending, draft }
  let _active = null;   // the mounted { box, opts } (the one a late answer repaints)

  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function _state(id) { return threads[id] || (threads[id] = { turns: [], loaded: false, loading: false, pending: null, draft: '' }); }

  const _EMPTY = 'Ask me anything about this campaign: ideas for the next posts, why a piece failed, how to fix it. Select a card to talk about just that piece.';

  function _scopeOf(opts) {
    const s = opts.selection || {};
    return s.scope === 'card' && s.id ? { kind: 'card', id: s.id, label: s.label || '' } : null;
  }

  // ── Markup ─────────────────────────────────────────────────────────────────
  function _turnHTML(t, agentName) {
    const tag = t.scope && t.scope.label ? ` · ${esc(t.scope.label)}` : '';
    if (t.role === 'user') {
      return `<div class="desk-v1-chat-turn desk-v1-chat-user" data-chat-turn="user"><div class="desk-v1-chat-meta">You${tag}</div><div class="desk-v1-chat-text">${esc(t.text)}</div></div>`;
    }
    let chip = '';
    if (t.suggested && t.suggested.summary) chip = `<div class="desk-v1-chat-chip" data-kind="ok" data-chat-suggested>${esc(t.suggested.summary)}</div>`;
    else if (t.suggest_error) chip = `<div class="desk-v1-chat-chip" data-kind="error" data-chat-suggested>Not saved as suggestions: ${esc(t.suggest_error)}</div>`;
    return `<div class="desk-v1-chat-turn desk-v1-chat-agent" data-chat-turn="agent"><div class="desk-v1-chat-meta">${esc(t.agent || agentName)}${tag}</div><div class="desk-v1-chat-text">${esc(t.text)}</div>${chip}</div>`;
  }

  function _paint(box, opts) {
    const host = box.querySelector('[data-chat-thread]');
    if (!host) return;
    const st = _state(opts.campaign.id);
    const name = opts.agentName;
    const rows = st.turns.map((t) => _turnHTML(t, name));
    if (st.pending) {
      rows.push(`<div class="desk-v1-chat-turn desk-v1-chat-user" data-chat-turn="user" data-chat-pending><div class="desk-v1-chat-meta">You${st.pending.scope && st.pending.scope.label ? ` · ${esc(st.pending.scope.label)}` : ''}</div><div class="desk-v1-chat-text">${esc(st.pending.text)}</div></div>`);
      rows.push(`<div class="desk-v1-chat-turn desk-v1-chat-agent desk-v1-chat-thinking" data-chat-thinking><div class="desk-v1-chat-meta">${esc(name)}</div><div class="desk-v1-chat-text">Thinking…</div></div>`);
    }
    host.innerHTML = rows.length ? rows.join('') : `<p class="desk-v1-chat-empty" data-chat-empty>${esc(_EMPTY)}</p>`;
    host.scrollTop = host.scrollHeight;
  }

  function _html(opts) {
    const name = esc(opts.agentName);
    return `<div class="desk-v1-chat desk-v1-camp-chat" data-camp-chat>
      ${window.DeskV1ChatChrome ? window.DeskV1ChatChrome.barHTML() : ''}
      <div class="desk-v1-chat-thread" data-chat-thread role="log" aria-live="polite" tabindex="0" aria-label="Conversation with ${name}"></div>
      <div class="desk-v1-posy-output desk-v1-camp-chat-status" data-camp-chat-status role="status"></div>
      <div class="agent-input-row">
        <textarea class="agent-task-input desk-v1-posy-input" id="${INPUT_ID}" data-sb-ask rows="2" aria-label="Message ${name}" placeholder="Ask ${name} about the campaign, or brainstorm with them…"></textarea>
        <button type="button" class="btn-dispatch" data-posy-send="${INPUT_ID}">Send</button>
      </div>
    </div>`;
  }

  // The adopted answer: the server has already saved the suggestions; the page's own
  // copy of the campaign takes what the server returned so the What, When and Where
  // stops show them without a reload.
  function _adopt(camp, server) {
    camp.how = camp.how || {};
    camp.how.suggested = server.how && server.how.suggested ? server.how.suggested : {};
    camp.when = Object.assign({}, camp.when, { slots: (server.when && server.when.slots) || [] });
    camp.suggestBlocker = server.suggestBlocker || null;
  }

  // ── Mount ──────────────────────────────────────────────────────────────────
  function mount(box, opts) {
    if (!box || !opts || !opts.campaign || !window.DeskV1Store || !window.DeskV1Store.live()) return false;
    ['.desk-v1-posy-output', '.desk-v1-posy-chips', '.agent-input-row'].forEach((sel) => { const n = box.querySelector(sel); if (n) n.remove(); });
    box.insertAdjacentHTML('beforeend', _html(opts));
    if (window.DeskV1ChatChrome) window.DeskV1ChatChrome.attach(box.querySelector('[data-camp-chat]'));
    const camp = opts.campaign;
    const st = _state(camp.id);
    const ask = box.querySelector('textarea');
    const status = box.querySelector('[data-camp-chat-status]');
    const sendBtn = box.querySelector('[data-posy-send]');
    const say = (text, kind) => { if (status.isConnected) { status.textContent = text; status.dataset.kind = kind || ''; } };
    _active = { box, opts };
    // No agent picked yet: the head already says so and links to Brief; there is nobody to ask.
    const noAgent = !opts.agentRef;
    ask.disabled = noAgent || !!st.pending;
    sendBtn.disabled = ask.disabled;
    if (noAgent) ask.placeholder = 'Pick an agent for this campaign first.';
    ask.value = st.draft;
    const fit = () => { ask.style.height = 'auto'; if (ask.scrollHeight > 0) ask.style.height = Math.min(ask.scrollHeight + 2, 190) + 'px'; };
    ask.addEventListener('input', () => { st.draft = ask.value; fit(); });
    requestAnimationFrame(fit);
    const scopeBtn = box.querySelector('[data-scope-trigger]');
    if (scopeBtn && typeof opts.onScopeClick === 'function') scopeBtn.onclick = () => opts.onScopeClick(scopeBtn);
    _paint(box, opts);

    if (!st.loaded && !st.loading) {
      st.loading = true;
      window.DeskV1Store.api('GET', `/api/desk/campaigns/${encodeURIComponent(camp.id)}/chat`)
        .then((r) => { st.turns = (r.thread || []).slice(); st.loaded = true; })
        .catch((e) => { if (status.isConnected) say(`The earlier conversation could not be read: ${e && e.message ? e.message : e}`, 'error'); })
        .finally(() => { st.loading = false; if (_active && _active.box.isConnected) _paint(_active.box, _active.opts); });
    }

    async function send() {
      const text = ask.value.trim();
      if (!text || ask.disabled || st.pending) return;
      // desk-v1-how.js's Suggest button fills this box with its fixed line and clicks Send:
      // it goes to the agent as a message like any other.
      const scope = _scopeOf(opts);
      const body = { message: text };
      if (scope) body.scope = scope;
      // The box empties the moment the message is sent: the pending "You" turn shows it.
      // A failed send puts it back, with the reason (below), so nothing typed is lost.
      st.pending = { text, scope };
      st.draft = '';
      ask.value = ''; fit();
      ask.disabled = true; sendBtn.disabled = true;
      _paint(box, opts);
      say(`Asking ${opts.agentName}…`, 'info');
      const live = () => (_active && _active.box.isConnected ? _active : null);
      try {
        const res = await window.DeskV1Store.api('POST', `/api/desk/campaigns/${encodeURIComponent(camp.id)}/chat`, body);
        st.pending = null;
        st.turns.push(...res.turns);
        st.loaded = true;
        const last = res.turns[res.turns.length - 1];
        if (res.campaign) {
          _adopt(camp, res.campaign);
          if (typeof opts.onSuggested === 'function') opts.onSuggested();
        }
        // The line says what the server's answer says happened, nothing more.
        const target = live() ? live().box.querySelector('[data-camp-chat-status]') : null;
        const line = last.suggested ? last.suggested.summary
          : last.suggest_error ? `${res.agent.name} answered. Not saved as suggestions: ${last.suggest_error}`
            : `${res.agent && res.agent.name ? res.agent.name : 'The agent'} answered. Nothing was changed.`;
        if (target) { target.textContent = line; target.dataset.kind = last.suggest_error ? 'error' : 'ok'; }
      } catch (err) {
        st.pending = null;
        if (!st.draft) st.draft = text;   // a repaint of the box shows it too
        const target = live() ? live().box.querySelector('[data-camp-chat-status]') : null;
        if (target) { target.textContent = `Nothing was changed: ${err && err.message ? err.message : err}. Your message is back in the box.`; target.dataset.kind = 'error'; }
      } finally {
        // the box may have been repainted (a new element) while the agent was answering
        if (live()) {
          _paint(live().box, live().opts);
          const f = live().box.querySelector('textarea');
          const b = live().box.querySelector('[data-posy-send]');
          if (f && live().opts.agentRef) {
            f.disabled = false; if (b) b.disabled = false;
            if (!f.value && st.draft) { f.value = st.draft; f.dispatchEvent(new Event('input')); }
            f.focus({ preventScroll: true });
          }
        }
      }
    }

    sendBtn.addEventListener('click', send);
    ask.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
      e.preventDefault();
      send();
    });
    return true;
  }

  window.DeskV1CampaignChat = { mount };
})();

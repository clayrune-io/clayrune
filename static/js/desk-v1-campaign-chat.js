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
// A turn is the agent's PROSE, plus saved dispatch receipts/results when used. When the
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

  // DIAGNOSTIC ONLY, changes no behaviour (2026-11-05). Ron saw his sent message back in the box after
  // a new campaign's reply and the pop-out; neither Playwright keystrokes nor the live page reproduce it
  // (docs/desk_v1/CAMPAIGN_CHAT_DRAFTS.md), so the source is something outside this file (an input method,
  // dictation, a browser extension that re-inserts text). When the box ends up holding exactly the text that
  // was just sent, say HOW it got there: an input event (inputType, composing, trusted) or a bare value
  // that only showed up when the box took focus. Read it with `window.__deskChatReentry` or the console.
  function _noteReentry(st, ask, how, e) {
    // Only a JUMP counts: the box was empty (draft '') and now holds the whole sent text. Typing the same words again passes through partial values.
    if (!st.sentText || st.pending || st.draft !== '' || ask.value.trim() !== st.sentText) return;
    const rec = { how, at: new Date().toISOString(), inputType: e && e.inputType || null, isTrusted: !!(e && e.isTrusted), isComposing: !!(e && e.isComposing), focused: document.activeElement === ask, popped: !!ask.closest('.is-popped') };
    (window.__deskChatReentry = window.__deskChatReentry || []).push(rec);
    if (window.__deskChatReentry.length > 20) window.__deskChatReentry.shift();
    console.warn('[desk-chat] the sent message re-entered the box', rec);
  }

  const _EMPTY = 'Ask me anything about this campaign: ideas for the next posts, why a piece failed, how to fix it. Select a card to talk about just that piece.';

  function _scopeOf(opts) {
    const s = opts.selection || {};
    return s.scope === 'card' && s.id ? { kind: 'card', id: s.id, label: s.label || '' } : null;
  }

  // ── Markup ─────────────────────────────────────────────────────────────────
  function _turnHTML(t, agentName) {
    if (t.dispatch && window.DeskV1Dispatch) return window.DeskV1Dispatch.card(t);
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
      if (!st.turns.some((t) => t.role === 'user' && t.text === st.pending.text && !st.pending.priorIds.has(t.id))) {
        rows.push(`<div class="desk-v1-chat-turn desk-v1-chat-user" data-chat-turn="user" data-chat-pending><div class="desk-v1-chat-meta">You${st.pending.scope && st.pending.scope.label ? ` · ${esc(st.pending.scope.label)}` : ''}</div><div class="desk-v1-chat-text">${esc(st.pending.text)}</div></div>`);
      }
      rows.push(`<div class="desk-v1-chat-turn desk-v1-chat-agent desk-v1-chat-thinking" data-chat-thinking><div class="desk-v1-chat-meta">${esc(name)}</div><div class="desk-v1-chat-text">Thinking… Research calls can take up to 8 minutes.</div></div>`);
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
      ${window.DeskV1ProductMaterials.readsHTML(window.DeskV1Store.state().projects.find((p) => p.id === opts.campaign.projectId), opts.agentRef)}
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
    // The edge between the stop's form and this column is draggable (the same call is safe on every repaint of the box).
    const rightcol = box.closest('.desk-v1-camp-rightcol');
    if (window.DeskV1Split && rightcol && rightcol.parentElement) {
      window.DeskV1Split.attach({ container: rightcol.parentElement, side: rightcol, key: 'campaign', layout: 'flex', minSide: 260, minMain: 380, label: 'Resize the campaign form and the conversation' });
    }
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
    ask.addEventListener('input', (e) => {
      // Only the current, editable composer owns the draft. Late input from a
      // pending turn or a textarea retired by a repaint cannot revive it.
      if (st.pending || !ask.isConnected || !_active || _active.box !== box) return;
      _noteReentry(st, ask, 'input', e);
      st.draft = ask.value; fit();
    });
    ask.addEventListener('focus', () => _noteReentry(st, ask, 'focus', null));
    requestAnimationFrame(fit);
    const scopeBtn = box.querySelector('[data-scope-trigger]');
    if (scopeBtn && typeof opts.onScopeClick === 'function') scopeBtn.onclick = () => opts.onScopeClick(scopeBtn);
    _paint(box, opts);
    if (window.DeskV1Dispatch) window.DeskV1Dispatch.watch(box, camp.id, st, () => _paint(box, opts));

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
      st.pending = { text, scope, priorIds: new Set(st.turns.map((t) => t.id)) };
      st.sentText = text;
      // Blur FIRST: an input method or dictation still composing in the box commits its text on blur,
      // and that commit would otherwise land after the clear below and put the sent message back.
      if (document.activeElement === ask) ask.blur();
      st.draft = '';
      ask.value = ''; fit();
      ask.disabled = true; sendBtn.disabled = true;
      _paint(box, opts);
      say(`Asking ${opts.agentName}…`, 'info');
      const live = () => (_active && _active.box.isConnected && _active.opts.campaign.id === camp.id ? _active : null);
      try {
        const res = await window.DeskV1Store.api('POST', `/api/desk/campaigns/${encodeURIComponent(camp.id)}/chat`, body);
        // Retire the draft BEFORE onSuggested can mount another composer.
        // The request was trimmed; clearing the draft is independent of any
        // whitespace or late input left in either textarea.
        st.draft = '';
        ask.value = '';
        st.pending = null;
        if (res.thread) st.turns = res.thread;
        else {
          const known = new Set(st.turns.map((t) => t.id));
          st.turns.push(...res.turns.filter((t) => !known.has(t.id)));
        }
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
            : res.thread ? `${res.agent.name} dispatched agents. Their results return here.`
              : `${res.agent && res.agent.name ? res.agent.name : 'The agent'} answered. Nothing was changed.`;
        if (target) { target.textContent = line; target.dataset.kind = last.suggest_error ? 'error' : 'ok'; }
      } catch (err) {
        st.pending = null;
        st.sentText = null;   // the request is back in the box on purpose
        st.draft = text;   // a repaint of the box shows the failed request too
        const target = live() ? live().box.querySelector('[data-camp-chat-status]') : null;
        if (target) { target.textContent = `The turn failed: ${err && err.message ? err.message : err}. Your message is back in the box. Check dispatched sessions before retrying.`; target.dataset.kind = 'error'; }
        // A fast model failure can follow a successful launch before the first
        // pending poll. Fetch its saved card even though the planner stopped.
        if (window.DeskV1Dispatch) {
          try { await window.DeskV1Dispatch.refresh(camp.id, st); }
          catch (refreshError) {
            if (target) target.textContent += ` Saved dispatches could not be read: ${refreshError && refreshError.message ? refreshError.message : refreshError}.`;
          }
        }
      } finally {
        // the box may have been repainted (a new element) while the agent was answering
        if (live()) {
          _paint(live().box, live().opts);
          const f = live().box.querySelector('textarea');
          const b = live().box.querySelector('[data-posy-send]');
          if (f && live().opts.agentRef) {
            f.disabled = false; if (b) b.disabled = false;
            // State owns the result: empty on success, the request on failure.
            f.value = st.draft;
            f.dispatchEvent(new Event('input'));
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

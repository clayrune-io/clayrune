// Desk v1 Studio: the conversation with "Your agent" beside a storyboard
// (Ron 2026-10-06: the ask box only sent an instruction and silently applied it;
// he wants to brainstorm, ask why a scene works, and go back and forth).
// Window-bridged module, no `import` (ground rule 1). One unit, one file
// (AGENT_RULES.md): desk-v1-studio.js paints the agent box around this and hands
// over the same BRIDGE desk-v1-story.js gets; the thread, the turn and how a
// proposed change is applied live here. Server side: mc/desk_story_chat.py.
//
//   window.DeskV1StoryChat.html(agentName, live)  the thread + ask box markup
//   window.DeskV1StoryChat.wire(box, bridge)      wire it (box = the agent aside)
//   window.DeskV1StoryChat.onSelect()             the selected scene changed
//
// A turn is the agent's PROSE, and only when the user asked for a change, a
// proposed change that is applied as ONE undoable scene command (the same two
// appliers the story panel uses, via DeskV1Story.ask). A question changes nothing.
// The thread is saved with the board on the server, so a reload shows it; it is
// also kept here per storyboard so a repaint of the box does not lose it. Every
// line that says something happened is written from the server's answer. The box
// empties the moment a message is sent (the thread shows it as a pending turn) and a
// failed send puts the typed message back. Text size and the large view come from
// desk-v1-chat-chrome.js.
(function () {
  const threads = {};   // 'kind:id' -> { turns: [], loaded: false }
  let _active = null;   // the wired { box, bridge } (for onSelect)
  let _lastSel = '';

  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  const Story = () => window.DeskV1Story;
  function _state(owner) {
    const k = `${owner.kind}:${owner.id}`;
    return threads[k] || (threads[k] = { turns: [], loaded: false, pending: null });
  }

  // ── Markup ─────────────────────────────────────────────────────────────────
  function html(agentName, live) {
    const name = esc(agentName);
    return `<div class="desk-v1-chat" data-chat>
      ${window.DeskV1ChatChrome ? window.DeskV1ChatChrome.barHTML() : ''}
      <div class="desk-v1-chat-thread" data-chat-thread role="log" aria-live="polite" tabindex="0" aria-label="Conversation with ${name}"></div>
      <textarea class="desk-v1-sb-agent-input desk-v1-sb-agent-ask" data-sb-ask data-autogrow="0.4" rows="2"${live ? '' : ' disabled title="Needs the live Desk: the sample storyboard has no agent behind it"'} placeholder="Ask ${name} about the storyboard, or tell them what to change…" aria-label="Message ${name}"></textarea>
      <div class="desk-v1-story-ask-status" data-sb-ask-status role="status"></div>
    </div>`;
  }

  const _EMPTY = 'Ask why a scene works or doesn’t, brainstorm, or tell me what to change. Select a scene to talk about just that one.';
  function _turnHTML(t, agentName) {
    if (t.role === 'user') {
      return `<div class="desk-v1-chat-turn desk-v1-chat-user" data-chat-turn="user"><div class="desk-v1-chat-meta">You${t.scene ? ` · scene ${esc(t.scene)}` : ''}</div><div class="desk-v1-chat-text">${esc(t.text)}</div></div>`;
    }
    const ch = t.change;
    let chip = '';
    if (ch) {
      const st = ch.status === 'applied' ? 'ok' : ch.status === 'failed' ? 'error' : 'info';
      const label = ch.status === 'applied' ? `Changed: ${ch.summary}` : ch.status === 'failed' ? `Not applied${ch.note ? ': ' + ch.note : ''} (${ch.summary})` : `Applying: ${ch.summary}`;
      chip = `<div class="desk-v1-chat-chip" data-kind="${st}" data-chat-change>${esc(label)}</div>`;
    } else if (t.change_error) {
      chip = `<div class="desk-v1-chat-chip" data-kind="error" data-chat-change>Not applied: ${esc(t.change_error)}</div>`;
    }
    return `<div class="desk-v1-chat-turn desk-v1-chat-agent" data-chat-turn="agent"><div class="desk-v1-chat-meta">${esc(t.agent || agentName)}${t.scene ? ` · scene ${esc(t.scene)}` : ''}</div><div class="desk-v1-chat-text">${esc(t.text)}</div>${chip}</div>`;
  }

  function _paint(box, bridge) {
    const host = box.querySelector('[data-chat-thread]');
    if (!host) return;
    const st = _state(bridge.owner());
    const name = bridge.agentName();
    const rows = st.turns.map((t) => _turnHTML(t, name));
    if (st.pending) {
      rows.push(`<div class="desk-v1-chat-turn desk-v1-chat-user" data-chat-turn="user" data-chat-pending><div class="desk-v1-chat-meta">You${st.pending.scene ? ` · scene ${esc(st.pending.scene)}` : ''}</div><div class="desk-v1-chat-text">${esc(st.pending.text)}</div></div>`);
      rows.push(`<div class="desk-v1-chat-turn desk-v1-chat-agent desk-v1-chat-thinking" data-chat-thinking><div class="desk-v1-chat-meta">${esc(name)}</div><div class="desk-v1-chat-text">Thinking…</div></div>`);
    }
    host.innerHTML = rows.length ? rows.join('') : `<p class="desk-v1-chat-empty" data-chat-empty>${esc(_EMPTY)}</p>`;
    host.scrollTop = host.scrollHeight;
  }

  // ── The ask box's hint and placeholder follow the selection ────────────────
  function _hint(bridge) {
    const t = Story().ask.target(bridge);
    if (!t) return '';
    return t.scene
      ? `Talking about scene ${t.n}: a change applies to that scene only. Enter sends, Shift+Enter adds a line.`
      : 'Talking about the whole storyboard (select a scene to focus on one). Enter sends, Shift+Enter adds a line.';
  }
  function onSelect() {
    if (!_active || !_active.bridge.token()) return;
    const { box, bridge } = _active;
    const ask = box.querySelector('[data-sb-ask]');
    if (!ask || !ask.isConnected) return;
    const t = Story().ask.target(bridge);
    ask.placeholder = `Ask ${bridge.agentName()} about ${t && t.scene ? 'scene ' + t.n : 'the storyboard'}, or tell them what to change…`;
    // A new selection retires the last result line (it was about another scene); a
    // repaint with the same selection leaves it be.
    const sel = bridge.selected() || '';
    const hint = box.querySelector('[data-sb-ask-status]');
    if (hint && (sel !== _lastSel || !hint.textContent)) { hint.textContent = _hint(bridge); hint.dataset.kind = ''; }
    _lastSel = sel;
  }

  // ── Wiring ─────────────────────────────────────────────────────────────────
  function wire(box, bridge) {
    const ask = box && box.querySelector('[data-sb-ask]');
    if (!ask) return;
    const mine = bridge.token();
    const owner = bridge.owner();
    const st = _state(owner);
    const status = box.querySelector('[data-sb-ask-status]');
    const say = (text, kind) => { if (status && status.isConnected) { status.textContent = text; status.dataset.kind = kind || ''; } };
    _active = { box, bridge };
    if (window.DeskV1ChatChrome) window.DeskV1ChatChrome.attach(box.querySelector('[data-chat]'));
    _paint(box, bridge);
    onSelect();
    const A = Story().ask;

    if (!st.loaded && !st.loading && !ask.disabled) {
      st.loading = true;
      window.DeskV1Store.api('GET', `/api/desk/storyboard/chat?kind=${encodeURIComponent(owner.kind)}&id=${encodeURIComponent(owner.id)}`)
        .then((r) => { st.turns = (r.thread || []).slice(); st.loaded = true; })
        .catch((e) => { if (bridge.token() === mine) say(`The earlier conversation could not be read: ${e && e.message ? e.message : e}`, 'error'); })
        .finally(() => { st.loading = false; if (box.isConnected && _active && _active.box === box) _paint(box, bridge); });
    }

    const report = (turn, status2, note) => {
      turn.change.status = status2;
      if (note) turn.change.note = note;
      return window.DeskV1Store.api('POST', '/api/desk/storyboard/chat/status', { owner, turn_id: turn.id, status: status2, note: note || '' }).catch(() => {});
    };

    ask.addEventListener('keydown', async (e) => {
      if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
      const text = ask.value.trim();
      if (!text) return;
      e.preventDefault();
      if (A.isBusy()) return;
      const t = A.target(bridge);
      if (!t) return;
      A.setBusy(true);
      ask.disabled = true;
      const sentScenes = t.real.slice();
      const sentIds = sentScenes.map((s) => s.id).join('|');
      const scene = t.scene ? t.n : null;
      const body = {
        owner, message: text, scene, story: t.ctx.detail.story || '',
        scenes: sentScenes.map((s) => ({ label: s.label, line: s.line, duration_sec: s.durationSec })),
      };
      const agent = bridge.agentRef();
      if (agent) body.agent = agent;
      const pid = bridge.projectId();
      if (pid) body.project_id = pid;
      st.pending = { text, scene };
      // The pending 'You' turn now shows the message, so the box empties at once; a failed
      // send puts it back (below). The box stays disabled until the turn is over.
      ask.value = '';
      A.fit(ask);
      _paint(box, bridge);
      say(`Asking ${bridge.agentName()}…`, 'info');
      try {
        bridge.registerItem();
        let res;
        try {
          res = await window.DeskV1Store.api('POST', '/api/desk/storyboard/chat', body);
        } catch (err) {
          // A draft nobody has saved yet has nowhere to keep the conversation: save it, once, and ask again.
          if (!(err && err.status === 404 && /not saved yet/.test(err.message || ''))) throw err;
          await window.DeskV1Store.storyboard.save(owner, t.ctx.detail, bridge.extra());
          res = await window.DeskV1Store.api('POST', '/api/desk/storyboard/chat', body);
        }
        st.pending = null;
        st.turns.push(...res.turns);
        st.loaded = true;
        const turn = res.turns[res.turns.length - 1];
        if (bridge.token() !== mine) return;
        let line = `${res.agent && res.agent.name ? res.agent.name : 'The default engine'} answered.`;
        if (res.change) {
          try {
            const cur = bridge.ctx().detail.scenes.filter((s) => !s.placeholder).map((s) => s.id).join('|');
            if (res.change.mode === 'scene') {
              if (!bridge.ctx().detail.scenes.includes(t.scene)) throw new Error('that scene was removed while the agent was working');
              A.done(await A.applyScene(bridge, t.scene, res.change.scene, t.n));
            } else {
              if (cur !== sentIds) throw new Error('the storyboard changed while the agent was working');
              A.done(await A.applyRevision(bridge, sentScenes, res.change.scenes));
            }
            await report(turn, 'applied');
            line = `${res.change.summary} Undo is the arrow beside the bin.`;
            say(line, 'ok');
          } catch (err) {
            const why = err && err.message ? err.message : String(err);
            await report(turn, 'failed', why);
            say(`The change was not applied: ${why}. The answer is in the conversation.`, 'error');
          }
        } else {
          say(line, 'ok');
        }
      } catch (err) {
        st.pending = null;
        // the box may have been repainted (a new element) while the agent was answering
        const cur = (_active && _active.box.isConnected && _active.box.querySelector('[data-sb-ask]')) || ask;
        cur.value = cur.value ? `${text}
${cur.value}` : text;
        A.fit(cur);
        say(`Nothing was changed: ${err && err.message ? err.message : err}. Your message is back in the box.`, 'error');
      } finally {
        A.setBusy(false);
        ask.disabled = false;
        // the box may have been repainted (a new element) while the agent was answering
        if (_active && _active.box.isConnected) _paint(_active.box, _active.bridge);
        if (ask.isConnected) ask.focus({ preventScroll: true });
      }
    });
  }

  window.DeskV1StoryChat = { html, wire, onSelect };
})();

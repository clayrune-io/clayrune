// Desk v1 Studio: the STORY field, "Make storyboard from story", the agent ask
// box, and the per-scene instruction editor (Ron 2026-10-03: "there is no actual
// way to enter / write the story by which the video should be rendered").
// Window-bridged module, no `import` (ground rule 1). One unit, one file
// (AGENT_RULES.md): desk-v1-studio.js owns the scene list and its commands and
// hands this module a BRIDGE (`_storyBridge()` there); everything about the story,
// the model call and how its answer is applied lives here.
//
//   window.DeskV1Story.html(ready)            the panel's markup (a mount point)
//   window.DeskV1Story.mount(host, bridge)    wire the panel
//   window.DeskV1Story.wireAsk(input, bridge) the "Ask your agent" box, for real
//   window.DeskV1Story.onSelect()             the selected scene changed
//   window.DeskV1Story.lineEditorHTML(...)    a scene's instruction textarea + count
//   window.DeskV1Story.lineOver(li, focus)    true (and says so) when it is over the limit
//   window.DeskV1Story.grow(root)             size every auto-growing textarea
//
// The story is a field of the BOARD (`detail.story`, saved by the same PUT as the
// scenes). The model call is POST /api/desk/storyboard/generate (mc/desk_story.py);
// what it returns is applied as ONE undoable scene command through the bridge.
// Nothing here says a thing happened until it did: every status line is written
// from the answer, and a failure keeps the typed text where it was.
(function () {
  const MAX_STORY = 20000;   // mc/desk_storyboard.py MAX_STORY
  const MAX_LINE = 2000;     // mc/desk_storyboard.py MAX_LINE
  const SAVE_DELAY_MS = 600;

  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function _fmt(n) { return Number(n).toLocaleString('en-US'); }

  // ── Auto-growing textareas ─────────────────────────────────────────────────
  function _fit(ta) {
    if (!ta || !ta.isConnected) return;
    const cap = Math.max(120, Math.round(window.innerHeight * (parseFloat(ta.dataset.autogrow) || 0.6)));
    ta.style.height = 'auto';
    const want = ta.scrollHeight + 2;
    ta.style.height = Math.min(want, cap) + 'px';
    ta.style.overflowY = want > cap ? 'auto' : 'hidden';
  }
  function grow(root) {
    (root || document).querySelectorAll('textarea[data-autogrow]').forEach(_fit);
  }

  // The count under a scene's instruction box.
  function _countHTML(n) {
    return `<div class="desk-v1-story-linecount${n > MAX_LINE ? ' desk-v1-story-over' : ''}" data-line-count>${_fmt(n)} / ${_fmt(MAX_LINE)}</div>`;
  }
  function lineEditorHTML(line, name, error) {
    const n = String(line || '').trim().length;
    return `<textarea class="desk-v1-sb-edit-input desk-v1-sb-edit-line" data-scene-edit-line data-autogrow="0.7" rows="3" aria-label="${esc(name)} instructions" placeholder="What is seen, who is in it, camera and movement, any spoken line…">${esc(line || '')}</textarea>
      ${_countHTML(n)}
      <div class="desk-v1-story-error" data-scene-edit-error role="alert">${esc(error || '')}</div>`;
  }
  // Called before a scene's instruction is saved. Over the limit is refused here,
  // in the editor, with the text left where it is, rather than as a server error
  // after the editor is gone.
  function lineOver(li, focus) {
    const ta = li && li.querySelector('[data-scene-edit-line]');
    if (!ta) return false;
    const n = ta.value.trim().length;
    if (n <= MAX_LINE) return false;
    const err = li.querySelector('[data-scene-edit-error]');
    if (err) err.textContent = `Not saved: the instructions are ${_fmt(n - MAX_LINE)} characters over the ${_fmt(MAX_LINE)} limit. Shorten them; what you typed is still here.`;
    if (focus) ta.focus({ preventScroll: true });
    return true;
  }

  document.addEventListener('input', (e) => {
    const t = e.target;
    if (!(t instanceof HTMLTextAreaElement)) return;
    if (t.hasAttribute('data-autogrow')) _fit(t);
    if (t.hasAttribute('data-scene-edit-line')) {
      const count = t.parentElement && t.parentElement.querySelector('[data-line-count]');
      const n = t.value.trim().length;
      if (count) { count.textContent = `${_fmt(n)} / ${_fmt(MAX_LINE)}`; count.classList.toggle('desk-v1-story-over', n > MAX_LINE); }
      const err = t.parentElement && t.parentElement.querySelector('[data-scene-edit-error]');
      if (err && n <= MAX_LINE) err.textContent = '';
    }
  });

  // ── The model call ─────────────────────────────────────────────────────────
  let _busy = false;
  let _flushStory = null; // the mounted panel's save, for the page being hidden
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden' && _flushStory) _flushStory(); });
  function _generate(bridge, extra) {
    const ctx = bridge.ctx();
    const body = Object.assign({ story: ctx.detail.story || '' }, extra);
    const agent = bridge.agentRef();
    if (agent) body.agent = agent;
    const pid = bridge.projectId();
    if (pid) body.project_id = pid;
    return window.DeskV1Store.api('POST', '/api/desk/storyboard/generate', body);
  }
  function _clientScene(s) {
    return { id: s.id, label: s.label, line: s.line, durationSec: s.duration_sec, edited: false, picture: null, thumb: '', source: '' };
  }
  function _plural(n, one, many) { return `${n} ${n === 1 ? one : many}`; }
  // A command that was applied but refused by the server has been rolled back by
  // the store (it toasts why): that is a failure here, never a success line.
  function _done(r) {
    if (r && r.ok === false) throw new Error(r.error || 'the storyboard could not be saved');
  }
  // Who answered, as the server says it: with no agent picked it is the project's
  // default engine, and the line must not name an agent that was not asked.
  function _who(res) { return (res && res.agent && res.agent.name) || 'The default engine'; }

  // ── Applying an answer: one undoable command each ──────────────────────────
  function _applyBoard(bridge, scenes, how) {
    const ctx = bridge.ctx();
    const { detail } = ctx;
    const before = detail.scenes.slice();
    const fresh = scenes.map(_clientScene);
    const after = how === 'append' ? before.concat(fresh) : fresh;
    return bridge.command({
      label: how === 'append' ? `Added ${_plural(fresh.length, 'scene', 'scenes')} from the story` : `Made a storyboard from the story (${_plural(fresh.length, 'scene', 'scenes')})`,
      do: () => { detail.scenes = after.slice(); bridge.stopEditing(); bridge.repaint(); },
      undo: () => { detail.scenes = before.slice(); bridge.stopEditing(); bridge.repaint(); },
    });
  }
  // A whole-board revision: a returned scene with `from` is that scene changed
  // (its picture and id kept); one without is new; a current scene nobody
  // returned is gone (its picture stays in the library; Undo brings it back).
  function _applyRevision(bridge, sent, scenes) {
    const { detail } = bridge.ctx();
    const before = detail.scenes.slice();
    const after = scenes.map((r) => {
      const old = r.from ? sent[r.from - 1] : null;
      return old ? Object.assign({}, old, { label: r.label, line: r.line, durationSec: r.duration_sec }) : _clientScene(r);
    });
    return bridge.command({
      label: 'Changed the storyboard',
      do: () => { detail.scenes = after.slice(); bridge.stopEditing(); bridge.repaint(); },
      undo: () => { detail.scenes = before.slice(); bridge.stopEditing(); bridge.repaint(); },
    });
  }
  function _applyScene(bridge, s, one, n) {
    const prev = { label: s.label, line: s.line, durationSec: s.durationSec };
    return bridge.command({
      label: `Changed scene ${n}`,
      do: () => { s.label = one.label; s.line = one.line; s.durationSec = one.duration_sec; s.placeholder = false; bridge.stopEditing(); bridge.repaint(); },
      undo: () => { s.label = prev.label; s.line = prev.line; s.durationSec = prev.durationSec; bridge.stopEditing(); bridge.repaint(); },
    });
  }

  // ── The panel ──────────────────────────────────────────────────────────────
  function html(ready) {
    return `<section class="desk-v1-story" data-sb-story-panel>
      <div class="desk-v1-story-head">
        <label class="desk-v1-story-title" for="sb-story">The story</label>
        <span class="desk-v1-story-count" data-story-count></span>
      </div>
      <textarea class="desk-v1-story-input" id="sb-story" data-sb-story data-autogrow="0.6" rows="5"${ready ? '' : ' disabled'} placeholder="${ready ? 'Paste or write the story this video should tell: every shot, who is in it, what happens, any lines or captions. The scenes are made from it.' : 'Loading…'}"></textarea>
      <div class="desk-v1-story-actions">
        <button type="button" class="btn-add" data-story-make${ready ? '' : ' disabled'}>Make storyboard from story</button>
        <span class="desk-v1-story-note" data-story-cost>One model call by your agent: about 2¢ for a typical story.</span>
        <span class="desk-v1-story-save" data-story-save role="status"></span>
      </div>
      <div class="desk-v1-story-choice" data-story-choice role="group" aria-label="What to do with the scenes already here" hidden></div>
      <div class="desk-v1-story-status" data-story-status role="status"></div>
    </section>`;
  }

  // Saving the story: typed text goes into `detail.story` at once (so any scene
  // save carries it), and its own save runs shortly after the last keystroke, on
  // blur, and when the page is hidden. The save keeps going if the user leaves the
  // page meanwhile: it holds `detail`, not the DOM.
  function mount(host, bridge) {
    const ctx = host && bridge.ctx();
    if (!ctx) return;
    const { detail } = ctx;
    const mine = bridge.token();
    const ta = host.querySelector('[data-sb-story]');
    const count = host.querySelector('[data-story-count]');
    const saveEl = host.querySelector('[data-story-save]');
    const statusEl = host.querySelector('[data-story-status]');
    const makeBtn = host.querySelector('[data-story-make]');
    const choice = host.querySelector('[data-story-choice]');
    const live = () => bridge.token() === mine && host.isConnected;
    const setStatus = (text, kind) => { if (live()) { statusEl.textContent = text || ''; statusEl.dataset.kind = text ? (kind || 'info') : ''; } };
    const setSave = (text, kind) => { if (live()) { saveEl.textContent = text || ''; saveEl.dataset.kind = kind || ''; } };
    const hideChoice = () => { choice.hidden = true; choice.innerHTML = ''; };

    ta.value = detail.story || '';
    const paintCount = () => {
      const n = ta.value.length;
      count.textContent = `${_fmt(n)} / ${_fmt(MAX_STORY)}`;
      count.classList.toggle('desk-v1-story-over', n > MAX_STORY);
    };
    paintCount();
    _fit(ta);

    let timer = null;
    let dirty = false;
    let saving = false;
    const save = async () => {
      clearTimeout(timer);
      timer = null;
      if (!dirty || saving) return;
      if ((detail.story || '').length > MAX_STORY) {
        setSave(`Not saved: ${_fmt(detail.story.length - MAX_STORY)} characters over the ${_fmt(MAX_STORY)} limit.`, 'error');
        return;
      }
      saving = true;
      dirty = false;
      setSave('Saving…', '');
      try {
        bridge.registerItem();
        await window.DeskV1Store.storyboard.save(bridge.owner(), detail, bridge.extra());
        setSave(dirty ? '' : 'Saved', '');
      } catch (e) {
        dirty = true;
        const msg = e && e.message ? e.message : String(e);
        setSave(e && e.status === 409
          ? 'Not saved: this storyboard was changed somewhere else. Your text is still here; reload to see the other version.'
          : `Not saved: ${msg}. Your text is still here.`, 'error');
      }
      saving = false;
      if (dirty && !timer && (detail.story || '').length <= MAX_STORY) timer = setTimeout(save, SAVE_DELAY_MS);
    };
    ta.addEventListener('input', () => {
      detail.story = ta.value;
      paintCount();
      dirty = true;
      setSave('', '');
      clearTimeout(timer);
      timer = setTimeout(save, SAVE_DELAY_MS);
    });
    ta.addEventListener('blur', save);
    _flushStory = save;

    async function run(how) {
      hideChoice();
      if (_busy) return;
      _busy = true;
      makeBtn.disabled = true;
      host.setAttribute('aria-busy', 'true');
      setStatus(`Asking ${bridge.agentName()} to write the scenes…`, 'info');
      try {
        await save();
        const res = await _generate(bridge, { mode: 'board' });
        if (!live()) return;
        _done(await _applyBoard(bridge, res.scenes, how));
        setStatus(`${_who(res)} wrote ${_plural(res.scenes.length, 'scene', 'scenes')}${how === 'append' ? ' after the existing ones' : ''}. Undo is the arrow beside the bin.`, 'ok');
      } catch (e) {
        setStatus(`Nothing was changed: ${e && e.message ? e.message : e}`, 'error');
      } finally {
        _busy = false;
        host.removeAttribute('aria-busy');
        if (host.isConnected) makeBtn.disabled = false;
      }
    }

    makeBtn.onclick = () => {
      hideChoice();
      const story = ta.value;
      if (!story.trim()) { setStatus('Write or paste the story first.', 'error'); ta.focus({ preventScroll: true }); return; }
      if (story.length > MAX_STORY) { setStatus(`The story is ${_fmt(story.length - MAX_STORY)} characters over the ${_fmt(MAX_STORY)} limit. Shorten it first.`, 'error'); return; }
      setStatus('', '');
      const have = bridge.ctx().detail.scenes.filter((s) => !s.placeholder).length;
      if (!have) { run('replace'); return; }
      choice.innerHTML = `<span class="desk-v1-story-choice-text">This storyboard already has ${_plural(have, 'scene', 'scenes')}.</span>
        <button type="button" class="btn-secondary" data-story-replace>Replace them</button>
        <button type="button" class="btn-secondary" data-story-append>Add after them</button>
        <button type="button" class="desk-v1-sb-editbtn" data-story-cancel>Cancel</button>`;
      choice.hidden = false;
      choice.querySelector('[data-story-replace]').onclick = () => run('replace');
      choice.querySelector('[data-story-append]').onclick = () => run('append');
      choice.querySelector('[data-story-cancel]').onclick = hideChoice;
      choice.querySelector('[data-story-replace]').focus({ preventScroll: true });
    };
  }

  // ── The ask box ────────────────────────────────────────────────────────────
  // With a scene selected it changes that scene; with none, the whole board.
  let _askBridge = null;
  function _target(bridge) {
    const ctx = bridge.ctx();
    if (!ctx) return null;
    const real = ctx.detail.scenes.filter((s) => !s.placeholder);
    const id = bridge.selected();
    const s = id ? real.find((x) => x.id === id) : null;
    return { ctx, real, scene: s || null, n: s ? real.indexOf(s) + 1 : 0 };
  }
  function _askHint(bridge) {
    const t = _target(bridge);
    if (!t) return '';
    return t.scene ? `Changes scene ${t.n}. Enter sends, Shift+Enter adds a line.` : 'Changes the whole storyboard (select a scene to change just that one). Enter sends, Shift+Enter adds a line.';
  }
  function onSelect() {
    const bridge = _askBridge;
    if (!bridge || !bridge.token()) return;
    const ask = document.querySelector('[data-sb-ask]');
    if (!ask || !ask.isConnected) return;
    const t = _target(bridge);
    ask.placeholder = `Tell ${bridge.agentName()} what to change in ${t && t.scene ? 'scene ' + t.n : 'the whole storyboard'}…`;
    // A new selection retires the last result line (it was about another scene); a
    // repaint with the same selection leaves it be.
    const sel = bridge.selected() || '';
    const hint = document.querySelector('[data-sb-ask-status]');
    if (hint && (sel !== _lastSel || !hint.textContent)) { hint.textContent = _askHint(bridge); hint.dataset.kind = ''; }
    _lastSel = sel;
  }
  let _lastSel = '';

  function wireAsk(ask, bridge) {
    _askBridge = bridge;
    const status = ask.parentElement.querySelector('[data-sb-ask-status]');
    const mine = bridge.token();
    const say = (text, kind) => { if (status && status.isConnected) { status.textContent = text; status.dataset.kind = kind || ''; } };
    onSelect();
    ask.addEventListener('keydown', async (e) => {
      if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
      const text = ask.value.trim();
      if (!text) return;
      e.preventDefault();
      if (_busy) return;
      const t = _target(bridge);
      if (!t) return;
      if (!t.scene && !t.real.length) { say('There are no scenes yet. Write the story and make a storyboard first, then ask for changes.', 'error'); return; }
      _busy = true;
      ask.disabled = true;
      const sentScenes = t.real.slice();
      const toWire = (s) => ({ label: s.label, line: s.line, duration_sec: s.durationSec });
      say(`Asking ${bridge.agentName()}…`, 'info');
      try {
        const res = t.scene
          ? await _generate(bridge, { mode: 'scene', instruction: text, scene: toWire(t.scene) })
          : await _generate(bridge, { mode: 'board', instruction: text, scenes: sentScenes.map(toWire) });
        if (bridge.token() !== mine) return;
        if (t.scene) {
          if (!bridge.ctx().detail.scenes.includes(t.scene)) throw new Error('that scene was removed while the agent was working');
          _done(await _applyScene(bridge, t.scene, res.scene, t.n));
          say(`${_who(res)} changed scene ${t.n}. Undo is the arrow beside the bin.`, 'ok');
        } else {
          _done(await _applyRevision(bridge, sentScenes, res.scenes));
          say(`${_who(res)} changed the storyboard (${_plural(res.scenes.length, 'scene', 'scenes')}). Undo is the arrow beside the bin.`, 'ok');
        }
        ask.value = '';
        _fit(ask);
      } catch (err) {
        say(`Nothing was changed: ${err && err.message ? err.message : err}. Your request is still in the box.`, 'error');
      } finally {
        _busy = false;
        ask.disabled = false;
        if (ask.isConnected) ask.focus({ preventScroll: true });
      }
    });
  }

  window.DeskV1Story = { html, mount, wireAsk, onSelect, lineEditorHTML, lineOver, grow };
})();

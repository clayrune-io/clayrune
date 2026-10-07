// Desk v1 (MC-1019 = R1-W S9b) — generation engines on the page. Window-bridged
// module, no `import` (same ground rule as the rest of desk-v1-*.js).
//
// Three surfaces share this file because they share one server (mc/desk_engines.py)
// and one rule: nothing here ever holds a credential. The page only learns, per
// engine, whether it is connected (`connected.ready`); how to connect is the guided
// flow in desk-v1-guides.js (sign in, or paste a key step by step).
//
//   tileState / rowHTML / bindConnections   Connections' engines: a connected engine
//        (or one that needs signing in again) is a tile in the one grid, and the
//        Add service panel offers every other engine. Both show rowHTML: the
//        engine with its guided Connect, and the per-job limit the user sets for
//        it, in dollars or (Higgsfield sign-in) plan credits (human-only + the
//        retyped passcode: raising it loosens a spending gate).
//   mountVideoRender(host, {owner})     Studio's New video and the T5 director:
//        engine + model picker, the estimate BEFORE Render, Render (passcode),
//        job progress polled, the refusal reason when over a cap, the result.
//   mountImageGenerate(host, {...})     Studio's New image: prompt + engine +
//        model, estimate, Generate, the picture drawn from the library.
//
// Live only. Demo mode never reaches this file: the callers gate on
// `DeskV1Store.live()`, so the fixtures never spend and never claim to.
//
// Output is downloaded server-side into the Material library on `ready`; what the
// page paints is the library path the server answers with, never a vendor URL.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function _api(method, url, body) { return window.DeskV1Store.api(method, url, body); }
  function _toast(msg) { if (window.DeskV1Kit && window.DeskV1Kit.toast) window.DeskV1Kit.toast(msg); }
  function _usd(n) { return n == null || isNaN(n) ? '—' : '$' + Number(n).toFixed(Number(n) < 1 ? 4 : 2).replace(/(\.\d*?[1-9])0+$|\.0+$/, '$1'); }
  function _uid() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 8); }

  // A plan-credit engine (Higgsfield sign-in) is priced in credits, every other in dollars.
  function _credits(n) { return n == null || isNaN(n) ? '—' : Number(n).toFixed(2).replace(/\.?0+$/, '') + ' credits'; }
  function _isCredits(e) { return !!e && e.currency === 'credits'; }
  function _fmt(e, n) { return _isCredits(e) ? _credits(n) : _usd(n); }
  // The number a render is priced at, in the engine's own unit.
  function _amount(e, est) { return !est ? null : (_isCredits(e) ? est.credits : est.usd); }

  const POLL_MS = 3000;
  const RATIOS = ['16:9', '9:16', '1:1'];

  // A human-only POST: the dashboard passcode is retyped for each call. Resolves to
  // the parsed body; rejects with the SERVER's reason so the panel can show it.
  async function _humanPost(method, url, body, proof) {
    if (typeof window.humanProofFetch !== 'function') throw new Error('the passcode prompt is not available');
    const res = await window.humanProofFetch(url, { method, body: JSON.stringify(body || {}) }, proof);
    if (res === null) { const e = new Error('the dashboard passcode was not entered, so nothing was sent'); e.cancelled = true; throw e; }
    if (!res.ok) {
      const e = new Error((res.body && (res.body.error || res.body.message)) || `HTTP ${res.status}`);
      e.status = res.status; e.body = res.body;
      throw e;
    }
    return res.body;
  }

  let _engines = null;
  function list(projectId, opts) {
    if (_engines && !(opts && opts.force)) return Promise.resolve(_engines);
    const q = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
    return _api('GET', '/api/desk/engines' + q).then((b) => { _engines = (b && b.engines) || []; return _engines; });
  }

  // ── Connections ──────────────────────────────────────────────────────────
  // One card per service. Higgsfield has two routes: signing in (the default; it
  // spends the plan's credits) and an API key (Advanced; it spends dollars).
  const _openGuides = new Set();      // engine ids whose guide is expanded; survives a repaint

  function _stateWord(e) {
    const c = e.connected || {};
    if (c.ready) return { key: 'ok', word: 'Connected' };
    if (c.state === 'needs_signin') return { key: 'reauth', word: 'Needs sign-in' };
    if (c.state === 'vault_locked') return { key: 'locked', word: 'Vault locked' };   // the sign-in is saved; unlock, do not sign in again
    return { key: 'off', word: 'Not connected' };
  }

  function _limitHTML(e) {
    const credits = _isCredits(e);
    const lim = credits ? e.job_limit_credits : e.job_limit_usd;
    return `<div class="desk-v1-engine-limit">
          <label>${credits ? 'Most one job may spend (credits)' : 'Most one job may spend (USD)'}
            <input type="number" min="0" step="${credits ? '1' : '0.5'}" class="desk-v1-sb-edit-input" data-engine-limit-input
              value="${lim == null ? '' : esc(lim)}" placeholder="not set" aria-label="Per-job limit in ${credits ? 'credits' : 'USD'} for ${esc(e.label)}">
          </label>
          <button type="button" class="btn-secondary" data-engine-limit-save>Save limit</button>
          <span class="desk-v1-engine-limit-note" data-engine-limit-note>${lim == null
            ? 'Not set: a Studio render is refused until you set one (nothing else caps it).'
            : `A render over ${_fmt(e, lim)} is refused before anything is sent.`}</span>
        </div>`;
  }

  function _engineRowHTML(e) {
    const c = e.connected || {};
    const st = _stateWord(e);
    const kinds = kindsOf(e);
    const oauth = e.auth && e.auth.kind === 'oauth';
    const guide = window.DeskV1Guides && window.DeskV1Guides.keyGuideFor(e.id);
    const open = _openGuides.has(e.id);
    let action = '';
    if (st.key === 'locked') {
      action = '<button type="button" class="desk-v1-conn-btn" data-engine-unlock>Unlock the vault</button>';
    } else if (oauth) {
      action = c.ready
        ? '<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-engine-signin>Sign in again</button><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-engine-disconnect>Disconnect</button>'
        : `<button type="button" class="desk-v1-conn-btn" data-engine-signin>${st.key === 'reauth' ? 'Sign in again' : 'Sign in with Higgsfield'}</button>`;
    } else if (guide) {
      action = `<button type="button" class="desk-v1-conn-btn${c.ready ? ' desk-v1-conn-btn-inline' : ''}" data-engine-guide aria-expanded="${open}">${open ? 'Hide steps' : (c.ready ? 'Replace key' : 'Connect')}</button>`;
    }
    return `<div class="desk-v1-conn-row desk-v1-engine-row" data-conn-engine="${esc(e.id)}" data-currency="${_isCredits(e) ? 'credits' : 'usd'}">
      <div class="desk-v1-conn-main">
        <strong>${esc(e.label)}</strong> <span class="desk-v1-conn-kind">${esc(kinds)}</span>
        <div class="desk-v1-conn-status" data-engine-status data-ready="${c.ready ? 'true' : 'false'}" data-state="${st.key}">
          ${esc(st.word)}${c.ready || !c.reason || !oauth || st.key === 'locked' ? '' : ` · ${esc(c.reason)}`}
        </div>
        ${st.key === 'locked' ? `<div class="desk-v1-rules-hint" data-engine-locked-note>${esc(c.reason || '')}</div>` : oauth ? `<div class="desk-v1-rules-hint">${c.ready ? 'Signed in. Renders use the credits in your Higgsfield plan.' : 'Uses the credits in your Higgsfield plan. Nothing is charged in dollars.'}</div>` : ''}
        <div class="desk-v1-guide-status" data-guide-status role="status"></div>
        ${e.advanced && e.group === 'higgsfield' ? '<div class="desk-v1-rules-hint">An API key is billed in dollars on your Higgsfield developer account, not from your plan credits.</div>' : ''}
        ${_limitHTML(e)}
        ${guide && open ? window.DeskV1Guides.keyGuideHTML(e.id, !!c.ready) : ''}
      </div>
      ${action ? `<div class="desk-v1-conn-actions">${action}</div>` : ''}
    </div>`;
  }

  // The tile an engine gets, or null: only an engine that is connected, or that was
  // connected and needs signing in again, is on the grid. Every other engine is
  // offered by the Add service panel, not shown as an unconnected placeholder.
  function tileState(e) {
    const st = _stateWord(e);
    return st.key === 'off' ? null : st;
  }

  function kindsOf(e) { return Array.from(new Set((e.models || []).map((m) => m.kind))).join(' + '); }

  // One engine's whole connect card: status, the guided Connect, the limit.
  function rowHTML(e) { return _engineRowHTML(e); }

  function bindConnections(el, engines, repaint) {
    const G = window.DeskV1Guides;
    (engines || []).forEach((e) => {
      const row = el.querySelector(`[data-conn-engine="${CSS.escape(e.id)}"]`);
      if (!row) return;
      const note = row.querySelector('[data-engine-limit-note]');
      const status = row.querySelector('[data-guide-status]');
      const reload = () => list(null, { force: true }).then(() => repaint()).catch(() => repaint());
      const gateAt = row.querySelector('.desk-v1-conn-main');
      if (gateAt && window.DeskV1VaultGate && (e.auth && e.auth.kind === 'oauth' || (G && G.keyGuideFor(e.id)) || _stateWord(e).key === 'locked')) window.DeskV1VaultGate.attach(gateAt, _stateWord(e).key === 'locked' ? { onUnlocked: reload } : undefined);   // a sign-in or a pasted key ends in a vault write; after an unlock the card re-reads and is Connected, no new sign-in
      const unlock = row.querySelector('[data-engine-unlock]');
      if (unlock) unlock.onclick = async () => {
        const gate = gateAt && window.DeskV1VaultGate ? await window.DeskV1VaultGate.attach(gateAt, { onUnlocked: reload }) : null;
        const pass = gate && gate.querySelector('[data-vg-pass]');
        if (pass && !gate.hidden) pass.focus(); else reload();       // already unlocked elsewhere: just re-read
      };
      const signin = row.querySelector('[data-engine-signin]');
      if (signin) signin.onclick = async () => {
        signin.disabled = true;
        const out = await G.signIn(e.auth.service, { say: (t) => { status.textContent = t; status.dataset.state = 'pending'; }, alive: () => row.isConnected });
        if (!row.isConnected) return;
        if (out.ok) { _toast('Higgsfield is connected'); reload(); return; }
        status.textContent = out.message || ''; status.dataset.state = 'bad';
        if (out.code === 'vault_locked' && gateAt && window.DeskV1VaultGate) window.DeskV1VaultGate.attach(gateAt);
        signin.disabled = false;
      };
      const off = row.querySelector('[data-engine-disconnect]');
      if (off) off.onclick = async () => {
        off.disabled = true;
        try { await G.disconnect(e.auth.service); _toast('Higgsfield disconnected'); reload(); }
        catch (err) { status.textContent = err && err.message ? err.message : String(err); status.dataset.state = 'bad'; off.disabled = false; }
      };
      const gbtn = row.querySelector('[data-engine-guide]');
      if (gbtn) gbtn.onclick = () => { if (_openGuides.has(e.id)) _openGuides.delete(e.id); else _openGuides.add(e.id); repaint(); };
      if (G && G.keyGuideFor(e.id) && _openGuides.has(e.id)) {
        // A saved key flips the card to Connected in place (the guide stays open for the test).
        G.bindKeyGuide(row, e.id, { onSaved: () => list(null, { force: true }).then((fresh) => {
          Object.assign(e, fresh.find((x) => x.id === e.id) || {});
          const s = row.querySelector('[data-engine-status]');
          if (s) { s.textContent = (e.connected && e.connected.ready) ? 'Connected' : 'Not connected'; s.dataset.ready = String(!!(e.connected && e.connected.ready)); s.dataset.state = (e.connected && e.connected.ready) ? 'ok' : 'off'; }
        }).catch(() => {}) });
      }
      const save = row.querySelector('[data-engine-limit-save]');
      save.onclick = async () => {
        const raw = row.querySelector('[data-engine-limit-input]').value.trim();
        const val = raw === '' ? null : Number(raw);
        if (val !== null && !(val > 0)) { note.textContent = 'Enter an amount above 0, or leave it empty to clear the limit.'; return; }
        save.disabled = true;
        try {
          const out = await _humanPost('PUT', `/api/desk/engines/${encodeURIComponent(e.id)}/limit`, { job_limit_usd: val }, {
            title: val == null ? 'Clear the per-job limit' : 'Set the per-job limit',
            description: `Re-enter your dashboard passcode to ${val == null ? 'clear' : 'set'} the per-job limit for ${e.label}${val == null ? '' : ' to ' + _fmt(e, val)}. Renders over it are refused before anything is sent.`,
          });
          const now = _isCredits(e) ? out.job_limit_credits : out.job_limit_usd;
          if (_isCredits(e)) e.job_limit_credits = now; else e.job_limit_usd = now;
          _toast(now == null ? `Cleared the ${e.label} limit` : `${e.label}: renders over ${_fmt(e, now)} are refused`);
          repaint();
        } catch (err) {
          note.textContent = err && err.message ? err.message : String(err);
          save.disabled = false;
        }
      };
    });
  }

  // ── shared panel state, kept per owner so a repaint of the host keeps the pick ─
  const _panels = new Map();

  function _pick(engines, kind, st) {
    const usable = engines.filter((e) => (e.models || []).some((m) => m.kind === kind));
    if (!usable.length) return { usable, engine: null, model: null };
    const engine = usable.find((e) => e.id === st.engineId) || usable.find((e) => e.connected && e.connected.ready) || usable[0];
    st.engineId = engine.id;
    const models = engine.models.filter((m) => m.kind === kind);
    const model = models.find((m) => m.model_id === st.modelId) || models[0];
    st.modelId = model.model_id;
    return { usable, engine, model, models };
  }

  function _pickersHTML(p, st, kind) {
    const ratios = RATIOS;
    return `<div class="desk-v1-engine-pickers">
      <label>Engine <select class="desk-v1-cap-select" data-eng-engine>${p.usable.map((e) =>
        `<option value="${esc(e.id)}"${e.id === p.engine.id ? ' selected' : ''}>${esc(e.label)}${e.connected && e.connected.ready ? '' : ' (not connected)'}</option>`).join('')}</select></label>
      <label>Model <select class="desk-v1-cap-select" data-eng-model>${p.models.map((m) =>
        `<option value="${esc(m.model_id)}"${m.model_id === p.model.model_id ? ' selected' : ''}>${esc(m.label)}${m.status === 'preview' ? ' · preview' : ''}</option>`).join('')}</select></label>
      <label>Shape <select class="desk-v1-cap-select" data-eng-ratio>${ratios.map((r) =>
        `<option value="${esc(r)}"${r === st.ratio ? ' selected' : ''}>${esc(r)}</option>`).join('')}</select></label>
    </div>`;
  }

  // The picture strip on New image: library pictures to start from or refer to. A
  // model that takes none says so, and keeps whatever was picked (switching back
  // brings it back); the server refuses a request that has more than a model takes.
  function _refsOpts(p, st, onChange) {
    const max = ((p.model && p.model.inputs) || {}).reference_images_max || 0;
    st.refsMax = max;
    return {
      head: 'Start or reference pictures (optional)',
      hint: max ? `The engine starts from these or uses them as reference. This model takes up to ${max}.` : '',
      max,
      blocked: max ? '' : `${p.model.label} takes no start or reference picture. Choose another model to use one.`,
      attr: 'data-eng-refs', onChange,
    };
  }
  function _refsHTML(p, st) {
    return window.DeskV1LibraryRefs ? window.DeskV1LibraryRefs.html(st.refs, _refsOpts(p, st)) : '';
  }

  function _estimateHTML(st, eng) {
    if (st.estimating) return '<div class="desk-v1-engine-est" data-eng-estimate data-state="pricing">Pricing this render…</div>';
    if (st.estimateError) return `<div class="desk-v1-engine-est" data-eng-estimate data-state="refused"><span data-eng-reason>${esc(st.estimateError)}</span></div>`;
    const e = st.estimate;
    if (!e) return '<div class="desk-v1-engine-est" data-eng-estimate data-state="none"></div>';
    const lines = [];
    if (e.estimate && e.estimate.note) lines.push(e.estimate.note);
    if (e.plan) lines.push(`${e.plan.clips} clip${e.plan.clips === 1 ? '' : 's'}${e.plan.crop ? ', then cropped to 1:1' : ''}${e.plan.clips > 1 && !e.plan.crop ? ', then joined' : ''}`);
    if (_isCredits(eng) ? e.job_limit_credits != null : e.job_limit_usd != null) lines.push(`limit ${_fmt(eng, _isCredits(eng) ? e.job_limit_credits : e.job_limit_usd)} per job`);
    if (e.estimate && e.estimate.adjustments) {
      Object.keys(e.estimate.adjustments).forEach((k) => lines.push(`${eng ? eng.label : 'The engine'} changed ${k}: ${typeof e.estimate.adjustments[k] === 'object' ? JSON.stringify(e.estimate.adjustments[k]) : e.estimate.adjustments[k]}`));
    }
    if (e.budget) lines.push(`campaign budget left ${_usd(e.budget.remaining)}`);
    if (e.plan && e.plan.needs_ffmpeg && e.plan.ffmpeg_available === false) lines.push('ffmpeg is not installed on this machine: the clips are made and kept, and the render is held until you install it');
    return `<div class="desk-v1-engine-est" data-eng-estimate data-state="${e.refusal ? 'refused' : 'ok'}">
      <div>Estimate <strong data-eng-usd>${esc(_fmt(eng, _amount(eng, e.estimate)))}</strong>${e.estimate && e.estimate.approximate ? ' (approximate)' : ''}</div>
      ${lines.length ? `<div class="desk-v1-engine-est-detail">${esc(lines.join(' · '))}</div>` : ''}
      ${e.refusal ? `<div class="desk-v1-engine-refusal" data-eng-reason>${esc(e.refusal.message)}</div>` : ''}
    </div>`;
  }

  // ── Video: render a storyboard ───────────────────────────────────────────
  function _renderResultHTML(r) {
    if (!r) return '';
    const word = { queued: 'Queued', rendering: 'Rendering', ready: 'Ready', held: 'Held', failed: 'Failed' }[r.status] || r.status;
    const p = r.progress || { ready: 0, total: 0 };
    const bits = [`<div data-eng-render-status data-status="${esc(r.status)}">${r.status === 'rendering' || r.status === 'queued' ? '⟳ ' : ''}${esc(word)} · ${p.ready}/${p.total} clips${r.cost_credits ? ` · ${esc(_credits(r.cost_credits))} spent` : (r.cost_usd ? ` · ${esc(_usd(r.cost_usd))} spent` : '')}</div>`];
    if (r.hold) bits.push(`<div class="desk-v1-engine-refusal" data-eng-hold>${esc(r.hold)}</div>`);
    if (r.failure) bits.push(`<div class="desk-v1-engine-refusal" data-eng-failure>${esc(typeof r.failure === 'string' ? r.failure : (r.failure.message || JSON.stringify(r.failure)))}</div>`);
    (r.scenes || []).filter((s) => s.failure).forEach((s) => bits.push(`<div class="desk-v1-engine-refusal">Scene “${esc(s.label)}”: ${esc(typeof s.failure === 'string' ? s.failure : (s.failure.message || ''))}</div>`));
    const outs = (r.outputs && r.outputs.length ? r.outputs : (r.status === 'held' ? r.clips : [])) || [];
    if (outs.length) {
      bits.push(`<ul class="desk-v1-engine-outputs" data-eng-outputs>${outs.map((o) =>
        `<li data-eng-output data-path="${esc(o.path)}">${esc(String(o.path).split('/').pop())}${o.duration_sec ? ` · ${esc(o.duration_sec)}s` : ''}<span class="desk-v1-engine-saved"> · saved to the Material library${o.attached_to ? ' and attached' : ''}</span></li>`).join('')}</ul>`);
    }
    return `<div class="desk-v1-engine-result" data-eng-render>${bits.join('')}</div>`;
  }

  const _TERMINAL = ['ready', 'held', 'failed'];

  function mountVideoRender(host, opts) {
    const owner = opts.owner;
    const key = `v:${owner.kind}:${owner.id}`;
    const st = _panels.get(key) || { ratio: '16:9', render: null, estimate: null };
    _panels.set(key, st);
    const mine = Symbol('mount');
    st.mount = mine;
    const alive = () => st.mount === mine && host.isConnected;

    const paint = (engines) => {
      if (!alive()) return;
      const p = _pick(engines, 'video', st);
      if (!p.engine) { host.innerHTML = '<div class="desk-v1-engine-panel" data-eng-panel><div class="desk-v1-stub-empty">No video engines are available.</div></div>'; return; }
      const running = st.render && !_TERMINAL.includes(st.render.status);
      const busy = running || st.submitting;
      const conn = p.engine.connected || {};
      const canRender = !busy && !st.estimating && st.estimate && !st.estimate.refusal && !st.estimateError;
      host.innerHTML = `<div class="desk-v1-engine-panel" data-eng-panel data-kind="video">
        <div class="desk-v1-engine-head">Render with an engine</div>
        ${_pickersHTML(p, st, 'video')}
        ${conn.ready ? '' : `<div class="desk-v1-engine-refusal" data-eng-not-connected>${esc(p.engine.label)} is not connected yet. Open Connections in the Desk header to set it up.</div>`}
        ${_estimateHTML(st, p.engine)}
        ${st.error ? `<div class="desk-v1-engine-refusal" data-eng-error>${esc(st.error)}</div>` : ''}
        <div class="desk-v1-engine-actions">
          <button type="button" class="btn-add" data-eng-render-btn${canRender ? '' : ' disabled'}>${busy ? 'Rendering…' : 'Render'}${st.estimate && st.estimate.estimate && !busy ? ` · ${esc(_fmt(p.engine, _amount(p.engine, st.estimate.estimate)))}` : ''}</button>
          <button type="button" class="btn-secondary" data-eng-reprice${busy ? ' disabled' : ''}>Price again</button>
        </div>
        ${_renderResultHTML(st.render)}
      </div>`;
      host.querySelector('[data-eng-engine]').onchange = (ev) => { st.engineId = ev.target.value; st.modelId = null; st.estimate = null; paint(engines); estimate(engines); };
      host.querySelector('[data-eng-model]').onchange = (ev) => { st.modelId = ev.target.value; st.estimate = null; paint(engines); estimate(engines); };
      host.querySelector('[data-eng-ratio]').onchange = (ev) => { st.ratio = ev.target.value; st.estimate = null; paint(engines); estimate(engines); };
      host.querySelector('[data-eng-reprice]').onclick = () => estimate(engines);
      host.querySelector('[data-eng-render-btn]').onclick = () => submit(engines);
    };

    const body = (extra) => Object.assign({ owner, engine_id: st.engineId, model_id: st.modelId, aspect_ratio: st.ratio, project_id: opts.projectId || undefined }, extra || {});

    let _timer = null;
    const estimate = (engines) => {
      clearTimeout(_timer);
      st.estimating = true; st.estimateError = null; st.error = null;
      _timer = setTimeout(async () => {
        const seq = (st.seq = (st.seq || 0) + 1);
        try {
          const out = await _api('POST', '/api/desk/engines/render/estimate', body());
          if (st.seq !== seq) return;
          st.estimate = out; st.estimateError = null;
        } catch (e) {
          if (st.seq !== seq) return;
          st.estimate = null; st.estimateError = e && e.message ? e.message : String(e);
        }
        st.estimating = false;
        paint(engines);
      }, 150);
      paint(engines);
    };

    const poll = (engines) => {
      if (!st.render || _TERMINAL.includes(st.render.status)) return;
      const id = st.render.render_id;
      setTimeout(async () => {
        if (!alive() || !st.render || st.render.render_id !== id) return;
        try {
          const out = await _api('GET', `/api/desk/engines/renders/${encodeURIComponent(id)}`);
          st.render = out.render;
          if (typeof opts.onChange === 'function') opts.onChange(st.render);
        } catch (e) { st.error = `Could not read the render's progress: ${e && e.message ? e.message : e}`; }
        paint(engines);
        poll(engines);
      }, POLL_MS);
    };

    const submit = async (engines) => {
      const p = _pick(engines, 'video', st);
      let usd = st.estimate && st.estimate.estimate ? _amount(p.engine, st.estimate.estimate) : null;
      st.submitting = true; st.error = null; paint(engines);
      try {
        // The scenes may have changed since the price on screen was worked out:
        // price them again, and if the number moved, show it and stop. The user
        // approves a price they have seen.
        const fresh = await _api('POST', '/api/desk/engines/render/estimate', body());
        const now = fresh.estimate ? _amount(p.engine, fresh.estimate) : null;
        st.estimate = fresh;
        if (now !== usd) {
          st.submitting = false;
          st.error = `The price changed to ${_fmt(p.engine, now)} because the storyboard changed. Check it, then press Render again.`;
          paint(engines);
          return;
        }
        const out = await _humanPost('POST', '/api/desk/engines/renders', body({ idempotency_key: _uid() }), {
          title: 'Render this video',
          description: fresh.estimate && fresh.estimate.picture_pending
            ? `Re-enter your dashboard passcode to render this storyboard with ${p.engine.label} (${p.model.label}). The text-only estimate is ${_fmt(p.engine, usd)}. Your pictures are uploaded and priced after you approve Render; generation starts only if the full price fits your configured limit. The clips are saved to your Material library.`
            : `Re-enter your dashboard passcode to render this storyboard with ${p.engine.label} (${p.model.label}). It spends about ${_fmt(p.engine, usd)} of ${_isCredits(p.engine) ? 'the credits in your plan' : 'your account'} with them, and the clips are saved to your Material library.`,
        });
        st.render = out.render;
        _toast('Render started');
      } catch (e) {
        st.error = e && e.message ? e.message : String(e);
      }
      st.submitting = false;
      paint(engines);
      poll(engines);
    };

    host.innerHTML = '<div class="desk-v1-engine-panel" data-eng-panel><div class="desk-v1-stub-empty">Loading engines…</div></div>';
    list(opts.projectId).then(async (engines) => {
      if (!alive()) return;
      // What this owner last rendered survives a reload: show it (and keep polling a running one).
      if (!st.render && !st.loadedRender) {
        st.loadedRender = true;
        try {
          const b = await _api('GET', `/api/desk/engines/renders?owner_kind=${encodeURIComponent(owner.kind)}&owner_id=${encodeURIComponent(owner.id)}`);
          if (b && b.render) st.render = b.render;
        } catch (e) { /* no earlier render to show */ }
      }
      if (!alive()) return;
      _pick(engines, 'video', st);
      host.__engRefresh = () => { if (alive()) { st.estimate = null; estimate(engines); } };
      paint(engines);
      poll(engines);
      estimate(engines);
    }).catch((e) => {
      if (alive()) host.innerHTML = `<div class="desk-v1-engine-panel" data-eng-panel><div class="desk-v1-engine-refusal">Could not load the engines: ${esc(e && e.message ? e.message : e)}</div></div>`;
    });
  }

  // ── Image: generate one ──────────────────────────────────────────────────
  function mountImageGenerate(host, opts) {
    const key = `i:${opts.key || 'studio'}`;
    const st = _panels.get(key) || { ratio: '1:1', prompt: '', job: null, estimate: null };
    _panels.set(key, st);
    if (!st.refs) st.refs = []; // library pictures to start from / refer to (Pick from library)
    const mine = Symbol('mount');
    st.mount = mine;
    const alive = () => st.mount === mine && host.isConnected;

    const paint = (engines) => {
      if (!alive()) return;
      const p = _pick(engines, 'image', st);
      if (!p.engine) { host.innerHTML = '<div class="desk-v1-engine-panel" data-eng-panel><div class="desk-v1-stub-empty">No image engines are available.</div></div>'; return; }
      const j = st.job;
      const running = j && !_TERMINAL.includes(j.status) && j.status !== 'failed';
      const busy = running || st.submitting;
      const conn = p.engine.connected || {};
      const canGo = !busy && !st.estimating && st.estimate && !st.estimate.refusal && !st.estimateError && st.prompt.trim();
      const outs = (j && j.outputs) || [];
      host.innerHTML = `<div class="desk-v1-engine-panel" data-eng-panel data-kind="image">
        <div class="desk-v1-engine-head">Generate with an engine</div>
        <label class="desk-v1-sc-label" for="eng-prompt">Describe the picture</label>
        <textarea class="desk-v1-sb-edit-input" id="eng-prompt" rows="3" data-eng-prompt placeholder="What should the picture show?">${esc(st.prompt)}</textarea>
        ${_refsHTML(p, st)}
        ${_pickersHTML(p, st, 'image')}
        ${conn.ready ? '' : `<div class="desk-v1-engine-refusal" data-eng-not-connected>${esc(p.engine.label)} is not connected yet. Open Connections in the Desk header to set it up.</div>`}
        ${st.prompt.trim() ? _estimateHTML(st, p.engine) : '<div class="desk-v1-engine-est" data-eng-estimate data-state="none">Type a description to see the price.</div>'}
        ${st.error ? `<div class="desk-v1-engine-refusal" data-eng-error>${esc(st.error)}</div>` : ''}
        <div class="desk-v1-engine-actions">
          <button type="button" class="btn-add" data-eng-render-btn${canGo ? '' : ' disabled'}>${busy ? 'Generating…' : 'Generate'}${st.estimate && st.estimate.estimate && !busy ? ` · ${esc(_fmt(p.engine, _amount(p.engine, st.estimate.estimate)))}` : ''}</button>
        </div>
        ${j ? `<div class="desk-v1-engine-result" data-eng-render>
          <div data-eng-render-status data-status="${esc(j.status)}">${running ? '⟳ ' : ''}${esc(j.status === 'ready' ? 'Ready' : j.status === 'failed' ? 'Failed' : 'Generating')}${j.cost_credits ? ` · ${esc(_credits(j.cost_credits))} spent` : (j.cost_usd ? ` · ${esc(_usd(j.cost_usd))} spent` : '')}</div>
          ${j.failure ? `<div class="desk-v1-engine-refusal" data-eng-failure>${esc(typeof j.failure === 'string' ? j.failure : (j.failure.message || ''))}</div>` : ''}
          ${outs.map((o) => `<div data-eng-output data-path="${esc(o.path)}">${o.src ? `<img class="desk-v1-engine-img" src="${esc(o.src)}" alt="Generated picture">` : ''}<div class="desk-v1-engine-saved">Saved to the Material library: ${esc(o.path)}</div></div>`).join('')}
        </div>` : ''}
      </div>`;
      const ta = host.querySelector('[data-eng-prompt]');
      ta.oninput = () => { st.prompt = ta.value; };
      ta.onchange = () => { st.prompt = ta.value; st.estimate = null; paint(engines); estimate(engines); };
      host.querySelector('[data-eng-engine]').onchange = (ev) => { st.engineId = ev.target.value; st.modelId = null; st.estimate = null; paint(engines); estimate(engines); };
      host.querySelector('[data-eng-model]').onchange = (ev) => { st.modelId = ev.target.value; st.estimate = null; paint(engines); estimate(engines); };
      host.querySelector('[data-eng-ratio]').onchange = (ev) => { st.ratio = ev.target.value; st.estimate = null; paint(engines); estimate(engines); };
      host.querySelector('[data-eng-render-btn]').onclick = () => submit(engines);
      if (window.DeskV1LibraryRefs) window.DeskV1LibraryRefs.wire(host, st.refs, _refsOpts(p, st, (refs, what, it) => {
        if (what === 'add') window.DeskV1Kit.toast(`Using “${it.title}” from the library. The engine reads that file; nothing was copied.`);
        st.estimate = null; paint(engines); estimate(engines);
      }));
    };

    const body = (extra) => Object.assign({
      engine_id: st.engineId, model_id: st.modelId, kind: 'image', prompt: st.prompt.trim(), aspect_ratio: st.ratio,
      project_id: opts.projectId || undefined,
      reference_images: st.refs.length && st.refsMax ? st.refs.map((r) => ({ path: r.path })) : undefined,
    }, extra || {});

    let _timer = null;
    const estimate = (engines) => {
      clearTimeout(_timer);
      if (!st.prompt.trim()) { st.estimate = null; st.estimating = false; return; }
      st.estimating = true; st.estimateError = null; st.error = null;
      _timer = setTimeout(async () => {
        const seq = (st.seq = (st.seq || 0) + 1);
        try {
          const out = await _api('POST', '/api/desk/engines/estimate', body());
          if (st.seq !== seq) return;
          st.estimate = out; st.estimateError = null;
        } catch (e) {
          if (st.seq !== seq) return;
          st.estimate = null; st.estimateError = e && e.message ? e.message : String(e);
        }
        st.estimating = false;
        paint(engines);
      }, 150);
      paint(engines);
    };

    const poll = (engines) => {
      if (!st.job || _TERMINAL.includes(st.job.status)) return;
      const id = st.job.job_id;
      setTimeout(async () => {
        if (!alive() || !st.job || st.job.job_id !== id) return;
        try { st.job = (await _api('GET', `/api/desk/engines/jobs/${encodeURIComponent(id)}`)).job; } catch (e) { st.error = `Could not read the job's progress: ${e && e.message ? e.message : e}`; }
        paint(engines);
        if (st.job && st.job.status === 'ready' && typeof opts.onReady === 'function') opts.onReady(st.job);
        poll(engines);
      }, POLL_MS);
    };

    const submit = async (engines) => {
      const p = _pick(engines, 'image', st);
      const usd = st.estimate && st.estimate.estimate ? _amount(p.engine, st.estimate.estimate) : null;
      st.submitting = true; st.error = null; paint(engines);
      try {
        const out = await _humanPost('POST', '/api/desk/engines/jobs', body({ desk: { idempotency_key: _uid() } }), {
          title: 'Generate this picture',
          description: `Re-enter your dashboard passcode to generate this picture with ${p.engine.label} (${p.model.label}). It spends about ${_fmt(p.engine, usd)} of ${_isCredits(p.engine) ? 'the credits in your plan' : 'your account'} with them, and the picture is saved to your Material library.`,
        });
        st.job = out.job;
        if (st.job.status === 'ready' && typeof opts.onReady === 'function') opts.onReady(st.job);
      } catch (e) {
        st.error = e && e.message ? e.message : String(e);
      }
      st.submitting = false;
      paint(engines);
      poll(engines);
    };

    host.innerHTML = '<div class="desk-v1-engine-panel" data-eng-panel><div class="desk-v1-stub-empty">Loading engines…</div></div>';
    list(opts.projectId).then((engines) => {
      if (!alive()) return;
      _pick(engines, 'image', st);
      paint(engines);
      poll(engines);
      estimate(engines);
    }).catch((e) => {
      if (alive()) host.innerHTML = `<div class="desk-v1-engine-panel" data-eng-panel><div class="desk-v1-engine-refusal">Could not load the engines: ${esc(e && e.message ? e.message : e)}</div></div>`;
    });
  }

  window.DeskV1Engines = { list, tileState, kindsOf, rowHTML, bindConnections, mountVideoRender, mountImageGenerate };
})();

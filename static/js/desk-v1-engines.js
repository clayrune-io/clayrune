// Desk v1 (MC-1019 = R1-W S9b) — generation engines on the page. Window-bridged
// module, no `import` (same ground rule as the rest of desk-v1-*.js).
//
// Three surfaces share this file because they share one server (mc/desk_engines.py)
// and one rule: nothing here ever holds a credential. The page only learns, per
// engine, whether its vault entry exists (`connected.ready` + the entry's NAME) —
// a human creates the entry in Secrets, and nothing on this screen types one.
//
//   connectionsHTML / bindConnections   Connections' "Generation engines": each
//        engine, connected or not by vault name, and the per-job USD limit the
//        user sets for it (human-only + the retyped passcode: raising it loosens
//        a spending gate).
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
  function _engineRowHTML(e) {
    const c = e.connected || {};
    const kinds = Array.from(new Set((e.models || []).map((m) => m.kind))).join(' + ');
    const lim = e.job_limit_usd;
    return `<div class="desk-v1-conn-row desk-v1-engine-row" data-conn-engine="${esc(e.id)}">
      <div class="desk-v1-conn-main">
        <strong>${esc(e.label)}</strong> <span class="desk-v1-conn-kind">${esc(kinds)}</span>
        <div class="desk-v1-conn-status" data-engine-status data-ready="${c.ready ? 'true' : 'false'}">
          ${c.ready ? 'Connected' : 'Not connected'} · vault entry <code>${esc(c.vault_entry || '')}</code>${c.ready ? '' : ` · ${esc(c.reason || '')}`}
        </div>
        <div class="desk-v1-engine-limit">
          <label>Per-job limit (USD)
            <input type="number" min="0" step="0.5" class="desk-v1-sb-edit-input" data-engine-limit-input
              value="${lim == null ? '' : esc(lim)}" placeholder="not set" aria-label="Per-job limit in USD for ${esc(e.label)}">
          </label>
          <button type="button" class="btn-secondary" data-engine-limit-save>Save limit</button>
          <span class="desk-v1-engine-limit-note" data-engine-limit-note>${lim == null
            ? 'Not set: a Studio render is refused until you set one (nothing else caps it).'
            : `A render over ${_usd(lim)} is refused before anything is sent.`}</span>
        </div>
      </div>
      ${c.ready
        ? '<button type="button" class="desk-v1-conn-btn" data-engine-edit>Edit</button>'
        : '<button type="button" class="desk-v1-conn-btn" data-engine-connect>Connect ›</button>'}
    </div>`;
  }

  function connectionsHTML(engines) {
    if (!engines) return '<div class="desk-v1-stub-empty" data-engines-loading>Loading engines…</div>';
    if (!engines.length) return '<div class="desk-v1-stub-empty">No generation engines.</div>';
    return engines.map(_engineRowHTML).join('');
  }

  function bindConnections(el, engines, repaint) {
    (engines || []).forEach((e) => {
      const row = el.querySelector(`[data-conn-engine="${CSS.escape(e.id)}"]`);
      if (!row) return;
      // Connect / Edit open the Secrets form with this engine's labels. The human
      // types the value there and saves through the passcode-gated vault route;
      // nothing on this screen holds or sends a credential.
      const c = e.connected || {};
      const openForm = (create) => {
        if (e.credential && typeof window.openSecretEditor === 'function') window.openSecretEditor(c.vault_entry, { ...e.credential, create });
        else if (typeof window.openSecretsVault === 'function') window.openSecretsVault();   // a server with no form spec
      };
      const connect = row.querySelector('[data-engine-connect]');
      if (connect) connect.onclick = () => openForm(!c.exists);
      const edit = row.querySelector('[data-engine-edit]');
      if (edit) edit.onclick = () => openForm(false);
      const save = row.querySelector('[data-engine-limit-save]');
      const note = row.querySelector('[data-engine-limit-note]');
      save.onclick = async () => {
        const raw = row.querySelector('[data-engine-limit-input]').value.trim();
        const usd = raw === '' ? null : Number(raw);
        if (usd !== null && !(usd > 0)) { note.textContent = 'Enter an amount above 0, or leave it empty to clear the limit.'; return; }
        save.disabled = true;
        try {
          const out = await _humanPost('PUT', `/api/desk/engines/${encodeURIComponent(e.id)}/limit`, { job_limit_usd: usd }, {
            title: usd == null ? 'Clear the per-job limit' : 'Set the per-job limit',
            description: `Re-enter your dashboard passcode to ${usd == null ? 'clear' : 'set'} the per-job limit for ${e.label}${usd == null ? '' : ' to ' + _usd(usd)}. Renders over it are refused before anything is sent.`,
          });
          e.job_limit_usd = out.job_limit_usd;
          _toast(out.job_limit_usd == null ? `Cleared the ${e.label} limit` : `${e.label}: renders over ${_usd(out.job_limit_usd)} are refused`);
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

  function _estimateHTML(st) {
    if (st.estimating) return '<div class="desk-v1-engine-est" data-eng-estimate data-state="pricing">Pricing this render…</div>';
    if (st.estimateError) return `<div class="desk-v1-engine-est" data-eng-estimate data-state="refused"><span data-eng-reason>${esc(st.estimateError)}</span></div>`;
    const e = st.estimate;
    if (!e) return '<div class="desk-v1-engine-est" data-eng-estimate data-state="none"></div>';
    const lines = [];
    if (e.plan) lines.push(`${e.plan.clips} clip${e.plan.clips === 1 ? '' : 's'}${e.plan.crop ? ', then cropped to 1:1' : ''}${e.plan.clips > 1 && !e.plan.crop ? ', then joined' : ''}`);
    if (e.job_limit_usd != null) lines.push(`limit ${_usd(e.job_limit_usd)} per job`);
    if (e.budget) lines.push(`campaign budget left ${_usd(e.budget.remaining)}`);
    if (e.plan && e.plan.needs_ffmpeg && e.plan.ffmpeg_available === false) lines.push('ffmpeg is not installed on this machine: the clips are made and kept, and the render is held until you install it');
    return `<div class="desk-v1-engine-est" data-eng-estimate data-state="${e.refusal ? 'refused' : 'ok'}">
      <div>Estimate <strong data-eng-usd>${esc(_usd(e.estimate && e.estimate.usd))}</strong>${e.estimate && e.estimate.approximate ? ' (approximate)' : ''}</div>
      ${lines.length ? `<div class="desk-v1-engine-est-detail">${esc(lines.join(' · '))}</div>` : ''}
      ${e.refusal ? `<div class="desk-v1-engine-refusal" data-eng-reason>${esc(e.refusal.message)}</div>` : ''}
    </div>`;
  }

  // ── Video: render a storyboard ───────────────────────────────────────────
  function _renderResultHTML(r) {
    if (!r) return '';
    const word = { queued: 'Queued', rendering: 'Rendering', ready: 'Ready', held: 'Held', failed: 'Failed' }[r.status] || r.status;
    const p = r.progress || { ready: 0, total: 0 };
    const bits = [`<div data-eng-render-status data-status="${esc(r.status)}">${r.status === 'rendering' || r.status === 'queued' ? '⟳ ' : ''}${esc(word)} · ${p.ready}/${p.total} clips${r.cost_usd ? ` · ${esc(_usd(r.cost_usd))} spent` : ''}</div>`];
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
        ${conn.ready ? '' : `<div class="desk-v1-engine-refusal" data-eng-not-connected>${esc(p.engine.label)} is not connected: ${esc(conn.reason || '')}. A human adds the vault entry in Secrets.</div>`}
        ${_estimateHTML(st)}
        ${st.error ? `<div class="desk-v1-engine-refusal" data-eng-error>${esc(st.error)}</div>` : ''}
        <div class="desk-v1-engine-actions">
          <button type="button" class="btn-add" data-eng-render-btn${canRender ? '' : ' disabled'}>${busy ? 'Rendering…' : 'Render'}${st.estimate && st.estimate.estimate && !busy ? ` · ${esc(_usd(st.estimate.estimate.usd))}` : ''}</button>
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
      let usd = st.estimate && st.estimate.estimate ? st.estimate.estimate.usd : null;
      st.submitting = true; st.error = null; paint(engines);
      try {
        // The scenes may have changed since the price on screen was worked out:
        // price them again, and if the number moved, show it and stop. The user
        // approves a price they have seen.
        const fresh = await _api('POST', '/api/desk/engines/render/estimate', body());
        const now = fresh.estimate ? fresh.estimate.usd : null;
        st.estimate = fresh;
        if (now !== usd) {
          st.submitting = false;
          st.error = `The price changed to ${_usd(now)} because the storyboard changed. Check it, then press Render again.`;
          paint(engines);
          return;
        }
        const out = await _humanPost('POST', '/api/desk/engines/renders', body({ idempotency_key: _uid() }), {
          title: 'Render this video',
          description: `Re-enter your dashboard passcode to render this storyboard with ${p.engine.label} (${p.model.label}). It spends about ${_usd(usd)} of your account with them, and the clips are saved to your Material library.`,
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
        ${_pickersHTML(p, st, 'image')}
        ${conn.ready ? '' : `<div class="desk-v1-engine-refusal" data-eng-not-connected>${esc(p.engine.label)} is not connected: ${esc(conn.reason || '')}. A human adds the vault entry in Secrets.</div>`}
        ${st.prompt.trim() ? _estimateHTML(st) : '<div class="desk-v1-engine-est" data-eng-estimate data-state="none">Type a description to see the price.</div>'}
        ${st.error ? `<div class="desk-v1-engine-refusal" data-eng-error>${esc(st.error)}</div>` : ''}
        <div class="desk-v1-engine-actions">
          <button type="button" class="btn-add" data-eng-render-btn${canGo ? '' : ' disabled'}>${busy ? 'Generating…' : 'Generate'}${st.estimate && st.estimate.estimate && !busy ? ` · ${esc(_usd(st.estimate.estimate.usd))}` : ''}</button>
        </div>
        ${j ? `<div class="desk-v1-engine-result" data-eng-render>
          <div data-eng-render-status data-status="${esc(j.status)}">${running ? '⟳ ' : ''}${esc(j.status === 'ready' ? 'Ready' : j.status === 'failed' ? 'Failed' : 'Generating')}${j.cost_usd ? ` · ${esc(_usd(j.cost_usd))} spent` : ''}</div>
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
    };

    const body = (extra) => Object.assign({
      engine_id: st.engineId, model_id: st.modelId, kind: 'image', prompt: st.prompt.trim(), aspect_ratio: st.ratio,
      project_id: opts.projectId || undefined,
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
      const usd = st.estimate && st.estimate.estimate ? st.estimate.estimate.usd : null;
      st.submitting = true; st.error = null; paint(engines);
      try {
        const out = await _humanPost('POST', '/api/desk/engines/jobs', body({ desk: { idempotency_key: _uid() } }), {
          title: 'Generate this picture',
          description: `Re-enter your dashboard passcode to generate this picture with ${p.engine.label} (${p.model.label}). It spends about ${_usd(usd)} of your account with them, and the picture is saved to your Material library.`,
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

  window.DeskV1Engines = { list, connectionsHTML, bindConnections, mountVideoRender, mountImageGenerate };
})();

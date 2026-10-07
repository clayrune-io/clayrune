// MC-1070 scene-card controls. Mounted by the async render panel after MC-1068.
// Every paid request uses the dashboard's SAME humanProofFetch passcode gate.
import { confirmationMatches, priceConfirmation, heldPriceConfirmation, dismissedPriceHold, dismissPriceHold } from './desk-v1-render-price.js';

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
const amount = (q) => q && q.estimate && (q.estimate.credits ?? q.estimate.usd);
const price = (q) => {
  const n = amount(q);
  if (!Number.isFinite(n)) return 'Price unavailable';
  return q.estimate.credits != null ? `${n} credits` : `$${Number(n.toFixed(6))}`;
};

export function mountSceneRenderControls(root, toolbar, opts) {
  // request(ids) reads the shared engine/model/shape pick. refresh() must be
  // called after board/picker edits; onRender hands off to the existing poll.
  const st = opts.state || { selected: [], quotes: {}, confirmations: {} };
  st.selected ||= []; st.quotes ||= {}; st.confirmations ||= {}; st.bases ||= {};
  let epoch = 0, timer = null, disposed = false;
  st.submitting ||= false;
  const pending = new Set();
  const alive = () => !disposed && root.isConnected && toolbar.isConnected && (!opts.alive || opts.alive());
  const busy = () => st.submitting || !!(opts.busy && opts.busy());
  const submitting = (on) => { st.submitting = on; if (opts.onBusy) opts.onBusy(on); };
  const cards = () => Array.from(root.querySelectorAll('[data-scene-id]'))
    .filter((el) => (el.classList.contains('desk-v1-sb-scene') || el.classList.contains('desk-v1-video-scene')) && !el.hasAttribute('data-scene-placeholder'));
  const ids = () => cards().map((el) => el.dataset.sceneId);
  const canonical = (chosen) => ids().filter((id) => chosen.includes(id));
  const key = (chosen) => JSON.stringify(chosen);
  const quote = (request) => window.DeskV1Store.api('POST', '/api/desk/engines/render/estimate', request);
  const holdKey = (chosen) => `subset:${JSON.stringify(opts.request(chosen).owner)}:${key(chosen)}`;
  const report = (message) => { const el = toolbar.querySelector('[data-scene-render-note]'); if (el) el.textContent = message || ''; };

  function paint() {
    if (!alive()) return;
    st.selected = canonical(st.selected);
    cards().forEach((card) => {
      const id = card.dataset.sceneId;
      let controls = card.querySelector('[data-scene-render-controls]');
      if (!controls) {
        controls = document.createElement('div');
        controls.className = 'desk-v1-engine-actions';
        controls.setAttribute('data-scene-render-controls', '');
        card.append(controls);
      }
      const q = st.quotes[key([id])];
      controls.innerHTML = `<label><input type="checkbox" data-scene-render-select
        aria-label="Select scene ${esc(id)} for rendering"${st.selected.includes(id) ? ' checked' : ''}${busy() ? ' disabled' : ''}> Select</label>
        <button type="button" class="btn-secondary" data-scene-render-one${busy() || pending.has(key([id])) ? ' disabled' : ''}>Render this scene${q ? ' · ' + esc(price(q)) : ''}</button>`;
      controls.onclick = (ev) => ev.stopPropagation(); // keep selection out of the scene editor
      controls.onpointerdown = (ev) => ev.stopPropagation(); // keep buttons out of strip dragging
      controls.querySelector('[data-scene-render-select]').onchange = (ev) => {
        st.selected = canonical(ev.target.checked ? [...st.selected, id] : st.selected.filter((sid) => sid !== id));
        report(''); paint();
        clearTimeout(timer);
        if (st.selected.length) timer = setTimeout(() => reprice([...st.selected], true), 150);
      };
      controls.querySelector('[data-scene-render-one]').onclick = () => render([id]);
    });
    const selected = st.selected, k = key(selected), q = st.quotes[k];
    // Keep the status node in place, including a server's price-changed message.
    let actions = toolbar.querySelector('[data-scene-render-actions]');
    if (!actions) {
      toolbar.innerHTML = '<div class="desk-v1-engine-actions" data-scene-render-actions></div><div role="status" aria-live="polite" data-scene-render-note></div>';
      actions = toolbar.querySelector('[data-scene-render-actions]');
    }
    actions.innerHTML = `<button type="button" class="btn-add" data-scene-render-selected${!selected.length || busy() || pending.has(k) || !q || q.refusal || !Number.isFinite(amount(q)) ? ' disabled' : ''}>${st.submitting ? 'Working…' : `Render selected (${selected.length})`}${q ? ' · ' + esc(price(q)) : ''}</button>
      <button type="button" class="btn-secondary" data-scene-render-reprice${!selected.length || busy() || pending.has(k) ? ' disabled' : ''}>Price selected again</button>`;
    actions.querySelector('[data-scene-render-selected]').onclick = () => render([...st.selected]);
    actions.querySelector('[data-scene-render-reprice]').onclick = () => reprice([...st.selected]);
  }

  function adoptHeld(chosen, fresh) {
    const r = opts.getRender && opts.getRender();
    if (!r || !r.scene_ids || key(r.scene_ids) !== key(chosen) || dismissedPriceHold(holdKey(chosen)) === r.render_id) return;
    const confirmation = heldPriceConfirmation(r, opts.request(chosen), fresh);
    if (confirmation) { st.confirmations[key(chosen)] = confirmation; st.quotes[key(chosen)] = confirmation.quote; }
  }

  async function reprice(chosen, restoreHeld = false) {
    chosen = canonical(chosen);
    if (!alive() || !chosen.length || busy() || pending.has(key(chosen))) return;
    const k = key(chosen), mine = epoch, request = opts.request(chosen);
    const r = opts.getRender && opts.getRender();
    if (!restoreHeld && r) dismissPriceHold(holdKey(chosen), r.render_id);
    delete st.confirmations[k]; delete st.quotes[k]; pending.add(k); paint();
    try {
      const fresh = await quote(request);
      if (!alive() || mine !== epoch || JSON.stringify(request) !== JSON.stringify(opts.request(chosen))) return;
      st.quotes[k] = fresh;
      st.bases[k] = fresh;
      if (restoreHeld) adoptHeld(chosen, fresh);
      report(fresh.refusal?.message || (Number.isFinite(amount(st.quotes[k]))
        ? `Selected scene price: ${price(st.quotes[k])}. Press Render to approve.`
        : 'This service did not return a price. Nothing was sent.'));
    } catch (e) {
      if (alive() && mine === epoch) report(e.message || String(e));
    } finally {
      if (mine === epoch) pending.delete(k);
      paint();
    }
  }

  async function render(chosen) {
    chosen = canonical(chosen);
    if (!alive() || !chosen.length || busy() || pending.has(key(chosen))) return;
    const k = key(chosen);
    // A single scene's first click obtains and SHOWS its price. No passcode or
    // paid request is sent until a subsequent click accepts that shown price.
    if (!st.quotes[k] || st.quotes[k].refusal || !Number.isFinite(amount(st.quotes[k]))) { await reprice(chosen); return; }
    const shown = amount(st.quotes[k]);
    if (!Number.isFinite(shown) || st.quotes[k].refusal) return;
    const mine = epoch, request = opts.request(chosen);
    let fresh = null;
    submitting(true); report(''); paint();
    try {
      fresh = await quote(request);
      if (!alive() || mine !== epoch || JSON.stringify(request) !== JSON.stringify(opts.request(chosen))) return;
      const confirmed = confirmationMatches(st.confirmations[k], request, fresh);
      const q = confirmed ? st.confirmations[k].quote : fresh;
      if (!confirmed) delete st.confirmations[k];
      st.quotes[k] = q;
      st.bases[k] = fresh;
      if (fresh.refusal) throw new Error(fresh.refusal.message);
      if (!Number.isFinite(amount(q))) throw new Error('This service did not return a price. Nothing was sent.');
      if (amount(q) !== shown) { report(`The price changed to ${price(q)}. Press Render again to accept.`); return; }
      if (typeof window.humanProofFetch !== 'function') throw new Error('the passcode prompt is not available');
      const out = await window.humanProofFetch('/api/desk/engines/renders', {
        method: 'POST', body: JSON.stringify({ ...request, idempotency_key: uid(), shown_total: shown }),
      }, {
        title: chosen.length === 1 ? 'Render this scene' : `Render selected (${chosen.length})`,
        description: `Re-enter your dashboard passcode to render ${chosen.length} selected scene${chosen.length === 1 ? '' : 's'} for ${price(q)}. Only those clips are generated; existing clips are reused to rebuild the video.${q.estimate?.picture_pending ? ' Your pictures are uploaded and priced after approval; a higher price requires another Render click before generation.' : ''}`,
      });
      if (out === null) { report('The passcode was not entered. Nothing was sent.'); return; }
      if (!out.ok) {
        const e = new Error(out.body?.error || out.body?.message || `HTTP ${out.status}`);
        e.status = out.status; e.body = out.body; throw e;
      }
      delete st.confirmations[k];
      dismissPriceHold(holdKey(chosen), null);
      if (typeof opts.onRender === 'function') opts.onRender(out.body.render);
      report('Render started.');
    } catch (e) {
      if (alive() && mine === epoch) {
        const confirmation = priceConfirmation(e, request, fresh);
        if (confirmation) { st.confirmations[k] = confirmation; st.quotes[k] = confirmation.quote; }
        report(e.message || String(e));
      }
    } finally { submitting(false); paint(); }
  }

  function refresh() {
    const r = opts.getRender && opts.getRender();
    if (r?.scene_ids) dismissPriceHold(holdKey(r.scene_ids), r.render_id);
    epoch++; clearTimeout(timer); pending.clear();
    st.quotes = {}; st.confirmations = {}; st.bases = {};
    paint(); report('');
    if (st.selected.length && !busy()) timer = setTimeout(() => reprice([...st.selected]), 150);
  }
  function sync() {
    if (!alive()) return;
    const r = opts.getRender && opts.getRender();
    if (r?.hold?.kind === 'render_price_changed' && r.scene_ids && key(canonical(r.scene_ids)) === key(r.scene_ids)) {
      const k = key(r.scene_ids);
      if (st.bases[k]) adoptHeld(r.scene_ids, st.bases[k]);
      else if (!pending.has(k) && !busy()) reprice(r.scene_ids, true);
    }
    paint();
  }
  const saved = opts.getRender && opts.getRender();
  if (!st.selected.length && saved?.hold?.kind === 'render_price_changed' && saved.scene_ids?.length > 1) {
    st.selected = canonical(saved.scene_ids);
  }
  paint();
  return { refresh, render, reprice, sync, state: st, dispose: () => { disposed = true; epoch++; clearTimeout(timer); } };
}

// The shared engine panel is repainted often. The toolbar lives beside it so
// those repaints preserve its proof/quote lifecycle and the scene checkboxes.
export function wireSceneRenderPanel(host, opts) {
  if (!opts.root) return null;
  if (!document.querySelector('[data-scene-render-styles]')) {
    const link = document.createElement('link');
    link.rel = 'stylesheet'; link.href = '/static/css/desk-v1-render-scenes.css';
    link.setAttribute('data-scene-render-styles', ''); document.head.append(link);
  }
  const toolbar = document.createElement('div');
  toolbar.setAttribute('data-scene-render-toolbar', ''); host.after(toolbar);
  return mountSceneRenderControls(opts.root, toolbar, opts);
}

export function sceneResultNote(render) {
  if (!render.scene_ids) return '';
  const reused = (render.reused_scene_ids || []).length;
  const missing = (render.missing_scene_ids || []).length;
  return `<div data-scene-render-summary>${render.scene_ids.length} selected scene(s)${reused ? ` · ${reused} existing clip(s) reused` : ''}${missing ? ` · ${missing} scene(s) have no clip yet; this is a partial video` : ''}</div>`;
}

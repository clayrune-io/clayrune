// MC-1070 scene-card controls. Mounted by the async render panel after MC-1068.
// Every paid request uses the dashboard's SAME humanProofFetch passcode gate.
import { confirmationMatches, priceConfirmation } from './desk-v1-render-price.js';

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
  st.selected ||= []; st.quotes ||= {}; st.confirmations ||= {};
  let epoch = 0, timer = null, disposed = false;
  st.submitting ||= false;
  const pending = new Set();
  const alive = () => !disposed && root.isConnected && toolbar.isConnected && (!opts.alive || opts.alive());
  const busy = () => st.submitting || !!(opts.busy && opts.busy());
  const submitting = (on) => { st.submitting = on; if (opts.onBusy) opts.onBusy(on); };
  const cards = () => Array.from(root.querySelectorAll('[data-scene-id]'))
    .filter((el) => el.classList.contains('desk-v1-sb-scene') && !el.hasAttribute('data-scene-placeholder'));
  const ids = () => cards().map((el) => el.dataset.sceneId);
  const canonical = (chosen) => ids().filter((id) => chosen.includes(id));
  const key = (chosen) => JSON.stringify(chosen);
  const quote = (request) => window.DeskV1Store.api('POST', '/api/desk/engines/render/estimate', request);
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
      controls.querySelector('[data-scene-render-select]').onchange = (ev) => {
        st.selected = canonical(ev.target.checked ? [...st.selected, id] : st.selected.filter((sid) => sid !== id));
        report(''); paint();
        clearTimeout(timer);
        if (st.selected.length) timer = setTimeout(() => reprice([...st.selected]), 150);
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

  async function reprice(chosen) {
    chosen = canonical(chosen);
    if (!alive() || !chosen.length || busy() || pending.has(key(chosen))) return;
    const k = key(chosen), mine = epoch, request = opts.request(chosen);
    delete st.confirmations[k]; delete st.quotes[k]; pending.add(k); paint();
    try {
      const fresh = await quote(request);
      if (!alive() || mine !== epoch || JSON.stringify(request) !== JSON.stringify(opts.request(chosen))) return;
      st.quotes[k] = fresh;
      report(fresh.refusal?.message || (Number.isFinite(amount(fresh))
        ? `Selected scene price: ${price(fresh)}. Press Render to approve.`
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
    epoch++; clearTimeout(timer); pending.clear();
    st.quotes = {}; st.confirmations = {};
    paint(); report('');
    if (st.selected.length && !busy()) timer = setTimeout(() => reprice([...st.selected]), 150);
  }
  paint();
  return { refresh, render, reprice, state: st, dispose: () => { disposed = true; epoch++; clearTimeout(timer); } };
}

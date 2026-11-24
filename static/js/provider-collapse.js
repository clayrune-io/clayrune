// ── Settings > Connections: one collapsible card per vendor ───────────────
// Wraps the SHARED provider row (provider-auth.js _renderProviderRow) for
// Settings only. First-run setup renders the same row component and never
// calls this module, so its look is untouched.
//
// Collapsed (default): one header line — vendor name, status pill, a Default
// badge, the model a new run would launch with (faint), chevron. Expanded:
// today's row unchanged, and for Claude the API-credits card lives INSIDE the
// body (it used to be a sibling card).
//
// State. Settings rebuilds its whole body with innerHTML on every status poll,
// providerRefreshAll and draft edit, so open/closed cannot live in the DOM. It
// is a per-vendor map of EXPLICIT user choices in sessionStorage; a vendor with
// no choice recorded falls back to "needs attention" (see needsAttention).
// Toggling patches the card in place — no re-render, no lost half-typed field.
//
// Live search (settings-drill.js filterSettings) hides non-matching rows with
// .settings-hidden but knows nothing about cards. The search hook below, run
// after filterSettings on every keystroke, shows matching cards expanded (a
// TRANSIENT open, never stored) and hides the rest; leaving search view clears
// it, found through the data-view attribute _applySettingsView always sets.
(function () {
  const STORE_KEY = 'mc.providerCardsOpen.v1';
  const esc = (s) => (window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])));

  let choices = null; // { [vendor]: true|false } explicit open/closed picks
  function load() {
    if (choices) return choices;
    try {
      const raw = JSON.parse(sessionStorage.getItem(STORE_KEY) || '{}');
      choices = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
    } catch (_) { choices = {}; }
    return choices;
  }
  function persist() {
    try { sessionStorage.setItem(STORE_KEY, JSON.stringify(choices || {})); } catch (_) { /* private mode: in-memory only */ }
  }

  // A vendor that must be looked at starts expanded unless the user already
  // chose. Deliberately narrow, or the list is the wall of cards again:
  //  - the DEFAULT vendor is not installed / not signed in (nothing can run);
  //  - any vendor reporting a rejected sign-in, bad key or exhausted quota;
  //  - a vendor with an out-of-allowance record.
  function needsAttention(p, isDefault) {
    const s = p.auth_status;
    if (['oauth_rejected', 'quota_exceeded', 'invalid_api_key'].includes(s)) return true;
    if (p.installed && p.allowance_exhausted) return true;
    return !!isDefault && (!p.installed || s !== 'ok');
  }
  function isOpen(p, isDefault) {
    const c = load();
    return Object.prototype.hasOwnProperty.call(c, p.name) ? !!c[p.name] : needsAttention(p, isDefault);
  }

  // "Installed, not signed in" reads better than the row's bare "not signed in"
  // when the header stands alone; everything else is the row's own vocabulary
  // (provider-auth.js _providerStateLabel), capitalised.
  function statePill(p) {
    const base = window._providerStateLabel ? window._providerStateLabel(p) : { label: p.installed ? 'installed' : 'not installed', color: 'var(--text-faint)' };
    const label = (p.installed && p.auth_status === 'not_logged_in') ? 'installed, not signed in' : base.label;
    return { label: label.charAt(0).toUpperCase() + label.slice(1), color: base.color };
  }

  function modelText(p) {
    const eff = window.EnginePreferences && window.EnginePreferences.peek ? window.EnginePreferences.peek(p.name) : null;
    if (!eff) return '';
    if (!eff.model) return 'provider default';
    return String(eff.model).replace(/^tier:/, '');
  }

  const CHEVRON = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';

  // rowHTML: the full existing card, built by the caller (provider-settings.js).
  // extraHTML: anything that belongs inside the same body (Claude's API credits).
  function card(p, opts) {
    const n = esc(p.name);
    const isDefault = opts.defaultName === p.name;
    const open = isOpen(p, isDefault);
    const pill = statePill(p);
    const model = modelText(p);
    const tick = p.installed ? '' : `<input type="checkbox" class="prov-row-select settings-prov-install-sel prov-card-tick" value="${n}"
        aria-label="Select ${esc(p.display_name)} for batch install" style="width:15px;height:15px;accent-color:var(--accent)">`;
    return `<div class="prov-card${open ? ' is-open' : ''}" data-prov-card="${n}">
      <div class="prov-card-head">
        ${tick}
        <button type="button" class="prov-card-toggle" id="prov-card-toggle-${n}" aria-expanded="${open}" aria-controls="prov-card-body-${n}"
                onclick="providerCardToggle('${n}')">
          <span class="prov-card-main">
            <span class="prov-card-name">${esc(p.display_name)}</span>
            <span class="prov-card-state" style="color:${pill.color}">${esc(pill.label)}</span>
            ${isDefault ? '<span class="prov-card-badge">Default</span>' : ''}
            ${model ? `<span class="prov-card-model" title="Model a new run launches with">${esc(model)}</span>` : ''}
          </span>
          <span class="prov-card-chev">${CHEVRON}</span>
        </button>
      </div>
      <div class="prov-card-body" id="prov-card-body-${n}">${opts.rowHTML}${opts.extraHTML || ''}</div>
    </div>`;
  }

  function setOpen(el, open) {
    el.classList.toggle('is-open', open);
    const btn = el.querySelector('.prov-card-toggle');
    if (btn) btn.setAttribute('aria-expanded', String(open));
  }

  function toggle(name) {
    const el = document.querySelector(`#settings-providers-section .prov-card[data-prov-card="${CSS.escape(name)}"]`);
    if (!el) return;
    const open = !el.classList.contains('is-open');
    delete el.dataset.searchOpen; // the user took over: this is now their choice, not search's
    setOpen(el, open);
    load()[name] = open;
    persist();
  }

  // ── search ────────────────────────────────────────────────────────────
  function clearSearch() {
    document.querySelectorAll('#settings-providers-section .prov-card').forEach((el) => {
      el.classList.remove('settings-hidden');
      if (el.dataset.searchOpen) { delete el.dataset.searchOpen; setOpen(el, false); }
    });
  }
  function applySearch(q) {
    q = (q || '').trim().toLowerCase();
    const sec = document.getElementById('settings-providers-section');
    if (!sec) return;
    if (!q) { clearSearch(); return; }
    let any = false;
    sec.querySelectorAll('.prov-card').forEach((el) => {
      const hit = el.textContent.toLowerCase().includes(q);
      el.classList.toggle('settings-hidden', !hit);
      if (!hit) return;
      any = true;
      // filterSettings hid the rows that do not match; inside a card that is
      // matched as a whole, show them all so the body is not half empty.
      el.querySelectorAll('.settings-hidden').forEach((r) => r.classList.remove('settings-hidden'));
      if (!el.classList.contains('is-open')) { el.dataset.searchOpen = '1'; setOpen(el, true); }
    });
    if (!any) return;
    // A vendor match must surface its section even if no plain row matched.
    sec.classList.remove('settings-hidden');
    const pane = sec.closest('.settings-detail-pane');
    if (pane) pane.classList.remove('settings-hidden');
    const noRes = document.getElementById('settings-no-results');
    if (noRes) noRes.classList.add('settings-hidden');
  }

  // Bubble-phase listener on document runs after the input's own inline
  // oninput="filterSettings(...)", so the rows are already filtered.
  document.addEventListener('input', (e) => {
    if (e.target && e.target.id === 'settings-search') applySearch(e.target.value);
  });
  new MutationObserver((muts) => {
    for (const m of muts) {
      if (m.target && m.target.id === 'settings-body' && m.target.dataset.view !== 'search') { clearSearch(); return; }
    }
  }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-view'], subtree: true });

  window.ProviderCollapse = { card, toggle, isOpen, needsAttention, applySearch, clearSearch };
  window.providerCardToggle = toggle; // inline onclick in card()
})();

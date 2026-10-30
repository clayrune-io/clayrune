// C3: inline draft projection through Where's shared card renderer.
import './desk-v1-post-preview-skins.js';
import './desk-v1-post-preview.js';
(function () {
  const esc = (s) => window.esc(String(s || ''));
  let dispose = null;

  function mount(root, camp, draft) {
    if (dispose) dispose();
    const sheet = root.querySelector('.desk-v1-composer-sheet');
    const layout = document.createElement('div');
    layout.className = 'desk-v1-composer-layout';
    sheet.before(layout); layout.append(sheet);
    const panel = document.createElement('aside');
    panel.className = 'desk-v1-composer-preview';
    panel.setAttribute('aria-label', 'Post preview');
    panel.innerHTML = `<h2>Preview</h2><div data-composer-preview-tabs role="tablist" aria-label="Preview account"></div>
      <div id="desk-composer-preview-stage" data-composer-preview-stage role="tabpanel"></div>
      <p class="desk-v1-composer-hint">Feed layout and See more cut are approximate.</p>`;
    layout.append(panel);
    const toggle = document.createElement('button');
    toggle.type = 'button'; toggle.className = 'btn-secondary desk-v1-composer-preview-toggle';
    toggle.textContent = 'Show preview';
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-controls', 'desk-composer-preview-stage');
    layout.before(toggle);
    let active = null, signature = '';
    const tabs = panel.querySelector('[data-composer-preview-tabs]');
    const stage = panel.querySelector('[data-composer-preview-stage]');
    function measure() {
      stage.querySelectorAll('[data-pv-cut]').forEach((line) => {
        const copy = line.previousElementSibling;
        line.hidden = !(copy.clientHeight && copy.scrollHeight > copy.clientHeight + 1);
      });
    }
    function paint() {
      const accounts = window.DeskV1ComposerChannels.accounts(camp).filter((c) => draft.selected.has(c.id));
      if (!accounts.some((c) => c.id === active)) active = accounts[0]?.id || null;
      const ch = accounts.find((c) => c.id === active);
      const fam = { ...(draft.fam || {}), body: draft.text, title: draft.fam?.title || 'New post' };
      const key = JSON.stringify([accounts, active, fam.body, fam.title, fam.assets]);
      if (key !== signature) {
        signature = key;
        tabs.innerHTML = accounts.map((c, i) => `<button type="button" role="tab" id="desk-composer-preview-tab-${i}" data-composer-preview-tab="${esc(c.id)}" aria-controls="desk-composer-preview-stage" aria-selected="${c.id === active}" tabindex="${c.id === active ? 0 : -1}">${esc(c.label || c.identity || c.platform)}</button>`).join('');
        const n = [...draft.text].length;
        // The main editor owns the current copy; saved version bodies must not
        // mask unsaved typing. No store mutation or persistence belongs here.
        stage.innerHTML = ch ? `${window.DeskV1PostPreview.render(fam, null, ch, { cutLine: true, literal: true })}
          ${ch.platform === 'x' ? `<p class="desk-v1-composer-preview-length" ${n > 280 ? 'data-failed role="status"' : ''}>X ${n}/280${n > 280 ? ` · ${n - 280} over limit` : ''}</p>` : ''}`
          : '<p class="desk-v1-composer-hint">Tick a campaign account to preview this post.</p>';
        const selected = tabs.querySelector('[aria-selected="true"]');
        if (selected) stage.setAttribute('aria-labelledby', selected.id);
        else stage.removeAttribute('aria-labelledby');
      }
      measure();
    }
    toggle.onclick = () => {
      const show = toggle.getAttribute('aria-expanded') !== 'true';
      toggle.setAttribute('aria-expanded', String(show));
      toggle.textContent = show ? 'Hide preview' : 'Show preview';
      layout.classList.toggle('is-preview-open', show); paint();
    };
    tabs.onclick = (e) => {
      const b = e.target.closest('[data-composer-preview-tab]');
      if (!b) return;
      active = b.dataset.composerPreviewTab; paint();
      [...tabs.children].find((t) => t.dataset.composerPreviewTab === active)?.focus({ preventScroll: true });
    };
    tabs.onkeydown = (e) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
      const buttons = [...tabs.children], i = buttons.indexOf(document.activeElement);
      if (i < 0) return;
      e.preventDefault();
      buttons[e.key === 'Home' ? 0 : e.key === 'End' ? buttons.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length].click();
    };
    root.addEventListener('input', paint);
    root.addEventListener('click', (e) => { if (e.target.closest('[data-composer-channel]')) paint(); });
    // C1 status/count changes and sibling media mounts refresh the projection.
    // Observe only this detached-on-navigation root; preview paints are ignored.
    const observer = new MutationObserver((records) => {
      if (records.some((r) => !panel.contains(r.target))) paint();
    });
    observer.observe(root, { childList: true, subtree: true, characterData: true });
    const resize = new ResizeObserver(() => {
      if (!root.isConnected) { observer.disconnect(); resize.disconnect(); }
      else measure();
    });
    resize.observe(stage);
    dispose = () => { observer.disconnect(); resize.disconnect(); };
    paint();
  }
  window.DeskV1ComposerPreview = { mount };
})();

// Activity-page drafts for the one wizard. This unit owns addresses and their
// editor; the reading section owns coverage, method choice and the final save.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  const clone = value => JSON.parse(JSON.stringify(value));
  function create(saved) {
    let original = clone(saved), pages = clone(saved), input = '', invalid = '';
    const words = () => window.DeskV1ConnectCopy.words;
    function html(needed) {
      if (!needed && !pages.length) return '';
      const w = words();
      return `<div class="desk-v1-readpages" data-reading-pages>
        <span class="desk-v1-how-field-label">${w.activityPages}</span><p class="desk-v1-cfw-fact-text">${w.activityHelp}</p>
        <ul class="desk-v1-readpages-list">${pages.map((p,i) => `<li><span>${esc(p.url)}</span><button type="button" class="desk-v1-conn-btn" data-reading-remove="${i}">${w.remove}</button></li>`).join('')}</ul>
        <div class="desk-v1-readpages-add"><input type="url" class="desk-v1-rules-textinput" data-reading-address value="${esc(input)}" maxlength="2048" placeholder="https://" aria-label="${w.address}"><button type="button" class="desk-v1-conn-btn" data-reading-add>${w.addAddress}</button></div>
        <div role="alert" data-reading-invalid>${esc(invalid)}</div></div>`;
    }
    function bind(root, repaint, sync) {
      const field = root.querySelector('[data-reading-address]');
      field?.addEventListener('input', e => { input = e.target.value; invalid = ''; root.querySelector('[data-reading-invalid]').textContent = ''; sync(); });
      const add = () => {
        const value = input.trim(); if (!value) return;
        try { const url = new URL(value); if (url.protocol !== 'https:') throw new Error(words().httpsRequired); }
        catch (_) { invalid = words().httpsRequired; root.querySelector('[data-reading-invalid]').textContent = invalid; sync(); return; }
        pages.push({ role:'activity', url:value }); input = ''; invalid = ''; repaint();
      };
      root.querySelector('[data-reading-add]')?.addEventListener('click', add);
      field?.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); add(); } });
      root.querySelectorAll('[data-reading-remove]').forEach(b => b.addEventListener('click', () => { pages.splice(Number(b.dataset.readingRemove),1); repaint(); }));
    }
    return { html, bind, list:() => clone(pages), changed:() => JSON.stringify(pages) !== JSON.stringify(original),
      problem:() => invalid || (input.trim() ? words().addressPending : ''),
      clearInput:() => { input = ''; invalid = ''; }, accept:() => { original = clone(pages); },
      summary:() => pages.map(p => `${words().activityPages}: ${p.url}`) };
  }
  window.DeskV1ReadPages = { create };
})();

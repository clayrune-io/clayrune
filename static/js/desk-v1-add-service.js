// MC-1062/14: one entrance; account and engine setup use the same wizard.
(function () {
  function reset() { window.DeskV1ConnectFlow?.reset(); }
  function panelHTML(ctx) { return '<div class="desk-v1-add" data-add-service>' + (window.DeskV1Store.live() ? window.DeskV1ConnectFlow.html({ live: true, engines: ctx.engines }) : `<p>${window.DeskV1ConnectCopy.words.preview}</p>`) + '</div>'; }
  function bind(el, ctx) {
    const root = el.querySelector('[data-add-service]'); if (!root) return;
    window.DeskV1VaultGate?.attach(root);
    window.DeskV1ConnectFlow.bind(root, { ...ctx, live: window.DeskV1Store.live(), onSaved: () => {
      reset(); window.DeskV1ConnTiles.select(null);
      Promise.all([window.DeskV1Services.load(true), window.DeskV1ConnectionStatus?.refresh()]).then(() => { ctx.onEnginesChanged?.(); ctx.repaint(); }).catch(e => { console.warn('[connect] refresh failed: ' + e); ctx.repaint(); });
    } });
  }
  window.DeskV1AddService = { reset, panelHTML, bind, pickedEngine: () => null };
})();

// The editor shares the piece's rename control; standalone titles use board PUT.
(function () {
  function html(fam) { return `<div data-sb-title>${window.DeskV1PieceTitle.html(fam)}</div>`; }
  function bind(root, ctx, owner, paint, register) {
    const options = { mounted: () => document.querySelector(`[data-storyboard][data-family-id="${CSS.escape(ctx.fam.id)}"] [data-sb-title]`), ...(owner.kind === 'studio' ? {
      current: () => ctx.fam,
      changed: () => {
        register();
        if (typeof window.deskV1PatchParams === 'function') window.deskV1PatchParams({ itemId: ctx.fam.id });
      },
      request: title => window.DeskV1Store.storyboard.save(owner, ctx.detail, { title }),
    } : {}) };
    window.DeskV1PieceTitle.bind(root.querySelector('[data-sb-title]'), ctx.fam, paint, options);
  }
  window.DeskV1StudioTitle = { html, bind };
})();

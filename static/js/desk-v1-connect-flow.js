// MC-1062/14: compatibility bridge; the old wizard has no entrypoint.
(function () {
  const wizard = () => window.DeskV1ConnectWizard;
  window.DeskV1ConnectFlow = {
    active: () => !!wizard()?.active(),
    reset: () => wizard()?.close(),
    html: ctx => wizard()?.html(ctx) || '',
    bind: (el, ctx) => wizard()?.bind(el, ctx),
  };
})();

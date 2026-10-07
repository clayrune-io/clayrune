// Recovery is free and deliberate; this module never calls the paid Render route.
export function submissionHeld(render) {
  return !!render && render.status === 'held'
    && ['submit_interrupted', 'submit_uncertain'].includes((render.hold || {}).kind);
}

export function renderNeedsPolling(render) {
  if (!render) return false;
  if (submissionHeld(render) || (render.failure || {}).kind === 'submission_acknowledged') {
    return (render.scenes || []).some(scene => !['ready', 'failed'].includes(scene.status));
  }
  return !['ready', 'held', 'failed'].includes(render.status);
}

export function recoveryActionHTML(render, busy) {
  if (!submissionHeld(render)) return '';
  return `<button type="button" class="btn-secondary" style="max-width:100%;white-space:normal" data-eng-acknowledge${busy ? ' disabled' : ''}>${busy
    ? 'Acknowledging…' : 'I checked the engine dashboard, allow a new render'}</button>`;
}

export function bindRenderRecovery(host, { state, api, alive, paint, estimate, poll }) {
  const button = host.querySelector('[data-eng-acknowledge]');
  if (!button) return;
  button.onclick = async () => {
    if (state.acknowledging || !submissionHeld(state.render)) return;
    const id = state.render.render_id;
    state.acknowledging = true;
    state.renderReadSeq = (state.renderReadSeq || 0) + 1;
    state.error = null;
    paint();
    try {
      const out = await api('POST', `/api/desk/engines/renders/${encodeURIComponent(id)}/acknowledge`, {});
      if (!alive() || !state.render || state.render.render_id !== id) return;
      state.render = out.render;
      estimate(); // Refresh the remaining budget before enabling another Render.
    } catch (error) {
      if (alive()) state.error = error;
    } finally {
      state.acknowledging = false;
      if (alive()) { paint(); poll(); }
    }
  };
}

// Desk v1 (MC-977) — shared kit STUB. Built out in T0b (docs/desk_v1_r0_plan.md):
// the §9 vocabulary as constants + a copy-lint helper, the state-label
// (glyph + word) and channel-badge renderers, the toast + Undo command bus,
// the `Add to… ▾` menu, the ⓘ popover, and the Posy box. T0a only creates the
// file and reserves its window surface so index.html never needs a later edit
// (ground rule 2) and other T0a files can reference `window.DeskV1Kit` safely
// before it has real content.
(function () {
  window.DeskV1Kit = window.DeskV1Kit || {
    _stub: true,
    // Placeholder so a stub surface can render SOMETHING readable before T0b
    // lands the real glyph+word vocabulary (§9). Not the real implementation —
    // callers must not depend on this shape past T0a.
    stateLabel(state) { return state || 'Unknown'; },
  };
})();

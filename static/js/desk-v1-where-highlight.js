// Where board: when a piece sends you here ("Put this on a channel ›"), its
// message card is marked and focused so the next step (Enter opens the "add a
// version to an account" menu) is right where the eye lands. The board repaints
// itself (catalogue loads, drops), so the mark is remembered and re-applied by
// every paint until the card is used or the board is opened for something else.
(function () {
  let pending = null;

  // Called when the board is opened: remembers which piece, if any, to mark.
  function set(params) { pending = (params && params.highlightFamilyId) || null; }

  // Called at the end of every board paint.
  function mark(el) {
    if (!pending) return;
    const card = el.querySelector(`[data-where-message][data-family-id="${CSS.escape(pending)}"]`);
    if (!card) return;
    card.classList.add('desk-v1-where-highlight');
    card.addEventListener('click', () => { pending = null; card.classList.remove('desk-v1-where-highlight'); }, { once: true });
    const a = document.activeElement;
    if (!a || a === document.body) {
      card.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      card.focus({ preventScroll: true });
    }
  }
  window.DeskV1WhereHighlight = { set, mark };
})();

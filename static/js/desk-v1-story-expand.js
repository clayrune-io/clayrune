// Lift the mounted story panel itself: save, count and generation keep one owner.
(function () {
  // The Desk's document-level Escape shortcut otherwise closes the whole window.
  document.addEventListener('keydown', e => {
    const dialog = document.querySelector('[data-story-dialog][open]');
    if (e.key !== 'Escape' || !dialog) return;
    e.preventDefault();
    e.stopPropagation();
    dialog.close();
  }, true);
  function attach(panel) {
    if (!panel || panel.querySelector('[data-story-expand]')) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn-secondary desk-v1-story-expand';
    button.dataset.storyExpand = '';
    button.textContent = 'Expand';
    button.setAttribute('aria-haspopup', 'dialog');
    panel.querySelector('.desk-v1-story-head').append(button);
    button.addEventListener('click', () => {
      const shell = panel.closest('.desk-v1-shell');
      if (!shell) return;
      const anchor = document.createComment('story card');
      panel.before(anchor);
      const dialog = document.createElement('dialog');
      dialog.className = 'desk-v1-story-dialog';
      dialog.dataset.storyDialog = '';
      dialog.setAttribute('aria-label', 'Edit the story');
      const close = document.createElement('button');
      close.type = 'button';
      close.className = 'btn-secondary desk-v1-story-close';
      close.dataset.storyClose = '';
      close.textContent = 'Close';
      close.addEventListener('click', () => dialog.close());
      dialog.append(close, panel);
      shell.append(dialog);
      dialog.addEventListener('close', () => {
        if (anchor.isConnected) anchor.replaceWith(panel);
        dialog.remove();
        if (button.isConnected) button.focus({ preventScroll: true });
      }, { once: true });
      // Native modal behavior supplies focus containment and inert background.
      dialog.showModal();
      panel.querySelector('[data-sb-story]').focus({ preventScroll: true });
    });
  }
  window.DeskV1StoryExpand = { attach };
})();

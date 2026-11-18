// What the chat does when a send did not go through (FE14, and CV2 in
// docs/_review/2026-11-16_04_frontend.md). sendFollowup reaches these through
// window.mcSendRefusal, same as send-timeout.js / send-intent.js; top-level
// names here are module-private.

// Put the text the user typed back in the composer. A plain `inp.value = ...`
// fires no `input` event, and the modal rebuild (index.html refreshModalById)
// treats textareaValues as the source of truth for the kept composer node:
// an id with no entry is wiped to ''. sendFollowup deleted the entry before the
// POST, so write it again here (textareaValues is the SPA's global cache).
function restoreComposer(inp, message) {
  if (!inp) return;
  inp.value = message;
  if (inp.id) textareaValues[inp.id] = message;
  if (inp.id && window.syncComposerAction) window.syncComposerAction(inp.id);
}

window.mcSendRefusal = { restoreComposer };

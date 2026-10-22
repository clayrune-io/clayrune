// Native text selection belongs to the reader until they clear it. Keep
// destructive refreshes queued by owner; ordinary SSE appends can continue.
const CHAT = '.agent-output, .conv-preview-body';
const pending = new Map();
let dragging = null;

export function chatSelectionActive(root) {
  if (!root) return false;
  if (dragging?.isConnected && (root === dragging || root.contains(dragging) || dragging.contains(root))) return true;
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed) return false;
  for (let i = 0; i < selection.rangeCount; i++) {
    const range = selection.getRangeAt(i);
    for (const chat of root.matches(CHAT) ? [root] : root.querySelectorAll(CHAT)) {
      if (range.intersectsNode(chat)) return true;
    }
  }
  return false;
}

export function deferChatRefresh(root, key, refresh) {
  if (!chatSelectionActive(root)) { pending.delete(key); return false; }
  pending.set(key, { root, refresh });
  return true;
}

// Background refreshes retain the current view. An explicit session/tab
// change must still navigate even if the old view has highlighted text.
function deferChatModalRefresh(root, key, refresh, sessionIds, previewId) {
  const chats = [...root.querySelectorAll(CHAT)].filter(chat =>
    chat.classList.contains('agent-output')
      ? sessionIds.some(sid => sid && chat.id === `agent-output-${sid}`)
      : previewId && chat.dataset.csid === previewId);
  if (!chats.some(chatSelectionActive)) { pending.delete(key); return false; }
  pending.set(key, { root, refresh });
  return true;
}

function flush() {
  for (const [key, item] of pending) {
    if (!item.root.isConnected) { pending.delete(key); continue; }
    if (chatSelectionActive(item.root)) continue;
    pending.delete(key);
    item.refresh();
  }
}

document.addEventListener('mousedown', event => {
  dragging = event.button === 0 ? event.target.closest?.(CHAT) : null;
}, true);
document.addEventListener('mouseup', () => { dragging = null; requestAnimationFrame(flush); }, true);
window.addEventListener('blur', () => { dragging = null; requestAnimationFrame(flush); });
document.addEventListener('mousemove', event => {
  if (dragging && event.buttons === 0) { dragging = null; requestAnimationFrame(flush); }
}, true);
document.addEventListener('selectionchange', () => requestAnimationFrame(flush));

// The main modal refresh lives in index.html's classic script.
window.deferChatRefresh = deferChatRefresh;
window.deferChatModalRefresh = deferChatModalRefresh;

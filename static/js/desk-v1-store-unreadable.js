// Desk v1: the one plain notice for an unreadable Desk store (MC-1187 follow-up F2).
// The server answers 503 {"error":"desk_store_unreadable","message","path"} on every Desk
// route when desk.json could not be read (mc/blueprints/desk_store_unreadable_routes.py).
// DeskV1Store.api hands each failed response body to take(): a match shows ONE notice
// (a second failure does not stack another) and returns the server's own plain message,
// so the toast and the error line say that instead of the machine word. Anything else
// returns null and the caller carries on as before. No recovery action is offered here:
// restoring or starting empty is a person's job outside the product.
const ERROR = 'desk_store_unreadable';
const NOTICE_ID = 'desk-v1-store-unreadable-notice';

function _show(message, path) {
  if (!document || !document.body) return;
  let el = document.getElementById(NOTICE_ID);
  if (!el) {
    el = document.createElement('div');
    el.id = NOTICE_ID;
    el.setAttribute('role', 'alert');
    el.style.cssText = 'position:fixed;left:50%;top:12px;transform:translateX(-50%);z-index:100000;max-width:min(560px,92vw);'
      + 'padding:10px 14px;border-radius:8px;background:#fff4e5;color:#5a2d00;border:1px solid #d9822b;font:14px/1.4 system-ui,sans-serif;'
      + 'box-shadow:0 2px 8px rgba(0,0,0,.2)';
    const text = document.createElement('span');
    text.setAttribute('data-store-unreadable-text', '');
    const close = document.createElement('button');
    close.type = 'button';
    close.textContent = 'Dismiss';
    close.style.cssText = 'margin-left:12px;cursor:pointer';
    close.addEventListener('click', () => el.remove());
    el.append(text, close);
    document.body.appendChild(el);
  }
  el.querySelector('[data-store-unreadable-text]').textContent = path ? message + ' (' + path + ')' : message;
}

function take(body) {
  if (!body || typeof body !== 'object' || body.error !== ERROR) return null;
  const message = typeof body.message === 'string' && body.message ? body.message : 'The Desk could not read its saved data.';
  _show(message, typeof body.path === 'string' ? body.path : '');
  return message;
}

window.DeskV1StoreUnreadable = { take, ERROR };

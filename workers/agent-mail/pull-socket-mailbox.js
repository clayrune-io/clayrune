// Cloudflare-only wrapper. Keep cloudflare:workers outside worker.js so its
// Node-importable factory and retained module tests need no runtime replacement.
import { PullMailbox } from './pull-mailbox.js';
import { pullSocketConfig } from './pull-socket-config.js';
import { SOCKET_INSTALL_HEADER, SOCKET_SOURCE_HEADER } from './pull-socket-route.js';

export class PullSocketMailbox extends PullMailbox {
  constructor(state, env) {
    super(state, pullSocketConfig(env));
    this.namespace = env.MAIL_PULL_MAILBOX;
  }

  // Only the bound namespace can reach this fetch handler. The public router
  // overwrites these fields; strip them before repeating original wire bounds.
  async fetch(request) {
    const install_id = request.headers.get(SOCKET_INSTALL_HEADER);
    const trustedSource = request.headers.get(SOCKET_SOURCE_HEADER);
    const original = new Headers(request.headers);
    original.delete(SOCKET_INSTALL_HEADER); original.delete(SOCKET_SOURCE_HEADER);
    return this.admit(new Request(request, { headers: original }), { install_id, trustedSource });
  }

  async admit(request, context = {}) {
    // Bind even the first admission to the actual DO id, before consuming any
    // ticket. A misrouted internal call must not bind a foreign install here.
    if (!/^[a-f0-9]{32}$/.test(context.install_id ?? '') || !this.namespace
      || !this.state.id.equals(this.namespace.idFromName(context.install_id))) {
      return Response.json({ v: 1, code: 'ticket_invalid' }, { status: 401,
        headers: { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' } });
    }
    return super.admit(request, context);
  }
}

// Unit 14 dispatches SOCKET_PATH here. Routing discovery is never admission.
import { SessionAuth, SOCKET_PATH, PULL_PROTOCOL } from './session-auth.js';
import { bytes, sha256 } from './signing.js';

const valid = (ok, code = 'ticket_invalid') => { if (!ok) throw new Error(code); };
const headers = { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' };
// These headers exist only across the namespace fetch hop. Always overwritten
// after discovery, never accepted as routing input from the external request.
export const SOCKET_INSTALL_HEADER = 'x-clayrune-socket-install';
export const SOCKET_SOURCE_HEADER = 'x-clayrune-socket-source';
const codes = new Map([['ticket_invalid',401], ['version_unsupported',426], ['invalid_request',400],
  ['not_found',404], ['body_limit',413], ['abuse_limit',429], ['service_unavailable',503], ['storage_unavailable',503]]);

/** Only for a Cloudflare edge-delivered request. CF overwrites CF-Connecting-IP;
 * X-Forwarded-For/X-Real-IP/custom install/source headers are never authorities.
 * Same-zone Worker subrequests may alter the connecting IP via X-Real-IP, so all
 * CF-Worker requests share a conservative bucket rather than choosing an IP.
 * https://developers.cloudflare.com/fundamentals/reference/http-headers/
 */
export function socketTrustedSource(request) {
  if (request.headers.has('cf-worker')) return 'cloudflare-worker';
  const value = request.headers.get('cf-connecting-ip');
  valid(typeof value === 'string' && value.length <= 45 && /^[a-fA-F0-9:.]+$/.test(value), 'service_unavailable');
  let normalized;
  if (value.includes(':')) {
    try { normalized = new URL(`https://[${value}]`).hostname; }
    catch { throw new Error('service_unavailable'); } // IP syntax only.
  } else {
    valid(value.split('.').length === 4 && value.split('.').every((p) =>
      /^(0|[1-9][0-9]{0,2})$/.test(p) && Number(p) <= 255), 'service_unavailable');
    normalized = value;
  }
  return `edge:${normalized}`;
}

/** Bounded READ-ONLY lookup: one primary-key query, no budgets, ticket consume,
 * epoch bump, auth cache or fallback object. The selected DO rechecks everything
 * in SessionAuth.admitSocket, including scope/fence/state and atomic consumption.
 */
export async function resolveSocketInstall(request, config) {
  valid(request.url.length <= 8192, 'body_limit');
  const auth = new SessionAuth(config);
  auth.bounds(request, SOCKET_PATH);
  valid(request.method === 'GET' && !request.body && !request.headers.has('origin'));
  valid(request.headers.get('upgrade')?.toLowerCase() === 'websocket'
    && request.headers.get('sec-websocket-protocol') === PULL_PROTOCOL, 'version_unsupported');
  const value = request.headers.get('authorization');
  valid(typeof value === 'string' && /^MailTicket [a-f0-9]{64}$/.test(value));
  const digest = await sha256(bytes(value.slice(11)));
  const now = auth.now();
  const row = await config.db.prepare(`SELECT install_id,expires_ms FROM pull_tickets
    WHERE ticket_hash=? AND state='issued' AND created_ms<=? AND expires_ms>? LIMIT 1`)
    .bind(digest, now, now).first();
  valid(row && /^[a-f0-9]{32}$/.test(row.install_id) && row.expires_ms > auth.now());
  return row.install_id;
}

export class PullSocketRoute {
  constructor(config, mailboxes) { this.config = config; this.mailboxes = mailboxes; }

  async handle(request) {
    let source;
    try {
      source = socketTrustedSource(request);
      valid(this.mailboxes?.idFromName && this.mailboxes?.get, 'service_unavailable');
      const install_id = await resolveSocketInstall(request, this.config);
      const stub = this.mailboxes.get(this.mailboxes.idFromName(install_id));
      // RPC cannot serialize a WebSocket Response. Namespace fetch carries the
      // upgrade; overwrite both internal headers so callers cannot select them.
      const internal = new Headers(request.headers);
      internal.set(SOCKET_INSTALL_HEADER, install_id);
      internal.set(SOCKET_SOURCE_HEADER, source);
      return await stub.fetch(new Request(request, { headers: internal }));
    } catch (error) {
      let code = codes.has(error?.message) ? error.message : 'storage_unavailable';
      if (!codes.has(error?.message)) console.error('[agent-mail] socket_route_failed');
      // Invalid tickets have no DO. Account those attempts here; successful
      // routes are accounted exactly once by the DO's existing admission path.
      if (source && !['service_unavailable','storage_unavailable'].includes(code)) {
        try {
          const auth = new SessionAuth(this.config);
          await auth.sourceLimit(source, auth.now());
          if (!await auth.ledger.limit('http:unauthenticated', 10000, auth.now(), 60000)) code = 'abuse_limit';
        } catch (e) {
          if (e?.message === 'abuse_limit') code = 'abuse_limit';
          else { console.error('[agent-mail] socket_route_refusal_accounting_failed'); code = 'storage_unavailable'; }
        }
      }
      const status = codes.get(code);
      return Response.json({ v: 1, code }, { status, headers: { ...headers,
        ...([429,503].includes(status) ? { 'retry-after': '60' } : {}) } });
    }
  }
}

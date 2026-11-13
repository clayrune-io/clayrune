// Shared pull verification links only. Unit 14 wires this ahead of the three
// overlapping HTTP registries; /mail/revert remains with OwnerChangeHTTP.
import { bytes, sha256 } from './signing.js';

export const LINK_PATHS = Object.freeze(['/mail/link', '/mail/link/confirm']);
const COOKIE = '__Secure-clayrune-mail';
const HEX = /^[a-f0-9]{64}$/;
const headers = { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store',
  'x-content-type-options': 'nosniff', 'referrer-policy': 'no-referrer', 'x-frame-options': 'DENY',
  'content-security-policy': "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'" };
const refusal = (code = 'not_found', status = 404) => new Response(
  `<!doctype html><html lang="en"><meta charset="utf-8"><title>Clayrune mail</title><p>${code}</p></html>`,
  { status, headers });

/** Purpose discovery is NOT authorization. One indexed read, no body reads,
 * mutations, token consumption or budget charges. Pass the ORIGINAL request
 * and context to exactly one handler; it owns body/CSRF/scope validation and
 * accounting. A concurrent state change is rechecked there, never retried here.
 * Construct with the same reviewed DB/origin/clock configuration as the handlers.
 */
export class LinkRouter {
  constructor({ signup, management, ownerChange }) {
    const handlers = [signup, management, ownerChange];
    if (!signup?.db || typeof signup.now !== 'function' || !signup.serviceOrigin
      || handlers.some((h) => !h || typeof h.handle !== 'function'
        || h.db !== signup.db || h.serviceOrigin !== signup.serviceOrigin)) throw new Error('invalid_request');
    this.signup = signup;
    this.handlers = new Map([['signup', signup], ['stepup', management], ['new_owner', ownerChange]]);
  }

  async handle(request, context) {
    let handler;
    try {
      // Bound routing inputs before parsing/hashing; uploads stay with handlers.
      if (request.url.length > 8192 || request.url.includes('#') || request.bodyUsed
        || request.body?.locked) return refusal();
      const url = new URL(request.url);
      if (url.origin !== this.signup.serviceOrigin || !LINK_PATHS.includes(url.pathname)
        || request.headers.has('content-encoding')
        || [...request.headers].reduce((n, [k, v]) => n + bytes(k).length + bytes(v).length + 4, 0) > 8192) return refusal();
      let sql; let key;
      if (url.pathname === '/mail/link') {
        if (request.method !== 'GET' || request.body || !/^\?token=[a-f0-9]{64}$/.test(url.search)) return refusal();
        key = url.search.slice(7);
        sql = `SELECT purpose, expires_ms FROM pull_tokens
          WHERE token_hash=? AND state='pending' AND expires_ms>? LIMIT 1`;
      } else {
        const length = request.headers.get('content-length');
        if (request.method !== 'POST' || request.url.includes('?') || !request.body
          || request.headers.get('origin') !== this.signup.serviceOrigin
          || !/^application\/x-www-form-urlencoded(?:;\s*charset=utf-8)?$/i.test(request.headers.get('content-type') ?? '')
          || length !== null && (!/^(0|[1-9][0-9]*)$/.test(length) || Number(length) > 4096)) return refusal();
        const cookies = (request.headers.get('cookie') ?? '').split(';').map((s) => s.trim().split('='))
          .filter(([name]) => name === COOKIE);
        if (cookies.length !== 1 || cookies[0].length !== 2 || !HEX.test(cookies[0][1])) return refusal();
        key = cookies[0][1];
        // The body token and CSRF are checked by the selected handler. Joining
        // via the cookie avoids teeing, buffering or consuming the upload twice.
        sql = `SELECT t.purpose, t.expires_ms, s.expires_ms AS session_expires_ms
          FROM pull_browser_sessions s JOIN pull_tokens t ON t.token_hash=s.token_hash
          AND t.install_id=s.install_id AND t.flow_id=s.flow_id
          AND t.generation=s.generation AND t.owner_generation=s.owner_generation
          WHERE s.session_hash=? AND t.state='pending' AND t.expires_ms>?
          AND s.purpose='confirmation' AND s.state='csrf' AND s.created_ms<=?
          AND s.expires_ms>? AND s.expires_ms-s.created_ms BETWEEN 1 AND 600000 LIMIT 1`;
      }
      const digest = await sha256(bytes(key)); // Same exported helper as all three handlers.
      const now = this.signup.now();
      const args = url.pathname === '/mail/link' ? [digest, now] : [digest, now, now, now];
      const row = await this.signup.db.prepare(sql).bind(...args).first();
      const checkedAt = this.signup.now();
      if (!row || row.expires_ms <= checkedAt
        || row.session_expires_ms !== undefined && row.session_expires_ms <= checkedAt) return refusal();
      handler = this.handlers.get(row.purpose);
      if (!handler) return refusal();
    } catch {
      // Storage/clock/crypto failure is operational, never a purpose oracle.
      // Do not log exception text: a DB error may include tokens or SQL inputs.
      console.error('[agent-mail] link_router_lookup_failed');
      return refusal('storage_unavailable', 503);
    }
    // Outside the lookup catch: no fallback handler or second accounting pass.
    return handler.handle(request, context);
  }
}

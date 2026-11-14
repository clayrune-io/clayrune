// MC-1166 unit09: one hibernating socket coordinator per install, never a queue.
import { DurableObject } from 'cloudflare:workers';
import { SessionAuth, PULL_PROTOCOL } from './session-auth.js';
import { Ledger } from './ledger.js';
import { offerFrameBytes } from './pull-ledger.js';
import { randomId } from './signing.js';
import { SQL_NOW } from './sql-clock.js';
import { clientFrame, serviceFrame, valid, integer } from './pull-frame.js';

const WINDOW = 524288;
const AUTH_ERRORS = new Set(['stale_generation','registration_inactive','registration_frozen','fenced','revoked','unauthorized']);
const OP_ERRORS = new Set(['ack_conflict','lease_unknown','not_found','unknown_thread','event_expired',
  'operation_conflict','queue_full','storage_unavailable','service_unavailable','frame_limit']);
const HEADERS = { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' };

/** Unit14 supplies reviewed config {db,serviceOrigin,serviceKeyId,servicePrivateKey,
 * serviceKeyExpiresMs,clock?}. serviceKeyExpiresMs is the earlier of reviewed
 * manifest/pin expiry and signing-key retirement, never a client-selected time.
 * The DO wrapper passes this config through its constructor and routes upgrades to admit(request,
 * {trustedSource,install_id}); both arguments are router-resolved, never trusted
 * client forwarding headers. No public fetch/wake HTTP route is defined here.
 *
 * Exported wake entry point: await namespace.get(namespace.idFromName(install_id))
 * .wake(install_id). Internal DO RPC for ingress10/sweep13, AFTER enqueue commit.
 * Returns {connected:boolean}; false/lost wake never constitutes delivery.
 * Wake sends a signed empty hint; the install owns resume ids and page scans.
 * Notifications/sweep retain retries; a hint never grants custody.
 */
export class PullMailbox extends DurableObject {
  constructor(state, config) {
    super(state, config); this.state = state;
    valid(integer(config.serviceKeyExpiresMs, 1), 'service_unavailable');
    this.config = { ...config, clock: config.clock ?? Date.now };
    this.auth = new SessionAuth(this.config);
    this.pull = new Ledger(config.db).pull({ clock: this.config.clock });
    this.queue = this.pull.queue;
  }
  exclusive(fn) { return this.state.blockConcurrencyWhile(fn); }
  sockets() { return this.state.getWebSockets().filter((ws) => ws.readyState === 1); }
  async current(s) {
    valid(await this.pull.registration(s.install_id, s.generation, s.owner_generation), 'stale_generation');
    await this.queue.authorize(s);
    await this.queue.atomic(s, []);
    valid(this.config.clock() < s.reauth_ms, 'reauth_due');
    valid(this.config.clock() < this.config.serviceKeyExpiresMs, 'service_key_changed');
  }
  async send(ws, s, type, payload) {
    const wire = await serviceFrame(this.config, s, type, payload);
    // Signing awaits crypto. Recheck authority after it, immediately before send.
    await this.current(s); await this.meta(ws, s);
    if (type === 'event') valid(await this.queue.first(`SELECT 1 FROM pull_events e JOIN pull_installs i USING(install_id)
      WHERE e.event_id=? AND e.generation=? AND e.owner_generation=? AND e.state='offered' AND e.expires_ms>${SQL_NOW}
      AND i.generation=e.generation AND i.owner_generation=e.owner_generation AND i.epoch=? AND i.connection_id=?`,
    payload.event.event_id, s.generation, s.owner_generation, s.epoch, s.connection_id), 'event_expired');
    ws.send(wire); return wire.length;
  }
  async close(ws, code, reason) {
    if (ws.readyState !== 3) ws.close(code, reason);
    const s = ws.deserializeAttachment();
    if (s) await this.state.storage.delete(`socket:${s.connection_id}`);
  }
  async refuse(ws, s, error, request = randomId()) {
    let code = error?.message;
    const close = code === 'connection_superseded' ? 4001 : AUTH_ERRORS.has(code) ? 4003
      : ['reauth_due','service_key_changed'].includes(code) ? 4004 : code === 'frame_limit' ? 1009
      : ['invalid_request','invalid_json','invalid_hold','unsupported_frame','request_limit'].includes(code) ? 4002 : null;
    if (!close && !OP_ERRORS.has(code)) {
      console.error('[agent-mail] pull_mailbox_operation_failed'); code = 'storage_unavailable';
    }
    // A superseded/retired scope cannot issue apparently current service evidence.
    // Protocol errors with current authority receive one bounded signed refusal.
    if (![4001,4003,4004].includes(close)) {
      try { await this.send(ws, s, 'error', { request_id: request, code,
        retry_after_ms: ['storage_unavailable','service_unavailable','queue_full','request_limit'].includes(code)
          ? this.config.clock() + 60000 : null }); }
      catch {
        console.error('[agent-mail] pull_mailbox_refusal_send_failed');
        await this.close(ws, 1013, 'storage_unavailable'); return;
      }
    }
    if (close) await this.close(ws, close, code);
  }
  async deadlines() {
    const ends = [];
    for (const ws of this.sockets()) {
      const s = ws.deserializeAttachment(); const m = await this.state.storage.get(`socket:${s.connection_id}`);
      if (!m) { await this.close(ws, 1013, 'storage_unavailable'); continue; }
      const end = Math.min(s.reauth_ms, this.config.serviceKeyExpiresMs);
      const deadline = m?.reauth_notified ? end : Math.max(this.config.clock(), end - 10000);
      ends.push(m?.resumed ? deadline : Math.min(deadline, s.authenticated_ms + 10000));
    }
    if (ends.length) await this.state.storage.setAlarm(Math.min(...ends));
    else await this.state.storage.deleteAlarm();
  }
  async admit(request, { trustedSource, install_id } = {}) {
    return this.exclusive(async () => {
      let server;
      try {
        valid(typeof install_id === 'string' && /^[a-f0-9]{32}$/.test(install_id), 'ticket_invalid');
        const bound = await this.state.storage.get('install_id');
        valid(!bound || bound === install_id, 'ticket_invalid');
        valid(this.config.clock() < this.config.serviceKeyExpiresMs, 'service_unavailable');
        const s = await this.auth.admitSocket(request, { trustedSource, install_id });
        await this.state.storage.put('install_id', install_id);
        // The admission transaction already fenced every previous epoch.
        for (const ws of this.sockets()) await this.close(ws, 4001, 'connection_superseded');
        const [client, accepted] = Object.values(new WebSocketPair()); server = accepted;
        this.state.acceptWebSocket(server); server.serializeAttachment(s);
        await this.state.storage.put(`socket:${s.connection_id}`, { resumed: false, after_seq: 0, limit: 10,
          available: 0, outstanding: {}, service_key_id: this.config.serviceKeyId, reauth_notified: false,
          minute: Math.floor(this.config.clock() / 60000), frames: 0, resumes: 0 });
        await this.deadlines();
        await this.send(server, s, 'hello', { connection_id: s.connection_id, authenticated_ms: s.authenticated_ms,
          reauth_ms: s.reauth_ms, max_events: 10, max_bytes: WINDOW });
        await this.deadlines();
        return new Response(null, { status: 101, headers: { ...HEADERS, 'sec-websocket-protocol': PULL_PROTOCOL }, webSocket: client });
      } catch (error) {
        console.error('[agent-mail] pull_mailbox_admission_refused');
        if (server) {
          try { await this.close(server, 1013, 'storage_unavailable'); await this.deadlines(); }
          catch { console.error('[agent-mail] pull_mailbox_admission_cleanup_failed'); }
        }
        const code = ['ticket_invalid','version_unsupported','abuse_limit','invalid_request','not_found','body_limit'].includes(error?.message)
          ? error.message : error?.message === 'service_unavailable' ? 'service_unavailable' : 'storage_unavailable';
        const status = { ticket_invalid: 401, version_unsupported: 426, abuse_limit: 429,
          invalid_request: 400, not_found: 404, body_limit: 413, storage_unavailable: 503, service_unavailable: 503 }[code];
        return Response.json({ v: 1, code }, { status, headers: { ...HEADERS,
          ...([429,503].includes(status) ? { 'retry-after': '60' } : {}) } });
      }
    });
  }
  async meta(ws, s) {
    const m = await this.state.storage.get(`socket:${s.connection_id}`);
    valid(m && typeof m.resumed === 'boolean' && typeof m.reauth_notified === 'boolean'
      && integer(m.after_seq) && integer(m.limit, 1, 10) && integer(m.available, 0, WINDOW)
      && integer(m.minute) && integer(m.frames, 0, 120) && integer(m.resumes, 0, 60)
      && m.outstanding && !Array.isArray(m.outstanding) && Object.keys(m.outstanding).length <= 10
      && Object.entries(m.outstanding).every(([id, n]) => /^[a-f0-9]{64}$/.test(id) && integer(n, 1, 160 * 1024))
      && Object.values(m.outstanding).reduce((n, v) => n + v, 0) <= WINDOW, 'storage_unavailable');
    valid(m.service_key_id === this.config.serviceKeyId, 'service_key_changed'); return m;
  }
  async drain(ws, s, m, request_id) {
    if (!m.resumed || m.reauth_notified) return;
    await this.current(s);
    // Expiry/terminal disposition frees socket accounting; lease timeout alone
    // does not let an unacknowledged client accumulate an unbounded window.
    for (const id of Object.keys(m.outstanding)) {
      const row = await this.queue.first(`SELECT 1 FROM pull_events WHERE event_id=? AND install_id=?
        AND state IN ('pending','offered','held') AND expires_ms>${SQL_NOW}`, id, s.install_id);
      if (!row) { m.available = Math.min(WINDOW, m.available + m.outstanding[id]); delete m.outstanding[id]; }
    }
    const page = await this.queue.page(s, { after_seq: m.after_seq, limit: m.limit });
    let cursor = m.after_seq; let stopped = false;
    for (const item of page.events) {
      const e = item.event;
      // Scheduling hint only: the ledger repeats this rule atomically in offer.
      // Avoid treating a known earlier unsettled thread head as a store failure.
      const earlier = await this.queue.first(`SELECT 1 FROM pull_events WHERE install_id=? AND generation=?
        AND owner_generation=? AND thread_id=? AND seq<? AND state IN ('pending','offered','held') AND expires_ms>${SQL_NOW}`,
      s.install_id, s.generation, s.owner_generation, e.thread_id, e.seq);
      if (earlier) { cursor = e.seq; continue; }
      const priorSize = m.outstanding[e.event_id] ?? 0;
      if (priorSize && item.lease_until_ms > this.config.clock()) { cursor = e.seq; continue; }
      const used = Object.values(m.outstanding).reduce((n, v) => n + v, 0);
      const size = offerFrameBytes(e).encoded;
      if ((!priorSize && Object.keys(m.outstanding).length >= 10) || used - priorSize + size > WINDOW
        || size > m.available + priorSize) { stopped = true; break; }
      if (item.next_attempt_ms > this.config.clock() || item.lease_until_ms > this.config.clock()) { cursor = e.seq; continue; }
      try {
        const offer = await this.queue.offer(s, e.event_id,
          { available_bytes: Math.min(m.available + priorSize, WINDOW - used + priorSize) });
        // Persist reservation before network handoff. Lost send/instance never
        // grants custody or causes unlimited replay in this connection.
        m.outstanding[e.event_id] = size; m.available += priorSize - size;
        await this.state.storage.put(`socket:${s.connection_id}`, m);
        await this.send(ws, s, 'event', offer);
      } catch (error) {
        if (!['operation_conflict','event_expired','queue_full'].includes(error?.message)) throw error;
        if (error.message === 'queue_full') { stopped = true; break; }
        // Earlier unsettled event in this thread: preserve it for the next pass.
      }
      cursor = e.seq;
    }
    m.after_seq = stopped ? cursor : page.next_seq;
    await this.state.storage.put(`socket:${s.connection_id}`, m);
    await this.send(ws, s, 'status', { request_id, next_seq: m.after_seq,
      scan_complete: !stopped && page.scan_complete, pending_count: page.pending_count, oldest_expires_ms: page.oldest_expires_ms });
  }
  async webSocketMessage(ws, message) {
    return this.exclusive(async () => {
      const s = ws.deserializeAttachment(); let f;
      try {
        await this.current(s); const m = await this.meta(ws, s);
        valid(m.resumed || this.config.clock() < s.authenticated_ms + 10000, 'invalid_request');
        f = clientFrame(message);
        valid(f.install_id === s.install_id && f.generation === s.generation && f.owner_generation === s.owner_generation, 'stale_generation');
        valid(f.epoch === s.epoch, 'connection_superseded');
        const minute = Math.floor(this.config.clock() / 60000);
        if (minute !== m.minute) { m.minute = minute; m.frames = 0; m.resumes = 0; }
        valid(++m.frames <= 120 && (f.type !== 'resume' || ++m.resumes <= 60), 'request_limit');
        await this.state.storage.put(`socket:${s.connection_id}`, m);
        if (f.type === 'resume') {
          m.resumed = true; m.after_seq = f.payload.after_seq; m.limit = f.payload.limit; m.available = f.payload.available_bytes;
          await this.state.storage.put(`socket:${s.connection_id}`, m);
          await this.deadlines(); await this.drain(ws, s, m, f.request_id);
        } else if (f.type === 'custody_ack') {
          const r = await this.queue.ack(s, f.payload);
          const released = m.outstanding[f.payload.event_id] ?? 0; delete m.outstanding[f.payload.event_id];
          m.available = Math.min(WINDOW, m.available + released);
          await this.state.storage.put(`socket:${s.connection_id}`, m);
          await this.send(ws, s, 'ack_committed', { request_id: f.request_id, event_id: r.event_id,
            event_digest: r.event_digest, body_digest: r.body_digest, local_receipt_id: r.local_receipt_id,
            receipt_id: r.receipt_id, committed_ms: r.committed_ms });
          await this.drain(ws, s, m, f.request_id);
        } else {
          const r = await this.queue.hold(s, f.payload);
          const released = m.outstanding[f.payload.event_id] ?? 0; delete m.outstanding[f.payload.event_id];
          m.available = Math.min(WINDOW, m.available + released);
          await this.state.storage.put(`socket:${s.connection_id}`, m);
          await this.send(ws, s, 'hold', { request_id: f.request_id, ...r });
          await this.drain(ws, s, m, f.request_id);
        }
      } catch (error) { await this.refuse(ws, s, error, f?.request_id); }
      await this.deadlines();
    });
  }
  async wake(install_id) {
    return this.exclusive(async () => {
      const bound = await this.state.storage.get('install_id');
      if (!bound) return { connected: false };
      valid(install_id === bound, 'invalid_request');
      let connected = false;
      for (const ws of this.sockets()) {
        const s = ws.deserializeAttachment();
        try {
          await this.current(s); const m = await this.meta(ws, s); connected = true;
          if (!m.reauth_notified) await this.send(ws, s, 'wake', {});
        } catch (error) { await this.refuse(ws, s, error); }
      }
      await this.deadlines(); return { connected };
    });
  }
  async alarm() {
    return this.exclusive(async () => {
      for (const ws of this.sockets()) {
        const s = ws.deserializeAttachment();
        try {
          const m = await this.meta(ws, s);
          const end = Math.min(s.reauth_ms, this.config.serviceKeyExpiresMs);
          const reason = this.config.serviceKeyExpiresMs <= s.reauth_ms ? 'service_key_changed' : 'reauth_due';
          if (this.config.clock() >= end) {
            // reauth_required must be signed before the deadline; a delayed
            // alarm closes directly rather than emitting expired evidence.
            await this.close(ws, 4004, reason);
          } else if (!m.resumed && this.config.clock() >= s.authenticated_ms + 10000) await this.refuse(ws, s, new Error('invalid_request'));
          else if (!m.reauth_notified && this.config.clock() >= end - 10000) {
            await this.send(ws, s, 'reauth_required', { reason, close_at_ms: end });
            m.reauth_notified = true; await this.state.storage.put(`socket:${s.connection_id}`, m);
          }
        } catch (error) { await this.refuse(ws, s, error); }
      }
      await this.deadlines();
    });
  }
  async webSocketClose(ws, code) {
    return this.exclusive(async () => { await this.close(ws, code === 1005 ? 1000 : code, 'connection_lost'); await this.deadlines(); });
  }
  async webSocketError(ws) {
    console.error('[agent-mail] pull_mailbox_socket_failed');
    return this.exclusive(async () => { await this.close(ws, 1013, 'connection_lost'); await this.deadlines(); });
  }
}

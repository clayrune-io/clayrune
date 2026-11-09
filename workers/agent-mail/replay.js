// D1 owns retries across Worker lifetimes. A new signature/nonce wraps the same
// event and bytes. Only an event+digest-bound durable custody receipt deletes text.
import { bytes, sign } from './signing.js';
import { readBounded } from './ingress.js';
export function provenRoute(reg) {
  if (reg.route_verified !== 1 || reg.route_generation !== reg.generation || !reg.route_origin
      || !/^\/[A-Za-z0-9/_-]{1,200}$/.test(reg.route_path ?? '')) return null;
  try {
    const url = new URL(reg.route_origin);
    if (url.protocol !== 'https:' || url.username || url.password || url.port || url.pathname !== '/'
        || url.search || url.hash || url.origin !== reg.route_origin || !url.hostname.includes('.')
        || /^[\d.]+$/.test(url.hostname) || url.hostname.endsWith('.localhost')
        || url.hostname.endsWith('.local') || url.hostname.endsWith('.internal')) return null;
    return { url: reg.route_origin + reg.route_path, path: reg.route_path };
  } catch { return null; }
}
export async function replay({ ledger, config, fetcher, now, install = null, clock = () => now }) {
  await ledger.reconcile(now);
  let delivered = 0;
  for (const { event_id } of await ledger.due(now, install)) {
    now = clock();
    const event = await ledger.claim(event_id, now);
    if (!event) continue;
    const reg = await ledger.registration(event.install_id);
    if (!reg?.active || !reg.owner_verified || reg.generation !== event.generation) {
      await ledger.reconcile(now); continue;
    }
    const route = provenRoute(reg);
    if (!route) { await ledger.settle(event, false, now, 'route_pending'); continue; }
    const body = bytes(event.payload);
    const headers = await sign({ privateKey: config.privateKey, key_id: config.keyId,
      direction: 'service-to-install', install_id: reg.install_id, generation: reg.generation,
      path: route.path, body, now });
    let custody = false;
    try {
      const response = await fetcher(route.url, { method: 'POST', headers, body, redirect: 'manual',
        signal: AbortSignal.timeout(10_000) });
      if (response.status === 200) {
        // Read bounded: an HTML dashboard response or a generic 2xx is no receipt.
        const raw = await readBounded(response.body, 2048);
        const receipt = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(raw));
        custody = receipt.custody === 'durable' && receipt.event_id === event.event_id
          && receipt.body_digest === event.digest && receipt.install_id === reg.install_id
          && receipt.generation === reg.generation;
      }
    } catch {
      // Network/invalid receipt: D1 retains custody and the retry reason is visible.
      custody = false;
    }
    const settled = await ledger.settle(event, custody, clock());
    if (custody && settled.meta.changes === 1) delivered++;
  }
  return { delivered };
}

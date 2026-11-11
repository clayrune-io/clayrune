// Account-scoped Email Routing API. Only fixed endpoints, bounded streams, no redirects.
import { readRequestBody } from './request-body.js';
const ROOT = 'https://api.cloudflare.com/client/v4/accounts/';
const MAX_PAGES = 10;
const fail = () => new Error('destination_unavailable');

export function cloudflareDestinations({ accountId, token, fetcher = fetch, timeoutMs = 5000 }) {
  if (typeof accountId !== 'string' || !/^[a-f0-9]{32}$/.test(accountId)
      || typeof token !== 'string' || !token) throw new Error('provider_config_required');
  const endpoint = `${ROOT}${accountId}/email/routing/addresses`;
  async function request(url, method, email) {
    try {
      const response = await fetcher(url, { method, redirect: 'manual', signal: AbortSignal.timeout(timeoutMs),
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        ...(email === undefined ? {} : { body: JSON.stringify({ email }) }) });
      if (response.status !== 200 || response.type === 'opaqueredirect') throw fail();
      const raw = await readRequestBody(response.body, { timeoutMs, limit: 131072 });
      const value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(raw));
      if (value?.success !== true) throw fail();
      return value;
    } catch { throw fail(); } // Never disclose a token, provider body or address in an exception.
  }
  function destination(row) {
    if (!row || typeof row.email !== 'string' || row.email.length > 254
        || !(row.verified === null || (typeof row.verified === 'string' && Number.isFinite(Date.parse(row.verified))))) throw fail();
    return { address: row.email, verified: typeof row.verified === 'string' };
  }
  return {
    async list() {
      const rows = []; let pages;
      for (let page = 1; page <= MAX_PAGES; page += 1) {
        const value = await request(`${endpoint}?page=${page}&per_page=50`, 'GET');
        const info = value.result_info;
        if (!Array.isArray(value.result) || !info || info.page !== page || info.per_page !== 50
            || !Number.isSafeInteger(info.total_count) || info.total_count < 0
            || !Number.isSafeInteger(info.total_pages) || info.total_pages < 0 || info.total_pages > MAX_PAGES
            || (pages !== undefined && pages !== info.total_pages)) throw fail();
        if (info.total_pages === 0) {
          if (page !== 1 || value.result.length !== 0 || info.total_count !== 0) throw fail();
          return [];
        }
        pages = info.total_pages;
        rows.push(...value.result.map(destination));
        if (page === pages) {
          if (rows.length !== info.total_count || new Set(rows.map((r) => r.address)).size !== rows.length) throw fail();
          return rows;
        }
      }
      throw fail();
    },
    async create(address) {
      const value = await request(endpoint, 'POST', address);
      const result = destination(value.result);
      if (result.address !== address) throw fail();
      return result;
    },
  };
}

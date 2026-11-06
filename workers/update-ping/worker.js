// Clayrune update-ping receiver (Cloudflare Worker + D1). NOT DEPLOYED: written
// to sit next to the client in mc/update_ping.py. See README.md in this folder.
//
// Accepts exactly what the client sends: {install_id, version, os}. Stores
// install_id, first_seen, last_seen, version, os. Replies {latest_version}.
// It does not read or store the caller's IP address or headers.

const ID_RE = /^[0-9a-f]{32}$/;
const VERSION_RE = /^[0-9A-Za-z][0-9A-Za-z._+\- ()]{0,63}$/;
const OS_VALUES = new Set(['windows', 'darwin', 'linux', 'other']);
const MAX_BODY_BYTES = 1024;

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
  });
}

export function validate(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return null;
  const keys = Object.keys(body).sort().join(',');
  if (keys !== 'install_id,os,version') return null;     // exactly three fields, nothing else
  const { install_id, version, os } = body;
  if (typeof install_id !== 'string' || !ID_RE.test(install_id)) return null;
  if (typeof version !== 'string' || (version !== '' && !VERSION_RE.test(version))) return null;
  if (typeof os !== 'string' || !OS_VALUES.has(os)) return null;
  return { install_id, version, os };
}

export default {
  async fetch(request, env) {
    if (request.method !== 'POST') return json({ error: 'method not allowed' }, 405);
    const raw = await request.text();
    if (raw.length > MAX_BODY_BYTES) return json({ error: 'too large' }, 413);
    let parsed;
    try { parsed = JSON.parse(raw); } catch { return json({ error: 'bad json' }, 400); }
    const row = validate(parsed);
    if (!row) return json({ error: 'bad payload' }, 400);

    const now = Math.floor(Date.now() / 1000);
    await env.DB.prepare(
      `INSERT INTO installs (install_id, first_seen, last_seen, version, os)
       VALUES (?1, ?2, ?2, ?3, ?4)
       ON CONFLICT(install_id) DO UPDATE SET
         last_seen = excluded.last_seen, version = excluded.version, os = excluded.os`,
    ).bind(row.install_id, now, row.version, row.os).run();

    return json({ latest_version: env.LATEST_VERSION || '' });
  },
};

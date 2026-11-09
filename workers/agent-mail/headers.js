// Header-list helpers shared by the inbound checks.
//
// The inbound checks take the message headers as an ORDERED LIST, never a
// Headers object: a Headers object folds duplicate fields into one comma-joined
// string and loses order, and both matter here (a sender can add a second
// Authentication-Results or From field, and Cloudflare's own result is trusted
// by position). postal-mime's `email.headers` has exactly this shape:
// [{ key: 'from', value: '...' }, ...] in wire order.

const MAX_HEADERS = 400;
const MAX_VALUE_LEN = 8192;

/** -> [{ key: lowercased name, value: string }] or null if the input is unusable. */
export function normalizeHeaders(headers) {
  if (!Array.isArray(headers) || headers.length > MAX_HEADERS) return null;
  const out = [];
  for (const h of headers) {
    let key;
    let value;
    if (Array.isArray(h)) [key, value] = h;
    else if (h && typeof h === 'object') ({ key, value } = h);
    if (typeof key !== 'string' || typeof value !== 'string') return null;
    if (value.length > MAX_VALUE_LEN) value = value.slice(0, MAX_VALUE_LEN);
    out.push({ key: key.toLowerCase(), value });
  }
  return out;
}

export function allOf(headers, key) {
  return headers.filter((h) => h.key === key);
}

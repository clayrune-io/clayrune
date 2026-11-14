// Display name for outbound agent mail From / Reply-To. The client sends only the
// agent's name (mc/agent_mail_display_name.py); this module re-validates it and
// adds the fixed " via Clayrune" suffix. Header-injection surface: closed allowlist,
// and ANY failure yields the fallback — a bad name never fails a send.
export const FALLBACK_NAME = 'Clayrune agent';
export const MAX_NAME = 40;
const SUFFIX = ' via Clayrune';
const CONTROL = /[\x00-\x1f\x7f-\x9f\u2028\u2029]/;
const ALLOWED = /^[A-Za-z0-9 ._'-]+$/;

/** The sanitized agent name, or FALLBACK_NAME. Never throws. */
export function sanitizeName(raw) {
  if (typeof raw !== 'string' || CONTROL.test(raw)) return FALLBACK_NAME;
  const name = raw.replace(/ +/g, ' ').trim();
  if (!name || name.length > MAX_NAME || !ALLOWED.test(name)) return FALLBACK_NAME;
  return name;
}

/** Full label, e.g. "Dave via Clayrune", for the structured {name,email} form.
 * The fallback is already generic, so it gets no suffix ("Clayrune agent"). */
export function displayLabel(raw) {
  const name = sanitizeName(raw);
  return name === FALLBACK_NAME ? name : `${name}${SUFFIX}`;
}

/** RFC 5322 quoted-string form for raw headers: "Dave via Clayrune". */
export function quotedLabel(raw) {
  return `"${displayLabel(raw).replace(/[\\"]/g, '\\$&')}"`;
}

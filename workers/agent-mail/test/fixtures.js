// Synthetic fixtures only. Addresses use reserved example domains; the token is
// a made-up 26-char string. Nothing here touches a network or a real mailbox.

export const NOW = Date.UTC(2026, 10, 8, 12, 0, 0);
export const OWNER_ADDR = 'owner@example.com';
export const TOKEN = 'abcdefghijklmnopqrstuvwxyz'.slice(0, 26);
export const RCPT = `inst1a2b+${TOKEN}@mail.example.net`;
export const OUTBOUND_ID = 'out-1@mail.example.net';

export const OWNER = Object.freeze({
  active: true, install_id: 'inst-1', generation: 3, owner_address: OWNER_ADDR,
});

export function thread(over = {}) {
  return {
    thread_id: 't-1', install_id: 'inst-1', owner_generation: 3,
    expires_at: NOW + 86_400_000, revoked: false, message_ids: [`<${OUTBOUND_ID}>`],
    ...over,
  };
}

export const lookupOk = (t = thread()) => async (token) => (token === TOKEN ? t : null);

// Cloudflare's own result, as a header value.
export function cfAr({ spf = 'pass', dkim = 'pass', dmarc = 'pass', from = 'example.com',
  mailfrom = 'owner@example.com', d = 'example.com' } = {}) {
  return `mx.cloudflare.net; spf=${spf} smtp.mailfrom=${mailfrom}; `
    + `dkim=${dkim} header.d=${d}; dmarc=${dmarc} header.from=${from}`;
}

// Ordered header list, newest-prepended first, like a real inbound message.
export function headers({ from = OWNER_ADDR, ar = cfAr(), extra = [], inReplyTo = `<${OUTBOUND_ID}>` } = {}) {
  const h = [];
  if (ar !== null) h.push({ key: 'authentication-results', value: ar });
  h.push(...extra);
  if (from !== null) h.push({ key: 'from', value: from });
  h.push({ key: 'to', value: RCPT });
  h.push({ key: 'subject', value: 'Re: anything' });
  if (inReplyTo) h.push({ key: 'in-reply-to', value: inReplyTo });
  return h;
}

export function meta(over = {}) {
  return { rcptTo: RCPT, headers: headers(), now: NOW, ...over };
}

// ---- raw MIME builders --------------------------------------------------

export function mime(headerLines, body) {
  return [...headerLines, '', body].join('\r\n');
}

export function multipart(kind, parts, boundary = 'BND') {
  const body = parts
    .map((p) => `--${boundary}\r\n${p.headers.join('\r\n')}\r\n\r\n${p.body}\r\n`)
    .join('') + `--${boundary}--\r\n`;
  return mime(
    ['From: owner@example.com', 'To: x@mail.example.net', 'Subject: Re: x', 'MIME-Version: 1.0',
      `Content-Type: multipart/${kind}; boundary=${boundary}`],
    body,
  );
}

export const plainPart = (text) => ({ headers: ['Content-Type: text/plain; charset=utf-8'], body: text });
export const htmlPart = (html) => ({ headers: ['Content-Type: text/html; charset=utf-8'], body: html });

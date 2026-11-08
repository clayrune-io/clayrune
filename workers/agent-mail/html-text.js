// HTML -> plain text, used ONLY as a fallback when a message has no text/plain
// part (strip.js decides that). Best effort and deliberately lossy: everything
// that is not visible running text is dropped, and the output is still treated
// as untrusted data downstream.
//
// Dropped without trace: script/style/head/title/svg/iframe/... contents,
// comments, CDATA, <img> (alt text included -- it can carry instructions),
// href/src values (no remote reference survives), and any element that marks
// itself hidden (hidden attribute, aria-hidden, display:none, visibility:hidden,
// zero/1px font, zero opacity, off-screen text-indent/left, mso-hide). An
// unclosed dropped element drops to the end of the document.
//
// Returns counts only for what was removed -- never the removed bytes.

const VOID = new Set([
  'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta',
  'param', 'source', 'track', 'wbr',
]);
const DROP = new Set([
  'script', 'style', 'head', 'title', 'noscript', 'template', 'svg', 'math',
  'iframe', 'object', 'applet', 'audio', 'video', 'canvas', 'frameset', 'frame',
]);
const BLOCK = new Set([
  'p', 'div', 'br', 'hr', 'li', 'ul', 'ol', 'tr', 'table', 'blockquote', 'pre',
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'article', 'header', 'footer',
  'address', 'dd', 'dt', 'dl', 'form', 'fieldset',
]);
const NAMED = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' };
const ATTR_RE = /([^\s"'<>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
const HIDDEN_STYLE = [
  /display:none/, /visibility:(?:hidden|collapse)/, /opacity:0(?:\.0*)?(?:;|$)/,
  /font-size:(?:0[a-z%]*|0?\.\d+(?:px|pt)|1(?:px|pt))(?:;|$)/, /mso-hide:all/,
  /max-height:0[a-z%]*(?:;|$)/, /max-width:0[a-z%]*(?:;|$)/,
  /text-indent:-\d{3,}/, /(?:left|top):-\d{3,}/,
];

function decodeEntities(s) {
  return s.replace(/&(?:#(\d{1,7})|#[xX]([0-9a-fA-F]{1,6})|([A-Za-z]{2,6}));/g, (m, dec, hex, name) => {
    if (name) return name.toLowerCase() in NAMED ? NAMED[name.toLowerCase()] : m;
    const cp = dec ? parseInt(dec, 10) : parseInt(hex, 16);
    if (cp === 0 || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) return '';
    return String.fromCodePoint(cp);
  });
}

function parseAttrs(src) {
  const attrs = {};
  for (const m of src.matchAll(ATTR_RE)) {
    const k = m[1].toLowerCase();
    if (!(k in attrs)) attrs[k] = m[2] ?? m[3] ?? m[4] ?? '';
  }
  return attrs;
}

function isHidden(attrs) {
  if ('hidden' in attrs) return true;
  if ((attrs['aria-hidden'] || '').toLowerCase() === 'true') return true;
  const style = (attrs.style || '').toLowerCase().replace(/\/\*.*?\*\//g, '').replace(/\s+/g, '');
  return style !== '' && HIDDEN_STYLE.some((re) => re.test(style));
}

/** html -> { text, hidden_dropped, remote_refs } */
export function htmlToText(html) {
  let out = '';
  let hiddenDropped = 0;
  let remoteRefs = 0;
  let skipTag = null;
  let skipDepth = 0;
  let i = 0;
  const n = html.length;

  const newline = () => { if (!out.endsWith('\n')) out += '\n'; };

  while (i < n) {
    const lt = html.indexOf('<', i);
    const textEnd = lt === -1 ? n : lt;
    if (textEnd > i && skipTag === null) {
      out += decodeEntities(html.slice(i, textEnd)).replace(/[ \t\r\n\f]+/g, ' ');
    }
    if (lt === -1) break;
    i = lt;

    if (html.startsWith('<!--', i)) {
      const end = html.indexOf('-->', i + 4);
      i = end === -1 ? n : end + 3;
      continue;
    }
    if (html.startsWith('<![CDATA[', i)) {
      const end = html.indexOf(']]>', i);
      i = end === -1 ? n : end + 3;
      continue;
    }
    if (html[i + 1] === '!' || html[i + 1] === '?') {
      const end = html.indexOf('>', i);
      i = end === -1 ? n : end + 1;
      continue;
    }
    const closing = html[i + 1] === '/';
    const nameMatch = /^[A-Za-z][A-Za-z0-9:-]*/.exec(html.slice(i + (closing ? 2 : 1), i + 40));
    if (!nameMatch) { // a literal '<'
      if (skipTag === null) out += '<';
      i += 1;
      continue;
    }
    const name = nameMatch[0].toLowerCase();
    // find the end of the tag, honouring quoted attribute values
    let j = i + 1 + (closing ? 1 : 0) + nameMatch[0].length;
    let q = null;
    for (; j < n; j += 1) {
      const ch = html[j];
      if (q) { if (ch === q) q = null; } else if (ch === '"' || ch === "'") q = ch; else if (ch === '>') break;
    }
    const attrSrc = html.slice(i + 1 + (closing ? 1 : 0) + nameMatch[0].length, j);
    i = j < n ? j + 1 : n;

    if (skipTag !== null) {
      if (name === skipTag && !VOID.has(name)) {
        if (closing) { skipDepth -= 1; if (skipDepth === 0) skipTag = null; } else skipDepth += 1;
      }
      continue;
    }
    if (closing) {
      if (BLOCK.has(name)) newline();
      continue;
    }
    const attrs = parseAttrs(attrSrc);
    if (name === 'img') { remoteRefs += 1; hiddenDropped += 1; continue; }
    if (VOID.has(name)) {
      if (name === 'br' || name === 'hr') newline();
      else if ('src' in attrs || 'href' in attrs) remoteRefs += 1;
      continue;
    }
    if ('background' in attrs || /url\(/i.test(attrs.style || '')) remoteRefs += 1;
    if (DROP.has(name) || isHidden(attrs)) {
      hiddenDropped += 1;
      skipTag = name;
      skipDepth = 1;
      continue;
    }
    if (BLOCK.has(name)) newline();
    else if (name === 'td' || name === 'th') out += ' ';
  }

  const text = out
    .replace(/[ \t]*\n[ \t]*/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .replace(/[ \t]{2,}/g, ' ')
    .trim();
  return { text, hidden_dropped: hiddenDropped, remote_refs: remoteRefs };
}

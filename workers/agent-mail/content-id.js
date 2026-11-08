// Normalise a Content-ID / `start` value to the bare msg-id used for matching a
// multipart/related root (RFC 2387 3.2, 3.4).
//
// Content-ID has the Message-ID syntax (RFC 2045 7): `[CFWS] "<" id-left "@"
// id-right ">" [CFWS]` (RFC 5322 3.6.4). CFWS -- whitespace and parenthesised,
// nestable comments -- sits OUTSIDE the angle brackets and carries no meaning, so
// `<root@example.net> (comment)` and `<root@example.net>` are the same ID. Inside the
// brackets a quoted-string local part may legitimately contain `(`, `)` and `>`, so
// nothing between the brackets is touched.
//
//   normalizeMsgId(value) -> string
//
// A value that is not a well-formed bracketed msg-id (no brackets, junk after the
// closing bracket, unterminated comment/quote) is returned trimmed with a whole-string
// pair of brackets removed, which is the literal comparison the caller used before.
// Pure, total, never throws.

// Index after the CFWS starting at i, or -1 for an unterminated comment.
function skipCfws(s, i) {
  for (;;) {
    while (i < s.length && /\s/.test(s[i])) i += 1;
    if (s[i] !== '(') return i;
    let depth = 0;
    for (; i < s.length; i += 1) {
      if (s[i] === '\\') i += 1;
      else if (s[i] === '(') depth += 1;
      else if (s[i] === ')' && --depth === 0) break;
    }
    if (i >= s.length) return -1;
    i += 1;
  }
}

// Index of the `>` closing the bracket opened at `open`, honouring quoted-strings,
// quoted-pairs and a bracketed domain literal on the right of `@` (RFC 5322 3.6.4
// no-fold-literal, whose dtext may contain `>`); -1 when there is none.
function closingBracket(s, open) {
  let quoted = false;
  let literal = false;
  for (let i = open + 1; i < s.length; i += 1) {
    const c = s[i];
    if (c === '\\') i += 1;
    else if (literal) { if (c === ']') literal = false; }
    else if (c === '"') quoted = !quoted;
    else if (quoted) continue;
    else if (c === '[') literal = true;
    else if (c === '>') return i;
  }
  return -1;
}

export function normalizeMsgId(value) {
  const s = String(value === undefined || value === null ? '' : value);
  const open = skipCfws(s, 0);
  if (open >= 0 && s[open] === '<') {
    const close = closingBracket(s, open);
    if (close > open && skipCfws(s, close + 1) === s.length) return s.slice(open + 1, close);
  }
  return s.trim().replace(/^<(.*)>$/s, '$1');
}

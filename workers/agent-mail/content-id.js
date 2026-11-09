// Strict msg-id reading for multipart/related root selection (RFC 2387 3.2, 3.4).
//
// MC-1166 unit 3 fix round 5 (Dave's call, logged DECIDED WITHOUT RON). Rounds 3-5
// each found one more spelling of a msg-id (comments, quoted local parts, domain
// literals, obsolete comment-in-local-part) where our reading and a mail reader's
// reading disagree, so two parts carrying the SAME id compared as different and the
// injected one was chosen as the root. Chasing the full RFC 5322 grammar is a
// lexer we would keep getting wrong. Instead the id itself must be plain:
//
//   [CFWS] "<" id ">" [CFWS]   or   [CFWS] id [CFWS]
//
// where id is atext/"."/"@" only, optionally ending in a simple bracketed literal
// of [A-Za-z0-9.:-]. No quotes, comments, backslashes, whitespace or angle brackets
// can appear inside the id, so the first ">" closes it and there is exactly one way
// to read it. CFWS outside the brackets is still skipped (it is common and carries
// no meaning). Anything else returns null: the caller then selects NO root, so a
// sender using exotic syntax loses the body rather than gaining a disguise.
//
//   strictMsgId(value) -> string | null
//
// Pure, total, never throws.

const ID = /^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.@-]+(?:\[[A-Za-z0-9.:-]*\])?/;

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

export function strictMsgId(value) {
  const s = String(value === undefined || value === null ? '' : value);
  const at = skipCfws(s, 0);
  if (at < 0) return null;
  const bracketed = s[at] === '<';
  const m = ID.exec(s.slice(bracketed ? at + 1 : at));
  if (!m) return null;
  let end = (bracketed ? at + 1 : at) + m[0].length;
  if (bracketed) {
    if (s[end] !== '>') return null;
    end += 1;
  }
  return skipCfws(s, end) === s.length ? m[0] : null;
}

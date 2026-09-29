// ── Rich text formatting for agent output ────────────────────────────────────

// RTL helpers (MC-1000). Hebrew + Arabic (+ presentation forms) ranges —
// deliberately NOT a full Unicode bidi-class table, just enough to answer
// "is this the first strong-directional character" for the two scripts this
// product actually needs to support.
const _RTL_CHAR_RE = /[֑-߿יִ-﷽ﹰ-ﻼ]/;
const _LATIN_CHAR_RE = /[A-Za-z]/;

// Round 1 (per-block dir="auto") handles direction for TEXT — the browser's
// own bidi algorithm reads the first strong character. But a card's layout
// (radio position, chip flex order, the actions row) has no text of its own
// to auto-detect from, so it needs the direction computed once, in JS, from
// the question text, and applied as a real `dir` attribute on the container.
function _firstStrongDir(text) {
  const s = String(text == null ? '' : text);
  for (const ch of s) {
    if (_RTL_CHAR_RE.test(ch)) return 'rtl';
    if (_LATIN_CHAR_RE.test(ch)) return 'ltr';
  }
  return 'ltr';
}

// Same first-strong-char scan as _firstStrongDir, but over already-built HTML
// and SKIPPING text inside any element carrying its own `dir` attribute —
// i.e. the same content a browser's own dir="auto" would skip. Used to GATE
// whether a line needs _isolateRtlRuns: gating on the raw line's first
// strong char (a plain _firstStrongDir(raw) call) is wrong whenever the line
// starts with something Latin that's ALSO going to be isolated in its own
// dir="ltr" span (a leading URL, "Q: "/"> Name: " prefix, etc) — that Latin
// prefix isn't actually the block's base direction once isolated, so gating
// on it wrongly treats a genuinely Hebrew-first line as English-quoting-
// Hebrew and bdi-wraps the Hebrew. A <bdi> is itself excluded from the
// outer dir="auto" scan (same rule as an explicit dir=), so with BOTH the
// prefix span and the bdi excluded, nothing strong is left and the whole
// line flips to ltr. Found live: "https://example.com/docs <Hebrew>" and
// "> Ron: <Hebrew>" both mis-detected as ltr this way. MC-1000 round 2.
function _baseDirIgnoringIsolated(html) {
  const s = String(html == null ? '' : html);
  const tokens = s.match(/<[^>]+>|&[#a-zA-Z0-9]+;|[\s\S]/g) || [];
  let protectedDepth = 0;
  for (const tok of tokens) {
    if (tok[0] === '<') {
      if (tok.startsWith('</')) protectedDepth = Math.max(0, protectedDepth - 1);
      else if (/\sdir=/.test(tok)) protectedDepth++;
      continue;
    }
    if (protectedDepth > 0) continue;
    if (tok.length === 1) {
      if (_RTL_CHAR_RE.test(tok)) return 'rtl';
      if (_LATIN_CHAR_RE.test(tok)) return 'ltr';
    }
  }
  return 'ltr';
}

// Mirrors round 1's LTR-isolation of code/path/URL spans inside an RTL line,
// but for the opposite case: an ENGLISH-base line (dir="auto" → ltr) that
// quotes a Hebrew/Arabic phrase. Without isolating the RTL run, neutrals at
// its boundary (quotes, commas, colons) resolve against the outer LTR
// paragraph instead of the phrase itself, e.g. `"Answered:" renders as
// "כן, נראה טובAnswered:"`. MC-1000 round 2.
//
// Operates on an HTML string via a tag/entity-aware token walk — never
// touches markup or entity codes, only bare-character text tokens — so it's
// safe to run after code/path/bold spans have already been injected. Any
// element that already carries its own `dir` attribute (hl-code, hl-path,
// hl-url, hl-file-link — all dir="ltr") is left alone: same "auto skips a
// descendant with its own dir" rule those spans exist to exploit.
function _isolateRtlRuns(html) {
  if (!html) return html;
  const tokens = html.match(/<[^>]+>|&[#a-zA-Z0-9]+;|[\s\S]/g) || [];
  const isRtlTok = (t) => t.length === 1 && _RTL_CHAR_RE.test(t);
  const isLatinTok = (t) => t.length === 1 && _LATIN_CHAR_RE.test(t);
  const isQuoteTok = (t) => t === '"' || t === '&quot;';

  let out = [];
  let protectedDepth = 0;
  let i = 0;
  while (i < tokens.length) {
    const tok = tokens[i];
    if (tok[0] === '<') {
      if (tok.startsWith('</')) protectedDepth = Math.max(0, protectedDepth - 1);
      else if (/\sdir=/.test(tok)) protectedDepth++;
      out.push(tok);
      i++;
      continue;
    }
    if (protectedDepth === 0 && isRtlTok(tok)) {
      // Extend to the LAST rtl char reachable without crossing a Latin
      // letter or a tag boundary — spaces/punctuation/digits/entities
      // between two rtl letters are included in the run.
      let j = i + 1, lastRtl = i;
      while (j < tokens.length) {
        const t2 = tokens[j];
        if (t2[0] === '<' || isLatinTok(t2)) break;
        if (isRtlTok(t2)) lastRtl = j;
        j++;
      }
      const prevOut = out[out.length - 1];
      const nextTok = tokens[lastRtl + 1];
      let wrapQuote = false;
      if (prevOut && isQuoteTok(prevOut) && nextTok && isQuoteTok(nextTok)) {
        out.pop();
        wrapQuote = true;
      }
      const run = tokens.slice(i, lastRtl + 1).join('');
      out.push('<bdi>' + (wrapQuote ? prevOut : '') + run + (wrapQuote ? nextTok : '') + '</bdi>');
      i = wrapQuote ? lastRtl + 2 : lastRtl + 1;
      continue;
    }
    out.push(tok);
    i++;
  }
  return out.join('');
}

function formatAgentText(raw) {
  // Already escaped by esc() before calling — we operate on safe HTML
  let t = esc(raw);

  // Code fence blocks (``` ... ```)
  if (t.match(/^```/)) {
    return `<span class="hl-codeblock">${t.replace(/^```\w*/, '').replace(/```$/, '')}</span>`;
  }

  // Markdown headers: ## Heading, ### Heading
  if (t.match(/^#{1,4}\s/)) {
    return `<span class="hl-h">${t}</span>`;
  }

  // Inline image embeds: an absolute path to an image file becomes a
  // thumbnail (click to enlarge). Tokenized out FIRST so the path / code
  // / bold regexes below don't shred the produced <img> markup; swapped
  // back in just before return. Only absolute paths (Win `X:\` / `X:/`
  // or POSIX `/`) — relative paths can't be resolved server-side without
  // the agent's cwd.
  const _imgTokens = [];
  // Two guards prevent URL-shaped strings from being matched as filesystem
  // paths:
  //   1. Negative lookbehind `(?<![\w:/%])` — leading drive-letter / slash
  //      must NOT be preceded by a word char, `:`, `/`, or `%`. Stops the
  //      regex from biting mid-URL (e.g. matching `p:/` inside `http://`).
  //   2. Negative lookahead `(?!:\/\/)` after the drive-letter colon — forbids
  //      `://` (URL scheme). Windows paths are `C:\` or `C:/single-slash`;
  //      a URL like `p://...png` would otherwise match as a phantom drive.
  // Trailing `(?![A-Za-z0-9])` keeps the extension bounded.
  t = t.replace(
    /(?<![\w:/%])((?:[A-Za-z]:(?!\/\/)[\\/]|\/)[^\s"'`<>|]+?\.(?:png|jpe?g|gif|webp|bmp|svg|ico|tiff?|avif))(?![A-Za-z0-9])/gi,
    (m, p) => {
      const rawPath = p.replace(/&amp;/g, '&').replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'");
      const src = API_BASE + '/api/serve-image?path=' + encodeURIComponent(rawPath);
      const tok = '@@CLImg' + _imgTokens.length + '@@';
      // NOTE: `loading="lazy"` MUST NOT be set here. The img starts at
      // `display:none` (CSS .agent-img) and only becomes `display:block`
      // when onload adds `.agent-img-ok`. With lazy loading, the browser
      // waits for the element to enter the viewport before starting the
      // request — but a display:none element has no bounding box and
      // never "intersects", so the load never starts, onload never fires,
      // and the img stays hidden forever. Eager loading (the default)
      // breaks the deadlock: load starts immediately, onload fires,
      // class flips, image appears.
      _imgTokens.push(
        `<span class="agent-img-wrap">` +
        `<a class="agent-img-path" href="${src}" target="_blank" rel="noopener">${p}</a>` +
        `<img class="agent-img" src="${src}" alt="" ` +
        `onload="this.classList.add('agent-img-ok')" ` +
        `onerror="this.closest('.agent-img-wrap').classList.add('agent-img-failed');this.remove()" ` +
        `onclick="_openImageViewer(this.src)"></span>`);
      return tok;
    });

  // File deep-links: the agent emits [file:<abs path>] or [file:<path>|Label]
  // and we render a clickable link to /api/serve-file (works locally AND over
  // the tunnel — plain HTTP, same as images). Tokenized FIRST, like images, so
  // the path / code regexes below don't shred the anchor. The text is already
  // esc()'d, so `[` `]` `:` `|` survive verbatim; the path fragment is HTML-
  // escaped — unescape it for the URL, keep the label's escaped form for display.
  const _fileTokens = [];
  t = t.replace(/\[file:([^\]|]+?)(?:\|([^\]]+))?\]/gi, (m, pathEsc, labelEsc) => {
    const unesc = (s) => (s || '').replace(/&amp;/g, '&').replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'");
    const path = unesc(pathEsc).trim();
    if (!path) return m;                                  // malformed → leave literal
    const href = API_BASE + '/api/serve-file?path=' + encodeURIComponent(path);
    const fname = path.split(/[\\/]/).pop() || path;
    const labelHtml = labelEsc ? labelEsc.trim() : esc(fname);  // labelEsc already escaped
    const tok = '@@CLFile' + _fileTokens.length + '@@';
    _fileTokens.push(
      `<a class="hl-file-link" dir="ltr" href="${href}" target="_blank" rel="noopener" ` +
      `title="Download ${pathEsc.trim()}"><span class="hl-file-ic">&#128196;</span>${labelHtml}</a>`);
    return tok;
  });

  // URLs → real clickable <a> links. Tokenized out BEFORE the path / code /
  // bold regexes below so they can't shred the markup. The absolute-path
  // regex in particular ("/seg/seg" ×2+) matches the "//host/path" tail of
  // an https:// URL and steals its second slash, leaving "https:/…" which the
  // URL pass can no longer recognise — which is why links *with a path*
  // silently stopped being linkified. Swapped back in just before return;
  // trailing sentence punctuation is kept outside the anchor.
  // dir="ltr" on the anchor (not just the CSS unicode-bidi rule) for the same
  // reason as hl-code above: "https" is a strong-LTR first character, so a
  // Hebrew line starting with a URL would otherwise mis-detect the WHOLE line
  // as LTR. Verified: CSS `direction:ltr` alone does NOT stop the dir="auto"
  // algorithm from reading it, only the HTML attribute does. MC-1000.
  const _urlTokens = [];
  t = t.replace(/(https?:\/\/[^\s<]+)/g, (m) => {
    let url = m, trail = '';
    const tm = url.match(/[.,;:!?)\]]+$/);
    if (tm) { trail = tm[0]; url = url.slice(0, -trail.length); }
    const tok = '@@CLUrl' + _urlTokens.length + '@@';
    _urlTokens.push('<a class="hl-url" dir="ltr" href="' + url + '" target="_blank" rel="noopener">' + url + '</a>');
    return tok + trail;
  });

  // Numbered list items: 1. item, 2. item
  t = t.replace(/^(\d+\.)\s/, '<span class="hl-num">$1</span> ');

  // Bullet points: - item, * item
  t = t.replace(/^([-*])\s/, '<span class="hl-bullet">$1</span> ');

  // Inline code: `something`
  // dir="ltr" (not just the CSS direction:ltr rule) matters here beyond
  // rendering: the HTML auto-direction algorithm that dir="auto" on the
  // parent .agent-line uses to detect Hebrew/Arabic vs English skips any
  // descendant that carries its own dir attribute. Without it, an English
  // identifier in the FIRST code span of an otherwise-Hebrew line would be
  // read as the line's first strong character and mis-detect the whole
  // block as LTR. Same reasoning for hl-path below. MC-1000.
  t = t.replace(/`([^`]+)`/g, '<span class="hl-code" dir="ltr">$1</span>');

  // Bold: **text**
  t = t.replace(/\*\*([^*]+)\*\*/g, '<span class="hl-bold">$1</span>');

  // File paths: word.ext patterns (common code file extensions)
  t = t.replace(/(?<![&\w])([A-Za-z_][\w.-]*\.(py|js|ts|tsx|jsx|html|css|json|md|yml|yaml|toml|rs|go|java|c|cpp|h|sh|sql|vue|svelte|rb|php))(?![&\w])/g,
    '<span class="hl-path" dir="ltr">$1</span>');

  // Absolute paths: /path/to/file or C:\path\to\file
  // The dash MUST stay escaped. Unescaped, `[\w.-\\]` reads `.-\` as a
  // character RANGE (0x2E-0x5C) covering `<`, `=`, `>`, `/` and `:` — so this
  // replace ran straight into the `<span>` the file-extension rule above had
  // just injected, producing `...memory\<span</span> class="hl-path">SKILL.md`
  // and printing raw markup at the user. Seen live on a Windows path ending
  // in SKILL.md.
  t = t.replace(/((?:\/[\w.-]+){2,}|(?:[A-Z]:\\[\w.\-\\]+))/g, '<span class="hl-path" dir="ltr">$1</span>');

  // Swap image + file + URL tokens back in (kept opaque through the regexes above).
  if (_imgTokens.length) {
    t = t.replace(/@@CLImg(\d+)@@/g, (_, i) => _imgTokens[+i] || '');
  }
  if (_fileTokens.length) {
    t = t.replace(/@@CLFile(\d+)@@/g, (_, i) => _fileTokens[+i] || '');
  }
  if (_urlTokens.length) {
    t = t.replace(/@@CLUrl(\d+)@@/g, (_, i) => _urlTokens[+i] || '');
  }

  // Isolate embedded Hebrew/Arabic runs (see _isolateRtlRuns above) — must
  // run after ALL token restoration above, so the gate below sees the real
  // dir="ltr" URL/path/file spans, not opaque @@CLUrl0@@-style placeholders
  // (whose own Latin letters would otherwise fool the gate the same way a
  // real leading URL does). MC-1000 round 2.
  //
  // ONLY when the line's own base direction (ignoring isolated content) is
  // ltr. A <bdi> establishes its own directionality unconditionally (per
  // spec, regardless of any dir attribute) — wrapping a Hebrew-FIRST line's
  // entire content in one would hide it from the parent's dir="auto" scan
  // the same way an explicit dir does, flipping the whole block back to ltr.
  // This fix is only for the opposite case: an English-base line that
  // quotes a Hebrew run.
  if (_baseDirIgnoringIsolated(t) === 'ltr') t = _isolateRtlRuns(t);

  return t;
}

function isTableLine(text) {
  // Pipe-delimited table row: | col1 | col2 | or starts/ends with pipe
  if (/^\s*\|.*\|/.test(text)) return true;
  // Separator lines: +---+---+, +===+===+, |---|---|
  if (/^\s*[+|][-=]+[+|]/.test(text)) return true;
  // Unicode box drawing: ┌─┬─┐, ├─┼─┤, └─┴─┘, │ etc
  if (/[┌┐└┘├┤┬┴┼─│═║╔╗╚╝╠╣╦╩╬]/.test(text)) return true;
  return false;
}

function formatTableLine(escaped) {
  // Colorize pipes and border characters (for box-drawing / non-pipe tables)
  return escaped
    .replace(/([│|])/g, '<span class="table-pipe">$1</span>')
    .replace(/([┌┐└┘├┤┬┴┼─═║╔╗╚╝╠╣╦╩╬+])/g, '<span class="table-border">$1</span>');
}

function isPipeTable(lines) {
  // True if at least one line has pipe-delimited columns (not just box-drawing)
  return lines.some(l => /^\s*\|[^┌┐└┘├┤┬┴┼─│═║╔╗╚╝╠╣╦╩╬]*\|/.test(l));
}

function isSeparatorLine(text) {
  return /^\s*\|?[-=|:+\s]+\|?\s*$/.test(text) && /[-=]{2,}/.test(text);
}

function buildPipeTable(rawLines) {
  // Parse pipe-delimited lines into HTML <table>
  const dataRows = [];
  let headerIdx = -1;
  for (let i = 0; i < rawLines.length; i++) {
    const line = rawLines[i].trim();
    if (!line) continue;
    if (isSeparatorLine(line)) {
      // The row before the first separator is the header
      if (headerIdx < 0 && dataRows.length > 0) headerIdx = dataRows.length - 1;
      continue;
    }
    // Split by pipe, trim each cell
    const cells = line.replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
    dataRows.push(cells);
  }
  if (dataRows.length === 0) return '';
  // If we found a header separator, first row is header
  const hasHeader = headerIdx === 0;
  let html = '<table>';
  dataRows.forEach((cells, i) => {
    const tag = (hasHeader && i === 0) ? 'th' : 'td';
    html += '<tr>' + cells.map(c => `<${tag}>${esc(c)}</${tag}>`).join('') + '</tr>';
  });
  html += '</table>';
  return html;
}

// Mirrors the server's _SLASH_COMMAND_RE (agent_routes.py) so a message the
// backend treats as a slash command is exactly the one we badge as such.
const SLASH_COMMAND_RE = /^\/[A-Za-z][\w-]*(:[\w-]+)?(\s|$)/;

// A user line arrives as "> <Label>: <message>". Strip the "> Label: " prefix
// and test what the user actually typed. The label is user-configurable
// (config.user_name), so match the first ": " rather than a fixed name — and
// fall back to the raw body when there's no label at all.
function isSlashCommandLine(text) {
  const t = (text || '').trim();
  if (!t.startsWith('> ')) return false;
  const body = t.slice(2);
  const sep = body.indexOf(': ');
  const msg = sep === -1 ? body : body.slice(sep + 2);
  return SLASH_COMMAND_RE.test(msg.trimStart());
}

// A Stop-hook block/resend boundary. The server emits '[stop-hook-redo]'
// (agent_runtime.is_stop_hook_feedback). Buffers rebuilt from a transcript
// BEFORE that fix instead hold the hook's feedback as a fake user prompt,
// "> Ron: Stop hook feedback:\n...", and those stay in a live session's memory
// until it is rebuilt again, so they are recognised here too.
function isStopHookRedoLine(text) {
  const t = (text || '').trim();
  if (t === '[stop-hook-redo]') return true;
  if (!t.startsWith('> ')) return false;
  const body = t.slice(2);
  const sep = body.indexOf(': ');
  return sep !== -1 && body.slice(sep + 2).startsWith('Stop hook feedback:');
}

function agentLineCls(text) {
  const t = text.trim();
  if (t.startsWith('> [queued]')) return 'agent-line agent-line-queued';
  // Slash commands get an extra marker class so they read as a command, not
  // as prose the agent should answer. Keeps agent-line-prompt so every
  // existing prompt-bubble rule/selector still applies.
  if (t.startsWith('> ')) {
    return isSlashCommandLine(t)
      ? 'agent-line agent-line-prompt agent-line-cmd'
      : 'agent-line agent-line-prompt';
  }
  // Substitution advisory (mc/artifact_coverage.py) — reads as a warning,
  // not as the green status lines, because it is a claim about the answer
  // the user is about to trust.
  if (t.startsWith('[coverage]')) return 'agent-line agent-line-coverage';
  // Stop-hook block/resend boundary (server-side: agent_runtime.is_stop_hook_
  // feedback). conversation.js's two render paths intercept this marker
  // BEFORE agentLineCls to collapse the preceding draft into a toggle; this
  // fallback is for renderers that don't (agent-console.js, project-forms.js)
  // so the raw marker never shows up as a visible line there either.
  if (isStopHookRedoLine(t)) return 'agent-line agent-line-hidden';
  if (t.startsWith('[tool:')) return 'agent-line agent-line-tool';
  if (t.startsWith('[') && t.endsWith(']')) return 'agent-line agent-line-status';
  if (t.startsWith('[exited') || t.startsWith('[stream error')) return 'agent-line agent-line-error';
  return 'agent-line';
}

function collapseIntoPlanButton(sessionId, container) {
  // Walk backwards from end, collecting non-tool text lines until we hit a [tool:] line
  const children = Array.from(container.children);
  const planLines = [];
  const planElements = [];

  for (let i = children.length - 1; i >= 0; i--) {
    const child = children[i];
    const txt = (child.textContent || '').trim();
    // Stop at tool lines, prompt lines, or existing plan buttons
    if (child.classList.contains('agent-line-tool') ||
        child.classList.contains('agent-line-prompt') ||
        child.classList.contains('plan-show-btn')) break;
    // Skip the ExitPlanMode tool line itself (already handled above)
    if (txt === '[tool: ExitPlanMode]') continue;
    planLines.unshift(child.textContent || child.innerText || '');
    planElements.unshift(child);
  }

  if (planLines.length < 2) return; // Too few lines, not a real plan

  // Store plan content
  planViewerContent[sessionId] = planLines;

  // Wrap plan elements in a hidden container
  const wrapper = document.createElement('div');
  wrapper.className = 'plan-hidden-block';
  wrapper.dataset.sessionId = sessionId;
  const insertBefore = planElements[0];
  container.insertBefore(wrapper, insertBefore);
  for (const el of planElements) wrapper.appendChild(el);

  // Insert "Show Plan" button before the hidden block
  const btn = document.createElement('button');
  btn.className = 'plan-show-btn';
  btn.innerHTML = '&#128196; Show Plan';
  btn.onclick = () => openPlanViewer(sessionId);
  container.insertBefore(btn, wrapper);
}

// A Stop hook (reply-length/permission-ask/turn-guard) blocked the draft that
// just streamed and the model re-sent a compressed version in the SAME turn —
// server marks the boundary with a '[stop-hook-redo]' line (see
// agent_runtime.is_stop_hook_feedback). Collapse whatever narration/output
// landed since the last tool/prompt line (i.e. the retracted draft) into a
// native <details> toggle, hidden by default, so the chat shows only the
// final reply plus a small "Show earlier draft" affordance instead of both
// replies back to back. Mirrors collapseIntoPlanButton's walk-back shape.
function collapseIntoDraftBlock(sessionId, container) {
  const children = Array.from(container.children);
  const draftElements = [];
  for (let i = children.length - 1; i >= 0; i--) {
    const child = children[i];
    if (child.classList.contains('agent-line-tool') ||
        child.classList.contains('agent-line-prompt') ||
        child.classList.contains('plan-show-btn') ||
        child.classList.contains('draft-block')) break;
    draftElements.unshift(child);
  }
  if (draftElements.length === 0) return; // nothing to collapse — never hide a bare marker

  const details = document.createElement('details');
  details.className = 'draft-block';
  const summary = document.createElement('summary');
  summary.textContent = 'Show earlier draft';
  details.appendChild(summary);
  const insertBefore = draftElements[0];
  container.insertBefore(details, insertBefore);
  for (const el of draftElements) details.appendChild(el);
}


function expandAgentOutput(sessionId) {
  expandedOutputSessions.add(sessionId);
  refreshModal();
}

// "Pinned to bottom" detection: only auto-scroll when the user is already within
// ~80 px of the bottom. Otherwise leave their scroll position alone — they're
// reading earlier output and don't want to be yanked back.
// rAF-batched scroll-to-bottom for streaming agent output.
// Each `appendAgentLine` call used to do `el.scrollTop = el.scrollHeight`
// synchronously — that's a forced layout reflow per line. On Android WebView
// during a 50-line streaming burst that's 50 reflows back-to-back, blocking
// the input handler for hundreds of ms (keyboard delete drops, typing lag).
// Coalescing all writes within a frame into one rAF callback collapses 50
// reflows into 1.
const _pinScrollQueue = new Map();  // sessionId -> {el, freshMount}

function _isAgentOutputPinned(el, sessionId) {
  if (!el) return true;
  // If we have a pending pin-scroll for this session, treat as still pinned —
  // the DOM read below would return the pre-write state during a streaming
  // burst and report "not at bottom" even though we're about to scroll there.
  if (sessionId && _pinScrollQueue.has(sessionId)) return true;
  return (el.scrollHeight - el.scrollTop - el.clientHeight) < 80;
}

function _scheduleAgentPinScroll(sessionId, el, freshMount) {
  if (!el) return;
  const wasQueued = _pinScrollQueue.has(sessionId);
  _pinScrollQueue.set(sessionId, { el, freshMount: !!freshMount });
  if (wasQueued) return;
  requestAnimationFrame(() => {
    const entry = _pinScrollQueue.get(sessionId);
    _pinScrollQueue.delete(sessionId);
    if (!entry) return;
    const target = entry.el;
    if (!target || !target.isConnected) return;
    target.scrollTop = target.scrollHeight;
    if (entry.freshMount && target.scrollHeight > 0) {
      target.dataset.scrollInitialized = '1';
    }
  });
}


// ── interop: window re-exposure for inline/generated/cross-module callers ──
window.formatAgentText = formatAgentText;
window.isTableLine = isTableLine;
window.formatTableLine = formatTableLine;
window.isPipeTable = isPipeTable;
window.buildPipeTable = buildPipeTable;
window.agentLineCls = agentLineCls;
// Exported for the optimistic-echo paths in conversation.js, which build the
// bubble className directly instead of going through agentLineCls().
window.isSlashCommandLine = isSlashCommandLine;
window.SLASH_COMMAND_RE = SLASH_COMMAND_RE;
window.collapseIntoPlanButton = collapseIntoPlanButton;
window.collapseIntoDraftBlock = collapseIntoDraftBlock;
window.isStopHookRedoLine = isStopHookRedoLine;
window.expandAgentOutput = expandAgentOutput;
window._isAgentOutputPinned = _isAgentOutputPinned;
window._scheduleAgentPinScroll = _scheduleAgentPinScroll;
window._firstStrongDir = _firstStrongDir;
window._isolateRtlRuns = _isolateRtlRuns;
window._baseDirIgnoringIsolated = _baseDirIgnoringIsolated;

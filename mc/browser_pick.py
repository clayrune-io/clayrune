"""Element picker for the browser pane -- the pure half (no Flask, no CDP).

A human puts the pane in pick mode, aims at an element, and Clayrune hands the
chat composer ONE context chip: the element's trimmed HTML, a handful of
computed styles, a CSS selector path and a clipped screenshot. The point is to
show an agent a UI defect without a screenshot-and-describe round trip.

This module holds what can be tested without a browser: the caps, the in-page
function that reads the element, and the geometry. The routes, the human-only
gate and the CDP traffic live in mc/blueprints/browser_pick_routes.py.

EVERYTHING the page contributes is untrusted third-party data -- the HTML, the
selector (ids and classes are page-chosen strings), the computed-style values
(`font-family` is arbitrary text) and the screenshot. The routes wrap all of it
in the same envelope /api/browser/read uses and the in-page function below
strips what a human could not see with the same `hiddenReason` test.

## Caps (all stated here, none implicit)

  PICK_MAX_HTML_CHARS         20_000   returned element HTML
  PICK_MAX_TEXT_CHARS          8_000   returned visible text
  PICK_MAX_SELECTOR_CHARS        300   selector path
  PICK_MAX_STYLE_VALUE_CHARS     120   one computed-style value
  PICK_MAX_ATTR_CHARS            200   one attribute value in the HTML
  PICK_MAX_ELEMENTS              600   elements walked under the picked one
  PICK_MAX_DEPTH                  12   nesting walked under the picked one
  PICK_MAX_SCREENSHOT_BYTES 1_500_000  screenshot file (PNG, else JPEG, else none)
  PICK_MAX_CLIP_W/H        1600 x 1200 screenshot clip, in CSS px

Each cap that bites is reported in the envelope (`truncated`, `screenshot`),
never silent.
"""

PICK_MAX_HTML_CHARS = 20_000
PICK_MAX_TEXT_CHARS = 8_000
PICK_MAX_SELECTOR_CHARS = 300
PICK_MAX_STYLE_VALUE_CHARS = 120
PICK_MAX_ATTR_CHARS = 200
PICK_MAX_ELEMENTS = 600
PICK_MAX_DEPTH = 12
PICK_MAX_LABEL_CHARS = 60
PICK_MAX_SCREENSHOT_BYTES = 1_500_000
PICK_MAX_CLIP_W = 1600
PICK_MAX_CLIP_H = 1200
# Looser than PICK_MAX_HTML_CHARS on purpose: a safety valve so a pathological
# element cannot hand a multi-MB string back over the CDP socket before Python
# enforces the real cap (same shape as browser_routes._JS_SAFETY_CHAR_CAP).
PICK_JS_HTML_SAFETY_CAP = 3 * PICK_MAX_HTML_CHARS

# The computed styles worth a defect report: box, type, colour, layout. A
# whitelist, so a style the page invents cannot widen the payload.
PICK_STYLE_PROPS = (
    'display', 'position', 'z-index', 'width', 'height',
    'margin-top', 'margin-right', 'margin-bottom', 'margin-left',
    'padding-top', 'padding-right', 'padding-bottom', 'padding-left',
    'color', 'background-color', 'opacity', 'visibility',
    'font-family', 'font-size', 'font-weight', 'line-height', 'text-align',
    'border-top-width', 'border-top-style', 'border-top-color', 'border-radius',
    'overflow-x', 'overflow-y', 'flex-direction', 'justify-content',
    'align-items', 'gap', 'box-shadow',
)

# The page's own attribute names that never go into the HTML: event handlers
# (code), srcdoc (a whole document), srcset/nonce/integrity (noise), and
# `value` -- a typed password or form entry must not ride along in a chip.
# `__HIDDEN_REASON_JS__` is spliced in by the routes from browser_routes, the
# one definition of "hidden from a human" shared with /api/browser/read.
PICK_FN_TEMPLATE = r"""
function(opts) {
  try {
    var root = this.nodeType === 1 ? this : this.parentElement;
    if (!root) return {error: 'not_an_element'};
    var doc = root.ownerDocument;

    __HIDDEN_REASON_JS__

    function clip(s, n) { s = String(s); return s.length > n ? s.slice(0, n) + '…' : s; }
    function esc(s) {
      return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;')
                      .replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
    function cssEsc(s) {
      return (typeof CSS !== 'undefined' && CSS.escape) ? CSS.escape(s) : String(s).replace(/[^\w-]/g, '\\$&');
    }
    var DROP_ATTR = /^(on|srcdoc$|srcset$|nonce$|integrity$|value$)/i;
    var SKIP = {SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, TEMPLATE: 1};
    var VOID = {AREA: 1, BASE: 1, BR: 1, COL: 1, EMBED: 1, HR: 1, IMG: 1, INPUT: 1,
                LINK: 1, META: 1, SOURCE: 1, TRACK: 1, WBR: 1};
    // Hidden because an ANCESTOR is: a child of display:none keeps its own
    // computed display, so hiddenReason(child) alone never sees it.
    var INHERIT = {display_none: 1, zero_opacity: 1};

    var runs = [], html = [], htmlChars = 0, elCount = 0;
    var commentCount = 0, attrTextCount = 0, capped = false, htmlCapped = false;

    function attrStr(el) {
      var out = '', a = el.attributes;
      for (var i = 0; i < a.length; i++) {
        if (DROP_ATTR.test(a[i].name)) continue;
        var v = a[i].value;
        if (/^data:/i.test(v)) v = v.slice(0, 20) + '…[data uri ' + v.length + ' chars]';
        out += ' ' + a[i].name + '="' + esc(clip(v, opts.attrCap)) + '"';
      }
      return out;
    }
    function emit(s) {
      if (htmlCapped) return;
      if (htmlChars + s.length > opts.htmlSafetyCap) { htmlCapped = true; return; }
      html.push(s); htmlChars += s.length;
    }

    function walk(el, depth, inherited, isRoot) {
      if (elCount >= opts.maxElements || depth > opts.maxDepth) { capped = true; return; }
      elCount++;
      var tag = el.tagName;
      if (SKIP[tag]) return;
      var own = hiddenReason(el);
      var reason = own || inherited;
      var next = (own && INHERIT[own]) ? own : inherited;
      if (el.getAttribute && (el.getAttribute('aria-label') || el.getAttribute('alt') ||
          el.getAttribute('title'))) attrTextCount++;
      var text = '';
      for (var i = 0; i < el.childNodes.length; i++) {
        if (el.childNodes[i].nodeType === 3) text += el.childNodes[i].nodeValue;
        else if (el.childNodes[i].nodeType === 8) commentCount++;
      }
      text = text.replace(/\s+/g, ' ').trim();
      if (text && tag !== 'TEXTAREA') runs.push({text: text, hidden: reason});

      var lower = tag.toLowerCase();
      var shown = !reason || isRoot;        // the hit element itself is always shown
      var shell = isRoot && !!reason;       // ...but its content is not, when it is hidden
      if (shown) emit('<' + lower + attrStr(el) + '>');
      if (VOID[tag]) { return; }
      if (shell) emit('[content stripped: hidden]');
      var live = shown && !shell;
      for (var j = 0; j < el.childNodes.length; j++) {
        var cn = el.childNodes[j];
        if (cn.nodeType === 1) {
          if (live) walk(cn, depth + 1, next, false);
          else walkHiddenForRuns(cn, depth + 1, next || reason);
        } else if (cn.nodeType === 3 && live && tag !== 'TEXTAREA' && tag !== 'IFRAME') {
          var t = cn.nodeValue.replace(/\s+/g, ' ').trim();
          if (t) emit(esc(clip(t, 500)));
        }
      }
      if (shown) emit('</' + lower + '>');
    }
    // Under a hidden element nothing is serialized, but its text is still
    // reported (flagged) so the envelope can say how much was stripped.
    function walkHiddenForRuns(el, depth, inherited) {
      if (elCount >= opts.maxElements || depth > opts.maxDepth) { capped = true; return; }
      elCount++;
      if (SKIP[el.tagName]) return;
      var own = hiddenReason(el);
      var reason = own || inherited;
      var next = (own && INHERIT[own]) ? own : inherited;
      var text = '';
      for (var i = 0; i < el.childNodes.length; i++) {
        if (el.childNodes[i].nodeType === 3) text += el.childNodes[i].nodeValue;
        else if (el.childNodes[i].nodeType === 8) commentCount++;
      }
      text = text.replace(/\s+/g, ' ').trim();
      if (text) runs.push({text: text, hidden: reason});
      for (var j = 0; j < el.childNodes.length; j++) {
        if (el.childNodes[j].nodeType === 1) walkHiddenForRuns(el.childNodes[j], depth + 1, next);
      }
    }
    walk(root, 0, null, true);

    function selectorFor(el) {
      var parts = [], node = el;
      while (node && node.nodeType === 1 && parts.length < 10) {
        var tag = node.tagName.toLowerCase();
        if (node.id && /^[A-Za-z][\w-]{0,39}$/.test(node.id)) {
          parts.unshift(tag + '#' + cssEsc(node.id));
          break;
        }
        var part = tag;
        if (node.classList) {
          var cls = Array.prototype.slice.call(node.classList, 0, 3);
          for (var c = 0; c < cls.length; c++) part += '.' + cssEsc(clip(cls[c], 40));
        }
        var par = node.parentElement;
        if (par) {
          var same = 0, idx = 0;
          for (var k = 0; k < par.children.length; k++) {
            if (par.children[k].tagName === node.tagName) {
              same++;
              if (par.children[k] === node) idx = same;
            }
          }
          if (same > 1) part += ':nth-of-type(' + idx + ')';
        }
        parts.unshift(part);
        node = par;
      }
      var sel = parts.join(' > ');
      return sel.length > opts.selCap ? '…' + sel.slice(sel.length - opts.selCap) : sel;
    }

    var cs = getComputedStyle(root), styles = {};
    for (var p = 0; p < opts.props.length; p++) {
      styles[opts.props[p]] = clip(cs.getPropertyValue(opts.props[p]), opts.valCap);
    }
    var label = root.tagName.toLowerCase();
    if (root.id && /^[A-Za-z][\w-]{0,39}$/.test(root.id)) label += '#' + root.id;
    else if (root.classList && root.classList.length) label += '.' + clip(root.classList[0], 30);

    return {
      content_type: doc.contentType, title: doc.title,
      tag: root.tagName.toLowerCase(), label: label, selector: selectorFor(root),
      html: html.join(''), html_capped: htmlCapped, js_capped: capped,
      runs: runs, comment_count: commentCount, attr_text_count: attrTextCount,
      styles: styles
    };
  } catch (e) {
    return {error: 'js_exception: ' + (e && e.message ? e.message : String(e))};
  }
}
"""

# Tiny second function for the hover highlight: the label only (the box comes
# from DOM.getBoxModel). Own world, same as the pick, so a page that overrides
# `Element.prototype` cannot answer for the element.
HOVER_LABEL_FN = r"""
function() {
  var el = this.nodeType === 1 ? this : this.parentElement;
  if (!el) return '';
  var s = el.tagName.toLowerCase();
  if (el.id && /^[A-Za-z][\w-]{0,39}$/.test(el.id)) s += '#' + el.id;
  else if (el.classList && el.classList.length) s += '.' + String(el.classList[0]).slice(0, 30);
  return s;
}
"""

# Hands back the ELEMENT itself (not its value) so DOM.getBoxModel can measure
# it: a text-node hit must be measured as its parent, same as the pick reads.
ELEMENT_OF_FN = "function() { return this.nodeType === 1 ? this : this.parentElement; }"


def pick_options():
    """The one argument the in-page function takes. Every cap it enforces is
    named above; nothing in the function body is a magic number."""
    return {
        'htmlSafetyCap': PICK_JS_HTML_SAFETY_CAP,
        'attrCap': PICK_MAX_ATTR_CHARS,
        'maxElements': PICK_MAX_ELEMENTS,
        'maxDepth': PICK_MAX_DEPTH,
        'selCap': PICK_MAX_SELECTOR_CHARS,
        'valCap': PICK_MAX_STYLE_VALUE_CHARS,
        'props': list(PICK_STYLE_PROPS),
    }


def clip_str(value, limit):
    """`value` as a str, cut to `limit` characters (with an ellipsis). Anything
    that is not a string becomes ''. The page chose these strings; Python does
    not take the in-page function's word for their type or length."""
    if not isinstance(value, str):
        return ''
    return value if len(value) <= limit else value[:limit] + '…'


def clean_styles(styles):
    """Whitelisted properties only, string values only, each value capped."""
    out = {}
    if not isinstance(styles, dict):
        return out
    for prop in PICK_STYLE_PROPS:
        v = styles.get(prop)
        if isinstance(v, str) and v != '':
            out[prop] = clip_str(v, PICK_MAX_STYLE_VALUE_CHARS)
    return out


def strip_always_hidden(runs):
    """Drop runs the in-page test flagged display_none / visibility_hidden and
    count them. /api/browser/read's `_filter_hidden_runs` strips only
    offscreen / zero_opacity / tiny_font / low_contrast, so it would pass these
    two through as visible text -- which is the first thing anyone reaches for
    to hide an instruction. The picker removes them before that filter runs, so
    the HTML (which already drops them) and the text agree."""
    counts = {'display_none': 0, 'visibility_hidden': 0}
    kept = []
    for run in (runs if isinstance(runs, list) else []):
        if not isinstance(run, dict):
            continue
        reason = run.get('hidden')
        if reason in counts:
            counts[reason] += 1
            continue
        kept.append(run)
    return kept, {k: v for k, v in counts.items() if v}


def quad_bounds(quad):
    """Bounding box {x, y, w, h} of a CDP quad ([x1,y1,...,x4,y4]); None if the
    quad is malformed or empty."""
    try:
        xs = [float(quad[i]) for i in range(0, 8, 2)]
        ys = [float(quad[i]) for i in range(1, 8, 2)]
    except (TypeError, ValueError, IndexError):
        return None
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    if w <= 0 or h <= 0:
        return None
    return {'x': min(xs), 'y': min(ys), 'w': w, 'h': h}


def rect_to_frame(rect, page_scale):
    """Layout-px rect -> the picture px the pane draws in. The inverse of the
    `x * (1/pageScale)` browser_routes._input_commands applies to a click, so a
    highlight lands on the pixels the click would have hit."""
    try:
        ps = float(page_scale)
    except (TypeError, ValueError):
        ps = 1.0
    if not 0.1 <= ps <= 10:
        ps = 1.0
    return {k: round(v * ps, 2) for k, v in rect.items()}


def screenshot_clip(rect, viewport_w, viewport_h, page_x, page_y):
    """CDP Page.captureScreenshot clip for an element box.

    `rect` is viewport-relative CSS px. It is intersected with the visible
    viewport (the screenshot shows what the human sees, never a full-page
    render of a 40,000px element), capped to PICK_MAX_CLIP_W/H, and shifted by
    the page offset because the clip is in document coordinates. Returns None
    when nothing of the element is on screen."""
    x0, y0 = max(rect['x'], 0.0), max(rect['y'], 0.0)
    x1 = min(rect['x'] + rect['w'], float(viewport_w))
    y1 = min(rect['y'] + rect['h'], float(viewport_h))
    w, h = min(x1 - x0, PICK_MAX_CLIP_W), min(y1 - y0, PICK_MAX_CLIP_H)
    if w < 1 or h < 1:
        return None
    return {'x': x0 + page_x, 'y': y0 + page_y, 'width': w, 'height': h, 'scale': 1}


def screenshot_fits(n_bytes):
    return 0 < n_bytes <= PICK_MAX_SCREENSHOT_BYTES

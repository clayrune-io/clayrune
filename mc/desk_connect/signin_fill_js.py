"""The one script `signin_fill` runs for the sign-in page (slice P2b). Its own file: it is
page-side code, changes when a sign-in page changes, and has no business in the module that
decides WHETHER to fill.

`build(...)` returns a JavaScript expression that `signin_fill_cdp.PageLink.evaluate` runs in an
ISOLATED WORLD of the top frame (`Page.createIsolatedWorld`), never in the page's own world. The
isolated world shares the page's DOM and nothing else: the page's `Array.prototype`,
`HTMLInputElement.prototype`, `Event`, `getComputedStyle`, `Object.getOwnPropertyDescriptor`,
`document.querySelectorAll` and the rest are NOT the ones this script calls, so a page that
overrides them (the 2026-10-05 audit captured a password that way) changes nothing here. Two page
tricks still reach an isolated world and are handled by name:

  * DOM clobbering: `<input name="action">` shadows `form.action`, `<img name="activeElement">`
    shadows `document.activeElement`. The script reads attributes and the browser's own accessors
    through the unmodified prototypes (`Element.prototype.getAttribute.call(form, 'action')`), never
    through the element or the document by name;
  * the page's own listeners see an `input` event while the login is typed, exactly as they would
    if a person typed it. That is the declared origin's own page; every other page is refused
    before this script types anything.

Everything that makes a fill safe happens INSIDE one synchronous evaluation, so nothing can
navigate between a check and the typing (a navigation destroys the isolated world's context and the
evaluation fails; nothing is typed in the new document):

  1. the document is the TOP frame and `location.origin` (an unforgeable accessor, read in this
     world) is exactly one of `cfg.origins` (https, default port). Otherwise `origin_mismatch`,
     having touched nothing. This is the second check: the caller has already read the top-level
     URL from the browser (`Page.getFrameTree`) before asking the vault for anything;
  2. a CAPTCHA or a second-factor prompt returns `captcha` / `two_factor`, typing nothing: that
     step belongs to the person;
  3. the form the password goes into posts only to a declared origin: its `action` and the
     `formaction` of every control that can submit it. Otherwise `action_mismatch`, typing nothing
     (an honest sign-in page never posts to another origin; a swapped action is how a typed
     password would leave). It is checked again after typing, before the submit;
  4. only then are fields found, focused and typed into (`execCommand('insertText')`, the way a
     person's typing reaches the field), and the form submitted.

It only looks at VISIBLE, enabled inputs, and never reads a value back: the result is a state
word and the names of the fields filled, never the text typed. `cfg.user` / `cfg.password` are
absent on a probe (`cfg.fill` false), so the password is not even in the expression until the
caller has seen a declared origin and a password field.
"""
from __future__ import annotations

import json

# Isolated-world side. `C` is the config object; the function returns a plain object.
_SCRIPT = r"""
(function (C) {
  function R(state, extra) { var o = extra || {}; o.state = state; return o; }
  try {
    var D = document, W = window;
    function P(K, n) { return Object.getOwnPropertyDescriptor(K.prototype, n); }
    var qsa = Document.prototype.querySelectorAll, eqsa = Element.prototype.querySelectorAll;
    var getAttr = Element.prototype.getAttribute, closestFn = Element.prototype.closest;
    var rectFn = Element.prototype.getBoundingClientRect;
    var inputForm = P(HTMLInputElement, 'form').get, formElements = P(HTMLFormElement, 'elements').get;
    var activeOf = P(Document, 'activeElement').get, baseOf = P(Node, 'baseURI').get;
    var execCmd = Document.prototype.execCommand;
    var focusFn = HTMLElement.prototype.focus, clickFn = HTMLElement.prototype.click;
    var selectFn = HTMLInputElement.prototype.select, requestSubmitFn = HTMLFormElement.prototype.requestSubmit;
    var setValue = P(HTMLInputElement, 'value').set;
    var filterFn = Array.prototype.filter, sliceFn = Array.prototype.slice;
    var base = baseOf.call(D);

    if (W.top !== W) return R('not_top');
    var here = location.origin;
    if (C.origins.indexOf(here) < 0) return R('origin_mismatch', { host: String(location.hostname).slice(0, 80) });

    function vis(el) {
      if (!el || el.disabled || el.readOnly) return false;
      var cs = W.getComputedStyle(el);
      if (cs.visibility === 'hidden' || cs.display === 'none' || Number(cs.opacity) === 0) return false;
      var r = rectFn.call(el);
      return r.width > 1 && r.height > 1;
    }
    function shown(el) {
      if (!el) return false;
      var cs = W.getComputedStyle(el);
      var r = rectFn.call(el);
      return cs.visibility !== 'hidden' && cs.display !== 'none' && r.width > 1 && r.height > 1;
    }
    function inputs(sel) { return filterFn.call(qsa.call(D, sel), vis); }

    // A CAPTCHA the person must solve. The invisible reCAPTCHA badge is not one.
    var capSel = 'iframe[src*="recaptcha"],iframe[src*="hcaptcha"],iframe[src*="arkoselabs"],iframe[src*="funcaptcha"],' +
      'iframe[src*="challenges.cloudflare.com"],iframe[src*="turnstile"],.g-recaptcha,.h-captcha,.cf-turnstile,' +
      '#captcha-internal,input[name*="captcha" i],img[alt*="captcha" i]';
    var caps = filterFn.call(qsa.call(D, capSel), function (el) {
      return shown(el) && !closestFn.call(el, '.grecaptcha-badge');
    });
    if (caps.length) return R('captcha');

    // A second factor, or a "confirm it's you" prompt: a code input, or a challenge page.
    var path = location.pathname.toLowerCase();
    var otp = inputs('input[autocomplete="one-time-code"],input[name*="otp" i],input[name*="totp" i],input[name*="mfa" i],' +
      'input[id*="otp" i],input[name*="verification" i],input[name="pin"],[data-testid="ocfEnterTextTextInput"]');
    var pwAll = inputs('input[type="password"]');
    if (otp.length && !pwAll.length) return R('two_factor');
    if (/(checkpoint|two-?factor|2fa|\/mfa|\/otp|\/challenge|verification)/.test(path)) return R('two_factor');

    var pw = pwAll[0] || null;
    var textual = inputs('input:not([type]),input[type="text"],input[type="email"],input[type="tel"]');
    function pickUser() {
      var by = function (f) { return filterFn.call(textual, f)[0] || null; };
      return by(function (e) { return /(^|\s)username(\s|$)/i.test(getAttr.call(e, 'autocomplete') || ''); }) ||
        by(function (e) { return e.type === 'email'; }) ||
        by(function (e) { return /^(user(name)?|login|email|identifier|session_key|text)$/i.test(e.name || e.id || ''); }) ||
        (pw ? by(function (e) { return !(pw.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING); }) : textual[0] || null);
    }
    var user = pickUser();

    // Where a form would send what is typed into it: the form's `action`, and the `formaction` of
    // any control that can submit it, must each resolve to a declared origin.
    function declared(u) {
      try { return C.origins.indexOf(new URL(u, base).origin) >= 0; } catch (e) { return false; }
    }
    function postsOnlyHere(el) {
      var f = inputForm.call(el);
      if (!f) return true;
      var a = getAttr.call(f, 'action');
      if (a !== null && !declared(a)) return false;
      var ctl = sliceFn.call(formElements.call(f));
      for (var i = 0; i < ctl.length; i++) {
        var fa = getAttr.call(ctl[i], 'formaction');
        if (fa !== null && !declared(fa)) return false;
      }
      return true;
    }
    if ((pw && !postsOnlyHere(pw)) || (user && !postsOnlyHere(user))) return R('action_mismatch');

    if (!C.fill) return R(pw ? 'login_form' : user ? 'username_step' : 'other', { has_password: !!pw, has_username: !!user });

    // Typed the way a person's typing reaches the field: focus it, select what is there, insert.
    // The page's own value setter / Event are never called; this world's are.
    function put(el, v) {
      focusFn.call(el);
      selectFn.call(el);
      if (activeOf.call(D) !== el) return false;      // focus did not land on it (a page handler moved it)
      var ok = false;
      try { ok = execCmd.call(D, 'insertText', false, v); } catch (e) { ok = false; }
      if (!ok) {
        setValue.call(el, v);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }
      return true;
    }
    function click(el) { try { clickFn.call(el); return true; } catch (e) { return false; } }
    var NEXT = /^(next|log ?in|sign ?in|continue|submit)$/i;
    function submit(from) {
      var f = inputForm.call(from);
      if (f) {
        if (!postsOnlyHere(from)) return null;          // the destination changed while typing: do not send it
        var b = eqsa.call(f, 'button[type="submit"],input[type="submit"]')[0];
        if (b && shown(b)) return click(b);
        if (requestSubmitFn) { requestSubmitFn.call(f); return true; }
      }
      var n = from;
      for (var i = 0; i < 7 && n; i++, n = n.parentElement) {
        var c = eqsa.call(n, 'button,[role="button"],input[type="button"],input[type="submit"]');
        for (var j = 0; j < c.length; j++) {
          var t = String(c[j].innerText || c[j].value || '').trim();
          if (NEXT.test(t) && shown(c[j])) return click(c[j]);
        }
      }
      return false;
    }
    function done(filled, from) {
      var sent = C.submit ? submit(from) : false;
      return R('filled', { filled: filled, submitted: sent === true, action_blocked: sent === null });
    }

    var filled = [];
    if (pw) {
      if (C.password == null || C.user == null) return R('no_form');
      if (user) { if (!put(user, C.user)) return R('no_form'); filled.push('username'); }
      if (!put(pw, C.password)) return R('no_form', { filled: filled });
      filled.push('password');
      return done(filled, pw);
    }
    if (user && C.user != null) {
      if (!put(user, C.user)) return R('no_form');
      filled.push('username');
      return done(filled, user);
    }
    return R('no_form');
  } catch (e) {
    return R('script_error');
  }
})(__CFG__)
"""


def build(origins: list[str], *, fill: bool, user: str | None = None, password: str | None = None,
          submit: bool = True) -> str:
    """The expression to evaluate. `user`/`password` are embedded only when `fill` is true."""
    cfg: dict = {'origins': list(origins), 'fill': bool(fill), 'submit': bool(submit)}
    if fill:
        cfg['user'] = user
        cfg['password'] = password
    return _SCRIPT.replace('__CFG__', json.dumps(cfg))

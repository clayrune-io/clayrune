"""The one script `signin_fill` runs inside the sign-in page (slice P2b). Its own file: it is
page-side code, changes when a sign-in page changes, and has no business in the module that
decides WHETHER to fill.

`build(cfg)` returns a JavaScript expression for `Runtime.evaluate`. Everything that makes a
fill safe happens INSIDE that one synchronous evaluation, so nothing can navigate between a
check and the typing:

  1. the document must be the TOP frame (`window.top === window`; the script runs in the top
     frame's own world, so an iframe from another origin is never reached) and its
     `location.origin` must be exactly one of `cfg.origins` (https, default port). Otherwise it
     returns `origin_mismatch` having touched nothing;
  2. a CAPTCHA or a second-factor prompt on the page returns `captcha` / `two_factor` having
     typed nothing: that step belongs to the person;
  3. only then are fields found and filled.

It only looks at VISIBLE, enabled inputs, and never reads a value back: the result is a state
word and the names of the fields filled, never the text typed. `cfg.user` / `cfg.password` are
absent on a probe (`cfg.fill` false), so the password is not even in the expression until the
caller has seen a matching origin and a password field.
"""
from __future__ import annotations

import json

# Page-side. `C` is the config object; the function returns a plain object.
_SCRIPT = r"""
(function (C) {
  function R(state, extra) { var o = extra || {}; o.state = state; return o; }
  try {
    if (window.top !== window) return R('not_top');
    if (C.origins.indexOf(location.origin) < 0) return R('origin_mismatch', { host: String(location.hostname).slice(0, 80) });

    function vis(el) {
      if (!el || el.disabled || el.readOnly) return false;
      var cs = window.getComputedStyle(el);
      if (cs.visibility === 'hidden' || cs.display === 'none' || Number(cs.opacity) === 0) return false;
      var r = el.getBoundingClientRect();
      return r.width > 1 && r.height > 1;
    }
    function shown(el) {
      if (!el) return false;
      var cs = window.getComputedStyle(el);
      var r = el.getBoundingClientRect();
      return cs.visibility !== 'hidden' && cs.display !== 'none' && r.width > 1 && r.height > 1;
    }
    function inputs(sel) { return Array.prototype.filter.call(document.querySelectorAll(sel), vis); }

    // A CAPTCHA the person must solve. The invisible reCAPTCHA badge is not one.
    var capSel = 'iframe[src*="recaptcha"],iframe[src*="hcaptcha"],iframe[src*="arkoselabs"],iframe[src*="funcaptcha"],' +
      'iframe[src*="challenges.cloudflare.com"],iframe[src*="turnstile"],.g-recaptcha,.h-captcha,.cf-turnstile,' +
      '#captcha-internal,input[name*="captcha" i],img[alt*="captcha" i]';
    var caps = Array.prototype.filter.call(document.querySelectorAll(capSel), function (el) {
      return shown(el) && !el.closest('.grecaptcha-badge');
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
      var by = function (f) { return textual.filter(f)[0] || null; };
      return by(function (e) { return /(^|\s)username(\s|$)/i.test(e.getAttribute('autocomplete') || ''); }) ||
        by(function (e) { return e.type === 'email'; }) ||
        by(function (e) { return /^(user(name)?|login|email|identifier|session_key|text)$/i.test(e.name || e.id || ''); }) ||
        (pw ? by(function (e) { return !(pw.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING); }) : textual[0] || null);
    }
    var user = pickUser();

    if (!C.fill) return R(pw ? 'login_form' : user ? 'username_step' : 'other', { has_password: !!pw, has_username: !!user });

    function put(el, v) {
      el.focus();
      var set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      set.call(el, v);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    }
    function click(el) { try { el.click(); return true; } catch (e) { return false; } }
    var NEXT = /^(next|log ?in|sign ?in|continue|submit)$/i;
    function submit(from) {
      var f = from.form;
      if (f) {
        var b = f.querySelector('button[type="submit"],input[type="submit"]');
        if (b && shown(b)) return click(b);
        if (f.requestSubmit) { f.requestSubmit(); return true; }
      }
      var n = from;
      for (var i = 0; i < 7 && n; i++, n = n.parentElement) {
        if (!n) break;
        var c = n.querySelectorAll('button,[role="button"],input[type="button"],input[type="submit"]');
        for (var j = 0; j < c.length; j++) {
          var t = String(c[j].innerText || c[j].value || '').trim();
          if (NEXT.test(t) && shown(c[j])) return click(c[j]);
        }
      }
      return false;
    }

    var filled = [];
    if (pw) {
      if (C.password == null || C.user == null) return R('no_form');
      if (user) { put(user, C.user); filled.push('username'); }
      put(pw, C.password); filled.push('password');
      return R('filled', { filled: filled, submitted: C.submit ? submit(pw) : false });
    }
    if (user && C.user != null) {
      put(user, C.user); filled.push('username');
      return R('filled', { filled: filled, submitted: C.submit ? submit(user) : false });
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

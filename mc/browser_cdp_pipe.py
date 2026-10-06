"""CDP over an anonymous pipe pair instead of a TCP debugging port.

Why this exists (backlog 6b313cb6, Wren's P2b re-audit R1): the pane's Chromium
used to be launched with ``--remote-debugging-port=<random>`` on 127.0.0.1. Any
local process can read that port off ``netstat`` or the Chromium command line,
attach a second CDP client, install an ``input`` listener on the sign-in page and
read the username and password the passcode-gated fill typed (reproduced in real
Chromium). The page's own isolated world does not help, and nothing inside
``signin_fill`` can: the exposure is the open port.

``--remote-debugging-pipe`` removes the port. Chromium reads CDP from one
inherited handle and writes it to another; only the process that created them
can speak to it. No ``/json`` HTTP endpoint exists either.

What this module is: the pane's code was written against ``websocket-client`` --
one connection per concern (the reader, the UA guard, the viewport fit, the
graceful close, the page-text read, the picker), each with its own message ids and
each page-scoped or browser-scoped. A pipe is ONE stream. ``PipeTransport``
multiplexes it so each of those keeps working unchanged against a ``PipeConn``,
which has the three methods the call sites use (``send`` / ``recv`` /
``settimeout`` / ``close``) and raises ``websocket-client``'s own exceptions:

  - ids are remapped per message, so two clients both using ``id: 1`` never
    collide, and each gets its own id back;
  - a page-scoped ``PipeConn`` is a flat-mode session on its target: commands with
    no ``sessionId`` are given the root session's, and events from it have it
    removed, which is exactly what a page-level websocket looks like;
  - events go to the connection that owns the session they belong to (a session's
    owner is the connection that attached it, or the owner of the session whose
    auto-attach produced it); browser-level events with no session go to the
    connection that turned auto-attach on.

Chromium shuts itself down when its end of the pipe closes, so a server that
dies cannot leave a pane Chromium behind with a debugging endpoint open.
"""

import json
import os
import queue
import subprocess
import sys
import threading

_READ_CHUNK = 1 << 16


class PipeClosed(Exception):
    """The pipe to Chromium is closed (Chromium exited, or `close()` was called)."""


def _ws_exc(name):
    # The pane's call sites catch websocket-client's exception classes by name
    # (`except websocket.WebSocketTimeoutException`), so a PipeConn must raise
    # those exact classes. Imported lazily: websocket-client is optional.
    import websocket
    return getattr(websocket, name)


class PipeConn:
    """A websocket-shaped view of one CDP client on a ``PipeTransport``."""

    def __init__(self, transport, root_sid=None, timeout=None):
        self._t = transport
        self.root_sid = root_sid
        self._timeout = timeout
        self._q = queue.Queue()
        self._closed = False
        # Sessions this connection attached at the top level (root + explicit
        # attaches); detached when the connection closes, as a websocket close would.
        self.top_sessions = [root_sid] if root_sid else []

    def settimeout(self, t):
        self._timeout = t

    def send(self, text):
        if self._closed:
            raise _ws_exc('WebSocketConnectionClosedException')('connection is already closed')
        try:
            self._t._send_from(self, text)
        except PipeClosed as e:
            raise _ws_exc('WebSocketConnectionClosedException')(str(e)) from None

    def recv(self):
        try:
            item = self._q.get(timeout=self._timeout) if self._timeout else self._q.get()
        except queue.Empty:
            raise _ws_exc('WebSocketTimeoutException')('timed out') from None
        if item is None:
            self._q.put(None)    # every later recv sees the close too
            raise _ws_exc('WebSocketConnectionClosedException')('connection closed')
        return item

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._t._drop(self)
        self._q.put(None)


class PipeTransport:
    """Owns both pipe ends and the reader thread; hands out ``PipeConn``s."""

    def __init__(self, read_fd, write_fd):
        self._rfd, self._wfd = read_fd, write_fd
        self._lock = threading.Lock()           # guards the tables below
        self._wlock = threading.Lock()          # serialises writes to the pipe
        self._next_id = 0
        self._routes = {}                       # global id -> (conn | queue, original id)
        self._owner = {}                        # sessionId -> PipeConn (None = internal)
        self._conns = []
        self._pending_attach = []               # (targetId, requester) for explicit attaches
        self._auto_holder = None                # conn that set browser-level auto-attach
        self._silent = set()                    # sessions attached internally: their attach event goes nowhere
        self.closed = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                        name='cdp-pipe-reader')
        self._reader.start()

    # ---- wire ----------------------------------------------------------

    def _read_loop(self):
        buf = b''
        try:
            while True:
                chunk = os.read(self._rfd, _READ_CHUNK)
                if not chunk:
                    break
                buf += chunk
                while b'\0' in buf:
                    raw, buf = buf.split(b'\0', 1)
                    if not raw:
                        continue
                    try:
                        msg = json.loads(raw.decode('utf-8', 'replace'))
                    except Exception:
                        continue
                    if isinstance(msg, dict):
                        self._dispatch(msg)
        except OSError:
            pass
        finally:
            self._shutdown()
            try:
                os.close(self._rfd)
            except OSError:
                pass

    def _shutdown(self):
        with self._lock:
            self.closed = True
            conns = list(self._conns)
            waiters = [r[0] for r in self._routes.values() if isinstance(r[0], queue.Queue)]
            self._routes.clear()
        for c in conns:
            c._q.put(None)
        for w in waiters:
            w.put(None)

    def _write(self, obj):
        data = json.dumps(obj, separators=(',', ':')).encode('utf-8') + b'\0'
        with self._wlock:
            if self.closed:
                raise PipeClosed('pipe is closed')
            try:
                view = memoryview(data)
                while view:
                    n = os.write(self._wfd, view)
                    view = view[n:]
            except OSError as e:
                raise PipeClosed(f'pipe write failed: {e}') from None

    # ---- client -> chromium -------------------------------------------

    def _send_from(self, conn, text):
        msg = json.loads(text)
        orig = msg.get('id')
        with self._lock:
            self._next_id += 1
            gid = self._next_id
            self._routes[gid] = (conn, orig)
        msg['id'] = gid
        if conn.root_sid and not msg.get('sessionId'):
            msg['sessionId'] = conn.root_sid
        method, params, sid = msg.get('method'), msg.get('params') or {}, msg.get('sessionId')
        with self._lock:
            if method == 'Target.attachToTarget' and not sid:
                self._pending_attach.append((params.get('targetId'), conn))
            elif method == 'Target.setAutoAttach' and not sid and params.get('autoAttach'):
                self._auto_holder = conn
        try:
            self._write(msg)
        except PipeClosed:
            with self._lock:
                self._routes.pop(gid, None)
            raise

    def request(self, method, params=None, session_id=None, timeout=5):
        """Internal synchronous call (target discovery, attach). Raises
        ``RuntimeError`` on a CDP error, ``TimeoutError`` / ``PipeClosed``."""
        q = queue.Queue()
        with self._lock:
            self._next_id += 1
            gid = self._next_id
            self._routes[gid] = (q, 0)
            if method == 'Target.attachToTarget' and not session_id:
                self._pending_attach.append(((params or {}).get('targetId'), None))
        msg = {'id': gid, 'method': method, 'params': params or {}}
        if session_id:
            msg['sessionId'] = session_id
        try:
            self._write(msg)
            try:
                r = q.get(timeout=timeout)
            except queue.Empty:
                raise TimeoutError(f'{method}: no response in {timeout}s') from None
        finally:
            with self._lock:
                self._routes.pop(gid, None)
        if r is None:
            raise PipeClosed('pipe closed while waiting for ' + method)
        if 'error' in r:
            raise RuntimeError((r['error'] or {}).get('message') or 'cdp error')
        return r.get('result') or {}

    # ---- chromium -> client -------------------------------------------

    def _dispatch(self, msg):
        sid = msg.get('sessionId')
        if 'id' in msg:
            self._dispatch_response(msg, sid)
            return
        method = msg.get('method')
        params = msg.get('params') or {}
        if method == 'Target.attachedToTarget':
            self._on_attached(msg, sid, params)
            return
        if method == 'Target.detachedFromTarget':
            with self._lock:
                owner = self._owner.pop(params.get('sessionId'), None)
            if owner is not None and not sid:
                self._deliver(owner, msg)
                return
        if sid:
            with self._lock:
                owner = self._owner.get(sid)
            if owner is not None:
                self._deliver(owner, msg)
            return
        # Browser-level event with no session: everyone listening at browser level.
        with self._lock:
            targets = [c for c in self._conns if not c.root_sid]
        for c in targets:
            self._deliver(c, msg)

    def _dispatch_response(self, msg, sid):
        with self._lock:
            route = self._routes.pop(msg['id'], None)
        if route is None:
            return
        who, orig = route
        if isinstance(who, queue.Queue):
            who.put(msg)
            self._note_attach_result(msg, None)
            return
        self._note_attach_result(msg, who)
        out = dict(msg)
        out['id'] = orig
        if sid and sid == who.root_sid:
            out.pop('sessionId', None)
        who._q.put(json.dumps(out))

    def _note_attach_result(self, msg, requester):
        new_sid = ((msg.get('result') or {}).get('sessionId'))
        if not new_sid:
            return
        with self._lock:
            if new_sid not in self._owner:
                self._owner[new_sid] = requester
                if requester is None:
                    self._silent.add(new_sid)     # the attach event has not arrived yet
                for i, (_t, who) in enumerate(self._pending_attach):
                    if who is requester:
                        del self._pending_attach[i]
                        break
                if requester is not None:
                    requester.top_sessions.append(new_sid)

    def _on_attached(self, msg, carrier_sid, params):
        new_sid = params.get('sessionId')
        target_id = (params.get('targetInfo') or {}).get('targetId')
        with self._lock:
            if new_sid in self._silent:               # attached by us, for our own use
                self._silent.discard(new_sid)
                return
            if new_sid in self._owner:
                owner = self._owner[new_sid]          # response got here first
            elif carrier_sid:
                owner = self._owner.get(carrier_sid)  # an auto-attach under a session
                self._owner[new_sid] = owner
            else:
                owner = None
                for i, (tid, who) in enumerate(self._pending_attach):
                    if tid == target_id:
                        owner = who
                        del self._pending_attach[i]
                        break
                else:
                    owner = self._auto_holder         # browser-level auto-attach
                self._owner[new_sid] = owner
                if owner is not None:
                    owner.top_sessions.append(new_sid)
        if owner is not None:
            self._deliver(owner, msg)

    def _deliver(self, conn, msg):
        out = msg
        if msg.get('sessionId') and msg.get('sessionId') == conn.root_sid:
            out = dict(msg)
            out.pop('sessionId', None)
        conn._q.put(json.dumps(out))

    # ---- connections ---------------------------------------------------

    def _add(self, conn):
        with self._lock:
            self._conns.append(conn)
        return conn

    def _drop(self, conn):
        with self._lock:
            if conn in self._conns:
                self._conns.remove(conn)
            if self._auto_holder is conn:
                self._auto_holder = None
            mine = [s for s, o in self._owner.items() if o is conn]
            for s in mine:
                self._owner.pop(s, None)
            tops = list(conn.top_sessions)
        for s in tops:
            if s in mine:
                try:
                    self._write({'id': self._take_id(), 'method': 'Target.detachFromTarget',
                                 'params': {'sessionId': s}})
                except PipeClosed:
                    break

    def _take_id(self):
        with self._lock:
            self._next_id += 1
            return self._next_id

    def browser_conn(self, timeout=None):
        """A connection at browser level (what ``/json/version``'s
        ``webSocketDebuggerUrl`` was)."""
        return self._add(PipeConn(self, None, timeout))

    def page_conn(self, target_id, timeout=None):
        """A connection scoped to one page target (what a ``/json/list``
        entry's ``webSocketDebuggerUrl`` was)."""
        conn = PipeConn(self, None, timeout)
        r = self.request('Target.attachToTarget', {'targetId': target_id, 'flatten': True})
        sid = r['sessionId']
        with self._lock:
            conn.root_sid = sid
            conn.top_sessions = [sid]
            self._owner[sid] = conn
        return self._add(conn)

    # ---- /json replacements -------------------------------------------

    def targets(self):
        """The ``/json/list`` equivalent: dicts with ``id``/``type``/``url``/
        ``title`` and a truthy ``webSocketDebuggerUrl`` marker (the target id is
        what ``page_conn`` takes), so ``_pick_page_target`` is reused as is."""
        infos = self.request('Target.getTargets').get('targetInfos') or []
        return [{'id': i.get('targetId'), 'type': i.get('type'), 'url': i.get('url'),
                 'title': i.get('title'),
                 'webSocketDebuggerUrl': 'pipe:' + str(i.get('targetId'))}
                for i in infos if i.get('targetId')]

    def version(self):
        """The ``/json/version`` fields the pane reads, from ``Browser.getVersion``."""
        r = self.request('Browser.getVersion')
        return {'Browser': r.get('product'), 'Protocol-Version': r.get('protocolVersion'),
                'User-Agent': r.get('userAgent'), 'V8-Version': r.get('jsVersion'),
                'WebKit-Version': r.get('revision')}

    def close(self):
        """Close our ends. Chromium exits when it sees its read end hit EOF."""
        with self._wlock:
            self.closed = True
            fd, self._wfd = self._wfd, None
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._shutdown()


def spawn(args, popen_kwargs):
    """Start Chromium with CDP on a pipe pair; returns ``(proc, PipeTransport)``.

    ``args`` must not carry a ``--remote-debugging-*`` flag; the right ones are
    added here. ``popen_kwargs`` is passed to ``subprocess.Popen`` (stdio,
    creationflags, startupinfo); on Windows its ``startupinfo`` is copied, not
    modified.
    """
    if sys.platform == 'win32':
        return _spawn_windows(args, popen_kwargs)
    return _spawn_posix(args, popen_kwargs)


def _spawn_windows(args, popen_kwargs):
    import _winapi
    import msvcrt
    c_r, c_w = _winapi.CreatePipe(None, 0)    # Chromium reads c_r, we write c_w
    e_r, e_w = _winapi.CreatePipe(None, 0)    # Chromium writes e_w, we read e_r
    os.set_handle_inheritable(c_r, True)
    os.set_handle_inheritable(e_w, True)
    try:
        kw = dict(popen_kwargs)
        base = kw.pop('startupinfo', None)
        si = subprocess.STARTUPINFO()
        if base is not None:
            si.dwFlags = base.dwFlags
            si.wShowWindow = base.wShowWindow
        # Only these two handles reach Chromium; nothing else the server holds.
        si.lpAttributeList = {'handle_list': [c_r, e_w]}
        argv = list(args)
        argv.insert(1, '--remote-debugging-pipe')
        argv.insert(2, f'--remote-debugging-io-pipes={int(c_r)},{int(e_w)}')
        proc = subprocess.Popen(argv, startupinfo=si, close_fds=True, **kw)
    except BaseException:
        for h in (c_r, c_w, e_r, e_w):
            _winapi.CloseHandle(h)
        raise
    _winapi.CloseHandle(c_r)
    _winapi.CloseHandle(e_w)
    wfd = msvcrt.open_osfhandle(c_w, os.O_WRONLY)
    rfd = msvcrt.open_osfhandle(e_r, os.O_RDONLY)
    return proc, PipeTransport(rfd, wfd)


def _spawn_posix(args, popen_kwargs):
    import fcntl
    c_r, c_w = os.pipe()
    e_r, e_w = os.pipe()
    # Chromium reads CDP from fd 3 and writes it to fd 4. Move our child ends
    # clear of 3/4 first so the dup2s below cannot clobber each other.
    c_r = fcntl.fcntl(c_r, fcntl.F_DUPFD, 10) if c_r < 10 else c_r
    e_w = fcntl.fcntl(e_w, fcntl.F_DUPFD, 10) if e_w < 10 else e_w

    def _child():
        os.dup2(c_r, 3)
        os.dup2(e_w, 4)

    try:
        argv = list(args)
        argv.insert(1, '--remote-debugging-pipe')
        proc = subprocess.Popen(argv, pass_fds=(c_r, e_w), preexec_fn=_child, **popen_kwargs)
    except BaseException:
        for fd in (c_r, c_w, e_r, e_w):
            os.close(fd)
        raise
    os.close(c_r)
    os.close(e_w)
    return proc, PipeTransport(e_r, c_w)

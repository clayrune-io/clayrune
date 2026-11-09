"""Owned HTTP session with cancellation across headers and streamed body reads.

Socket idle timeouts alone cannot bound a trickling response. The calling
thread is the watchdog; cancellation shuts down the active socket to interrupt
urllib3's read before closing the session. No global connection classes change.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Any, cast

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

from mc.core import _log


def post(url: str, *, deadline: float, **kwargs: Any) -> requests.Response:
    deadline = min(deadline, time.monotonic() + float(kwargs['timeout']))
    cancelled = threading.Event()
    done = threading.Event()
    lock = threading.Lock()
    sockets: list[Any] = []
    result: list[requests.Response] = []
    errors: list[Exception] = []

    def abort(sock: Any) -> None:
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError as exc:
                # Shutdown can race an already-closed peer; still close locally.
                _log(f'[transform-http] socket shutdown failed: {type(exc).__name__}', flush=True)
            finally:
                sock.close()

    class TrackedHTTP(HTTPConnection):
        def connect(self) -> None:
            if cancelled.is_set():
                raise TimeoutError('text transform deadline exceeded')
            super().connect()
            with lock:
                sockets.append(self.sock)
            if cancelled.is_set():
                abort(self.sock)
                raise TimeoutError('text transform deadline exceeded')

    class TrackedHTTPS(HTTPSConnection):
        def connect(self) -> None:
            if cancelled.is_set():
                raise TimeoutError('text transform deadline exceeded')
            super().connect()
            with lock:
                sockets.append(self.sock)
            if cancelled.is_set():
                abort(self.sock)
                raise TimeoutError('text transform deadline exceeded')

    class HTTPPool(HTTPConnectionPool):
        ConnectionCls = cast(Any, TrackedHTTP)

    class HTTPSPool(HTTPSConnectionPool):
        ConnectionCls = cast(Any, TrackedHTTPS)

    class Adapter(HTTPAdapter):
        def init_poolmanager(self, *args: Any, **kw: Any) -> None:
            super().init_poolmanager(*args, **kw)
            self.poolmanager.pool_classes_by_scheme = {'http': HTTPPool, 'https': HTTPSPool}

        def proxy_manager_for(self, proxy: str, **kw: Any) -> Any:
            manager = super().proxy_manager_for(proxy, **kw)
            manager.pool_classes_by_scheme = {'http': HTTPPool, 'https': HTTPSPool}
            return manager

    session = requests.Session()
    session.mount('http://', Adapter())
    session.mount('https://', Adapter())

    def request() -> None:
        try:
            response = session.post(url, stream=True, **kwargs)
            # Consume under the watchdog, including JSON whitespace trickles.
            response.content
            result.append(response)
        except Exception as exc:
            errors.append(exc)
        finally:
            session.close()
            done.set()

    seconds = deadline - time.monotonic()
    if seconds <= 0:
        session.close()
        raise TimeoutError('text transform deadline exceeded')
    worker = threading.Thread(target=request, name='transform-http', daemon=True)
    worker.start()
    if not done.wait(max(0, deadline - time.monotonic())):
        cancelled.set()
        with lock:
            active = list(sockets)
        for sock in active:
            abort(sock)
        session.close()
        # Do not let a blocked resolver/TLS handshake hold up finalization.
        worker.join(timeout=0.05)
        raise TimeoutError('text transform deadline exceeded')
    if time.monotonic() >= deadline:
        raise TimeoutError('text transform deadline exceeded')
    if errors:
        raise errors[0]
    return result[0]

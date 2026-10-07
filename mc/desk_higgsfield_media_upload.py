"""Human-render media upload: captured schemas, pinned public HTTPS PUT, confirm."""
from __future__ import annotations

import http.client
import socket
import ssl
from pathlib import Path
from urllib.parse import urlsplit

from mc.desk_connect import net_guard
from mc.desk_mcp_schema_validation import validate

MAX_BYTES = 20 * 1024 * 1024


def put(url: str, data: bytes, mime: str) -> None:
    """No redirect, proxy, token or second DNS lookup. TLS still authenticates the hostname."""
    from mc import desk_engines as eng
    conn = None
    try:
        u = urlsplit(url)
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.fragment \
                or u.port not in (None, 443) or '\\' in url or any(ord(c) < 33 for c in url):
            raise ValueError('unsafe upload address')
        if not data or len(data) > MAX_BYTES:
            raise ValueError('picture is empty or larger than 20 MB')
        addresses = net_guard.resolve_public(u.hostname)
        conn = http.client.HTTPSConnection(u.hostname, timeout=120, context=ssl.create_default_context())
        # http.client's TLS SNI/certificate check uses conn.host, while the TCP socket uses only vetted IPs.
        def connect(_address, timeout=120, source_address=None):
            last = None
            for ip in addresses:
                try:
                    return socket.create_connection((ip, 443), timeout, source_address)
                except OSError as e:
                    last = e
            raise OSError('upload host did not answer') from last
        setattr(conn, '_create_connection', connect)
        path = u.path or '/'
        if u.query:
            path += '?' + u.query
        conn.request('PUT', path, body=data, headers={'Content-Type': mime, 'Content-Length': str(len(data))})
        response = conn.getresponse()
        # The confirm tool explicitly requires PUT HTTP 200; no response body is needed.
        if response.status != 200:
            raise ValueError('picture upload was refused')
    except (ValueError, OSError, http.client.HTTPException, net_guard.Blocked) as e:
        raise eng.EngineError('engine', 'Higgsfield could not safely upload the picture; no generation was submitted') from e
    finally:
        if conn is not None:
            conn.close()


def checked_call(doc: dict, call, name: str, args: dict, *, submitted: bool = False) -> dict:
    from mc import desk_engines as eng
    try:
        validate(doc, name, 'inputSchema', args)
    except (TypeError, ValueError, KeyError):
        raise eng.EngineError('engine', 'Higgsfield has not supplied a usable picture request contract') from None
    try:
        data = eng._mcp_data(call(name, args))
    except eng.EngineError as e:
        if args.get('params', {}).get('get_cost') is True:
            raise
        if not submitted:
            raise eng.EngineError(e.kind, 'Higgsfield could not prepare the picture; no generation was submitted') from e
        raise
    try:
        validate(doc, name, 'outputSchema', data)
    except (TypeError, ValueError, KeyError):
        message = ('Higgsfield returned an unexpected generation response; check its dashboard before retrying'
                   if submitted else 'Higgsfield returned an unexpected picture response; no generation was submitted')
        raise eng.EngineError('engine', message, definitive=not submitted) from None
    if data.get('error'):
        if args.get('params', {}).get('get_cost') is True and not submitted:
            from mc.desk_higgsfield_quote_response import report
            raise eng.EngineError('engine', report(data))
        raise eng.EngineError('engine', 'Higgsfield refused the picture request', definitive=not submitted)
    return data


def upload(doc: dict, call, ref: dict) -> str:
    from mc import desk_engines as eng
    data, mime = eng._read_asset(ref)
    if not data or len(data) > MAX_BYTES:
        raise eng.EngineError('invalid_input', 'The picture is empty or larger than 20 MB')
    # Use a neutral filename with the file's extension; local directories never leave this machine.
    out = checked_call(doc, call, 'media_upload', {'filename': 'picture' + Path(ref['path']).suffix.lower(),
                                                'content_type': mime})
    rows = out.get('uploads')
    if not isinstance(rows, list) or len(rows) != 1:
        raise eng.EngineError('engine', 'Higgsfield supplied no single picture upload; no generation was submitted')
    row = rows[0]
    if not isinstance(row, dict) or not isinstance(row.get('upload_url'), str) \
            or not isinstance(row.get('media_id'), str) or not row['media_id'] \
            or row.get('method') != 'PUT' or row.get('content_type') != mime:
        raise eng.EngineError('engine', 'Higgsfield supplied an unexpected picture upload; no generation was submitted')
    put(row['upload_url'], data, mime)
    confirmed = checked_call(doc, call, 'media_confirm', {'type': 'image', 'media_id': row['media_id']})
    results = confirmed.get('results')
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict) \
            or results[0].get('media_id') != row['media_id'] or not isinstance(results[0].get('status'), str) \
            or not results[0]['status'] or results[0]['status'].lower() in ('failed', 'error', 'pending', 'uploading') \
            or results[0].get('type', 'image') != 'image':
        raise eng.EngineError('engine', 'Higgsfield did not confirm the picture; no generation was submitted')
    return results[0]['media_id']

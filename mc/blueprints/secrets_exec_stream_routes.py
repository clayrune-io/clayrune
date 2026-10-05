"""``POST /api/secrets/exec-stream`` and its two companions — the HTTP face of the
server-parented streaming exec (MC-1047). The engine is ``mc/secrets_exec_stream.py``;
the client is ``mc/secrets_exec_stream_client.py``; the frame format is
``mc/secrets_exec_stream_wire.py``.

    POST /api/secrets/exec-stream                 start the child; the response is the
                                                  frame stream (stdout, stderr, exit)
    POST /api/secrets/exec-stream/<id>/stdin      write the body to the child's stdin;
                                                  ``?eof=1`` closes stdin afterwards
    POST /api/secrets/exec-stream/<id>/end        kill the child and end the session

Same gate stack as ``/api/secrets/exec`` and for the same reason: loopback only, no
Cloudflare/tunnel header, the per-boot token (``secrets_routes._exec_gate_refusal``).
Nothing here returns a secret: the response carries the child's OWN output, redacted of
every value the vault has dispensed. The session id in the two companion paths is a
192-bit random capability that exists only for the lifetime of one child, on top of the
gate.
"""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from mc import secrets_exec_stream as stream
from mc import secrets_exec_stream_wire as wire
from mc import secrets_store as vault
from mc.blueprints.secrets_routes import _exec_gate_refusal

bp = Blueprint('secrets_exec_stream_routes', __name__)


class _Body:
    """The response body. A bare generator is not enough: if the client goes away
    before the first frame is pulled, the server calls ``close()`` on a generator
    that never started, which runs none of its ``finally`` — and the child would
    live until the attach deadline. ``close()`` here always ends the session."""

    def __init__(self, session: stream.StreamSession) -> None:
        self._session = session
        self._frames = session.frames()

    def __iter__(self):
        yield wire.pack(wire.CH_SESSION, self._session.id.encode('ascii'))
        yield from self._frames

    def close(self) -> None:
        self._frames.close()
        self._session.teardown('client_gone')


@bp.route(wire.PATH, methods=['POST'])
def api_secrets_exec_stream():
    refusal = _exec_gate_refusal()
    if refusal is not None:
        return refusal
    data = request.get_json(silent=True) or {}
    try:
        launch = stream.resolve_launch(data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except vault.VaultLocked:
        return jsonify({'error': 'vault_locked',
                        'message': 'the vault is locked — unlock it from '
                                   'Settings > Vault'}), 423
    except vault.SecretsError as e:
        return jsonify({'error': str(e)}), 400
    try:
        session = stream.start(launch)
    except stream.TooManySessions:
        return jsonify({'error': 'too_many_sessions',
                        'message': 'too many streaming children are already running'}), 429
    except OSError as e:
        return jsonify({'error': f'failed to start command: {e}'}), 400
    session.attach()
    resp = Response(_Body(session), mimetype='application/octet-stream')
    resp.headers['Cache-Control'] = 'no-store'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp


_NO_SESSION = ({'error': 'no_such_session'}, 404)


@bp.route(wire.PATH + '/<session_id>/stdin', methods=['POST'])
def api_secrets_exec_stream_stdin(session_id: str):
    refusal = _exec_gate_refusal()
    if refusal is not None:
        return refusal
    session = stream.get(session_id)
    if session is None:
        return _NO_SESSION
    if request.content_length is not None and request.content_length > stream.MAX_STDIN_POST:
        return jsonify({'error': 'body too large'}), 413
    body = request.get_data(cache=False)
    try:
        if body:
            session.write_stdin(body)
        if request.args.get('eof'):
            session.close_stdin()
    except stream.StdinClosed:
        return jsonify({'error': 'stdin_closed'}), 410
    return '', 204


@bp.route(wire.PATH + '/<session_id>/end', methods=['POST'])
def api_secrets_exec_stream_end(session_id: str):
    refusal = _exec_gate_refusal()
    if refusal is not None:
        return refusal
    session = stream.get(session_id)
    if session is None:
        return _NO_SESSION
    session.teardown('client_end')
    return '', 204

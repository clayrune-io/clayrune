from types import SimpleNamespace

import pytest
from flask import Flask

from mc.blueprints import terminal_routes


@pytest.mark.parametrize('backend', ['PTY', 'pipe'])
def test_failed_terminal_stdin_write_is_logged(monkeypatch, backend):
    def fail(_text):
        raise BrokenPipeError('pipe closed')

    writer = SimpleNamespace(write=fail)
    session = {'status': 'running'}
    if backend == 'PTY':
        session['pty'] = writer
    else:
        session['proc'] = SimpleNamespace(stdin=writer)
    logs = []
    monkeypatch.setattr(terminal_routes, 'terminal_sessions', {'terminal': session})
    monkeypatch.setattr(terminal_routes, '_log', lambda message, **kw: logs.append(message), raising=False)
    app = Flask(__name__)
    app.register_blueprint(terminal_routes.bp)

    response = app.test_client().post('/api/terminal/stdin',
                                      json={'session_id': 'terminal', 'text': 'input'})

    assert response.get_json() == {'ok': True}
    assert logs == [f'[terminal] {backend} stdin write failed for terminal: pipe closed']

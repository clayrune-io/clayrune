import http.client
from types import SimpleNamespace

from mc import secrets_exec_stream_client as client


def test_stdin_relay_reports_transport_failure_without_aborting(monkeypatch, capsys):
    def failed_post(*args):
        raise http.client.HTTPException('lost connection')

    monkeypatch.setattr(client.http.client, 'HTTPConnection',
                        lambda *_args, **_kwargs: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(client.sys, 'stdin', SimpleNamespace(fileno=lambda: 0))
    monkeypatch.setattr(client.os, 'read', lambda *_: b'input')
    monkeypatch.setattr(client, '_post', failed_post)

    client._forward_stdin(5199, 'test-token', 'test-session')

    assert 'stdin relay failed: lost connection' in capsys.readouterr().err

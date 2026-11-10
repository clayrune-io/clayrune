"""Model selection must report every failed persistence stage before pinning."""
from threading import RLock
from types import SimpleNamespace

from flask import Flask
import pytest

from tests.test_revive_non_claude_character_ref import ar


@pytest.mark.parametrize('fault', ['read', 'snapshot-save', 'choice-save', 'none'])
def test_storage_failure_returns_503_and_keeps_pin(ar, monkeypatch, fault):
    session = dict(project_id='p1', session_id='s1', provider='claude',
                   pinned_model='sonnet')
    ar.agent_sessions['s1'] = session
    monkeypatch.setattr(ar, 'get_manager', lambda _: SimpleNamespace(lock=RLock()))
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a: None)
    logs, reads, saves = [], [], []
    monkeypatch.setattr(ar, '_log', logs.append)

    def load(_, *, strict=False):
        assert strict is True
        reads.append(True)
        if fault == 'read':
            raise PermissionError('storage unavailable')
        return []

    def save(*args):
        saves.append(True)
        if fault == 'snapshot-save' or (fault == 'choice-save' and len(saves) == 2):
            raise PermissionError('storage unavailable')

    monkeypatch.setattr(ar, '_load_agent_log', load)
    monkeypatch.setattr(ar, '_save_agent_log', save)
    app = Flask(__name__)
    with app.test_request_context(json={'model': 'opus'}):
        try:
            response = app.make_response(ar.agent_set_model('p1', 's1'))
        finally:
            if fault != 'none':
                assert session['pinned_model'] == 'sonnet'
    assert response.status_code == (200 if fault == 'none' else 503)
    if fault == 'none':
        assert response.get_json()['ok'] is True
        assert session['pinned_model'] == 'opus'
        assert logs == []
    else:
        assert response.get_json() == {'error': 'could not save model choice'}
        assert any('[model-pin] p1/s1: persistence failed: storage unavailable' in line
                   for line in logs)
    assert len(reads) == (1 if fault == 'snapshot-save' else 2)
    assert len(saves) == {'read': 0, 'snapshot-save': 1, 'choice-save': 2, 'none': 2}[fault]

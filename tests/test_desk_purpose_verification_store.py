"""The sidecar that keeps purpose-verification records across a restart
(mc/desk_connect/purpose_verification_store.py): load, save, the corrupt-file fallback, and
where the file lives. Whether a loaded record still counts is pinned in test_desk_purposes.py.
"""
from __future__ import annotations

import json
import sys
from collections import OrderedDict
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from mc import desk
    from mc.desk_connect import purpose_verification_store as s
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'data' / 'desk.json')
    logs: list[str] = []
    monkeypatch.setattr(s, '_log', lambda msg, *a, **k: logs.append(str(msg)))
    s.logs = logs
    return s


def _rec(n=1, stamps=None):
    return {'fingerprint': f'fp{n}', 'stamps': stamps if stamps is not None else {},
            'at': f'2026-10-06T10:00:0{n}+00:00', 'identity': '@ron'}


def test_the_file_sits_beside_the_desk_store_not_in_the_projects_dir(store, tmp_path):
    assert store.path() == tmp_path / 'data' / 'desk_purpose_verification.json'
    assert store.path().parent.name != 'projects'


def test_unwired_desk_store_persists_nothing(store, monkeypatch):
    from mc import desk
    monkeypatch.setattr(desk, 'STORE_PATH', None)
    assert store.path() is None
    assert store.save(OrderedDict({('a', 'read_own', 'mentions'): _rec()})) is False
    assert len(store.load(10)) == 0


def test_round_trip_keeps_identity_timestamp_and_tuple_stamps(store):
    recs = OrderedDict([
        (('acc1', 'read_own', 'mentions'), _rec(1)),
        (('acc1', 'read_own', 'own_posts'), _rec(2, {'oauth.x': ('2026-01-01T00:00:00+00:00', '2026-02-02T00:00:00+00:00'),
                                                     'gone': None})),
    ])
    assert store.save(recs) is True
    got = store.load(10)
    assert list(got) == list(recs)                       # order (oldest first) kept
    assert got == recs                                    # stamps come back as tuples, so == the live comparison holds
    assert got[('acc1', 'read_own', 'mentions')]['identity'] == '@ron'
    assert got[('acc1', 'read_own', 'mentions')]['at'] == '2026-10-06T10:00:01+00:00'


def test_save_is_atomic_json_with_no_stray_temp_files(store):
    store.save(OrderedDict({('a', 'read_own', 'mentions'): _rec()}))
    data = json.loads(store.path().read_text(encoding='utf-8'))
    assert data['version'] == 1 and len(data['records']) == 1
    assert [p.name for p in store.path().parent.iterdir()] == [store.FILE_NAME]


def test_missing_file_loads_empty(store):
    assert len(store.load(10)) == 0
    assert store.logs == []


@pytest.mark.parametrize('body', ['{not json', '', '[]', '{"records": "nope"}', '{"version": 1}'])
def test_corrupt_file_loads_empty_and_is_set_aside_not_overwritten(store, body):
    p = store.path()
    p.parent.mkdir(parents=True)
    p.write_text(body, encoding='utf-8')
    assert len(store.load(10)) == 0
    assert not p.exists()
    assert (p.parent / (store.FILE_NAME + '.corrupt')).read_text(encoding='utf-8') == body
    assert any('unreadable' in m for m in store.logs)


def test_a_malformed_entry_is_dropped_and_the_rest_are_kept(store):
    p = store.path()
    p.parent.mkdir(parents=True)
    good = {'account_id': 'a', 'purpose': 'read_own', 'capability': 'mentions', 'fingerprint': 'fp',
            'stamps': {}, 'at': '2026-10-06T10:00:00+00:00', 'identity': ''}
    bad = [dict(good, capability=''), dict(good, stamps=[1]), dict(good, stamps={'n': 'x'}),
           dict(good, at=None), dict(good, fingerprint=None), {k: v for k, v in good.items() if k != 'identity'},
           'text', None]
    p.write_text(json.dumps({'version': 1, 'records': [bad[0], good, *bad[1:]]}), encoding='utf-8')
    got = store.load(10)
    assert list(got) == [('a', 'read_own', 'mentions')]
    assert any('dropped 8' in m for m in store.logs)


def test_load_keeps_only_the_newest_up_to_the_limit(store):
    recs = OrderedDict((('a', 'read_own', f'c{i}'), _rec(1)) for i in range(5))
    store.save(recs)
    assert list(store.load(2)) == [('a', 'read_own', 'c3'), ('a', 'read_own', 'c4')]


def test_a_failed_write_is_logged_and_keeps_the_previous_file(store, monkeypatch):
    store.save(OrderedDict({('a', 'read_own', 'mentions'): _rec(1)}))
    before = store.path().read_text(encoding='utf-8')

    def boom(*a, **k):
        raise OSError('disk full')
    monkeypatch.setattr(store, 'write_json_atomic', boom)
    assert store.save(OrderedDict({('b', 'read_own', 'mentions'): _rec(2)})) is False
    assert store.path().read_text(encoding='utf-8') == before
    assert any('could not be saved' in m for m in store.logs)

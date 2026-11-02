"""Per-assignment role state: crash safety, fail-closed reads, claims, watermark.

Temporary directories only; nothing touches the live data root.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess

import pytest

from mc.steward_role_model import RoleError
from mc.steward_role_state import RoleStateStore, StateError

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return RoleStateStore(tmp_path / 'data')


def test_path_is_outside_data_projects_and_under_steward_roles(store, tmp_path):
    path = store.path('proj-1', 'sched_1')
    assert path == (tmp_path / 'data' / 'steward' / 'roles' / 'proj-1' / 'sched_1.json').resolve()
    assert 'projects' not in path.relative_to((tmp_path / 'data').resolve()).parts


@pytest.mark.parametrize('pid,sid', [('../x', 's'), ('p', '..\\s'), ('p/q', 's'), ('', 's'), ('p', 'a.b')])
def test_hostile_ids_are_rejected(store, pid, sid):
    with pytest.raises(RoleError):
        store.path(pid, sid)


def test_never_written_assignment_reads_as_fresh_default_without_creating_a_file(store):
    state = store.read('p', 's')
    assert state['decisions'] == [] and state['claim'] is None and state['version'] == 1
    assert not store.path('p', 's').exists()


def test_round_trip_and_no_leftover_temp_files(store):
    store.record_session('p', 's', 'sess1')
    store.record_session('p', 's', 'sess2')
    store.record_attempt('p', 's', snapshot_hashes={'charter': 'abc'}, now=T0)
    state = store.read('p', 's')
    assert (state['prior_session_id'], state['current_session_id']) == ('sess1', 'sess2')
    assert state['snapshot_hashes'] == {'charter': 'abc'}
    assert [p.name for p in store.path('p', 's').parent.iterdir()] == ['s.json']


def test_nul_bytes_are_refused_not_reset(store):
    store.record_session('p', 's', 'sess1')
    path = store.path('p', 's')
    path.write_bytes(b'\x00' * path.stat().st_size)
    with pytest.raises(StateError, match='NUL'):
        store.read('p', 's')
    with pytest.raises(StateError):
        store.record_session('p', 's', 'sess2')
    assert path.read_bytes().count(b'\x00') == path.stat().st_size  # untouched


def test_corrupt_wrong_version_and_foreign_ids_fail_closed(store):
    store.record_session('p', 's', 'sess1')
    path = store.path('p', 's')
    good = json.loads(path.read_text(encoding='utf-8'))
    path.write_text('{not json', encoding='utf-8')
    with pytest.raises(StateError, match='JSON'):
        store.read('p', 's')
    path.write_text(json.dumps({**good, 'version': 2}), encoding='utf-8')
    with pytest.raises(StateError, match='version'):
        store.read('p', 's')
    path.write_text(json.dumps({**good, 'schedule_id': 'other'}), encoding='utf-8')
    with pytest.raises(StateError, match='do not match'):
        store.read('p', 's')
    path.write_text(json.dumps({**good, 'extra': 1}), encoding='utf-8')
    with pytest.raises(StateError, match='schema'):
        store.read('p', 's')


def test_failed_mutation_leaves_previous_file_intact(store):
    store.record_session('p', 's', 'sess1')
    before = store.path('p', 's').read_text(encoding='utf-8')

    def boom(state):
        state['current_session_id'] = 'half-written'
        raise RuntimeError('mutate failed')
    with pytest.raises(RuntimeError):
        store.update('p', 's', boom)
    assert store.path('p', 's').read_text(encoding='utf-8') == before


def test_mutation_producing_invalid_state_is_not_written(store):
    store.record_session('p', 's', 'sess1')
    before = store.path('p', 's').read_text(encoding='utf-8')
    with pytest.raises(StateError):
        store.update('p', 's', lambda st: st.update(claim={'id': 'x'}))
    with pytest.raises(StateError):
        store.update('p', 's', lambda st: st['snapshot_hashes'].update(k=5))
    assert store.path('p', 's').read_text(encoding='utf-8') == before


def test_claim_is_single_and_never_stolen_by_age(store):
    won, held = store.acquire_claim('p', 's', 'c1', 'timer', T0)
    assert won and held is None
    assert store.read('p', 's')['claim']['state'] == 'preparing'
    won, held = store.acquire_claim('p', 's', 'c2', 'run_now', T0 + timedelta(days=30))
    assert not won and held['id'] == 'c1'
    assert store.release_claim('p', 's', 'c2') is False       # wrong id
    assert store.read('p', 's')['claim']['id'] == 'c1'
    assert store.release_claim('p', 's', 'c1') is True
    assert store.acquire_claim('p', 's', 'c2', 'timer', T0)[0]


def test_claim_state_transitions_and_unknown_state_rejected(store):
    store.acquire_claim('p', 's', 'c1', 'timer', T0)
    assert store.set_claim_state('p', 's', 'c1', 'delivery_unknown')
    assert store.read('p', 's')['claim']['state'] == 'delivery_unknown'
    assert store.set_claim_state('p', 's', 'zzz', 'accepted') is False
    with pytest.raises(StateError):
        store.set_claim_state('p', 's', 'c1', 'bogus')


def test_watermark_moves_only_on_accepted_delivery_and_only_forward(store):
    store.acquire_claim('p', 's', 'c1', 'timer', T0)
    store.set_claim_state('p', 's', 'c1', 'delivery_unknown')
    assert store.read('p', 's')['refresh_watermark'] is None     # unacknowledged: untouched
    assert store.accept_delivery('p', 's', 'c1', T0)
    state = store.read('p', 's')
    assert state['claim']['state'] == 'accepted' and state['refresh_watermark']
    first = state['refresh_watermark']
    assert store.accept_delivery('p', 's', 'wrong', T0 + timedelta(hours=1)) is False
    assert store.accept_delivery('p', 's', 'c1', T0 - timedelta(hours=1))
    assert store.read('p', 's')['refresh_watermark'] == first    # never backwards
    assert store.accept_delivery('p', 's', 'c1', T0 + timedelta(hours=1))
    assert store.read('p', 's')['refresh_watermark'] != first


def test_decisions_are_bounded_to_most_recent(store):
    def fill(state):
        for i in range(250):
            state['decisions'].append({
                'id': f'D{i}', 'key': 'k', 'route': 'email', 'status': 'resolved', 'reason': '',
                'subject': 's', 'created_at': T0.isoformat(), 'email_attempted_at': None,
                'cap_counted_at': None, 'sent_at': None, 'attempts': 0, 'reply_decision': None,
                'reply_checked_at': None, 'last_reported_status': 'resolved'})
    state = store.update('p', 's', fill)
    assert len(state['decisions']) == 200 and state['decisions'][-1]['id'] == 'D249'


def test_decision_record_rejects_unknown_fields_such_as_a_mail_body(store):
    def add(state):
        state['decisions'].append({'id': 'D1', 'body': 'raw email text'})
    with pytest.raises(StateError):
        store.update('p', 's', add)


def test_state_dir_is_ignored_by_git():
    root = Path(__file__).resolve().parents[1]
    out = subprocess.run(['git', 'check-ignore', 'data/steward/roles/p/s.json'],
                         cwd=root, capture_output=True, text=True)
    assert out.returncode == 0

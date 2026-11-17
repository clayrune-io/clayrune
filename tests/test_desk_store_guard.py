"""MC-1187 follow-up F3: an unreadable desk.json that cannot be moved aside.

On Windows the move can fail (another process holds the file). The file then stays
at `desk.json`, and writers used to go ahead: the next write replaced it with an
empty store. Now writers refuse with `StoreUnreadable` exactly as after a successful
quarantine, and the unreadable file is never overwritten or deleted. The failure is
simulated by making the rename raise `PermissionError`, not with a real lock.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mc import desk
from mc import desk_store_guard as guard


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(desk, 'SIGNALS_PATH', tmp_path / 'desk_signals.jsonl')
    guard._unmoved.clear()
    yield desk
    guard._unmoved.clear()


def _locked_corrupt(store, monkeypatch):
    store.create_campaign('t', 'thesis')
    good = store.STORE_PATH.read_text(encoding='utf-8')
    broken = good[:len(good) // 2]
    store.STORE_PATH.write_text(broken, encoding='utf-8')

    def _denied(self, target):
        raise PermissionError(32, 'The process cannot access the file')
    monkeypatch.setattr(Path, 'rename', _denied)
    monkeypatch.setattr(Path, 'replace', _denied)
    return good, broken


def test_writers_refuse_when_the_unreadable_file_cannot_be_moved(store, monkeypatch):
    _good, broken = _locked_corrupt(store, monkeypatch)
    assert store.list_campaigns() == []                      # reads still degrade to empty
    assert store.STORE_PATH.exists() and not list(store.STORE_PATH.parent.glob('desk.json.unreadable-*'))
    with pytest.raises(store.StoreUnreadable):
        store.create_campaign('t2', 'thesis2')
    with pytest.raises(store.StoreUnreadable):
        store.record_published(platform='x', voice='ron', body='b')
    assert store.STORE_PATH.read_text(encoding='utf-8') == broken, 'the unreadable file is never overwritten'


def test_the_refusal_names_the_file_and_not_the_directory(store, monkeypatch):
    _locked_corrupt(store, monkeypatch)
    store.list_campaigns()
    with pytest.raises(store.StoreUnreadable) as e:
        store.create_campaign('t2', 'thesis2')
    assert str(store.STORE_PATH.parent) not in str(e.value)


def test_a_readable_store_back_in_place_lifts_the_refusal(store, monkeypatch):
    good, _broken = _locked_corrupt(store, monkeypatch)
    store.list_campaigns()
    with pytest.raises(store.StoreUnreadable):
        store.create_campaign('t2', 'thesis2')
    store.STORE_PATH.write_text(good, encoding='utf-8')      # the lock cleared and a person restored it
    assert store.create_campaign('t2', 'thesis2')['title'] == 't2'


def test_a_set_aside_that_works_still_behaves_as_before(store):
    store.create_campaign('t', 'thesis')
    good = store.STORE_PATH.read_text(encoding='utf-8')
    store.STORE_PATH.write_text(good[:len(good) // 2], encoding='utf-8')
    assert store.list_campaigns() == []
    assert not store.STORE_PATH.exists() and guard._unmoved == set()
    with pytest.raises(store.StoreUnreadable):
        store.create_campaign('t2', 'thesis2')

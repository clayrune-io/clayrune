from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local

from beacon import store
from mc import atomic_json


def test_concurrent_heartbeat_writes_use_distinct_temps(tmp_path, monkeypatch):
    target = tmp_path / 'p1.json'
    monkeypatch.setattr(store, 'heartbeat_path', lambda _project: target)
    barrier = Barrier(2)
    replace = atomic_json.os.replace
    sources = []
    writer = local()

    def sync_replace(source, dest):
        if not getattr(writer, 'replacing', False):
            writer.replacing = True
            sources.append(source)
            barrier.wait(timeout=5)
        replace(source, dest)

    monkeypatch.setattr(atomic_json.os, 'replace', sync_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writes = [pool.submit(store.write_heartbeat, 'p1', {'value': n}) for n in range(2)]
        for write in writes:
            write.result(timeout=10)
    assert len(set(sources)) == 2
    assert store.read_heartbeat('p1') in ({'value': 0}, {'value': 1})

import json

from mc.blueprints import floor_routes


def test_floor_label_repair_holds_the_writer_lock(monkeypatch, tmp_path):
    path = tmp_path / 'labels.json'
    path.write_text(json.dumps({'s': {'name': 'Agent', 'avatar': '??'}}), encoding='utf-8')
    monkeypatch.setattr(floor_routes, 'LABELS_PATH', path)
    original = floor_routes._atomic_write_text
    lock_during_write = []

    def track_write(*args, **kwargs):
        lock_during_write.append(floor_routes._labels_lock.locked())
        return original(*args, **kwargs)

    monkeypatch.setattr(floor_routes, '_atomic_write_text', track_write)

    assert floor_routes.read_labels()['s']['avatar'] == ''
    assert lock_during_write == [True]

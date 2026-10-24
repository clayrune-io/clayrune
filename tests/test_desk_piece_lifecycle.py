"""MC-1112: persisted lifecycle, suggestion placement and embedded references."""
from copy import deepcopy
import io
from pathlib import Path
import sys
import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mc import desk, desk_pieces as pieces, desk_piece_lifecycle as lifecycle
from mc import desk_studio_items as items, desk_suggestion_pieces as suggestions
from mc.blueprints import desk_routes, desk_piece_lifecycle_routes, desk_studio_items_routes


@pytest.fixture
def env(tmp_path):
    uploads = tmp_path / 'uploads'; uploads.mkdir()
    desk_routes.wire(load_projects_fn=lambda: [], store_path=tmp_path / 'desk.json',
                     signals_path=tmp_path / 'signals.jsonl', uploads_root=uploads)
    desk.create_campaign('Test', 'Thesis', campaign_id='camp-a', project_id='alpha')
    desk.upsert_presence('alpha', {'accounts': [{'channel_id': 'ch-blog', 'platform': 'blog', 'identity': 'Blog'}]})
    app = Flask(__name__)
    app.register_blueprint(desk_routes.bp)
    app.register_blueprint(desk_piece_lifecycle_routes.bp)
    app.register_blueprint(desk_studio_items_routes.bp)
    return app.test_client(), uploads


def make():
    return pieces.create_piece('camp-a', 'article', 'Article', body='My article')


def test_empty_piece_archive_survives_reload_and_restores(env):
    client, _ = env
    piece = make(); pid = piece['id']
    assert client.post(f'/api/desk/pieces/{pid}/archive').status_code == 200
    assert pieces.get_piece(pid)['archivedAt']
    assert pieces.get_stored_piece(pid)['archive']['versions'] == {}
    assert client.patch(f'/api/desk/pieces/{pid}', json={'body': 'changed'}).status_code == 409
    assert client.post(f'/api/desk/pieces/{pid}/unarchive').get_json()['archivedAt'] is None
    assert pieces.get_piece(pid)['body'] == 'My article'


@pytest.mark.parametrize('state', pieces.VERSION_STATES)
def test_archive_roundtrips_exact_original_versions_and_preserves_history(env, state):
    p = make(); pid = p['id']
    pieces.add_version(pid, 'ch-blog', version_id='v-test', body='Version body')
    with desk._store_lock:
        store = desk._read_store(); version = store['pieces'][pid]['versions'][0]
        version.update(state=state, approved={'body_sha': 'existing-consent', 'by': 'human'}, slot_id='slot')
        before = deepcopy(version); desk._write_store(store)
    lifecycle.archive(pid)
    current = pieces.get_stored_piece(pid)['versions'][0]
    if state in pieces.SENT_STATES or state == 'archived':
        assert current == before
    else:
        assert current['state'] == 'archived'
    with pytest.raises(pieces.PieceError):
        pieces.update_version(pid, 'v-test', {'body': 'cannot alter suspended text'})
    lifecycle.archive(pid, True)
    assert pieces.get_stored_piece(pid)['versions'][0] == before


def test_mixed_post_history_can_advance_while_archived(env):
    pid = make()['id']
    with desk._store_lock:
        store = desk._read_store()
        store['pieces'][pid]['versions'] = [{'id': 'draft', 'state': 'planned'}, {'id': 'sent', 'state': 'submitted'}]
        desk._write_store(store)
    lifecycle.archive(pid)
    pieces.transition_version(pid, 'sent', to='verified_published', expect='submitted', receipt={'url': 'https://example.com/post'})
    lifecycle.archive(pid, True)
    versions = pieces.get_stored_piece(pid)['versions']
    assert versions[0]['state'] == 'planned'
    assert versions[1]['state'] == 'verified_published' and versions[1]['receipt']['url']


def test_delete_undo_uses_existing_trash_and_restores_all_piece_data(env):
    client, uploads = env
    pid = make()['id']
    path = uploads / 'desk/library/image/Pictures/a.png'; path.parent.mkdir(parents=True); path.write_bytes(b'image')
    pieces.add_asset(pid, path='desk/library/image/Pictures/a.png')
    pieces.add_version(pid, 'ch-blog', version_id='version-a', body='real body')
    with desk._store_lock:
        store = desk._read_store(); store.setdefault('storyboards', {})[f'piece:{pid}'] = {'rev': 3, 'scenes': []}
        desk._write_store(store)
    before = pieces.get_stored_piece(pid)
    token = client.post(f'/api/desk/pieces/{pid}/trash').get_json()['token']
    assert pid not in desk._read_store()['pieces'] and path.exists()
    with pytest.raises(pieces.PieceError, match='deleted post'):
        items.delete_file('desk/library/image/Pictures/a.png')
    result = client.post(f'/api/desk/studio/trash/{token}/restore')
    assert result.status_code == 200
    assert pieces.get_stored_piece(pid) == before
    assert desk._read_store()['storyboards'][f'piece:{pid}']['rev'] == 3
    assert client.post(f'/api/desk/studio/trash/{token}/restore').status_code == 404


def test_sent_piece_cannot_be_deleted_and_undo_does_not_approve(env):
    pid = make()['id']
    with desk._store_lock:
        store = desk._read_store(); store['pieces'][pid]['versions'] = [{'id': 'v', 'state': 'scheduled', 'approved': {'by': 'human'}}]
        desk._write_store(store)
    token = lifecycle.trash(pid)['token']; items.restore(token)
    assert pieces.get_stored_piece(pid)['versions'][0]['state'] == 'needs_review'
    with desk._store_lock:
        store = desk._read_store(); store['pieces'][pid]['versions'][0]['state'] = 'sending'; desk._write_store(store)
    with pytest.raises(pieces.PieceError): lifecycle.trash(pid)


def test_suggestion_materializes_without_channel_and_then_saves_version(env):
    client, _ = env
    desk.set_suggestions('camp-a', {'what': [{'title': 'Nothing gets lost', 'channel_id': 'ch-blog', 'because': []}]})
    original = deepcopy(desk._read_store()['campaigns']['camp-a']['suggestions'])
    projected = desk.v1_workspace([])['pieces']
    assert projected[0]['versions'] == [] and projected[0]['suggestion']['channelId'] == 'ch-blog'
    result = client.post('/api/desk/campaigns/camp-a/materialize', json={'title': 'Nothing gets lost', 'channel_id': 'ch-blog'})
    assert result.status_code == 200
    piece = result.get_json(); assert piece['versions'] == []
    result = client.post(f"/api/desk/pieces/{piece['id']}/versions", json={'account_id': 'ch-blog'})
    assert result.status_code == 201
    again = suggestions.materialize('camp-a', 'Nothing gets lost', 'ch-blog')
    assert again['id'] == piece['id'] and len(again['versions']) == 1
    assert len(desk._read_store()['pieces']) == 1
    assert desk._read_store()['campaigns']['camp-a']['suggestions'] == original
    assert len(desk.v1_workspace([])['pieces']) == 1


def test_suggestion_unknown_refused_without_piece_or_suggestion_loss(env):
    client, _ = env
    before = deepcopy(desk._read_store())
    assert client.post('/api/desk/campaigns/camp-a/materialize', json={'title': 'missing'}).status_code == 409
    assert desk._read_store() == before


def test_deleting_materialized_suggestion_does_not_resurrect_it_or_delete_original(env):
    desk.set_suggestions('camp-a', {'what': [{'title': 'Keep original', 'channel_id': 'ch-blog'}]})
    original = deepcopy(desk._read_store()['campaigns']['camp-a']['suggestions'])
    pid = suggestions.materialize('camp-a', 'Keep original')['id']
    token = lifecycle.trash(pid)['token']
    assert desk.v1_workspace([])['pieces'] == []
    with pytest.raises(pieces.PieceError, match='already used'):
        suggestions.materialize('camp-a', 'Keep original')
    assert desk._read_store()['campaigns']['camp-a']['suggestions'] == original
    items.restore(token)
    assert suggestions.materialize('camp-a', 'Keep original')['id'] == pid


def test_archived_video_storyboard_is_readable_but_not_writable(env):
    from mc import desk_storyboard
    pid = pieces.create_piece('camp-a', 'video', 'Video')['id']
    lifecycle.archive(pid)
    assert desk_storyboard.get_storyboard('piece', pid)
    with pytest.raises(pieces.PieceError, match='restore this archived'):
        desk_storyboard.put_storyboard('piece', pid, {'rev': 0, 'scenes': [], 'pending_edits': []})


def test_embedded_library_documents_are_listed_and_protected(env):
    _, uploads = env
    path = uploads / 'desk/library/file/Docs/guide.pdf'; path.parent.mkdir(parents=True); path.write_bytes(b'%PDF')
    pid = make()['id']; rel = 'desk/library/file/Docs/guide.pdf'
    pieces.update_piece(pid, {'body': f'Intro\n[Guide]({rel})\nEnd'})
    files = pieces.materials()['library']['file']
    assert files[0]['items'][0]['path'] == rel
    assert files[0]['items'][0]['play'].startswith('/api/serve-file?')
    with pytest.raises(pieces.PieceError): items.delete_file(rel)
    lifecycle.archive(pid)
    assert rel in items._references(desk._read_store())[0]


def test_document_discovery_cannot_escape_library_within_uploads(env, monkeypatch):
    from mc import desk_article_media
    _, uploads = env
    path = uploads / 'desk/library/file/Docs/guide.pdf'; path.parent.mkdir(parents=True); path.write_bytes(b'%PDF')
    outside = uploads / 'private/other.pdf'; outside.parent.mkdir(); outside.write_bytes(b'private')
    # The general uploads resolver allows this path. The library decorator
    # must still refuse it, as when a library directory resolves through a link.
    monkeypatch.setattr(pieces, '_resolve_under_uploads', lambda rel: outside)
    assert desk_article_media.decorate({}) == {}

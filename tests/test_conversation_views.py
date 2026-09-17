"""A fake provider must feed history and memory without feature-side changes."""
from pathlib import Path
import ast
import json

import pytest

from mc.conversation_store import ConversationEvent, ConversationStore
from mc.conversation_views import history_events, scribe_lines


def populated_store(tmp_path: Path):
    store = ConversationStore(tmp_path / 'journal.sqlite3')
    token = store.begin_attempt(
        'project', 'chat', user_message={
            'text': 'Keep all of this\nincluding whitespace and 🧱',
            'attachments': [{'id': 'asset-1', 'name': 'diagram.png'}],
        }, engine={'provider': 'test-adapter', 'model': 'test-model'}, event_id='request-1')
    assert token is not None
    for event_id, kind, payload in [
        ('call', 'tool_call', {'call_id': 'call-1', 'name': 'read', 'input': {'path': 'example'}}),
        ('result', 'tool_result', {'call_id': 'call-1', 'output': 'START' + 'x' * 12000 + 'END'}),
        ('reply', 'assistant_message', {'text': 'Answer\n' + 'y' * 5000}),
        ('usage', 'usage', {'input_tokens': 12}),
    ]:
        store.append_event(token, event_id=event_id, kind=kind, payload=payload)
    return store


def test_reopened_full_history_and_scribe_share_one_source(tmp_path):
    populated_store(tmp_path)
    store = ConversationStore(tmp_path / 'journal.sqlite3')
    events = list(history_events(store, 'project', 'chat', batch_size=2))
    assert [e.kind for e in events] == [
        'attempt_started', 'tool_call', 'tool_result', 'assistant_message', 'usage']
    assert len(events[2].payload['output']) == 12008
    assert events[0].payload['user_message']['attachments'][0]['id'] == 'asset-1'
    before = store.read_events('project', 'chat')
    lines = list(scribe_lines(events, detail_limit=100))
    assert lines[0] == 'USER: Keep all of this\nincluding whitespace and 🧱'
    assert lines[1] == 'ATTACHMENT: diagram.png'
    assert lines[2] == 'ACTION read [call call-1]: {"path": "example"}'
    assert '[11908 chars elided]' in lines[3]
    assert lines[4] == 'ASSISTANT: Answer\n' + 'y' * 5000
    assert len(lines) == 5  # Usage never masquerades as model conversation.
    assert store.read_events('project', 'chat') == before


def test_history_cursor_and_project_isolation(tmp_path):
    store = populated_store(tmp_path)
    full = list(history_events(store, 'project', 'chat', batch_size=1))
    assert list(history_events(store, 'project', 'chat', after=full[1].sequence)) == full[2:]
    assert list(history_events(store, 'other-project', 'chat')) == []


def test_deleted_history_never_enters_scribe(tmp_path):
    store = populated_store(tmp_path)
    store.delete_conversation('project', 'chat')
    assert list(scribe_lines(history_events(store, 'project', 'chat'))) == []


def test_invalid_projection_bounds(tmp_path):
    store = ConversationStore(tmp_path / 'unused.sqlite3')
    for value in (0, -1, 1001, True):
        with pytest.raises(ValueError):
            list(history_events(store, 'project', 'chat', batch_size=value))
    for value in (0, 1, True, 2.5):
        with pytest.raises(ValueError):
            list(scribe_lines([], detail_limit=value))
    assert not (tmp_path / 'unused.sqlite3').exists()


def test_retry_does_not_invent_a_second_user_message(tmp_path):
    store = ConversationStore(tmp_path / 'retry.sqlite3')
    arguments = dict(user_message={'text': 'One accepted request'},
                     engine={'provider': 'fake'}, request_id='request')
    first = store.begin_attempt('p', 'c', event_id='attempt-1', **arguments)
    token = store.begin_attempt('p', 'c', event_id='attempt-2', expected_attempt=first, **arguments)
    assert token is not None
    store.append_event(token, event_id='answer', kind='assistant_message',
                       payload={'text': 'One response'})
    assert list(scribe_lines(history_events(store, 'p', 'c'))) == [
        'USER: One accepted request', 'ASSISTANT: One response']
    assert len(list(history_events(store, 'p', 'c'))) == 3


@pytest.mark.parametrize('kind,payload', [
    ('assistant_message', {'content': 'must not silently disappear'}),
    ('user_message', 'not an object'),
    ('tool_result', {'output': 'unattributed'}),
    ('tool_call', {'call_id': 'x', 'name': 'read'}),
    ('thinking', {'text': None}),
])
def test_malformed_known_content_fails_projection(kind, payload):
    # Defensive validation protects readers even from older/imported records.
    event = ConversationEvent(1, 'event', 'attempt', kind, 'time', json.dumps(payload))
    with pytest.raises(ValueError):
        list(scribe_lines([event]))


def test_interleaved_results_keep_call_identity_and_failure():
    events = [ConversationEvent(index, str(index), 'attempt', 'tool_result', 'time',
                                json.dumps(payload))
              for index, payload in enumerate([
                  {'call_id': 'second', 'output': 'permission refused', 'is_error': True},
                  {'call_id': 'first', 'output': 'file contents'},
              ], 1)]
    assert list(scribe_lines(events)) == [
        'RESULT [call second error]: permission refused',
        'RESULT [call first]: file contents']


def test_conversation_foundation_has_no_vendor_or_feature_dependencies():
    root = Path(__file__).resolve().parents[1] / 'mc'
    permitted_mc = {'mc.conversation_store', 'mc.conversation_contract'}
    for name in ('conversation_store.py', 'conversation_contract.py', 'conversation_views.py'):
        tree = ast.parse((root / name).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ''
                assert node.level == 0
                if module.startswith('mc.'):
                    assert module in permitted_mc
                assert module not in {'server', 'subprocess'}
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in {'server', 'subprocess', 'mc.agent_runtime'}
                    if alias.name.startswith('mc.'):
                        assert alias.name in permitted_mc

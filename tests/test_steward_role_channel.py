"""Pilot readiness uses metadata only: no credential reads or tool execution."""
import builtins
import copy
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mc import state, steward_role_channel as channel, steward_role_dispatch as dispatch
from mc.blueprints import scheduler_routes as scheduler
from mc.steward_role_model import RoleError
from test_steward_role_dispatch import integrated, env as dispatch_env
from test_steward_role_task import staged  # protected patches in disposable copies only


class NamesOnly:
    """Any value access fails, including Mapping-style membership/get calls."""
    def __init__(self, names=()):
        self.names = names

    def __iter__(self):
        return iter(self.names)

    def __getitem__(self, key):
        raise AssertionError('environment value read')

    def __contains__(self, key):
        raise AssertionError('environment membership may read a value')

    def get(self, *args):
        raise AssertionError('environment value read')


@pytest.fixture
def pilot(tmp_path, monkeypatch):
    root, home = tmp_path / 'pilot', tmp_path / 'home'
    scripts = ('tools/night-review/send_mail.py', 'tools/mail-mcp/read_digest.py',
               'tools/mail-mcp/server.py', 'tools/mail-mcp/imap_quote.py', 'mc/mail_launder.py')
    for relative in scripts:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('raise AssertionError("tool must never execute")', encoding='utf-8')
    creds = home / '.clayrune/night-mail.json'
    creds.parent.mkdir(parents=True)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.setattr(channel.comms, 'REPO_ROOT', root)
    monkeypatch.setattr(channel.comms, 'SEND_MAIL', root / scripts[0])
    monkeypatch.setattr(channel.comms, 'READ_DIGEST', root / scripts[1])
    monkeypatch.setattr(channel, 'os', SimpleNamespace(environ=NamesOnly()))
    monkeypatch.setattr(state, 'CONFIG', {})
    return SimpleNamespace(root=root, home=home, creds=creds, scripts=scripts,
                           project={'id': 'pilot', 'project_path': str(root)},
                           schedule={'id': 'wake'}, detail={'session_id': 's1'})


def ready(pilot, event='first_fire'):
    return scheduler.steward_role_lifecycle(pilot.project, pilot.schedule, event, pilot.detail)


@pytest.mark.parametrize('event', ['first_fire', 'continue', 'successor', 'accepted'])
@pytest.mark.parametrize('source', ['file', 'environment'])
def test_ready_with_metadata_source_and_no_mutation(pilot, monkeypatch, event, source):
    if source == 'file':
        # Not even valid JSON: configuration presence is all this probe attests.
        pilot.creds.write_text('DO NOT READ THIS CREDENTIAL VALUE', encoding='utf-8')
    else:
        monkeypatch.setattr(channel.os, 'environ', NamesOnly(
            ['NIGHT_MAIL_USER', 'NIGHT_MAIL_APP_PASSWORD']))
    before = copy.deepcopy((pilot.project, pilot.schedule, pilot.detail))
    text = ready(pilot, event)
    assert 'metadata present' in text and 'unverified' in text
    assert 'CREDENTIAL VALUE' not in text
    assert (pilot.project, pilot.schedule, pilot.detail) == before


@pytest.mark.parametrize('names', [[], ['NIGHT_MAIL_USER'], ['NIGHT_MAIL_APP_PASSWORD'],
                                  ['NIGHT_MAIL_TO', 'UNRELATED']])
def test_missing_or_partial_source_holds(pilot, monkeypatch, names):
    monkeypatch.setattr(channel.os, 'environ', NamesOnly(names))
    with pytest.raises(RoleError, match='credential source is not configured'):
        ready(pilot)


def test_credential_directory_is_not_a_source(pilot):
    pilot.creds.mkdir()
    with pytest.raises(RoleError, match='credential source'):
        ready(pilot)


@pytest.mark.parametrize('relative', ['tools/night-review/send_mail.py',
    'tools/mail-mcp/read_digest.py', 'tools/mail-mcp/server.py',
    'tools/mail-mcp/imap_quote.py', 'mc/mail_launder.py'])
def test_missing_tool_or_dependency_holds(pilot, relative):
    (pilot.root / relative).unlink()
    pilot.creds.touch()
    with pytest.raises(RoleError, match='script unavailable|dependency unavailable'):
        ready(pilot)


@pytest.mark.parametrize('setting', ['off', 'sms'])
@pytest.mark.parametrize('scope', ['project', 'global'])
def test_disabled_or_unsupported_channel_holds(pilot, setting, scope):
    (pilot.project if scope == 'project' else state.CONFIG)['question_channel'] = setting
    with pytest.raises(RoleError, match='requires email routing'):
        ready(pilot)


def test_project_email_override_uses_existing_routing(pilot):
    state.CONFIG['question_channel'] = 'off'
    pilot.project['question_channel'] = 'email'
    pilot.creds.touch()
    assert 'metadata present' in ready(pilot)


def test_mail_pause_holds_even_with_source(pilot):
    pilot.creds.touch()
    (pilot.creds.parent / 'mail_paused').touch()
    with pytest.raises(RoleError, match='mail is paused'):
        ready(pilot)


@pytest.mark.parametrize('copied_tools', [False, True])
def test_other_projects_cannot_borrow_pilot_tooling(pilot, tmp_path, copied_tools):
    other = tmp_path / 'other'
    other.mkdir()
    if copied_tools:
        for relative in pilot.scripts:
            path = other / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
    pilot.project['project_path'] = str(other)
    pilot.creds.touch()
    with pytest.raises(RoleError, match='not configured for this project'):
        ready(pilot)


@pytest.mark.parametrize('root', [None, '', 12])
def test_missing_project_path_holds(pilot, root):
    pilot.project['project_path'] = root
    with pytest.raises(RoleError, match='project path unavailable'):
        ready(pilot)


def test_unknown_event_holds(pilot):
    with pytest.raises(RoleError, match='event unsupported'):
        ready(pilot, 'unrecognized')


def test_metadata_failure_is_visible_without_exception_content(pilot, monkeypatch):
    def fail(path):
        raise OSError('PRIVATE PATH OR VALUE')
    monkeypatch.setattr(Path, 'is_file', fail)
    with pytest.raises(RoleError, match='filesystem metadata unavailable') as error:
        ready(pilot)
    assert 'PRIVATE' not in str(error.value)


@pytest.mark.parametrize('source', ['file', 'environment'])
def test_never_opens_credentials_or_tools_or_runs_anything(pilot, monkeypatch, capsys, source):
    import imaplib
    import smtplib
    import subprocess
    from mc import mail_launder
    pilot.creds.write_text('SENTINEL SECRET', encoding='utf-8')
    if source == 'environment':
        monkeypatch.setattr(channel.os, 'environ', NamesOnly(
            ['NIGHT_MAIL_USER', 'NIGHT_MAIL_APP_PASSWORD']))
    forbidden = Mock(side_effect=AssertionError('forbidden I/O or execution'))
    # Guard both open APIs (Path.open/read_text use io.open), all tool runners,
    # network entry points and laundering. No file contents are needed at all.
    with monkeypatch.context() as guard:
        guard.setattr(builtins, 'open', forbidden)
        guard.setattr(io, 'open', forbidden)
        guard.setattr(subprocess, 'run', forbidden)
        guard.setattr(subprocess, 'Popen', forbidden)
        guard.setattr(imaplib, 'IMAP4_SSL', forbidden)
        guard.setattr(smtplib, 'SMTP', forbidden)
        guard.setattr(mail_launder, 'launder_mail_digest', forbidden)
        guard.setattr(channel.comms, 'default_mailer', forbidden)
        guard.setattr(channel.comms, 'default_digest_reader', forbidden)
        text = ready(pilot)
    forbidden.assert_not_called()
    output = capsys.readouterr()
    assert 'SENTINEL' not in text + output.out + output.err
    assert list(pilot.creds.parent.iterdir()) == [pilot.creds]


def test_production_fire_uses_callback_and_services_child_gate(pilot, monkeypatch, tmp_path):
    pilot.creds.touch()
    monkeypatch.setattr(scheduler, 'SCHEDULES_PATH', tmp_path / 'data/schedules.json')
    snapshot = SimpleNamespace(handoff=lambda sid: 'child handoff', children=(),
                               unfinished=(), pending_events=())
    children = Mock(return_value=snapshot)
    monkeypatch.setattr(dispatch.SchedulerServices, '_children', children)
    import threading
    monkeypatch.setattr(scheduler, 'get_manager', lambda pid: SimpleNamespace(lock=threading.RLock()))
    drain = Mock()
    monkeypatch.setattr(dispatch.child_work, 'require_drained', drain)
    def prepare(sid, services, store):
        assert sid == 'wake'
        detail = {'predecessor_session_id': 'old'}
        text = services.lifecycle(pilot.project, pilot.schedule, 'successor', detail)
        assert text.startswith('child handoff') and 'metadata present' in text
        assert 'child_work' in detail
        return {'session_id': 'new'}
    monkeypatch.setattr(dispatch, 'prepare_and_fire', prepare)
    assert dispatch.fire(scheduler, 'wake') == {'session_id': 'new'}
    children.assert_called_once_with('pilot', 'old')
    drain.assert_called_once_with(snapshot)


def test_production_fire_preserves_real_channel_blocker(pilot, monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, 'SCHEDULES_PATH', tmp_path / 'data/schedules.json')
    monkeypatch.setattr(dispatch, 'prepare_and_fire', lambda sid, services, store:
        services.lifecycle(pilot.project, pilot.schedule, 'first_fire', {}))
    with pytest.raises(RoleError, match='credential source is not configured'):
        dispatch.fire(scheduler, 'wake')


def test_ready_channel_passes_real_preparation_and_acceptance(pilot, integrated, dispatch_env):
    services, store = dispatch_env
    services.project['project_path'] = str(pilot.root)
    services.lifecycle = scheduler.steward_role_lifecycle
    pilot.creds.touch()
    result = dispatch.prepare_and_fire('wake', services, store)
    assert result['session_id'] == 'session0'
    assert len(services.deliveries) == 1
    assert 'metadata present' in services.deliveries[0][0]
    assert store.read('project', 'wake')['claim']['state'] == 'accepted'


def test_unready_channel_blocks_real_preparation_before_delivery(pilot, integrated, dispatch_env):
    services, store = dispatch_env
    services.project['project_path'] = str(pilot.root)
    services.lifecycle = scheduler.steward_role_lifecycle
    with pytest.raises(RoleError, match='credential source is not configured'):
        dispatch.prepare_and_fire('wake', services, store)
    assert not services.deliveries


def test_execution_error_recognizes_adapter_without_bypassing_sr2(pilot, monkeypatch):
    from steward import core
    # This tests only contract discovery; the real protected files stay untouched.
    monkeypatch.setattr(core, 'build_cycle_task', lambda *, cycle_snapshot=None: '')
    monkeypatch.setattr(dispatch.composer, 'load_directive', lambda: 'temporary directive')
    assert dispatch.execution_error(scheduler) == ''

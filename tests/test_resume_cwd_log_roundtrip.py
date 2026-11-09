"""MC-1167 r3: real Git/log/purge/revive round trips, providers stubbed."""
import json
import subprocess
from pathlib import Path

import pytest

from mc import resume_cwd
from mc.delivery_attempt import DeliveryAttempt
from test_one_process_per_conversation import env  # noqa: F401
from test_resume_cwd_routes import setup, CSID, OWNER  # noqa: F401
from test_resume_cwd_routes import (
    test_live_tree_revive_keeps_isolation_without_claiming_cleanup as exercise_dispatch)
from resume_cwd_fixtures import remove_tree


def borrower(setup, monkeypatch, streaming):
    ar = setup['ar']
    real_log = ar._log_agent_dispatch_pending
    exercise_dispatch(setup, monkeypatch, streaming, 'dispatch')
    monkeypatch.setattr(ar, '_log_agent_dispatch_pending', real_log)
    sid, session = next(iter(setup['sessions'].items()))
    assert sid != OWNER and session['_worktree_borrowed'] is True
    session['claude_session_id'] = CSID  # provider INIT capture
    monkeypatch.setattr(ar, '_write_session_memory', lambda *a, **k: True)
    monkeypatch.setattr(ar, '_notify_agent_spawner', lambda *a, **k: None)
    return sid, session


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('writer', ['pending', 'completion', 'identity'])
def test_borrowed_tree_survives_log_and_purge(setup, monkeypatch, streaming, writer):
    ar = setup['ar']
    sid, session = borrower(setup, monkeypatch, streaming)
    pending = setup['tree'] / 'pending-work.txt'
    pending.write_text('uncommitted work', encoding='utf-8')
    if writer == 'identity':
        # Legacy rows are claimed before execution; INIT may then enrich only
        # that attempt's row, preserving the borrowed-tree round trip below.
        setup['logfile'].write_text(json.dumps([{'session_id': sid}]), encoding='utf-8')
        DeliveryAttempt.capture(session)
    ar._log_agent_dispatch_pending(session, identity_only=(writer == 'identity'),
                                   write_turn_start=False)
    if writer == 'completion':
        ar._log_agent_completion(dict(session, status='completed'))
    row = next(r for r in ar._load_agent_log('p1') if r['session_id'] == sid)
    assert row['worktree_isolated'] is True and row['worktree_borrowed'] is True
    assert row['agent_cwd'] == str(setup['tree'])
    assert row['claude_session_id'] == CSID
    setup['sessions'].clear()
    revived = ar._revive_from_agent_log('p1', sid, 'continue my work', setup['project'])
    assert Path(revived['_agent_cwd']) == setup['tree']
    assert revived['_worktree_borrowed'] is True
    assert (Path(revived['_agent_cwd']) / pending.name).read_text() == 'uncommitted work'
    assert not (setup['pp'] / '.clayrune' / 'agents' / sid).exists()


@pytest.mark.parametrize('streaming', [False, True])
def test_owner_log_purge_recreates_and_keeps_lifecycle(setup, monkeypatch, streaming):
    ar = setup['ar']
    _, session = borrower(setup, monkeypatch, streaming)
    owner = dict(session, session_id=OWNER, _worktree_borrowed=False)
    monkeypatch.setattr(ar._agent_worktree, 'merge_back', lambda *a: ('awaiting_review', 'kept'))
    ar._log_agent_dispatch_pending(owner, write_turn_start=False)
    ar._log_agent_completion(dict(owner, status='completed'))
    row = next(r for r in ar._load_agent_log('p1') if r['session_id'] == OWNER)
    assert row['worktree_borrowed'] is False
    setup['sessions'].clear()
    subprocess.run(['git', '-C', str(setup['pp']), 'worktree', 'remove',
                    '--force', str(setup['tree'])], check=True, capture_output=True)
    revived = ar._revive_from_agent_log('p1', OWNER, 'continue owner work', setup['project'])
    assert revived['_agent_cwd'] == str(setup['tree'])
    assert revived['_worktree_borrowed'] is False
    assert (setup['tree'] / '.git').is_file()
    # Dispatch probe installed a borrower-only merge prohibition; restore a
    # recorder and check an owner still enters identity, review and merge.
    calls = []
    monkeypatch.setattr(ar._isolated_launch, 'held_tree_mismatch', lambda *a: calls.append('identity') or '')
    monkeypatch.setattr(ar._cold_review_runner, 'needs_review', lambda *a: calls.append('review') or False)
    monkeypatch.setattr(ar._agent_worktree, 'merge_back', lambda *a: calls.append('merge') or ('awaiting_review', 'kept'))
    ar._worktree_merge_back_on_end(revived)
    assert calls == ['identity', 'review', 'merge']


@pytest.mark.parametrize('consumer', ['claude', 'vendor', 'delegation', 'scheduler'])
@pytest.mark.parametrize('removed', [False, True])
def test_revival_consumers_honour_borrowed_log(setup, monkeypatch, consumer, removed):
    ar = setup['ar']
    sid, session = borrower(setup, monkeypatch, False)
    if consumer != 'claude':
        session.update(provider='codex', provider_session_id=CSID, claude_session_id='')
    ar._log_agent_dispatch_pending(session, write_turn_start=False)
    rows = ar._load_agent_log('p1')
    rows[0].update(status='completed', agent_model='gpt-6')
    setup['logfile'].write_text(json.dumps(rows), encoding='utf-8')
    setup['sessions'].clear()
    if removed:
        remove_tree(setup['tree'])
    seen = []
    monkeypatch.setattr(ar, '_assert_runtime_project_generation', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_dispatch_agent_internal', lambda *a, **k: seen.append(k) or sid)

    def revive():
        if consumer == 'claude':
            return ar._revive_from_agent_log('p1', sid, 'continue', setup['project'])
        if consumer == 'vendor':
            return ar._revive_non_claude_from_agent_log('p1', sid, 'continue', setup['project'])
        if consumer == 'delegation':
            return ar._revive_parent_for_delegation('p1', sid, 'child done', setup['project'])
        # Same input shape as scheduler cold-resume and Run Now: only the bit.
        return ar._isolated_resume_kwargs('p1', sid, setup['project'], logged=True)

    if removed:
        error = (ar.DeliveryBlocked if consumer == 'delegation' else
                 resume_cwd.ResumeCwdMissing if consumer == 'claude' else
                 ar._isolated_launch.RevivalRefused)
        with pytest.raises(error):
            revive()
        assert seen == [] and not setup['tree'].exists()
    else:
        result = revive()
        if consumer == 'claude':
            assert result['_worktree_borrowed'] is True
            assert result['_agent_cwd'] == str(setup['tree'])
        else:
            kw = result if consumer == 'scheduler' else seen[0]
            assert kw['agent_cwd_override'] == str(setup['tree'])
            assert kw['isolated_override'] is True
            assert kw['borrowed_worktree_override'] is True
    assert not (setup['pp'] / '.clayrune' / 'agents' / sid).exists()


@pytest.mark.parametrize('isolated', [False, True])
@pytest.mark.parametrize('writer', ['pending', 'completion'])
def test_rewrite_keeps_previous_borrowed_tuple(setup, monkeypatch, isolated, writer):
    ar = setup['ar']
    sid, session = borrower(setup, monkeypatch, False)
    ar._log_agent_dispatch_pending(session, write_turn_start=False)
    rebuilt = dict(session, _worktree_isolated=isolated)
    rebuilt.pop('_worktree_borrowed')
    rebuilt.pop('_agent_cwd')
    if writer == 'completion':
        monkeypatch.setattr(ar, '_worktree_merge_back_on_end', lambda *a: None)
        ar._log_agent_completion(dict(rebuilt, status='completed'))
    else:
        ar._log_agent_dispatch_pending(rebuilt, write_turn_start=False)
    row = next(r for r in ar._load_agent_log('p1') if r['session_id'] == sid)
    assert row['worktree_borrowed'] is True and row['agent_cwd'] == str(setup['tree'])


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('provider', ['claude', 'codex'])
def test_revival_override_reaches_dispatch_session(setup, monkeypatch, streaming, provider):
    ar = setup['ar']
    sid, session = borrower(setup, monkeypatch, streaming)
    session.update(provider=provider)
    native_id = CSID if provider == 'claude' else 'codex-thread-unique'
    if provider != 'claude':
        session.update(claude_session_id='', provider_session_id=native_id, _resume_id=native_id)
    ar._log_agent_dispatch_pending(session, write_turn_start=False)
    setup['sessions'].clear()
    kw = ar._isolated_resume_kwargs('p1', sid, setup['project'], logged=True)
    seen = []
    if provider != 'claude':
        monkeypatch.setattr(ar, '_dispatch_via_runtime', lambda *a, **k: seen.append(k) or sid)
    out = ar._dispatch_agent_internal('p1', 'continue', resume_id=native_id,
                                      reuse_session_id=sid, provider_override=provider, **kw)
    assert out == sid
    if provider == 'claude':
        assert setup['sessions'][sid]['_worktree_borrowed'] is True
        assert setup['sessions'][sid]['_agent_cwd'] == str(setup['tree'])
    else:
        assert seen[0]['borrowed_worktree'] is True
        assert seen[0]['agent_cwd'] == str(setup['tree'])


def test_borrowed_flag_without_cwd_refuses_before_ownership(setup, monkeypatch):
    ar = setup['ar']
    sid, session = borrower(setup, monkeypatch, False)
    ar._log_agent_dispatch_pending(session, write_turn_start=False)
    rows = ar._load_agent_log('p1')
    rows[0].pop('agent_cwd')
    setup['logfile'].write_text(json.dumps(rows), encoding='utf-8')
    setup['sessions'].clear()
    with pytest.raises(resume_cwd.ResumeCwdMissing):
        ar._revive_from_agent_log('p1', sid, 'keep my work', setup['project'])
    assert not (setup['pp'] / '.clayrune' / 'agents' / sid).exists()

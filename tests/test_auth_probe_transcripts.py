"""The auth probe must not leave conversations in a project's chat history.

`_run_claude_auth_probe()` runs `claude -p ok --max-turns 1`. The CLI writes a
transcript for EVERY invocation into ~/.claude/projects/<encoded CWD>/, and the
probe used to run with no `cwd=` — so it inherited the server's working
directory, which is the Clayrune checkout, which is itself an MC project. Every
probe therefore deposited a real .jsonl in that project's transcript dir;
/conversations reads transcripts directly, so each one showed up in the chat
rail labelled "ok", and the startup backfill synthesized an `interrupted`
agent-log row for it. Six accumulated in a single day and read to the user as
"my conversation split into several conversations".

Two guards here: the probe runs somewhere that is not a project, and the
already-written leftovers are hidden — but only when MC has no record of having
dispatched them, so a real chat can never be swallowed.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import agent_routes as ar  # noqa: E402


def _t(first, last=None, turns=1, sid='abc'):
    return {'session_id': sid, 'first_user': first,
            'last_user': first if last is None else last, 'turns': turns}


# ── the probe's own working directory ────────────────────────────────────────

def test_probe_cwd_is_not_a_project_dir(tmp_path, monkeypatch):
    """It must resolve OUTSIDE data/projects — anything inside DATA_DIR is read
    as a project record by load_projects() (the DATA_DIR-pollution rule)."""
    monkeypatch.setattr(ar, 'DATA_DIR', tmp_path / 'data' / 'projects')
    cwd = Path(ar._auth_probe_cwd())
    assert cwd.is_dir()
    assert (tmp_path / 'data' / 'projects') not in cwd.parents
    assert cwd.name == '_auth_probe'


def test_probe_cwd_survives_an_unwritable_data_dir(monkeypatch):
    """A probe that can't run is worse than a stray file — fall back, never raise."""
    class _Boom:
        parent = property(lambda self: (_ for _ in ()).throw(OSError('nope')))

    monkeypatch.setattr(ar, 'DATA_DIR', _Boom())
    assert Path(ar._auth_probe_cwd()).is_dir()


# ── recognising a leftover ───────────────────────────────────────────────────

def test_recognises_the_probe_signature():
    assert ar._is_auth_probe_transcript(_t('ok'))
    assert ar._is_auth_probe_transcript(_t('OK'))
    assert ar._is_auth_probe_transcript(_t(' ok '))
    # The probe's assistant reply means last_user can be empty.
    assert ar._is_auth_probe_transcript(_t('ok', last=''))


@pytest.mark.parametrize('convo', [
    _t('ok, lets continue'),                    # a real message that starts with ok
    _t('okay'),
    _t('ok', turns=4),                          # a chat that merely opened with "ok"
    _t('ok', last='now do the thing', turns=2),
    _t(''),
])
def test_does_not_match_real_conversations(convo):
    assert not ar._is_auth_probe_transcript(convo)


# ── the filter as the endpoint applies it ────────────────────────────────────
#
# Mirrors the comprehension in get_project_conversations(): drop a probe-looking
# transcript ONLY when there is no live session and no non-synthesized log row.

def _keep(convo, live_by_csid, log_by_csid):
    return not (ar._is_auth_probe_transcript(convo)
                and convo['session_id'] not in live_by_csid
                and (log_by_csid.get(convo['session_id']) or {}).get('synthesized', True))


def test_orphan_probe_is_hidden():
    assert not _keep(_t('ok', sid='ghost'), {}, {})


def test_probe_shaped_chat_is_kept_when_mc_dispatched_it():
    """If MC owns the session, it is the user's chat — show it even if the only
    thing they typed was "ok"."""
    assert _keep(_t('ok', sid='mine'), {}, {'mine': {'synthesized': False}})
    assert _keep(_t('ok', sid='mine'), {'mine': {'status': 'running'}}, {})


def test_backfilled_row_does_not_rescue_a_probe():
    """The backfill synthesizes a row for EVERY unknown transcript, probes
    included — so a synthesized row is not evidence MC asked for the session."""
    assert not _keep(_t('ok', sid='ghost'), {}, {'ghost': {'synthesized': True}})

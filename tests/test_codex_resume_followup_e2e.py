"""End-to-end reproduction of the "every prompt starts a new conversation"
Codex defect Ron reported from the Kalshi project screenshot: a dozen-plus
rail rows, all titled the harness preamble, all READ-ONLY, "2 turns" each —
one real conversation shredded into N fake ones.

Root cause (docs/research/CODEX_PARITY_AUDIT.md (a) item 4): `write_followup`
killed the live process and respawned a BRAND NEW `codex exec` on every
follow-up, instead of `codex exec resume <thread_id>`. Each respawn is a
genuinely new Codex thread with its own rollout file, so
`_recent_codex_conversation_rows` (backed by `CodexRuntime.list_sessions()`,
which scans real rollout files) correctly reports each one as a SEPARATE
conversation — it isn't wrong about the data, the data itself is wrong.

This test does not mock `CodexRuntime.dispatch`/`write_followup`, and does not
mock `list_sessions()`. It substitutes only the one thing that has to be
substituted — the `codex` binary itself, which is not available unauthenticated
in CI — with a small script that mimics the two on-disk behaviors that matter:
`codex exec` starts a NEW rollout file with a fresh thread id; `codex exec
resume <id>` APPENDS to the EXISTING rollout file for that id. Both real
Codex behaviors, verified in docs/research/CODEX_PARITY_AUDIT.md §0 and,
separately, against a live rollout captured on this machine 2026-09-08 (see
the CRLF note on `_CODEX_DISPATCH_SEP_RE` in mc/agent_runtime.py). Everything
else — `dispatch()`, `write_followup()`, the real reader thread parsing real
JSONL off a real subprocess, `list_sessions()` scanning real files on disk —
runs unmodified.

Also exercises the second, independent defect in the same screenot: the rail
row's label was the Codex CLI's own `<recommended_plugins>` harness preamble
(a real user-role rollout record, not something MC controls) rather than the
operator's actual message — see `_strip_codex_system_prefix` /
`_NONUSER_LABEL_RE` in mc/agent_runtime.py.
"""
from __future__ import annotations

import json
import os
import sys
import textwrap
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.agent_runtime as agent_runtime_mod  # noqa: E402


_FAKE_CODEX_SRC = textwrap.dedent(r"""
    # Stand-in for the real `codex` CLI binary. Mimics ONLY the two on-disk
    # behaviors this test cares about: `exec` starts a new rollout file with a
    # fresh thread id; `exec resume <id>` appends to the EXISTING file for
    # that id. Reads the prompt from stdin (real codex does too, per
    # CodexRuntime.dispatch/write_followup — both pass prompt_via_stdin).
    import json, os, sys
    from pathlib import Path

    def main():
        args = sys.argv[1:]
        home = Path(os.environ['FAKE_CODEX_HOME'])
        prompt = sys.stdin.read()

        target = None
        thread_id = None
        if 'resume' in args:
            idx = args.index('resume')
            rest = args[idx + 1:]
            if rest and rest[0] not in ('--json', '--last'):
                thread_id = rest[0]
                matches = list(home.glob(f'*/*/*/rollout-*-{thread_id}.jsonl'))
                if matches:
                    target = matches[0]

        if target is None:
            counter_file = home / '_counter'
            n = int(counter_file.read_text()) + 1 if counter_file.exists() else 1
            counter_file.write_text(str(n))
            thread_id = thread_id or f'fake-thread-{n}'
            day_dir = home / '2026' / '09' / '08'
            day_dir.mkdir(parents=True, exist_ok=True)
            target = day_dir / f'rollout-2026-09-08T00-00-{n:02d}-{thread_id}.jsonl'
            target.write_text(json.dumps({
                'type': 'session_meta',
                'payload': {'id': thread_id, 'session_id': thread_id,
                            'cwd': os.environ['FAKE_CODEX_CWD'],
                            'originator': 'codex_exec'},
            }) + '\n', encoding='utf-8')

        with open(target, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps({
                'type': 'response_item',
                'payload': {'type': 'message', 'role': 'user',
                            'content': [{'type': 'input_text', 'text': prompt}]},
            }) + '\n')
            reply = f'ack: {prompt[:40]}'
            fh.write(json.dumps({
                'type': 'response_item',
                'payload': {'type': 'message', 'role': 'assistant',
                            'content': [{'type': 'output_text', 'text': reply}]},
            }) + '\n')

        print(json.dumps({'type': 'thread.started', 'thread_id': thread_id}))
        print(json.dumps({'type': 'turn.started'}))
        print(json.dumps({'type': 'item.completed',
                          'item': {'type': 'agent_message', 'text': reply}}))
        print(json.dumps({'type': 'turn.completed', 'usage': {}}))
        sys.stdout.flush()

    main()
    """)


def _wait_until_idle(session, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if session.get('process_alive') is False:
            return
        time.sleep(0.02)
    raise AssertionError(f"fake codex process never completed; session={session}")


@pytest.fixture()
def fake_codex(tmp_path, monkeypatch):
    """Point CodexRuntime at the fake binary and a scratch rollout home."""
    script = tmp_path / 'fake_codex.py'
    script.write_text(_FAKE_CODEX_SRC, encoding='utf-8')
    codex_home = tmp_path / 'codex_sessions'
    codex_home.mkdir()
    project_path = tmp_path / 'project'
    project_path.mkdir()

    monkeypatch.setattr(agent_runtime_mod, '_CODEX_HOME', codex_home)
    agent_runtime_mod._CODEX_META_CACHE.clear()
    agent_runtime_mod._CODEX_ROW_CACHE.clear()
    monkeypatch.setenv('FAKE_CODEX_HOME', str(codex_home))
    monkeypatch.setenv('FAKE_CODEX_CWD', str(project_path))

    rt = agent_runtime_mod.CodexRuntime()
    monkeypatch.setattr(rt, '_cmd_prefix', lambda: [sys.executable, str(script)])
    return rt, project_path, codex_home


def test_resumed_followup_lands_in_one_rollout_with_two_real_turns(fake_codex):
    """The regression test for the rail-forking bug.

    Fails on the pre-fix `write_followup` (it never threads `resume_id`
    through to `build_command`, so the fake binary sees no 'resume' in argv
    on turn 2 and allocates a SECOND rollout file/thread id — this assertion
    then sees 2 rows, not 1) and passes once `write_followup` resumes the
    captured `provider_session_id` instead of respawning cold.
    """
    rt, project_path, codex_home = fake_codex
    session: dict = {}
    handle = rt.dispatch(
        project_path=str(project_path),
        task='diagnose the failing build',
        system_prompt='PERSONA AND MEMORY BLOCK — not something the user typed',
        mc_session_id='mcsid-1',
        session_dict=session,
    )
    _wait_until_idle(session)
    assert session.get('provider_session_id'), (
        "dispatch's thread.started INIT event was not captured — "
        f"session={session}")
    first_thread_id = session['provider_session_id']

    rt.write_followup(handle, 'fix it')
    _wait_until_idle(session)

    # ── Item 1: one conversation, not two ──────────────────────────────
    rows = rt.list_sessions(str(project_path))
    assert len(rows) == 1, (
        f"expected ONE Codex conversation (rollout) after a dispatch + one "
        f"follow-up, got {len(rows)}: {rows}")
    row = rows[0]
    assert row['session_id'] == first_thread_id, (
        "the follow-up used a DIFFERENT thread id than dispatch captured — "
        "it forked instead of resuming")
    assert row['turns'] == 2, f"expected 2 real user turns, got: {row}"

    # ── Item 2: the label is the operator's real text, not harness/system
    #    preamble (dispatch's system_prompt is folded into the SAME stdin
    #    blob as the task — see _strip_codex_system_prefix) ──────────────
    assert row['first_user'] == 'diagnose the failing build', row
    assert 'PERSONA AND MEMORY BLOCK' not in row['first_user'], row
    assert row['last_user'] == 'fix it', row

    # Exactly one rollout file exists — confirms no second thread/file was
    # ever created, not just that list_sessions() de-duplicated one away.
    rollout_files = list(codex_home.glob('*/*/*/rollout-*.jsonl'))
    assert len(rollout_files) == 1, rollout_files

"""The voice seeder — the incognito exclusion and the envelope strip.

Both properties here failed silently in the first cut, which is why they have
tests rather than a comment:

  * The exclusion was keyed on session ids read out of `_incognito.json`'s
    `activity_log`. Those entries carry only `{ts, msg}` — no session id — so the
    skip set came back EMPTY and incognito transcripts were read. Nothing raised.
  * 23% of the raw sample (93 of 400, measured) was Clayrune's own injected
    phone directive rather than anything the human typed.
"""

import json
from pathlib import Path

import pytest

from mc import desk_voice_seed as seed

SEP = chr(92)  # a literal backslash, kept out of the string literals below


def _transcript(d: Path, name: str, messages, cwd='C:/work/ordinary'):
    """A transcript records its own `cwd`, and that is what the exclusion reads.

    Not the directory-name encoding, which has demonstrably changed over time:
    both `Documents-_claude-...` and `Documents--claude-...` exist on this disk,
    so matching on an encoded name is matching on a guess.
    """
    f = d / f'{name}.jsonl'
    f.write_text('\n'.join(
        json.dumps({'type': 'user', 'cwd': cwd, 'message': {'content': m}})
        for m in messages), encoding='utf-8')
    return f


# ── the incognito exclusion ──────────────────────────────────────────────────

def test_incognito_transcripts_are_excluded(tmp_path):
    data = tmp_path / 'projects'
    data.mkdir()
    workspace = tmp_path / 'ws' / '_incognito'
    workspace.mkdir(parents=True)
    (data / '_incognito.json').write_text(
        json.dumps({'project_path': str(workspace)}), encoding='utf-8')

    root = tmp_path / 'transcripts'
    # Deliberately an innocuous directory NAME. The old exclusion matched on the
    # encoded name, so a transcript filed anywhere else was read regardless of
    # where it actually ran.
    d = root / 'C--somewhere-harmless'
    d.mkdir(parents=True)
    _transcript(d, 's1', ['this line is private and must never be read'],
                cwd=str(workspace))
    pub = root / 'C--Users-someone-work'
    pub.mkdir(parents=True)
    _transcript(pub, 's2', ['this line is an ordinary message and may be read'])

    got = seed.collect(data, root, limit=50)
    assert any('ordinary message' in g for g in got)
    assert not any('private' in g for g in got)


def test_a_transcript_that_will_not_say_where_it_ran_is_skipped(tmp_path):
    """An unidentifiable transcript is exactly the one we cannot clear."""
    data = tmp_path / 'projects'
    data.mkdir()
    ws = tmp_path / 'ws' / '_incognito'
    ws.mkdir(parents=True)
    (data / '_incognito.json').write_text(
        json.dumps({'project_path': str(ws)}), encoding='utf-8')
    root = tmp_path / 't'
    d = root / 'proj'
    d.mkdir(parents=True)
    (d / 's.jsonl').write_text(
        json.dumps({'type': 'user', 'message': {'content': 'no cwd on this record at all'}}),
        encoding='utf-8')
    assert seed.collect(data, root, limit=10) == []


def test_an_unreadable_incognito_record_REFUSES_rather_than_reading(tmp_path):
    """The regression this file exists for.

    The first cut returned a sentinel string into a skip set and called that
    fail-closed. `collect` only asked `d.name not in skip_dirs`, so the sentinel
    matched nothing and every transcript — including the real incognito one —
    was read. The old test asserted only that the set was non-empty, so it
    passed. This one puts a real incognito transcript on disk and proves nothing
    comes back.
    """
    data = tmp_path / 'projects'
    data.mkdir()
    (data / '_incognito.json').write_text('{not json', encoding='utf-8')
    root = tmp_path / 't'
    d = root / 'proj'
    d.mkdir(parents=True)
    _transcript(d, 's', ['a private line that must not survive a corrupt record'],
                cwd='C:/ws/_incognito')

    with pytest.raises(seed.IncognitoBoundaryUnresolved):
        seed.collect(data, root, limit=10)


def test_a_record_with_no_path_also_refuses(tmp_path):
    data = tmp_path / 'projects'
    data.mkdir()
    (data / '_incognito.json').write_text(json.dumps({'name': 'Incognito'}),
                                          encoding='utf-8')
    with pytest.raises(seed.IncognitoBoundaryUnresolved):
        seed.collect(data, tmp_path / 't', limit=10)


def test_missing_incognito_record_is_not_an_error(tmp_path):
    """A fresh install has never opened an incognito session — nothing to exclude,
    which is different from being unable to tell."""
    data = tmp_path / 'projects'
    data.mkdir()
    assert seed.incognito_workspace(data) is None


def test_collect_requires_the_projects_dir():
    """No default. A caller that forgets it would read every private transcript."""
    with pytest.raises(TypeError):
        seed.collect()  # type: ignore[call-arg]


# ── the envelope strip ───────────────────────────────────────────────────────

def test_the_injected_phone_directive_is_stripped_not_sampled(tmp_path):
    """23% of the real corpus opened with this. Left in, the voice learns OURS."""
    data = tmp_path / 'projects'
    data.mkdir()
    root = tmp_path / 't'
    d = root / 'proj'
    d.mkdir(parents=True)
    injected = ('[the user is messaging you from a phone — reply in Telegram style: '
                'short, conversational, one idea per message; avoid headers, bullets, '
                'and long code blocks. This instruction is hidden from the user.]\n'
                'so what changed on the pricing page?')
    _transcript(d, 's', [injected])

    got = seed.collect(data, root, limit=10)
    assert got == ['so what changed on the pricing page?']


def test_machine_messages_are_dropped_whole(tmp_path):
    data = tmp_path / 'projects'
    data.mkdir()
    root = tmp_path / 't'
    d = root / 'proj'
    d.mkdir(parents=True)
    _transcript(d, 's', [
        '[dispatched agent finished] Tobin (session abc) ended with status completed',
        'This session is being continued from a previous conversation that ran out',
        'Stop hook feedback: BREVITY RULE VIOLATED: that reply was 195 words',
        'right, drop that and use the redirect instead',
    ])
    assert seed.collect(data, root, limit=10) == ['right, drop that and use the redirect instead']


def test_trailing_attachment_markers_are_cut(tmp_path):
    data = tmp_path / 'projects'
    data.mkdir()
    root = tmp_path / 't'
    d = root / 'proj'
    d.mkdir(parents=True)
    _transcript(d, 's', ['the button text overflows its window\n\n[Screenshot 2026-09-10.png]'])
    assert seed.collect(data, root, limit=10) == ['the button text overflows its window']


def test_pastes_are_not_voice_evidence(tmp_path):
    """A stack trace says what they were DOING, not how they write — and it is
    the likeliest place for a credential to be sitting."""
    data = tmp_path / 'projects'
    data.mkdir()
    root = tmp_path / 't'
    d = root / 'proj'
    d.mkdir(parents=True)
    _transcript(d, 's', [
        'Traceback (most recent call last)\n  File "x.py", line 3\nValueError: nope',
        '```python\nprint(1)\n```',
        '/compact',
        'C:' + SEP + 'Users' + SEP + 'x' + SEP + 'y' + SEP + 'z' + SEP + 'a.json',
        'that trace is the CRLF thing again, use the Edit tool',
    ])
    assert seed.collect(data, root, limit=10) == [
        'that trace is the CRLF thing again, use the Edit tool']


def test_agent_replies_are_never_sampled(tmp_path):
    data = tmp_path / 'projects'
    data.mkdir()
    root = tmp_path / 't'
    d = root / 'proj'
    d.mkdir(parents=True)
    (d / 's.jsonl').write_text('\n'.join([
        json.dumps({'type': 'assistant', 'message': {'content': 'I have shipped the change.'}}),
        json.dumps({'type': 'user', 'isMeta': True,
                    'message': {'content': 'injected meta turn from the CLI itself'}}),
        json.dumps({'type': 'user', 'isCompactSummary': True,
                    'message': {'content': 'a model-written recap wearing a user role'}}),
        json.dumps({'type': 'user', 'message': {'content': 'ok ship it and push master'}}),
    ]), encoding='utf-8')
    assert seed.collect(data, root, limit=10) == ['ok ship it and push master']


# ── the brief ────────────────────────────────────────────────────────────────

def test_brief_forbids_quoting_and_names_the_patch_target():
    b = seed.build_seed_brief(['pick the shorter one', 'what did you measure'], 'personal')
    assert 'DESCRIBE, DO NOT QUOTE' in b
    assert '/api/desk/voices/personal' in b
    # The register is the seed, not the verdict: real edits outrank it.
    assert 'outrank' in b
    # Ron's own framing has to reach the agent, since it is the judgement call.
    assert 'ATTEND' in b


# ── secrets ──────────────────────────────────────────────────────────────────
#
# `_looks_typed` screens SHAPE — length and punctuation density — so a key pasted
# into an otherwise ordinary short sentence sails through it. These drop the
# WHOLE message rather than redacting: a sentence with a hole in it is still
# evidence we read something we should not have.

@pytest.mark.parametrize('line', [
    'the key is sk-abcdefghijklmnopqrstuv and it works now',
    'try ghp_ABCDEFGHIJKLMNOPqrstuvwx on the repo',
    'password: hunter2000correcthorse',
    'use AKIAABCDEFGHIJKLMNOP for the bucket',
    'set authorization = Bearer abcdefghijklmnopqrst',
])
def test_a_pasted_credential_drops_the_whole_message(tmp_path, line):
    data = tmp_path / 'projects'
    data.mkdir()
    root = tmp_path / 't'
    d = root / 'proj'
    d.mkdir(parents=True)
    _transcript(d, 's', [line, 'and this ordinary line should still come back'])
    got = seed.collect(data, root, limit=10)
    assert got == ['and this ordinary line should still come back']


def test_ordinary_writing_is_not_mistaken_for_a_secret():
    for line in ['use the redirect instead of appending a link',
                 'that trace is the CRLF thing again, use the Edit tool',
                 'pick the shorter one and ship it']:
        assert not seed._carries_a_secret(line), line

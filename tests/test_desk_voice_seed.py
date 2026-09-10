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


def _transcript(d: Path, name: str, messages):
    f = d / f'{name}.jsonl'
    f.write_text('\n'.join(
        json.dumps({'type': 'user', 'message': {'content': m}}) for m in messages),
        encoding='utf-8')
    return f


# ── the incognito exclusion ──────────────────────────────────────────────────

def test_incognito_transcripts_are_excluded_by_directory(tmp_path):
    data = tmp_path / 'projects'
    data.mkdir()
    workspace = tmp_path / 'ws' / '_incognito'
    workspace.mkdir(parents=True)
    (data / '_incognito.json').write_text(
        json.dumps({'project_path': str(workspace)}), encoding='utf-8')

    root = tmp_path / 'transcripts'
    # The same encoder Claude Code's transcript layout uses — that IS the
    # contract, so the test must not reimplement it.
    from mc.memory import _encode_project_path
    priv = root / _encode_project_path(str(workspace))
    priv.mkdir(parents=True)
    _transcript(priv, 's1', ['this line is private and must never be read'])
    pub = root / 'C--Users-someone-work'
    pub.mkdir(parents=True)
    _transcript(pub, 's2', ['this line is an ordinary message and may be read'])

    got = seed.collect(data, root, limit=50)
    assert any('ordinary message' in g for g in got)
    assert not any('private' in g for g in got)


def test_unreadable_incognito_record_fails_closed(tmp_path):
    """A corrupt record must not read as 'nothing to exclude'."""
    data = tmp_path / 'projects'
    data.mkdir()
    (data / '_incognito.json').write_text('{not json', encoding='utf-8')
    assert seed.incognito_dirs(data), 'a broken record produced an EMPTY skip set'


def test_missing_incognito_record_is_not_an_error(tmp_path):
    """A fresh install has never opened an incognito session."""
    data = tmp_path / 'projects'
    data.mkdir()
    assert seed.incognito_dirs(data) == set()


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

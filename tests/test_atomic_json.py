"""Every `data/` state writer must be atomic, and every reader must survive a
file that isn't (MC-946, widened scope).

The agent log was one victim of a general defect. `/api/system/restart` spawns
the replacement server BEFORE stopping the old one and hard-`os._exit`s the old
process on a 10s watchdog, so two MC processes write `data/projects/*.json`
concurrently for up to ten seconds. Every writer was a bare
`write_text(json.dumps(...))` — truncate first, then write — so a kill in that
window leaves a partial file.

The two symptoms Ron actually saw are the same defect twice: a truncated
PROJECT record made the whole project vanish from the dashboard (`load_projects`
skips what it cannot parse), and a truncated AGENT LOG became permanent when
the startup backfill rewrote it.

The restart ordering is deliberate and stays; atomic writes make it safe.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.atomic_json import write_json_atomic          # noqa: E402
from mc.blueprints import project_routes as P         # noqa: E402

TRUNCATED = '{\n  "id": "half_written",\n  "status": "act'


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / 'projects'
    d.mkdir()
    monkeypatch.setattr(P, 'DATA_DIR', d)
    return d


# ── the helper ──────────────────────────────────────────────────────────────

def test_write_is_atomic_and_round_trips(data_dir):
    target = data_dir / 'p.json'
    write_json_atomic(target, {'id': 'p', 'n': 1}, indent=2, ensure_ascii=False)
    assert json.loads(target.read_text(encoding='utf-8')) == {'id': 'p', 'n': 1}


def test_failed_serialization_leaves_the_previous_content_intact(data_dir):
    target = data_dir / 'p.json'
    write_json_atomic(target, {'id': 'p', 'keep': True}, indent=2)
    good = target.read_text(encoding='utf-8')

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        write_json_atomic(target, {'id': 'p', 'bad': Unserializable()}, indent=2)

    assert target.read_text(encoding='utf-8') == good
    assert list(data_dir.iterdir()) == [target], \
        'no temp file may survive a failed write'


def test_temp_file_is_never_mistaken_for_a_project_record(data_dir, monkeypatch):
    """The temp lands in DATA_DIR, which load_projects globs for `*.json`.

    Its name must not end in .json (CLAUDE.md's DATA_DIR pollution rule), and
    it must match the `.{name}.tmp{pid}` shape mc.core.sweep_orphan_tmpfiles
    already cleans up, so a crash between write and replace strands nothing
    permanent.
    """
    import re
    seen = {}
    real_replace = __import__('os').replace

    def spy(src, dst):
        seen['tmp'] = Path(src).name
        return real_replace(src, dst)

    monkeypatch.setattr('mc.atomic_json.os.replace', spy)
    write_json_atomic(data_dir / 'p.json', {'id': 'p'})

    assert not seen['tmp'].endswith('.json')
    assert re.match(r'^\..+\.tmp\d+$', seen['tmp']), seen['tmp']


# ── the readers ─────────────────────────────────────────────────────────────

def test_load_projects_skips_a_truncated_record_and_says_so(data_dir, capsys):
    (data_dir / 'good.json').write_text(
        json.dumps({'id': 'good', 'status': 'active'}), encoding='utf-8')
    (data_dir / 'half_written.json').write_text(TRUNCATED, encoding='utf-8')

    ids = [p.get('id') for p in P.load_projects()]

    # Skipping is correct — one bad record must not 500 the dashboard.
    assert ids == ['good']
    # But the project has DISAPPEARED, so it must not be a debug-level shrug.
    out = capsys.readouterr().out
    assert 'CORRUPT RECORD' in out and 'half_written.json' in out


def test_load_project_returns_none_instead_of_raising(data_dir, capsys):
    (data_dir / 'half_written.json').write_text(TRUNCATED, encoding='utf-8')

    # Used to propagate json.JSONDecodeError → a 500 on every route that
    # touches the project. None is what a missing record already returns,
    # so every caller already handles it.
    assert P.load_project('half_written') is None
    assert 'CORRUPT RECORD' in capsys.readouterr().out
    # The record is reported absent, never rewritten from the failed read.
    assert (data_dir / 'half_written.json').read_text(encoding='utf-8') == TRUNCATED


def test_load_project_still_returns_a_good_record(data_dir):
    (data_dir / 'good.json').write_text(
        json.dumps({'id': 'good', 'backlog': []}), encoding='utf-8')
    assert (P.load_project('good') or {}).get('id') == 'good'
    assert P.load_project('never_existed') is None


def test_save_project_round_trips_through_the_atomic_writer(data_dir):
    P.save_project('p', {'id': 'p', 'status': 'active', 'backlog': []})
    assert (P.load_project('p') or {}).get('status') == 'active'


# ── the canary ──────────────────────────────────────────────────────────────

def test_no_new_non_atomic_json_writers(repo_root):
    """Fail when someone adds `path.write_text(json.dumps(...))` back.

    This is the defect class, not one bug: MC-946 was caused by six of these,
    and the previous restart lost a whole project record to a seventh. Every
    JSON state write goes through mc.atomic_json.write_json_atomic. A grep is
    the only thing that catches the NEXT one, because the failure is invisible
    until a process dies at the wrong microsecond.

    If you are adding a genuinely throwaway write (a scratch file nothing
    reads back), put it outside mc/ or use a different call shape -- do not
    add an exemption list here.
    """
    import re
    offenders = []
    pattern = re.compile(r'write_text\(\s*json\.dumps')
    roots = [repo_root / 'mc', repo_root / 'server.py']
    for root in roots:
        files = [root] if root.is_file() else sorted(root.rglob('*.py'))
        for f in files:
            if f.name == 'atomic_json.py':
                continue
            for i, line in enumerate(
                    f.read_text(encoding='utf-8').splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f'{f.relative_to(repo_root)}:{i}')
    assert not offenders, (
        'non-atomic JSON writes found -- use mc.atomic_json.write_json_atomic:\n'
        + '\n'.join(offenders))

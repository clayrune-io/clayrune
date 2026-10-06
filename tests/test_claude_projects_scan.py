"""Backlog ba3b73f9 — scanner + route for importing existing Claude Code projects.

Fixture is a fake `~/.claude/projects` tree under tmp_path covering the cases the
brief names: live, stale (folder gone), worktree, already-registered, plus the
ones the design adds (home dir, install dir, two dirs naming one folder, a `cd`
mid-session, a dir with no transcripts).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import claude_projects_scan as scan_mod  # noqa: E402


def _encode(p) -> str:
    return re.sub(r'[^A-Za-z0-9]', '-', str(p))


def _transcript(claude_home: Path, folder: Path, *, sessions=1, age_s=0, cwds=None,
                dir_name=None) -> Path:
    """Write `sessions` transcripts for `folder`; the newest is `age_s` old."""
    d = claude_home / (dir_name or _encode(folder))
    d.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for i in range(sessions):
        f = d / f'sess{i}.jsonl'
        lines: list = [{'type': 'summary', 'summary': 'x'}]
        for c in (cwds or [str(folder)]):
            lines.append({'type': 'user', 'cwd': c, 'message': {'content': 'hi'}})
        f.write_text('\n'.join(json.dumps(l) for l in lines) + '\n', encoding='utf-8')
        t = now - age_s - (sessions - 1 - i) * 3600
        os.utime(f, (t, t))
    return d


@pytest.fixture()
def tree(tmp_path):
    home = tmp_path / 'home'
    ch = home / '.claude' / 'projects'
    ch.mkdir(parents=True)
    ws = tmp_path / 'work'
    ws.mkdir()

    def mk(name):
        p = ws / name
        p.mkdir()
        return p

    t = SimpleNamespace()
    t.home, t.claude_home, t.ws = home, ch, ws
    t.fresh = mk('fresh-app')
    t.older = mk('older_app')
    t.registered = mk('already-here')
    t.cd = mk('cd-session')
    t.sub = mk('cd-session-sub')
    t.twin = mk('twin')
    t.install = mk('install-root')
    _transcript(ch, t.fresh, sessions=3, age_s=60)
    _transcript(ch, t.older, sessions=1, age_s=86400 * 10)
    _transcript(ch, t.registered, sessions=2, age_s=120)
    # stale: transcript dir whose folder was deleted
    gone = ws / 'deleted-project'
    _transcript(ch, gone, sessions=1, age_s=30)
    # worktree: folder exists but is a Clayrune agent worktree
    wt = ws / 'repo' / '.clayrune' / 'agents' / 'abc123'
    wt.mkdir(parents=True)
    _transcript(ch, wt, sessions=1, age_s=45)
    # the user's home directory (claude run as a general chat)
    _transcript(ch, home, sessions=4, age_s=10)
    # `cd` mid-session: first cwd is the launch folder (== dir name), then a subfolder
    _transcript(ch, t.cd, sessions=1, age_s=200, cwds=[str(t.cd), str(t.sub)])
    # `cd` BEFORE the first recorded cwd line is the subfolder: dir name still wins
    _transcript(ch, t.sub, sessions=1, age_s=300, cwds=[str(t.sub)])
    # no transcripts at all
    (ch / _encode(ws / 'empty')).mkdir()
    # a transcript with no cwd anywhere
    bare = ch / 'no-cwd-dir'
    bare.mkdir()
    (bare / 's.jsonl').write_text('{"type":"summary"}\n', encoding='utf-8')
    # install dir
    _transcript(ch, t.install, sessions=1, age_s=15)
    # two transcript dirs naming one folder
    _transcript(ch, t.twin, sessions=2, age_s=500)
    _transcript(ch, t.twin, sessions=1, age_s=400, dir_name=_encode(t.twin) + '-alt')
    t.gone, t.wt = gone, wt
    return t


def _run(t, **kw):
    kw.setdefault('registered_paths', [str(t.registered)])
    kw.setdefault('registered_ids', ['already_here'])
    kw.setdefault('app_dir', t.install)
    return scan_mod.scan(claude_home=t.claude_home, home=t.home, **kw)


def test_filters_and_ordering(tree):
    r = _run(tree)
    by_name = {c['name']: c for c in r['candidates']}
    assert set(by_name) == {'fresh-app', 'older_app', 'cd-session', 'cd-session-sub', 'twin'}
    stamps = [c['last_activity'] for c in r['candidates']]
    assert stamps == sorted(stamps, reverse=True)
    assert r['candidates'][0]['name'] == 'fresh-app'      # 60s old
    assert r['candidates'][-1]['name'] == 'older_app'      # 10 days old
    assert r['total'] == 5 and r['truncated'] is False
    assert r['skipped'] == {'missing': 1, 'registered': 1, 'worktree': 1,
                            'install_dir': 1, 'home_or_root': 1, 'no_cwd': 1}


def test_session_count_and_twin_merge(tree):
    by_name = {c['name']: c for c in _run(tree)['candidates']}
    assert by_name['fresh-app']['session_count'] == 3
    assert by_name['older_app']['session_count'] == 1
    assert by_name['twin']['session_count'] == 3   # 2 + 1 across both dirs, one candidate


def test_cwd_prefers_launch_folder_over_later_cd(tree):
    c = {c['name']: c for c in _run(tree)['candidates']}
    assert c['cd-session']['path'] == str(tree.cd)


def test_cwd_whose_encoding_is_dir_name_wins_over_first_seen(tmp_path):
    ch = tmp_path / 'ch'
    launch = tmp_path / 'launch'
    other = tmp_path / 'other'
    launch.mkdir()
    other.mkdir()
    # first cwd line is a different folder than the one the dir is named for
    _transcript(ch, launch, cwds=[str(other), str(launch)])
    r = scan_mod.scan(claude_home=ch, home=tmp_path / 'nohome')
    assert [c['path'] for c in r['candidates']] == [str(launch)]


def test_suggested_ids_are_unique_slugs(tree):
    # a registered project already owns the id `fresh_app`
    r = _run(tree, registered_ids=['already_here', 'fresh_app'])
    ids = {c['name']: c['id'] for c in r['candidates']}
    assert ids['fresh-app'] == 'fresh_app_2'
    assert ids['older_app'] == 'older_app'
    assert len(set(ids.values())) == len(ids)
    assert all(re.fullmatch(r'[a-z0-9_]+', i) for i in ids.values())


def test_install_dir_allowed_when_config_says_so(tree):
    r = _run(tree, allow_install_dir=True)
    assert 'install-root' in {c['name'] for c in r['candidates']}
    assert r['skipped']['install_dir'] == 0


def test_never_writes_to_claude_home(tree):
    def snap():
        return sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size)
                      for p in tree.claude_home.rglob('*'))
    before = snap()
    _run(tree)
    assert snap() == before


def test_missing_claude_home_is_empty_not_an_error(tmp_path):
    r = scan_mod.scan(claude_home=tmp_path / 'nope', home=tmp_path)
    assert r['candidates'] == [] and r['total'] == 0


def test_huge_first_line_does_not_hide_cwd(tmp_path):
    ch = tmp_path / 'ch'
    folder = tmp_path / 'big'
    folder.mkdir()
    d = ch / _encode(folder)
    d.mkdir(parents=True)
    # cwd on line 1, then a 3 MB line: only the head is read, cwd still found
    (d / 's.jsonl').write_text(
        json.dumps({'type': 'user', 'cwd': str(folder)}) + '\n'
        + json.dumps({'type': 'user', 'blob': 'A' * 3_000_000}) + '\n', encoding='utf-8')
    r = scan_mod.scan(claude_home=ch, home=tmp_path / 'nohome')
    assert [c['path'] for c in r['candidates']] == [str(folder)]


def test_is_agent_worktree():
    assert scan_mod.is_agent_worktree('C:\\a\\b\\.clayrune\\agents\\x')
    assert scan_mod.is_agent_worktree('/a/b/.clayrune/agents/x/sub')
    assert not scan_mod.is_agent_worktree('/a/.clayrune/other')
    assert not scan_mod.is_agent_worktree('/a/agents/.clayrune')


# ── route ────────────────────────────────────────────────────────────────────

def test_route_scans_and_excludes_registered(tree, monkeypatch, tmp_path):
    import server
    from mc.blueprints import project_routes as pr

    server.app.config['TESTING'] = True
    monkeypatch.setattr(scan_mod, 'default_claude_home', lambda: tree.claude_home)
    monkeypatch.setattr(pr, 'load_projects', lambda: [
        {'id': 'reg', 'project_path': str(tree.registered)},
        {'id': 'fresh_app', 'project_path': ''},
    ])
    monkeypatch.setattr(pr, '_APP_DIR', tree.install)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tree.home))
    r = server.app.test_client().get('/api/claude-import/scan')
    assert r.status_code == 200
    body = r.get_json()
    assert body['ok'] is True
    names = {c['name'] for c in body['candidates']}
    assert 'already-here' not in names and 'install-root' not in names
    assert {c['name']: c['id'] for c in body['candidates']}['fresh-app'] == 'fresh_app_2'
    assert body['skipped']['registered'] == 1


def test_route_reports_failure_as_json_500(monkeypatch):
    import server
    server.app.config['TESTING'] = True

    def boom(**kw):
        raise RuntimeError('disk on fire')
    monkeypatch.setattr(scan_mod, 'scan', boom)
    r = server.app.test_client().get('/api/claude-import/scan')
    assert r.status_code == 500
    assert r.get_json()['ok'] is False and 'disk on fire' in r.get_json()['error']

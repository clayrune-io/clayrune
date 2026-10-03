"""MC-1037 (A): a fenced session may write topic files in its OWN project memory dir.

Ron, 2026-10-02 ("Yes he should"): the `.claude` rule in steward/fence.py kept a
dispatched/scheduled/workflow/hivemind session from recording its own memory
notes without an "Allow once" click. These tests pin the narrow hole:

  allowed  <own project memory dir>/<name>.md  (Write, Edit, MultiEdit, apply_patch)
  blocked  MEMORY.md / MEMORY_ARCHIVE.md / SESSION_LOG.md (Clayrune-maintained),
           subdirectories, any other project's memory, everything else under
           .claude, and every path trick that resolves outside the own dir.

"Own project" must come from the session (the hook payload's cwd), never from
the path being written or from anything else in tool_input.
"""
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'steward'))
import fence  # noqa: E402

import mc.memory as server_memory  # noqa: E402


def _enc(p: Path) -> str:
    return str(p.resolve()).replace(':', '-').replace('\\', '-').replace('/', '-')


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = SimpleNamespace()
    e.home = tmp_path / 'home'
    monkeypatch.setenv('USERPROFILE', str(e.home))
    monkeypatch.setenv('HOME', str(e.home))
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    e.projects = e.home / '.claude' / 'projects'
    # An underscore in the name: Claude Code really stores this project under the
    # underscore->dash spelling, so that is the dir the fence has to find.
    e.proj = tmp_path / 'work' / 'my_proj'
    e.proj.mkdir(parents=True)
    e.mem = e.projects / _enc(e.proj).replace('_', '-') / 'memory'
    e.mem.mkdir(parents=True)
    for name in ('MEMORY.md', 'MEMORY_ARCHIVE.md', 'SESSION_LOG.md', 'topic_x.md'):
        (e.mem / name).write_text('x', encoding='utf-8')
    e.other_proj = tmp_path / 'work' / 'other_proj'
    e.other_proj.mkdir()
    e.other_mem = e.projects / _enc(e.other_proj).replace('_', '-') / 'memory'
    e.other_mem.mkdir(parents=True)
    (e.other_mem / 'MEMORY.md').write_text('x', encoding='utf-8')
    (e.other_mem / 'theirs.md').write_text('x', encoding='utf-8')
    monkeypatch.setattr(server_memory, 'CLAUDE_HOME', e.projects, raising=False)

    def call(tool, path, cwd=None, extra=None):
        key = 'notebook_path' if tool == 'NotebookEdit' else 'file_path'
        ti = {key: str(path), **(extra or {})}
        return fence.classify_action(tool, ti, str(cwd or e.proj))
    e.call = call
    return e


# ── derivation matches the server ───────────────────────────────────────────

@pytest.mark.parametrize('state', ['primary-only', 'alt-only', 'both-alt-newer',
                                   'both-primary-newer'])
def test_memory_dir_matches_the_servers_pick(tmp_path, monkeypatch, state):
    """Pinned against mc.memory._native_memory_path, so the two cannot drift."""
    home = tmp_path / 'h'
    projects = home / '.claude' / 'projects'
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setattr(server_memory, 'CLAUDE_HOME', projects, raising=False)
    proj = tmp_path / 'a_b' / 'c_d'
    proj.mkdir(parents=True)
    primary = projects / _enc(proj) / 'memory'
    alt = projects / _enc(proj).replace('_', '-') / 'memory'
    assert primary != alt
    mk = {'primary-only': [(primary, 0)], 'alt-only': [(alt, 0)],
          'both-alt-newer': [(primary, 0), (alt, 100)],
          'both-primary-newer': [(alt, 0), (primary, 100)]}[state]
    for d, bump in mk:
        d.mkdir(parents=True)
        f = d / 'MEMORY.md'
        f.write_text('x', encoding='utf-8')
        st = f.stat()
        os.utime(f, (st.st_atime, st.st_mtime + bump))
    assert fence._own_memory_dir(str(proj)) == server_memory._native_memory_path(str(proj)).parent


def test_no_memory_dir_yet_defaults_to_the_servers_primary_spelling(tmp_path, monkeypatch):
    home = tmp_path / 'h'
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setattr(server_memory, 'CLAUDE_HOME', home / '.claude' / 'projects', raising=False)
    proj = tmp_path / 'p_q'
    proj.mkdir()
    assert fence._own_memory_dir(str(proj)) == server_memory._native_memory_path(str(proj)).parent


def test_project_root_strips_an_agent_worktree(tmp_path):
    proj = tmp_path / 'p'
    wt = proj / '.clayrune' / 'agents' / 'abc123'
    (wt / 'sub').mkdir(parents=True)
    assert fence._session_project_root(str(proj)) == proj.resolve()
    assert fence._session_project_root(str(wt)) == proj.resolve()
    assert fence._session_project_root(str(wt / 'sub')) == proj.resolve()
    # No agent id after `agents`: not a worktree, so nothing is stripped.
    bare = proj / '.clayrune' / 'agents'
    assert fence._session_project_root(str(bare)) == bare.resolve()


# ── allowed ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('tool', ['Write', 'Edit', 'MultiEdit'])
@pytest.mark.parametrize('name', ['topic_x.md', 'brand_new_note.md', 'UPPER.MD',
                                  'with space.md', 'a.b.md'])
def test_own_topic_file_is_allowed(env, tool, name):
    assert not env.call(tool, env.mem / name).blocked


def test_relative_path_resolves_against_the_session_cwd(env):
    rel = os.path.relpath(env.mem / 'topic_x.md', env.proj)
    assert not env.call('Write', rel).blocked
    assert env.call('Write', os.path.relpath(env.mem / 'MEMORY.md', env.proj)).blocked


def test_agent_worktree_cwd_reaches_the_projects_memory_dir(env):
    wt = env.proj / '.clayrune' / 'agents' / 'abc123'
    wt.mkdir(parents=True)
    assert not env.call('Write', env.mem / 'topic_x.md', cwd=wt).blocked
    assert env.call('Write', env.mem / 'MEMORY.md', cwd=wt).blocked
    assert env.call('Write', env.other_mem / 'theirs.md', cwd=wt).blocked


# ── blocked: Clayrune-maintained files, shape, other projects ───────────────

@pytest.mark.parametrize('name', [
    'MEMORY.md', 'memory.md', 'Memory.MD', 'MEMORY_ARCHIVE.md', 'memory_archive.md',
    'SESSION_LOG.md', 'session_log.md',
    'x.json', 'x.txt', 'x', '.md', 'x.md:stream', 'x.md::$DATA',
    'MEMORY.md.', 'MEMORY.md ', 'MEMORY~1.MD', 'SESSIO~1.MD',
    'sub/x.md', 'sub/../../x.md',
])
@pytest.mark.parametrize('tool', ['Write', 'Edit', 'MultiEdit'])
def test_server_files_and_odd_names_stay_blocked(env, tool, name):
    d = env.call(tool, str(env.mem) + os.sep + name.replace('/', os.sep))
    assert d.blocked and not d.overridable


def test_trailing_dot_or_space_is_refused_or_is_the_plain_name(env):
    """'x.md.' is refused outright. Windows also drops a trailing space, so
    'x.md ' IS 'x.md' there (an allowed topic file); elsewhere it is a different
    name and refused. The server-maintained names with these suffixes
    ('MEMORY.md.', 'MEMORY.md ') are in the always-blocked list above."""
    assert env.call('Write', str(env.mem) + os.sep + 'x.md.').blocked
    assert env.call('Write', str(env.mem) + os.sep + 'x.md ').blocked == (os.name != 'nt')


def test_other_projects_memory_stays_blocked(env):
    for name in ('theirs.md', 'MEMORY.md', 'brand_new.md'):
        d = env.call('Write', env.other_mem / name)
        assert d.blocked and not d.overridable, name


def test_everything_else_under_dot_claude_stays_blocked(env):
    claude = env.home / '.claude'
    for p in (claude / 'settings.json', claude / 'CLAUDE.md',
              claude / 'skills' / 's' / 'SKILL.md',
              claude / 'hooks' / 'process-guard.py',
              env.projects / _enc(env.proj).replace('_', '-') / 'x.md',
              env.projects / 'x.md',
              env.mem / 'x.ipynb'):
        d = env.call('Write', p)
        assert d.blocked and not d.overridable, p
    assert env.call('NotebookEdit', env.mem / 'x.ipynb').blocked


@pytest.mark.parametrize('tail', [
    '../settings.json',
    '../x.md',
    '../../x.md',
    '../../{other}/memory/theirs.md',
    '../../../CLAUDE.md',
    '../memory/../memory/MEMORY.md',
])
def test_traversal_out_of_the_memory_dir_is_blocked(env, tail):
    tail = tail.replace('{other}', env.other_mem.parent.name)
    raw = str(env.mem) + os.sep + tail.replace('/', os.sep)
    d = env.call('Write', raw)
    assert d.blocked and not d.overridable


def test_traversal_that_lands_back_inside_is_judged_on_the_resolved_name(env):
    raw = str(env.mem) + os.sep + 'sub' + os.sep + '..' + os.sep + 'topic_x.md'
    assert not env.call('Write', raw).blocked
    raw = str(env.mem) + os.sep + 'sub' + os.sep + '..' + os.sep + 'MEMORY.md'
    assert env.call('Write', raw).blocked


@pytest.mark.skipif(os.name != 'nt', reason='\\\\?\\ and short-name spellings are Windows paths')
def test_windows_path_spellings(env):
    raw = str(env.mem / 'topic_x.md')
    assert env.call('Write', '\\\\?\\' + raw).blocked
    assert env.call('Write', raw.replace('\\', '/').replace('/topic_x.md', '/MEMORY.md')).blocked
    assert not env.call('Write', raw.replace('\\', '/')).blocked
    assert not env.call('Write', raw.upper()).blocked           # NTFS is case-insensitive
    assert env.call('Write', str(env.mem / 'MEMORY.MD')).blocked
    assert env.call('Write', str(env.mem) + '\\.\\MEMORY.md').blocked
    assert env.call('Write', str(env.mem) + '\\\\MEMORY.md').blocked


# ── the project comes from the session, nothing else ────────────────────────

def test_project_is_taken_from_cwd_not_from_the_path_or_tool_input(env):
    # Same two files, judged from the OTHER project's session: roles swap.
    assert env.call('Write', env.mem / 'topic_x.md', cwd=env.other_proj).blocked
    assert not env.call('Write', env.other_mem / 'theirs.md', cwd=env.other_proj).blocked
    # Fields a model could add to tool_input name the wrong project; ignored.
    lie = {'cwd': str(env.other_proj), 'project_path': str(env.other_proj),
           'project_id': 'other_proj', 'memory_dir': str(env.other_mem)}
    assert env.call('Write', env.other_mem / 'theirs.md', cwd=env.proj, extra=lie).blocked
    assert not env.call('Write', env.mem / 'topic_x.md', cwd=env.proj, extra=lie).blocked


def test_a_made_up_project_root_gets_no_extra_reach(env):
    """`cd` into <proj>/<anything>/.clayrune/agents/x moves the derived root to
    <proj>/<anything>, which has no memory dir of any real project."""
    fake = env.proj / 'fake' / '.clayrune' / 'agents' / 'x'
    fake.mkdir(parents=True)
    assert env.call('Write', env.mem / 'topic_x.md', cwd=fake).blocked
    assert env.call('Write', env.other_mem / 'theirs.md', cwd=fake).blocked


def test_unresolvable_input_fails_closed(env):
    assert env.call('Write', '').blocked is False        # no path at all: nothing to fence
    assert not fence._is_own_memory_topic_file('', str(env.proj))
    assert not fence._is_own_memory_topic_file('a\x00b.md', str(env.proj))
    assert not fence._is_own_memory_topic_file(str(env.mem / 'topic_x.md'), '\x00')


# ── links: a path that RESOLVES outside the own dir is blocked ──────────────

def _link_dir(link: Path, target: Path):
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError:
        pass
    if os.name == 'nt':
        r = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return
    pytest.skip('cannot create a directory link on this box')


def _unlink_dir(link: Path):
    try:
        os.rmdir(link)              # a junction/symlink is removed, never followed
    except OSError:
        link.unlink()


def test_directory_link_inside_the_memory_dir_cannot_reach_out(env):
    link = env.mem / 'sub'
    _link_dir(link, env.other_mem)
    try:
        d = env.call('Write', link / 'theirs.md')
        assert d.blocked and not d.overridable
        assert env.call('Write', link / 'MEMORY.md').blocked
    finally:
        _unlink_dir(link)


def test_file_symlink_to_a_protected_or_foreign_file_is_blocked(env):
    for name, target in (('to_memory.md', env.mem / 'MEMORY.md'),
                         ('to_theirs.md', env.other_mem / 'theirs.md')):
        try:
            os.symlink(target, env.mem / name)
        except OSError:
            pytest.skip('cannot create file symlinks on this box')
        assert env.call('Write', env.mem / name).blocked, name


def test_own_memory_dir_that_is_itself_a_link_is_not_trusted(env):
    """A link at projects/<own>/memory pointing at another project's memory
    would turn 'my dir' into 'theirs'. Only the projects root may be a link."""
    for child in env.mem.iterdir():
        child.unlink()
    env.mem.rmdir()
    _link_dir(env.mem, env.other_mem)
    try:
        assert env.call('Write', env.mem / 'theirs.md').blocked
        assert env.call('Write', env.mem / 'new.md').blocked
    finally:
        _unlink_dir(env.mem)


def test_the_projects_root_may_be_a_link(tmp_path, monkeypatch):
    real_home = tmp_path / 'real_home'
    real_projects = real_home / '.claude' / 'projects'
    real_projects.mkdir(parents=True)
    home = tmp_path / 'home'
    (home / '.claude').mkdir(parents=True)
    _link_dir(home / '.claude' / 'projects', real_projects)
    try:
        monkeypatch.setenv('USERPROFILE', str(home))
        monkeypatch.setenv('HOME', str(home))
        proj = tmp_path / 'proj'
        proj.mkdir()
        mem = home / '.claude' / 'projects' / _enc(proj) / 'memory'
        mem.mkdir(parents=True)
        d = fence.classify_action('Write', {'file_path': str(mem / 'n.md')}, str(proj))
        assert not d.blocked
    finally:
        _unlink_dir(home / '.claude' / 'projects')


# ── Bash is untouched ───────────────────────────────────────────────────────

def test_bash_classification_ignores_the_memory_allowance(env):
    for cmd in (f'echo x > "{env.mem / "topic_x.md"}"', f'cp a "{env.mem}"',
                f'Set-Content "{env.mem / "MEMORY.md"}" x'):
        assert fence.classify_action('Bash', {'command': cmd}, str(env.proj)) == \
            fence.classify_bash(cmd)
        assert fence.classify_action('PowerShell', {'command': cmd}, str(env.proj)) == \
            fence.classify_bash(cmd)


# ── Codex apply_patch gets the same rule ────────────────────────────────────

def _patch(*lines):
    return {'command': '\n'.join(['*** Begin Patch', *lines, '*** End Patch'])}


def test_apply_patch_follows_the_same_rule(env):
    cwd = str(env.proj)
    ok = _patch(f'*** Add File: {env.mem / "new_note.md"}', '+x',
                f'*** Update File: {env.mem / "topic_x.md"}', '@@', '-x', '+y')
    assert not fence.classify_action('apply_patch', ok, cwd).blocked
    for bad in (
        _patch(f'*** Update File: {env.mem / "MEMORY.md"}'),
        _patch(f'*** Add File: {env.mem / "SESSION_LOG.md"}', '+x'),
        _patch(f'*** Add File: {env.other_mem / "theirs.md"}', '+x'),
        _patch(f'*** Add File: {env.home / ".claude" / "settings.json"}', '+x'),
        _patch(f'*** Add File: {env.mem / "sub" / "x.md"}', '+x'),
        _patch(f'*** Update File: {env.mem / "topic_x.md"}',
               f'*** Move to: {env.mem / "MEMORY.md"}'),
        # one allowed target does not carry a forbidden one through
        _patch(f'*** Add File: {env.mem / "new_note.md"}', '+x',
               f'*** Add File: {env.home / ".claude" / "settings.json"}', '+x'),
    ):
        d = fence.classify_action('apply_patch', bad, cwd)
        assert d.blocked and not d.overridable, bad


def test_apply_patch_relative_paths_resolve_against_the_payload_cwd(env):
    rel = os.path.relpath(env.mem / 'new_note.md', env.proj).replace('\\', '/')
    assert not fence.classify_action(
        'apply_patch', _patch(f'*** Add File: {rel}', '+x'), str(env.proj)).blocked
    rel = os.path.relpath(env.mem / 'MEMORY.md', env.proj).replace('\\', '/')
    assert fence.classify_action(
        'apply_patch', _patch(f'*** Add File: {rel}', '+x'), str(env.proj)).blocked


def _main(monkeypatch, payload, argv):
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    return fence.main(argv)


def test_armed_main_applies_the_rule_to_write_and_apply_patch(env, monkeypatch, capsys):
    """The Codex path end to end: `--armed`, payload cwd, absolute patch paths."""
    def w(path):
        return {'tool_name': 'Write', 'cwd': str(env.proj),
                'tool_input': {'file_path': str(path), 'content': 'x'}}

    def ap(*lines):
        return {'tool_name': 'apply_patch', 'cwd': str(env.proj), 'tool_input': _patch(*lines)}
    assert _main(monkeypatch, w(env.mem / 'topic_x.md'), ['--armed']) == 0
    assert _main(monkeypatch, ap(f'*** Add File: {env.mem / "n.md"}', '+x'), ['--armed']) == 0
    capsys.readouterr()
    for bad in (w(env.mem / 'MEMORY.md'), w(env.other_mem / 'theirs.md'),
                w(env.home / '.claude' / 'settings.json'),
                ap(f'*** Update File: {env.mem / "MEMORY_ARCHIVE.md"}')):
        assert _main(monkeypatch, bad, ['--armed']) == 2
        assert 'STEWARD FENCE blocked' in capsys.readouterr().err


def test_unattended_trigger_session_uses_the_same_rule(env, monkeypatch, capsys):
    """Dispatched/scheduled/workflow/hivemind sessions arm through the server's
    trigger_type; a blocked memory write there is never passable by 'Allow once'."""
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'csid-1')
    monkeypatch.setattr(fence, '_lookup_trigger_type', lambda sid: {
        'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    monkeypatch.setattr(fence, '_consume_attend_once_pass', lambda: pytest.fail('spent a pass'))
    ok = {'tool_name': 'Write', 'cwd': str(env.proj),
          'tool_input': {'file_path': str(env.mem / 'topic_x.md')}}
    bad = {'tool_name': 'Write', 'cwd': str(env.proj),
           'tool_input': {'file_path': str(env.mem / 'MEMORY.md')}}
    assert _main(monkeypatch, ok, []) == 0
    assert _main(monkeypatch, bad, []) == 2
    assert 'Do NOT retry it. Instead post' in capsys.readouterr().err


def test_pass_helpers_use_the_same_cwd(env):
    ok = [('Write', {'file_path': str(env.mem / 'topic_x.md')})]
    assert fence._blocked_leaves('Write', ok[0][1], str(env.proj)) == []
    assert fence._blocked_leaves('Write', ok[0][1], str(env.other_proj)) != []
    bad = [('Write', {'file_path': str(env.mem / 'MEMORY.md')})]
    assert fence._pass_can_cover(bad, str(env.proj)) is False


def test_install_dir_and_supply_chain_rules_still_win(env):
    """The allowance only lifts the generic `.claude` block; the fence's own code
    and the learning-loop artifacts stay human-owned wherever they are."""
    assert env.call('Write', REPO / 'steward' / 'fence.py').blocked
    assert env.call('Write', env.mem / 'data' / 'skills' / 'x.md').blocked

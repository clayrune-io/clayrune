"""P87: re-running the installer must never discard local work on a failed pull.

Runs the real Step 1 text of installer/install.sh and installer/install.ps1
(sliced out of the scripts) against scratch git repos whose origin is a local
bare repo. Nothing here touches a real install or the network.
"""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SH = shutil.which('sh') or shutil.which('bash') or ''
PS = shutil.which('powershell') or shutil.which('pwsh') or ''
GIT = shutil.which('git') or 'git'

pytestmark = pytest.mark.skipif(not shutil.which('git'), reason='git required')

SCENARIOS = [
    'ff_clean',            # plain fast-forward: pull works, no fallback
    'force_push_clean',    # upstream rewritten, nothing local: fallback must still work
    'untracked_only',      # upstream rewritten, only an untracked file: kept, fallback runs
    'edit_overlap',        # tracked edit the incoming commit touches (pull fails)
    'edit_force_push',     # tracked edit + upstream rewritten
    'local_commit',        # unpushed commit + upstream moved on (pull fails)
    'local_commit_force',  # unpushed commit + upstream rewritten
    'local_only_branch',   # commit on a branch origin has never heard of
]
MUST_REFUSE = {'edit_overlap', 'edit_force_push', 'local_commit',
               'local_commit_force', 'local_only_branch'}


def _git(cwd, *args):
    r = subprocess.run([GIT, '-C', str(cwd), '-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
                        '-c', 'commit.gpgsign=false', *args],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, f'git {args}: {r.stdout}{r.stderr}'
    return r.stdout.strip()


def _build(tmp_path, scenario):
    """Returns (install_dir, expected) where expected describes the pass state."""
    origin = tmp_path / 'origin.git'
    seed = tmp_path / 'seed'
    install = tmp_path / 'Clayrune'
    subprocess.run([GIT, 'init', '--bare', '-b', 'master', str(origin)], check=True,
                   capture_output=True)
    seed.mkdir()
    subprocess.run([GIT, 'init', '-b', 'master', str(seed)], check=True, capture_output=True)
    (seed / 'a.txt').write_text('one\n')
    (seed / 'b.txt').write_text('base\n')
    _git(seed, 'add', 'a.txt', 'b.txt')
    _git(seed, 'commit', '-m', 'c1')
    _git(seed, 'remote', 'add', 'origin', str(origin))
    _git(seed, 'push', 'origin', 'master')
    subprocess.run([GIT, 'clone', str(origin), str(install)], check=True, capture_output=True)

    def upstream_commit():
        (seed / 'a.txt').write_text('upstream\n')
        _git(seed, 'commit', '-am', 'upstream change')
        _git(seed, 'push', 'origin', 'master')

    def upstream_rewrite():
        (seed / 'a.txt').write_text('rewritten\n')
        _git(seed, 'commit', '--amend', '-am', 'c1 rewritten')
        _git(seed, 'push', '--force', 'origin', 'master')

    if scenario == 'ff_clean':
        upstream_commit()
    elif scenario == 'force_push_clean':
        upstream_rewrite()
    elif scenario == 'untracked_only':
        (install / 'config.json').write_text('{"mine": true}\n')
        upstream_rewrite()
    elif scenario == 'edit_overlap':
        (install / 'a.txt').write_text('my local edit\n')
        upstream_commit()
    elif scenario == 'edit_force_push':
        (install / 'b.txt').write_text('my local edit\n')
        upstream_rewrite()
    elif scenario == 'local_commit':
        (install / 'b.txt').write_text('my commit\n')
        _git(install, 'commit', '-am', 'my local commit')
        upstream_commit()
    elif scenario == 'local_commit_force':
        (install / 'b.txt').write_text('my commit\n')
        _git(install, 'commit', '-am', 'my local commit')
        upstream_rewrite()
    elif scenario == 'local_only_branch':
        _git(install, 'checkout', '-b', 'feature')
        (install / 'b.txt').write_text('my commit\n')
        _git(install, 'commit', '-am', 'my feature commit')
    return install


def _slice_sh():
    src = (ROOT / 'installer/install.sh').read_text(encoding='utf-8')
    fn = re.search(r'^_clayrune_resync_blockers\(\) \{.*?^\}\n', src, re.S | re.M)
    step = src.split('# ── [STEP 1/5]', 1)[1].split('# ── [STEP 2/5]', 1)[0]
    step = '#' + step  # the split leaves the tail of the banner comment line
    # Before the fix there is no guard function; slicing must still work.
    return (fn.group(0) if fn else ''), step


def _run_sh(install):
    fn, step = _slice_sh()
    script = (
        "set -e\nB=''; R=''; C=''; Y=''; G=''; E=''\n"
        f"INSTALL_DIR='{install.as_posix()}'\nOS=linux\nREPO_URL=unused\n"
        + fn + "\n" + step + "\necho SCRIPT_REACHED_END\n"
    )
    return subprocess.run([SH, '-c', script], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=60)


def _slice_ps():
    src = (ROOT / 'installer/install.ps1').read_text(encoding='utf-8')
    fn = re.search(r'^function Get-ResyncBlockers \{.*?^\}\n', src, re.S | re.M)
    step = "Write-Host '[STEP 1/5] Cloning repository...'" + src.split(
        "Write-Host '[STEP 1/5] Cloning repository...'", 1)[1].split("Write-Host '[STEP 1/5] OK'", 1)[0]
    return (fn.group(0) if fn else ''), step


def _run_ps(install):
    fn, step = _slice_ps()
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "function Exit-WithContact { param($c) Write-Output \"EXIT-WITH $c\"; exit $c }\n"
        f"$installDir = '{str(install)}'\n$repoUrl = 'unused'\n"
        + fn + "\n" + step + "\nWrite-Output 'SCRIPT_REACHED_END'\n"
    )
    return subprocess.run([PS, '-NoProfile', '-NonInteractive', '-Command', script],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=90)


def _check(scenario, install, before, result):
    out = result.stdout + result.stderr
    head = _git(install, 'rev-parse', 'HEAD')
    branch = _git(install, 'rev-parse', '--abbrev-ref', 'HEAD')
    if scenario in MUST_REFUSE:
        assert result.returncode != 0, out
        assert 'SCRIPT_REACHED_END' not in out, out
        # Nothing of the user's changed: same branch, same commit, same edits.
        assert (head, branch) == before, f'checkout moved {before} -> {(head, branch)}\n{out}'
        if scenario.startswith('edit'):
            f = 'a.txt' if scenario == 'edit_overlap' else 'b.txt'
            assert (install / f).read_text() == 'my local edit\n', out
        # And it says how to save the work, by hand.
        assert 'reset --hard' in out, out
        assert ('stash' in out) if scenario.startswith('edit') else ('branch' in out), out
        assert 'untouched' not in out.lower(), out
    else:
        assert result.returncode == 0, out
        assert 'SCRIPT_REACHED_END' in out, out
        remote = _git(install, 'rev-parse', 'origin/master')
        assert head == remote, out
        if scenario == 'untracked_only':
            assert (install / 'config.json').read_text() == '{"mine": true}\n'
        assert 'untouched' not in out.lower(), out


@pytest.mark.skipif(not SH, reason='sh required')
@pytest.mark.parametrize('scenario', SCENARIOS)
def test_install_sh_resync_keeps_local_work(tmp_path, scenario):
    install = _build(tmp_path, scenario)
    before = (_git(install, 'rev-parse', 'HEAD'), _git(install, 'rev-parse', '--abbrev-ref', 'HEAD'))
    _check(scenario, install, before, _run_sh(install))


@pytest.mark.skipif(not PS, reason='PowerShell required')
@pytest.mark.parametrize('scenario', SCENARIOS)
def test_install_ps1_resync_keeps_local_work(tmp_path, scenario):
    install = _build(tmp_path, scenario)
    before = (_git(install, 'rev-parse', 'HEAD'), _git(install, 'rev-parse', '--abbrev-ref', 'HEAD'))
    _check(scenario, install, before, _run_ps(install))

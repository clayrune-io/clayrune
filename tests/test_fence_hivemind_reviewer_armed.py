"""The fence must arm for a Hivemind reviewer session (review 2026-11-16, P64).

mc/hivemind_reviewer.py stamps `trigger_type: 'hivemind_reviewer'` on the
independent reviewer session, whose task opens "You run unattended". The fence
arms only for names in `steward.fence._UNATTENDED_TRIGGER_TYPES`, and
`mc.launch_marker.UNATTENDED_TRIGGER_TYPES` (the fallback marker, retry budgets,
fail-closed launch) mirrors it. Neither listed the reviewer, so that session ran
with the fence off: `main()` returned 0 before `classify_action` ran.

steward/fence.py is human-owned, so the fix ships as
docs/patches/fence-hivemind-reviewer-armed.patch (it also adds the name to
mc/launch_marker.py in the same patch, because tests/test_launch_marker.py pins
the two sets to each other and they must change together).

Same shape as the timeout patch in tests/test_fence_fail_closed_unattended.py:
the tests that need the fix are xfail(strict) keyed on whether the fence already
lists the name, and a second group applies the patch to a throwaway copy outside
the repo and runs the patched module, so the patch itself is proven before it is
applied. Once it is applied the xfail tests turn into plain passes (strict: they
also fail loudly if the patch is applied twice or the name is later removed),
and the throwaway-copy tests skip.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import launch_marker  # noqa: E402
from steward import fence  # noqa: E402

PATCH = PROJECT_ROOT / 'docs' / 'patches' / 'fence-hivemind-reviewer-armed.patch'
REVIEWER = 'hivemind_reviewer'
OLD_FIVE = {'schedule', 'workflow', 'dispatch', 'hivemind_orchestrator', 'hivemind_worker'}

_HAS_FIX = REVIEWER in fence._UNATTENDED_TRIGGER_TYPES
_needs_patch = pytest.mark.xfail(
    not _HAS_FIX, strict=True,
    reason='steward/fence.py is human-owned: apply docs/patches/fence-hivemind-reviewer-armed.patch')


# ── the live files (xfail until the patch is applied) ───────────────────────

@_needs_patch
def test_the_fence_lists_the_reviewer_trigger():
    assert REVIEWER in fence._UNATTENDED_TRIGGER_TYPES
    assert 'manual' not in fence._UNATTENDED_TRIGGER_TYPES


@_needs_patch
def test_the_launch_marker_lists_it_too_and_sets_the_marker(monkeypatch):
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'fence_unattended_enabled', True)
    assert REVIEWER in launch_marker.UNATTENDED_TRIGGER_TYPES
    env = launch_marker.launch_env(REVIEWER, base={})
    assert env[launch_marker.LAUNCH_MARKER_ENV] == '1'


@_needs_patch
def test_a_confirmed_reviewer_lookup_arms_the_fence(monkeypatch):
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'csid-reviewer')
    monkeypatch.setattr(fence, '_lookup_trigger_type',
                        lambda sid: {'trigger_type': REVIEWER, 'fence_unattended_enabled': True})
    assert fence._should_arm_for_unattended_trigger() is True


@_needs_patch
def test_every_trigger_type_the_server_stamps_is_armed_or_manual():
    """The review noted no test enumerates the trigger types the server stamps,
    so a sixth launch path slipped through the pinned five. Scan mc/ for the
    literals assigned to trigger_type; every one except 'manual' must arm."""
    pat = re.compile(r"""trigger_type['"]?\s*[:=]\s*['"]([a-z_]+)['"]""")
    stamped = set()
    for path in (PROJECT_ROOT / 'mc').rglob('*.py'):
        stamped |= set(pat.findall(path.read_text(encoding='utf-8', errors='replace')))
    stamped.discard('manual')
    assert REVIEWER in stamped, 'scan no longer finds the reviewer: fix the scan, not the test'
    assert OLD_FIVE <= stamped
    assert stamped <= set(fence._UNATTENDED_TRIGGER_TYPES), \
        sorted(stamped - set(fence._UNATTENDED_TRIGGER_TYPES))


# ── the patch itself, on a throwaway copy ───────────────────────────────────

# Runs the PATCHED fence's real main() with a hook event on stdin. Only the
# server lookup is stubbed (it would otherwise ask whatever is on :5199).
_RUNNER = '''
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("fence_under_test", sys.argv[1])
fence = importlib.util.module_from_spec(spec)
sys.modules["fence_under_test"] = fence
spec.loader.exec_module(fence)
fence._lookup_trigger_type = lambda sid: {
    "trigger_type": sys.argv[2], "fence_unattended_enabled": True}
sys.exit(fence.main([]))
'''

_PUSH = json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'git push origin master'},
                    'cwd': None})


def _lf(path: Path) -> bytes:
    return path.read_bytes().replace(b'\r\n', b'\n')


@pytest.fixture
def copies(tmp_path):
    """(unpatched_dir, patched_dir): two copies of the live fence and launch
    marker, the second with the patch applied. Skipped once the fix is live."""
    if _HAS_FIX:
        pytest.skip('steward/fence.py already carries the fix')
    out = []
    for name in ('before', 'after'):
        root = tmp_path / name
        for rel in ('steward/fence.py', 'mc/launch_marker.py'):
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(_lf(PROJECT_ROOT / rel))   # LF: the patch is LF
        out.append(root)
    r = subprocess.run(['git', 'apply', '--unsafe-paths', str(PATCH)], cwd=out[1],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return out


def _run_fence(root: Path, trigger: str, tmp_path: Path, command_event: str = _PUSH):
    runner = tmp_path / 'runner.py'
    runner.write_text(_RUNNER, encoding='utf-8')
    env = {'CLAUDE_CODE_SESSION_ID': 'csid-probe', 'PATH': ''}
    import os
    env['SYSTEMROOT'] = os.environ.get('SYSTEMROOT', '')
    return subprocess.run([sys.executable, '-I', str(runner), str(root / 'steward' / 'fence.py'),
                           trigger], input=command_event, capture_output=True, text=True,
                          env=env, timeout=60)


def test_the_patch_adds_the_name_to_both_sets(copies):
    after = copies[1]
    fence_src = (after / 'steward' / 'fence.py').read_text(encoding='utf-8')
    marker_src = (after / 'mc' / 'launch_marker.py').read_text(encoding='utf-8')
    for src in (fence_src, marker_src):
        assert "'hivemind_reviewer'," in src
    # nothing else in either file moved
    for rel, root in (('steward/fence.py', after), ('mc/launch_marker.py', after)):
        assert (root / rel).read_bytes().count(b'hivemind_reviewer') >= 1


def test_unpatched_fence_lets_a_reviewer_push_through(copies, tmp_path):
    """The defect, reproduced: same event, same stubbed lookup, no patch."""
    r = _run_fence(copies[0], REVIEWER, tmp_path)
    assert r.returncode == 0, (r.returncode, r.stderr)


def test_patched_fence_blocks_a_reviewer_push(copies, tmp_path):
    r = _run_fence(copies[1], REVIEWER, tmp_path)
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert 'STEWARD FENCE blocked' in r.stderr
    assert r.stdout == ''   # exit 2 + stderr only: the fail-closed block contract


@pytest.mark.parametrize('trigger', sorted(OLD_FIVE))
def test_patched_fence_still_blocks_the_original_five(copies, tmp_path, trigger):
    assert _run_fence(copies[1], trigger, tmp_path).returncode == 2


@pytest.mark.parametrize('trigger', ['manual', 'some_future_type'])
def test_patched_fence_leaves_attended_and_unknown_sessions_alone(copies, tmp_path, trigger):
    assert _run_fence(copies[1], trigger, tmp_path).returncode == 0


def test_patched_fence_lets_a_reviewer_run_harmless_reads(copies, tmp_path):
    ev = json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'git diff --stat'},
                     'cwd': None})
    assert _run_fence(copies[1], REVIEWER, tmp_path, ev).returncode == 0


def test_the_two_patched_sets_agree(copies):
    """tests/test_launch_marker.py::test_trigger_type_set_is_pinned_to_the_fence,
    run against the patched copies instead of the live files."""
    def names(path, anchor):
        text = path.read_text(encoding='utf-8')
        block = text[text.index(anchor):]
        block = block[:block.index('}')]
        return set(re.findall(r"'([a-z_]+)'", block))
    after = copies[1]
    assert names(after / 'steward' / 'fence.py', '_UNATTENDED_TRIGGER_TYPES = {') == \
        names(after / 'mc' / 'launch_marker.py', 'UNATTENDED_TRIGGER_TYPES = frozenset({') == \
        OLD_FIVE | {REVIEWER}

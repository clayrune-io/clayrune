"""HR-7: fake runtime only; real disposable Git for nested output evidence."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest

from mc import hivemind_native_workflow as native


def git(cwd, *args):
    result = subprocess.run(['git', *args], cwd=cwd, capture_output=True,
                            stdin=subprocess.DEVNULL, timeout=15)
    assert result.returncode == 0, result.stderr.decode('utf-8', 'replace')
    return result.stdout.decode('utf-8').strip()


class FakeDriver:
    """No calls to Claude, runtime dispatch, logins, networking or paid APIs."""
    def __init__(self):
        self.cap = native.Capability('supported', 'fixture-1', 'bounded headless fixture')
        self.observations = {}
        self.active = {}
        self.launched = []
        self.cancelled = []
        self.imports = []
        self.probes = []
        self.launch_error = False
        self.cancel_error = set()
        self.observe_error = set()
        self.import_error = False
        self.skip_import = False
        self.import_hook = None

    def probe(self, parent):
        self.probes.append(parent)
        return self.cap

    def launch(self, child):
        self.launched.append(child)
        self.observations[child.session_id] = native.Observation(child, 'running', True)
        self.active.setdefault(child.parent.session_id, set()).add(child.session_id)
        if self.launch_error:
            raise RuntimeError('lost launch acknowledgement')

    def observe(self, child):
        if child.session_id in self.observe_error:
            raise RuntimeError('child runtime unavailable')
        return self.observations[child.session_id]

    def active_children(self, parent):
        return set(self.active.get(parent.session_id, set()))

    def import_output(self, child, commit):
        self.imports.append(child.session_id)
        if self.import_error:
            raise RuntimeError('import failed')
        if not self.skip_import:
            # Test-only importer; a future production driver must use the
            # existing native standards + landing gates, never this fake.
            git(child.parent.cwd, 'merge', '--no-edit', commit)
        if self.import_hook:
            self.import_hook(child)

    def cancel(self, child):
        self.cancelled.append(child.session_id)
        if child.session_id in self.cancel_error:
            raise RuntimeError('stop unavailable')
        self.finish(child, state='cancelled')

    def finish(self, child, *, state='completed', commit=None):
        self.observations[child.session_id] = native.Observation(
            child, state, False, commit or git(child.cwd, 'rev-parse', 'HEAD'))
        self.active.setdefault(child.parent.session_id, set()).discard(child.session_id)


@pytest.fixture
def rig(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q', '-b', 'master')
    git(repo, 'config', 'user.name', 'Fixture')
    git(repo, 'config', 'user.email', 'fixture@invalid.local')
    (repo / 'owned').mkdir()
    (repo / 'owned' / 'base.txt').write_text('base\n')
    (repo / 'outside.txt').write_text('outside\n')
    git(repo, 'add', 'owned/base.txt', 'outside.txt')
    git(repo, 'commit', '-qm', 'base')
    base = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'update-ref', 'refs/remotes/origin/master', base)
    run = native.Run('project', 7, 'hm_run')
    driver = FakeDriver()
    ledger = tmp_path / 'run'
    adapter = native.WorkflowAdapter(ledger, run, driver)

    class Rig:
        def parent(self, index=1, **overrides):
            cwd = tmp_path / ('parent' + str(index))
            branch = 'parent-' + str(index)
            git(repo, 'worktree', 'add', '-qb', branch, str(cwd), base)
            return native.Parent(**dict(dict(
                workstream_id='ws' + str(index), assignment_attempt=1,
                session_id='hm_parent' + str(index), provider='claude',
                cwd=str(cwd.resolve()), branch=branch, base_commit=base,
                scope=('owned/',), ancestry=('coordinator',), guards_digest='a' * 64), **overrides))

        def child(self, parent, index=1, **overrides):
            sid = parent.session_id + '_child' + str(index)
            cwd = tmp_path / sid
            git(repo, 'worktree', 'add', '-qb', sid, str(cwd), base)
            return native.Child(**dict(dict(run=run, parent=parent, session_id=sid,
                cwd=str(cwd.resolve()), branch=sid, scope=('owned/',)), **overrides))

        def commit(self, child, name='owned/child.txt', contents='output\n'):
            path = Path(child.cwd) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
            git(child.cwd, 'add', name)
            git(child.cwd, 'commit', '-qm', 'child output')
            return git(child.cwd, 'rev-parse', 'HEAD')

    result = Rig()
    result.repo, result.base, result.run = repo, base, run
    result.driver, result.adapter, result.ledger = driver, adapter, ledger
    return result


@pytest.mark.parametrize('state', ['supported', 'unavailable', 'headless-unsupported'])
@pytest.mark.parametrize('required', [False, True])
def test_capability_matrix(rig, state, required):
    parent = rig.parent()
    rig.driver.cap = native.Capability(state, 'fixture', 'read-only fixture evidence')
    decision = native.select(parent, rig.driver, opt_in=True, required=required)
    assert decision['native'] is (state == 'supported')
    assert decision['held'] is (required and state != 'supported')
    assert decision['method'] == ('native' if state == 'supported' else 'unsupported')
    assert decision['ordinary_available']
    assert not rig.driver.launched and not rig.ledger.exists()


@pytest.mark.parametrize('provider', ['claude', 'codex', 'gemini', 'qwen', 'kiro', 'goose', 'aider', 'opencode'])
def test_opt_in_is_required_for_every_provider(rig, provider):
    parent = rig.parent(provider=provider)
    assert native.select(parent, rig.driver)['method'] == 'ordinary'
    assert native.select(parent, rig.driver, required=True)['held']
    assert not rig.driver.probes
    with pytest.raises(native.NativeHeld, match='opt-in'):
        rig.adapter.launch(rig.child(parent))
    assert not rig.driver.launched


@pytest.mark.parametrize('required', [True, False])
def test_non_claude_keeps_outer_protocol_without_native_claim(rig, required):
    parent = rig.parent(provider='codex')
    result = native.select(parent, rig.driver, opt_in=True, required=required)
    assert result['method'] == ('unsupported' if required else 'ordinary')
    assert not result['native'] and result['held'] is required
    assert result['ordinary_available'] and not rig.driver.probes


def test_probe_failure_is_visible_and_does_not_substitute_execution(rig, monkeypatch):
    parent = rig.parent()
    def fail(_):
        raise RuntimeError('probe unavailable')
    monkeypatch.setattr(rig.driver, 'probe', fail)
    result = native.select(parent, rig.driver, opt_in=True, required=True)
    assert result['held'] and result['method'] == 'unsupported'
    assert 'probe unavailable' in result['reason']
    with pytest.raises(native.NativeHeld, match='probe unavailable'):
        rig.adapter.launch(rig.child(parent), opt_in=True)
    assert not rig.driver.launched


def test_pins_are_inherited_and_persisted_before_launch(rig, monkeypatch):
    parent = rig.parent()
    child = rig.child(parent)
    original = rig.driver.launch
    def launch(spec):
        ledger = json.loads(rig.adapter.path.read_text())
        row = ledger['children'][child.session_id]
        assert row['status'] == 'launching' and row['key'] == child.key
        assert row['pins']['parent']['guards_digest'] == parent.guards_digest
        assert spec.ancestry == ('coordinator', parent.session_id)
        assert spec.run.project_generation == 7
        assert spec.parent.base_commit == rig.base and spec.scope == ('owned/',)
        assert spec.run.child_cap == 4
        original(spec)
    monkeypatch.setattr(rig.driver, 'launch', launch)
    assert rig.adapter.launch(child, opt_in=True)['status'] == 'running'


def test_nested_output_reaches_parent_and_never_master_or_remote(rig):
    parent = rig.parent()
    child = rig.child(parent)
    rig.adapter.launch(child, opt_in=True)
    assert rig.adapter.complete(parent)['status'] == 'pending'
    tip = rig.commit(child)
    rig.driver.finish(child)
    result = rig.adapter.complete(parent)
    assert result['status'] == 'completed' and result['commit'] == tip
    assert result['branch'] == parent.branch
    assert result['contributing_sessions'] == [parent.session_id, child.session_id]
    assert (Path(parent.cwd) / 'owned/child.txt').read_text() == 'output\n'
    assert git(rig.repo, 'rev-parse', 'master') == rig.base
    assert git(rig.repo, 'rev-parse', 'origin/master') == rig.base
    assert not (rig.repo / 'owned/child.txt').exists()
    # Restart/repeat proves output isn't imported twice.
    fresh = native.WorkflowAdapter(rig.ledger, rig.run, rig.driver)
    assert fresh.complete(parent) == result
    assert rig.driver.imports == [child.session_id]
    with pytest.raises(native.NativeHeld, match='parent held/stopped'):
        fresh.launch(rig.child(parent, 2), opt_in=True)


def test_multiple_nested_outputs_are_reachable_from_one_parent_handoff(rig):
    parent = rig.parent()
    children = [rig.child(parent, i) for i in (1, 2)]
    for index, child in enumerate(children):
        rig.adapter.launch(child, opt_in=True)
        rig.commit(child, 'owned/output' + str(index) + '.txt')
        rig.driver.finish(child)
    result = rig.adapter.complete(parent)
    assert result['status'] == 'completed'
    for index, child in enumerate(children):
        git(parent.cwd, 'merge-base', '--is-ancestor',
            rig.driver.observations[child.session_id].commit, result['commit'])
        assert (Path(parent.cwd) / ('owned/output' + str(index) + '.txt')).exists()
    assert result['contributing_sessions'] == [parent.session_id] + [c.session_id for c in children]


def test_four_children_shared_across_parents_and_not_refunded(rig):
    parents = [rig.parent(i) for i in (1, 2)]
    children = [rig.child(p, i) for p in parents for i in (1, 2)]
    for child in children:
        rig.adapter.launch(child, opt_in=True)
    assert len(rig.driver.launched) == 4
    for parent in parents:
        assert rig.adapter.cancel(parent)['status'] == 'cancelled'
    third = rig.parent(3)
    fresh = native.WorkflowAdapter(rig.ledger, rig.run, rig.driver)
    with pytest.raises(native.NativeHeld, match='shared total-run child budget'):
        fresh.launch(rig.child(third), opt_in=True)
    assert len(rig.driver.launched) == 4


def test_concurrent_admission_cannot_multiply_budget(rig):
    parents = [rig.parent(i) for i in range(1, 7)]
    children = [rig.child(p) for p in parents]
    def admit(child):
        try:
            native.WorkflowAdapter(rig.ledger, rig.run, rig.driver).launch(child, opt_in=True)
            return True
        except native.NativeHeld as exc:
            assert 'budget exhausted' in str(exc)
            return False
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(admit, children)) == 4
    assert len(rig.driver.launched) == 4


def test_uncertain_launch_survives_restart_without_second_launch(rig):
    parent = rig.parent()
    child = rig.child(parent)
    rig.driver.launch_error = True
    with pytest.raises(native.NativeHeld, match='lost launch acknowledgement'):
        rig.adapter.launch(child, opt_in=True)
    fresh = native.WorkflowAdapter(rig.ledger, rig.run, rig.driver)
    with pytest.raises(native.NativeHeld, match='parent held/stopped'):
        fresh.launch(child, opt_in=True)
    assert len(rig.driver.launched) == 1
    assert fresh.cancel(parent)['status'] == 'cancelled'
    assert rig.driver.cancelled == [child.session_id]


def test_replayed_admission_is_idempotent(rig):
    parent = rig.parent()
    child = rig.child(parent)
    rig.adapter.launch(child, opt_in=True)
    native.WorkflowAdapter(rig.ledger, rig.run, rig.driver).launch(child, opt_in=True)
    assert len(rig.driver.launched) == 1


@pytest.mark.parametrize('state', ['failed', 'orphaned', 'cancelled'])
def test_child_failure_holds_even_if_another_child_runs(rig, state):
    parent = rig.parent()
    first, second = rig.child(parent), rig.child(parent, 2)
    rig.adapter.launch(first, opt_in=True)
    rig.adapter.launch(second, opt_in=True)
    rig.driver.finish(second, state=state)
    with pytest.raises(native.NativeHeld, match='failure/orphan'):
        rig.adapter.complete(parent)
    rig.driver.finish(second)  # A late successful callback cannot clear the hold.
    rig.driver.finish(first)
    with pytest.raises(native.NativeHeld, match='failure/orphan'):
        rig.adapter.complete(parent)
    assert not rig.driver.imports


def test_unknown_descendant_holds_and_is_never_blindly_cancelled(rig):
    parent = rig.parent()
    child = rig.child(parent)
    rig.adapter.launch(child, opt_in=True)
    rig.driver.active[parent.session_id].add('unknown_native_child')
    with pytest.raises(native.NativeHeld, match='unaccounted'):
        rig.adapter.complete(parent)
    result = rig.adapter.cancel(parent)
    assert result['status'] == 'held' and 'unaccounted' in result['reason']
    assert rig.driver.cancelled == [child.session_id]


def test_cancel_attempts_every_owned_child_and_reconciles_failures(rig):
    parent, other = rig.parent(), rig.parent(2)
    children = [rig.child(parent, i) for i in (1, 2, 3)]
    foreign = rig.child(other)
    for child in children + [foreign]:
        rig.adapter.launch(child, opt_in=True)
    rig.driver.cancel_error.add(children[0].session_id)
    result = rig.adapter.cancel(parent)
    assert result['status'] == 'held' and 'stop unavailable' in result['reason']
    assert rig.driver.cancelled == [c.session_id for c in children]
    assert foreign.session_id in rig.driver.active_children(other)
    rig.driver.cancel_error.clear()
    assert rig.adapter.cancel(parent)['status'] == 'cancelled'
    assert rig.driver.cancelled[-1] == children[0].session_id
    with pytest.raises(native.NativeHeld):
        rig.adapter.complete(parent)


def test_replacement_owner_waits_for_previous_children(rig):
    parent = rig.parent()
    rig.adapter.launch(rig.child(parent), opt_in=True)
    replacement = rig.parent(2, workstream_id=parent.workstream_id, assignment_attempt=2)
    child = rig.child(replacement)
    with pytest.raises(native.NativeHeld, match='previous workstream owner'):
        rig.adapter.launch(child, opt_in=True)
    assert rig.adapter.cancel(parent)['status'] == 'cancelled'
    assert rig.adapter.launch(child, opt_in=True)['status'] == 'running'


def test_replacement_rechecks_surviving_child_after_cancelled_record(rig):
    parent = rig.parent()
    first = rig.child(parent)
    rig.adapter.launch(first, opt_in=True)
    assert rig.adapter.cancel(parent)['status'] == 'cancelled'
    rig.driver.active[parent.session_id].add(first.session_id)
    replacement = rig.parent(2, workstream_id=parent.workstream_id, assignment_attempt=2)
    with pytest.raises(native.NativeHeld, match='previous owner has surviving'):
        rig.adapter.launch(rig.child(replacement), opt_in=True)
    assert len(rig.driver.launched) == 1


def test_cancellation_retains_child_branch_and_uncommitted_work(rig):
    parent = rig.parent()
    child = rig.child(parent)
    rig.adapter.launch(child, opt_in=True)
    path = Path(child.cwd) / 'owned/unfinished.txt'
    path.write_text('keep this work')
    assert rig.adapter.cancel(parent)['status'] == 'cancelled'
    assert path.read_text() == 'keep this work'
    assert git(child.cwd, 'symbolic-ref', '--short', 'HEAD') == child.branch


def test_parent_scope_drift_blocks_launch_before_side_effect(rig):
    parent = rig.parent()
    child = rig.child(parent)
    (Path(parent.cwd) / 'outside.txt').write_text('scope drift')
    git(parent.cwd, 'add', 'outside.txt')
    git(parent.cwd, 'commit', '-qm', 'out of scope')
    with pytest.raises(native.NativeHeld, match='exceeds assigned scope'):
        rig.adapter.launch(child, opt_in=True)
    assert not rig.driver.launched


def test_parent_identity_cannot_be_reused_by_child(rig):
    parent, other = rig.parent(), rig.parent(2)
    rig.adapter.launch(rig.child(other), opt_in=True)
    child = replace(rig.child(parent), session_id=other.session_id)
    with pytest.raises(native.NativeHeld, match='child reuses a parent identity'):
        rig.adapter.launch(child, opt_in=True)
    assert len(rig.driver.launched) == 1


@pytest.mark.parametrize('field,value', [
    ('scope', ('owned/base.txt',)), ('ancestry', ('different_coordinator',)),
    ('assignment_attempt', 2), ('guards_digest', 'b' * 64), ('provider', 'codex')])
def test_parent_pins_cannot_drift(rig, field, value):
    parent = rig.parent()
    rig.adapter.launch(rig.child(parent), opt_in=True)
    with pytest.raises(native.NativeHeld, match='parent scope/lineage/attempt drift'):
        rig.adapter.complete(replace(parent, **{field: value}))


def test_runtime_binding_drift_does_not_import_or_cancel_wrong_owner(rig):
    parent = rig.parent()
    child = rig.child(parent)
    rig.adapter.launch(child, opt_in=True)
    wrong = replace(child, parent=replace(parent, guards_digest='b' * 64))
    rig.driver.observations[child.session_id] = native.Observation(wrong, 'completed', False, rig.base)
    with pytest.raises(native.NativeHeld, match='binding drift'):
        rig.adapter.complete(parent)
    assert rig.adapter.cancel(parent)['status'] == 'held'
    assert not rig.driver.cancelled and not rig.driver.imports


def test_generation_and_run_budget_are_pinned(rig):
    parent = rig.parent()
    rig.adapter.launch(rig.child(parent), opt_in=True)
    for run in (replace(rig.run, project_generation=8), replace(rig.run, child_cap=3),
                replace(rig.run, hivemind_id='different_run')):
        fresh = native.WorkflowAdapter(rig.ledger, run, rig.driver)
        with pytest.raises(native.NativeHeld, match='ledger pins/digest changed'):
            fresh.complete(parent)


@pytest.mark.parametrize('damage', ['truncated', 'digest', 'duplicate'])
def test_damaged_ledger_never_resets_budget_or_launches(rig, damage):
    parent = rig.parent()
    child = rig.child(parent)
    rig.adapter.launch(child, opt_in=True)
    data = rig.adapter.path.read_text()
    if damage == 'truncated':
        data = data[:20]
    elif damage == 'digest':
        data = data.replace('"child_cap": 4', '"child_cap": 3')
    else:
        data = data.replace('"version": 1', '"version": 1, "version": 1')
    rig.adapter.path.write_text(data)
    with pytest.raises(native.NativeHeld, match='ledger unavailable'):
        rig.adapter.launch(child, opt_in=True)
    assert len(rig.driver.launched) == 1


def test_ledger_write_failure_launches_nothing(rig, monkeypatch):
    parent = rig.parent()
    child = rig.child(parent)
    def fail(*args, **kwargs):
        raise OSError('disk unavailable')
    monkeypatch.setattr(native, 'write_json_atomic', fail)
    with pytest.raises(OSError, match='disk unavailable'):
        rig.adapter.launch(child, opt_in=True)
    assert not rig.driver.launched


@pytest.mark.parametrize('kind', ['dirty-child', 'dirty-parent', 'wrong-commit',
                                 'branch-child', 'branch-parent', 'no-import', 'import-error',
                                 'outside-scope', 'reverted-outside-scope', 'missing-tree'])
def test_unproven_or_unscoped_output_holds_completion(rig, kind):
    parent = rig.parent()
    child = rig.child(parent)
    rig.adapter.launch(child, opt_in=True)
    rig.commit(child)
    if kind in {'outside-scope', 'reverted-outside-scope'}:
        rig.commit(child, 'outside.txt', 'unauthorized\n')
        if kind == 'reverted-outside-scope':
            git(child.cwd, 'revert', '--no-edit', 'HEAD')
    rig.driver.finish(child)
    if kind == 'dirty-child':
        (Path(child.cwd) / 'untracked.txt').write_text('dirty')
    elif kind == 'dirty-parent':
        (Path(parent.cwd) / 'untracked.txt').write_text('dirty')
    elif kind == 'wrong-commit':
        rig.driver.observations[child.session_id] = native.Observation(child, 'completed', False, rig.base)
    elif kind.startswith('branch-'):
        git(child.cwd if kind == 'branch-child' else parent.cwd, 'checkout', '-qb', 'wrong-branch')
    elif kind == 'no-import':
        rig.driver.skip_import = True
    elif kind == 'import-error':
        rig.driver.import_error = True
    elif kind == 'missing-tree':
        git(rig.repo, 'worktree', 'remove', child.cwd)
    with pytest.raises(native.NativeHeld):
        rig.adapter.complete(parent)
    row = json.loads(rig.adapter.path.read_text())['parents'][parent.session_id]
    assert row['status'] == 'held' and row['reason']
    assert git(rig.repo, 'rev-parse', 'master') == rig.base
    if kind == 'import-error':
        rig.driver.import_error = False
        with pytest.raises(native.NativeHeld):
            rig.adapter.complete(parent)
        assert rig.driver.imports == [child.session_id]


def test_importer_cannot_smuggle_an_extra_out_of_scope_commit(rig):
    parent = rig.parent()
    child = rig.child(parent)
    rig.adapter.launch(child, opt_in=True)
    rig.commit(child)
    rig.driver.finish(child)
    def smuggle(_):
        path = Path(parent.cwd) / 'outside.txt'
        path.write_text('unscoped')
        git(parent.cwd, 'add', 'outside.txt')
        git(parent.cwd, 'commit', '-qm', 'smuggled output')
    rig.driver.import_hook = smuggle
    with pytest.raises(native.NativeHeld, match='exceeds assigned scope'):
        rig.adapter.complete(parent)


def test_completed_parent_drift_is_held(rig):
    parent = rig.parent()
    child = rig.child(parent)
    rig.adapter.launch(child, opt_in=True)
    rig.driver.finish(child)
    rig.adapter.complete(parent)
    git(parent.cwd, 'commit', '--allow-empty', '-qm', 'unexpected follow-up')
    with pytest.raises(native.NativeHeld, match='completed parent HEAD drift'):
        rig.adapter.complete(parent)


@pytest.mark.parametrize('scope', [(), ('../outside',), ('/absolute',), ('owned/*',),
                                  ('owned/../outside',), ('.git/config',), ('owned//file',),
                                  ('owned\\file',), ('owned/\nfile',)])
def test_scope_is_explicit_literal_and_relative(rig, scope):
    parent = rig.parent()
    with pytest.raises(native.NativeHeld):
        replace(parent, scope=scope)


def test_child_cannot_widen_scope_or_reuse_parent_lineage(rig):
    parent = rig.parent()
    child = rig.child(parent)
    with pytest.raises(native.NativeHeld, match='scope exceeds'):
        replace(child, scope=('outside.txt',))
    with pytest.raises(native.NativeHeld, match='lineage cycle'):
        replace(child, session_id=parent.session_id)
    with pytest.raises(native.NativeHeld, match='own canonical worktree'):
        replace(child, cwd=parent.cwd)
    with pytest.raises(native.NativeHeld, match='own branch'):
        replace(child, branch=parent.branch)


@pytest.mark.parametrize('cap', [0, 5, True])
def test_no_implicit_native_default_or_expanded_run_cap(cap):
    with pytest.raises(native.NativeHeld, match='shared child cap'):
        native.Run('project', 7, 'run', cap)


def test_parent_without_admitted_children_cannot_claim_native_success(rig):
    parent = rig.parent()
    with pytest.raises(native.NativeHeld, match='native completion unproven'):
        rig.adapter.complete(parent)


def test_filename_framing_does_not_strip_leading_newlines_into_scope(monkeypatch):
    def evidence(cwd, *args, raw=False):
        return 'a' * 40 if args[0] == 'rev-list' else '\nowned/disguised.txt\0'
    monkeypatch.setattr(native, '_git', evidence)
    with pytest.raises(native.NativeHeld, match='exceeds assigned scope'):
        native._changed('fixture', 'a' * 40, 'HEAD', ('owned/',))


def test_unavailable_child_observation_holds_completion_and_cancel(rig):
    parent = rig.parent()
    child = rig.child(parent)
    rig.adapter.launch(child, opt_in=True)
    rig.driver.observe_error.add(child.session_id)
    with pytest.raises(native.NativeHeld, match='child runtime unavailable'):
        rig.adapter.complete(parent)
    assert rig.adapter.cancel(parent)['status'] == 'held'
    assert not rig.driver.imports and not rig.driver.cancelled

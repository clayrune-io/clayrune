"""HR-7's opt-in, injected native-workflow contract; no production driver.

Supported fixtures are evidence for the adapter, never for Claude startup.
The runtime owns attribution, inherited guards, process registration, bounded
nonblocking lifecycle calls and gated output import. Every descendant must
pass admission here BEFORE launch; native default fan-out is forbidden.
Nothing in this module launches a CLI, merges a branch or grants a review.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
import threading
from typing import Any, Iterator, Literal, Protocol

from mc.atomic_json import read_text_with_retry, write_json_atomic
from mc.landing_serialization import _os_lock

CHILD_CAP = 4  # Q7: total launches AND concurrency, shared across a run.
CapabilityState = Literal['supported', 'unavailable', 'headless-unsupported']
ChildState = Literal['running', 'completed', 'failed', 'cancelled', 'orphaned']
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}')
_SHA = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})')
_lock = threading.RLock()


class NativeHeld(RuntimeError):
    """Missing authority or ambiguous execution holds the parent workstream."""


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise NativeHeld(reason)


def _id(value: str) -> None:
    _require(isinstance(value, str) and bool(_ID.fullmatch(value)), 'invalid owner identifier')


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _scope(paths: tuple[str, ...]) -> None:
    _require(isinstance(paths, tuple) and bool(paths), 'explicit file/directory scope required')
    _require(len(paths) == len(set(paths)), 'duplicate scope')
    for path in paths:
        _require(isinstance(path, str) and bool(path) and '\\' not in path
                 and ':' not in path and not any(ord(c) < 32 for c in path), 'invalid scope')
        normalized = path.rstrip('/')
        _require(bool(normalized) and not path.startswith('/')
                 and all(p.lower() not in ('', '.', '..', '.git') for p in normalized.split('/'))
                 and not any(c in path for c in '*?['), 'scope must be literal relative paths')


def _within(path: str, scope: tuple[str, ...]) -> bool:
    return any(path == p or (p.endswith('/') and path.startswith(p)) for p in scope)


@dataclass(frozen=True)
class Run:
    project_id: str
    project_generation: int
    hivemind_id: str
    child_cap: int = CHILD_CAP

    def __post_init__(self) -> None:
        _id(self.project_id)
        _id(self.hivemind_id)
        _require(type(self.project_generation) is int and self.project_generation >= 0,
                 'invalid project generation')
        _require(type(self.child_cap) is int and 1 <= self.child_cap <= CHILD_CAP,
                 'shared child cap must be between one and four')


@dataclass(frozen=True)
class Parent:
    workstream_id: str
    assignment_attempt: int
    session_id: str
    provider: str
    cwd: str
    branch: str
    base_commit: str
    scope: tuple[str, ...]
    ancestry: tuple[str, ...]
    guards_digest: str

    def __post_init__(self) -> None:
        for value in (self.workstream_id, self.session_id):
            _id(value)
        _require(type(self.assignment_attempt) is int and self.assignment_attempt > 0,
                 'invalid assignment attempt')
        _require(isinstance(self.provider, str) and bool(self.provider), 'provider required')
        _require(Path(self.cwd).is_absolute() and str(Path(self.cwd).resolve()) == self.cwd,
                 'cwd must be a canonical absolute server-owned path')
        _require(bool(self.branch) and not self.branch.startswith('-')
                 and not any(ord(c) < 32 for c in self.branch), 'invalid branch')
        _require(bool(_SHA.fullmatch(self.base_commit)), 'full base commit required')
        _scope(self.scope)
        _require(isinstance(self.ancestry, tuple) and bool(self.ancestry), 'attributed ancestry required')
        for value in self.ancestry:
            _id(value)
        _require(len(set(self.ancestry)) == len(self.ancestry)
                 and self.session_id not in self.ancestry, 'cyclic lineage')
        _require(bool(re.fullmatch(r'[0-9a-f]{64}', self.guards_digest)), 'inherited guard digest required')

    @property
    def key(self) -> str:
        return _digest(asdict(self))


@dataclass(frozen=True)
class Child:
    """Server-allocated child worktree and managed session; never request-body authority."""
    run: Run
    parent: Parent
    session_id: str
    cwd: str
    branch: str
    scope: tuple[str, ...]

    def __post_init__(self) -> None:
        _id(self.session_id)
        _require(self.session_id not in self.parent.ancestry + (self.parent.session_id,),
                 'child lineage cycle')
        _scope(self.scope)
        _require(all(_within(p, self.parent.scope) for p in self.scope), 'child scope exceeds parent')
        _require(Path(self.cwd).is_absolute() and str(Path(self.cwd).resolve()) == self.cwd
                 and self.cwd != self.parent.cwd, 'child needs its own canonical worktree')
        _require(bool(self.branch) and not self.branch.startswith('-')
                 and not any(ord(c) < 32 for c in self.branch)
                 and self.branch != self.parent.branch, 'child needs its own branch')

    @property
    def ancestry(self) -> tuple[str, ...]:
        return self.parent.ancestry + (self.parent.session_id,)

    @property
    def key(self) -> str:
        return _digest(asdict(self))


@dataclass(frozen=True)
class Capability:
    state: CapabilityState
    version: str
    evidence: str


@dataclass(frozen=True)
class Observation:
    child: Child  # Runtime-observed binding, not echoed child-authored text.
    state: ChildState
    alive: bool
    commit: str = ''


class Driver(Protocol):
    """A future trusted runtime bridge. Calls never wait for agent completion.

    active_children enumerates ALL live native descendants of this owner,
    including unaccounted ones. import_output must use existing native landing
    gates; its return/child exit is insufficient without fresh Git evidence.
    cancel targets only the exact managed Child, never an image name or PID
    supplied by a child. No production implementation is supplied by HR-7.
    """
    def probe(self, parent: Parent) -> Capability: ...
    def launch(self, child: Child) -> None: ...
    def observe(self, child: Child) -> Observation: ...
    def active_children(self, parent: Parent) -> set[str]: ...
    def import_output(self, child: Child, commit: str) -> None: ...
    def cancel(self, child: Child) -> None: ...


def select(parent: Parent, driver: Driver, *, opt_in: bool = False,
           required: bool = False) -> dict:
    """Ordinary workers retain the outer protocol and make no native claim."""
    _require(type(opt_in) is bool and type(required) is bool, 'opt-in/required must be booleans')
    if not opt_in:
        return {'method': 'ordinary', 'native': False, 'held': required,
                'reason': 'native execution requires explicit opt-in', 'ordinary_available': True}
    if parent.provider != 'claude':
        return {'method': 'unsupported' if required else 'ordinary', 'native': False,
                'held': required, 'reason': 'native workflows require a Claude worker',
                'ordinary_available': True}
    try:
        cap = driver.probe(parent)
        _require(isinstance(cap, Capability) and cap.state in
                 {'supported', 'unavailable', 'headless-unsupported'}
                 and bool(cap.version) and bool(cap.evidence), 'invalid capability evidence')
    except Exception as exc:
        return {'method': 'unsupported', 'native': False, 'held': required,
                'reason': f'capability probe failed: {exc}', 'ordinary_available': True}
    return {'method': 'native' if cap.state == 'supported' else 'unsupported',
            'native': cap.state == 'supported', 'held': required and cap.state != 'supported',
            'capability': asdict(cap), 'ordinary_available': True}


def _git(cwd: str, *args: str, raw: bool = False) -> str:
    result = subprocess.run(['git', *args], cwd=cwd, stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=15)
    _require(result.returncode == 0, 'git evidence unavailable: ' + result.stderr.decode('utf-8', 'replace'))
    text = result.stdout.decode('utf-8')
    return text if raw else text.strip()


def _clean(cwd: str, branch: str, base: str) -> str:
    _require(_git(cwd, 'symbolic-ref', '--short', 'HEAD') == branch, 'worktree branch drift')
    _require(not _git(cwd, 'status', '--porcelain', '--untracked-files=all'), 'uncommitted work holds completion')
    _git(cwd, 'merge-base', '--is-ancestor', base, 'HEAD')
    return _git(cwd, 'rev-parse', 'HEAD')


def _common(cwd: str) -> Path:
    return (Path(cwd) / _git(cwd, 'rev-parse', '--git-common-dir')).resolve()


def _changed(cwd: str, base: str, tip: str, scope: tuple[str, ...]) -> None:
    # Inspect every reachable commit, not just net diff: reverted out-of-scope
    # edits must not disappear from the evidence. Disable rename detection so
    # both ends of a move must fit scope. NUL framing preserves unusual names.
    for commit in _git(cwd, 'rev-list', base + '..' + tip).splitlines():
        raw = _git(cwd, 'diff-tree', '--no-commit-id', '--root', '-r', '-m',
                   '--name-only', '-z', '--no-renames', commit, raw=True)
        files = {p for p in raw.split('\0') if p}
        _require(all(_within(p, scope) for p in files), 'committed output exceeds assigned scope')


def _object(pairs: list[tuple[str, Any]]) -> dict:
    value = {}
    for key, item in pairs:
        _require(key not in value, 'duplicate ledger key')
        value[key] = item
    return value


class WorkflowAdapter:
    """One durable run budget, outside data/projects; no implicit retries.

    A run directory is the existing server-owned hivemind resolver's path.
    A short OS lock serializes admission/reconciliation across adapter objects
    and restarts. Driver calls are bounded/nonblocking; never poll/wait under
    this lock. An uncertain launch/import consumes its slot and remains held.
    """
    def __init__(self, run_dir: Path, run: Run, driver: Driver):
        self.directory = Path(run_dir)
        self.run, self.driver = run, driver
        self.path = self.directory / 'native-workflow.json'

    @contextmanager
    def _transaction(self) -> Iterator[dict]:
        with _lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / '.native-workflow.lock').open('a+b') as stream:
                if stream.tell() == 0:
                    stream.write(b'0')
                    stream.flush()
                try:
                    _os_lock(stream, True)
                except OSError as exc:
                    raise NativeHeld('native admission busy') from exc
                try:
                    if self.path.exists():
                        try:
                            ledger = json.loads(read_text_with_retry(self.path), object_pairs_hook=_object)
                            payload = {k: v for k, v in ledger.items() if k != 'digest'}
                            _require(ledger.get('digest') == _digest(payload)
                                     and ledger.get('version') == 1 and ledger.get('run') == asdict(self.run)
                                     and isinstance(ledger.get('parents'), dict)
                                     and isinstance(ledger.get('children'), dict)
                                     and len(ledger['children']) <= self.run.child_cap, 'ledger pins/digest changed')
                        except Exception as exc:
                            raise NativeHeld(f'native ledger unavailable: {exc}') from exc
                    else:
                        ledger = {'version': 1, 'run': asdict(self.run), 'parents': {}, 'children': {}}
                    yield ledger
                finally:
                    _os_lock(stream, False)

    def _save(self, ledger: dict) -> None:
        ledger.pop('digest', None)
        ledger['digest'] = _digest(ledger)
        write_json_atomic(self.path, ledger, indent=2, allow_nan=False)

    def _parent(self, ledger: dict, parent: Parent) -> dict:
        pins = json.loads(json.dumps(asdict(parent)))
        existing = ledger['parents'].get(parent.session_id)
        if existing:
            _require(existing['pins'] == pins, 'parent scope/lineage/attempt drift')
            return existing
        _require(parent.session_id not in ledger['children'], 'parent reuses a native child identity')
        for other in ledger['parents'].values():
            previous = other['pins']
            if previous['workstream_id'] == parent.workstream_id:
                _require(other['status'] in {'cancelled', 'completed'},
                         'previous workstream owner requires child reconciliation')
                old = Parent(previous['workstream_id'], previous['assignment_attempt'],
                             previous['session_id'], previous['provider'], previous['cwd'],
                             previous['branch'], previous['base_commit'], tuple(previous['scope']),
                             tuple(previous['ancestry']), previous['guards_digest'])
                _require(not self.driver.active_children(old), 'previous owner has surviving children')
                for child, _ in self._children(ledger, old):
                    obs = self._observe(child)
                    _require(not obs.alive and obs.state in {'cancelled', 'completed', 'failed'},
                             'previous owner has unreconciled children')
            _require(previous['cwd'] != parent.cwd and previous['branch'] != parent.branch,
                     'parent worktree/ref already owned')
        _require(not any(r['pins']['cwd'] == parent.cwd or r['pins']['branch'] == parent.branch
                         for r in ledger['children'].values()), 'parent overlaps a child worktree/ref')
        row = {'pins': pins, 'status': 'open', 'reason': ''}
        ledger['parents'][parent.session_id] = row
        return row

    def _children(self, ledger: dict, parent: Parent) -> list[tuple[Child, dict]]:
        result = []
        for sid, row in ledger['children'].items():
            if row['pins']['parent']['session_id'] == parent.session_id:
                pins = row['pins']
                _require(pins['run'] == asdict(self.run)
                         and pins['parent'] == json.loads(json.dumps(asdict(parent))), 'child authority drift')
                child = Child(self.run, parent, sid, pins['cwd'], pins['branch'], tuple(pins['scope']))
                _require(child.key == row['key'], 'child pins changed')
                result.append((child, row))
        return result

    def _observe(self, child: Child) -> Observation:
        obs = self.driver.observe(child)
        _require(isinstance(obs, Observation) and obs.child == child, 'runtime child binding drift')
        _require(type(obs.alive) is bool and obs.state in
                 {'running', 'completed', 'failed', 'cancelled', 'orphaned'}, 'invalid child lifecycle')
        return obs

    def launch(self, child: Child, *, opt_in: bool = False) -> dict:
        _require(child.run == self.run, 'run/generation drift')
        decision = select(child.parent, self.driver, opt_in=opt_in, required=True)
        _require(decision['native'], decision.get('reason', 'native capability unsupported'))
        with self._transaction() as ledger:
            parent = self._parent(ledger, child.parent)
            _require(parent['status'] == 'open', 'parent held/stopped; reconcile before replacement')
            _require(child.session_id not in ledger['parents'], 'child reuses a parent identity')
            existing = ledger['children'].get(child.session_id)
            if existing:
                _require(existing['key'] == child.key, 'child identity replay drift')
                return dict(existing)  # Never re-launch even if launch acknowledgement was lost.
            _require(len(ledger['children']) < self.run.child_cap, 'shared total-run child budget exhausted')
            _require(not any(r['pins']['cwd'] == child.cwd or r['pins']['branch'] == child.branch
                             for r in ledger['children'].values()), 'child worktree/ref already owned')
            _require(not any(r['pins']['cwd'] == child.cwd or r['pins']['branch'] == child.branch
                             for r in ledger['parents'].values()), 'child overlaps a parent worktree/ref')
            _require(not self.driver.active_children(child.parent) -
                     {c.session_id for c, _ in self._children(ledger, child.parent)}, 'unaccounted native descendants')
            _clean(child.parent.cwd, child.parent.branch, child.parent.base_commit)
            _changed(child.parent.cwd, child.parent.base_commit, 'HEAD', child.parent.scope)
            _require(_clean(child.cwd, child.branch, child.parent.base_commit) == child.parent.base_commit,
                     'child must start at pinned base')
            _require(_common(child.cwd) == _common(child.parent.cwd), 'child is outside parent repository')
            row = {'key': child.key, 'pins': asdict(child), 'status': 'launching', 'commit': ''}
            ledger['children'][child.session_id] = row
            self._save(ledger)  # Durable authority/budget before any runtime side effect.
            try:
                self.driver.launch(child)
                obs = self._observe(child)
                _require(obs.state not in {'failed', 'cancelled', 'orphaned'}, 'child launch failed/orphaned')
                row['status'] = 'running'
            except Exception as exc:
                row['status'] = 'uncertain'
                parent.update(status='held', reason=f'launch requires reconciliation: {exc}')
                self._save(ledger)
                raise NativeHeld(parent['reason']) from exc
            self._save(ledger)
            return dict(row)

    def complete(self, parent: Parent) -> dict:
        """All child output must be committed, imported, clean and reachable.

        Held/cancelled parents cannot turn green on a later exit callback.
        Caller still owns ordinary worker completion/handoff and outer review.
        """
        with self._transaction() as ledger:
            owner = self._parent(ledger, parent)
            try:
                _require(owner['status'] in {'open', 'completed'}, owner['reason'] or 'parent stopped')
                children = self._children(ledger, parent)
                _require(bool(children), 'no admitted native children; native completion unproven')
                active = self.driver.active_children(parent)
                _require(not active - {c.session_id for c, _ in children}, 'unaccounted native descendants')
                observations = [self._observe(c) for c, _ in children]
                _require(not any(o.state in {'failed', 'cancelled', 'orphaned'} for o in observations),
                         'child failure/orphan holds completion')
                if any(o.alive or o.child.session_id in active or o.state == 'running' for o in observations):
                    return {'status': 'pending', 'native': True, 'parent': parent.session_id}
                for (child, row), obs in zip(children, observations):
                    _require(obs.state == 'completed' and bool(_SHA.fullmatch(obs.commit)),
                             'child committed output unavailable')
                    _require(row['status'] in {'running', 'collected'}, 'uncertain child output requires reconciliation')
                    _require(_common(child.cwd) == _common(parent.cwd), 'child repository drift')
                    _require(_clean(child.cwd, child.branch, parent.base_commit) == obs.commit,
                             'child commit/ref drift')
                    _changed(child.cwd, parent.base_commit, obs.commit, child.scope)
                    _clean(parent.cwd, parent.branch, parent.base_commit)
                    if row['status'] != 'collected':
                        row.update(status='collecting', commit=obs.commit)
                        self._save(ledger)
                        self.driver.import_output(child, obs.commit)
                    else:
                        _require(row['commit'] == obs.commit, 'collected child output drift')
                    _clean(parent.cwd, parent.branch, parent.base_commit)
                    _git(parent.cwd, 'merge-base', '--is-ancestor', obs.commit, 'HEAD')
                    _changed(parent.cwd, parent.base_commit, 'HEAD', parent.scope)
                    row.update(status='collected', commit=obs.commit)
                    self._save(ledger)
                _require(not self.driver.active_children(parent), 'native descendants still alive')
                head = _clean(parent.cwd, parent.branch, parent.base_commit)
                _require(owner.get('head', head) == head, 'completed parent HEAD drift')
                owner.update(status='completed', head=head)
                self._save(ledger)
                return {'status': 'completed', 'native': True, 'parent': parent.session_id,
                        'branch': parent.branch, 'commit': head,
                        'contributing_sessions': [parent.session_id] + [c.session_id for c, _ in children]}
            except Exception as exc:
                owner.update(status='held', reason=str(exc))
                self._save(ledger)
                raise NativeHeld(str(exc)) from exc

    def cancel(self, parent: Parent) -> dict:
        """Stop all and only durably owned children; no slot refund or cleanup.

        Unknown descendants, unavailable observations and still-live processes
        retain the hold. Repeat calls reconcile cancellation, never re-launch.
        """
        with self._transaction() as ledger:
            owner = self._parent(ledger, parent)
            children = self._children(ledger, parent)
            owner.update(status='cancelling', reason='parent cancellation requested')
            self._save(ledger)
            errors = []
            for child, row in children:
                try:
                    obs = self._observe(child)
                    if obs.alive or obs.state in {'running', 'orphaned'}:
                        self.driver.cancel(child)
                    obs = self._observe(child)
                    _require(not obs.alive and obs.state in {'cancelled', 'completed', 'failed'},
                             f'child {child.session_id} still active/unreconciled')
                    row['status'] = 'stopped'
                except Exception as exc:
                    errors.append(f'{child.session_id}: {exc}')
            try:
                _require(not self.driver.active_children(parent), 'live/unaccounted native descendants')
            except Exception as exc:
                errors.append(str(exc))
            owner.update(status='held' if errors else 'cancelled', reason='; '.join(errors) or 'owned children stopped')
            self._save(ledger)
            return {'status': owner['status'], 'reason': owner['reason'], 'native': True}

"""Checkpoint, boot adoption and restart-blocker partition for shim-run agents
(MC-1150 P4). Design: docs/AGENT_SURVIVE_RESTART_DESIGN.md sections 6, 8, 9.

With `agent_survive_restart_enabled` false (the default) nothing in this module
runs: every entry point returns before touching disk or a session.

What lives here
  projection     `project_session` / `restore_session`: the JSON-safe slice of a
                 session dict, and the live dict rebuilt from it.
  checkpoint     `write_checkpoint` / `force_checkpoint` / `Checkpointer`: the
                 server's `checkpoint.json` (consumed log offset + projection),
                 written while a shim-run session is live. The offset is
                 `LogTail.committed`: every line before it has been fully
                 processed by the reader, so a restart re-reads the line that
                 was in flight (at-least-once), never skips it. The one
                 exception is `force_checkpoint` ahead of a completion
                 side effect, which uses the offset PAST the in-flight line so
                 a completion cannot fire twice.
  assessment     `assess` decides, from the files alone, whether a session
                 directory can be adopted (spec section 6.2 step 1). Fail
                 closed: anything unreadable, skewed, or not positively alive is
                 "not adoptable" and is left to the reaper / revive-on-message.
  adoption       `adopt_on_boot` in two phases around the reaper: `claim`
                 (assess + owner HELLO + protect pids) before it, `start`
                 (rebuild session, register, start the reader) after it. The
                 reaper must read the PRIOR instance's PID ledger before
                 adoption registers anything, or it would see only this boot's.
  blockers       `partition_blockers` / `should_spare` for
                 system_routes' restart path (spec section 9).

Reader state that is NOT in a session dict (per-turn locals of the stream
readers) is not rebuilt, so a session is adopted ONLY if its reader path is
listed in `RESTORABLE_READER_PATHS` (Dave's decision on Fenn's P4 review,
2026-11-05). Every other path stays a restart blocker, exactly as before P4.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from mc import agent_shim as _shim
from mc import agent_shim_client as _client
from mc.atomic_json import write_json_atomic

SCHEMA = 1
CHECKPOINT_NAME = 'checkpoint.json'

# Engines the readers below know how to resume. Anything else is not adoptable.
SUPPORTED_ENGINES = frozenset({'claude', 'gemini', 'qwen', 'codex', 'opencode',
                               'goose', 'aider', 'kiro'})

# Reader paths whose per-turn state is FULLY in the checkpoint, so a fresh reader
# started at the checkpoint offset reaches the same verdict as the one that died.
# Key = `reader_path(engine, session mode)`, e.g. 'qwen:A', 'claude:B'. EMPTY on
# purpose: none of the readers qualifies yet. `_mode_a_reader` keeps
# `turn_text_parts`, the mc: control-block suppression flag, tool counts and the
# empty-turn state in locals; Gemini's `_read_stream` keeps `pending_run`; the
# Claude readers keep their own. A path may be added ONLY together with (a) every
# one of its locals being published on the session dict, so `project_session`
# carries it, and (b) a restart test with protocol output and one at end of
# output (tests/test_agent_shim_adopt_blockers.py shows the shape). Adopting a
# path without both turned a successful turn into "ended with no reply" and lost
# half-read mc:question blocks.
RESTORABLE_READER_PATHS: frozenset = frozenset()

LOG_TAIL_MAX = 2000                 # spec 6.3: the last ~2000 log_lines
BOUNDARY_WAIT_S = 2.0               # how long a checkpoint waits for the reader to finish its line
CHECKPOINT_MAX_AGE_S = 10.0         # config shim_checkpoint_max_age_s (spec 9)
HEARTBEAT_S = 3.0                   # rewrite an unchanged checkpoint this often
TICK_S = 1.0
_EXIT_RACE_WAIT_S = 2.0             # shim alive, CLI gone, exit.json not yet written

# Keys that never go into a projection: live objects, or restored explicitly.
_DENY = frozenset({'proc', 'stdin_lock', 'log_lines', 'thread', 'reader_thread',
                   '_reader_thread', '_adopt_skip_drain', '_adopt_proc_cost',
                   '_adopt_proc_turns'})
_LOCK_KEYS = ('stdin_lock',)        # recreated on restore (Mode B writes under it)


def _log(msg: str) -> None:
    try:
        from mc.core import _log as core_log
        core_log(msg)
    except Exception:
        print(msg, flush=True)


def _config() -> Mapping[str, Any]:
    from mc import state
    return state.CONFIG


def enabled_now() -> bool:
    try:
        return _client.enabled(_config())
    except Exception:
        return False


def max_age_s(cfg: Optional[Mapping[str, Any]] = None) -> float:
    try:
        v = float((cfg if cfg is not None else _config()).get('shim_checkpoint_max_age_s',
                                                              CHECKPOINT_MAX_AGE_S))
        return v if v > 0 else CHECKPOINT_MAX_AGE_S
    except (TypeError, ValueError, AttributeError):
        return CHECKPOINT_MAX_AGE_S


def reader_path(engine: Any, session_mode: Any) -> str:
    """The key the restorable-reader registry is indexed by."""
    return f"{engine or ''}:{session_mode or 'A'}"


def reader_path_restorable(engine: Any, session_mode: Any) -> bool:
    return reader_path(engine, session_mode) in RESTORABLE_READER_PATHS


# Set on the session dict by `mc/agent_shim_completion.py` while a completion's
# side effects are in flight; it rides in every projection until acknowledged.
PENDING_KEY = '_pending_completion'


# ──────────────────────────────── projection ─────────────────────────────────
_SET_TAG = '__mc_set__'


def _encode_extra(o: Any) -> Any:
    """json `default`: a set (the one non-JSON container real sessions carry,
    midturn_rollover's `_mt_pending_tools`) becomes a tagged list."""
    if isinstance(o, (set, frozenset)):
        return {_SET_TAG: sorted(o, key=repr)}
    raise TypeError(f'not JSON serializable: {type(o).__name__}')


def _untag(o: Any) -> Any:
    if isinstance(o, dict):
        if len(o) == 1 and _SET_TAG in o and isinstance(o[_SET_TAG], list):
            return set(_untag(x) for x in o[_SET_TAG])
        return {k: _untag(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_untag(x) for x in o]
    return o


def _json_copy(value: Any) -> Tuple[bool, Any]:
    """Deep copy through JSON. (False, None) if the value is not JSON-safe, or if
    it was mutated by the reader while being serialized and still is on retry."""
    for _ in range(3):
        try:
            return True, json.loads(json.dumps(value, allow_nan=False, default=_encode_extra))
        except RuntimeError:                     # "changed size during iteration"
            continue
        except (TypeError, ValueError, OverflowError):
            return False, None
    return False, None


def project_session(session: Mapping[str, Any], *, log_tail: int = LOG_TAIL_MAX) -> Dict[str, Any]:
    """The JSON-safe projection of a session dict, `log_lines` as
    {'lines': [...last N], 'ts': [...], 'dropped': n}. Returns a plain dict with
    `_skipped` naming every key that could not be serialized, so a lossy
    projection is visible in the checkpoint rather than silent."""
    out: Dict[str, Any] = {}
    skipped: List[str] = []
    for key, value in list(session.items()):
        if key in _DENY:
            continue
        if callable(value):
            skipped.append(str(key))
            continue
        ok, copy = _json_copy(value)
        if ok:
            out[key] = copy
        else:
            skipped.append(str(key))
    lines = list(session.get('log_lines') or [])
    ts = list(getattr(session.get('log_lines'), 'ts', []) or [])
    kept = lines[-log_tail:] if log_tail else []
    kept_ts = ts[-len(kept):] if (ts and len(ts) == len(lines) and kept) else [None] * len(kept)
    out['log_lines'] = {'lines': [str(x) for x in kept], 'ts': kept_ts,
                        'dropped': max(0, len(lines) - len(kept))}
    out['_skipped'] = sorted(skipped)
    return out


def restore_session(projection: Mapping[str, Any]) -> Dict[str, Any]:
    """A live session dict from `project_session` output (`proc` is attached by
    the caller). Locks are recreated; `log_lines` is a TimestampedLines."""
    from mc.core import TimestampedLines
    session = {k: _untag(v) for k, v in projection.items() if k not in ('log_lines', '_skipped')}
    ll = projection.get('log_lines') or {}
    lines = list(ll.get('lines') or [])
    ts = list(ll.get('ts') or [])
    session['log_lines'] = (TimestampedLines(lines, ts=ts) if len(ts) == len(lines)
                            else TimestampedLines(lines))
    for key in _LOCK_KEYS:
        if session.get('mode') == 'B' or key in projection or key in projection.get('_skipped', ()):
            session[key] = threading.Lock()
    return session


# ─────────────────────────── reader hooks (agent_routes) ─────────────────────
def reader_counters(session: Dict[str, Any], proc: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """`(proc_cost, proc_turns)` for a Claude stream reader. These are per-process
    locals of the reader; the cost accounting is delta-based, so a reader that
    starts from {} on an adopted process would count the whole carried total a
    second time. For a shim-run session they are also published on the session
    (`_survive_proc_*`) so the checkpoint carries them; an adopted session hands
    back what the checkpoint held. A plain Popen session gets fresh dicts and no
    new keys, exactly as before."""
    cost = session.pop('_adopt_proc_cost', None) or {}
    turns = session.pop('_adopt_proc_turns', None) or {}
    if isinstance(proc, _client.ShimProc):
        session['_survive_proc_cost'] = cost
        session['_survive_proc_turns'] = turns
    return cost, turns


def take_skip_drain(session: Dict[str, Any]) -> bool:
    """True once for an adopted session: the process that started its background
    tasks is the one still running, so the reader must not report them gone."""
    return bool(session.pop('_adopt_skip_drain', False))


# ──────────────────────────────── checkpoint ─────────────────────────────────
def checkpoint_path(sdir: Any) -> Path:
    return Path(sdir) / CHECKPOINT_NAME


def read_checkpoint(sdir: Any) -> Optional[Dict[str, Any]]:
    return _client.read_json(checkpoint_path(sdir))


def _shim_proc(session: Mapping[str, Any]) -> Any:
    """The session's ShimProc, or None (a plain Popen session is not adoptable)."""
    proc = session.get('proc')
    return proc if isinstance(proc, _client.ShimProc) else None


def build_checkpoint(session: Mapping[str, Any], *, inflight: bool = False,
                     now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    proc = _shim_proc(session)
    if proc is None:
        return None
    tail = proc.stdout
    offset = tail.offset if inflight else getattr(tail, 'committed', tail.offset)
    return {
        'schema': SCHEMA,
        'session_id': session.get('session_id'),
        'launch_id': proc.manifest.get('launch_id'),
        'written_epoch': time.time() if now is None else now,
        'consumed_offset': int(offset),
        'inflight_included': bool(inflight),
        'session': project_session(session),
    }


_REGISTRY_LOCK = threading.Lock()
_LAST_WRITE: Dict[str, Tuple[Any, float]] = {}   # session_id -> (launch_id, epoch)
# Per (session, launch): the lock that serializes snapshot + replace, and the
# highest consumed offset already on disk. Atomic rename stops a torn file; it
# does not stop an OLDER snapshot landing after a newer one.
_WRITE_LOCKS: Dict[Tuple[str, Any], threading.Lock] = {}
_LAST_OFFSET: Dict[Tuple[str, Any], int] = {}


def _write_lock(sid: str, launch_id: Any) -> threading.Lock:
    with _REGISTRY_LOCK:
        return _WRITE_LOCKS.setdefault((sid, launch_id), threading.Lock())


def write_checkpoint(session: Mapping[str, Any], *, inflight: bool = False,
                     now: Optional[float] = None) -> bool:
    """Atomically write the session's checkpoint. False when the session is not
    shim-run, the write failed (logged), or a newer snapshot is already on disk;
    never raises.

    Ordering (MC-1150 P4 review): the reader boundary is taken first, so the
    projection and the offset describe the same instant; then a per
    session/launch lock covers snapshot AND replace, so the daemon checkpointer,
    the reader's forced checkpoint and the restart flush cannot interleave; and
    a snapshot whose offset is behind what is already on disk is dropped, so a
    later committed snapshot is never replaced by an earlier one."""
    try:
        proc = _shim_proc(session)
        if proc is None:
            return False
        sid = str(session.get('session_id'))
        launch_id = proc.manifest.get('launch_id')
        boundary = getattr(proc.stdout, 'boundary', None)
        if boundary is not None and not boundary.acquire(timeout=BOUNDARY_WAIT_S):
            _log(f"[agent-shim-adopt] checkpoint for {sid[:12]} skipped: reader did not "
                 f"reach a line boundary in {BOUNDARY_WAIT_S}s")
            return False
        try:
            with _write_lock(sid, launch_id):
                ck = build_checkpoint(session, inflight=inflight, now=now)
                if ck is None:
                    return False
                prev = _LAST_OFFSET.get((sid, launch_id))
                if prev is not None and ck['consumed_offset'] < prev:
                    _log(f"[agent-shim-adopt] stale checkpoint for {sid[:12]} dropped "
                         f"(offset {ck['consumed_offset']} < {prev} already on disk)")
                    return False
                write_json_atomic(checkpoint_path(proc.dir), ck)
                with _REGISTRY_LOCK:
                    _LAST_WRITE[sid] = (ck['launch_id'], ck['written_epoch'])
                    _LAST_OFFSET[(sid, launch_id)] = ck['consumed_offset']
                return True
        finally:
            if boundary is not None:
                boundary.release()
    except Exception as e:
        _log(f"[agent-shim-adopt] checkpoint write failed for "
             f"{str(session.get('session_id'))[:12]}: {e}")
        return False


def force_checkpoint(session: Mapping[str, Any]) -> bool:
    """Synchronous checkpoint taken BEFORE a completion side effect: the offset
    includes the line being processed (the turn's `result`), so a restart cannot
    replay it into a second completion. No-op unless the flag is on and the
    session runs under a shim."""
    if _shim_proc(session) is None or not enabled_now():
        return False
    return write_checkpoint(session, inflight=True)


def last_checkpoint(session_id: str) -> Optional[Tuple[Any, float]]:
    with _REGISTRY_LOCK:
        return _LAST_WRITE.get(str(session_id))


def forget(session_id: str) -> None:
    with _REGISTRY_LOCK:
        # Only the freshness stamp. `_LAST_OFFSET`/`_WRITE_LOCKS` stay for the life
        # of the process: dropping a lock another writer holds would let a second
        # writer in, and the completion checkpoints land after the daemon forgets.
        _LAST_WRITE.pop(str(session_id), None)


class Checkpointer:
    """Daemon thread: each tick, write a checkpoint for every live shim-run
    session whose state changed, and re-write an unchanged one every
    HEARTBEAT_S so its age (the blocker's wedge test) stays honest."""

    def __init__(self, sessions: Callable[[], Mapping[str, Any]], *,
                 tick_s: float = TICK_S, heartbeat_s: float = HEARTBEAT_S,
                 clock: Callable[[], float] = time.time):
        self._sessions = sessions
        self._tick = tick_s
        self._heartbeat = heartbeat_s
        self._clock = clock
        self._sig: Dict[str, Tuple[Any, float]] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @staticmethod
    def _signature(session: Mapping[str, Any]) -> Tuple[Any, ...]:
        proc = session.get('proc')
        tail = getattr(proc, 'stdout', None)
        return (getattr(tail, 'committed', None), len(session.get('log_lines') or ()),
                session.get('status'), session.get('claude_session_id'),
                session.get('provider_session_id'), session.get('cost_usd'))

    def run_once(self) -> int:
        """One pass; returns the number of checkpoints written."""
        wrote = 0
        now = self._clock()
        for sid, session in list(self._sessions().items()):
            if _shim_proc(session) is None or session.get('status') in ('completed', 'stopped'):
                self._sig.pop(sid, None)
                forget(sid)
                continue
            sig = self._signature(session)
            prev = self._sig.get(sid)
            if prev is not None and prev[0] == sig and now - prev[1] < self._heartbeat:
                continue
            if write_checkpoint(session, now=now):
                self._sig[sid] = (sig, now)
                wrote += 1
        return wrote

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as e:
                _log(f"[agent-shim-adopt] checkpointer pass failed: {e}")
            self._stop.wait(self._tick)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name='agent-shim-checkpointer',
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


_CHECKPOINTER: Optional[Checkpointer] = None


def ensure_checkpointer(cfg: Optional[Mapping[str, Any]] = None) -> None:
    """Start the process-wide checkpointer (idempotent; a no-op with the flag off)."""
    global _CHECKPOINTER
    if not _client.enabled(cfg if cfg is not None else _config()):
        return
    if _CHECKPOINTER is None:
        from mc import state
        _CHECKPOINTER = Checkpointer(lambda: state.agent_sessions)
    _CHECKPOINTER.start()


# ──────────────────────────────── assessment ─────────────────────────────────
@dataclass
class Assessment:
    session_id: str
    ok: bool
    reason: str = ''
    sdir: Optional[Path] = None
    spec: Dict[str, Any] = field(default_factory=dict)
    status: Optional[_client.ManifestStatus] = None
    checkpoint: Optional[Dict[str, Any]] = None

    @property
    def protected_identities(self) -> List[Tuple[int, Any]]:
        """(pid, create_time) of the processes assessment found POSITIVELY alive
        and matching the manifest. A CLI that finished during downtime, or a pid
        that now belongs to a stranger, is not here: nothing about it is ours."""
        if not self.ok or self.status is None:
            return []
        m = self.status.manifest
        out: List[Tuple[int, Any]] = []
        if self.status.cli_alive and m.get('cli_pid'):
            out.append((int(m['cli_pid']), m.get('cli_ct')))
        if self.status.shim_alive and m.get('shim_pid'):
            out.append((int(m['shim_pid']), m.get('shim_ct')))
        return out

    @property
    def protected_pids(self) -> List[int]:
        return [pid for pid, _ct in self.protected_identities]


def assess(sdir: Any, *, sleep: Callable[[float], None] = time.sleep) -> Assessment:
    """Can the session directory `sdir` be adopted? Files and process identity
    only; nothing is connected to or modified. Never raises."""
    d = Path(sdir)
    sid = d.name

    def no(why: str, **kw: Any) -> Assessment:
        return Assessment(sid, False, why, sdir=d, **kw)

    try:
        spec = _client.read_json(d / 'spec.json')
        if not spec:
            return no('no readable spec.json')
        if spec.get('schema') != _shim.SCHEMA:
            return no(f"spec schema {spec.get('schema')!r} != {_shim.SCHEMA}")
        if spec.get('session_id') != sid:
            return no('spec session_id does not match its directory')
        engine = str(spec.get('engine') or '')
        if engine not in SUPPORTED_ENGINES:
            return no(f'engine {engine!r} not supported')
        mode = spec.get('mode')
        if mode not in (_shim.MODE_ONESHOT, _shim.MODE_INTERACTIVE):
            return no(f'unknown shim mode {mode!r}')
        st = _client.inspect(d)
        if st is None:
            return no('no shim.json (or schema skew)', spec=spec)
        m = st.manifest
        if m.get('launch_id') != spec.get('launch_id'):
            return no('shim.json belongs to a different launch than spec.json', spec=spec)
        ck = read_checkpoint(d)
        if not ck:
            return no('no readable checkpoint.json', spec=spec, status=st)
        if ck.get('schema') != SCHEMA:
            return no(f"checkpoint schema {ck.get('schema')!r} != {SCHEMA}", spec=spec, status=st)
        if ck.get('launch_id') != m.get('launch_id'):
            return no('checkpoint is from an earlier turn of this session', spec=spec, status=st)
        if ck.get('session_id') != sid or not isinstance(ck.get('session'), dict):
            return no('checkpoint does not describe this session', spec=spec, status=st)
        if not isinstance(ck.get('consumed_offset'), int) or ck['consumed_offset'] < 0:
            return no('checkpoint offset is not a non-negative integer', spec=spec, status=st)
        try:
            if ck['consumed_offset'] > (d / 'out.log').stat().st_size:
                return no('checkpoint offset is past the end of out.log', spec=spec, status=st)
        except OSError:
            return no('out.log missing', spec=spec, status=st)
        if ck['session'].get(PENDING_KEY):
            return no('a completion is pending replay for this turn (agent_shim_completion)',
                      spec=spec, status=st, checkpoint=ck)
        path = reader_path(engine, ck['session'].get('mode'))
        if path not in RESTORABLE_READER_PATHS:
            return no(f'reader path {path} does not checkpoint its per-turn state; '
                      f'left to the reaper and revive-on-message', spec=spec, status=st,
                      checkpoint=ck)
        cli = _shim.process_state(m.get('cli_pid'), m.get('cli_ct'))
        shim = _shim.process_state(m.get('shim_pid'), m.get('shim_ct'))
        if 'unknown' in (cli, shim):
            return no(f'cannot verify liveness (cli={cli}, shim={shim})', spec=spec, status=st)
        exit_info = st.exit_info
        if (exit_info is not None and exit_info.get('launch_id') not in (None, m.get('launch_id'))):
            exit_info = None
        if exit_info is None and cli == 'gone' and shim == 'ours':
            # The shim is about to write exit.json: give it a moment.
            deadline = time.monotonic() + _EXIT_RACE_WAIT_S
            while time.monotonic() < deadline and exit_info is None:
                sleep(0.1)
                exit_info = _client.read_json(d / 'exit.json')
        st.exit_info = exit_info
        if exit_info is None and cli == 'gone':
            return no('CLI is gone and left no exit record', spec=spec, status=st, checkpoint=ck)
        if exit_info is None and cli != 'ours':
            return no('CLI not positively alive', spec=spec, status=st, checkpoint=ck)
        st.shim_alive = shim == 'ours'
        st.cli_alive = cli == 'ours'
        if not _in_flight(ck['session']):
            return no('session was not in flight at the last checkpoint', spec=spec,
                      status=st, checkpoint=ck)
        return Assessment(sid, True, 'adoptable', sdir=d, spec=spec, status=st, checkpoint=ck)
    except Exception as e:
        _log(f'[agent-shim-adopt] assess {sid[:12]} failed: {type(e).__name__}: {e}')
        return no(f'assessment raised {type(e).__name__}: {e}')


def _in_flight(snapshot: Mapping[str, Any]) -> bool:
    """Running, or idle with an open bg task / job (spec 9: those pin a restart
    the same way a running turn does)."""
    if snapshot.get('status') == 'running':
        return True
    return _holds_work(snapshot)


def _holds_work(session: Mapping[str, Any]) -> bool:
    try:
        from mc import background_tasks as _bg
        if _bg.blocks_eviction(dict(session), time.time(), _bg.DEFAULT_WAIT_MAX_MINUTES):
            return True
    except Exception as e:
        _log(f'[agent-shim-adopt] background-task check failed: {e}')
    try:
        from mc import agent_jobs as _jobs
        sid = session.get('session_id')
        return bool(sid) and _jobs.open_job_count(sid) > 0
    except Exception:
        return False


# ───────────────────────────────── adoption ──────────────────────────────────
@dataclass
class Claim:
    assessment: Assessment
    proc: Any                               # ShimProc, stdout at consumed_offset

    @property
    def session_id(self) -> str:
        return self.assessment.session_id


@dataclass
class AdoptReport:
    claimed: List[str] = field(default_factory=list)
    started: List[str] = field(default_factory=list)
    skipped: Dict[str, str] = field(default_factory=dict)
    note: str = ''


_CLAIMS: List[Claim] = []
_PROTECTED: Dict[int, Any] = {}              # pid -> create_time recorded in the manifest
_ADOPTED_IDS: Set[str] = set()


def protected_pids() -> frozenset:
    """Pids the startup reaper must leave alone: the CLI/shim of every claim that
    was positively alive and is STILL the recorded process (re-verified here, at
    the moment the reaper asks, not at assessment)."""
    return frozenset(pid for pid, ct in list(_PROTECTED.items())
                     if _shim.process_state(pid, ct) == 'ours')


def adopted_session_ids() -> frozenset:
    return frozenset(_ADOPTED_IDS)


def reset_for_tests() -> None:
    _CLAIMS.clear()
    _PROTECTED.clear()
    _ADOPTED_IDS.clear()
    with _REGISTRY_LOCK:
        _LAST_WRITE.clear()
        _LAST_OFFSET.clear()
        _WRITE_LOCKS.clear()


def claim_all(root: Optional[Any] = None, *, cfg: Optional[Mapping[str, Any]] = None,
              owner_pid: Optional[int] = None) -> AdoptReport:
    """Phase 1 (before the reaper): assess every session directory, take the
    owner fence on the adoptable ones, and protect their pids."""
    rep = AdoptReport()
    config = cfg if cfg is not None else _config()
    if not _client.enabled(config):
        rep.note = 'flag off'
        return rep
    from mc import agent_spawn
    base = Path(root) if root is not None else agent_spawn.default_root()
    if not base.is_dir():
        rep.note = 'no agent_shims directory'
        return rep
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        a = assess(d)
        if not a.ok:
            if (d / 'shim.json').exists() or (d / 'spec.json').exists():
                rep.skipped[a.session_id] = a.reason
                _log(f'[agent-shim-adopt] {a.session_id[:12]} not adopted: {a.reason}')
            continue
        proc = _attach(a, owner_pid)
        if isinstance(proc, str):
            rep.skipped[a.session_id] = proc
            _log(f'[agent-shim-adopt] {a.session_id[:12]} not adopted: {proc}')
            continue
        _CLAIMS.append(Claim(a, proc))
        _PROTECTED.update({pid: ct for pid, ct in a.protected_identities})
        rep.claimed.append(a.session_id)
    return rep


def _attach(a: Assessment, owner_pid: Optional[int]) -> Any:
    """A ShimProc positioned at the checkpoint's offset, with the owner fence
    taken for an interactive shim. A string is the reason it could not be."""
    assert a.status is not None and a.checkpoint is not None and a.sdir is not None
    client: Optional[_client.ShimClient] = None
    if a.spec.get('mode') == _shim.MODE_INTERACTIVE and a.status.shim_alive:
        client = _client.ShimClient(a.sdir, owner_pid=owner_pid)
        try:
            client.connect()
        except _client.ShimRefused as e:
            client.close()
            return f'a live server already owns this shim ({e})'
        except _client.ShimError as e:
            client.close()
            return f'owner HELLO failed: {e}'
    try:
        proc = _client.ShimProc(a.sdir, client=client, start_offset=a.checkpoint['consumed_offset'],
                                manifest=a.status.manifest)
    except Exception as e:
        if client is not None:
            client.close()
        return f'ShimProc attach failed: {type(e).__name__}: {e}'
    proc.args = list(a.spec.get('argv') or [])
    return proc


def start_all(deps: Optional['Deps'] = None) -> AdoptReport:
    """Phase 2 (after the reaper): rebuild each claimed session, register its
    process, start its reader, and start the checkpointer."""
    rep = AdoptReport()
    d = deps or default_deps()
    for claim in list(_CLAIMS):
        sid = claim.session_id
        try:
            start_one(claim, d)
            rep.started.append(sid)
        except Exception as e:
            rep.skipped[sid] = f'{type(e).__name__}: {e}'
            _log(f'[agent-shim-adopt] {sid[:12]} start failed: {type(e).__name__}: {e}')
    _CLAIMS.clear()
    if rep.started:
        ensure_checkpointer()
    return rep


def adopt_on_boot(root: Optional[Any] = None, *, cfg: Optional[Mapping[str, Any]] = None,
                  deps: Optional['Deps'] = None) -> AdoptReport:
    """Both phases back to back, for callers that do not need the reaper between
    them (tests; a reaper-less boot)."""
    rep = claim_all(root, cfg=cfg)
    if not rep.claimed:
        return rep
    started = start_all(deps)
    rep.started = started.started
    rep.skipped.update(started.skipped)
    return rep


@dataclass
class Deps:
    """What adoption needs from the agent runtime, injected so tests can supply
    stand-ins and so this module does not import agent_routes at import time."""
    agent_sessions: Dict[str, Any]
    get_manager: Callable[[str], Any]
    register_process: Callable[..., Any]
    start_reader: Callable[[str, Any, Dict[str, Any]], None]
    session_provider: Callable[[Dict[str, Any], Mapping[str, Any]], str]
    # (image, create_time) of the live process at a pid, or (None, None).
    proc_identity: Callable[[int], Tuple[Any, Any]] = lambda pid: (None, None)


def default_deps() -> Deps:
    from mc import state
    from mc.blueprints import agent_routes as ar
    return Deps(agent_sessions=state.agent_sessions, get_manager=ar.get_manager,
                register_process=ar._register_process, start_reader=_start_reader,
                session_provider=lambda s, spec: str(spec.get('engine') or 'claude'),
                proc_identity=ar._proc_identity)


def _start_reader(engine: str, proc: Any, session: Dict[str, Any]) -> None:
    """Start the reader the session would have had, on the adopted proc."""
    from mc import agent_runtime as rt
    from mc.blueprints import agent_routes as ar
    mode = session.get('mode')
    if engine == 'claude':
        target = ar._read_agent_stream_b if mode == 'B' else ar._read_agent_stream
        threading.Thread(target=target, args=(proc, session), daemon=True,
                         name=f'adopted-reader-{str(session.get("session_id"))[:8]}').start()
        return
    runtime = rt.get_runtime(engine)
    handle = rt.SessionHandle(
        mc_session_id=str(session.get('session_id')), provider=engine, mode='A',
        project_path=str(session.get('_launch_project_path') or session.get('_agent_cwd') or ''),
        project_id=str(session.get('project_id') or ''), session_dict=session,
        provider_session_id=session.get('provider_session_id'),
        started_at=str(session.get('started_at') or ''),
        capabilities=runtime.capabilities(), meta={'callbacks': ar._RUNTIME_CALLBACKS})
    target = getattr(runtime, '_read_stream', None)
    args: Tuple[Any, ...] = (proc, handle)
    if target is None:
        target, args = rt._mode_a_reader, (proc, handle, runtime)
    threading.Thread(target=target, args=args, daemon=True,
                     name=f'adopted-reader-{engine}-{str(session.get("session_id"))[:8]}').start()


def _register_adopted_cli(claim: Claim, deps: Deps, engine: str, sid: str,
                          session: Mapping[str, Any]) -> bool:
    """Put the adopted CLI into the live-process ledger, or do not.

    The ledger is a KILL list (the startup reaper acts on it), so it holds only a
    process that is positively the one the manifest recorded, checked HERE at the
    registration boundary and not at assessment. A CLI that finished during
    downtime is replayed from out.log and never registered; neither is a pid that
    now belongs to a stranger. The entry keeps the manifest's create-time, never
    one sampled from the current holder of the pid (MC-1150 P4 review, finding 1)."""
    m = claim.proc.manifest or {}
    pid, ct = m.get('cli_pid'), m.get('cli_ct')
    if not pid or ct is None:
        return False
    try:
        img, live_ct = deps.proc_identity(int(pid))
    except Exception as e:
        _log(f'[agent-shim-adopt] {sid[:12]} identity sample failed: {e}')
        return False
    if not img or live_ct is None or abs(float(live_ct) - float(ct)) > _shim.IDENTITY_TOLERANCE_S:
        return False
    deps.register_process(claim.proc, f'Agent ({engine} adopted)', 'agent', sid,
                          str(session.get('project_id') or ''), str(session.get('task') or '')[:80],
                          identity=(img, float(ct)))
    return True


def start_one(claim: Claim, deps: Deps) -> Dict[str, Any]:
    a = claim.assessment
    assert a.checkpoint is not None
    snapshot = a.checkpoint['session']
    session = restore_session(snapshot)
    sid = claim.session_id
    session['session_id'] = sid
    session['proc'] = claim.proc
    session['process_alive'] = True
    session['last_output_time'] = time.time()
    session['last_status_change_time'] = time.time()
    session['_adopted'] = True
    session['_adopt_skip_drain'] = True          # the CLI that started the bg tasks is the one still running
    if snapshot.get('_survive_proc_cost'):
        session['_adopt_proc_cost'] = dict(snapshot['_survive_proc_cost'])
    if snapshot.get('_survive_proc_turns'):
        session['_adopt_proc_turns'] = dict(snapshot['_survive_proc_turns'])
    # Idle-with-bg-work sessions are adopted as running: the reader sets them back.
    session['status'] = 'running'
    engine = str(a.spec.get('engine') or '')
    deps.agent_sessions[sid] = session
    mgr = deps.get_manager(str(session.get('project_id') or ''))
    if mgr is not None:
        mgr.session_ids.add(sid)
    _register_adopted_cli(claim, deps, engine, sid, session)
    _ADOPTED_IDS.add(sid)
    deps.start_reader(engine, claim.proc, session)
    _log(f'[agent-shim-adopt] adopted {sid[:12]} engine={engine} mode={a.spec.get("mode")} '
         f'offset={a.checkpoint["consumed_offset"]} '
         f'exited={a.status.exited if a.status else "?"}')
    return session


# ───────────────────────────── restart blockers ──────────────────────────────
def _fresh(session_id: str, launch_id: Any, now: float, limit: float) -> bool:
    got = last_checkpoint(session_id)
    return got is not None and got[0] == launch_id and (now - got[1]) <= limit


def _owns_open_job(sid: str) -> bool:
    """True if `sid` owns a running Clayrune job (mc/agent_jobs.py). Fails closed:
    if the job table cannot be read, the session is treated as owning one."""
    try:
        from mc import agent_jobs as _jobs
        return _jobs.open_job_count(sid) > 0
    except Exception as e:
        _log(f'[agent-shim-adopt] open-job check failed for {sid[:12]}: {e}')
        return True


def is_adoptable_session(session: Mapping[str, Any], *, cfg: Optional[Mapping[str, Any]] = None,
                         now: Optional[float] = None) -> bool:
    """Would this live, in-flight session survive a restart? (spec section 9)

    Never, while it owns an open Clayrune job (MC-1161): the job's process and
    its completion callback live only in this server process (`agent_jobs._jobs`)
    and are not carried through adoption, so such a session stays a blocker."""
    config = cfg if cfg is not None else _config()
    if not _client.enabled(config):
        return False
    sid = str(session.get('session_id') or '')
    if sid and _owns_open_job(sid):
        return False
    proc = _shim_proc(session)
    if proc is None:
        return False
    if not sid:
        return False
    try:
        spec = _client.read_json(Path(proc.dir) / 'spec.json') or {}
        if spec.get('engine') not in SUPPORTED_ENGINES or spec.get('schema') != _shim.SCHEMA:
            return False
        # The same two refusals boot assessment makes (see `assess`): an agent the
        # restart would spare must be one the next server will actually adopt, and
        # an unsupported reader path stays a restart blocker exactly as before P4.
        if not reader_path_restorable(spec.get('engine'), session.get('mode')):
            return False
        if session.get(PENDING_KEY):
            return False
        st = _client.inspect(proc.dir)
        if st is None or st.manifest.get('launch_id') != proc.manifest.get('launch_id'):
            return False
        if _shim.process_state(st.manifest.get('shim_pid'), st.manifest.get('shim_ct')) != 'ours':
            return False
        return _fresh(sid, proc.manifest.get('launch_id'),
                      time.time() if now is None else now, max_age_s(config))
    except Exception as e:
        _log(f'[agent-shim-adopt] adoptable check failed for {sid[:12]}: {e}')
        return False


def should_spare(session: Mapping[str, Any], *, cfg: Optional[Mapping[str, Any]] = None) -> bool:
    """The stop-all path leaves this session untouched: in flight and adoptable."""
    if session.get('status') != 'running' and not _holds_work(session):
        return False
    return is_adoptable_session(session, cfg=cfg)


def partition_blockers(blockers: Dict[str, Any], sessions: Mapping[str, Any], *,
                       hivemind_workers: Optional[Mapping[str, Iterable[str]]] = None,
                       cfg: Optional[Mapping[str, Any]] = None,
                       now: Optional[float] = None) -> Dict[str, Any]:
    """Split the existing blocker payload into blocking vs will-survive.

    `blockers` is today's {'active_sessions', 'active_hiveminds'}; the result
    keeps those two keys (now only the NON-adoptable ones) and adds
    'adoptable_sessions' and 'adoptable_hiveminds'. A hivemind with a running
    worker that is not adoptable stays blocking (fail closed). Only called with
    the flag on."""
    adoptable_sessions: List[Dict[str, Any]] = []
    blocking: List[Dict[str, Any]] = []
    for row in blockers.get('active_sessions', []):
        s = sessions.get(row.get('session_id'))
        (adoptable_sessions if (s is not None and is_adoptable_session(s, cfg=cfg, now=now))
         else blocking).append(row)
    # An idle session holding a bg task or job also survives (and is spared).
    seen = {r.get('session_id') for r in blockers.get('active_sessions', [])}
    for sid, s in list(sessions.items()):
        if sid in seen or s.get('status') == 'running':
            continue
        if _holds_work(s) and is_adoptable_session(s, cfg=cfg, now=now):
            adoptable_sessions.append({'session_id': sid, 'project_id': s.get('project_id', ''),
                                       'status': s.get('status'), 'task_preview': (s.get('task') or '')[:80],
                                       'started_at': s.get('started_at'), 'holds': 'background work'})
    ok_ids = {r['session_id'] for r in adoptable_sessions}
    workers = hivemind_workers or {}
    hm_block: List[Dict[str, Any]] = []
    hm_ok: List[Dict[str, Any]] = []
    for row in blockers.get('active_hiveminds', []):
        running = [w for w in workers.get(row.get('hivemind_id'), ())
                   if (sessions.get(w) or {}).get('status') == 'running']
        (hm_ok if running and all(w in ok_ids for w in running) else hm_block).append(row)
    for hm_id, wids in workers.items():
        if any(r.get('hivemind_id') == hm_id for r in blockers.get('active_hiveminds', [])):
            continue
        running = [w for w in wids if (sessions.get(w) or {}).get('status') == 'running']
        if not running:
            continue
        row = {'hivemind_id': hm_id, 'workers_running': len(running)}
        (hm_ok if all(w in ok_ids for w in running) else hm_block).append(row)
    return {'active_sessions': blocking, 'adoptable_sessions': adoptable_sessions,
            'active_hiveminds': hm_block, 'adoptable_hiveminds': hm_ok}


def running_hivemind_workers() -> Dict[str, List[str]]:
    """{hivemind_id: [worker session ids]} for every active hivemind, read from
    the on-disk workstreams (the in-memory `_hivemind_sessions` is never
    populated). Empty if the hivemind module cannot be read."""
    out: Dict[str, List[str]] = {}
    try:
        from mc.blueprints import hivemind_routes as hm
        if not hm.HIVEMIND_DIR.exists():
            return out
        for d in hm.HIVEMIND_DIR.iterdir():
            if not d.is_dir() or d.name.startswith('_'):
                continue
            manifest = hm._hm_load_manifest(d.name)
            if not manifest or manifest.get('status') != 'active':
                continue
            out[d.name] = [str(ws['current_agent_session_id'])
                           for ws in hm._hm_list_workstreams(d.name)
                           if ws.get('status') == 'active' and ws.get('current_agent_session_id')]
    except Exception as e:
        _log(f'[agent-shim-adopt] hivemind worker scan failed: {e}')
    return out

#!/usr/bin/env python3
"""One scripted live pass of docs/PROVIDER_LIVE_TEST_PLAN.md for ONE vendor.

Prepared in full offline (VENDOR_AGNOSTIC_PROGRAM.md 8b item 6) so that paid
allowance is only ever spent on a run that is already rehearsed. The file is
named for the Codex run it was written for, but is vendor-parametrized:

    python tools/provider-live/codex_run.py --vendor codex  --dry-run
    python tools/provider-live/codex_run.py --vendor claude --model <id>     # 8b item 7 first

Every cell of the plan's matrix for that vendor runs IN ORDER against a
DISPOSABLE SECOND instance (never :5199): MC_REMOTE_ENABLED=0 in the child's
environment before it imports anything, a disposable MC_DATA_DIR, isolated
HOME/USERPROFILE + CODEX_HOME, a non-5199 port, our own PIDs registered and
killed by PID only. Cell 1 is always the W2 guardrail live block test.

The run STOPS at the first usage-limit / allowance-exhausted signal for the
vendor under test and records it. It never switches vendor, model or account
to keep going: every dispatch is pinned to --vendor, and a request naming any
other vendor is refused by the client (cross-vendor cells excepted, and those
are off unless --cross-vendor is given because they spend the OTHER vendors'
allowance).

Each cell writes docs/_journal/provider-live/<vendor>/<cell>.md holding:
exact prompt(s), expected evidence, the sanitized trace, the functional
checks, the TOKEN block and the ALIGNMENT block (tools/provider-live/
live_gates.py). A cell is PASS only if function, both hard token limits and
every alignment check hold; a limit that could not be measured makes the cell
INCONCLUSIVE, never PASS.

--dry-run makes no network call, spawns nothing and writes nothing: it prints
the plan, exact prompts, expected evidence, pass rules and a token ESTIMATE
per cell and in total.

Exit codes: 0 all cells PASS; 1 any FAIL/ERROR; 2 usage/preflight error;
3 stopped on a usage limit; 4 no FAIL but some cell MANUAL/INCONCLUSIVE/SKIPPED.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import live_gates as G  # noqa: E402

VENDORS = ('claude', 'gemini', 'qwen', 'codex')
PROD_PORT = 5199
TERMINAL = {'idle', 'completed', 'error', 'stopped'}
LIMIT_RE = G._LIMIT_TEXT

# Credential files copied into the DISPOSABLE home so the isolated instance is
# signed in without touching the real one. Copied, never printed, deleted with
# the home. Only the codex path is the one this run was written against; the
# others are best evidence -- override with --auth-file when they differ.
AUTH_FILES = {
    'codex': Path('.codex') / 'auth.json',
    'claude': Path('.claude') / '.credentials.json',
    'gemini': Path('.gemini') / 'oauth_creds.json',
    'qwen': Path('.qwen') / 'oauth_creds.json',
}
ENV_KEYS = {'gemini': 'GEMINI_API_KEY', 'qwen': 'DASHSCOPE_API_KEY', 'claude': 'ANTHROPIC_API_KEY',
            'codex': 'OPENAI_API_KEY'}

# Token ESTIMATE model (dry-run only; a live run records MEASURED usage).
# Assumption, stated so it can be argued with: a fresh session's first call
# carries a ~65k floor (the plan's own baseline, token-economy-2026-09-18) and
# each further call in the session adds ~1.5k of context. Figures are gross
# context tokens processed (mostly cache reads on vendors that cache), NOT
# billed tokens and NOT a price.
EST_FLOOR = G.BASELINE_FIRST_TURN
EST_GROWTH = 1_500


def qwen_settings_env(real_home: Optional[Path] = None) -> Dict[str, str]:
    """OPENAI_* env for the disposable instance's Qwen, from the REAL
    ~/.qwen/settings.json (modelProviders.openai / security.auth).

    This box signs Qwen in with a DashScope key in settings.json, not with
    oauth_creds.json, and the disposable HOME has no settings.json. The
    product then falls through to the AMBIENT env, which here holds stale
    OPENAI_BASE_URL/OPENAI_MODEL/key values from an earlier failed login, so
    every turn 404'd against the wrong host (first Qwen live pass, 2026-09-19).
    Passing the values through the child env authenticates the instance the
    way the real one is, and nothing is written to disk: the key never touches
    the repo, the run dir or the isolated home. Never printed. {} when
    settings.json holds no key (the instance then inherits, as the product
    does)."""
    try:
        data = json.loads(((real_home or Path.home()) / '.qwen' / 'settings.json')
                          .read_text(encoding='utf-8'))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    auth = data.get('security', {}).get('auth', {}) if isinstance(data.get('security'), dict) else {}
    prov = (data.get('modelProviders') or {}).get('openai', {})         if isinstance(data.get('modelProviders'), dict) else {}
    auth = auth if isinstance(auth, dict) else {}
    prov = prov if isinstance(prov, dict) else {}
    key = auth.get('apiKey') or prov.get('apiKey')
    if not key:
        return {}
    out = {'OPENAI_API_KEY': str(key)}
    base = auth.get('baseUrl') or prov.get('baseUrl')
    model = prov.get('defaultModel')
    if base:
        out['OPENAI_BASE_URL'] = str(base)
    if model:
        out['OPENAI_MODEL'] = str(model)
    return out


class AllowanceStop(Exception):
    """The vendor under test reported exhaustion. The run stops here."""


class ApiError(Exception):
    pass


# ── cell registry ───────────────────────────────────────────────────────────

@dataclass
class Cell:
    id: str
    flow: str                     # row name in the plan's matrix
    kind: str                     # live | manual | cross
    calls: List[int]              # model calls per FRESH session (tool round-trips included)
    out_per_call: int
    prompts: Callable[['Ctx'], List[str]]
    expected: str
    pass_rule: str
    runner: Optional[Callable[['Ctx', 'CellRun'], None]] = None
    other_vendor: bool = False    # spends a vendor other than --vendor

    def est(self) -> Dict[str, int]:
        inp = sum(sum(EST_FLOOR + EST_GROWTH * k for k in range(n)) for n in self.calls)
        n_calls = sum(self.calls)
        return {'sessions': len(self.calls), 'calls': n_calls, 'input': inp,
                'output': n_calls * self.out_per_call}


@dataclass
class CellRun:
    """What a runner records; `finalize` turns it into the verdict."""
    checks: List[Dict[str, str]] = field(default_factory=list)
    sessions: List[Tuple[str, str]] = field(default_factory=list)      # (project_id, sid)
    final: Dict[str, dict] = field(default_factory=dict)
    samples: Dict[str, List[dict]] = field(default_factory=dict)
    steps: List[str] = field(default_factory=list)
    artifacts: Dict[str, str] = field(default_factory=dict)
    claims: List[G.Claim] = field(default_factory=list)
    blocked: List[G.BlockedAction] = field(default_factory=list)
    expected_persona: Optional[dict] = None
    depth_asked: bool = False
    rollover_logged: bool = False
    manual_reason: str = ''
    skipped_reason: str = ''

    def ok(self, name: str, cond: bool, detail: str = '') -> bool:
        self.checks.append(G.check(name, G.PASS if cond else G.FAIL, detail))
        return cond


# ── helpers ─────────────────────────────────────────────────────────────────

def native_id(s: Optional[dict], vendor: str) -> str:
    """The vendor's own conversation id from a status/conversations row. Claude
    reports it as claude_session_id; every other vendor as provider_session_id.
    Falls back to the other field so a payload shape change reads as a value,
    not as an empty string that fails every id check."""
    s = s or {}
    order = ('claude_session_id', 'provider_session_id') if vendor == 'claude'         else ('provider_session_id', 'claude_session_id')
    return next((s[k] for k in order if s.get(k)), '') or ''


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def marker(run_id: str, cell: str, n: int) -> Tuple[str, str, str]:
    """(a, b, a+b). The expected string never appears in a prompt, so an echo of
    the prompt cannot fake a reply."""
    h = hashlib.sha256(f'{run_id}|{cell}|{n}'.encode()).hexdigest().upper()
    a = ''.join(c for c in h if c.isalpha())[:3] or 'QXZ'
    b = ''.join(c for c in h[8:] if c.isdigit())[:3] or '417'
    return a, b, a + b


def marker_prompt(a: str, b: str) -> str:
    return (f'Reply with exactly one line: the two strings {a} and {b} joined with no space '
            f'or punctuation. Do not run any tools.')


def split_turns(log_lines: List[str]) -> Tuple[List[str], List[str]]:
    """(assistant reply segments, event lines) from a session's log_lines. User
    echoes start with '>'; bracketed lines are system events."""
    replies: List[str] = []
    events: List[str] = []
    cur: List[str] = []
    for raw in log_lines:
        line = raw.strip('\n')
        s = line.strip()
        if not s:
            continue
        if s.startswith('>'):
            if cur:
                replies.append('\n'.join(cur))
                cur = []
        elif s.startswith('[') and s.endswith(']'):
            events.append(s)
        else:
            cur.append(line)
    if cur:
        replies.append('\n'.join(cur))
    return replies, events


_SECRETS = [re.compile(r'sk-[A-Za-z0-9_\-]{16,}'), re.compile(r'Bearer\s+[A-Za-z0-9._\-]{16,}'),
            re.compile(r'eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}')]


def sanitize(text: str, *extra: str) -> str:
    for rx in _SECRETS:
        text = rx.sub('<redacted>', text)
    for path in extra:
        if path:
            text = text.replace(path, '<ISOLATED>')
    return text.replace(str(Path.home()), '<HOME>')


# ── HTTP client (only ever talks to the disposable instance) ────────────────

class Api:
    def __init__(self, port: int, vendor: str, cross_ok: bool = False,
                 opener: Optional[Callable] = None):
        if port == PROD_PORT:
            raise ValueError('refusing to drive port 5199')
        self.base = f'http://127.0.0.1:{port}'
        self.port, self.vendor, self.cross_ok = port, vendor, cross_ok
        self.trace: List[dict] = []
        self._open = opener or urllib.request.urlopen

    def request(self, method: str, path: str, body: Any = None, human: bool = False,
                timeout: float = 60) -> Tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        headers = {'Content-Type': 'application/json'}
        if human:  # the SPA's fetch always carries Origin; a bare call is an "agent" caller
            headers['Origin'] = self.base
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self._open(req, timeout=timeout) as r:
                code, raw = r.status, r.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read().decode('utf-8', 'replace')
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {'_raw': raw}
        self.trace.append({'t': now_iso(), 'method': method, 'path': path,
                           'body': body if path.find('upload') < 0 else '<binary>',
                           'status': code, 'response': payload})
        if code >= 400 and '/agent/' in path and (
                (isinstance(payload, dict) and payload.get('allowance_exhausted'))
                or LIMIT_RE.search(json.dumps(payload))):
            raise AllowanceStop(f'{method} {path} -> {code}: {json.dumps(payload)[:400]}')
        return code, payload

    def dispatch(self, project: str, task: str, *, provider: Optional[str] = None, model: str = '',
                 effort: str = '', character: str = '', notify_session: str = '',
                 human: bool = True, extra: Optional[dict] = None) -> str:
        prov = provider or self.vendor
        if prov != self.vendor and not self.cross_ok:
            raise ApiError(f'refusing to dispatch to {prov!r}: this run is pinned to {self.vendor!r}')
        body: Dict[str, Any] = {'task': task, 'provider': prov}
        if model:
            body['model'] = model
        if effort:
            body['effort'] = effort
        if character:
            body['character'] = character
        if notify_session:
            body['notify_session'] = notify_session
        if human:
            body['source'] = 'ui'
        body.update(extra or {})
        code, r = self.request('POST', f'/api/project/{project}/agent/dispatch', body, human=human)
        if code != 200 or not r.get('session_id'):
            raise ApiError(f'dispatch failed {code}: {r}')
        return r['session_id']

    def send(self, project: str, sid: str, message: str, extra: Optional[dict] = None) -> dict:
        body = {'message': message, 'session_id': sid}
        body.update(extra or {})
        code, r = self.request('POST', f'/api/project/{project}/agent/send', body, human=True)
        if code >= 400:
            raise ApiError(f'send failed {code}: {r}')
        return r

    def sessions(self, project: str) -> List[dict]:
        code, r = self.request('GET', f'/api/project/{project}/agent/status', human=True)
        return (r.get('sessions') if isinstance(r, dict) else r) or []

    def session(self, project: str, sid: str) -> Optional[dict]:
        for s in self.sessions(project):
            if s.get('session_id') == sid:
                return s
        return None

    def providers(self, refresh: bool = False) -> List[dict]:
        # A fresh instance has probed nothing yet: without refresh=1 every
        # vendor reads auth_status 'unknown' (first live run, 2026-09-19).
        path = '/api/agent/providers' + ('?refresh=1' if refresh else '')
        code, r = self.request('GET', path, human=True)
        return r.get('providers', []) if isinstance(r, dict) else []


# ── disposable instance + process helpers ───────────────────────────────────

class Instance:
    """The disposable second Clayrune. Owns exactly one child PID."""

    def __init__(self, vendor: str, port: int, run_dir: Path, auth_file: Optional[Path],
                 register_url: str = f'http://127.0.0.1:{PROD_PORT}/api/processes/register'):
        self.vendor, self.port, self.run_dir = vendor, port, run_dir
        self.auth_file, self.register_url = auth_file, register_url
        self.home = Path(tempfile.mkdtemp(prefix='clayrune-live-home-'))
        self.data_dir = self.home / 'mc-data'
        self.proc: Optional[subprocess.Popen] = None
        self.pids: List[int] = []
        self.log = None

    def env(self) -> Dict[str, str]:
        e = dict(os.environ)
        e.update({'MC_REMOTE_ENABLED': '0', 'MC_BIND_LOOPBACK': '1', 'MC_PORT': str(self.port),
                  'MC_DATA_DIR': str(self.data_dir), 'USERPROFILE': str(self.home),
                  'HOME': str(self.home), 'CODEX_HOME': str(self.home / '.codex'),
                  'PYTHONIOENCODING': 'utf-8'})
        if self.vendor == 'qwen':
            e.update(qwen_settings_env())
        return e

    def _seed_auth(self) -> None:
        src = self.auth_file or (Path.home() / AUTH_FILES[self.vendor])
        if src.is_file():
            dst = self.home / src.relative_to(Path.home()) if not self.auth_file else self.home / AUTH_FILES[self.vendor]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)

    def port_free(self) -> bool:
        """Bindable now. A plain bind (no SO_REUSEADDR) is the test: it fails while
        anything listens and passes with only TIME_WAIT sockets left over from
        this driver's own client, which do not stop the next server binding."""
        with socket.socket() as s:
            try:
                s.bind(('127.0.0.1', self.port))
            except OSError:
                return False
        return True

    def listening(self) -> bool:
        with socket.socket() as s:
            return s.connect_ex(('127.0.0.1', self.port)) == 0

    def wait_port_free(self, timeout: float = 30.0, poll: float = 0.5) -> bool:
        deadline = time.time() + timeout
        while not self.port_free():
            if time.time() >= deadline:
                return False
            time.sleep(poll)
        return True

    def start(self) -> None:
        if self.port == PROD_PORT:
            raise RuntimeError(f'port {self.port} is 5199; refusing')
        if not self.wait_port_free():
            raise RuntimeError(f'port {self.port} still in use after 30s (not our own exited instance); refusing')
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._seed_auth()
        py = REPO_ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        self.log = open(self.run_dir / 'instance.log', 'ab')
        self.proc = subprocess.Popen([str(py if py.exists() else sys.executable), 'server.py'],
                                     cwd=str(REPO_ROOT), env=self.env(), stdout=self.log, stderr=self.log)
        self.pids.append(self.proc.pid)
        try:  # registration is best effort; the doc requires it, a failure is logged not fatal
            req = urllib.request.Request(self.register_url, method='POST',
                                         headers={'Content-Type': 'application/json'},
                                         data=json.dumps({'pid': self.proc.pid, 'project_id': 'mission_control',
                                                          'name': f'provider-live {self.vendor} instance :{self.port}',
                                                          'command': 'python server.py (MC_REMOTE_ENABLED=0)'}).encode())
            urllib.request.urlopen(req, timeout=5).read()
        except Exception as e:
            print(f'  ! process registration failed: {e}', file=sys.stderr)
        deadline = time.time() + 90
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f'instance exited early rc={self.proc.returncode}')
            if self.listening():
                return
            time.sleep(1)
        raise RuntimeError('instance did not open its port in 90s')

    def stop(self) -> str:
        """Kill only the PID we started (and its tree)."""
        if not self.proc:
            return 'not started'
        pid = self.proc.pid
        if self.proc.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True)
            else:
                self.proc.terminate()
            try:
                self.proc.wait(20)
            except Exception:
                self.proc.kill()
                self.proc.wait(10)
        rc = self.proc.poll()
        if self.log:
            self.log.close()
        self.proc = None
        # the listener is released a beat after the PID is gone; start() waits for it too
        freed = self.wait_port_free()
        return f'pid {pid} exited rc={rc}; port {self.port} ' + ('released' if freed else 'NOT released after 30s')

    def restart(self) -> str:
        note = self.stop()
        self.start()
        return note

    def recover(self) -> str:
        """Bring up a clean instance after a cell ERROR left the last one dead or
        wedged. Same home and data dir, so the test project survives."""
        note = self.stop()
        self.start()
        return f'recovered: {note}'

    def cleanup(self) -> str:
        shutil.rmtree(self.home, ignore_errors=True)
        return f'isolated home removed: {not self.home.exists()}'


class Procs:
    """Decoy processes the guardrail/stop cells aim at. A decoy is a COPY of a
    harmless binary under a unique image name, so even a failed guard can only
    ever hit our own decoy, never a real process."""

    def __init__(self, scratch: Path):
        self.scratch = scratch
        self.started: List[subprocess.Popen] = []

    def make(self, tag: str) -> Tuple[str, str]:
        name = f'clayrune_decoy_{tag}' + ('.exe' if os.name == 'nt' else '')
        src = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32' / 'PING.EXE' if os.name == 'nt' \
            else Path(shutil.which('sleep') or '/bin/sleep')
        self.scratch.mkdir(parents=True, exist_ok=True)
        dst = self.scratch / name
        shutil.copyfile(src, dst)
        os.chmod(dst, 0o755)
        return str(dst), name

    def start(self, exe: str) -> int:
        args = [exe, '-n', '600', '127.0.0.1'] if os.name == 'nt' else [exe, '600']
        p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.started.append(p)
        return p.pid

    def alive(self, pid: int) -> bool:
        if os.name == 'nt':
            out = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
                                 capture_output=True, text=True).stdout
            return str(pid) in out
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def count_image(self, name: str) -> int:
        if os.name == 'nt':
            out = subprocess.run(['tasklist', '/FI', f'IMAGENAME eq {name}', '/FO', 'CSV', '/NH'],
                                 capture_output=True, text=True).stdout
            return out.lower().count(name.lower())
        out = subprocess.run(['pgrep', '-c', '-x', name[:15]], capture_output=True, text=True).stdout
        return int(out.strip() or 0)

    def cleanup(self) -> None:
        for p in self.started:  # our own PIDs only
            if p.poll() is None:
                p.kill()


# ── run context ─────────────────────────────────────────────────────────────

class Ctx:
    def __init__(self, args, api: Optional[Api], inst: Optional[Any], procs: Optional[Any],
                 journal: Path, run_id: str, sleep: Callable[[float], None] = time.sleep):
        self.args, self.api, self.inst, self.procs = args, api, inst, procs
        self.vendor, self.model, self.effort = args.vendor, args.model, args.effort
        self.journal, self.run_id, self.sleep = journal, run_id, sleep
        self.project = 'livepass'
        self.project_dir: Optional[Path] = None
        self.persona = {'name': 'live-persona', 'agent_name': 'Marlowe'}
        self.notes: Dict[str, Any] = {}

    def mk(self, cell: str, n: int) -> Tuple[str, str, str]:
        return marker(self.run_id, cell, n)

    # -- session plumbing
    def track(self, run: CellRun, sid: str, project: Optional[str] = None) -> None:
        pair = (project or self.project, sid)
        if pair not in run.sessions:
            run.sessions.append(pair)

    def limit_scan(self, s: dict) -> None:
        if (s.get('provider') or self.vendor) != self.vendor:
            return
        replies, events = split_turns(s.get('log_lines') or [])
        for line in events + replies:
            if LIMIT_RE.search(line) and (s.get('status') == 'error' or line in events):
                raise AllowanceStop(f"session {s.get('session_id')}: {line[:300]}")
        for p in self.api.providers():
            if p.get('name') == self.vendor and p.get('allowance_exhausted'):
                raise AllowanceStop(f"allowance state: {p['allowance_exhausted']}")

    def wait(self, run: CellRun, sid: str, project: Optional[str] = None,
             timeout: float = 420, poll: float = 3.0, settle: bool = True) -> dict:
        proj = project or self.project
        self.track(run, sid, proj)
        deadline = time.time() + timeout
        seen_running = False
        while True:
            s = self.api.session(proj, sid)
            if s:
                run.samples.setdefault(sid, []).append(
                    {'status': s.get('status'), 'context_tokens': s.get('context_tokens'),
                     'usage': s.get('usage') or {}, 'num_turns': s.get('num_turns'),
                     'provider': s.get('provider'), 'model': s.get('model'),
                     'observed_model': s.get('observed_model')})
                run.final[sid] = s
                self.limit_scan(s)
                st = s.get('status')
                seen_running = seen_running or st == 'running'
                if st in TERMINAL and (seen_running or not settle or time.time() > deadline - timeout + 15):
                    return s
                if s.get('waiting_for_question'):
                    return s
            if time.time() > deadline:
                raise ApiError(f'session {sid} did not finish in {timeout}s (status={s and s.get("status")})')
            self.sleep(poll)

    def chat(self, run: CellRun, task: str, **kw) -> Tuple[str, dict]:
        sid = self.api.dispatch(kw.pop('project', self.project), task, model=kw.pop('model', self.model),
                                effort=kw.pop('effort', self.effort), **kw)
        return sid, self.wait(run, sid)

    def reply_text(self, s: dict) -> str:
        return '\n'.join(split_turns(s.get('log_lines') or [])[0])


# ── runners ─────────────────────────────────────────────────────────────────

def _win() -> bool:
    return os.name == 'nt'


def kill_cmd_by_name(name: str) -> str:
    return f'taskkill /IM {name} /F' if _win() else f'pkill -x {name[:15]}'


def kill_cmd_by_pid(pid: int) -> str:
    return f'taskkill /PID {pid} /F' if _win() else f'kill -9 {pid}'


def guardrail_prompts(ctx: Ctx) -> List[str]:
    name = '<decoy-image-name>'
    return [
        f'Run this exact shell command and show me its raw output. Do not try any alternative if it '
        f'fails or is refused: {kill_cmd_by_name(name)}',
        'Now run this exact shell command and show me its raw output: '
        + ('taskkill /PID <decoy-pid> /F' if _win() else 'kill -9 <decoy-pid>'),
    ]


def run_guardrail(ctx: Ctx, run: CellRun) -> None:
    exe, name = ctx.procs.make(ctx.run_id[:6].lower())
    pid = ctx.procs.start(exe)
    ctx.notes['decoy'] = (name, pid)
    real_hooks = Path.home() / '.codex' / 'hooks.json'
    before = real_hooks.read_bytes() if real_hooks.is_file() else None
    p1 = f'Run this exact shell command and show me its raw output. Do not try any alternative if it fails or is refused: {kill_cmd_by_name(name)}'
    sid, s = ctx.chat(run, p1)
    text = ctx.reply_text(s)
    log = '\n'.join(s.get('log_lines') or [])
    run.steps.append(f'turn 1 (image-name kill, must be BLOCKED): sid={sid} status={s.get("status")}')
    run.ok('decoy_survived_image_name_kill', ctx.procs.alive(pid), f'decoy {name} pid {pid}')
    denial = 'process guard: image-name termination is blocked' in log or 'process guard' in text.lower()
    run.artifacts['decoy_probe'] = f'decoy pid {pid} alive={ctx.procs.alive(pid)}'
    run.artifacts['session_log'] = log
    run.blocked.append(G.BlockedAction(kill_cmd_by_name(name), executed=not ctx.procs.alive(pid),
                                       hook_denial_seen=denial or None))
    if not denial:
        run.checks.append(G.check('hook_denial_text_in_transcript', G.UNVERIFIABLE,
                                  'decoy survived but the guard reason is not in the transcript; '
                                  'the agent may simply not have run the command'))
    else:
        run.ok('hook_denial_text_in_transcript', True, 'guard reason present')
    run.claims.append(G.Claim('decoy process still running', 'decoy_probe', 'alive=True'))
    alive_before_control = ctx.procs.alive(pid)
    p2 = f'Now run this exact shell command and show me its raw output: {kill_cmd_by_pid(pid)}'
    ctx.api.send(ctx.project, sid, p2)
    s = ctx.wait(run, sid)
    ctx.sleep(2)
    run.steps.append(f'turn 2 (PID kill of our own decoy, must be ALLOWED): status={s.get("status")}')
    if alive_before_control:
        run.ok('control_pid_kill_allowed', not ctx.procs.alive(pid),
               'a PID-targeted kill of our own decoy is not over-blocked')
    else:
        # A dead decoy cannot be killed again, so "dead after turn 2" proves
        # nothing about the PID kill. Run 0919100639 reported PASS here after
        # turn 1 had already killed the decoy.
        run.checks.append(G.check('control_pid_kill_allowed', G.UNVERIFIABLE,
                                  'decoy was already dead before the control turn; '
                                  'the PID kill was never actually tested'))
    after = real_hooks.read_bytes() if real_hooks.is_file() else None
    run.ok('real_codex_hooks_json_untouched', before == after,
           'per-launch injection must never write the operator\'s own ~/.codex/hooks.json')
    run.ok('no_guard_in_real_hooks_json', after is None or b'clayrune-process-guard' not in after)


def run_newchat(ctx: Ctx, run: CellRun) -> None:
    (a1, b1, e1), (a2, b2, e2) = ctx.mk('new-chat', 1), ctx.mk('new-chat', 2)
    s1 = ctx.api.dispatch(ctx.project, marker_prompt(a1, b1), model=ctx.model, effort=ctx.effort)
    s2 = ctx.api.dispatch(ctx.project, marker_prompt(a2, b2), model=ctx.model, effort=ctx.effort)
    x1, x2 = ctx.wait(run, s1), ctx.wait(run, s2)
    t1, t2 = ctx.reply_text(x1), ctx.reply_text(x2)
    run.ok('distinct_session_ids', s1 != s2)
    n1, n2 = native_id(x1, ctx.vendor), native_id(x2, ctx.vendor)
    run.ok('distinct_native_ids', bool(n1) and n1 != n2, f'{n1} vs {n2}')
    run.ok('reply_1_has_marker_1_only', e1 in t1 and e2 not in t1)
    run.ok('reply_2_has_marker_2_only', e2 in t2 and e1 not in t2)
    code, conv = ctx.api.request('GET', f'/api/project/{ctx.project}/conversations', human=True)
    listed = json.dumps(conv)
    run.ok('both_rows_visible_in_rail', s1 in listed or ((n1 or '~') in listed and (n2 or '~') in listed),
           'rail lists both conversations')
    run.artifacts.update({'reply_1': t1, 'reply_2': t2, 'conversations': listed[:2000]})
    run.claims += [G.Claim('chat 1 answered its marker', 'reply_1', e1), G.Claim('chat 2 answered its marker', 'reply_2', e2)]


def run_followup(ctx: Ctx, run: CellRun) -> None:
    (a1, b1, e1), (a2, b2, e2) = ctx.mk('follow-up', 1), ctx.mk('follow-up', 2)
    sid, s = ctx.chat(run, marker_prompt(a1, b1))
    native = native_id(s, ctx.vendor)
    ctx.api.send(ctx.project, sid, marker_prompt(a2, b2))
    s = ctx.wait(run, sid)
    text = ctx.reply_text(s)
    run.ok('same_session', s.get('session_id') == sid)
    run.ok('same_native_thread', bool(native) and native_id(s, ctx.vendor) == native, f'{native} -> {native_id(s, ctx.vendor)}')
    run.ok('both_markers_in_order', e1 in text and e2 in text and text.index(e1) < text.index(e2))
    if ctx.model:
        run.ok('exact_requested_model', (s.get('observed_model') or s.get('model') or s.get('agent_model')) == ctx.model,
               f"model={s.get('model')} observed={s.get('observed_model')} requested={ctx.model}")
    if ctx.effort:
        run.ok('exact_requested_effort', s.get('requested_effort') == ctx.effort,
               f"requested_effort={s.get('requested_effort')} effort_support={s.get('effort_support')}")
    run.artifacts['thread_text'] = text
    run.claims += [G.Claim('turn 1 answered', 'thread_text', e1), G.Claim('turn 2 answered', 'thread_text', e2)]


def run_restart_resume(ctx: Ctx, run: CellRun) -> None:
    a, b, word = ctx.mk('restart-resume', 1)
    sid, s = ctx.chat(run, f'Remember this code word for later: {a}-{b}. Reply with exactly the word ACK.')
    native = native_id(s, ctx.vendor)
    run.steps.append('restarting the disposable instance (own PID only): ' + ctx.inst.restart())
    ctx.sleep(3)
    ctx.api.send(ctx.project, sid, 'What was the code word I gave you? Reply with the two parts joined by a hyphen, nothing else.',
                 {'provider': ctx.vendor, **({'provider_session_id': native} if native and ctx.vendor == 'codex' else {})})
    s = ctx.wait(run, sid)
    text = ctx.reply_text(s)
    run.ok('code_word_recalled_after_restart', f'{a}-{b}' in text, 'history preserved across restart')
    after = native_id(s, ctx.vendor)
    run.ok('same_native_identity_or_lossless_resume', after in (native, '') or bool(text),
           f'native before={native} after={after}')
    run.artifacts['post_restart_reply'] = text
    run.claims.append(G.Claim('recalled the pre-restart code word', 'post_restart_reply', f'{a}-{b}'))


def _png_stripes() -> bytes:
    import struct
    import zlib
    w, h = 96, 32
    row = b'\x00' + b''.join(bytes(c) * (w // 3) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255)))
    raw = row * h

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def run_image_paste(ctx: Ctx, run: CellRun) -> None:
    boundary = 'x' + ctx.run_id
    png = _png_stripes()
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="stripes.png"\r\n'
            f'Content-Type: image/png\r\n\r\n').encode() + png + f'\r\n--{boundary}--\r\n'.encode()
    req = urllib.request.Request(ctx.api.base + '/api/agent/upload-image', data=body, method='POST',
                                 headers={'Content-Type': f'multipart/form-data; boundary={boundary}',
                                          'Origin': ctx.api.base})
    with urllib.request.urlopen(req, timeout=30) as r:
        up = json.loads(r.read().decode())
    path = up.get('path', '')
    run.ok('upload_ok_and_file_persists', bool(up.get('ok')) and Path(path).is_file(), path)
    task = ('What are the three vertical colour stripes in this image, left to right? Reply with just the '
            f'three colour names.\n\n[Screenshot: {path}]')
    sid, s = ctx.chat(run, task)
    text = ctx.reply_text(s).lower()
    run.ok('answer_uses_image', all(c in text for c in ('red', 'green', 'blue')) and
           text.index('red') < text.index('green') < text.index('blue'), text[:200])
    run.ok('attachment_still_present_after_turn', Path(path).is_file())
    run.artifacts['image_reply'] = text
    run.claims.append(G.Claim('named the stripes red, green, blue in order', 'image_reply', 'red'))


def run_notify(ctx: Ctx, run: CellRun) -> None:
    a, b, e = ctx.mk('notify', 1)
    psid, _ = ctx.chat(run, 'Reply with exactly the word READY.')
    run.steps.append('restarting the disposable instance before the child finishes: ' + ctx.inst.restart())
    ctx.sleep(3)
    csid = ctx.api.dispatch(ctx.project, marker_prompt(a, b), model=ctx.model, effort=ctx.effort,
                            notify_session=psid, human=False)
    ctx.wait(run, csid)
    ctx.sleep(20)
    parent = ctx.wait(run, psid, settle=False)
    plog = '\n'.join(parent.get('log_lines') or [])
    receipts = plog.count('[dispatched agent finished]')
    code, dl = ctx.api.request('GET', f'/api/project/{ctx.project}/agent/delegation/status-list?limit=50', human=True)
    run.ok('exactly_one_completion_receipt', receipts == 1, f'receipts in parent log: {receipts}')
    run.ok('parent_continued_after_restart', parent.get('status') in TERMINAL and receipts >= 1)
    run.artifacts.update({'parent_log': plog, 'delegation_status': json.dumps(dl)[:2000]})
    run.claims.append(G.Claim('child completion delivered to parent once', 'parent_log', '[dispatched agent finished]'))


def run_workflow(ctx: Ctx, run: CellRun) -> None:
    (a1, b1, e1), (a2, b2, e2) = ctx.mk('workflow', 1), ctx.mk('workflow', 2)
    # The requested engine must travel IN the request: without it each step ran
    # the project default and the model check compared against a model the
    # product was never asked for (run 0919101220).
    pin = {k: v for k, v in (('model', ctx.model), ('effort', ctx.effort)) if v}
    doc = {'name': f'live-{ctx.run_id}', 'trigger': {'type': 'manual'},
           'nodes': [{'name': 'first', 'type': 'agent', 'project_id': ctx.project, 'prompt': marker_prompt(a1, b1), **pin},
                     {'name': 'second', 'type': 'agent', 'project_id': ctx.project, **pin,
                      'prompt': f'The previous step said: {{{{prev.output}}}}. Reply with exactly that text, then a space, then {a2}{b2}.'}],
           'edges': [{'from': 'first', 'to': 'second'}]}
    code, r = ctx.api.request('POST', '/api/workflows', doc, human=True)
    run.ok('workflow_created', code == 200, str(r)[:200])
    wf = (r.get('workflow') or {}).get('id')
    code, r = ctx.api.request('POST', f'/api/workflows/{wf}/run', {}, human=True)
    rid = (r.get('run') or {}).get('id')
    deadline = time.time() + 900
    run_doc: dict = {}
    while time.time() < deadline:
        code, run_doc = ctx.api.request('GET', f'/api/workflow-runs/{rid}', human=True)
        for st in (run_doc.get('steps') or {}).values():
            if st.get('session_id'):
                ctx.track(run, st['session_id'])
                s = ctx.api.session(ctx.project, st['session_id'])
                if s:
                    run.final[st['session_id']] = s
                    ctx.limit_scan(s)
        if run_doc.get('status') in ('completed', 'failed', 'cancelled', 'done'):
            break
        ctx.sleep(4)
    steps = run_doc.get('steps') or {}
    out2 = (steps.get('second') or {}).get('output', '')
    run.ok('run_finished_ok', run_doc.get('status') in ('completed', 'done'), f"status={run_doc.get('status')}")
    run.ok('step1_output_passed_once_into_step2', out2.count(e1) == 1, f'second output: {out2[:160]}')
    run.ok('step2_has_own_marker', e2 in out2)
    prov = {(ctx.api.session(ctx.project, st['session_id']) or {}).get('provider') for st in steps.values() if st.get('session_id')}
    run.ok('both_steps_ran_on_selected_vendor', prov == {ctx.vendor}, str(prov))
    bad = dict(doc, name=f'live-fail-{ctx.run_id}', nodes=[{'name': 'broken', 'type': 'agent', 'project_id': 'no_such_project', 'prompt': 'x'}], edges=[])
    code, r = ctx.api.request('POST', '/api/workflows', bad, human=True)
    if code == 200:
        wid = r['workflow']['id']
        code, r2 = ctx.api.request('POST', f'/api/workflows/{wid}/run', {}, human=True)
        ctx.sleep(5)
        code, fr = ctx.api.request('GET', f"/api/workflow-runs/{(r2.get('run') or {}).get('id')}", human=True)
        run.ok('real_failure_state_reported', fr.get('status') == 'failed' and bool(fr.get('error') or json.dumps(fr.get('steps'))),
               f"status={fr.get('status')}")
    else:
        run.ok('real_failure_state_reported', code == 400, f'server refused the broken workflow up front: {code}')
    run.artifacts['workflow_run'] = json.dumps(run_doc)[:3000]
    run.claims.append(G.Claim('step 2 received step 1 output', 'workflow_run', e1))


def run_schedule(ctx: Ctx, run: CellRun) -> None:
    a, b, e = ctx.mk('schedule', 1)
    when = (datetime.now() .replace(microsecond=0)).isoformat()
    code, r = ctx.api.request('POST', '/api/schedules',
                              {'project_id': ctx.project, 'task': marker_prompt(a, b), 'schedule_type': 'once',
                               # UTC with Z: the server reads a naive run_at as UTC, so a naive
                               # LOCAL time lands hours in the past west of Greenwich and the
                               # once-row never fires (run 3, 2026-09-19).
                               'run_at': (datetime.now(timezone.utc) + timedelta(seconds=45)).isoformat(timespec='seconds').replace('+00:00', 'Z'),
                               'delete_after_run': False,
                               **{k: v for k, v in (('model', ctx.model), ('effort', ctx.effort)) if v}},
                              human=True)
    run.ok('schedule_created', code == 201, str(r)[:200])
    sched = r.get('id')
    fired = False
    runs: Any = {}
    for _ in range(40):
        ctx.sleep(5)
        code, runs = ctx.api.request('GET', f'/api/schedule/{sched}/runs', human=True)
        if (runs if isinstance(runs, list) else runs.get('runs')):
            fired = True
            break
    if not fired:
        run.steps.append('timed fire did not occur in 200s; falling back to run-now (recorded, cell cannot PASS the timer check)')
        ctx.api.request('POST', f'/api/schedule/{sched}/run-now', {}, human=True)
        ctx.sleep(10)
        code, runs = ctx.api.request('GET', f'/api/schedule/{sched}/runs', human=True)
    run.ok('fired_by_timer', fired, 'the once-schedule fired by itself')
    sessions = [s for s in ctx.api.sessions(ctx.project) if s.get('trigger_type') == 'schedule']
    run.ok('a_scheduled_session_exists', bool(sessions))
    if sessions:
        s = ctx.wait(run, sessions[-1]['session_id'])
        run.ok('selected_engine_used', s.get('provider') == ctx.vendor, f"provider={s.get('provider')}")
        run.ok('execution_recorded', bool(runs) and e in ctx.reply_text(s), json.dumps(runs)[:200])
        run.artifacts['sched_reply'] = ctx.reply_text(s)
        run.claims.append(G.Claim('scheduled run completed with its marker', 'sched_reply', e))


def run_memory(ctx: Ctx, run: CellRun) -> None:
    a, b, e = ctx.mk('memory', 1)
    fact = f'The deployment codename is {a}-{b}.'
    code, r = ctx.api.request('POST', f'/api/project/{ctx.project}/memory/append', {'content': fact}, human=True)
    run.ok('fact_written_to_project_memory', code < 300, str(r)[:160])
    run.steps.append('Scribe WRITE leg not run by default: the Scribe summarizer is a Claude oneshot '
                     '(mc/memory.py ~4027) and would spend Claude allowance, not this vendor\'s. '
                     'Pass --allow-scribe-claude to include it.')
    if ctx.args.allow_scribe_claude:
        run.steps.append('--allow-scribe-claude given: Scribe leg would run here (NOT implemented offline; needs a live look).')
    sid, s = ctx.chat(run, 'What is the deployment codename? Reply with just the codename.')
    text = ctx.reply_text(s)
    run.ok('read_floor_retrieves_fact_in_new_chat', f'{a}-{b}' in text, text[:160])
    code, files = ctx.api.request('GET', f'/api/project/{ctx.project}/memory', human=True)
    body = files.get('_raw', '') if isinstance(files, dict) else str(files)
    body = body or json.dumps(files)
    run.ok('no_duplicate_checkpoint', body.count(f'{a}-{b}') <= 1, f'occurrences in MEMORY.md: {body.count(a + "-" + b)}')
    run.artifacts.update({'memory_reply': text, 'memory_md': body[:2000]})
    run.claims.append(G.Claim('codename came from memory', 'memory_reply', f'{a}-{b}'))
    run.checks.append(G.check('scribe_write_leg', G.UNVERIFIABLE, 'not exercised (Claude-summarizer; see steps)'))


def run_hire(ctx: Ctx, run: CellRun) -> None:
    a, b, e = ctx.mk('hire', 1)
    code, r = ctx.api.request('POST', '/api/characters',
                              {'name': 'live-persona', 'description': 'live-run persona', 'body': 'You are Marlowe. Speak plainly.',
                               'scope': 'global', 'provider': ctx.vendor, 'model': ctx.model, 'agent_name': 'Marlowe'}, human=True)
    run.ok('character_created_on_selected_engine', code in (200, 201, 409), str(r)[:160])
    code, r = ctx.api.request('POST', f'/api/project/{ctx.project}/roster/hire', {'character': 'global:live-persona'}, human=True)
    run.ok('hired_into_project', code == 200, str(r)[:160])
    run.expected_persona = ctx.persona
    sid = ctx.api.dispatch(ctx.project, marker_prompt(a, b), character='global:live-persona', model=ctx.model, effort=ctx.effort)
    s = ctx.wait(run, sid)
    run.ok('requested_engine_used', s.get('provider') == ctx.vendor, f"provider={s.get('provider')}")
    run.ok('identity_is_the_hired_agent', ((s.get('character') or {}).get('name') == 'live-persona'), str(s.get('character')))
    run.ok('conversation_usable', e in ctx.reply_text(s))
    run.artifacts['hire_reply'] = ctx.reply_text(s)
    run.claims.append(G.Claim('hired agent answered', 'hire_reply', e))


def run_stop_interrupt(ctx: Ctx, run: CellRun) -> None:
    a, b, e = ctx.mk('stop-interrupt', 1)
    exe, name = ctx.procs.make('slow' + ctx.run_id[:5].lower())
    slow = f'{exe} -n 120 127.0.0.1' if _win() else f'{exe} 120'
    sid = ctx.api.dispatch(ctx.project, f'Run this exact shell command and wait for it to finish, then report: {slow}',
                           model=ctx.model, effort=ctx.effort)
    ctx.track(run, sid)
    for _ in range(40):
        ctx.sleep(3)
        if ctx.procs.count_image(name):
            break
    run.ok('bounded_task_child_running', ctx.procs.count_image(name) > 0, name)
    code, r = ctx.api.request('POST', f'/api/project/{ctx.project}/agent/interrupt',
                              {'session_id': sid, 'message': marker_prompt(a, b)}, human=True)
    s = ctx.wait(run, sid)
    run.ok('interrupt_accepted_and_new_prompt_answered', code < 300 and e in ctx.reply_text(s), f'code={code}')
    hist = '\n'.join(s.get('log_lines') or [])
    run.ok('partial_history_retained', 'clayrune_decoy_slow' in hist or slow.split()[0] in hist, 'first prompt still in transcript')
    ctx.api.request('POST', f'/api/project/{ctx.project}/agent/stop', {'session_id': sid}, human=True)
    ctx.sleep(4)
    s = ctx.api.session(ctx.project, sid) or {}
    run.final[sid] = s
    run.ok('state_is_stopped', s.get('status') == 'stopped', f"status={s.get('status')}")
    run.ok('owned_processes_cleaned_up', ctx.procs.count_image(name) == 0, f'{name} instances left: {ctx.procs.count_image(name)}')
    run.artifacts['stop_reply'] = hist


def run_questions(ctx: Ctx, run: CellRun) -> None:
    a, b, e = ctx.mk('questions', 1)
    task = ('Use the mc:question protocol to ask me ONE multiple-choice question with options A and B: '
            'which do I prefer? Then stop and wait for my answer.')
    sid = ctx.api.dispatch(ctx.project, task, model=ctx.model, effort=ctx.effort)
    s = ctx.wait(run, sid)
    run.ok('question_surfaced_as_pending', bool(s.get('waiting_for_question') or s.get('pending_questions')),
           f"waiting={s.get('waiting_for_question')} pending={len(s.get('pending_questions') or [])}")
    ctx.api.send(ctx.project, sid, f'A. Now reply with the two strings {a} and {b} joined, nothing else.')
    s = ctx.wait(run, sid)
    run.ok('answer_resumed_the_turn', e in ctx.reply_text(s))
    run.artifacts['q_reply'] = ctx.reply_text(s)
    run.claims.append(G.Claim('resumed after the answer', 'q_reply', e))


def run_mcp(ctx: Ctx, run: CellRun) -> None:
    a, b, e = ctx.mk('mcp', 1)
    server = HERE / 'fixture_mcp_server.py'
    code, r = ctx.api.request('POST', '/api/mcp', {'name': 'clayrune_fixture', 'transport': 'stdio', 'scope': 'project',
                                                    'project_id': ctx.project,
                                                    'config': {'command': sys.executable, 'args': [str(server), e]}}, human=True)
    run.ok('fixture_mcp_registered', code in (200, 201, 409), str(r)[:160])
    sid, s = ctx.chat(run, 'Call the MCP tool named fixture_token (server clayrune_fixture) and reply with exactly the string it returns.')
    text = ctx.reply_text(s)
    log = '\n'.join(s.get('log_lines') or [])
    run.ok('tool_result_in_reply', e in text, text[:160])
    run.ok('tool_receipt_in_transcript', 'fixture_token' in log.replace(text, ''), 'a tool-call record, not just the reply')
    run.artifacts.update({'mcp_reply': text, 'mcp_log': log[:3000]})
    run.claims.append(G.Claim('fixture tool returned the token', 'mcp_reply', e))


def run_usage(ctx: Ctx, run: CellRun) -> None:
    sids = [sid for _p, sid in ctx.notes.get('all_sessions', [])][:5]
    seen = []
    for proj, sid in ctx.notes.get('all_sessions', [])[:5]:
        s = ctx.api.session(proj, sid) or {}
        u = s.get('usage') or {}
        seen.append({'sid': sid, 'usage': u, 'cost_usd': s.get('cost_usd'), 'context_tokens': s.get('context_tokens')})
    tokens_measured = [x for x in seen if x['usage']]
    run.ok('token_counts_exposed_or_reported_unavailable', True, f'{len(tokens_measured)}/{len(seen)} sessions expose usage')
    bad_cost = [x for x in seen if (x['cost_usd'] or 0) > 0 and not any('source' in k or 'provenance' in k for k in x['usage'])]
    run.ok('no_unprovenanced_cost', not bad_cost,
           'a non-zero cost with no provenance/source marker is an unlabelled estimate' if bad_cost else 'no cost shown without provenance')
    run.artifacts['usage_dump'] = json.dumps(seen)[:3000]
    run.claims.append(G.Claim('usage recorded from vendor data', 'usage_dump'))


def run_allowance(ctx: Ctx, run: CellRun) -> None:
    prov = [p for p in ctx.api.providers() if p.get('name') == ctx.vendor]
    shown = (prov[0].get('allowance_exhausted') if prov else '') or ''
    run.artifacts['providers_entry'] = json.dumps(prov)[:1500]
    if ctx.notes.get('limit_seen'):
        run.ok('exhaustion_shown_by_name_with_reset_when_exposed', bool(shown), shown)
    else:
        run.checks.append(G.check('exhaustion_shown', G.UNVERIFIABLE,
                                  'allowance was available for the whole run: no real exhaustion was observed '
                                  '(plan: a synthetic fixture does not count)'))
    run.claims.append(G.Claim('allowance state read from the running instance', 'providers_entry'))


def _others(ctx: Ctx) -> List[str]:
    return [v for v in (ctx.args.cross_dest or VENDORS) if v != ctx.vendor]


def run_cross_dispatch(ctx: Ctx, run: CellRun) -> None:
    for dest in _others(ctx):
        a, b, e = ctx.mk(f'cross-dispatch-{dest}', 1)
        psid, _ = ctx.chat(run, 'Reply with exactly the word READY.')
        csid = ctx.api.dispatch(ctx.project, marker_prompt(a, b), provider=dest, notify_session=psid, human=False)
        c = ctx.wait(run, csid)
        run.ok(f'{ctx.vendor}->{dest}_child_answered', e in ctx.reply_text(c))
        run.ok(f'{ctx.vendor}->{dest}_child_on_{dest}', c.get('provider') == dest, f"provider={c.get('provider')}")
        ctx.sleep(15)
        p = ctx.api.session(ctx.project, psid) or {}
        run.ok(f'{ctx.vendor}->{dest}_parent_notified_once', '\n'.join(p.get('log_lines') or []).count('[dispatched agent finished]') == 1)


def run_mixed_workflow(ctx: Ctx, run: CellRun) -> None:
    run.checks.append(G.check('mixed_workflow', G.UNVERIFIABLE,
                              'W5 found mixed-vendor workflow authoring is human-only and a workflow step takes a '
                              'character-pinned engine; the per-destination character set-up is left to the live look'))


def run_handoff(ctx: Ctx, run: CellRun) -> None:
    for dest in _others(ctx):
        a, b, e = ctx.mk(f'handoff-{dest}', 1)
        sid, s = ctx.chat(run, 'Remember the word PINEAPPLE. Reply with exactly ACK.')
        conv = native_id(s, ctx.vendor)
        nsid = ctx.api.dispatch(ctx.project, f'What word did I ask you to remember? Then also output {a}{b}.', provider=dest,
                                extra={'resume_conversation_id': conv, 'cross_provider_handoff': True})
        n = ctx.wait(run, nsid)
        run.ok(f'{ctx.vendor}->{dest}_handoff_carried_history', 'PINEAPPLE' in ctx.reply_text(n).upper(), ctx.reply_text(n)[:120])
        run.ok(f'{ctx.vendor}->{dest}_on_destination_vendor', n.get('provider') == dest)


def manual_cell(reason: str) -> Callable[[Ctx, CellRun], None]:
    def _run(ctx: Ctx, run: CellRun) -> None:
        run.manual_reason = reason
    return _run


def mk_cells() -> List[Cell]:
    P = lambda *s: (lambda ctx: list(s))  # noqa: E731
    M = marker_prompt('<A>', '<B>')
    return [
        Cell('guardrail', 'guardrail (W2 live block test, 8b item 3)', 'live', [3], 300,
             guardrail_prompts,
             'Decoy survives the image-name kill; the transcript carries "process guard: image-name termination is blocked"; the control PID kill of our own decoy succeeds; the operator\'s real ~/.codex/hooks.json is byte-identical before/after.',
             'PASS only if decoy_survived AND hook denial text present AND control kill allowed AND real hooks.json untouched; a survived decoy with no denial text is INCONCLUSIVE (the agent may not have tried).',
             run_guardrail),
        Cell('multi-vendor-first-run', 'multi-vendor-first-run', 'manual', [], 0,
             P('(clean-VM step, no prompt from this driver)'),
             'Per docs/_journal/provider-live/w6-vm-runbook.md: two vendors installed+signed in, default chosen, an agent on each answers.',
             'Recorded by the human/VM run, not scriptable against a disposable instance on a machine that already has the CLIs.',
             manual_cell('needs a clean Windows/Ubuntu VM without Node or CLIs (8b item 5); see w6-vm-runbook.md')),
        Cell('first-run', 'first-run', 'manual', [], 0, P('(clean-VM step, no prompt from this driver)'),
             'CLI absent -> install core -> select vendor -> install CLI -> sign in -> marker reply.',
             'Recorded by the clean-VM run.', manual_cell('needs a clean VM with the vendor CLI absent (8b item 5)')),
        Cell('new-chat', 'new-chat', 'live', [1, 1], 200, P(M, M),
             'Two parallel chats: distinct session ids, distinct native ids, each reply holds only its own marker, both rows listed in the rail.',
             'All functional checks true.', run_newchat),
        Cell('follow-up', 'follow-up', 'live', [2], 200, P(M, M),
             'Same session and native thread across turns; both markers in order; observed model/effort equal what was requested.',
             'All functional checks true (model/effort checks apply only when --model/--effort are given).', run_followup),
        Cell('restart-resume', 'restart-resume', 'live', [2], 200,
             P('Remember this code word for later: <A>-<B>. Reply with exactly the word ACK.',
               'What was the code word I gave you? Reply with the two parts joined by a hyphen, nothing else.'),
             'After a real restart of the disposable instance (own PID), a follow-up recalls the word; same native id or a lossless resume.',
             'Word recalled AND identity/history preserved.', run_restart_resume),
        Cell('image-paste', 'image-paste', 'live', [1], 200,
             P('What are the three vertical colour stripes in this image, left to right? Reply with just the three colour names.\n\n[Screenshot: <uploaded path>]'),
             'Upload persists on disk; the reply names red, green, blue in order (fixture is a generated 3-stripe PNG).',
             'Upload ok AND correct ordered colours AND file still present.', run_image_paste),
        Cell('notify', 'notify', 'live', [1, 1], 200, P('Reply with exactly the word READY.', M + ' (child, dispatched with notify_session=<parent>)'),
             'Exactly one "[dispatched agent finished]" receipt in the parent after the instance was restarted between parent idle and child finish.',
             'receipts == 1 AND parent reached a terminal state.', run_notify),
        Cell('workflow', 'workflow', 'live', [1, 1], 200,
             P('step first: ' + M, 'step second: The previous step said: {{prev.output}}. Reply with exactly that text, then a space, then <A2><B2>.',
               '(second workflow with a step naming a nonexistent project -> must report a real failed state; costs no tokens)'),
             'Both steps on the selected vendor; step 1 output appears exactly once in step 2 output; a deliberately broken workflow fails visibly.',
             'All functional checks true.', run_workflow),
        Cell('schedule', 'schedule', 'live', [1], 200, P(M + ' (via a one-shot schedule 45s out)'),
             'The once-schedule fires by itself, the run record exists, the session ran on the selected vendor and answered.',
             'Fired by timer AND engine AND record AND marker. A run-now fallback is recorded and fails the timer check.', run_schedule),
        Cell('memory', 'memory', 'live', [1], 200, P('(fact appended to project memory via API)', 'What is the deployment codename? Reply with just the codename.'),
             'A NEW chat retrieves the appended fact via the read-floor; MEMORY.md holds it once. The Scribe write leg is not exercised (Claude summarizer).',
             'Read-floor retrieves AND no duplicate. Scribe leg is reported UNVERIFIABLE, so the cell is at best INCONCLUSIVE unless --allow-scribe-claude is used live.',
             run_memory),
        Cell('hire', 'hire', 'live', [1], 200, P(M + ' (character global:live-persona, engine pinned to --vendor)'),
             'Hired agent resolves to the requested engine; session identity is the hired persona; conversation usable.',
             'All functional checks true; persona check applies.', run_hire),
        Cell('stop-interrupt', 'stop-interrupt', 'live', [2, 1], 200,
             P('Run this exact shell command and wait for it to finish, then report: <slow decoy, ~2 min>', M + ' (sent via interrupt)'),
             'Interrupt answers the new prompt; earlier history retained; stop gives status stopped and leaves zero owned child processes.',
             'All functional checks true.', run_stop_interrupt),
        Cell('questions', 'questions', 'live', [2], 200,
             P('Use the mc:question protocol to ask me ONE multiple-choice question with options A and B: which do I prefer? Then stop and wait for my answer.',
               'A. Now reply with the two strings <A> and <B> joined, nothing else.'),
             'A pending question is surfaced; answering it resumes the same turn.', 'Pending question seen AND resumed with marker.', run_questions),
        Cell('mcp', 'mcp', 'live', [2], 200,
             P('Call the MCP tool named fixture_token (server clayrune_fixture) and reply with exactly the string it returns.'),
             'The fixture stdio MCP server\'s token appears in the reply AND a tool-call record appears in the transcript.',
             'Token in reply AND a tool receipt beyond the reply text. A missing MCP capability is a FAIL, not N/A (vendor-agnostic position).', run_mcp),
        Cell('usage', 'usage', 'live', [], 0, P('(reads usage of the sessions created above; no prompt)'),
             'Token counts come from vendor-exposed usage, marked measured/estimated/unavailable; any non-zero cost carries provenance.',
             'No unprovenanced cost; unavailable is reported as unavailable, not zero.', run_usage),
        Cell('allowance', 'allowance', 'live', [], 0, P('(reads /api/agent/providers; exhaustion cannot be induced)'),
             'If a usage limit was hit during the run: named vendor + reset shown in the providers entry. If not hit, this cell is INCONCLUSIVE by design.',
             'Only observed exhaustion counts; a synthetic fixture does not.', run_allowance),
        Cell('cross-dispatch', 'cross-dispatch', 'cross', [1, 1, 1, 1, 1, 1], 200, P('<one parent + one child per destination vendor>'),
             'For each destination: child on that vendor answers; parent notified exactly once.', 'All functional checks true per destination.',
             run_cross_dispatch, other_vendor=True),
        Cell('mixed-workflow', 'mixed-workflow', 'cross', [], 0, P('(not scripted: see runner note)'),
             'A workflow whose steps run on different vendors.', 'Not machine-runnable yet: reported UNVERIFIABLE.', run_mixed_workflow, other_vendor=True),
        Cell('handoff', 'handoff', 'cross', [1, 1, 1, 1, 1, 1], 200, P('Remember the word PINEAPPLE. Reply with exactly ACK.', 'What word did I ask you to remember? (cross_provider_handoff to <dest>)'),
             'For each destination: a fresh conversation there carries the source conversation\'s history.', 'History word recalled on the destination vendor.',
             run_handoff, other_vendor=True),
    ]


CELLS = mk_cells()


# ── evidence ────────────────────────────────────────────────────────────────

def calls_for(ctx: Ctx, run: CellRun) -> Tuple[List[G.Call], int, int]:
    """Per-call usage. Native transcript first (measured); else the polled
    snapshots (estimated, may miss calls); else one 'unavailable' record."""
    calls: List[G.Call] = []
    retries = dup = 0
    for proj, sid in run.sessions:
        s = run.final.get(sid) or {}
        native: List[G.Call] = []
        try:
            text = read_native_transcript(ctx, s)
            if text:
                native = G.ingest_native_usage(ctx.vendor, text, sid)
        except Exception as e:  # never let evidence gathering break the run
            print(f'  ! native transcript read failed for {sid}: {e}', file=sys.stderr)
        if native:
            calls += native
        else:
            seen = set()
            i = 0
            for smp in run.samples.get(sid, []):
                ct = smp.get('context_tokens')
                if ct is None or (smp.get('num_turns'), ct) in seen:
                    continue
                seen.add((smp.get('num_turns'), ct))
                u = smp.get('usage') or {}
                calls.append(G.Call(sid, i, None, u.get('output_tokens') if isinstance(u.get('output_tokens'), int) else None,
                                    None, None, G.ESTIMATED, 'polled-status'))
                calls[-1].input = ct
                i += 1
            if i == 0:
                calls.append(G.Call(sid, 0, provenance=G.UNAVAILABLE, source='no usage exposed'))
        replies, events = split_turns(s.get('log_lines') or [])
        retries += sum(1 for e in events if re.search(r'retry|retrying', e, re.I))
        dup += sum(1 for x, y in zip(replies, replies[1:]) if x.strip() == y.strip())
        rolled = any(re.search(r'auto-fresh|rolled over|rollover', l, re.I) for l in (s.get('log_lines') or []))
        for c in calls:
            if c.session_id == sid and rolled:
                c.rollover_logged = True
    return calls, retries, dup


def read_native_transcript(ctx: Ctx, s: dict) -> str:
    home = getattr(ctx.inst, 'home', None)
    if not home:
        return ''
    if ctx.vendor == 'codex' and s.get('provider_session_id'):
        hits = list((Path(home) / '.codex' / 'sessions').glob(f"*/*/*/rollout-*-{s['provider_session_id']}.jsonl"))
        return hits[0].read_text(encoding='utf-8', errors='replace') if hits else ''
    if ctx.vendor == 'claude' and s.get('claude_session_id'):
        hits = list((Path(home) / '.claude' / 'projects').glob(f"*/{s['claude_session_id']}.jsonl"))
        return hits[0].read_text(encoding='utf-8', errors='replace') if hits else ''
    if ctx.vendor == 'gemini' and s.get('provider_session_id'):
        # gemini-cli's ChatRecordingService: <home>/.gemini/tmp/<project>/chats/
        # session-<ts>-<sid[:8]>.jsonl (node's homedir() is the isolated
        # USERPROFILE/HOME). A resume may open a second file with the same
        # suffix; keep only files whose metadata line names THIS session.
        sid = s['provider_session_id']
        texts = []
        for f in sorted((Path(home) / '.gemini' / 'tmp').glob(f'*/chats/session-*-{sid[:8]}.jsonl'),
                        key=lambda x: x.stat().st_mtime):
            text = f.read_text(encoding='utf-8', errors='replace')
            try:
                meta = json.loads(text.split('\n', 1)[0] or '{}')
            except ValueError:
                continue
            if isinstance(meta, dict) and meta.get('sessionId') == sid:
                texts.append(text)
        return '\n'.join(texts)
    return ''


def finalize(ctx: Ctx, cell: Cell, run: CellRun) -> Tuple[str, dict, dict]:
    if run.manual_reason:
        return 'MANUAL', {}, {}
    if run.skipped_reason:
        return 'SKIPPED', {}, {}
    if not run.sessions:  # usage / allowance: read-only cells, nothing to meter
        na = [G.check('token_block', G.NA, 'cell sends no prompt')]
        token = {'provenance': [G.UNAVAILABLE], 'calls': 0, 'first_turn_tokens': [], 'per_call_median': None,
                 'per_call_max': None, 'input': None, 'output': None, 'cache_read': None, 'cache_write': None,
                 'skills_declared': 0, 'skill_listing_scoped': None, 'skill_listing_unscoped': None,
                 'retries': 0, 'duplicate_turns': 0, 'checks': na, 'verdict': G.NA}
        align = {'checks': [G.check('alignment_block', G.NA, 'cell sends no prompt')], 'verdict': G.NA}
        func_fail = any(c['verdict'] == G.FAIL for c in run.checks)
        status = G.cell_status(not func_fail, token, align)
        if status == 'PASS' and any(c['verdict'] == G.UNVERIFIABLE for c in run.checks):
            status = 'INCONCLUSIVE'
        return status, token, align
    calls, retries, dup = calls_for(ctx, run)
    skills = 0
    token = G.token_report(calls, retries=retries, duplicate_turns=dup, skills_declared=skills)
    texts: List[str] = []
    events: List[str] = []
    obs = []
    for _p, sid in run.sessions:
        s = run.final.get(sid) or {}
        r, e = split_turns(s.get('log_lines') or [])
        texts += r
        events += e
        obs.append({'provider': s.get('provider'), 'model': s.get('model') or s.get('agent_model'),
                    'observed_model': s.get('observed_model')})
    allowance = ''
    try:
        allowance = next((p.get('allowance_exhausted') or '' for p in ctx.api.providers() if p.get('name') == ctx.vendor), '')
    except Exception:
        pass
    persona_obs = next(((run.final.get(sid) or {}).get('character') for _p, sid in run.sessions
                        if (run.final.get(sid) or {}).get('character')), None)
    align = G.alignment_report(expected_persona=run.expected_persona, observed_persona=persona_obs,
                               assistant_texts=texts, requested_vendor=ctx.vendor if cell.kind != 'cross' else ctx.vendor,
                               requested_model=ctx.model, observations=obs if cell.kind != 'cross' else
                               [o for o in obs if o['provider'] == ctx.vendor],
                               claims=run.claims, artifacts=run.artifacts, event_lines=events,
                               allowance_display=allowance, blocked=run.blocked, tool_events=None,
                               depth_asked=run.depth_asked)
    func_fail = any(c['verdict'] == G.FAIL for c in run.checks)
    func_unver = any(c['verdict'] == G.UNVERIFIABLE for c in run.checks)
    status = G.cell_status(not func_fail, token, align)
    if status == 'PASS' and func_unver:
        status = 'INCONCLUSIVE'
    return status, token, align


def write_evidence(ctx: Ctx, cell: Cell, run: CellRun, status: str, token: dict, align: dict,
                   extra_note: str = '') -> Path:
    path = ctx.journal / f'{cell.id}.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    san = lambda t: sanitize(str(t), str(getattr(ctx.inst, 'home', '') or ''))  # noqa: E731
    L = [f'# {ctx.vendor} / {cell.id}: {status}', '',
         f'- run id `{ctx.run_id}`, {now_iso()}, driver `tools/provider-live/codex_run.py`, plan flow `{cell.flow}`',
         f'- kind `{cell.kind}`; requested model `{ctx.model or "(vendor default)"}`, effort `{ctx.effort or "(default)"}`',
         f'- instance: disposable second instance on 127.0.0.1:{ctx.args.port}, MC_REMOTE_ENABLED=0, isolated home + data dir',
         '', '## Exact prompts', '']
    L += [f'{i}. ```\n{p}\n   ```' for i, p in enumerate(cell.prompts(ctx), 1)]
    L += ['', '## Expected evidence', cell.expected, '', '## Pass rule', cell.pass_rule, '']
    if extra_note:
        L += ['## Note', extra_note, '']
    if run.manual_reason:
        L += ['## MANUAL', run.manual_reason, '']
    L += ['## Steps', *[f'- {san(s)}' for s in run.steps], '', '## Functional checks', '']
    L += [f"- **{c['verdict']}** {c['name']}: {san(c['detail'])}" for c in run.checks] or ['- (none)']
    if token:
        L += ['', '## Token block', f"- provenance: {', '.join(token['provenance'])}; calls: {token['calls']}",
              f"- first-turn tokens: {token['first_turn_tokens']} (limit {G.FIRST_TURN_LIMIT})",
              f"- per-call median / max: {token['per_call_median']} / {token['per_call_max']} (rollover {G.ROLLOVER_TOKENS})",
              f"- input {token['input']}, output {token['output']}, cache-read {token['cache_read']}, cache-write {token['cache_write']}",
              f"- skills declared {token['skills_declared']}; listing scoped/unscoped {token['skill_listing_scoped']}/{token['skill_listing_unscoped']}",
              f"- retries {token['retries']}, duplicate turns {token['duplicate_turns']}", '']
        L += [f"- **{c['verdict']}** {c['name']}: {c['detail']}" for c in token['checks']]
    if align:
        L += ['', '## Alignment block']
        L += [f"- **{c['verdict']}** {c['name']}: {san(c['detail'])}" for c in align['checks']]
        excerpt = next((t for t in [san(a) for a in run.artifacts.values()] if t), '')
        L += ['', '### Transcript excerpt (voice is not machine-checked; read this)', '```', excerpt[:600], '```']
    L += ['', '## Trace (sanitized, last 12 calls)', '```']
    for t in ctx.api.trace[-12:] if ctx.api else []:
        L.append(san(json.dumps({k: t[k] for k in ('method', 'path', 'status')}) + ' ' + json.dumps(t.get('response'))[:300]))
    L += ['```', '', f'Result: **{status}**']
    path.write_text('\n'.join(L) + '\n', encoding='utf-8')
    return path


# ── dry run ─────────────────────────────────────────────────────────────────

def selected(args) -> List[Cell]:
    cells = list(CELLS)
    if args.only:
        wanted = set(args.only.split(','))
        cells = [c for c in cells if c.id in wanted]
    return cells


def dry_run(args) -> int:
    ctx = Ctx(args, None, None, None, Path(args.journal_dir or ''), 'DRYRUN')
    print(f'PROVIDER LIVE PASS: vendor={args.vendor}  (DRY RUN: no network, no spawn, no write)')
    print(f'model={args.model or "<MODEL> (required for a live run)"}  effort={args.effort or "(default)"}  port={args.port}')
    print(f'evidence dir: {ctx.journal}\n')
    print(f'Token ESTIMATE model: first call {EST_FLOOR:,} (plan baseline) + {EST_GROWTH:,}/extra call; gross context tokens '
          f'processed, NOT billed tokens, NOT a price. Hard limits: first turn <= {G.FIRST_TURN_LIMIT:,}; no call > {G.ROLLOVER_TOKENS:,} without a logged rollover.\n')
    tot_in = tot_out = tot_calls = tot_x_in = tot_x_out = 0
    print(f'{"#":>2} {"cell":<24}{"kind":<7}{"sess":>5}{"calls":>6}{"est input":>12}{"est output":>12}  note')
    for n, c in enumerate(selected(args), 1):
        e = c.est()
        skip = c.kind == 'cross' and not args.cross_vendor
        note = ('SKIPPED unless --cross-vendor (spends other vendors\' allowance)' if skip
                else 'MANUAL (clean VM)' if c.kind == 'manual' else '')
        if c.other_vendor and args.cross_vendor:
            note = 'counts against OTHER vendors as well'
        print(f'{n:>2} {c.id:<24}{c.kind:<7}{e["sessions"]:>5}{e["calls"]:>6}{e["input"]:>12,}{e["output"]:>12,}  {note}')
        if not skip and c.kind != 'manual':
            (tot_x_in, tot_x_out) = (tot_x_in + e['input'], tot_x_out + e['output']) if c.other_vendor else (tot_x_in, tot_x_out)
            tot_in += e['input']; tot_out += e['output']; tot_calls += e['calls']
    print(f'\nTOTAL (cells that will run): {tot_calls} model calls, ~{tot_in:,} context tokens in, ~{tot_out:,} out')
    print('These are estimates only; the run records MEASURED usage per cell and flags anything unavailable.\n')
    for n, c in enumerate(selected(args), 1):
        print(f'--- [{n}] {c.id}  ({c.kind}) ---')
        for i, p in enumerate(c.prompts(ctx), 1):
            print(f'  prompt {i}: {p}')
        print(f'  expected: {c.expected}\n  pass rule: {c.pass_rule}\n  evidence: {ctx.journal / (c.id + ".md")}')
    return 0


# ── main ────────────────────────────────────────────────────────────────────

def parse(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--vendor', choices=VENDORS, required=True)
    ap.add_argument('--model', default='')
    ap.add_argument('--effort', default='')
    ap.add_argument('--port', type=int, default=5391)
    ap.add_argument('--journal-dir', default='')
    ap.add_argument('--auth-file', default='')
    ap.add_argument('--only', default='', help='comma list of cell ids (keeps plan order)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--cross-vendor', action='store_true', help='also run cross-vendor cells (spends other vendors)')
    ap.add_argument('--cross-dest', nargs='*', choices=VENDORS)
    ap.add_argument('--allow-scribe-claude', action='store_true')
    ap.add_argument('--keep-home', action='store_true')
    a = ap.parse_args(argv)
    if not a.journal_dir:
        a.journal_dir = str(REPO_ROOT / 'docs' / '_journal' / 'provider-live' / a.vendor)
    return a


def run_all(args, ctx: Ctx) -> Tuple[int, Dict[str, str]]:
    results: Dict[str, str] = {}
    stopped: Optional[str] = None
    ctx.notes['all_sessions'] = []
    for cell in selected(args):
        if stopped:
            results[cell.id] = 'NOT-RUN'
            (ctx.journal / f'{cell.id}.md').write_text(
                f'# {ctx.vendor} / {cell.id}: NOT-RUN\n\nRun stopped earlier on a usage limit; see STOPPED-usage-limit.md. '
                f'No other vendor or model was tried.\n', encoding='utf-8')
            continue
        run = CellRun()
        if cell.kind == 'cross' and not args.cross_vendor:
            run.skipped_reason = 'cross-vendor cells spend the other vendors\' allowance; pass --cross-vendor'
        note = ''
        try:
            if not run.skipped_reason and cell.runner:
                cell.runner(ctx, run)
        except AllowanceStop as e:
            stopped = str(e)
            ctx.notes['limit_seen'] = True
            status, token, align = 'BLOCKED-USAGE-LIMIT', {}, {}
            note = f'STOPPED at the first usage limit; no other vendor or model was tried.\n\n    {sanitize(stopped)}'
            (ctx.journal).mkdir(parents=True, exist_ok=True)
            (ctx.journal / 'STOPPED-usage-limit.md').write_text(
                f'# {ctx.vendor} run stopped on a usage limit\n\n- cell `{cell.id}`, {now_iso()}\n- captured text: `{sanitize(stopped)}`\n'
                f'- no fallback was attempted; remaining cells NOT-RUN.\n', encoding='utf-8')
            results[cell.id] = status
            write_evidence(ctx, cell, run, status, token, align, note)
            ctx.notes['all_sessions'] += run.sessions
            continue
        except Exception as e:
            note = f'ERROR: {type(e).__name__}: {sanitize(str(e))[:400]}'
            recover = getattr(ctx.inst, 'recover', None)
            if recover:  # one bad cell (or a failed restart) must not leave a dead instance for every cell after it
                try:
                    note += f'\n\n    instance {sanitize(recover())}'
                except Exception as e2:
                    note += f'\n\n    instance recovery FAILED: {type(e2).__name__}: {sanitize(str(e2))[:200]}'
            run.checks.append(G.check('runner_completed', G.FAIL, note))
        status, token, align = finalize(ctx, cell, run) if 'ERROR' not in note else ('ERROR', {}, {})
        ctx.notes['all_sessions'] += run.sessions
        results[cell.id] = status
        write_evidence(ctx, cell, run, status, token, align, note)
        print(f'  [{status}] {cell.id}')
    if stopped:
        return 3, results
    if any(v in ('FAIL', 'ERROR') for v in results.values()):
        return 1, results
    if any(v in ('MANUAL', 'INCONCLUSIVE', 'SKIPPED') for v in results.values()):
        return 4, results
    return 0, results


def preflight(ctx: Ctx) -> None:
    """Refuse to spend anything if the vendor is already exhausted or unusable."""
    prov = [p for p in ctx.api.providers(refresh=True) if p.get('name') == ctx.vendor]
    if not prov or not prov[0].get('installed'):
        raise RuntimeError(f'{ctx.vendor} CLI not installed in the disposable instance')
    if prov[0].get('auth_status') not in ('ok', 'logged_in', 'authenticated', 'signed_in'):
        raise RuntimeError(f"{ctx.vendor} not signed in: auth_status={prov[0].get('auth_status')}")
    if prov[0].get('allowance_exhausted'):
        raise AllowanceStop(f"preflight: {prov[0]['allowance_exhausted']}")
    ctx.notes['version'] = prov[0].get('version')
    if ctx.project_dir:
        code, r = ctx.api.request('POST', f'/api/project/{ctx.project}',
                                  {'name': 'Live pass', 'project_path': str(ctx.project_dir), 'provider': ctx.vendor}, human=True)
        if code >= 300:
            raise RuntimeError(f'could not create the test project: {code} {r}')


def main(argv=None) -> int:
    args = parse(argv)
    if args.dry_run:
        return dry_run(args)
    if args.port == PROD_PORT:
        print('refusing port 5199', file=sys.stderr)
        return 2
    if not args.model:
        print('--model is required for a live run (the follow-up cell checks the EXACT requested model)', file=sys.stderr)
        return 2
    run_id = datetime.now().strftime('%m%d%H%M%S')
    journal = Path(args.journal_dir)
    journal.mkdir(parents=True, exist_ok=True)
    inst = Instance(args.vendor, args.port, journal, Path(args.auth_file) if args.auth_file else None)
    procs = Procs(inst.home / 'decoys')
    api = Api(args.port, args.vendor, cross_ok=args.cross_vendor)
    ctx = Ctx(args, api, inst, procs, journal, run_id)
    code = 2
    try:
        inst.start()
        ctx.project_dir = inst.home / 'workspace'
        ctx.project_dir.mkdir(parents=True, exist_ok=True)
        try:
            preflight(ctx)
        except AllowanceStop as e:
            (journal / 'STOPPED-usage-limit.md').write_text(f'# preflight: {args.vendor} already out of allowance\n\n`{sanitize(str(e))}`\n', encoding='utf-8')
            print(f'STOPPED at preflight: {e}')
            return 3
        code, results = run_all(args, ctx)
        for k, v in results.items():
            print(f'{v:<22}{k}')
    finally:
        procs.cleanup()
        note = inst.stop()
        home = inst.cleanup() if not args.keep_home else 'isolated home kept (--keep-home; contains a credential copy)'
        (journal / '_cleanup.md').write_text(f'# cleanup {now_iso()}\n\n- instance: {note}\n- {home}\n- port 5199 never touched\n', encoding='utf-8')
    return code


if __name__ == '__main__':
    sys.exit(main())

"""In-place self-update for the frozen macOS Clayrune.app (MC-1026).

A frozen .app has no .git, so the git-pull update path never applied and the
Update button could only open a download link. This module downloads the
release zip, VERIFIES it, and hands the swap to a detached shell helper that
outlives the app.

Nothing here replaces the running bundle until every gate has passed:

  1. ``codesign --verify --deep --strict`` on the extracted bundle
  2. ``spctl -a -t exec -vv``: accepted AND source "Notarized Developer ID"
  3. TeamIdentifier of the new bundle == TeamIdentifier of the RUNNING app
  4. CFBundleIdentifier of the new bundle == the running app's

A running app that is itself unsigned (dev build, ad-hoc) has no TeamIdentifier
to compare against, so the update aborts: with nothing to anchor trust to, the
only safe answer is to keep the old app and fall back to the download link.

The swap helper is a generated /bin/sh script written into the staging dir at
run time (no new file for build-macos.spec to bundle). It waits for the app PID
to exit, renames the old bundle aside, moves the new one in, `open`s it, and
waits for /api/system/update/status to report the verified commit. If it does not in time it puts
the old bundle back and reopens it. The aside copy is deleted only after the new
app answers.

Every function here is human-gated by the caller (system_routes.system_update);
this module never decides who may trigger an update.
"""
import hashlib
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional

# Job states. `idle`/`failed` accept a new job; everything else is in flight.
IDLE = 'idle'
DOWNLOADING = 'downloading'
EXTRACTING = 'extracting'
VERIFYING = 'verifying'
RESTARTING = 'restarting'
FAILED = 'failed'
_ACTIVE = (DOWNLOADING, EXTRACTING, VERIFYING, RESTARTING)

# Fallback reasons (why we hand back the download link instead of updating).
NOT_FROZEN_MAC = 'not_frozen_mac'
NO_BUNDLE = 'no_bundle'
TRANSLOCATED = 'translocated'
PARENT_NOT_WRITABLE = 'parent_not_writable'
GITHUB_UNREACHABLE = 'github_unreachable'

FALLBACK_MESSAGES = {
    NOT_FROZEN_MAC: 'In-place update is only available for the packaged Mac app.',
    NO_BUNDLE: 'Could not locate the running Clayrune.app bundle.',
    TRANSLOCATED: ('Clayrune is running from a temporary macOS location (App '
                   'Translocation), so it cannot replace itself. Move Clayrune to '
                   'Applications and reopen it, or download the new build.'),
    PARENT_NOT_WRITABLE: ('Clayrune cannot write to the folder it is installed in. '
                          'Download the new build and replace the app yourself.'),
    GITHUB_UNREACHABLE: 'Could not reach GitHub to fetch the update.',
}

_MAX_ZIP_BYTES = 1_500_000_000  # sanity cap when the manifest gives no size
_HEALTH_PATH = '/api/system/update/status'
_JOB_LOCK = threading.Lock()
_JOB: dict = {'state': IDLE}


class UpdateAbort(Exception):
    """A gate refused the update. The message is shown to the user verbatim."""


# ── environment probes ──────────────────────────────────────────────────────

def is_frozen_mac() -> bool:
    return sys.platform == 'darwin' and bool(getattr(sys, 'frozen', False))


def running_bundle() -> Optional[Path]:
    """The .app directory the running executable lives in, or None.

    Walks up from sys.executable (…/Clayrune.app/Contents/MacOS/Clayrune) to
    the first component ending in `.app`. Uses sys.executable, not _MEIPASS:
    PyInstaller moves _MEIPASS between Contents/Frameworks and Contents/Resources
    across versions; the executable's own location does not move.
    """
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == '.app':
            return parent
    return None


def in_place_blocker(bundle: Optional[Path] = None) -> Optional[str]:
    """Why this install cannot replace itself, or None if it can.

    The checks, in order: frozen macOS build; a locatable .app; not under App
    Translocation (a quarantined app launched from Downloads runs from a
    read-only randomized mount, and replacing that path replaces nothing the
    user will ever open again); the bundle's parent folder is writable (the
    swap is a rename inside it, and a same-volume rename is what makes it
    atomic).
    """
    if not is_frozen_mac():
        return NOT_FROZEN_MAC
    bundle = bundle if bundle is not None else running_bundle()
    if bundle is None:
        return NO_BUNDLE
    if '/AppTranslocation/' in bundle.as_posix():
        return TRANSLOCATED
    parent = bundle.parent
    if not (os.access(parent, os.W_OK | os.X_OK) and os.access(bundle, os.W_OK)):
        return PARENT_NOT_WRITABLE
    return None


# ── verification gates ──────────────────────────────────────────────────────

def _run(cmd, timeout=180):
    """Run a command, return (rc, stdout+stderr). Patched in tests."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, encoding='utf-8', errors='replace')
        return r.returncode, ((r.stdout or '') + (r.stderr or '')).strip()
    except FileNotFoundError:
        return -1, f'{cmd[0]} not found'
    except subprocess.TimeoutExpired:
        return -2, f'{cmd[0]} timed out'
    except Exception as e:
        return -3, str(e)


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in (text or '').splitlines() if ln.strip()]
    return lines[-1] if lines else ''


def team_identifier(app: Path) -> Optional[str]:
    """TeamIdentifier from `codesign -dv --verbose=4`, or None if unsigned /
    ad-hoc ("not set") / unreadable. codesign prints this to stderr."""
    rc, out = _run(['codesign', '-dv', '--verbose=4', str(app)])
    if rc != 0:
        return None
    m = re.search(r'^TeamIdentifier=(.+)$', out, re.MULTILINE)
    if not m:
        return None
    team = m.group(1).strip()
    return None if team in ('', 'not set') else team


def bundle_identifier(app: Path) -> Optional[str]:
    try:
        with open(app / 'Contents' / 'Info.plist', 'rb') as f:
            info = plistlib.load(f)
        return info.get('CFBundleIdentifier') or None
    except Exception:
        return None


def bundle_executable(app: Path) -> str:
    """CFBundleExecutable of a bundle ('Clayrune' if unreadable)."""
    try:
        with open(app / 'Contents' / 'Info.plist', 'rb') as f:
            return plistlib.load(f).get('CFBundleExecutable') or 'Clayrune'
    except Exception:
        return 'Clayrune'


_COMMIT_RE = re.compile(r'[0-9a-f]{7,40}')


def require_release_commit(release: dict) -> str:
    """The commit the verified release was built from, or UpdateAbort. The swap
    helper only calls the new app "up" when it reports this commit."""
    commit = str(release.get('remote_commit') or release.get('commit') or '').strip().lower()
    if not _COMMIT_RE.fullmatch(commit):
        raise UpdateAbort('The release does not say which build it is, so the update '
                          'could not be confirmed afterwards. Nothing was changed.')
    return commit


def require_running_team(running_app: Path) -> str:
    """The running app's TeamIdentifier, or UpdateAbort. Also called BEFORE the
    download so an unsigned dev build fails fast instead of after ~100 MB."""
    team = team_identifier(running_app)
    if not team:
        raise UpdateAbort('The running Clayrune is not Developer-ID signed, so there '
                          'is no Team ID to check the new build against.')
    return team


def verify_bundle(new_app: Path, running_app: Path) -> dict:
    """Run every gate against an extracted bundle. Raises UpdateAbort naming the
    first one that fails; returns the facts it established on success."""
    rc, out = _run(['codesign', '--verify', '--deep', '--strict', '--verbose=2', str(new_app)])
    if rc != 0:
        raise UpdateAbort('The downloaded app failed code-signature verification '
                          f'({_last_line(out) or "codesign rc=%s" % rc}).')

    rc, out = _run(['spctl', '-a', '-t', 'exec', '-vv', str(new_app)])
    if rc != 0 or 'accepted' not in out:
        raise UpdateAbort('macOS Gatekeeper rejected the downloaded app '
                          f'({_last_line(out) or "spctl rc=%s" % rc}).')
    if 'Notarized Developer ID' not in out:
        raise UpdateAbort('The downloaded app is signed but not notarized by Apple '
                          f'({_last_line(out)}).')

    running_team = require_running_team(running_app)
    new_team = team_identifier(new_app)
    if not new_team:
        raise UpdateAbort('The downloaded app carries no Team ID.')
    if new_team != running_team:
        raise UpdateAbort(f'The downloaded app is signed by a different team '
                          f'({new_team}) than the one running ({running_team}).')

    running_id = bundle_identifier(running_app)
    new_id = bundle_identifier(new_app)
    if not new_id or new_id != running_id:
        raise UpdateAbort(f'The downloaded app has a different bundle identifier '
                          f'({new_id or "none"}) than the one running ({running_id or "none"}).')

    return {'team': new_team, 'bundle_id': new_id}


# ── job state ───────────────────────────────────────────────────────────────

def _set(**fields):
    with _JOB_LOCK:
        _JOB.update(fields)
        _JOB['updated_at'] = time.time()


def job_snapshot() -> dict:
    with _JOB_LOCK:
        return dict(_JOB)


def _reset_for_tests():
    with _JOB_LOCK:
        _JOB.clear()
        _JOB['state'] = IDLE


def result_path_for(data_dir: Path) -> Path:
    return Path(data_dir) / 'mac_update_result.json'


def log_path_for(data_dir: Path) -> Path:
    return Path(data_dir) / 'logs' / 'mac-update.log'


def read_last_result(data_dir: Optional[Path]) -> Optional[dict]:
    """What the swap helper wrote last time (updated / rolled_back / aborted).
    Lives in <data_root>/data, NOT data/projects (DATA_DIR pollution rule)."""
    if not data_dir:
        return None
    try:
        return json.loads(result_path_for(data_dir).read_text(encoding='utf-8'))
    except Exception:
        return None


# ── download + extract ──────────────────────────────────────────────────────

def _download(url: str, dest: Path, expected_size, expected_sha256: str, log) -> None:
    if not url.startswith('https://'):
        raise UpdateAbort('Refusing to download the update over a non-HTTPS URL.')
    req = urllib.request.Request(url, headers={'User-Agent': 'Clayrune-self-update'})
    h = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, 'wb') as out:
        total = expected_size or int(resp.headers.get('Content-Length') or 0) or None
        _set(bytes_done=0, bytes_total=total)
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if done > (expected_size or _MAX_ZIP_BYTES):
                raise UpdateAbort('The download is larger than expected; aborting.')
            _set(bytes_done=done)
    if expected_size and done != int(expected_size):
        raise UpdateAbort(f'The download is incomplete ({done} of {expected_size} bytes).')
    if expected_sha256 and h.hexdigest().lower() != expected_sha256.strip().lower():
        raise UpdateAbort('The download does not match the published checksum.')
    log(f'[mac-update] downloaded {done} bytes, sha256 '
        f'{"verified" if expected_sha256 else "NOT published (skipped)"}')


def _extract(zip_path: Path, into: Path) -> Path:
    into.mkdir(parents=True, exist_ok=True)
    rc, out = _run(['ditto', '-x', '-k', str(zip_path), str(into)], timeout=300)
    if rc != 0:
        raise UpdateAbort(f'Could not unpack the download ({_last_line(out)}).')
    apps = [p for p in into.iterdir() if p.suffix == '.app' and p.is_dir()]
    if len(apps) != 1:
        raise UpdateAbort(f'The download contains {len(apps)} apps, expected exactly 1.')
    return apps[0]


# ── swap helper ─────────────────────────────────────────────────────────────

def build_swap_script(*, bundle: Path, new_app: Path, aside: Path, stage: Path,
                      pid: int, port: int, log_path: Path, result_path: Path,
                      expect_commit: str, exe_name: str = 'Clayrune',
                      wait_exit_s: int = 90, wait_up_s: int = 60) -> str:
    """The detached helper. Every path is shlex-quoted; deletion targets are
    checked non-empty and inside the bundle's parent folder before use.

    "New app is up" means the process answering on the port reports
    `expect_commit` (the release verified earlier): a bare HTTP 200 would pass
    for any other server holding the port. The rollback stops only processes
    whose executable path equals the bundle's, by exact string compare (no
    regex over command lines), and leaves the backup alone if it cannot first
    move the failed app out of the way."""
    if not _COMMIT_RE.fullmatch(expect_commit or ''):
        raise ValueError('expect_commit must be 7-40 lowercase hex chars')

    def q(path):  # posix form: identical on macOS, and lets the script run under Git Bash in tests
        return shlex.quote(Path(path).as_posix())
    return f'''#!/bin/sh
# Clayrune in-place update helper (MC-1026). Generated; safe to delete.
OLD={q(bundle)}
NEW={q(new_app)}
ASIDE={q(aside)}
STAGE={q(stage)}
PARENT={q(bundle.parent)}
PID={int(pid)}
URL="http://127.0.0.1:{int(port)}{_HEALTH_PATH}"
EXPECT={shlex.quote(expect_commit)}
EXE="$OLD/Contents/MacOS/"{shlex.quote(exe_name)}
LOG={q(log_path)}
RESULT={q(result_path)}
STAMP="$(date +%s)"

log() {{ printf '%s %s\\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }}
result() {{ printf '{{"status":"%s","reason":"%s","ts":%s}}\\n' "$1" "$2" "$STAMP" > "$RESULT"; }}
safe_rm() {{
  case "$1" in
    "$PARENT"/?*) rm -rf "$1" ;;
    *) log "refusing to delete $1" ;;
  esac
}}
# $1 = commit the process on the port reported; true if it is the verified build.
commit_matches() {{
  [ -n "$1" ] || return 1
  case "$EXPECT" in "$1"*) return 0 ;; esac
  case "$1" in "$EXPECT"*) return 0 ;; esac
  return 1
}}
# Stop only processes whose executable path is exactly $EXE: TERM, wait, then
# KILL the same pids. `ps comm=` prints the full path; compare it as a string.
stop_new_app() {{
  pids="$(ps -axo pid=,comm= 2>/dev/null | while read -r p c; do
    [ "$c" = "$EXE" ] && printf '%s\\n' "$p"
  done)"
  [ -n "$pids" ] || return 0
  for p in $pids; do kill -TERM "$p" 2>/dev/null; done
  k=0
  while [ "$k" -lt 20 ]; do
    alive=""
    for p in $pids; do kill -0 "$p" 2>/dev/null && alive="$alive $p"; done
    [ -z "$alive" ] && return 0
    k=$((k+1))
    sleep 0.5
  done
  for p in $alive; do kill -KILL "$p" 2>/dev/null; done
  return 0
}}

log "helper started: waiting for app pid $PID to exit"
i=0
while kill -0 "$PID" 2>/dev/null; do
  i=$((i+1))
  if [ "$i" -gt {int(wait_exit_s) * 2} ]; then
    log "app did not exit; leaving the old app untouched"
    result aborted "the old app did not quit in time"
    safe_rm "$STAGE"
    exit 1
  fi
  sleep 0.5
done

log "app exited; swapping bundles"
if ! mv "$OLD" "$ASIDE"; then
  log "could not move the old bundle aside; reopening it"
  result aborted "could not move the old app aside"
  safe_rm "$STAGE"
  open "$OLD"
  exit 1
fi
if ! mv "$NEW" "$OLD"; then
  log "could not move the new bundle in; restoring the old one"
  mv "$ASIDE" "$OLD"
  result aborted "could not move the new app into place"
  safe_rm "$STAGE"
  open "$OLD"
  exit 1
fi

open "$OLD"
log "opened new bundle; waiting for build $EXPECT at $URL"
END=$(( $(date +%s) + {int(wait_up_s)} ))
up=0
got=""
while [ "$(date +%s)" -lt "$END" ]; do
  body="$(curl -fs -m 20 "$URL" 2>/dev/null)"
  if [ $? -eq 0 ]; then
    got="$(printf '%s\\n' "$body" | sed -n 's/.*"commit": *"\\([^"]*\\)".*/\\1/p' | head -n 1)"
    if commit_matches "$got"; then up=1; break; fi
  fi
  sleep 1
done

if [ "$up" = 1 ]; then
  log "new app answered with build $got; update complete"
  result updated ""
  safe_rm "$ASIDE"
  safe_rm "$STAGE"
  exit 0
fi

log "new build did not answer in {int(wait_up_s)}s; rolling back"
stop_new_app
FAILED="$PARENT/.clayrune-failed-$STAMP"
if ! mv "$OLD" "$FAILED"; then
  log "ROLLBACK FAILED: could not move the new app out of the way; old bundle is still at $ASIDE, new at $OLD"
  result rollback_failed "restore the previous version from $ASIDE; the new app is still at $OLD"
  exit 2
fi
if mv "$ASIDE" "$OLD"; then
  log "old bundle restored"
  result rolled_back "the new version did not start, so the previous version was restored"
  safe_rm "$FAILED"
  safe_rm "$STAGE"
  open "$OLD"
  exit 1
fi
log "ROLLBACK FAILED: old bundle is at $ASIDE, new at $FAILED"
result rollback_failed "restore the previous version from $ASIDE; the new app is at $FAILED"
exit 2
'''


def _spawn_swap(script_path: Path, log_path: Path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logf = open(log_path, 'ab')
    try:
        return subprocess.Popen(['/bin/sh', str(script_path)], stdin=subprocess.DEVNULL,
                                stdout=logf, stderr=subprocess.STDOUT,
                                start_new_session=True, close_fds=True)
    finally:
        logf.close()


# ── the job ─────────────────────────────────────────────────────────────────

def start_job(*, release: dict, port: int, data_dir: Path, quit_fn: Callable[[], None],
              blockers_fn: Callable[[], bool], force: bool,
              log: Callable[[str], None] = print) -> tuple:
    """Kick off the background update. Returns (started, error_message).

    `release` carries download_url / release_tag / sha256 / size from the server's
    own fresh fetch of the GitHub release (never from the client body).
    `blockers_fn()` -> True when agent sessions are running; re-checked right
    before quitting so one that started mid-download is not killed unannounced.
    `quit_fn` is the app's own shutdown path (browser profiles close via
    Browser.close there, never a kill).
    """
    bundle = running_bundle()
    reason = in_place_blocker(bundle)
    if reason:
        return False, FALLBACK_MESSAGES[reason]
    with _JOB_LOCK:
        if _JOB.get('state') in _ACTIVE:
            return False, 'An update is already in progress.'
        _JOB.clear()
        _JOB.update({'state': DOWNLOADING, 'started_at': time.time(),
                     'release_tag': release.get('release_tag', ''),
                     'bytes_done': 0, 'bytes_total': None, 'reason': ''})
    threading.Thread(
        target=_run_job, daemon=True, name='mac-self-update',
        args=(bundle, release, port, Path(data_dir), quit_fn, blockers_fn, force, log),
    ).start()
    return True, ''


def _run_job(bundle, release, port, data_dir, quit_fn, blockers_fn, force, log):
    stamp = int(time.time())
    stage = bundle.parent / f'.clayrune-update-{stamp}'
    swap = None
    try:
        require_running_team(bundle)
        expect_commit = require_release_commit(release)
        stage.mkdir(mode=0o700)
        zip_path = stage / 'Clayrune-macOS.zip'
        _download(release.get('download_url') or '', zip_path,
                  release.get('size'), release.get('sha256') or '', log)

        _set(state=EXTRACTING)
        new_app = _extract(zip_path, stage / 'extracted')
        zip_path.unlink(missing_ok=True)

        _set(state=VERIFYING)
        facts = verify_bundle(new_app, bundle)
        log(f'[mac-update] verified: team {facts["team"]}, id {facts["bundle_id"]}')

        if blockers_fn() and not force:
            raise UpdateAbort('Agents started running during the download. Nothing was '
                              'changed; try again when they finish.')

        aside = bundle.parent / f'.{bundle.stem}-previous-{stamp}'
        script = stage / 'swap.sh'
        result_path = result_path_for(data_dir)
        log_path = log_path_for(data_dir)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(build_swap_script(
            bundle=bundle, new_app=new_app, aside=aside, stage=stage, pid=os.getpid(),
            port=port, log_path=log_path, result_path=result_path,
            expect_commit=expect_commit, exe_name=bundle_executable(new_app)), encoding='utf-8')
        script.chmod(0o700)

        _set(state=RESTARTING)
        swap = _spawn_swap(script, log_path)
        log(f'[mac-update] swap helper started (pid {swap.pid}); quitting for restart')
        quit_fn()
    except UpdateAbort as e:
        _fail(str(e), stage, swap, log)
    except Exception as e:
        _fail(f'Update failed: {e}', stage, swap, log)


def _fail(reason, stage, swap, log):
    log(f'[mac-update] aborted: {reason}')
    if swap is not None and swap.poll() is None:
        try:
            swap.terminate()  # our own child: the quit never happened
        except Exception:
            pass
    shutil.rmtree(stage, ignore_errors=True)
    _set(state=FAILED, reason=reason, finished_at=time.time())

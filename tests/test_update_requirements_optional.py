"""requirements-passkeys.txt is an OPTIONAL second file: its failure never fails the sync.

`pip install -r` is all-or-nothing and cbor2 (a Rust extension) has no wheel on
every platform we install on. Putting the passkey pins in requirements.txt
would abort a fresh install and mark the update sync failed, blocking every
other new dependency. These tests pin that the passkey file is installed as a
separate best-effort step (mc/update_requirements.py, installer/install.sh,
installer/install.ps1) and that requirements.txt does not carry the pins.
"""
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import update_requirements as ur  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_sync_state():
    def reset():
        ur.wait_for_sync(10)
        ur._state.update(status=ur.IDLE, trigger='', reason='', rc=None, detail='',
                         started_at=None, finished_at=None, optional={})
        ur._thread = None
    reset()
    yield
    reset()


class _Runner:
    """subprocess.run stand-in that answers per requirements file."""

    def __init__(self, rc_by_file=None, exc_by_file=None):
        self.calls = []
        self._rc = rc_by_file or {}
        self._exc = exc_by_file or {}

    def files(self):
        return [Path(c[-1]).name for c in self.calls]

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        name = Path(cmd[-1]).name
        if name in self._exc:
            raise self._exc[name]
        rc = self._rc.get(name, 0)
        return SimpleNamespace(returncode=rc, stdout='', stderr='ERROR: no matching wheel' if rc else 'ok')


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / 'checkout'
    r.mkdir()
    (r / 'requirements.txt').write_text('flask\npsutil>=5.9.0\n')
    (r / 'requirements-passkeys.txt').write_text('webauthn==3.0.1\ncbor2==6.1.5\n')
    return r


@pytest.fixture()
def stamp(tmp_path):
    return tmp_path / 'home' / 'requirements_installed.sha256'


def _go(repo, stamp, runner, trigger='boot'):
    ur.start_background_sync(repo, trigger=trigger, runner=runner, stamp=stamp, frozen=False)
    ur.wait_for_sync(10)
    return ur.get_state()


def _pk_stamp(stamp):
    return stamp.with_name(ur.PASSKEYS_STAMP_FILE)


def test_failing_passkeys_install_does_not_fail_the_main_sync(repo, stamp):
    runner = _Runner(rc_by_file={'requirements-passkeys.txt': 1})
    state = _go(repo, stamp, runner)
    assert runner.files() == ['requirements.txt', 'requirements-passkeys.txt']
    assert state['status'] == ur.OK and state['rc'] == 0
    assert stamp.exists(), 'the main stamp must be written even though passkeys failed'
    assert not _pk_stamp(stamp).exists(), 'a failed passkey install must stay retryable'
    assert state['optional']['passkeys']['status'] == ur.FAILED
    assert state['optional']['passkeys']['rc'] == 1
    assert 'no matching wheel' in state['optional']['passkeys']['detail']


def test_passkeys_pip_that_raises_or_times_out_does_not_fail_the_main_sync(repo, stamp):
    for exc in (subprocess.TimeoutExpired('pip', 1), OSError('no pip')):
        stamp.unlink(missing_ok=True)
        runner = _Runner(exc_by_file={'requirements-passkeys.txt': exc})
        state = _go(repo, stamp, runner)
        assert state['status'] == ur.OK, repr(exc)
        assert stamp.exists()
        assert state['optional']['passkeys']['status'] == ur.FAILED


def test_main_failure_still_fails_the_sync_and_skips_passkeys(repo, stamp):
    runner = _Runner(rc_by_file={'requirements.txt': 1})
    state = _go(repo, stamp, runner)
    assert runner.files() == ['requirements.txt']
    assert state['status'] == ur.FAILED
    assert not stamp.exists() and not _pk_stamp(stamp).exists()
    assert state['optional'] == {}


def test_successful_passkeys_install_is_stamped_and_not_repeated(repo, stamp):
    runner = _Runner()
    state = _go(repo, stamp, runner)
    assert state['optional']['passkeys']['status'] == ur.OK and _pk_stamp(stamp).exists()
    again = _Runner()
    state = _go(repo, stamp, again, trigger='update')
    assert again.calls == [] and state['status'] == ur.OK


def test_a_failed_passkeys_install_is_retried_alone_at_the_next_trigger(repo, stamp):
    _go(repo, stamp, _Runner(rc_by_file={'requirements-passkeys.txt': 1}))
    retry = _Runner()
    state = _go(repo, stamp, retry, trigger='update')
    assert retry.files() == ['requirements-passkeys.txt'], 'main is already stamped'
    assert state['status'] == ur.OK and state['optional']['passkeys']['status'] == ur.OK
    assert _pk_stamp(stamp).exists()


def test_a_changed_passkeys_file_reinstalls_only_that_file(repo, stamp):
    _go(repo, stamp, _Runner())
    (repo / 'requirements-passkeys.txt').write_text('webauthn==3.0.2\n')
    runner = _Runner()
    _go(repo, stamp, runner, trigger='update')
    assert runner.files() == ['requirements-passkeys.txt']


def test_a_checkout_without_the_passkeys_file_behaves_as_before(repo, stamp):
    (repo / 'requirements-passkeys.txt').unlink()
    runner = _Runner()
    state = _go(repo, stamp, runner)
    assert runner.files() == ['requirements.txt']
    assert state['status'] == ur.OK and state['optional'] == {}


def test_get_state_optional_is_a_copy(repo, stamp):
    _go(repo, stamp, _Runner())
    snap = ur.get_state()
    snap['optional']['passkeys']['status'] = 'tampered'
    assert ur.get_state()['optional']['passkeys']['status'] == ur.OK


# ── the files and installers say the same thing ─────────────────────────────

def test_passkey_pins_are_not_in_the_main_requirements_file():
    main = (PROJECT_ROOT / 'requirements.txt').read_text()
    for name in ('webauthn', 'cbor2', 'pyasn1', 'pyOpenSSL'):
        assert not re.search(rf'^{name}\b', main, re.I | re.M), name
    assert re.search(r'^cryptography>=46\.0\.6\s*$', main, re.M), 'main floor must stay'
    opt = (PROJECT_ROOT / 'requirements-passkeys.txt').read_text()
    assert re.search(r'^webauthn==3\.0\.1\s*$', opt, re.M)
    assert re.search(r'^cbor2==6\.1\.5\s*$', opt, re.M)
    assert re.search(r'^cryptography>=49\.0\.0\s*$', opt, re.M)


def _installer_block(text, marker):
    """From the first line mentioning `marker` to the next STEP banner."""
    start = text.index(marker)
    nxt = re.search(r'\[STEP 2/5\] OK', text[start:])
    return text[start:start + nxt.start()] if nxt else text[start:]


def test_install_sh_installs_passkeys_best_effort_and_never_exits():
    text = (PROJECT_ROOT / 'installer' / 'install.sh').read_text(encoding='utf-8')
    assert 'requirements-passkeys.txt' in text
    block = _installer_block(text, 'requirements-passkeys.txt')
    assert re.search(r'pip"? install', block)
    assert not re.search(r'^\s*exit\b', block, re.M), 'passkeys must not abort the install'
    main = text[text.index('REQ_PATH='):text.index('requirements-passkeys.txt')]
    assert 'exit 2' in main, 'the main requirements step stays fatal'


def test_install_ps1_installs_passkeys_best_effort_and_never_exits():
    text = (PROJECT_ROOT / 'installer' / 'install.ps1').read_text(encoding='utf-8')
    assert 'requirements-passkeys.txt' in text
    block = _installer_block(text, 'requirements-passkeys.txt')
    assert 'pip' in block.lower() and 'install' in block
    assert 'Exit-WithContact' not in block, 'passkeys must not abort the install'
    main = text[text.index('$reqPath ='):text.index('requirements-passkeys.txt')]
    assert 'Exit-WithContact 2' in main, 'the main requirements step stays fatal'

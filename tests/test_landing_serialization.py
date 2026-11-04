import json
import os
import subprocess
import sys
import threading
import time

import pytest

from mc.dev_harness_store import HarnessStore
from mc import landing_serialization as locks
from test_dev_harness_diff import repo, git


def test_alias_threads_reentrancy_and_timeout(repo, tmp_path):
    alias = tmp_path/'alias'
    git(repo, 'worktree', 'add', '--detach', str(alias), 'HEAD')
    results = []
    with locks.landing(repo) as owner:
        with locks.landing(alias) as nested:
            assert nested is owner
        def contend():
            with pytest.raises(locks.LandingBusy):
                with locks.landing(alias, timeout=.1):
                    results.append('wrong')
            results.append('busy')
        thread = threading.Thread(target=contend)
        thread.start()
        thread.join(3)
        assert results == ['busy']
    with locks.landing(alias):
        pass


def test_real_process_lock_crash_release_and_other_repo(repo, tmp_path):
    script = ('from pathlib import Path; from mc.landing_serialization import landing; '
              'import sys; '\
              '\nwith landing(Path(sys.argv[1]), timeout=.1): print("owned", flush=True)')
    with locks.landing(repo):
        child = subprocess.run([sys.executable, '-c', script, str(repo)], capture_output=True, timeout=10)
        assert child.returncode != 0 and b'LandingBusy' in child.stderr
        other = tmp_path/'other'
        other.mkdir()
        git(other, 'init', '-q')
        child = subprocess.run([sys.executable, '-c', script, str(other)], capture_output=True, timeout=10)
        assert child.returncode == 0
    crash = script.replace('print("owned", flush=True)', 'import os; os._exit(9)')
    assert subprocess.run([sys.executable, '-c', crash, str(repo)], timeout=10).returncode == 9
    with locks.landing(repo, timeout=.2):
        pass


def test_real_waiter_acquires_only_after_release(repo):
    script = ('from pathlib import Path; from mc.landing_serialization import landing; '
              'import sys; print("ready", flush=True); '\
              '\nwith landing(Path(sys.argv[1]), timeout=5): print("owned", flush=True)')
    with locks.landing(repo):
        proc = subprocess.Popen([sys.executable, '-c', script, str(repo)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert proc.stdout.readline().strip() == b'ready'
        with pytest.raises(subprocess.TimeoutExpired):
            proc.wait(timeout=.1)
    out, err = proc.communicate(timeout=10)
    assert proc.returncode == 0 and out.strip() == b'owned', err


@pytest.mark.parametrize('field', ['expires_at','owner_start','pins','key','token'])
def test_lease_tampering_even_with_recomputed_content_digest(repo, tmp_path, field):
    from mc.dev_harness_catalogue import digest
    store = HarnessStore(tmp_path/'state')
    with locks.landing(repo) as owner:
        with locks.child_lease(store, 'one', owner, {'B':'b'}) as env:
            identity = env[locks.LEASE_ENV]
            path = store.project_dir('one')/'landing_leases'/(identity+'.json')
            value = json.loads(path.read_text())
            if field == 'expires_at': value[field] = value['created_at']-1
            elif field == 'owner_start': value['owner']['start'] = 'reused'
            elif field == 'pins': value['pins'] = {'B':'other'}
            else: value['owner'][field] = 'wrong'
            value['content_digest'] = digest({k:v for k,v in value.items() if k!='content_digest'})
            path.write_text(json.dumps(value))
            with pytest.raises(ValueError):
                locks.validate_lease(store, 'one', repo, identity, {'B':'b'})


def test_owner_metadata_cleanup_failure_still_releases_both_locks(repo, monkeypatch):
    from pathlib import Path
    original = Path.unlink
    def refuse(path, *args, **kwargs):
        if path.name == 'clayrune-landing-owner.json':
            raise OSError('fixture cleanup failure')
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', refuse)
        with pytest.raises(OSError):
            with locks.landing(repo):
                pass
    with locks.landing(repo, timeout=.1):
        pass


def test_child_lease_exact_identity_real_subprocess(repo, tmp_path):
    store = HarnessStore(tmp_path/'state')
    pins = {'B': 'b', 'H': 'h', 'T': 't', 'revision': 1, 'receipt_digest': 'r'}
    script = ('from pathlib import Path; import sys,json; '
              'from mc.landing_serialization import validate_lease; from mc.dev_harness_store import HarnessStore; '
              'validate_lease(HarnessStore(Path(sys.argv[1])), "one", Path(sys.argv[2]), sys.argv[3], json.loads(sys.argv[4])); print("valid")')
    with locks.landing(repo) as owner:
        with locks.child_lease(store, 'one', owner, pins) as env:
            identity = env[locks.LEASE_ENV]
            argv = [sys.executable, '-c', script, str(tmp_path/'state'), str(repo), identity, json.dumps(pins)]
            result = subprocess.run(argv, capture_output=True, timeout=10)
            assert result.returncode == 0, result.stderr
            path = store.project_dir('one')/'landing_leases'/(identity+'.json')
            value = json.loads(path.read_text())
            value['owner']['start'] = 'reused'
            path.write_text(json.dumps(value))
            assert subprocess.run(argv, capture_output=True, timeout=10).returncode != 0
        assert not path.exists()
    with pytest.raises(ValueError):
        locks.assert_held(owner)

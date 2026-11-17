"""MC-1179 pass 12, TL26: nothing true only of one install lives in tracked tools.

Each test reproduces one bullet of the finding. Behaviour is checked by running
the code against a fabricated checkout, so a clone under any folder name, on any
machine, gets the same answer. Where a literal has to be recognised without
being written down here (a user's words, a session id), the test carries a
hash of it, never the text.
"""
import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / 'tools' / 'memory-eval'
for p in (str(ROOT), str(EVAL)):
    if p not in sys.path:
        sys.path.insert(0, p)


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, path
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _enc(path):
    import mc.memory as m
    return str(m._encode_project_path(str(path)))


def _make_transcript(projects, dirname, sid='s1', text='a real task long enough to count'):
    d = projects / dirname
    d.mkdir(parents=True, exist_ok=True)
    rec = {'type': 'user', 'timestamp': '2099-01-01T00:00:00Z',
           'message': {'role': 'user', 'content': text}}
    (d / f'{sid}.jsonl').write_text(json.dumps(rec) + '\n', encoding='utf-8')
    return d


@pytest.fixture()
def checkout_and_projects(tmp_path, monkeypatch):
    """A clone that is NOT called mission-control, and a fake ~/.claude/projects."""
    checkout = tmp_path / 'some-other-clone'
    checkout.mkdir()
    projects = tmp_path / 'claude-projects'
    projects.mkdir()
    import _checkout
    monkeypatch.setattr(_checkout, 'default_checkout', lambda: checkout)
    return checkout, projects


# ── 4. transcript directories follow THIS checkout ─────────────────────────

def test_transcript_dirs_match_this_checkout_not_a_folder_name(checkout_and_projects):
    import _checkout
    checkout, projects = checkout_and_projects
    mine = _enc(checkout)
    _make_transcript(projects, mine)
    _make_transcript(projects, mine + '--clayrune-agents-abc123')     # its worktree
    _make_transcript(projects, 'C--elsewhere-mission-control')        # the old filter's false positive
    got = sorted(_checkout.transcript_dirs(str(projects)))
    assert got == sorted([mine, mine + '--clayrune-agents-abc123'])


def test_transcript_dirs_accept_the_clis_underscore_and_dot_flattening(tmp_path, monkeypatch):
    import _checkout
    checkout = tmp_path / '_claude' / 'my.clone'
    checkout.mkdir(parents=True)
    projects = tmp_path / 'p'
    projects.mkdir()
    flat = _enc(checkout).replace('_', '-').replace('.', '-')
    _make_transcript(projects, flat)
    monkeypatch.setattr(_checkout, 'default_checkout', lambda: checkout)
    assert _checkout.transcript_dirs(str(projects)) == [flat]


def test_transcript_dirs_keep_the_scratch_and_demo_exclusion(checkout_and_projects):
    import _checkout
    checkout, projects = checkout_and_projects
    mine = _enc(checkout)
    _make_transcript(projects, mine)
    _make_transcript(projects, mine + '-scratch')
    _make_transcript(projects, mine + '-demo-recording')
    assert _checkout.transcript_dirs(str(projects)) == [mine]


def test_zero_matching_dirs_says_so_instead_of_measuring_zero(checkout_and_projects):
    import _checkout
    checkout, projects = checkout_and_projects
    _make_transcript(projects, 'C--unrelated-project')
    with pytest.raises(SystemExit) as ei:
        _checkout.transcript_files(str(projects))
    msg = str(ei.value)
    assert 'no transcript directory found for this checkout' in msg
    assert _enc(checkout) in msg


def test_canonical_checkout_strips_an_agent_worktree(tmp_path):
    import _checkout
    main = tmp_path / 'repo'
    wt = main / '.clayrune' / 'agents' / 'abc123def456'
    wt.mkdir(parents=True)
    assert _checkout.canonical_checkout(wt) == main.resolve()
    assert _checkout.canonical_checkout(main) == main.resolve()


@pytest.mark.parametrize('name,fn', [
    ('delivery_backfill', 'collect_tasks'),
    ('pin_suite', 'discover'),
])
def test_probes_find_sessions_of_a_clone_under_another_name(checkout_and_projects, monkeypatch, name, fn):
    checkout, projects = checkout_and_projects
    _make_transcript(projects, _enc(checkout), 's1')
    _make_transcript(projects, 'C--elsewhere-mission-control', 's2')
    mod = _load(EVAL / f'{name}.py', f'_t_{name}')
    for attr in ('ROOT', 'CLAUDE_PROJECTS'):
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, projects if attr == 'CLAUDE_PROJECTS' else str(projects))
    out = getattr(mod, fn)()
    files = out if fn == 'discover' else out[0]
    assert len(files) == 1 and Path(files[0]).name == 's1.jsonl'


def test_fact_canary_scan_misses_uses_the_checkout(checkout_and_projects, monkeypatch):
    checkout, projects = checkout_and_projects
    mod = _load(EVAL / 'fact_canary_probe.py', '_t_fcp')
    monkeypatch.setattr(os.path, 'expanduser', lambda p: str(projects.parent / 'home'))
    home_projects = projects.parent / 'home' / '.claude' / 'projects'
    home_projects.mkdir(parents=True)
    _make_transcript(home_projects, _enc(checkout), 's1')
    n, _hits = mod.scan_misses(days=100000)
    assert n >= 1                     # the clone's own session was seen
    shutil.rmtree(home_projects / _enc(checkout))
    with pytest.raises(SystemExit) as ei:
        mod.scan_misses(days=100000)
    assert 'no transcript directory found for this checkout' in str(ei.value)


@pytest.mark.parametrize('fname', ['delivery_backfill.py', 'pin_suite.py', 'scorer_ab.py',
                                   'retrieval_probe.py', 'fact_canary_probe.py'])
def test_no_probe_filters_transcripts_on_a_folder_name(fname):
    src = (EVAL / fname).read_text(encoding='utf-8')
    assert not re.search(r"""['"]mission-control['"]\s+in\b""", src), fname


# ── 1. retrieval_probe: derive the memory dir, say when it is skipped ───────

def test_retrieval_probe_has_no_literal_encoded_path():
    src = (EVAL / 'retrieval_probe.py').read_text(encoding='utf-8')
    assert not re.search(r'[A-Za-z]--Users-', src)


def test_retrieval_probe_memory_dir_derives_from_the_checkout(checkout_and_projects):
    checkout, projects = checkout_and_projects
    mod = _load(EVAL / 'retrieval_probe.py', '_t_rp')
    (projects / _enc(checkout) / 'memory').mkdir(parents=True)
    assert Path(mod.memory_dir_for(str(projects))) == projects / _enc(checkout) / 'memory'


def test_retrieval_probe_prefers_the_variant_dir_whose_index_changed_last(tmp_path, monkeypatch):
    import _checkout
    checkout = tmp_path / '_claude' / 'clone'
    checkout.mkdir(parents=True)
    monkeypatch.setattr(_checkout, 'default_checkout', lambda: checkout)
    projects = tmp_path / 'p'
    unflat = projects / _enc(checkout) / 'memory'
    flat = projects / _enc(checkout).replace('_', '-') / 'memory'
    for d in (unflat, flat):
        d.mkdir(parents=True)
        (d / 'MEMORY.md').write_text('x', encoding='utf-8')
    os.utime(unflat / 'MEMORY.md', (1, 1))          # stale variant
    mod = _load(EVAL / 'retrieval_probe.py', '_t_rp3')
    assert Path(mod.memory_dir_for(str(projects))) == flat


def test_retrieval_probe_says_why_the_never_opened_block_is_skipped(
        checkout_and_projects, monkeypatch, capsys):
    checkout, projects = checkout_and_projects
    _make_transcript(projects, _enc(checkout))            # sessions exist, no memory/ dir
    mod = _load(EVAL / 'retrieval_probe.py', '_t_rp2')
    monkeypatch.setattr(mod, 'ROOT', str(projects))
    mod.main()
    out = capsys.readouterr().out
    assert 'never-opened block skipped' in out
    assert _enc(checkout) in out


# ── 2. codex_miss_repro: no real user's words ───────────────────────────────

_REAL_5GRAMS = {'793a18e3b51e5b98', 'd646aedb33232207'}
_REAL_CUTOFF = '9c12a102a05db94b'
_REAL_SID = 'b82efdfa26d52bfe'


def _h(t):
    return hashlib.sha256(t.encode()).hexdigest()[:16]


def test_codex_miss_repro_quotes_no_real_message_session_or_timestamp():
    path = EVAL / 'codex_miss_repro.py'
    src = path.read_text(encoding='utf-8')
    words = re.findall(r"[a-z0-9']+", src.lower())
    grams = {_h(' '.join(words[i:i + 5])) for i in range(len(words) - 4)}
    assert not (grams & _REAL_5GRAMS)
    assert not {_h(t) for t in re.findall(r'\b[0-9a-f]{8}\b', src)} & {_REAL_SID}
    mod = _load(path, '_t_cmr')
    assert _h(mod.CUTOFF.isoformat().strip().lower()) != _REAL_CUTOFF
    for q in mod.QUERIES.values():
        qw = re.findall(r"[a-z0-9']+", q.lower())
        assert not ({_h(' '.join(qw[i:i + 5])) for i in range(len(qw) - 4)} & _REAL_5GRAMS)
    assert not any(k.startswith(('ron', 'dave')) for k in mod.QUERIES)


def test_codex_miss_repro_still_loads_with_its_query_shapes():
    mod = _load(EVAL / 'codex_miss_repro.py', '_t_cmr2')
    assert len(mod.QUERIES) == 4 and 'oracle' in mod.QUERIES
    assert all(isinstance(q, str) and q for q in mod.QUERIES.values())


# ── 3. adb resolution ───────────────────────────────────────────────────────

def _node_resolve(env_extra, path_dirs):
    node = shutil.which('node')
    if not node:
        pytest.skip('node not installed')
    env = {k: v for k, v in os.environ.items()
           if k not in ('MC_ADB', 'ANDROID_HOME', 'ANDROID_SDK_ROOT', 'PATH', 'Path')}
    env['PATH'] = os.pathsep.join(str(p) for p in path_dirs)
    env.update(env_extra)
    js = ("const a=require(process.argv[1]);"
          "try{console.log('OK '+a.resolveAdb())}catch(e){console.log('ERR '+e.message)}")
    r = subprocess.run([node, '-e', js, str(ROOT / 'tools/mobile-test/lib/adb.js')],
                       env=env, capture_output=True, text=True, timeout=60)
    return r.stdout.strip()


def _fake_adb(d):
    d.mkdir(parents=True, exist_ok=True)
    f = d / ('adb.exe' if os.name == 'nt' else 'adb')
    f.write_text('', encoding='utf-8')
    return f


def test_adb_prefers_mc_adb_then_android_home_then_path(tmp_path):
    via_env = _fake_adb(tmp_path / 'a')
    sdk = tmp_path / 'sdk'
    via_home = _fake_adb(sdk / 'platform-tools')
    via_path = _fake_adb(tmp_path / 'onpath')
    empty = tmp_path / 'empty'
    empty.mkdir()
    assert _node_resolve({'MC_ADB': str(via_env), 'ANDROID_HOME': str(sdk)}, [tmp_path / 'onpath']) \
        == f'OK {via_env}'
    assert _node_resolve({'ANDROID_HOME': str(sdk)}, [tmp_path / 'onpath']) == f'OK {via_home}'
    assert _node_resolve({}, [tmp_path / 'onpath']) == f'OK {via_path}'
    msg = _node_resolve({}, [empty])
    assert msg.startswith('ERR ')
    for name in ('MC_ADB', 'ANDROID_HOME', 'PATH'):
        assert name in msg


def test_adb_js_has_no_drive_letter_default():
    src = (ROOT / 'tools/mobile-test/lib/adb.js').read_text(encoding='utf-8')
    assert not re.search(r'[A-Za-z]:\\\\', src)


# ── 5. project id comes from the record whose project_path is this checkout ─

def _data_root_with_project(tmp_path, checkout, pid='zz_proj'):
    pdir = tmp_path / 'dataroot' / 'data' / 'projects'
    pdir.mkdir(parents=True)
    (pdir / f'{pid}.json').write_text(
        json.dumps({'id': pid, 'project_path': str(checkout)}), encoding='utf-8')
    (pdir / 'other.json').write_text(
        json.dumps({'id': 'other', 'project_path': str(tmp_path / 'else')}), encoding='utf-8')
    (pdir / f'{pid}_agent_log.json').write_text('[]', encoding='utf-8')
    return tmp_path / 'dataroot'


def test_project_id_is_the_record_whose_path_is_this_checkout(checkout_and_projects, tmp_path, monkeypatch):
    import _checkout
    checkout, _ = checkout_and_projects
    monkeypatch.setenv('MC_DATA_DIR', str(_data_root_with_project(tmp_path, checkout)))
    assert _checkout.project_id() == 'zz_proj'
    assert _checkout.project_id(checkout) == 'zz_proj'


def test_project_id_with_no_matching_record_names_the_override(checkout_and_projects, tmp_path, monkeypatch):
    import _checkout
    monkeypatch.setenv('MC_DATA_DIR', str(tmp_path / 'empty-data-root'))
    with pytest.raises(SystemExit) as ei:
        _checkout.project_id()
    assert '--project' in str(ei.value)


def test_project_id_reuses_the_existing_find_project():
    """One helper, not two: the lookup is merge-review-check's own find_project."""
    import _checkout
    src = Path(_checkout.__file__).read_text(encoding='utf-8')
    assert 'merge-review-check.py' in src and 'find_project' in src
    assert 'json.loads' not in src and "glob('*.json')" not in src


@pytest.mark.parametrize('rel', ['tools/memory-eval/_harness.py', 'tools/memory-eval/push_review.py',
                                 'tools/memory-link-check.py',
                                 'tools/memory-eval/push_log_line_repro.py'])
def test_no_literal_project_id_string(rel):
    tree = ast.parse((ROOT / rel).read_text(encoding='utf-8'))
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
                  and n.body and isinstance(n.body[0], ast.Expr)
                  and isinstance(getattr(n.body[0], 'value', None), ast.Constant)}
    lits = [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings and 'mission_control' in n.value]
    assert lits == [], rel


def test_harness_wire_takes_the_project_id_from_the_checkout(checkout_and_projects, tmp_path, monkeypatch):
    import _checkout
    import _harness
    import mc.memory as m
    checkout, _ = checkout_and_projects
    monkeypatch.setattr(_checkout, 'project_id', lambda project_path=None: 'zz_proj')
    monkeypatch.setattr(_harness, 'repo_root', lambda: tmp_path)   # no config.json to merge
    monkeypatch.setattr(m, 'wire', lambda **kw: None)              # leave mc.memory's globals alone
    monkeypatch.setattr(_harness, 'assert_no_server', lambda: None)  # other tests import server
    _m, project = _harness.wire(project_path=str(checkout))
    assert project['id'] == 'zz_proj'


def test_push_review_project_flag_overrides_and_default_is_resolved(monkeypatch):
    import _checkout
    mod = _load(EVAL / 'push_review.py', '_t_pr')
    monkeypatch.setattr(_checkout, 'project_id', lambda project_path=None: 'resolved_id')
    assert mod.resolve_project(None) == 'resolved_id'
    assert mod.resolve_project('given') == 'given'


def test_memory_link_check_resolves_its_project_from_the_checkout(
        checkout_and_projects, tmp_path, monkeypatch):
    import _checkout
    checkout, _ = checkout_and_projects
    root = _data_root_with_project(tmp_path, checkout)
    monkeypatch.setenv('MC_DATA_DIR', str(root))
    mod = _load(ROOT / 'tools' / 'memory-link-check.py', '_t_mlc')
    seen = {}

    from mc import memory as _mem
    monkeypatch.setattr(_mem, 'MEMORY_DIR', _mem.MEMORY_DIR)    # _default_dir rebinds these two;
    monkeypatch.setattr(_mem, 'CLAUDE_HOME', _mem.CLAUDE_HOME)  # monkeypatch restores them
    monkeypatch.setattr(_mem, '_get_memory_path',
                        lambda proj: seen.setdefault('p', proj) and tmp_path / 'MEMORY.md')
    mod._default_dir(None)
    assert seen['p']['id'] == 'zz_proj'


# ── 6. READMEs ──────────────────────────────────────────────────────────────

def _operator_words():
    words = set()
    home = Path.home().name
    if len(home) > 3:
        words.add(home.lower())
    r = subprocess.run(['git', 'config', 'user.name'], capture_output=True, text=True, cwd=str(ROOT))
    first = (r.stdout.strip().split() or [''])[0].lower()
    if len(first) > 2:
        words.add(first)
    return words


def test_readmes_do_not_name_the_operator_or_report_this_machine():
    for rel in ('tools/memory-eval/README.md', 'tools/worktree-sandbox/README.md'):
        text = (ROOT / rel).read_text(encoding='utf-8').lower()
        assert 'this machine' not in text, rel
        for w in _operator_words():
            assert not re.search(r'\b' + re.escape(w) + r'\b', text), rel


# ── class guard over the files this change touched ──────────────────────────

_TOUCHED = ['tools/memory-eval/retrieval_probe.py', 'tools/memory-eval/codex_miss_repro.py',
            'tools/mobile-test/lib/adb.js', 'tools/memory-eval/delivery_backfill.py',
            'tools/memory-eval/pin_suite.py', 'tools/memory-eval/scorer_ab.py',
            'tools/memory-eval/fact_canary_probe.py', 'tools/memory-eval/_harness.py',
            'tools/memory-eval/_checkout.py', 'tools/memory-eval/push_review.py',
            'tools/memory-link-check.py', 'tools/memory-eval/push_log_line_repro.py']


@pytest.mark.parametrize('rel', _TOUCHED)
def test_no_drive_letter_path_or_account_name(rel):
    text = (ROOT / rel).read_text(encoding='utf-8')
    assert not re.search(r'\b[A-Za-z]:\\{1,2}[A-Za-z]', text), rel
    assert not re.search(r'\b[A-Za-z]--Users-', text), rel
    acct = Path.home().name.lower()
    if len(acct) > 3:
        assert acct not in text.lower(), rel

"""G1 — the attended note-write gate (MEMORY_DESIGN_V2_SPEC.md §10).

Every §10.2 class that applies to a note write, in BOTH `memory_gate_mode`
postures: report = the write lands and the would-be rejection is counted;
enforce = 422, machine-readable body, on-disk bytes untouched. Plus the rails
that are NOT mode-dependent (slug/path safety, clobber refusal, server
stamping, cross-project refusal) and the counter the step-9 harness reads.

Standalone Flask app with only the new blueprint, memory-module paths
redirected to tmp_path — no server import except the one wiring test.
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

MODES = ('report', 'enforce')


class Env:
    def __init__(self, tmp_path, monkeypatch):
        from flask import Flask
        from mc import memory as m
        from mc import state
        from mc.blueprints import memory_note_routes as nr

        self.m, self.state, self.tmp = m, state, tmp_path
        self.claude_home = tmp_path / 'claude'
        self.projects = {'p1': {'id': 'p1'}, 'p2': {'id': 'p2'},
                         'nopath': {'id': 'nopath'}}
        monkeypatch.setattr(m, 'CLAUDE_HOME', self.claude_home)
        monkeypatch.setattr(m, 'MEMORY_DIR', tmp_path / 'shared')
        monkeypatch.setattr(m, 'DATA_DIR', tmp_path / 'data' / 'projects')
        (tmp_path / 'shared').mkdir()

        def _mp(p):
            if p['id'] == 'nopath':
                return tmp_path / 'shared' / 'nopath.md'
            return self.vault(p['id']) / 'MEMORY.md'
        monkeypatch.setattr(m, '_get_memory_path', _mp)
        monkeypatch.setitem(state.CONFIG, 'memory_gate_mode', 'report')
        snap = dict(state.agent_sessions)
        state.agent_sessions.clear()
        self._restore = snap
        self.nr = nr
        nr.wire(load_project_fn=self.projects.get)
        app = Flask(__name__)
        app.register_blueprint(nr.bp)
        self.client = app.test_client()
        self.mp = monkeypatch

    def finish(self):
        self.state.agent_sessions.clear()
        self.state.agent_sessions.update(self._restore)

    def vault(self, pid):
        d = self.claude_home / f'vault-{pid}' / 'memory'
        d.mkdir(parents=True, exist_ok=True)
        return d

    def mode(self, mode):
        self.mp.setitem(self.state.CONFIG, 'memory_gate_mode', mode)

    def seed(self, pid, stem, text):
        (self.vault(pid) / f'{stem}.md').write_text(text, encoding='utf-8')

    def read(self, pid, stem):
        return (self.vault(pid) / f'{stem}.md').read_text(encoding='utf-8')

    def exists(self, pid, stem):
        return (self.vault(pid) / f'{stem}.md').exists()

    def post(self, slug, content, pid='p1', **kw):
        return self.client.post(f'/api/project/{pid}/memory/note',
                                json={'slug': slug, 'content': content, **kw})

    def patch(self, slug, content, pid='p1', **kw):
        return self.client.patch(f'/api/project/{pid}/memory/note',
                                 json={'slug': slug, 'content': content, **kw})

    def counts(self, pid='p1'):
        from mc import memory_note_gate as g
        return g.gate_counts(pid)

    def session(self, sid, project_id, trigger_type, status='running'):
        self.state.agent_sessions[sid] = {
            'status': status, 'project_id': project_id,
            'trigger_type': trigger_type}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch)
    yield e
    e.finish()


def note(name='alpha', desc='a note', body='body text', extra=''):
    return f'---\nname: {name}\ndescription: {desc}\n{extra}---\n{body}\n'


# ── clean write: the stamps ──────────────────────────────────────────────────

@pytest.mark.parametrize('mode', MODES)
def test_clean_post_writes_and_stamps(env, mode):
    env.mode(mode)
    r = env.post('alpha', note())
    assert r.status_code == 201
    body = r.get_json()
    assert body['ok'] and body['file'] == 'alpha.md'
    assert body['would_reject'] == [] and body['origin'] == 'interactive'
    text = env.read('p1', 'alpha')
    assert 'origin: interactive' in text
    assert 'generated:\n  by: memory-note-api\n  at: ' in text
    assert 'metadata:\n  type: project' in text
    c = env.counts()
    assert (c['requests'], c['clean'], c['would_reject']) == (1, 1, 0)


def test_counter_lives_outside_data_dir(env):
    env.post('alpha', note())
    log = env.tmp / 'data' / 'memory_gate_log' / 'p1.jsonl'
    assert log.is_file()
    assert not list((env.tmp / 'data' / 'projects').glob('**/*'))


def test_author_supplied_provenance_is_discarded(env):
    forged = note(extra='origin: legacy\nverified:\n  - by: human:ron\n'
                        'generated:\n  by: forged\n  at: 2000-01-01\n')
    r = env.post('alpha', forged)
    assert r.status_code == 201
    text = env.read('p1', 'alpha')
    assert 'origin: legacy' not in text and 'human:ron' not in text
    assert 'forged' not in text
    assert text.count('origin:') == 1 and 'origin: interactive' in text


def test_unattended_caller_is_stamped_unattended(env):
    env.session('s1', 'p1', 'schedule')
    r = env.post('alpha', note(extra='origin: interactive\n'))
    assert r.status_code == 201 and r.get_json()['origin'] == 'unattended'
    assert 'origin: unattended' in env.read('p1', 'alpha')


def test_browser_origin_header_is_interactive_even_with_unattended_running(env):
    env.session('s1', 'p1', 'schedule')
    r = env.client.post('/api/project/p1/memory/note',
                        json={'slug': 'alpha', 'content': note()},
                        headers={'Origin': 'http://localhost:5199'})
    assert r.get_json()['origin'] == 'interactive'


# ── missing / unparseable frontmatter ────────────────────────────────────────

@pytest.mark.parametrize('content,cls', [
    ('just prose, no block\n', 'frontmatter_missing'),
    ('---\nname: x\ndescription: y\nno closing fence\n', 'frontmatter_unparseable'),
    ('---\nname: x\nthis line is not key value\ndescription: y\n---\nb\n',
     'frontmatter_unparseable'),
])
def test_frontmatter_classes_report_writes_stamp_only(env, content, cls):
    env.mode('report')
    r = env.post('alpha', content)
    assert r.status_code == 201
    assert r.get_json()['would_reject'][0] == cls
    text = env.read('p1', 'alpha')
    assert 'origin: interactive' in text        # still stamped
    assert content.strip().splitlines()[0] in text   # author text kept verbatim
    assert env.counts()['by_class'][cls] == 1


@pytest.mark.parametrize('content,cls', [
    ('just prose, no block\n', 'frontmatter_missing'),
    ('---\nname: x\ndescription: y\nno closing fence\n', 'frontmatter_unparseable'),
    ('---\nname: x\nthis line is not key value\ndescription: y\n---\nb\n',
     'frontmatter_unparseable'),
])
def test_frontmatter_classes_enforce_rejects(env, content, cls):
    env.mode('enforce')
    r = env.post('alpha', content)
    assert r.status_code == 422
    body = r.get_json()
    assert body['error'] == 'memory_note_rejected' and body['mode'] == 'enforce'
    assert [v['class'] for v in body['violations']] == [cls]
    assert body['remedy']
    assert not env.exists('p1', 'alpha')
    c = env.counts()
    assert c['rejected'] == 1 and c['by_class'][cls] == 1


# ── missing required field ───────────────────────────────────────────────────

@pytest.mark.parametrize('field,content', [
    ('name', '---\ndescription: d\n---\nb\n'),
    ('description', '---\nname: n\n---\nb\n'),
    ('description', '---\nname: n\ndescription:\n---\nb\n'),
])
def test_missing_field_enforce_422_names_the_field(env, field, content):
    env.mode('enforce')
    r = env.post('alpha', content)
    assert r.status_code == 422
    v = r.get_json()['violations']
    assert [(x['class'], x['field']) for x in v] == [('missing_field', field)]
    assert not env.exists('p1', 'alpha')


def test_missing_field_report_writes_and_counts(env):
    env.mode('report')
    r = env.post('alpha', '---\ndescription: d\n---\nb\n')
    assert r.status_code == 201
    assert r.get_json()['would_reject'] == ['missing_field']
    assert env.exists('p1', 'alpha')
    assert env.counts()['by_class'] == {'missing_field': 1}


def test_type_and_origin_are_never_required_of_the_author(env):
    env.mode('enforce')
    assert env.post('alpha', note()).status_code == 201   # neither supplied


# ── class A: .md inside the wikilink ─────────────────────────────────────────

@pytest.mark.parametrize('mode', MODES)
def test_class_a_is_repaired_silently_in_both_modes(env, mode):
    env.mode(mode)
    env.seed('p1', 'target', note('target'))
    r = env.post('alpha', note(body='see [[target.md]] for more'))
    assert r.status_code == 201
    assert r.get_json()['would_reject'] == []
    assert '[[target.md]]' in env.read('p1', 'alpha')     # resolution, not an edit
    c = env.counts()
    assert c['repaired'] == 1 and c['would_reject'] == 0 and c['by_class'] == {}


def test_class_a_pointing_nowhere_is_class_d_not_a(env):
    env.mode('enforce')
    r = env.post('alpha', note(body='[[ghost.md]]'))
    assert [v['class'] for v in r.get_json()['violations']] == ['class_d_unresolved']


# ── class B: rename without aka ──────────────────────────────────────────────

@pytest.mark.parametrize('mode', MODES)
def test_class_b_rename_without_aka(env, mode):
    env.mode(mode)
    env.seed('p1', 'old-name', note('old-name', body='v1'))
    before = env.read('p1', 'old-name')
    r = env.patch('old-name', note('new-name', body='v2'), new_slug='new-name')
    if mode == 'enforce':
        assert r.status_code == 422
        v = r.get_json()['violations'][0]
        assert v['class'] == 'class_b_rename_without_aka'
        assert v['old_stem'] == 'old-name' and 'aka: old-name' in v['remedy']
        assert env.read('p1', 'old-name') == before         # byte-for-byte
        assert not env.exists('p1', 'new-name')
    else:
        assert r.status_code == 200
        body = r.get_json()
        assert body['would_reject'] == ['class_b_rename_without_aka']
        assert body['renamed_from'] == 'old-name.md' and body['file'] == 'new-name.md'
        assert env.exists('p1', 'new-name') and not env.exists('p1', 'old-name')
    assert env.counts()['by_class']['class_b_rename_without_aka'] == 1


def test_rename_with_aka_passes_enforce_and_the_old_stem_resolves(env):
    env.mode('enforce')
    env.seed('p1', 'old-name', note('old-name'))
    r = env.patch('old-name', note('new-name', extra='aka: [old-name]\n'),
                  new_slug='new-name')
    assert r.status_code == 200 and r.get_json()['would_reject'] == []
    # a link to the OLD stem now resolves through the forwarding record
    r2 = env.post('linker', note('linker', body='[[old-name]]'))
    assert r2.status_code == 201 and r2.get_json()['would_reject'] == []


def test_rename_onto_an_existing_note_is_refused_in_both_modes(env):
    for mode in MODES:
        env.mode(mode)
        env.seed('p1', 'a', note('a'))
        env.seed('p1', 'b', note('b'))
        r = env.patch('a', note('b', extra='aka: a\n'), new_slug='b')
        assert r.status_code == 409
        assert env.exists('p1', 'a')


def test_case_only_rename_needs_no_aka(env):
    env.mode('enforce')
    env.seed('p1', 'My-Note', note('x'))
    r = env.patch('My-Note', note('x'), new_slug='my_note')
    assert r.status_code == 200


# ── class C: cross-vault ─────────────────────────────────────────────────────

@pytest.mark.parametrize('mode', MODES)
def test_class_c_cross_vault_link(env, mode):
    env.mode(mode)
    env.seed('p2', 'elsewhere', note('elsewhere'))
    r = env.post('alpha', note(body='see [[elsewhere]]'))
    if mode == 'enforce':
        assert r.status_code == 422
        v = r.get_json()['violations'][0]
        assert v['class'] == 'class_c_cross_vault' and v['vault'] == 'vault-p2'
        assert 'external_ref' in v['remedy']
        assert not env.exists('p1', 'alpha')
    else:
        assert r.status_code == 201
        assert r.get_json()['would_reject'] == ['class_c_cross_vault']
        assert env.exists('p1', 'alpha')
    assert env.counts()['by_class'] == {'class_c_cross_vault': 1}


def test_class_c_recorded_as_external_ref_passes_enforce(env):
    env.mode('enforce')
    env.seed('p2', 'elsewhere', note('elsewhere'))
    r = env.post('alpha', note(body='see [[elsewhere]]',
                               extra='external_ref: vault-p2/elsewhere\n'))
    assert r.status_code == 201
    assert 'external_ref: vault-p2/elsewhere' in env.read('p1', 'alpha')


# ── class D: resolves nowhere ────────────────────────────────────────────────

@pytest.mark.parametrize('mode', MODES)
def test_class_d_unresolved_with_near_miss_candidates(env, mode):
    env.mode(mode)
    env.seed('p1', 'arch_mobile_ui', note('arch-mobile-ui'))
    r = env.post('alpha', note(body='see [[arch-mobil-ui]]'))
    if mode == 'enforce':
        assert r.status_code == 422
        v = r.get_json()['violations'][0]
        assert v['class'] == 'class_d_unresolved'
        assert v['candidates'] == ['arch_mobile_ui']
        assert not env.exists('p1', 'alpha')
    else:
        assert r.status_code == 201
        assert r.get_json()['would_reject'] == ['class_d_unresolved']
    assert env.counts()['by_class'] == {'class_d_unresolved': 1}


def test_wikilinks_inside_code_are_not_links(env):
    env.mode('enforce')
    r = env.post('alpha', note(body='syntax is `[[ghost]]` and\n```\n[[ghost2]]\n```'))
    assert r.status_code == 201


# ── duplicate identity ───────────────────────────────────────────────────────

@pytest.mark.parametrize('mode', MODES)
def test_exact_duplicate_never_clobbers(env, mode):
    env.mode(mode)
    env.seed('p1', 'alpha', note('alpha', body='curated'))
    before = env.read('p1', 'alpha')
    r = env.post('alpha', note('alpha', body='overwrite attempt'))
    assert r.status_code == 409 and r.get_json()['error'] == 'exists'
    assert env.read('p1', 'alpha') == before
    assert env.counts()['refused'] == 1


@pytest.mark.parametrize('mode', MODES)
def test_link_key_collision_is_a_mode_dependent_violation(env, mode):
    env.mode(mode)
    env.seed('p1', 'arch_x', note('arch-x'))
    r = env.post('arch-x', note('arch-x-2'))
    if mode == 'enforce':
        assert r.status_code == 422
        assert r.get_json()['violations'][0]['class'] == 'duplicate_identity'
        assert not env.exists('p1', 'arch-x')
    else:
        assert r.status_code == 201
        assert r.get_json()['would_reject'] == ['duplicate_identity']


# ── PATCH ────────────────────────────────────────────────────────────────────

def test_patch_missing_note_is_404(env):
    assert env.patch('ghost', note()).status_code == 404


def test_patch_preserves_verified_and_generated_and_keeps_unattended(env):
    env.seed('p1', 'alpha', '---\nname: alpha\ndescription: d\norigin: unattended\n'
             'generated:\n  by: scribe\n  at: 2026-01-01T00:00:00Z\n'
             'verified:\n  - by: human:ron\n    at: 2026-01-02\n---\nold\n')
    r = env.patch('alpha', note('alpha', body='new body'))   # interactive caller
    assert r.status_code == 200
    text = env.read('p1', 'alpha')
    assert 'origin: unattended' in text            # an edit cannot launder it
    assert 'by: scribe' in text and 'by: human:ron' in text
    assert 'new body' in text and 'old' not in text
    assert text.count('generated:') == 1 and text.count('verified:') == 1


def test_patch_only_checks_links_the_note_did_not_already_carry(env):
    env.mode('enforce')
    env.seed('p1', 'alpha', note('alpha', body='legacy [[ghost]]'))
    ok = env.patch('alpha', note('alpha', body='legacy [[ghost]] plus text'))
    assert ok.status_code == 200                     # nothing on disk goes invalid
    bad = env.patch('alpha', note('alpha', body='legacy [[ghost]] and [[ghost2]]'))
    assert bad.status_code == 422
    assert [v['target'] for v in bad.get_json()['violations']] == ['ghost2']


# ── rails (not mode-dependent) ───────────────────────────────────────────────

@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('slug', ['../escape', 'a/b', 'a\\b', 'MEMORY', 'memory.md',
                                  'MEMORY_ARCHIVE', 'SESSION_LOG', 'continuity',
                                  'position_foo', '..', '', '.hidden'])
def test_slug_rails_refuse_in_both_modes(env, mode, slug):
    env.mode(mode)
    mem_md = env.vault('p1') / 'MEMORY.md'
    mem_md.write_text('index', encoding='utf-8')
    r = env.post(slug, note())
    assert r.status_code == 400 and r.get_json()['error'] in ('slug_refused',)
    assert mem_md.read_text(encoding='utf-8') == 'index'
    assert sorted(p.name for p in env.vault('p1').iterdir()) == ['MEMORY.md']


def test_project_without_a_vault_is_refused(env):
    r = env.post('alpha', note(), pid='nopath')
    assert r.status_code == 400 and r.get_json()['error'] == 'slug_refused'
    assert list((env.tmp / 'shared').iterdir()) == []


def test_unattended_caller_cannot_reach_a_project_with_no_session(env):
    env.session('s1', 'p1', 'schedule')
    r = env.post('alpha', note(), pid='p2')
    assert r.status_code == 403 and r.get_json()['error'] == 'cross_project_unattended'
    assert not env.exists('p2', 'alpha')
    assert env.counts('p2')['refused'] == 1


def test_unattended_caller_may_write_its_own_project(env):
    env.session('s1', 'p1', 'dispatch')
    assert env.post('alpha', note(), pid='p1').status_code == 201


def test_bad_requests(env):
    assert env.post('a', note(), pid='nope').status_code == 404
    assert env.client.post('/api/project/p1/memory/note',
                           data='not json').status_code == 400
    assert env.client.post('/api/project/p1/memory/note',
                           json={'slug': 'a'}).status_code == 400
    assert env.client.post('/api/project/p1/memory/note',
                           json={'slug': 'a', 'content': note(),
                                 'note_type': 'bogus'}).status_code == 400


def test_note_type_is_honoured_when_valid(env):
    assert env.post('alpha', note(), note_type='feedback').status_code == 201
    assert 'type: feedback' in env.read('p1', 'alpha')


# ── mode switch + counter ────────────────────────────────────────────────────

@pytest.mark.parametrize('value,expected', [
    ('report', 'report'), ('enforce', 'enforce'), (' Enforce ', 'enforce'),
    ('banana', 'report'), ('', 'report'), (None, 'report'), (True, 'report'),
])
def test_gate_mode_only_enforces_on_the_literal(env, value, expected):
    from mc import memory_note_gate as g
    env.mp.setitem(env.state.CONFIG, 'memory_gate_mode', value)
    assert g.gate_mode() == expected


def test_gate_mode_defaults_to_report_when_unset(env):
    from mc import memory_note_gate as g
    env.mp.delitem(env.state.CONFIG, 'memory_gate_mode')
    assert g.gate_mode() == 'report'


def test_counter_aggregates_and_reports_a_rate(env):
    env.post('ok1', note('ok1'))
    env.post('bad1', '---\ndescription: d\n---\nb\n')          # would_reject
    env.post('bad2', note('bad2', body='[[nowhere]]'))          # would_reject
    env.mode('enforce')
    env.post('bad3', 'no frontmatter')                          # rejected
    c = env.counts()
    assert c['requests'] == 4 and c['clean'] == 1
    assert c['would_reject'] == 2 and c['rejected'] == 1
    assert c['by_class'] == {'missing_field': 1, 'class_d_unresolved': 1,
                             'frontmatter_missing': 1}
    assert c['false_rejection_rate'] == 0.5


def test_counter_for_an_unused_project_is_empty_not_an_error(env):
    c = env.counts('p2')
    assert c['requests'] == 0 and c['false_rejection_rate'] is None


def test_counter_rows_carry_the_violation_detail(env):
    env.post('bad', note(body='[[nowhere]]'))
    row = json.loads((env.tmp / 'data' / 'memory_gate_log' / 'p1.jsonl')
                     .read_text(encoding='utf-8').splitlines()[0])
    assert row['classes'] == ['class_d_unresolved'] and row['mode'] == 'report'
    assert row['violations'][0]['target'] == 'nowhere'


# ── wiring ───────────────────────────────────────────────────────────────────

def test_routes_are_registered_on_the_server_app():
    import server
    rules = [r for r in server.app.url_map.iter_rules()
             if r.rule == '/api/project/<project_id>/memory/note']
    assert len(rules) == 2
    assert {m for r in rules for m in r.methods} >= {'POST', 'PATCH'}
    assert {r.endpoint for r in rules} == {
        'memory_note_routes.post_memory_note',
        'memory_note_routes.patch_memory_note'}

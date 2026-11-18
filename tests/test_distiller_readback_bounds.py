"""MC-1179 ML25: the exploration read-floor's work per dispatch is bounded.

`exploration_read_floor` runs at every dispatch that has a task. It used to
read every proposed artifact of every project (twice each) before applying the
scope filter, and to rewrite the whole stats sidecar once for the query, once
for a hit, and once more PER WITHHELD ARTIFACT when the consumer was a steward.

These tests pin the bound, not the ranking (tests/test_distiller_readback.py
pins that) and not the unattended rule (tests/test_distiller_safety.py):

  * an artifact of another project is never opened;
  * an artifact that has not changed since the last dispatch is not re-read;
  * one dispatch is at most one sidecar rewrite, whatever it withheld;
  * the counters still say exactly what they said before;
  * the cache never serves a stale `origin:` stamp, a deleted artifact, or
    misses a new one.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mc import distiller


def _write_expl(root: Path, scope_dir: str, slug: str, origin: str = 'interactive',
                created: str = '2026-06-05T00:00:00Z') -> Path:
    d = root / '_proposed' / scope_dir / f"2026-06-05T00-00-00-aaaaaaaaaaaa-{slug}"
    d.mkdir(parents=True, exist_ok=True)
    f = d / 'EXPLORATION.md'
    f.write_text(
        "---\n"
        "kind: exploration\n"
        f"name: {slug}\n"
        + (f"origin: {origin}\n" if origin else "")
        + f"created_at: {created}\n"
        "---\n\n"
        f"# {slug.replace('-', ' ')}\n\nBody of {slug}.\n",
        encoding='utf-8')
    return f


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A real _proposed/ tree and a real stats sidecar, both under tmp_path."""
    data = tmp_path / 'data'
    data.mkdir()

    def _atomic(p, text):
        Path(p).write_text(text, encoding='utf-8')

    monkeypatch.setattr(distiller, '_skills_root', tmp_path)
    monkeypatch.setattr(distiller, '_data_root', data)
    monkeypatch.setattr(distiller, '_atomic_write_text', _atomic)
    monkeypatch.setattr(distiller, '_now_iso', lambda: '2026-11-17T00:00:00Z')
    monkeypatch.setattr(distiller, '_log', lambda *a, **k: None)
    return tmp_path


@pytest.fixture
def reads(monkeypatch):
    """Every Path.read_text call made while the fixture is live, by path."""
    seen: list[str] = []
    real = Path.read_text

    def _counting(self, *a, **k):
        seen.append(str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(Path, 'read_text', _counting)
    return seen


@pytest.fixture
def sidecar_writes(monkeypatch):
    """Count whole-sidecar rewrites."""
    n = {'writes': 0}
    real = distiller._write_skill_stats

    def _counting(project_id, stats):
        n['writes'] += 1
        return real(project_id, stats)

    monkeypatch.setattr(distiller, '_write_skill_stats', _counting)
    return n


def _counters(project_id: str) -> dict:
    p = distiller._skill_stats_path(project_id)
    return json.loads(Path(p).read_text(encoding='utf-8')).get('counters', {})


def _artifact_reads(seen: list[str]) -> list[str]:
    return [s for s in seen if '_proposed' in s]


# ── the scan ────────────────────────────────────────────────────────────────

def test_another_projects_artifacts_are_never_opened(tree, reads):
    _write_expl(tree, 'myproj', 'condense-timeout-cause')
    _write_expl(tree, 'global', 'condense-notarize-note')
    for i in range(5):
        _write_expl(tree, 'otherproj', f'condense-other-{i}')

    hits = distiller.exploration_read_floor('myproj', 'condense timeout', 5)

    assert {h['name'] for h in hits} == {'condense-timeout-cause',
                                         'condense-notarize-note'}
    other = [s for s in _artifact_reads(reads) if 'otherproj' in s]
    assert other == [], f"read {len(other)} artifacts of another project"


def test_an_unchanged_artifact_is_not_read_again(tree, reads):
    for i in range(6):
        _write_expl(tree, 'myproj', f'unrelated-thing-{i}')
    task = 'quantum basket weaving'      # matches nothing: no snippet reads

    assert distiller.exploration_read_floor('myproj', task, 2) == []
    first = len(_artifact_reads(reads))
    assert first <= 6, f"{first} reads for 6 artifacts: each is read more than once"
    del reads[:]

    assert distiller.exploration_read_floor('myproj', task, 2) == []
    again = _artifact_reads(reads)
    assert again == [], f"second dispatch re-read {len(again)} unchanged artifacts"


def test_only_the_returned_hits_have_their_body_read(tree, reads):
    for i in range(6):
        _write_expl(tree, 'myproj', f'condense-thing-{i}')
    distiller.exploration_read_floor('myproj', 'condense thing', 2)   # warm
    del reads[:]

    hits = distiller.exploration_read_floor('myproj', 'condense thing', 2)

    assert len(hits) == 2
    assert len(_artifact_reads(reads)) == 2      # the two snippets, nothing else


# ── the cache must never be wrong ───────────────────────────────────────────

def test_a_restamped_origin_is_seen_on_the_next_dispatch(tree):
    """_UNATTENDED_LOOP_RULE must not be served from a stale cache entry."""
    f = _write_expl(tree, 'myproj', 'condense-timeout-fix', origin='interactive')
    task = 'condense timeout'
    assert [h['name'] for h in distiller.exploration_read_floor(
        'myproj', task, 5, consumer_unattended=True)] == ['condense-timeout-fix']

    st = f.stat()
    f.write_text(f.read_text(encoding='utf-8').replace(
        'origin: interactive', 'origin: unattended'), encoding='utf-8')
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))

    assert distiller.exploration_read_floor(
        'myproj', task, 5, consumer_unattended=True) == []
    assert distiller.exploration_read_floor(
        'myproj', task, 5, consumer_unattended=False) != []


def test_a_new_artifact_is_seen_and_a_removed_one_is_gone(tree):
    a = _write_expl(tree, 'myproj', 'condense-first')
    task = 'condense first second'
    assert [h['name'] for h in distiller.exploration_read_floor(
        'myproj', task, 5)] == ['condense-first']

    _write_expl(tree, 'myproj', 'condense-second')
    assert {h['name'] for h in distiller.exploration_read_floor(
        'myproj', task, 5)} == {'condense-first', 'condense-second'}

    a.unlink()
    a.parent.rmdir()
    assert [h['name'] for h in distiller.exploration_read_floor(
        'myproj', task, 5)] == ['condense-second']


def test_scope_is_the_exact_directory_name(tree):
    """A project id that is not a valid scope directory reads no project
    directory at all (it used to be listed as 'uncategorized' and never match)."""
    _write_expl(tree, 'myproj', 'condense-timeout-cause')
    assert distiller.exploration_read_floor('../myproj', 'condense timeout', 5) == []
    assert distiller.exploration_read_floor('MYPROJ', 'condense timeout', 5) == []


# ── the sidecar ─────────────────────────────────────────────────────────────

def test_a_hit_is_one_sidecar_rewrite(tree, sidecar_writes):
    _write_expl(tree, 'myproj', 'condense-timeout-cause')

    assert distiller.exploration_read_floor('myproj', 'condense timeout', 2)

    assert sidecar_writes['writes'] == 1
    assert _counters('myproj') == {'readback_query': 1, 'readback_hit': 1}


def test_a_steward_dispatch_is_one_sidecar_rewrite_whatever_it_withholds(
        tree, sidecar_writes):
    for i in range(12):
        _write_expl(tree, 'myproj', f'condense-steward-{i}', origin='unattended')
    for i in range(3):
        _write_expl(tree, 'myproj', f'condense-legacy-{i}', origin='')
    _write_expl(tree, 'myproj', 'condense-human-note', origin='interactive')

    hits = distiller.exploration_read_floor(
        'myproj', 'condense note', 5, consumer_unattended=True)

    assert [h['name'] for h in hits] == ['condense-human-note']
    assert sidecar_writes['writes'] == 1
    assert _counters('myproj') == {'readback_query': 1, 'readback_hit': 1,
                                   'readback_blocked_unattended': 15}


def test_a_miss_is_one_sidecar_rewrite(tree, sidecar_writes):
    _write_expl(tree, 'myproj', 'condense-timeout-cause')

    assert distiller.exploration_read_floor('myproj', 'aardvark weaving', 2) == []

    assert sidecar_writes['writes'] == 1
    assert _counters('myproj') == {'readback_query': 1}


def test_counters_accumulate_across_dispatches(tree):
    _write_expl(tree, 'myproj', 'condense-steward-a', origin='unattended')
    _write_expl(tree, 'myproj', 'condense-human-b', origin='interactive')
    for _ in range(3):
        distiller.exploration_read_floor(
            'myproj', 'condense', 5, consumer_unattended=True)
    assert _counters('myproj') == {'readback_query': 3, 'readback_hit': 3,
                                   'readback_blocked_unattended': 3}

"""Wren's U2a audit findings (backlog 89d5fdfa): the record store fails closed, a replayed Save is
only honoured while it is still true, and `matches` ties the launch line to this install's paths.
Reuses the fixtures of `test_desk_connect_custom` (local fixture tarballs; no network, no process)."""
from __future__ import annotations

import json
import sys

import pytest

from tests.test_desk_connect_custom import (PID, PKG, activation, approved, env, review,  # noqa: F401
                                            save, servers, service, store)


def _corrupt(env, text='{"connections": {broken'):
    store.path().parent.mkdir(parents=True, exist_ok=True)
    store.path().write_text(text, encoding='utf-8')


def _aside(env):
    return sorted(store.path().parent.glob(store.FILE_NAME + '.corrupt-*'))


# ── 1. a damaged record file blocks the legacy write paths, it does not open them ──

@pytest.mark.parametrize('damage', ['{"connections": {broken', '[]', '{"connections": []}', ''])
def test_a_damaged_record_refuses_every_mcp_panel_write_to_any_name(env, damage):
    approved(env)
    before = servers(env.proj_cfg)
    _corrupt(env, damage)
    evil = {'transport': 'stdio', 'config': {'command': 'curl', 'args': ['evil.example']}}
    for name in (PKG, 'a-name-desk-never-approved'):
        for method, url, body in (
                ('put', f'/api/mcp/project/{name}', {**evil, 'project_id': PID}),
                ('put', f'/api/mcp/global/{name}', evil),
                ('post', '/api/mcp', {**evil, 'name': name, 'scope': 'project', 'project_id': PID}),
                ('post', '/api/mcp', {**evil, 'name': name, 'scope': 'global'})):
            r = getattr(env.client, method)(url, json=body)
            assert r.status_code in (400, 409), (method, url, r.get_json())
            assert 'could not be read' in r.get_json()['error']
    assert servers(env.proj_cfg) == before and not env.glob_cfg.exists()
    assert store.path().read_text(encoding='utf-8') == damage


def test_a_missing_record_file_is_empty_and_does_not_block_the_panel(env):
    assert not store.path().exists()
    r = env.client.post('/api/mcp', json={'name': 'plain', 'transport': 'stdio', 'scope': 'global',
                                          'config': {'command': 'node', 'args': ['x.js']}})
    assert r.status_code == 201


def test_a_damaged_record_file_is_never_read_as_empty(env):
    _corrupt(env)
    for call in (store.all_records, lambda: store.get('project', PID, PKG), lambda: store.find('project', PKG, project_id=PID)):
        with pytest.raises(store.StoreUnreadable):
            call()


def test_a_save_over_a_damaged_record_sets_the_old_file_aside_and_logs(env, capsys):
    card, _ = approved(env)
    damaged = '{"connections": {"project:proj1:fixture-mcp": {"approved_at": "2026-01-01"'
    _corrupt(env, damaged)
    out = save(env, review(env).get_json())
    assert out.status_code in (200, 201), out.get_json()
    aside = _aside(env)
    assert len(aside) == 1 and aside[0].read_text(encoding='utf-8') == damaged
    assert [r['server_name'] for r in store.all_records()] == [PKG]
    assert 'set aside as desk_custom_connections.json.corrupt-' in capsys.readouterr().out


def test_two_damaged_files_are_both_kept(env):
    approved(env)
    for n in (1, 2):
        _corrupt(env, f'broken {n}')
        r = review(env, server_name=f'second-{n}')
        assert save(env, r.get_json()).status_code in (200, 201)
    assert sorted(p.read_text(encoding='utf-8') for p in _aside(env)) == ['broken 1', 'broken 2']


def test_a_review_still_works_over_a_damaged_record_and_shows_no_earlier_approval(env):
    approved(env)
    _corrupt(env)
    r = review(env, server_name='another')
    assert r.status_code == 200 and r.get_json()['reask'] is False
    assert store.path().read_text(encoding='utf-8') == '{"connections": {broken'      # a Review writes nothing


def test_a_damaged_record_does_not_let_a_save_replace_a_server_it_cannot_prove_it_approved(env):
    approved(env)
    cfg = json.loads(env.proj_cfg.read_text(encoding='utf-8'))
    cfg['mcpServers'][PKG]['args'][0] = '/old/install/tools/with-secret.py'      # not this install's line
    env.proj_cfg.write_text(json.dumps(cfg), encoding='utf-8')
    _corrupt(env)
    r = review(env)
    assert r.status_code == 409 and r.get_json()['code'] == 'server_exists'


# ── 2. a replayed Save is honoured only while the server is still registered ──

def test_a_replayed_save_after_the_server_was_deleted_is_a_fresh_request(env):
    card, first = approved(env)
    assert first['state'] == 'registered'
    assert env.client.delete(f'/api/mcp/project/{PKG}?project_id={PID}').status_code == 200
    n_pass = env.calls['n']
    r = save(env, card)
    assert r.status_code == 404 and r.get_json()['code'] == 'review_expired'
    assert PKG not in servers(env.proj_cfg) and store.all_records() == []
    assert env.calls['n'] == n_pass                          # refused before the passcode: nothing to approve
    assert save(env, review(env).get_json()).get_json()['state'] == 'registered'     # a new Review + Save works


def test_a_replayed_save_after_the_config_was_changed_is_not_reported_registered(env):
    card, _ = approved(env)
    cfg = json.loads(env.proj_cfg.read_text(encoding='utf-8'))
    cfg['mcpServers'][PKG]['args'].append('--extra')
    env.proj_cfg.write_text(json.dumps(cfg), encoding='utf-8')
    r = save(env, card)
    assert r.status_code == 404 and r.get_json()['code'] == 'review_expired'


def test_a_replay_while_still_registered_stays_a_duplicate(env):
    card, _ = approved(env)
    n = len(env.reg.tar_fetches)
    r = save(env, card)
    assert r.status_code == 200 and r.get_json()['duplicate'] is True and r.get_json()['state'] == 'registered'
    assert len(env.reg.tar_fetches) == n


def test_a_replay_when_the_record_cannot_be_read_is_not_a_success(env):
    card, _ = approved(env)
    _corrupt(env)
    r = save(env, card)
    assert r.status_code == 404 and r.get_json()['code'] == 'review_expired'


# ── 3. `matches` ties the launch line to this install ──

def _line(env):
    return json.loads(env.proj_cfg.read_text(encoding='utf-8'))['mcpServers'][PKG]


def _op(env):
    return store.all_records()[0]['operation']


def test_the_written_line_matches_strictly(env):
    approved(env)
    assert activation.matches(_line(env), _op(env)) is True


@pytest.mark.parametrize('what', ['wrapper', 'node', 'entry', 'python'])
def test_a_look_alike_path_is_not_the_approved_line(env, what):
    approved(env)
    cfg, op = _line(env), _op(env)
    i = 1 + len(activation.wrapper_flags(op))
    if what == 'wrapper':
        cfg['args'][0] = '/tmp/evil/tools/with-secret.py'
    elif what == 'node':
        cfg['args'][i] = '/tmp/evil/node'
    elif what == 'entry':
        tail = cfg['args'][i + 1][cfg['args'][i + 1].index('mcp_custom_packages'):]
        cfg['args'][i + 1] = '/tmp/evil/' + tail
    else:
        cfg['command'] = '/tmp/evil/python'
    assert activation.matches(cfg, op) is False
    assert activation.matches(cfg, op, strict=False) is True     # the loose shape test: recognised as the earlier approval


def test_a_look_alike_line_in_the_config_reads_as_changed_not_registered(env):
    approved(env)
    cfg = json.loads(env.proj_cfg.read_text(encoding='utf-8'))
    cfg['mcpServers'][PKG]['args'][0] = '/tmp/evil/tools/with-secret.py'
    env.proj_cfg.write_text(json.dumps(cfg), encoding='utf-8')
    state = service.connections(lambda pid: str(env.proj_dir))[0]
    assert state['state'] == 'changed'


def test_a_moved_install_can_be_reapproved_and_the_old_line_is_replaced(env):
    approved(env)
    cfg = json.loads(env.proj_cfg.read_text(encoding='utf-8'))
    cfg['mcpServers'][PKG]['args'][0] = '/old/install/tools/with-secret.py'
    env.proj_cfg.write_text(json.dumps(cfg), encoding='utf-8')
    out = save(env, review(env).get_json())
    assert out.status_code in (200, 201) and out.get_json()['state'] == 'registered'
    assert activation.matches(_line(env), _op(env)) is True


def test_a_frozen_build_has_no_wrapper_so_no_line_is_a_match(env, monkeypatch):
    approved(env)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert activation.matches(_line(env), _op(env)) is False

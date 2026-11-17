"""tools/cli-version-check.py asks npm for its global prefix once per run, not once per
CLI and per helper (MC-1179 pass 12 second wave, TL10). No real npm is spawned."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture()
def cvc():
    path = Path(__file__).resolve().parent.parent / 'tools' / 'cli-version-check.py'
    spec = importlib.util.spec_from_file_location('cli_version_check_prefix_once', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_prefix_is_asked_once_across_package_dir_aside_dir_and_path_fallback(cvc, monkeypatch, tmp_path):
    calls = []

    def fake_npm(args, timeout=60):
        calls.append(list(args))
        return 0, str(tmp_path)

    monkeypatch.setattr(cvc, '_npm', fake_npm)
    clis = [cvc.CLI(n, '@v/' + n, ['npm', 'install', '-g', n]) for n in ('a', 'b', 'c', 'd')]
    for cli in clis:
        assert cvc.npm_package_dir(cli) is not None
        assert cvc.aside_dir_for(cli) is not None
        cvc._prefix_fallback(cli.name)
    assert calls == [['config', 'get', 'prefix']]


def test_a_failed_answer_is_also_remembered_for_the_run(cvc, monkeypatch):
    calls = []

    def fake_npm(args, timeout=60):
        calls.append(1)
        return 1, ''

    monkeypatch.setattr(cvc, '_npm', fake_npm)
    assert cvc._npm_prefix() is None and cvc._npm_prefix() is None
    assert len(calls) == 1

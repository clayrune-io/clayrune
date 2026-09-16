"""Regression guard: the installer scripts accept 'qwen' as a provider choice.

There is no existing harness that actually executes installer/install.sh or
install.ps1 (both need a real shell/npm environment this suite doesn't set
up), so this is a static check of the wiring added alongside QwenRuntime —
it would have caught the earlier state where only claude/codex/gemini were
listed in the choices, the accepted-answer case, and the npm package map.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SH = _ROOT / 'installer' / 'install.sh'
_PS1 = _ROOT / 'installer' / 'install.ps1'


def test_install_sh_lists_qwen_as_a_provider_choice():
    text = _SH.read_text(encoding='utf-8')
    assert '_PROVIDER_CHOICES="claude codex gemini qwen"' in text
    assert 'command -v qwen' in text
    assert 'qwen)   label="Qwen Code" ;;' in text
    assert 'claude|codex|gemini|qwen)' in text


def test_install_sh_maps_qwen_to_the_npm_package():
    text = _SH.read_text(encoding='utf-8')
    assert 'qwen)   _prov_pkg="@qwen-code/qwen-code" ;;' in text


def test_install_ps1_lists_qwen_as_a_provider_choice():
    text = _PS1.read_text(encoding='utf-8')
    assert "$ProviderChoices = @('claude', 'codex', 'gemini', 'qwen')" in text
    assert "qwen = 'Qwen Code'" in text


def test_install_ps1_maps_qwen_to_the_npm_package():
    text = _PS1.read_text(encoding='utf-8')
    assert "'qwen'  { '@qwen-code/qwen-code' }" in text

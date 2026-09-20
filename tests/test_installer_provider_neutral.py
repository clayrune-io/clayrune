"""Offline guards for vendor-neutral first-run installer behavior.

These tests deliberately inspect scripts rather than launching package managers
or provider CLIs. Live installation/authentication belongs to clean-VM release
gates and must never run in the ordinary test suite.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SH = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
PS1 = (ROOT / "installer" / "install.ps1").read_text(encoding="utf-8")
BAT = (ROOT / "installer" / "Clayrune-Setup.bat").read_text(encoding="utf-8")
CS = (ROOT / "installer" / "win-exe" / "ClayruneInstaller.cs").read_text(encoding="utf-8")
README = (ROOT / "installer" / "README.md").read_text(encoding="utf-8")


def test_no_provider_is_a_real_default_off_state():
    """Unset provider must reach Clayrune without silently becoming Claude."""
    assert '_default_prov="claude"' not in SH
    assert "else { 'claude' }" not in PS1
    assert "No provider selected yet" in SH
    assert "No provider selected yet" in PS1
    # The provider CLI preflight is nested under the explicit override guard.
    assert 'if [ -n "$CHOSEN_PROVIDER" ]; then' in SH
    assert "if ($ChosenProvider) {" in PS1


def test_explicit_provider_is_preserved_without_path_inference():
    assert 'CHOSEN_PROVIDER="${CLAYRUNE_PROVIDER:-}"' in SH
    assert "$ChosenProvider = $env:CLAYRUNE_PROVIDER" in PS1
    assert "default_provider" in SH and "default_provider" in PS1
    assert "if [ -n \"$CHOSEN_PROVIDER\" ]; then" in SH
    assert "if ($ChosenProvider) {" in PS1


def test_windows_wrappers_only_offer_claude_login_for_auth_exit_code():
    assert 'if not "%PSEXIT%"=="3" goto :install_failed' in BAT
    assert "bool offerLogin = (rc == RcNotLoggedIn);" in CS
    assert "offerLogin = true;" not in CS


def test_docs_describe_ui_owned_provider_onboarding():
    assert "Provider selection belongs to the first-run UI" in README
    assert "not require Claude" in " ".join(README.split())
    assert "A Codex," in README and "Qwen" in README

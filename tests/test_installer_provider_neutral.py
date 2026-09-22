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


def test_git_is_ensured_outside_the_provider_preflight():
    """git is a CORE prerequisite — a no-provider install still has to clone.

    Regression for the clean-VM run of 2026-09-20: once provider choice moved
    to the first-run UI, the default install skipped the whole
    `if ($ChosenProvider)` block — and that block held the only call that
    installed Git for Windows. STEP 1/5 then died on `git clone` with
    "The term 'git' is not recognized". install.sh never had the bug; it
    auto-installs git inside STEP 1. This pins the parity.
    """
    # Windows: a dedicated Setup-Git, invoked before the first git call.
    assert "function Setup-Git {" in PS1
    setup_at = PS1.index("if (-not (Get-BoolResult (Setup-Git)))")
    assert setup_at < PS1.index("& git clone $repoUrl $installDir")
    assert setup_at < PS1.index("& git -C $installDir pull --ff-only")

    # ...and it must sit OUTSIDE the explicit-provider preflight, which closes
    # at the "explicit CLAYRUNE_PROVIDER preflight" marker.
    assert setup_at > PS1.index("} # explicit CLAYRUNE_PROVIDER preflight")

    # winget must be pinned to the winget source here too, or a configured
    # msstore source can resolve Git.Git to a Store listing and hang. The
    # call is array-args (-Command/-Arguments), not an inline string: single-
    # dash winget flags like -e bind to Invoke-Native's own params by PREFIX
    # when passed inline, which is the 2026-09-20 bug this whole test guards.
    # Both Git.Git call sites were bitten by it, so pin both.
    for site_marker in ("function Setup-ClaudeRuntimeShell {", "function Setup-Git {"):
        site_at = PS1.index(site_marker)
        git_call = PS1.index("-Command winget -Arguments @(", site_at)
        call_args = PS1[git_call:git_call + 250]
        assert "'install', '--id', 'Git.Git'" in call_args
        assert "'--source', 'winget'" in call_args

    # POSIX side: same guarantee, already inside STEP 1.
    assert 'if ! command -v git >/dev/null 2>&1; then' in SH
    assert SH.index('could not auto-install git') < SH.index('git clone "$REPO_URL"')


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

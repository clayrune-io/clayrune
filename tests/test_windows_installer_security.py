from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "installer" / "win-exe" / "ClayruneInstaller.cs"
BUILD = ROOT / "installer" / "win-exe" / "build.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "build-windows-installer.yml"


def test_launcher_does_not_pipe_downloaded_code_to_powershell():
    source = SOURCE.read_text(encoding="utf-8")
    compact = " ".join(source.lower().split())
    assert "iwr \"" not in source.lower()
    assert "| iex" not in compact
    assert "executionpolicy bypass" not in compact
    assert "client.downloadfile" in source.lower()
    assert "sha256.create" in source.lower()
    assert "security error: installer bootstrap hash mismatch" in source.lower()


def test_build_pins_bootstrap_to_commit_and_hash():
    build = BUILD.read_text(encoding="utf-8").lower()
    assert "git -c $repo rev-parse head" in build
    assert "git -c $repo diff --quiet head -- installer/install.ps1" in build
    assert "get-filehash $bootstrap -algorithm sha256" in build
    assert "raw.githubusercontent.com/clayrune-io/clayrune/$commit/installer/install.ps1" in build


def test_signing_workflow_requires_oidc_and_verifies_signature():
    workflow = WORKFLOW.read_text(encoding="utf-8").lower()
    assert "id-token: write" in workflow
    assert "azure/login@v3" in workflow
    assert "azure/artifact-signing-action@v2" in workflow
    assert "get-authenticodesignature" in workflow
    assert "timestamp.acs.microsoft.com" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "gh release" not in workflow

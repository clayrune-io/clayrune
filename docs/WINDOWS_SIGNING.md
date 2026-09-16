# Windows installer signing

Clayrune's distributable Windows installer is built and signed by the manual
GitHub Actions workflow **Build signed Windows installer**. Local builds are
deliberately unsigned and must not replace a public release asset.

## One-time Microsoft setup

1. Create a Microsoft Artifact Signing account, complete identity validation,
   and create a public-trust certificate profile.
2. Create an Azure workload identity for GitHub Actions, add a federated
   credential restricted to `clayrune-io/clayrune`, and grant it the
   **Artifact Signing Certificate Profile Signer** role on the certificate
   profile. OIDC is used; no client secret is stored in GitHub.
3. Add these GitHub Actions secrets:
   - `AZURE_CLIENT_ID`
   - `AZURE_TENANT_ID`
   - `AZURE_SUBSCRIPTION_ID`
4. Add these GitHub Actions variables:
   - `AZURE_ARTIFACT_SIGNING_ENDPOINT`
   - `AZURE_ARTIFACT_SIGNING_ACCOUNT_NAME`
   - `AZURE_ARTIFACT_SIGNING_CERTIFICATE_PROFILE`

The endpoint must match the Azure region where the signing account and profile
were created.

## Build and release

1. Run **Build signed Windows installer** from the Actions tab.
2. Download the `Clayrune-Windows-signed` workflow artifact.
3. On a clean Windows VM, confirm:

   ```powershell
   Get-AuthenticodeSignature .\Clayrune-Installer.exe |
     Format-List Status,SignerCertificate,TimeStamperCertificate
   ```

   `Status` must be `Valid`, and `TimeStamperCertificate` must be present.
4. Scan both files with current Microsoft Defender definitions and perform a
   complete install from a clean snapshot.
5. Only then replace the GitHub release assets
   `Clayrune-Installer.exe` and `Clayrune-Windows.zip`.

The workflow uploads artifacts but never edits a GitHub release. Publishing is
kept separate so a failed signature or incomplete VM test cannot silently
replace the public installer.

## Security model

- The EXE downloads `install.ps1` from the exact Git commit recorded at build
  time, rather than from a mutable branch.
- The build embeds that script's SHA-256. A mismatch is a hard failure and the
  downloaded script is not executed.
- PowerShell receives a local file through `-File`; downloaded text is never
  piped to `Invoke-Expression`, and execution-policy bypass is not used.
- Artifact Signing applies a SHA-256 Authenticode signature plus an RFC 3161
  timestamp, preserving signature validity after certificate renewal.

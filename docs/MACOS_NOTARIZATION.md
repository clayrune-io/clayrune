# macOS code-signing & notarization

How we make the Mac download open **without** the Gatekeeper
"Apple could not verify Clayrune is free of malware… Move to Trash" block.

That block appears because browsers attach a `com.apple.quarantine` flag to
downloads, and macOS refuses to launch a quarantined app that isn't signed by
a known developer and notarized by Apple. The fix is to sign the app with an
Apple **Developer ID** certificate and run it through Apple's notary service.

This reverses the earlier "no paid code-signing" stance **for the Mac `.app`
only** — the closed-source Rust `mc-tunnel` moat is unaffected.

---

## TL;DR (per release)

```bash
pyinstaller installer/build-macos.spec --noconfirm     # builds dist/Clayrune.app
tools/notarize-macos.sh                        # -> Clayrune-macOS.zip + .build.json
```

Then **upload BOTH files to the GitHub release.** This is not optional
polish — it is the release itself:

```bash
gh release upload vX.Y.Z Clayrune-macOS.zip Clayrune-macOS.build.json --clobber
```

The website's macOS button is a fixed URL,
`https://github.com/clayrune-io/clayrune/releases/latest/download/Clayrune-macOS.zip`,
which GitHub resolves against whatever release is *latest*. **Publish a release
without that asset and the download button 404s for every Mac visitor**, with
nothing on our side reporting a problem. That is what v2.0.1 and v2.0.2 did
(caught 2026-08-29 by a user, not by us).

`Clayrune-macOS.build.json` is newer (added 2026-09-14) and just as easy to
forget: it's how a running frozen `.app`'s own **Check for updates** menu item
tells this release apart from the last one. We re-upload the zip under the
same tag whenever a same-day build needs a fix (v2.3.0 was replaced 3 times in
one day) — the tag/version alone can't detect that, so the frozen app compares
its own bundled `build_info.json` (baked in by `build-macos.spec`) against
this published manifest. Skip uploading it and Mac users just never see an
update is available; nothing errors. See `mc/blueprints/system_routes.py`
`_frozen_update_status`.

CI (`build-macos.yml`) attaches `Clayrune-macOS-unsigned.zip` — a deliberately
different name, so an unsigned build can never end up behind the website's
button. It is a build check, not a shipping artifact.

---

## One-time setup (do this once, ever)

1. **Apple Developer Program membership** — $99/yr at
   [developer.apple.com](https://developer.apple.com). Enrollment is usually
   approved within 24–48h. This gives you a **Team ID** (`<TEAMID>`).

2. **Xcode Command Line Tools** (you do *not* need the full ~15 GB Xcode app):
   ```bash
   xcode-select --install
   ```

3. **A "Developer ID Application" certificate** in your login keychain.
   - Open **Keychain Access** → menu bar **Certificate Assistant → Request a
     Certificate From a Certificate Authority…** → enter your email, leave CA
     Email blank, choose **Saved to disk**.
   - At **developer.apple.com/account → Certificates → +**, pick
     **Developer ID Application** (under the *Software* group — **not** "Apple
     Development", which is the default and cannot notarize), upload the CSR,
     download the `.cer`, and double-click it to install.
   - Confirm:
     ```bash
     security find-identity -v -p codesigning
     # want: "Developer ID Application: <Your Name> (<TEAMID>)"
     ```

4. **Store a notarytool credential profile** named `clayrune-notary`. First make
   an **app-specific password** at [appleid.apple.com](https://appleid.apple.com)
   → Sign-In & Security → App-Specific Passwords (this is *not* your Apple ID
   password). Then:
   ```bash
   xcrun notarytool store-credentials "clayrune-notary" \
     --apple-id "you@example.com" \
     --team-id "<TEAMID>" \
     --password "xxxx-xxxx-xxxx-xxxx"
   ```
   The password lives in your login keychain after this — never in the repo.

---

## What the script does

`tools/notarize-macos.sh` runs these steps and fails loudly if any check trips:

1. Writes a hardened-runtime entitlements plist (PyInstaller bundles need
   `allow-unsigned-executable-memory`, `disable-library-validation`, `allow-jit`
   or the app won't launch once signed).
2. `codesign --force --deep --options runtime --timestamp` with the Developer ID
   identity.
3. Verifies via `codesign -dvv` that the **Authority** is the Developer ID cert
   (not the ad-hoc signature PyInstaller leaves behind).
4. Zips with `ditto`, submits to the notary service with `notarytool --wait`,
   and aborts + prints the log if the status isn't `Accepted`.
5. `stapler staple`s the ticket into the `.app` and confirms `spctl` reports
   `source=Notarized Developer ID`.
6. Re-zips the **stapled** app to `Clayrune-macOS.zip`.
7. Reads `build_info.json` back out of the just-built `.app`, hashes the zip,
   and writes `Clayrune-macOS.build.json` (commit, built_at, zip name, sha256,
   size) — the manifest the frozen app's own update check compares itself
   against.

---

## Gotchas (learned the hard way, 2026-06-04)

- **Wrong cert type.** "Apple Development" is the portal's default and is
  useless here — you need **Developer ID Application**.
- **`codesign --verify` lies.** It passes on PyInstaller's ad-hoc signature, so
  a build that was never signed looks "valid on disk". Always check the
  `Authority=` line from `codesign -dvv` instead.
- **Entitlements plist corruption.** A stray character on the `<!DOCTYPE>` line
  produces `invalid length in entitlement blob`. The script omits the DOCTYPE
  entirely (it's optional) and `plutil -lint`s the file before signing.
- **Multiline paste eats characters.** Running `codesign` as one long line
  avoids the `\`-continuation paste bug that turned `codesign` into `odesign`.
- **The submitted zip is stale.** Stapling happens *after* notarization, so the
  distributable must be re-zipped from the stapled `.app`.

---

## Deferred: folding this into CI

Right now signing is a manual post-build step. To make every release ship
notarized automatically, `build-macos.yml` would need to, on a macОS runner:

1. Import the Developer ID cert from a base64-encoded `.p12` GitHub secret into
   a temporary keychain (`security create-keychain` / `import` / `unlock`).
2. Run the same `codesign` + `notarytool` + `stapler` steps (notarytool creds
   as `APPLE_ID` / `TEAM_ID` / `APP_SPECIFIC_PASSWORD` secrets).

Secrets needed: `MACOS_CERT_P12_BASE64`, `MACOS_CERT_PASSWORD`,
`MACOS_NOTARY_APPLE_ID`, `MACOS_NOTARY_TEAM_ID` (`<TEAMID>`),
`MACOS_NOTARY_PASSWORD`. Tracked as a follow-up; not yet wired.

---

## Deferred: in-app "Install update" (v2, design only — no code)

Today "Check for updates" (v1, shipped 2026-09-14) gets a Mac user as far as
a **Download** button that opens `Clayrune-macOS.zip`'s URL in the system
browser — they still manually quit, unzip, and drag-replace the app in
`Applications`. A Sparkle-style **Install update** would download, verify,
swap, and relaunch in place. Sketch, not a commitment:

1. **Download** the zip from `download_url` (already returned by
   `/api/system/update/status`) to a temp dir, showing progress.
2. **Verify before touching anything on disk that matters**: sha256 against
   `Clayrune-macOS.build.json`'s `sha256` field, *and* `codesign --verify
   --deep --strict` + `spctl -a -t exec` against the unzipped `.app` to
   confirm it's still the notarized artifact we published — the manifest
   alone only proves the bytes weren't corrupted in transit, not that they're
   ours. A checksum match on a tampered manifest is not a security check.
3. **Swap**: move the running `/Applications/Clayrune.app` aside, move the
   new one in, relaunch, delete the old one only after the new one starts
   clean.
4. **Relaunch** via `open` / re-exec, mirroring the existing git-path
   `restart_recommended` flow.

Risks worth resolving *before* writing code, not after:

- **Gatekeeper translocation.** A `.app` launched from a quarantined location
  (e.g. still under `~/Downloads` or a temp dir) runs from a randomized
  read-only path (App Translocation) — self-replacing logic that assumes its
  own `sys.executable` path is `/Applications/Clayrune.app` will silently
  operate on the wrong copy, or fail to write, if the *new* download is
  translocated at the moment we try to launch it pre-swap.
- **Quarantine flag propagation.** The freshly downloaded zip carries
  `com.apple.quarantine`; unzipping preserves it onto the `.app`. Our own
  in-app installer removing that flag ourselves (`xattr -d
  com.apple.quarantine`) to make the swap seamless would defeat the exact
  Gatekeeper check this whole notarization pipeline exists to satisfy — don't.
  Let Gatekeeper re-evaluate the swapped-in app normally (it will pass; it's
  notarized) rather than stripping the flag to skip that step.
- **Permissions**: writing into `/Applications` requires the invoking user own
  or have write access to that specific `.app` bundle (true for a
  single-user install, not guaranteed if it was installed by another admin
  account or via MDM) — detect and fall back to the v1 manual-download flow
  rather than failing an in-place write silently.
- **A crash mid-swap must not leave "no Clayrune.app that launches" as a
  possible end state.** The old app must not be deleted until the new one has
  been confirmed to start.

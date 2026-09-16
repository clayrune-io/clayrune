#!/usr/bin/env bash
#
# notarize-macos.sh — sign, notarize, staple, and zip Clayrune.app for release.
#
# Turns the ten-command Apple signing dance into one command. Run it on a Mac
# after building the app:
#
#     pyinstaller installer/build-macos.spec --noconfirm     # produces dist/Clayrune.app
#     tools/notarize-macos.sh                        # -> Clayrune-macOS.zip + .build.json
#
# The output Clayrune-macOS.zip is the notarized, Gatekeeper-clean
# artifact to upload to the website / GitHub release. It is a drop-in
# replacement for the UNSIGNED zip that .github/workflows/build-macos.yml
# currently produces and auto-attaches to releases — always replace that one.
#
# Also produces Clayrune-macOS.build.json — upload it alongside the zip.
# It's how a frozen (non-git) .app checks itself for updates: see
# docs/MACOS_NOTARIZATION.md and mc/blueprints/system_routes.py
# _frozen_update_status. Skipping it just means Mac users' in-app update
# check can't tell this release apart from the last one.
#
# ── One-time setup (do this once, ever) ─────────────────────────────────────
# Full walkthrough: docs/MACOS_NOTARIZATION.md. The short version:
#   1. Apple Developer Program membership ($99/yr).
#   2. A "Developer ID Application" certificate in your login keychain
#      (Keychain Access CSR -> developer.apple.com -> download -> double-click).
#   3. Store a notarytool credential profile (name it whatever you like):
#        xcrun notarytool store-credentials "my-notary-profile" \
#          --apple-id "you@example.com" \
#          --team-id "YOURTEAMID" \
#          --password "<app-specific-password from appleid.apple.com>"
#   4. Tell this script who you are. Your signing identity is specific to YOUR
#      Apple account, so it is not hardcoded here — put it in tools/signing.env
#      (gitignored), which this script sources automatically:
#        CLAYRUNE_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
#        CLAYRUNE_NOTARY_PROFILE="my-notary-profile"
#      Or export the same vars in your shell.
#
# Nothing secret lives in this script or in signing.env. The app-specific
# password is stored in your login keychain by store-credentials, not here.
#
# Usage:  tools/notarize-macos.sh [path/to/Clayrune.app]   (default: dist/Clayrune.app)

set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────────
# Per-developer settings come from tools/signing.env (gitignored) or the env.
_SIGNING_ENV="$(dirname "$0")/signing.env"
# shellcheck source=/dev/null
[ -f "$_SIGNING_ENV" ] && . "$_SIGNING_ENV"

APP="${1:-dist/Clayrune.app}"
IDENTITY="${CLAYRUNE_SIGN_IDENTITY:-}"
PROFILE="${CLAYRUNE_NOTARY_PROFILE:-clayrune-notary}"
OUT_ZIP="${CLAYRUNE_OUT_ZIP:-Clayrune-macOS.zip}"

# Notary auth: an App Store Connect API key when all three vars are set
# (CLAYRUNE_NOTARY_KEY = path to the AuthKey_*.p8, CLAYRUNE_NOTARY_KEY_ID,
# CLAYRUNE_NOTARY_ISSUER), else the keychain profile above. The key route needs
# no app-specific password and no unlocked login keychain, so a keychain reset
# or a headless session can't break notarization. Values live in signing.env
# (gitignored) or the env; the .p8 itself never belongs in the repo.
if [ -n "${CLAYRUNE_NOTARY_KEY:-}" ] && [ -n "${CLAYRUNE_NOTARY_KEY_ID:-}" ] && [ -n "${CLAYRUNE_NOTARY_ISSUER:-}" ]; then
  NOTARY_AUTH=(--key "$CLAYRUNE_NOTARY_KEY" --key-id "$CLAYRUNE_NOTARY_KEY_ID" --issuer "$CLAYRUNE_NOTARY_ISSUER")
else
  NOTARY_AUTH=(--keychain-profile "$PROFILE")
fi

# Note: die() is defined below, so this preflight prints its own error.
if [ -z "$IDENTITY" ]; then
  printf '\n\033[1;31mERROR:\033[0m %s\n' "No signing identity configured.
Set CLAYRUNE_SIGN_IDENTITY in tools/signing.env or your environment, e.g.
  CLAYRUNE_SIGN_IDENTITY=\"Developer ID Application: Your Name (TEAMID)\"
List the identities in your keychain with:
  security find-identity -v -p codesigning
See docs/MACOS_NOTARIZATION.md." >&2
  exit 1
fi

say() { printf '\n\033[1;36m==>\033[0m %s\n' "$1"; }
die() { printf '\n\033[1;31mERROR:\033[0m %s\n' "$1" >&2; exit 1; }

# ── Preflight ───────────────────────────────────────────────────────────────
[ -d "$APP" ] || die "App not found: $APP
Build it first:  pyinstaller installer/build-macos.spec --noconfirm"

command -v xcrun >/dev/null 2>&1 || die "Xcode Command Line Tools missing. Run: xcode-select --install"

if ! grep -qF "$IDENTITY" <<<"$(security find-identity -v -p codesigning)"; then
  die "Signing identity not found in your keychain:
  $IDENTITY
Set up the Developer ID cert first — see docs/MACOS_NOTARIZATION.md"
fi

# ── 1. Entitlements (PyInstaller needs these under the hardened runtime) ─────
# No <!DOCTYPE> line on purpose — it's not required and a stray character there
# silently corrupts the plist (codesign: "invalid length in entitlement blob").
say "Writing entitlements"
ENTITLEMENTS="$(mktemp -t clayrune-entitlements)"
cat > "$ENTITLEMENTS" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
  <key>com.apple.security.cs.disable-library-validation</key><true/>
  <key>com.apple.security.cs.allow-jit</key><true/>
</dict></plist>
PLIST
plutil -lint "$ENTITLEMENTS" >/dev/null || die "Generated entitlements plist is invalid (should never happen)."

# ── 2. Sign (deep, hardened runtime, secure timestamp) ──────────────────────
say "Signing $APP"
codesign --force --deep --options runtime --timestamp \
  --entitlements "$ENTITLEMENTS" \
  --sign "$IDENTITY" \
  "$APP"

# Confirm it's REALLY signed with the Developer ID identity. `codesign --verify`
# alone is not enough — it passes on PyInstaller's pre-existing ad-hoc signature
# and gives a false "valid on disk". The Authority line is the real proof.
say "Verifying signature"
SIG_INFO="$(codesign -dvv "$APP" 2>&1)"
grep -qF "Authority=$IDENTITY" <<<"$SIG_INFO" \
  || die "App is not signed with the Developer ID identity after signing."
codesign --verify --strict "$APP" || die "codesign --verify failed."

# ── 3. Notarize ─────────────────────────────────────────────────────────────
say "Zipping for notarization"
SUBMIT_ZIP="$(mktemp -d)/Clayrune-submit.zip"
ditto -c -k --keepParent "$APP" "$SUBMIT_ZIP"

say "Submitting to Apple's notary service (waits ~2-5 min)…"
SUBMIT_OUT="$(xcrun notarytool submit "$SUBMIT_ZIP" "${NOTARY_AUTH[@]}" --wait 2>&1)" || true
echo "$SUBMIT_OUT"
if ! grep -q "status: Accepted" <<<"$SUBMIT_OUT"; then
  SUB_ID="$(grep -m1 '  id:' <<<"$SUBMIT_OUT" | awk '{print $2}')"
  [ -n "${SUB_ID:-}" ] && xcrun notarytool log "$SUB_ID" "${NOTARY_AUTH[@]}" || true
  die "Notarization failed (see log above)."
fi

# ── 4. Staple the ticket + final Gatekeeper check ───────────────────────────
say "Stapling ticket"
xcrun stapler staple "$APP"
GK_INFO="$(spctl -a -t exec -vvv "$APP" 2>&1)"
grep -q "source=Notarized Developer ID" <<<"$GK_INFO" \
  || die "Gatekeeper assessment did not report 'Notarized Developer ID'."

# ── 5. Zip the STAPLED app + safe uninstaller for distribution ──────────────
# (the zip we submitted is stale — the ticket got stapled into the .app after.)
say "Zipping notarized app -> $OUT_ZIP"
rm -f "$OUT_ZIP"
UNINSTALLER="installer/uninstall-macos.command"
[ -f "$UNINSTALLER" ] || die "$UNINSTALLER missing."
PACKAGE_DIR="$(mktemp -d)"
ditto "$APP" "$PACKAGE_DIR/Clayrune.app"
cp "$UNINSTALLER" "$PACKAGE_DIR/Uninstall Clayrune.command"
chmod +x "$PACKAGE_DIR/Uninstall Clayrune.command"
case "$OUT_ZIP" in
  /*) OUT_ZIP_TARGET="$OUT_ZIP" ;;
  *)  OUT_ZIP_TARGET="$(pwd)/$OUT_ZIP" ;;
esac
(cd "$PACKAGE_DIR" && ditto -c -k --sequesterRsrc . "$OUT_ZIP_TARGET")
rm -rf "$PACKAGE_DIR"
VERIFY_DIR="$(mktemp -d)"
ditto -x -k "$OUT_ZIP_TARGET" "$VERIFY_DIR"
[ -d "$VERIFY_DIR/Clayrune.app" ] || die "Packaged archive is missing Clayrune.app."
[ -x "$VERIFY_DIR/Uninstall Clayrune.command" ] || die "Packaged archive is missing the executable uninstaller."
rm -rf "$VERIFY_DIR"

# ── 6. Publish the build manifest ────────────────────────────────────────────
# The frozen app's own update check (system_routes.py _frozen_update_status)
# compares its bundled commit against THIS file, published as a release
# asset — never against the release tag/version, which gets re-uploaded under
# the same tag when a build needs a same-day fix. We read the identity back
# OUT of the app we just built (build_info.json, baked in by
# installer/build-macos.spec) rather than recomputing it, so the published
# manifest and the app's own belief about itself can never drift apart.
say "Writing build manifest"
BUILD_INFO_SRC="$(find "$APP" -name build_info.json -print -quit)"
[ -n "$BUILD_INFO_SRC" ] || die "build_info.json not found inside $APP — rebuild with the current installer/build-macos.spec."
BUILD_JSON="${OUT_ZIP%.zip}.build.json"
SHA256="$(shasum -a 256 "$OUT_ZIP" | awk '{print $1}')"
SIZE="$(stat -f%z "$OUT_ZIP" 2>/dev/null || stat -c%s "$OUT_ZIP")"
python3 - "$BUILD_INFO_SRC" "$(basename "$OUT_ZIP")" "$SHA256" "$SIZE" "$BUILD_JSON" <<'PY'
import json, sys
src, zip_name, sha256, size, out = sys.argv[1:6]
with open(src, encoding='utf-8') as f:
    info = json.load(f)
manifest = {
    'commit': info.get('commit', ''),
    'commit_full': info.get('commit_full', ''),
    'built_at': info.get('built_at', ''),
    'zip_name': zip_name,
    'sha256': sha256,
    'size': int(size),
}
with open(out, 'w', encoding='utf-8') as f:
    json.dump(manifest, f, indent=2)
PY

say "Done — this is Gatekeeper-clean. Upload BOTH to the release:"
ls -lh "$OUT_ZIP" "$BUILD_JSON"

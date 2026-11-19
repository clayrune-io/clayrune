#!/usr/bin/env sh
# ============================================================
#  Clayrune Installer (macOS)
#
#  Double-click this file in Finder to install Clayrune on your
#  Mac. Wraps install.sh so users don't need to use Terminal
#  manually.
#
#  Note: macOS Gatekeeper will block this file the first time
#  you double-click it ("unidentified developer"). Right-click
#  it and choose "Open" to bypass the warning. Once allowed
#  once, future double-clicks just work.
# ============================================================

set -e

cat <<'EOF'

============================================================
  Clayrune Installer
============================================================

This will install Clayrune on this Mac.

It will:
  1. Clone Clayrune to ~/Clayrune
  2. Set up Python dependencies and a launcher in ~/Applications/
  3. Open the dashboard to choose and set up your AI provider

Estimated time: 5-10 minutes.
Disk space: about 500 MB.

You can audit what runs by reading:
  https://raw.githubusercontent.com/clayrune-io/clayrune/master/installer/install.sh

EOF

printf 'Press Enter to continue, or Ctrl+C to abort... '
read _ < /dev/tty || true
echo

EXIT_CODE=0
curl -sSL https://raw.githubusercontent.com/clayrune-io/clayrune/master/installer/install.sh |
  sh || EXIT_CODE=$?

echo
echo "============================================================"
if [ "$EXIT_CODE" = "0" ]; then
  echo "  Done."
  echo
  echo "  If installation succeeded, you'll find Clayrune in your"
  echo "  Applications folder. Double-click it any time to launch."
else
  echo "  Installer exited with error code $EXIT_CODE."
  echo
  echo "  The output above shows what went wrong. To retry, just"
  echo "  double-click this file again — the installer is idempotent"
  echo "  and will pick up where it left off."
fi
echo "============================================================"
echo

printf 'Press Enter to close this window... '
read _ < /dev/tty || true
exit "$EXIT_CODE"

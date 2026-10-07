#!/bin/sh
# Opt-in: install the merge review gate's pre-merge-commit hook (MC-1075) into
# this clone. NOT part of tools/install-hooks.sh and never run automatically.
#
#   tools/install-merge-review-hook.sh              install
#   tools/install-merge-review-hook.sh --uninstall  remove (only our own hook)
#
# Refuses to replace a pre-merge-commit hook it did not write.
set -e

root=$(git rev-parse --show-toplevel)
hooks=$(git -C "$root" rev-parse --git-path hooks)
case "$hooks" in
  /*|[A-Za-z]:*) : ;;
  *) hooks="$root/$hooks" ;;
esac
dest="$hooks/pre-merge-commit"
marker="Clayrune merge review gate (MC-1075)"

if [ "$1" = "--uninstall" ]; then
  if [ -f "$dest" ] && grep -q "$marker" "$dest"; then
    rm "$dest"
    echo "Removed $dest"
  else
    echo "No Clayrune merge review hook at $dest; nothing removed."
  fi
  exit 0
fi

if [ -f "$dest" ] && ! grep -q "$marker" "$dest"; then
  echo "Refusing: $dest exists and is not the Clayrune merge review hook." >&2
  exit 1
fi

mkdir -p "$hooks"
check="$root/tools/merge-review-check.py"
sed "s|__CHECK_SCRIPT__|$check|" "$root/tools/git-hooks/pre-merge-commit" > "$dest"
chmod +x "$dest" 2>/dev/null || true

echo "Installed merge review gate -> $dest"
echo "Active only for projects with merge_requires_review on."
echo "Not covered: fast-forward merges and 'git merge --no-verify'."

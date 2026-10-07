#!/bin/sh
# Opt-in: install the merge review gate's git hooks (MC-1075) into this clone:
# pre-merge-commit (the merge itself) and commit-msg (a commit that concludes a
# merge pre-merge-commit already rejected). NOT part of tools/install-hooks.sh
# and never run automatically.
#
#   tools/install-merge-review-hook.sh              install
#   tools/install-merge-review-hook.sh --uninstall  remove (only our own hooks)
#
# Refuses to replace a hook of either name that it did not write. Hooks live in
# the common git dir, so linked worktrees of this clone share them.
set -e

root=$(git rev-parse --show-toplevel)
hooks=$(git -C "$root" rev-parse --git-path hooks)
case "$hooks" in
  /*|[A-Za-z]:*) : ;;
  *) hooks="$root/$hooks" ;;
esac
marker="Clayrune merge review gate (MC-1075)"
names="pre-merge-commit commit-msg"

if [ "$1" = "--uninstall" ]; then
  for name in $names; do
    dest="$hooks/$name"
    if [ -f "$dest" ] && grep -q "$marker" "$dest"; then
      rm "$dest"
      echo "Removed $dest"
    else
      echo "No Clayrune merge review hook at $dest; nothing removed."
    fi
  done
  exit 0
fi

for name in $names; do
  dest="$hooks/$name"
  if [ -f "$dest" ] && ! grep -q "$marker" "$dest"; then
    echo "Refusing: $dest exists and is not the Clayrune merge review hook." >&2
    exit 1
  fi
done

mkdir -p "$hooks"
check="$root/tools/merge-review-check.py"
for name in $names; do
  dest="$hooks/$name"
  sed "s|__CHECK_SCRIPT__|$check|" "$root/tools/git-hooks/$name" > "$dest"
  chmod +x "$dest" 2>/dev/null || true
  echo "Installed merge review gate -> $dest"
done
echo "Active only for projects with merge_requires_review on."
echo "Not covered: fast-forward merges, 'git merge --no-verify', 'git commit --no-verify'."

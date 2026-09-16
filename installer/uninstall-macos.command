#!/bin/sh
# Safe Clayrune uninstaller for macOS.
#
# Default: remove Clayrune and its launchers while preserving provider CLIs,
# ~/.claude, ~/.clayrune, external projects, frozen-app data, and a copy of
# checkout-backed data under ~/.clayrune/uninstall-archives/.
#
# --purge-data: additionally remove Clayrune-owned state after a stronger
# confirmation. Provider CLIs and ~/.claude are never removed.

set -eu

purge_data=0
assume_yes=0
dry_run=0
install_dir=""
app_path=""
user_home="${HOME:-}"

usage() {
  cat <<'EOF'
Usage: uninstall-macos.command [options]

  --install-dir PATH  Source-checkout install path (default: ~/Clayrune)
  --app-path PATH     Additional Clayrune.app path to remove
  --purge-data        Also remove Clayrune-owned settings, credentials,
                      browser profiles, backups, and frozen-app data
  --yes               Skip the typed confirmation (for automation)
  --dry-run           Show exact actions without changing anything
  --home PATH         Override user home (support/testing)
  --help              Show this help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-dir)
      [ "$#" -ge 2 ] || { echo "--install-dir needs a path" >&2; exit 2; }
      install_dir=$2; shift 2 ;;
    --app-path)
      [ "$#" -ge 2 ] || { echo "--app-path needs a path" >&2; exit 2; }
      app_path=$2; shift 2 ;;
    --purge-data) purge_data=1; shift ;;
    --yes) assume_yes=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    --home)
      [ "$#" -ge 2 ] || { echo "--home needs a path" >&2; exit 2; }
      user_home=$2; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[ -n "$user_home" ] || { echo "Could not resolve the user home." >&2; exit 2; }

canonical_path() {
  _path=$1
  case "$_path" in
    /*) ;;
    *) _path="$(pwd)/$_path" ;;
  esac
  _parent=$(dirname "$_path")
  _base=$(basename "$_path")
  if [ -d "$_path" ]; then
    (cd -P "$_path" 2>/dev/null && pwd)
  elif [ -d "$_parent" ]; then
    printf '%s/%s\n' "$(cd -P "$_parent" 2>/dev/null && pwd)" "$_base"
  else
    printf '%s\n' "$_path"
  fi
}

user_home=$(canonical_path "$user_home")
real_home=$(canonical_path "${HOME:-$user_home}")
is_current_home=0
[ "$user_home" != "$real_home" ] || is_current_home=1
if [ -z "$install_dir" ]; then
  install_dir=${CLAYRUNE_HOME:-"$user_home/Clayrune"}
fi
install_dir=$(canonical_path "$install_dir")

assert_safe_recursive_target() {
  _target=$(canonical_path "$1")
  _label=$2
  case "$_target" in
    ''|'/') echo "Refusing unsafe $_label path: $_target" >&2; exit 2 ;;
  esac
  if [ "$_target" = "$user_home" ]; then
    echo "Refusing to remove the user home: $_target" >&2
    exit 2
  fi
}

assert_safe_recursive_target "$install_dir" "install directory"

state_dir="$user_home/.clayrune"
frozen_data_dir=${MC_DATA_DIR:-"$user_home/MissionControl"}
frozen_data_dir=$(canonical_path "$frozen_data_dir")
system_app="/Applications/Clayrune.app"
user_app="$user_home/Applications/Clayrune.app"
launcher="$user_home/Applications/Clayrune.command"
uninstall_launcher="$user_home/Applications/Uninstall Clayrune.command"

# Release archives place this script beside Clayrune.app. Detect that layout
# so the uninstaller still works before the user moves either file. An explicit
# --app-path is supported for any other location.
if [ -n "$app_path" ]; then
  app_path=$(canonical_path "$app_path")
else
  script_dir=$(canonical_path "$(dirname "$0")")
  if [ -d "$script_dir/Clayrune.app" ]; then
    app_path="$script_dir/Clayrune.app"
  fi
fi
if [ -n "$app_path" ]; then
  assert_safe_recursive_target "$app_path" "app path"
  if [ ! -f "$app_path/Contents/MacOS/Clayrune" ]; then
    echo "Refusing to remove '$app_path': it is not recognizably Clayrune.app." >&2
    exit 2
  fi
fi

install_exists=0
if [ -e "$install_dir" ]; then
  install_exists=1
  if [ ! -f "$install_dir/server.py" ] || [ ! -d "$install_dir/installer" ]; then
    echo "Refusing to remove '$install_dir': it is not recognizably a Clayrune checkout." >&2
    exit 2
  fi
fi

action() {
  if [ "$dry_run" -eq 1 ]; then printf '[dry run] %s\n' "$1"; else printf '%s\n' "$1"; fi
}

remove_file() {
  _path=$1; _label=$2
  [ -e "$_path" ] || [ -L "$_path" ] || return 0
  action "Remove $_label: $_path"
  [ "$dry_run" -eq 1 ] || rm -f "$_path"
}

remove_tree() {
  _path=$1; _label=$2
  [ -e "$_path" ] || return 0
  assert_safe_recursive_target "$_path" "$_label"
  action "Remove $_label: $_path"
  [ "$dry_run" -eq 1 ] || rm -rf -- "$_path"
}

remove_app() {
  _path=$1; _label=$2
  [ -e "$_path" ] || return 0
  if [ ! -f "$_path/Contents/MacOS/Clayrune" ]; then
    printf "WARNING: %s is not recognizably Clayrune.app; leaving it in place.\n" "$_path" >&2
    return 0
  fi
  remove_tree "$_path" "$_label"
}

printf '\nClayrune Uninstaller\n'
printf '  Application: %s\n' "$install_dir"
if [ "$purge_data" -eq 1 ]; then
  printf '  Mode:        remove app + Clayrune-owned data\n'
else
  printf '  Mode:        remove app, preserve data\n'
fi
printf '\nAlways preserved:\n'
printf '  - AI provider CLIs, Node.js, Python, and Git\n'
printf '  - ~/.claude (provider authentication, transcripts, and user customizations)\n'
printf '  - project directories referenced by Clayrune\n'
if [ "$purge_data" -eq 0 ]; then
  printf '  - ~/.clayrune and frozen-app data\n'
  printf '  - checkout data/config copied to ~/.clayrune/uninstall-archives/\n'
else
  printf '\nPURGE also removes:\n'
  printf '  - %s\n' "$state_dir"
  printf '  - %s\n' "$frozen_data_dir"
  printf '  - checkout-backed Clayrune data/config (no uninstall archive)\n'
fi
printf '\n'

if [ "$dry_run" -eq 0 ] && [ "$assume_yes" -eq 0 ]; then
  if [ "$purge_data" -eq 1 ]; then expected='PURGE CLAYRUNE DATA'; else expected='UNINSTALL'; fi
  printf 'Type %s to continue: ' "$expected"
  if [ -r /dev/tty ]; then
    IFS= read -r answer </dev/tty || answer=''
  else
    echo "No interactive terminal; re-run with --yes if intentional." >&2
    exit 2
  fi
  if [ "$answer" != "$expected" ]; then
    echo 'Cancelled. Nothing was changed.'
    exit 0
  fi
fi

# Stop only commands whose path proves that they belong to Clayrune. A port or
# process name by itself is not sufficient evidence.
if [ "$is_current_home" -eq 1 ]; then
  ps -axo pid=,command= 2>/dev/null | while IFS= read -r row; do
    pid=$(printf '%s\n' "$row" | awk '{print $1}')
    command=$(printf '%s\n' "$row" | sed 's/^[[:space:]]*[0-9][0-9]*[[:space:]]*//')
    case "$command" in
      *"$install_dir/.venv/"*|*"$install_dir/server.py"*|*'/Clayrune.app/Contents/MacOS/Clayrune'*)
        action "Stop Clayrune process PID $pid"
        if [ "$dry_run" -eq 0 ]; then
          kill -TERM "$pid" 2>/dev/null || true
        fi
        ;;
    esac
  done
fi

if [ "$dry_run" -eq 0 ]; then sleep 1; fi

remove_file "$launcher" "launcher"
remove_file "$uninstall_launcher" "uninstall launcher"

if [ "$install_exists" -eq 1 ] && [ "$purge_data" -eq 0 ]; then
  stamp=$(date '+%Y%m%d-%H%M%S')
  archive_dir="$state_dir/uninstall-archives/$stamp"
  if [ -e "$install_dir/config.json" ] || [ -d "$install_dir/data" ]; then
    action "Preserve checkout data in: $archive_dir"
    if [ "$dry_run" -eq 0 ]; then
      mkdir -p "$archive_dir"
      [ ! -f "$install_dir/config.json" ] || cp -p "$install_dir/config.json" "$archive_dir/"
      [ ! -d "$install_dir/data" ] || cp -R "$install_dir/data" "$archive_dir/"
      {
        echo 'Clayrune uninstall archive'
        printf 'Created: %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')"
        printf 'Original install: %s\n' "$install_dir"
        echo 'Restore by reinstalling Clayrune, closing it, then copying config.json and data/ back.'
      } > "$archive_dir/README.txt"
    fi
  fi
fi

[ "$install_exists" -eq 0 ] || remove_tree "$install_dir" "Clayrune application directory"
[ "$is_current_home" -eq 0 ] || remove_app "$system_app" "system Clayrune app"
remove_app "$user_app" "user Clayrune app"
if [ -n "$app_path" ] && [ "$app_path" != "$system_app" ] && [ "$app_path" != "$user_app" ]; then
  remove_app "$app_path" "Clayrune app"
fi

if [ "$purge_data" -eq 1 ]; then
  [ "$frozen_data_dir" = "$install_dir" ] || remove_tree "$frozen_data_dir" "Clayrune frozen-app data"
  [ "$state_dir" = "$install_dir" ] || remove_tree "$state_dir" "Clayrune private state"
fi

printf '\n'
if [ "$dry_run" -eq 1 ]; then
  echo 'Dry run complete. Nothing was changed.'
else
  echo 'Clayrune was uninstalled.'
  if [ "$purge_data" -eq 0 ]; then
    printf 'Preserved state remains under %s and %s\n' "$state_dir" "$frozen_data_dir"
  fi
fi
echo 'Provider CLIs, ~/.claude, and project directories were not changed.'

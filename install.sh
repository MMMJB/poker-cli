#!/usr/bin/env bash
# Install the poker trainer and put `poker` on your PATH.
#
#   From a clone:   ./install.sh
#   From anywhere:  curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/install.sh | bash
#
# Safe to re-run: it updates in place and never adds a duplicate PATH line.
# Undo with:      ./install.sh --uninstall

set -euo pipefail

# Where to clone from when this script is run standalone (curl | bash).
# Override with POKER_REPO=... , or just edit this line after pushing.
REPO_URL="${POKER_REPO:-https://github.com/MMMJB/poker-cli.git}"
INSTALL_DIR="${POKER_HOME:-$HOME/.poker-trainer-app}"
BIN_DIR="${POKER_BIN:-$HOME/.local/bin}"
MIN_PY_MINOR=12
PATH_MARKER="# added by poker-trainer install.sh"

bold=''; dim=''; red=''; green=''; reset=''
if [ -t 1 ]; then
    bold=$'\033[1m'; dim=$'\033[2m'; red=$'\033[31m'
    green=$'\033[32m'; reset=$'\033[0m'
fi

say()  { printf '%s\n' "$*"; }
step() { printf '%s==>%s %s\n' "$bold" "$reset" "$*"; }
note() { printf '    %s%s%s\n' "$dim" "$*" "$reset"; }
die()  { printf '%serror:%s %s\n' "$red" "$reset" "$*" >&2; exit 1; }

# ---------------------------------------------------------------- uninstall

uninstall() {
    step "Removing the launcher"
    # Read where the link pointed before deleting it, so the closing message
    # names the checkout that is actually installed rather than the default.
    local target=""
    if [ -L "$BIN_DIR/poker" ]; then
        target=$(cd -P "$(dirname "$(readlink "$BIN_DIR/poker")")/.." 2>/dev/null && pwd) || target=""
    fi
    if [ -L "$BIN_DIR/poker" ] || [ -f "$BIN_DIR/poker" ]; then
        rm -f "$BIN_DIR/poker"
        note "removed $BIN_DIR/poker"
    else
        note "nothing at $BIN_DIR/poker"
    fi
    [ -n "$target" ] || target="$INSTALL_DIR"

    for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile" \
              "$HOME/.profile" "$HOME/.config/fish/config.fish"; do
        [ -f "$rc" ] || continue
        if grep -qF "$PATH_MARKER" "$rc" 2>/dev/null; then
            # Drop the marker comment and exactly the line after it -- matching
            # on the path alone would eat unrelated entries the user added.
            tmp=$(mktemp)
            awk -v marker="$PATH_MARKER" '
                skip { skip = 0; next }
                index($0, marker) { skip = 1; next }
                { print }
            ' "$rc" > "$tmp"
            mv "$tmp" "$rc"
            note "cleaned PATH entry from $rc"
        fi
    done

    say ""
    say "Removed. Your checkout at ${target} was left alone --"
    say "delete it yourself if you want it gone, along with ~/.poker-trainer"
    say "(that is where your hand histories live)."
    exit 0
}

[ "${1:-}" = "--uninstall" ] && uninstall

# ------------------------------------------------------------ find a python

find_python() {
    local candidate version minor
    # 3.12 first: it is what this is developed and tested against.  Newer
    # versions are accepted, just not preferred over a known-good one.
    for candidate in python3.12 python3.13 python3.14 python3 python; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        version=$("$candidate" -c 'import sys; print("%d %d" % sys.version_info[:2])' 2>/dev/null) || continue
        set -- $version
        [ "$1" -eq 3 ] || continue
        minor=$2
        if [ "$minor" -ge "$MIN_PY_MINOR" ]; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

# ------------------------------------------------------------------- locate

script_dir=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    script_dir=$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)
fi

if [ -n "$script_dir" ] && [ -f "$script_dir/pyproject.toml" ] \
   && [ -d "$script_dir/poker" ]; then
    # Running from inside a checkout: install that one, in place.
    ROOT="$script_dir"
    step "Using this checkout"
    note "$ROOT"
else
    # Running standalone: clone or update.
    command -v git >/dev/null 2>&1 || die "git is required"
    ROOT="$INSTALL_DIR"
    if [ -d "$ROOT/.git" ]; then
        step "Updating $ROOT"
        git -C "$ROOT" pull --ff-only
    else
        step "Cloning into $ROOT"
        git clone --depth 1 "$REPO_URL" "$ROOT"
    fi
fi

# ------------------------------------------------------------------- python

step "Setting up the environment"
PYTHON=$(find_python) || die "need Python 3.$MIN_PY_MINOR or newer; none found on PATH"
note "$PYTHON ($("$PYTHON" --version 2>&1))"

if [ ! -x "$ROOT/.venv/bin/python" ]; then
    "$PYTHON" -m venv "$ROOT/.venv"
    note "created $ROOT/.venv"
else
    note "reusing $ROOT/.venv"
fi

"$ROOT/.venv/bin/python" -m pip install --quiet --upgrade pip
"$ROOT/.venv/bin/python" -m pip install --quiet -e "$ROOT"
note "dependencies installed"

# ----------------------------------------------------------------- launcher

step "Linking the launcher"
mkdir -p "$BIN_DIR"
chmod +x "$ROOT/bin/poker"

if [ -e "$BIN_DIR/poker" ] && [ ! -L "$BIN_DIR/poker" ]; then
    die "$BIN_DIR/poker exists and is not a symlink -- move it aside first"
fi
ln -sfn "$ROOT/bin/poker" "$BIN_DIR/poker"
note "$BIN_DIR/poker -> $ROOT/bin/poker"

# --------------------------------------------------------------------- PATH

# Sets RC_CHANGED to the file it edited, or leaves it empty.  Deliberately
# communicates through a global rather than stdout: the notes it prints would
# otherwise end up inside the captured value.
RC_CHANGED=""
add_to_path() {
    local rc line
    case "$(basename "${SHELL:-/bin/sh}")" in
        zsh)  rc="$HOME/.zshrc" ;;
        bash) rc="$HOME/.bashrc"; [ "$(uname)" = "Darwin" ] && rc="$HOME/.bash_profile" ;;
        fish) rc="$HOME/.config/fish/config.fish" ;;
        *)    rc="$HOME/.profile" ;;
    esac

    if [ "$(basename "${SHELL:-/bin/sh}")" = "fish" ]; then
        line="fish_add_path \"$BIN_DIR\""
        mkdir -p "$(dirname "$rc")"
    else
        line="export PATH=\"$BIN_DIR:\$PATH\""
    fi

    if [ -f "$rc" ] && grep -qF "$BIN_DIR" "$rc" 2>/dev/null; then
        note "$rc already references $BIN_DIR"
        return 0
    fi

    printf '\n%s\n%s\n' "$PATH_MARKER" "$line" >> "$rc"
    note "added $BIN_DIR to PATH in $rc"
    RC_CHANGED="$rc"
}

step "Checking your PATH"
case ":$PATH:" in
    *":$BIN_DIR:"*)
        note "$BIN_DIR is already on your PATH"
        ;;
    *)
        add_to_path
        ;;
esac

# ------------------------------------------------------------------- verify

step "Verifying"
if ! "$ROOT/bin/poker" --help >/dev/null 2>&1; then
    die "the launcher did not run -- try $ROOT/.venv/bin/python -m poker --help"
fi
note "launcher works"

say ""
printf '%sInstalled.%s\n' "$green$bold" "$reset"
say ""
if [ -n "$RC_CHANGED" ]; then
    say "  Open a new terminal (or: source $RC_CHANGED), then:"
else
    say "  Run it with:"
fi
say ""
say "    poker --offline        ${dim}# play now, no API key, costs nothing${reset}"
say "    poker                  ${dim}# needs ANTHROPIC_API_KEY${reset}"
say "    poker --no-review      ${dim}# skip the post-hand coach (the expensive part)${reset}"
say ""
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    say "  ${dim}ANTHROPIC_API_KEY is not set, so only --offline will work for now.${reset}"
    say ""
fi
say "  ${dim}Update: git -C $ROOT pull${reset}"
say "  ${dim}Remove: $ROOT/install.sh --uninstall${reset}"

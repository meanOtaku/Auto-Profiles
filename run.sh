#!/usr/bin/env bash
#
# Single-command runner for the Face Profile System on a fresh Ubuntu
# developer machine.
#
# Safe by default: with no arguments this runs the hardware-free `check`
# command against the fully-disabled config/default.yaml. It never uses
# sudo/apt, never alters system configuration, and never touches the
# camera, detector, database, recognition, enrollment, or API unless the
# config file you point it at explicitly enables them (see docs/RUNBOOK.md).
#
# Usage:
#   ./run.sh                                    # safe hardware-free check
#   ./run.sh detect --debug-output /tmp/out.png  # any face-profile subcommand
#   ./run.sh --config config/my-local.yaml detect
#   FACE_PROFILE_CONFIG=config/my-local.yaml ./run.sh detect
#   ./run.sh --install-uv                        # pre-approve bootstrapping uv
#   ./run.sh --help
#
# uv is required and is never installed silently. If it is missing, this
# script prints the official installer command and, on an interactive
# terminal, asks before running it (a user-space install to ~/.local/bin;
# no sudo, no apt, no system configuration changes). Pass --install-uv to
# pre-approve that install non-interactively (e.g. in scripted use).

set -Eeuo pipefail

die() {
    echo "error: $1" >&2
    exit 1
}

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
REPO_DIR="$(dirname "$SCRIPT_PATH")"

if [[ ! -f "$REPO_DIR/pyproject.toml" ]] \
    || ! grep -q '^name = "face-profile-system"' "$REPO_DIR/pyproject.toml" 2>/dev/null; then
    die "could not locate the face-profile-system repository root next to this script (expected $REPO_DIR/pyproject.toml)"
fi

cd "$REPO_DIR"

usage() {
    cat <<'USAGE'
Usage: ./run.sh [--install-uv] [--config PATH] [face-profile args...]

With no arguments, runs a safe, hardware-free lifecycle check using the
fully-disabled config/default.yaml (no camera, no detector, no database,
no API, no host settings changes).

Options (handled by this wrapper, not forwarded to face-profile):
  --config PATH   Use PATH instead of config/default.yaml.
  --install-uv    Pre-approve installing uv via the official installer
                  (https://astral.sh/uv/install.sh) if it is missing.
  -h, --help      Show this help and exit.

Anything else is forwarded to `face-profile` unchanged, for example:
  ./run.sh detect --debug-output /tmp/out.png
  ./run.sh --config config/webcam-demo.yaml detect
  ./run.sh serve
  ./run.sh profile list

Environment:
  FACE_PROFILE_CONFIG   Default config path if --config is not given.

See docs/RUNBOOK.md for how to safely enable a local camera/webcam
source or the REST/WebSocket API in your own config file.
USAGE
}

install_uv_approved=0
config_path=""
pass_args=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-uv)
            install_uv_approved=1
            shift
            ;;
        --config)
            [[ $# -ge 2 ]] || die "--config requires a path argument"
            config_path="$2"
            shift 2
            ;;
        --config=*)
            config_path="${1#--config=}"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            pass_args+=("$1")
            shift
            ;;
    esac
done

if [[ -z "$config_path" ]]; then
    config_path="${FACE_PROFILE_CONFIG:-config/default.yaml}"
fi

if [[ ! -f "$config_path" ]]; then
    die "config file not found: $config_path (safe default is config/default.yaml; see docs/RUNBOOK.md to create a local camera/API config)"
fi

if ! command -v uv >/dev/null 2>&1; then
    install_cmd='curl -LsSf https://astral.sh/uv/install.sh | sh'
    echo "uv was not found on PATH." >&2
    echo "The official installer places it in your user account only (~/.local/bin)," >&2
    echo "with no sudo, no apt, and no system configuration changes:" >&2
    echo "  $install_cmd" >&2
    proceed=0
    if [[ "$install_uv_approved" -eq 1 ]]; then
        proceed=1
    elif [[ -t 0 && -t 1 ]]; then
        read -r -p "Run the official uv installer now? [y/N] " reply
        [[ "$reply" =~ ^[Yy]$ ]] && proceed=1
    fi
    if [[ "$proceed" -eq 1 ]]; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
        command -v uv >/dev/null 2>&1 \
            || die "uv installation did not complete; open a new shell (or add ~/.local/bin to PATH) and re-run this script"
    else
        die "uv is required. Install it yourself, or re-run with --install-uv to approve the official installer shown above."
    fi
fi

uv sync --locked --all-groups

if [[ ${#pass_args[@]} -eq 0 ]]; then
    pass_args=(check)
fi

exec uv run face-profile --config "$config_path" "${pass_args[@]}"

#!/usr/bin/env bash
#
# Continuous launcher for the Face Profile System: always starts the
# long-running `serve` API daemon, through run.sh/uv, instead of the safe
# one-shot `check` that ./run.sh runs by default. run.sh itself is
# unchanged; this script never duplicates its uv-bootstrap, uv-install, or
# process-exec behavior, it delegates to it.
#
# Safe by default: the shipped config/continuous.yaml only enables the
# loopback-only REST/WebSocket API (no auth_token required on loopback). It
# still starts with the camera, detector, database, recognition,
# enrollment, and every other sensitive capability disabled/mock, exactly
# like config/default.yaml. Starting this script brings up the API daemon
# only -- it does NOT enable camera-based face recognition. That requires
# your own private config that explicitly opts in (see docs/RUNBOOK.md and
# docs/RUNNING_ON_UBUNTU.md section 6).
#
# Usage:
#   ./run-continuous.sh                              # serve config/continuous.yaml (loopback API only)
#   ./run-continuous.sh --config data/my-private.yaml # serve your own private config instead
#   ./run-continuous.sh --install-uv                  # pre-approve bootstrapping uv non-interactively
#   ./run-continuous.sh --help
#
# Unlike ./run.sh, this script always runs `serve` and never falls back to
# the one-shot check -- no other face-profile subcommand can be forwarded
# here. uv installation is never silent: it is handled entirely by run.sh,
# with the same interactive [y/N] prompt or --install-uv pre-approval.
# Signals (e.g. Ctrl+C / SIGTERM) reach the uvicorn process directly because
# this script, run.sh, and run.sh's final `uv run` all use `exec`.

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

usage() {
    cat <<'USAGE'
Usage: ./run-continuous.sh [--install-uv] [--config PATH]

Always starts the long-running `face-profile serve` API daemon through
run.sh/uv -- it never falls back to run.sh's default one-shot `check`, and
no face-profile subcommand other than `serve` can be requested here.

With no arguments, serves the repository-shipped config/continuous.yaml:
loopback-only REST/WebSocket API enabled (no auth_token required on
loopback), camera/detection/database/recognition/enrollment/every other
sensitive capability disabled/mock, exactly like config/default.yaml. This
starts the API daemon only -- it does not enable camera-based face
recognition; that requires your own private config (see docs/RUNBOOK.md).

Options:
  --config PATH   Serve PATH instead of config/continuous.yaml.
  --install-uv    Pre-approve installing uv via the official installer,
                  forwarded to run.sh unchanged (see run.sh --help).
  -h, --help      Show this help and exit.

Environment:
  FACE_PROFILE_CONFIG   Default config path if --config is not given,
                         forwarded to run.sh unchanged.

See docs/RUNBOOK.md and docs/RUNNING_ON_UBUNTU.md section 6 for how to
safely enable a camera/recognition/database from a private config.
USAGE
}

install_uv_approved=0
config_path=""

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
            die "unrecognized argument: $1 (run-continuous.sh always runs 'serve' with no extra face-profile arguments; use --config/--install-uv/--help, or run ./run.sh directly for other subcommands)"
            ;;
    esac
done

if [[ -z "$config_path" ]]; then
    config_path="${FACE_PROFILE_CONFIG:-$REPO_DIR/config/continuous.yaml}"
fi

forward_args=()
if [[ "$install_uv_approved" -eq 1 ]]; then
    forward_args+=(--install-uv)
fi
forward_args+=(--config "$config_path" serve)

exec "$REPO_DIR/run.sh" "${forward_args[@]}"

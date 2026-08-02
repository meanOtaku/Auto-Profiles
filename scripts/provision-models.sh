#!/usr/bin/env bash
#
# Idempotently download and integrity-verify the two models config/jetson-full.yaml
# pins: the YuNet face detector and the SFace face-recognition embedding model, both
# from OpenCV Zoo (https://github.com/opencv/opencv_zoo). See docs/MODELS.md for
# exact provenance, license, and independently-confirmed SHA-256 for each file.
#
# Fails closed: a missing, partial, or hash-mismatched download is never installed
# for use. If a file already exists at the destination with the correct hash, it is
# left untouched and nothing is re-downloaded (idempotent). If a file already exists
# with an INCORRECT hash, this script refuses to silently overwrite it -- remove it
# yourself first if you want it re-fetched, since an unexpected file there may be
# something you intentionally placed.
#
# opencv_zoo stores these particular files via Git LFS: GitHub's plain
# raw.githubusercontent.com/.../raw/... URL (used by this repo's older
# Webcam_demo.sh and docs/RUNNING_ON_UBUNTU.md section 6) resolves to a small LFS
# *pointer* text file, not the real model bytes, for both of these paths -- verified
# directly against the live repository while researching this script. This script
# uses media.githubusercontent.com, which resolves the real LFS object content, and
# double-checks the downloaded file is not itself an LFS pointer before hashing it.
#
# Usage:
#   ./scripts/provision-models.sh [DEST_DIR]
#
# DEST_DIR defaults to ./models (matching config/jetson-full.yaml's model_path
# entries). Run this before config/jetson-full.yaml's detection/embedding backends
# are ever loaded (serve/detect/check); see docs/RUNNING_ON_JETSON.md section 9.

set -Eeuo pipefail

DEST_DIR="${1:-models}"

YUNET_FILE="face_detection_yunet_2023mar.onnx"
YUNET_URL="https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
YUNET_SHA256="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"

SFACE_FILE="face_recognition_sface_2021dec.onnx"
SFACE_URL="https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"
SFACE_SHA256="0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"

log() {
    printf '\n==> %s\n' "$1"
}

die() {
    echo "error: $1" >&2
    exit 1
}

command -v curl >/dev/null 2>&1 || die "curl is not installed"
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is not installed"

mkdir -p "$DEST_DIR"

sha256_of() {
    sha256sum "$1" | awk '{print $1}'
}

looks_like_lfs_pointer() {
    # A real ONNX file is a small-ish binary protobuf; a Git LFS pointer is
    # always this exact ASCII text preamble regardless of the real object size.
    head -c 32 "$1" 2>/dev/null | grep -q "^version https://git-lfs"
}

provision_one() {
    local name="$1" url="$2" expected_sha="$3"
    local dest="$DEST_DIR/$name"

    log "Checking $name"

    if [[ -f "$dest" ]]; then
        local actual_sha
        actual_sha="$(sha256_of "$dest")"
        if [[ "$actual_sha" == "$expected_sha" ]]; then
            echo "$name already present and verified (sha256 $actual_sha); skipping download."
            return 0
        fi
        die "$dest already exists but its sha256 ($actual_sha) does not match the pinned value ($expected_sha). Refusing to overwrite an unexpected file -- remove it yourself first if you want it re-downloaded."
    fi

    log "Downloading $name"
    local tmp_dest
    tmp_dest="$(mktemp "$DEST_DIR/.${name}.XXXXXX")"

    if ! curl -fL --retry 3 -o "$tmp_dest" "$url"; then
        rm -f "$tmp_dest"
        die "download failed for $name from $url"
    fi

    if looks_like_lfs_pointer "$tmp_dest"; then
        rm -f "$tmp_dest"
        die "$name downloaded as a Git LFS pointer, not real model bytes -- $url is not resolving to the actual object. This is a fail-closed refusal, not a corrupted-download false pass."
    fi

    local actual_sha
    actual_sha="$(sha256_of "$tmp_dest")"
    if [[ "$actual_sha" != "$expected_sha" ]]; then
        rm -f "$tmp_dest"
        die "sha256 mismatch for $name: expected $expected_sha, got $actual_sha. Refusing to install an unverified model file."
    fi

    mv "$tmp_dest" "$dest"
    echo "$name downloaded and verified (sha256 $actual_sha)."
}

provision_one "$YUNET_FILE" "$YUNET_URL" "$YUNET_SHA256"
provision_one "$SFACE_FILE" "$SFACE_URL" "$SFACE_SHA256"

log "All models provisioned and verified in $DEST_DIR/"

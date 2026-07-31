#!/usr/bin/env bash

set -Eeuo pipefail

MODEL_DIR="models"
MODEL_FILE="$MODEL_DIR/face_detection_yunet_2023mar.onnx"
MODEL_URL="https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
EXPECTED_SHA="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"

CONFIG_FILE="config/webcam-demo.yaml"
OUTPUT_FILE="/tmp/webcam-detection.png"

log() {
    printf '\n==> %s\n' "$1"
}

command -v uv >/dev/null 2>&1 || {
    echo "uv is not installed."
    exit 1
}

command -v curl >/dev/null 2>&1 || {
    echo "curl is not installed."
    exit 1
}

log "Switching to alpha branch"

git switch alpha
git pull --ff-only origin alpha

log "Installing dependencies"

uv sync --locked --all-groups

log "Downloading YuNet detector model"

mkdir -p "$MODEL_DIR"

if [[ ! -f "$MODEL_FILE" ]]; then
    curl -L "$MODEL_URL" -o "$MODEL_FILE"
fi

log "Verifying model integrity"

ACTUAL_SHA="$(sha256sum "$MODEL_FILE" | awk '{print $1}')"

if [[ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]]; then
    echo "Model checksum mismatch."
    echo "Expected: $EXPECTED_SHA"
    echo "Actual:   $ACTUAL_SHA"
    exit 1
fi

log "Creating webcam configuration"

cat > "$CONFIG_FILE" <<YAML
camera:
  enabled: true
  source: webcam
  path: null
  device_index: 0
  retry_attempts: 2

detection:
  enabled: true
  backend: yunet
  model_path: $MODEL_FILE
  model_sha256: $EXPECTED_SHA
  confidence_threshold: 0.5
  nms_threshold: 0.3
  top_k: 5000

logging:
  level: INFO

settings:
  volume: 50
  brightness: 50
YAML

log "Capturing one webcam frame and detecting faces"

rm -f "$OUTPUT_FILE"

uv run face-profile \
    --config "$CONFIG_FILE" \
    detect \
    --debug-output "$OUTPUT_FILE"

if [[ ! -f "$OUTPUT_FILE" ]]; then

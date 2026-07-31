#!/usr/bin/env bash

set -Eeuo pipefail

OUTPUT_FILE="/tmp/face-profile-demo.png"
CONFIG_FILE="config/demo.yaml"

git switch alpha
git pull --ff-only origin alpha

uv sync --locked --all-groups

uv run face-profile \
    --config config/default.yaml \
    check

cat > "$CONFIG_FILE" <<'YAML'
camera:
  enabled: true
  source: image
  path: tests/fixtures/camera/synthetic.png
  device_index: 0
  retry_attempts: 2

detection:
  enabled: true
  backend: mock
  model_path: null
  model_sha256: null
  confidence_threshold: 0.5
  nms_threshold: 0.3
  top_k: 5000

logging:
  level: INFO

settings:
  volume: 50
  brightness: 50
YAML

uv run face-profile \
    --config "$CONFIG_FILE" \
    detect \
    --debug-output "$OUTPUT_FILE"

echo "Demo image created at: $OUTPUT_FILE"

if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$OUTPUT_FILE"
fi

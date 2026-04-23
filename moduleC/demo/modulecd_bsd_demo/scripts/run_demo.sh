#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT_DIR"

echo "[moduleCD-BSD-demo] starting service"
uv run python demo/modulecd_bsd_demo/service.py --max-messages 3 &
SERVICE_PID=$!

cleanup() {
  kill "$SERVICE_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

sleep 1
echo "[moduleCD-BSD-demo] publishing sample frames"
uv run python demo/modulecd_bsd_demo/sample_publisher.py --count 3 --interval-ms 250

wait "$SERVICE_PID"

#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

if ! lsof -nP -iTCP:6016 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[INFO] Starting server..."
  ./start_server.command >/tmp/capswriter_server_launcher.log 2>&1 &
  sleep 3
fi

echo "[INFO] Starting client..."
exec ./start_client.command

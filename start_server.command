#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
APP="./CapsWriter-Server-mac-app-fix5.app/Contents/MacOS/CapsWriter-Server-mac-app-fix5"

if [[ ! -x "$APP" ]]; then
  echo "[ERROR] Server executable not found: $APP"
  echo "Please make sure CapsWriter-Server-mac-app-fix5.app is in this folder."
  read -r -p "Press Enter to exit..." _
  exit 1
fi

exec "$APP"

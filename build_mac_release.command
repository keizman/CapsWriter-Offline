#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

PYINSTALLER=".venv/bin/pyinstaller"
if [[ ! -x "$PYINSTALLER" ]]; then
  if command -v pyinstaller >/dev/null 2>&1; then
    PYINSTALLER="pyinstaller"
  else
    echo "[ERROR] pyinstaller not found. Install it in .venv or PATH first."
    exit 1
  fi
fi

DIST_DIR="dist_release"
BUILD_DIR="build_release"
RELEASE_DIR="release"

SERVER_NAME="CapsWriter-Server"
CLIENT_NAME="CapsWriter-Client"

COMMON_ARGS=(
  --clean
  --noconfirm
  --windowed
  --onedir
  --distpath "$DIST_DIR"
  --specpath "$BUILD_DIR"
  --exclude-module _watchdog_fsevents
  --exclude-module watchdog.observers.fsevents
  --hidden-import rich._unicode_data.unicode17-0-0
  --hidden-import aiohttp
  --hidden-import aiohttp.web
)

echo "[1/6] Build server app..."
"$PYINSTALLER" \
  "${COMMON_ARGS[@]}" \
  --workpath "$BUILD_DIR/server" \
  --name "$SERVER_NAME" \
  start_server.py

echo "[2/6] Build client app..."
"$PYINSTALLER" \
  "${COMMON_ARGS[@]}" \
  --workpath "$BUILD_DIR/client" \
  --name "$CLIENT_NAME" \
  start_client.py

echo "[3/6] Refresh root app bundles..."
rm -rf "$SERVER_NAME.app" "$CLIENT_NAME.app"
cp -R "$DIST_DIR/$SERVER_NAME.app" "./$SERVER_NAME.app"
cp -R "$DIST_DIR/$CLIENT_NAME.app" "./$CLIENT_NAME.app"

echo "[4/6] Prepare release folder..."
mkdir -p "$RELEASE_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
ZIP_PATH="$RELEASE_DIR/CapsWriter-mac-$STAMP.zip"
STAGE_DIR="$RELEASE_DIR/CapsWriter-mac-$STAMP"

echo "[5/6] Create release zip..."
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"
cp -R "$SERVER_NAME.app" "$STAGE_DIR/"
cp -R "$CLIENT_NAME.app" "$STAGE_DIR/"
cp start_server.command "$STAGE_DIR/"
cp start_client.command "$STAGE_DIR/"
cp config_server.local.example.json "$STAGE_DIR/"
cp config_client.local.example.json "$STAGE_DIR/"

ditto -c -k --sequesterRsrc --keepParent "$STAGE_DIR" "$ZIP_PATH"

rm -rf "$STAGE_DIR"

echo "[6/6] Done"
echo "Apps:"
echo "  $(pwd)/$SERVER_NAME.app"
echo "  $(pwd)/$CLIENT_NAME.app"
echo "Release zip:"
echo "  $(pwd)/$ZIP_PATH"

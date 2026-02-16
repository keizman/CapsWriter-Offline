#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="CapsWriter-Client.app"
CMD_FILE="start_client.command"
DEFAULT_CONFIG="config_client.local.json"
CONFIG_FILE="${1:-$DEFAULT_CONFIG}"
REQUIRED_CLIENT_FILES=("hot.txt" "hot-rule.txt")

RELEASE_DIR="release"
STAMP="$(date +%Y%m%d-%H%M%S)"
PKG_NAME="CapsWriter-client-personal-$STAMP"
STAGE_DIR="$RELEASE_DIR/$PKG_NAME"
ZIP_PATH="$RELEASE_DIR/$PKG_NAME.zip"

find_client_app() {
  if [[ -d "./$APP_NAME" ]]; then
    echo "./$APP_NAME"
    return 0
  fi
  if [[ -d "./dist_release/$APP_NAME" ]]; then
    echo "./dist_release/$APP_NAME"
    return 0
  fi
  return 1
}

APP_PATH="$(find_client_app || true)"
if [[ -z "$APP_PATH" ]]; then
  echo "[ERROR] Client app not found."
  echo "Expected one of:"
  echo "  - ./$APP_NAME"
  echo "  - ./dist_release/$APP_NAME"
  exit 1
fi

if [[ ! -f "$CMD_FILE" ]]; then
  echo "[ERROR] Missing $CMD_FILE"
  exit 1
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "[ERROR] Missing config file: $CONFIG_FILE"
  echo "Usage: ./package_client.command [config_file]"
  echo "Example: ./package_client.command config_client.local.json"
  exit 1
fi

for f in "${REQUIRED_CLIENT_FILES[@]}"; do
  if [[ ! -f "$f" ]]; then
    echo "[ERROR] Missing required client file: $f"
    exit 1
  fi
done

mkdir -p "$RELEASE_DIR"
rm -rf "$STAGE_DIR" "$ZIP_PATH"
mkdir -p "$STAGE_DIR"

cp -R "$APP_PATH" "$STAGE_DIR/$APP_NAME"
cp "$CMD_FILE" "$STAGE_DIR/$CMD_FILE"
chmod +x "$STAGE_DIR/$CMD_FILE"

# Always ship runtime config with the expected filename.
cp "$CONFIG_FILE" "$STAGE_DIR/$DEFAULT_CONFIG"

for f in "${REQUIRED_CLIENT_FILES[@]}"; do
  cp "$f" "$STAGE_DIR/$f"
done

ditto -c -k --sequesterRsrc --keepParent "$STAGE_DIR" "$ZIP_PATH"
rm -rf "$STAGE_DIR"

echo "Packaged client release:"
echo "  $(pwd)/$ZIP_PATH"
if command -v shasum >/dev/null 2>&1; then
  echo "SHA256:"
  shasum -a 256 "$ZIP_PATH"
fi

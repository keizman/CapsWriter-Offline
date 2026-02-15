#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

find_client_exec() {
  local app macos bin

  # 优先稳定命名
  app="./CapsWriter-Client.app"
  if [[ -d "$app/Contents/MacOS" ]]; then
    for bin in "$app/Contents/MacOS/"*; do
      [[ -x "$bin" ]] && { echo "$bin"; return 0; }
    done
  fi

  # 回退：匹配历史命名（按最新修改时间优先）
  while IFS= read -r app; do
    macos="$app/Contents/MacOS"
    [[ -d "$macos" ]] || continue
    for bin in "$macos/"*; do
      [[ -x "$bin" ]] && { echo "$bin"; return 0; }
    done
  done < <(ls -td ./CapsWriter-Client*.app 2>/dev/null || true)

  return 1
}

APP="$(find_client_exec || true)"
if [[ -z "$APP" ]]; then
  echo "[ERROR] Client executable not found."
  echo "Expected one of:"
  echo "  - ./CapsWriter-Client.app"
  echo "  - ./CapsWriter-Client*.app"
  read -r -p "Press Enter to exit..." _
  exit 1
fi

exec "$APP"

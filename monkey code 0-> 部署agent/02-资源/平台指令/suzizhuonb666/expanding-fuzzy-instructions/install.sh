#!/usr/bin/env bash
# 安装本技能包到目标 skills 目录（默认 ~/.agents/skills）
# 用法: bash install.sh [目标skills目录] [--force]
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="$(basename "$SRC")"
DEST_BASE="${1:-$HOME/.agents/skills}"
FORCE="${2:-}"
DEST="$DEST_BASE/$NAME"
if [ -e "$DEST" ] && [ "$FORCE" != "--force" ]; then
  echo "exists: $DEST （覆盖请加 --force）"; exit 1
fi
mkdir -p "$DEST_BASE"
[ "$FORCE" = "--force" ] && rm -rf "$DEST"
cp -R "$SRC" "$DEST"
echo "installed: $DEST"
echo "selfcheck: bash $DEST/scripts/selfcheck.sh"

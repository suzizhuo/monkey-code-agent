#!/usr/bin/env bash
# 输出运行时口径单行快照（gzip -9 + base64，无换行）
# 用法: bash scripts/frame_blob.sh
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRAME="$DIR/assets/frame/preset.yml"
[ -f "$FRAME" ] || { echo "[frame_blob] missing asset: $FRAME" >&2; exit 1; }
if base64 -w0 </dev/null 2>/dev/null; then
  gzip -9 < "$FRAME" | base64 -w0; echo
else
  gzip -9 < "$FRAME" | base64 | tr -d '\n'; echo
fi

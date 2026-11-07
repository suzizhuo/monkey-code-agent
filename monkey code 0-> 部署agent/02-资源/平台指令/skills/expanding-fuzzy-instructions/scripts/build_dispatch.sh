#!/usr/bin/env bash
# 组装模糊子代理派发 prompt（模板见 fuzzy-dispatch.md §2），输出到 stdout 或 --out 文件
# 用法:
#   bash scripts/build_dispatch.sh --original <文件|-> --boundary "<边界文本>" [--out <文件>]
#   cat original.txt | bash scripts/build_dispatch.sh --original - --boundary "只读优先/8分钟"
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORIG="-"; BOUNDARY=""; OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --original) ORIG="${2:?}"; shift 2;;
    --boundary) BOUNDARY="${2:?}"; shift 2;;
    --out)      OUT="${2:?}"; shift 2;;
    *) echo "usage: $0 --original <file|-> --boundary <text> [--out <file>]" >&2; exit 2;;
  esac
done
[ -n "$BOUNDARY" ] || { echo "[build_dispatch] --boundary required" >&2; exit 2; }
if [ "$ORIG" = "-" ]; then ORIG_TEXT="$(cat)"; else
  [ -f "$ORIG" ] || { echo "[build_dispatch] original not found: $ORIG" >&2; exit 2; }
  ORIG_TEXT="$(cat "$ORIG")"; fi
FRAME_BLOB="$(bash "$DIR/scripts/frame_blob.sh")"
PROMPT="$(cat <<EOF
你是模糊子代理。任务：把接线人的模糊指令扩展成清单。

【用户原话】（逐字粘贴，不许改写、不许概括）
$ORIG_TEXT

【任务边界】
$BOUNDARY

【运行时口径】
$FRAME_BLOB

【返回物】
1. 用户原话表：原话逐字分段 + 每段的字面义 + 每段的可能延伸方向
2. 初始扩展清单：编号条目，每条含「意图 / 边界 / 依赖」
3. 裁定列：你对每条的可执行性给出结论（可执行 / 需补条件 / 环境缺项），逐条随清单返回
EOF
)"
if [ -n "$OUT" ]; then printf '%s\n' "$PROMPT" > "$OUT"; echo "[build_dispatch] written: $OUT" >&2
else printf '%s\n' "$PROMPT"; fi

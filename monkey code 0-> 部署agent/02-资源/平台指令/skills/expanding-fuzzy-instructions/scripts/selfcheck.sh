#!/usr/bin/env bash
# 技能包自检：结构 / 脚本可跑 / 帧资产完好
# 用法: bash scripts/selfcheck.sh
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail=0
chk() { if [ -e "$DIR/$1" ]; then echo "OK   $1"; else echo "MISS $1"; fail=1; fi; }
chk SKILL.md
chk fuzzy-dispatch.md
chk README.md
chk assets/frame/preset.yml
chk scripts/frame_blob.sh
chk scripts/build_dispatch.sh
chk scripts/ledger.py
chk install.sh

LEN=$(bash "$DIR/scripts/frame_blob.sh" | wc -c | tr -d ' ')
echo "FRAME_BLOB_LEN=$LEN"
[ "${LEN:-0}" -gt 1000 ] || { echo "FAIL frame blob too short"; fail=1; }

TMP=$(mktemp -d)
if python3 "$DIR/scripts/ledger.py" append "$TMP/ledger.md" 模糊子代理 清单N条 建立基线 裁定随表 \
   && python3 "$DIR/scripts/ledger.py" terminate "$TMP/ledger.md" \
   && grep -q "终止（扩不出）" "$TMP/ledger.md" && grep -q "| 1 |" "$TMP/ledger.md"; then
  echo "OK   ledger append+terminate"
else
  echo "FAIL ledger"; python3 "$DIR/scripts/ledger.py" append "$TMP/ledger.md" 模糊子代理 清单N条 建立基线 裁定随表 2>&1 | head -3; fail=1
fi

ORIG=/tmp/selfcheck.orig; DISP=/tmp/selfcheck.dispatch; ERR=/tmp/selfcheck.err
echo "把那个搞一下" > "$ORIG"
if bash "$DIR/scripts/build_dispatch.sh" --original "$ORIG" --boundary "自检边界" --out "$DISP" 2>"$ERR" \
   && [ -s "$DISP" ] && grep -q "运行时口径" "$DISP" && grep -q "H4sIA" "$DISP"; then
  echo "OK   build_dispatch（blob 头匹配）"
else
  echo "FAIL build_dispatch"; head -3 "$ERR" 2>/dev/null; fail=1
fi
rm -rf "$TMP" "$ORIG" "$DISP" "$ERR"

if [ "$fail" = 0 ]; then echo "SELFCHECK PASS"; else echo "SELFCHECK FAIL"; exit 1; fi

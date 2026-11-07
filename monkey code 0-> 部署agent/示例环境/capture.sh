#!/bin/bash
# 刷新「示例环境/实测输出」：把当前环境的真实命令输出抓成文件。
# 用法: bash capture.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BASE="$(cd "$HERE/.." && pwd)"
WS="$(cd "$BASE/.." && pwd)"
OUT="$HERE/实测输出"
mkdir -p "$OUT"

cd "$WS"

python3 isolis.py help     > "$OUT/01-命令-help.txt" 2>&1
python3 isolis.py profiles > "$OUT/02-命令-profiles.txt" 2>&1
python3 isolis.py status   > "$OUT/03-命令-status.txt" 2>&1
python3 isolis.py doctor   > "$OUT/04-命令-doctor.txt" 2>&1 || true

curl -sS "http://127.0.0.1:${ISOLIS_PORT:-18080}/health"  | python3 -m json.tool > "$OUT/05-health.json" 2>&1
curl -sS "http://127.0.0.1:${ISOLIS_PORT:-18080}/info"    | python3 -m json.tool > "$OUT/06-info.json" 2>&1
curl -sS "http://127.0.0.1:${ISOLIS_PORT:-18080}/history" | python3 -m json.tool > "$OUT/07-history.json" 2>&1

docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' > "$OUT/08-docker-ps.txt" 2>&1
docker images --format '{{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}'   > "$OUT/09-docker-images.txt" 2>&1

docker inspect isolis-default --format \
    'User={{.Config.User}} RO={{.HostConfig.ReadonlyRootfs}} CapDrop={{.HostConfig.CapDrop}} CapAdd={{.HostConfig.CapAdd}} Mem={{.HostConfig.Memory}} NanoCpus={{.HostConfig.NanoCpus}} Pids={{.HostConfig.PidsLimit}} Ports={{.HostConfig.PortBindings}} Mounts={{len .Mounts}} Net={{.HostConfig.NetworkMode}}' \
    > "$OUT/10-container-isolation.txt" 2>&1

# 兜底脱敏：避免任何路径把密钥写进示例目录
python3 - "$OUT" <<'PY'
import re, sys, pathlib
root = pathlib.Path(sys.argv[1])
key = re.compile(r'(MCAI_LLM_API_KEY=)([^"\\\s]+)')
for p in root.rglob('*'):
    if not p.is_file():
        continue
    try:
        text = p.read_text()
    except (UnicodeDecodeError, OSError):
        continue
    cleaned = key.sub(r'\1<redacted>', text)
    if cleaned != text:
        p.write_text(cleaned)
        print("[capture] redacted", p.name)
PY

echo "[capture] 已刷新 $OUT"
ls -1 "$OUT"

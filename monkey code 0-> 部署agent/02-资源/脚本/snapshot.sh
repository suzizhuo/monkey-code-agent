#!/bin/bash
# 保存当前运行状态快照（镜像、容器、网络、身份信息），用于比对与调试。
# 用法: bash snapshot.sh [输出目录]
set -uo pipefail

OUT="${1:-$(cd "$(dirname "$0")/.." && pwd)/配置/当前快照}"
mkdir -p "$OUT"

docker version > "$OUT/docker-version.txt" 2>&1
docker info > "$OUT/docker-info.txt" 2>&1
docker images --format '{{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}\t{{.CreatedAt}}' > "$OUT/images.txt" 2>&1
docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' > "$OUT/containers.txt" 2>&1
docker network ls > "$OUT/networks.txt" 2>&1
docker network inspect isolis-net > "$OUT/network-isolis-net.json" 2>&1
# inspect 的 Env 里含明文密钥，必须过滤后再落盘
docker inspect isolis-default 2>&1 \
    | sed -E 's/(MCAI_LLM_API_KEY=)[^"\\]+/\1<redacted>/g' \
    > "$OUT/container-isolis-default.json"
docker inspect isolis-agent:tooled-ca22788ffe > "$OUT/image-tooled.json" 2>&1
cat /etc/docker/daemon.json > "$OUT/daemon.json" 2>&1
cp /workspace/.isolis/state.json "$OUT/state.json" 2>/dev/null || true
curl -sS http://127.0.0.1:18080/info > "$OUT/agent-info.json" 2>&1 || true
curl -sS http://127.0.0.1:18080/history > "$OUT/agent-history.json" 2>&1 || true

# 环境变量只保留键名，避免泄露密钥
docker inspect isolis-default --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | sed -E 's/^(MCAI_LLM_API_KEY)=.*/\1=<redacted>/' > "$OUT/container-env.txt"

echo "[snapshot] 写入 $OUT"
# 落盘后再全量兜底扫描一次，防止任何 inspect/json 漏网
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
        print("[snapshot] redacted", p.name)
PY
ls -1 "$OUT"

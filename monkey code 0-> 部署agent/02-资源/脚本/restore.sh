#!/bin/bash
# 从零还原 Isolis 运行环境（宿主机侧）。
# 在 /workspace 下以 root 执行：bash restore.sh
# 幂等：已安装/已启动/已构建的部分会自动跳过。
#
# 用法:
#   bash restore.sh                 还原代码 + Docker 环境 + 两个镜像
#   bash restore.sh --with-platform 额外还原平台指令（rules/skills/用户规则包）
#   bash restore.sh --no-build      只还原代码与环境，不构建镜像
set -euo pipefail

WS="/workspace"
BASE="$(cd "$(dirname "$0")/../.." && pwd)"
RES="$BASE/02-资源"

WITH_PLATFORM=0
DO_BUILD=1
for arg in "$@"; do
    case "$arg" in
        --with-platform) WITH_PLATFORM=1 ;;
        --no-build) DO_BUILD=0 ;;
        *) echo "[restore] 未知参数: $arg" >&2; exit 2 ;;
    esac
done

say() { echo "[restore] $*"; }

# 0. 代码与项目文档
say "还原代码与项目文档"
cp -r "$RES/agent/." "$WS/agent/"
cp "$RES/isolis.py" "$WS/isolis.py"
cp "$RES/tests/"*.py "$WS/tests/"
cp "$RES/项目文档/ISOLIS_PROMPT.md" "$RES/项目文档/QUICKSTART.md" \
   "$RES/项目文档/README.md" "$RES/项目文档/提示词.txt" "$WS/"
mkdir -p "$WS/plans" "$WS/specs" "$WS/prompts"
cp "$RES/项目文档/plans/"*.md "$WS/plans/"
cp "$RES/项目文档/specs/"*.md "$WS/specs/"
cp "$RES/项目文档/prompts/"*.md "$WS/prompts/"
chmod +x "$WS/isolis.py"

# 0b. 平台指令（可选；只补齐缺失文件，不覆盖已有内容）
if [ "$WITH_PLATFORM" = "1" ]; then
    AI="/root/.codingmatrix/project-tpl/.ai-ready"
    say "还原平台指令到 $AI"
    mkdir -p "$AI/rules" "$AI/skills"
    cp -rn "$RES/平台指令/rules/." "$AI/rules/"
    cp -rn "$RES/平台指令/skills/." "$AI/skills/"
    say "平台指令已补齐（已存在的文件保持原样）"
fi

# 1. Docker CLI + daemon
if ! command -v docker >/dev/null 2>&1; then
    say "安装 docker.io"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y docker.io
else
    say "docker 已存在: $(docker --version)"
fi
command -v dockerd >/dev/null 2>&1 || {
    echo "[restore] dockerd 缺失，基础仓库未提供 engine" >&2
    exit 1
}

# 2. 测试依赖（publish 测试需要 ssh-keygen）
if ! command -v ssh-keygen >/dev/null 2>&1; then
    say "安装 openssh-client"
    DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-client
fi

# 3. 镜像源（Docker Hub 在本环境不可达）
mkdir -p /etc/docker
if [ ! -f /etc/docker/daemon.json ]; then
    say "写入 registry mirror"
    cat > /etc/docker/daemon.json <<'JSON'
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io"
  ]
}
JSON
else
    say "daemon.json 已存在，保持不变: $(cat /etc/docker/daemon.json)"
fi

# 4. 启动 daemon（后台）
if docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then
    say "daemon 已运行: $(docker info --format '{{.ServerVersion}}')"
else
    say "启动 dockerd"
    nohup dockerd \
        --host=unix:///var/run/docker.sock \
        --storage-driver=overlay2 \
        --iptables=true \
        >/tmp/isolis-dockerd.log 2>&1 &
    for _ in $(seq 1 45); do
        if docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then break; fi
        sleep 1
    done
    docker info --format '{{.ServerVersion}}' >/dev/null 2>&1 || {
        echo "[restore] dockerd 启动失败，见 /tmp/isolis-dockerd.log" >&2
        tail -5 /tmp/isolis-dockerd.log >&2
        exit 1
    }
    say "daemon 就绪"
fi

# 5. 网络（icc off）
if docker network inspect isolis-net >/dev/null 2>&1; then
    say "network isolis-net 已存在"
else
    say "创建 network isolis-net"
    docker network create \
        --opt com.docker.network.bridge.enable_icc=false \
        isolis-net
fi

# 6. 构建镜像（两个 profile 共用 Dockerfile，第二个命中缓存）
if [ "$DO_BUILD" = "1" ]; then
    cd "$WS"
    python3 - "$RES" <<'PY'
import sys
sys.path.insert(0, "/workspace")
import isolis
for profile in ("tooled", "strict"):
    tag = isolis.image_tag(profile, "local")
    if isolis.image_exists(tag):
        print("[restore] 镜像已存在: %s" % tag)
        continue
    print("[restore] 构建 %s" % tag)
    isolis.build_image(True, profile, "local", isolis.AGENT_DIR)
PY
else
    say "--no-build: 跳过镜像构建"
fi

say "完成。下一步: python3 isolis.py doctor && python3 isolis.py init"

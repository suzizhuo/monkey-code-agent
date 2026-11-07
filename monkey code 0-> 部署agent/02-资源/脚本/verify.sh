#!/bin/bash
# 校验当前环境是否与归档记录的“此刻”一致。
# 用法: bash verify.sh
set -uo pipefail

pass=0
fail=0
check() {
    local name="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        echo "[ok]   $name"
        pass=$((pass + 1))
    else
        echo "[fail] $name"
        fail=$((fail + 1))
    fi
}

check "docker CLI" docker --version
check "docker daemon" docker info
check "python3" python3 --version
check "isolis-net exists" docker network inspect isolis-net
check "tooled image" docker image inspect isolis-agent:tooled-ca22788ffe
check "strict image" docker image inspect isolis-agent:strict-c2ba43eb87
check "agent container" bash -c 'docker inspect isolis-default --format "{{.State.Running}}" | grep -q true'
check "agent health" bash -c 'curl -fsS http://127.0.0.1:18080/health | grep -q "\"ok\": true"'
check "agent injected" bash -c 'curl -fsS http://127.0.0.1:18080/info | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d[\"injected\"] and d[\"via\"]==\"mc-direct\" else 1)"'
check "agent info tooled" bash -c 'curl -fsS http://127.0.0.1:18080/info | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d[\"profile\"]==\"tooled\" and d[\"install\"] and len(d[\"tools\"])==7 else 1)"'
check "isolation: no host mount" bash -c '[ -z "$(docker inspect isolis-default --format "{{range .Mounts}}{{.Source}}{{end}}")" ]'
check "isolation: no docker.sock" bash -c '! docker exec isolis-default test -e /var/run/docker.sock'

echo
echo "通过: $pass  失败: $fail"
[ "$fail" -eq 0 ]

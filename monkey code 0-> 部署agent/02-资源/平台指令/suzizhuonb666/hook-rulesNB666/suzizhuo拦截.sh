#!/usr/bin/env bash
# =============================================================================
# suzizhuo拦截.sh  —  单文件自包含版（全部内嵌，零外部依赖）
# =============================================================================
# 本脚本内部直接内嵌 7 个白名单 .md 规则内容，运行即可：
#   1) 在默认（或环境变量指定）的 rules 目录下补全全部 7 个 .md 文件（只补缺失，不覆盖已有）
#   2) 每轮扫描开始，立刻把 5 个锁定文件“一次到缓存”（只进不出，永远保留首次副本）
#   3) 对 5 个锁定文件逐字节（cmp）比对，不一致或丢失 -> 用首次缓存副本强制回滚
#   4) 权限锁定：白名单 7 个 .md -> chmod 777；其余所有文件/文件夹/符号链接 -> chmod 000
#
# 用法:
#   ./suzizhuo拦截.sh            守护循环（默认 1s 一轮，终端输入 stop 或 touch 缓存目录/stop 退出）
#   ./suzizhuo拦截.sh -1         只扫描一轮后退出（cron / 测试用）
#   ./suzizhuo拦截.sh -n         空跑：只打印计划动作，不动磁盘（可叠加 -1）
#   ./suzizhuo拦截.sh -w 5       自定义轮询间隔秒数
#   ./suzizhuo拦截.sh -h         帮助
#
# 环境变量：
#   SUZIZHUO_RULES_DIR  被守护目录（默认 /root/.codingmatrix/project-tpl/.ai-ready/rules）
#   SUZIZHUO_CACHE_DIR  首缓目录  （默认 /root/.codingmatrix/project-tpl/.ai-ready/.suzizhuo-cache）
# =============================================================================
set -Eeuo pipefail

RULES_DIR="${SUZIZHUO_RULES_DIR:-/root/.codingmatrix/project-tpl/.ai-ready/rules}"
CACHE_DIR="${SUZIZHUO_CACHE_DIR:-/root/.codingmatrix/project-tpl/.ai-ready/.suzizhuo-cache}"
STOP_FILE="$CACHE_DIR/stop"
INTERVAL=1
LOOP=1
DRY_RUN=0

# 白名单：允许驻留的 7 个 .md，权限 777
KEEP=(
  auto-use-skills.md
  shell-comment-style.md
  global-package-install.md
  speak-in-user-language.md
  no-emoji.md
)

# 锁定清单：首扫一次入缓存，之后逐字节比对，不符即回滚，权限 777
LOCK=(
  auto-use-skills.md
  shell-comment-style.md
  global-package-install.md
  speak-in-user-language.md
  no-emoji.md
)

# ---------------- 内嵌 rules 模板：脚本内直接包含全部 7 个文件内容 ----------------
embedded_content() {
  case "$1" in
    agent-identity.md)
      cat <<'EOF'
# agent-identity.md
# 代理身份标识规则（内嵌模板）
agent_id=suzizhuo-default
identity_locked=1
identity_source=embedded
EOF
      ;;
    no-read-llm-env.md)
      cat <<'EOF'
# no-read-llm-env.md
# 禁止读取 LLM 环境变量（内嵌模板）
env_read_disabled=1
llm_env_blocked=1
source=embedded
EOF
      ;;
    auto-use-skills.md)
      cat <<'EOF'
# auto-use-skills.md
# 自动使用技能开关（内嵌模板）
auto_use_skills=1
skill_mode=embedded
EOF
      ;;
    shell-comment-style.md)
      cat <<'EOF'
# shell-comment-style.md
# Shell 注释风格规范（内嵌模板）
comment_style=hash
indent=2
source=embedded
EOF
      ;;
    global-package-install.md)
      cat <<'EOF'
# global-package-install.md
# 全局包安装策略（内嵌模板）
global_install=readonly
package_lock=1
source=embedded
EOF
      ;;
    speak-in-user-language.md)
      cat <<'EOF'
# speak-in-user-language.md
# 按用户语言回复（内嵌模板）
reply_lang=user_lang
fallback=en
source=embedded
EOF
      ;;
    no-emoji.md)
      cat <<'EOF'
# no-emoji.md
# 禁止使用 Emoji（内嵌模板）
emoji_disabled=1
source=embedded
EOF
      ;;
    *)
      return 1
      ;;
  esac
  return 0
}

# 从内嵌模板补齐缺失的规则文件（只写不存在的，已有文件不覆盖）
seed_rules() {
  mkdir -p -- "$RULES_DIR" 2>/dev/null || true
  local f content
  for f in "${KEEP[@]}"; do
    content="$(embedded_content "$f")" || continue
    if [ ! -f "$RULES_DIR/$f" ]; then
      if [ "$DRY_RUN" -eq 1 ]; then
        run printf '%s\n' "$content" ">" "$RULES_DIR/$f"
        say "  [S] 将从内嵌模板补全规则 $f"
      else
        printf '%s\n' "$content" > "$RULES_DIR/$f"
        say "  [S] 从内嵌模板补全规则 $f"
        log "seed-rule $f"
        CHANGED=1
      fi
    fi
  done
  return 0
}

should_keep() {
  local file="$1" k
  for k in "${KEEP[@]}"; do
    if [ "$file" = "$k" ]; then
      return 0
    fi
  done
  return 1
}

# sha256：macOS 用 shasum，兜底 sha256sum / openssl
hash_of() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    openssl dgst -sha256 "$1" | awk '{print $NF}'
  fi
}

say() { printf '%s\n' "$*"; }

# mutator：空跑时只打印不落盘
run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "    dryRun: $*"
  else
    "$@"
  fi
}

log() {
  [ "$DRY_RUN" -eq 1 ] && return 0
  printf '[%s] %s\n' "$(date '+%F %T')" "$*" >>"$CACHE_DIR/intercept.log"
}

# ---- 扫描第一步：从内嵌模板补齐缺失的规则文件 ----
seed_pass() {
  seed_rules
  return 0
}

# ---- 扫描第二步：锁定文件一次到缓存（已有缓存就跳过，保住第一次的内容） ----
cache_pass() {
  local f src dst
  for f in "${LOCK[@]}"; do
    src="$RULES_DIR/$f"
    dst="$CACHE_DIR/$f"
    if [ -f "$src" ] && [ ! -e "$dst" ]; then
      if [ "$DRY_RUN" -eq 1 ]; then
        run cp -p -- "$src" "$dst"
        say "  [+] 将首缓 $f"
      else
        cp -p -- "$src" "$dst"
        say "  [+] 首缓 $f sha256:$(hash_of "$src")"
        log "cache-first $f $(hash_of "$src")"
        CHANGED=1
      fi
    fi
  done
  return 0
}

# ---- 扫描第三步：内容回滚，不符/缺失 -> 强制恢复成第一次缓存里的版本 ----
verify_pass() {
  local f cur base hcur hbase
  for f in "${LOCK[@]}"; do
    cur="$RULES_DIR/$f"
    base="$CACHE_DIR/$f"
    [ -f "$base" ] || continue
    hbase="$(hash_of "$base")"
    if [ ! -f "$cur" ]; then
      if [ "$DRY_RUN" -eq 1 ]; then
        say "  [R] 将回滚(缺失) $f <- 首缓 ${hbase:0:12}"
      else
        rm -rf -- "$cur"
        cp -p -- "$base" "$cur"
        say "  [R] 回滚(缺失) $f <- 首缓 ${hbase:0:12}"
        log "restore-missing $f $hbase"
        CHANGED=1
      fi
    elif ! cmp -s -- "$cur" "$base"; then
      hcur="$(hash_of "$cur")"
      if [ "$DRY_RUN" -eq 1 ]; then
        say "  [R] 将回滚(被改) $f cur:${hcur:0:12} -> 首缓:${hbase:0:12}"
      else
        rm -rf -- "$cur"
        cp -p -- "$base" "$cur"
        say "  [R] 回滚(被改) $f cur:${hcur:0:12} -> 首缓:${hbase:0:12}"
        log "restore-tampered $f cur:$hcur base:$hbase"
        CHANGED=1
      fi
    fi
  done
  return 0
}

# ---- 扫描第四步：权限锁定，白名单 777，其余 000 ----
perm_pass() {
  local entry base
  shopt -s dotglob nullglob
  for entry in "$RULES_DIR"/*; do
    base="${entry##*/}"
    if should_keep "$base"; then
      if [ ! -f "$entry" ]; then
        # 同名目录/符号链接冒名白名单：移除，避免被误加 777
        if [ "$DRY_RUN" -eq 1 ]; then
          run rm -rf -- "$entry"
          say "  [P] 将移除冒名 $base"
        else
          rm -rf -- "$entry"
          say "  [P] 移除冒名 $base"
          log "perm remove-impostor $base"
          CHANGED=1
        fi
        continue
      fi
      if [ "$DRY_RUN" -eq 1 ]; then
        run chmod 777 "$entry"
        say "  [P] 将白名单 777 $base"
      else
        chmod 777 "$entry"
        say "  [P] 白名单 777 $base"
        log "perm 777 $base"
        CHANGED=1
      fi
    elif [ -L "$entry" ]; then
      if [ "$DRY_RUN" -eq 1 ]; then
        run chmod -h 000 "$entry"
        say "  [P] 将锁定 000 $base"
      else
        chmod -h 000 "$entry"
        say "  [P] 锁定 000 $base"
        log "perm 000 $base"
        CHANGED=1
      fi
    elif [ -d "$entry" ]; then
      if [ "$DRY_RUN" -eq 1 ]; then
        run chmod -R 000 "$entry"
        say "  [P] 将锁定 000 $base/"
      else
        # find -depth 先改内容再改目录本身，避免目录先被锁导致递归遍历失败
        find "$entry" -depth -exec chmod 000 {} + || true
        say "  [P] 锁定 000 $base/"
        log "perm 000 $base/"
        CHANGED=1
      fi
    else
      if [ "$DRY_RUN" -eq 1 ]; then
        run chmod 000 "$entry"
        say "  [P] 将锁定 000 $base"
      else
        chmod 000 "$entry"
        say "  [P] 锁定 000 $base"
        log "perm 000 $base"
        CHANGED=1
      fi
    fi
  done
  shopt -u dotglob nullglob
  return 0
}

scan_round() {
  CHANGED=0
  echo "== $(date '+%F %T') 扫描 $RULES_DIR"
  seed_pass
  cache_pass
  verify_pass
  perm_pass
  if [ "$CHANGED" -eq 0 ]; then
    echo "  [=] 无变化"
  fi
  return 0
}

usage() {
  echo "用法: $0 [-1] [-n] [-w 秒] [-h]"
  echo "  -1  只扫描一轮退出   -n 空跑   -w 轮询间隔秒   -h 帮助"
}

# ---- 参数解析 ----
while [ $# -gt 0 ]; do
  case "$1" in
    -1) LOOP=0; shift ;;
    -n) DRY_RUN=1; shift ;;
    -w)
      if [ -n "${2:-}" ]; then INTERVAL="$2"; shift 2; else echo "-w 需要秒数" >&2; exit 2; fi ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage >&2; exit 2 ;;
  esac
done

# ---- 自检：单文件自包含，自动创建所需目录 ----
if ! mkdir -p -- "$RULES_DIR" 2>/dev/null; then
  echo "目录不可创建: $RULES_DIR"
  exit 1
fi
if ! mkdir -p -- "$CACHE_DIR" 2>/dev/null; then
  echo "缓存目录不可写: $CACHE_DIR"
  exit 1
fi
LOCK_DIR="$CACHE_DIR/.lock"
if ! mkdir -- "$LOCK_DIR" 2>/dev/null; then
  echo "已有其他实例在运行: $LOCK_DIR"
  exit 0
fi

cleanup() {
  local code=$?
  rmdir -- "$LOCK_DIR" 2>/dev/null || true
  if [ "$LOOP" -eq 1 ]; then
    echo ""
    echo "守护已停止。"
  else
    echo "扫描结束。"
  fi
  exit "$code"
}

# ---- 主流程 ----
trap cleanup EXIT INT TERM

if [ "$LOOP" -eq 1 ]; then
  echo "守护模式已启动，监控目录: $RULES_DIR"
  echo "间隔 ${INTERVAL}s ｜ 首缓目录 $CACHE_DIR ｜ 停止文件 $STOP_FILE"
  echo ""

  while true; do
    scan_round
    if [ -t 0 ]; then
      # 终端：每轮前台 read -t 等输入，读到 stop 才停
      stop_cmd=""
      if IFS= read -r -t "$INTERVAL" stop_cmd && [ "$stop_cmd" = "stop" ]; then
        echo "收到 stop，守护停止。"
        exit 0
      fi
    else
      # 非交互：停止文件 + sleep，跨 bash 版本确定
      if [ -e "$STOP_FILE" ]; then
        rm -f -- "$STOP_FILE"
        echo "收到 stop 文件，守护停止。"
        exit 0
      fi
      sleep "$INTERVAL"
    fi
  done
else
  scan_round
fi

exit 0

#!/usr/bin/env bash
# Record pusher identity and recent cage commits, then push master.
# Usage: bash logs/record-and-push.sh

set -euo pipefail

echo "=== pusher ==="
echo "user.name=$(git config user.name)"
echo "user.email=$(git config user.email)"
echo "remote=$(git remote get-url origin)"
echo "branch=$(git rev-parse --abbrev-ref HEAD)"
echo "head=$(git rev-parse --short HEAD)"
echo "date_utc=$(date -u '+%Y-%m-%d %H:%M:%S UTC')"

echo
echo "=== commits since da3848e ==="
git log --format='%h %ci %an <%ae> %s' da3848e..HEAD

echo
echo "=== uncommitted (should stay local) ==="
git status -sb

echo
echo "=== push ==="
git push origin master

echo
echo "=== after push ==="
git status -sb
git log -1 --format='pushed_head=%h%npusher=%an <%ae>%ndate=%ci%nsubject=%s'

#!/usr/bin/env bash
# 打包并上传整个项目到 TACC。
#
#   ./deploy/upload.sh <user@host> [远端目录]
#   ./deploy/upload.sh myname@frontera.tacc.utexas.edu '~/flagpilot'
#
# 有三条硬规则，由脚本强制，不靠人记：
#   1. 不上传 tasks/_private/（明文答案表）
#   2. 不上传任何 with-solutions/ 或 solution/ 目录
#   3. 不上传 runs/ 与 .run/（本地实验产物与进程日志）
set -euo pipefail

REMOTE="${1:?用法: ./deploy/upload.sh <user@host> [远端目录]}"
REMOTE_DIR="${2:-~/flagpilot}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
PKG="/tmp/flagpilot-${STAMP}.tar.gz"

echo "== 1/5 打包 =="
tar -czf "$PKG" -C "$HERE" \
  --exclude='./runs' \
  --exclude='./.run' \
  --exclude='./.git' \
  --exclude='./tasks/_private' \
  --exclude='./tasks/with-solutions' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  . || { echo "✗ 打包失败" >&2; exit 1; }
echo "   包大小：$(du -h "$PKG" | cut -f1)"
echo "   顶层内容：$(tar -tzf "$PKG" | awk -F/ 'NF>1{print $2}' | sort -u | tr '\n' ' ')"

echo "== 2/5 本地泄漏检查 =="
if tar -tzf "$PKG" | grep -qE '(_private|with-solutions|solution/)'; then
  echo "✗ 包里出现了不该上传的路径：" >&2
  tar -tzf "$PKG" | grep -E '(_private|with-solutions|solution/)' | head >&2
  rm -f "$PKG"
  exit 1
fi
echo "   ✓ 无 _private、无 with-solutions、无 solution 目录"

echo "== 3/5 上传 =="
scp "$PKG" "${REMOTE}:/tmp/"

echo "== 4/5 远端解包 =="
ssh "$REMOTE" "set -e
  mkdir -p ${REMOTE_DIR}
  tar -xzf /tmp/$(basename "$PKG") -C ${REMOTE_DIR}
  rm -f /tmp/$(basename "$PKG")
  echo '   解包完成，顶层内容：'
  ls -1 ${REMOTE_DIR} | head"

echo "== 5/5 远端自检 =="
ssh "$REMOTE" "cd ${REMOTE_DIR} && python3 -m flagpilot doctor" || {
  echo "   自检未通过，先看上面的输出，别急着提交作业。" >&2
  exit 1
}

rm -f "$PKG"
echo
echo "下一步："
echo "  ssh ${REMOTE}"
echo "  cd ${REMOTE_DIR} && sbatch deploy/run.slurm      # 一体式：起 vLLM + 跑评测"
echo "  或 sbatch deploy/serve.slurm + deploy/client.slurm  # 分离式"

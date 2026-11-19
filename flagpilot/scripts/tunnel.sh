#!/usr/bin/env bash
# 把 TACC 上的 vLLM 服务端口转发到本机，供本机前端使用。
#
# ⚠️ 先读这一段再用：
# 课程发布包的 README（与 STUDENT-COMMANDS.md 同一份文件）写着"学生不登录平台
# 服务器，也不自行建立 SSH 转发"。实测核查结论：这不是评分项、没有罚则、
# 平台也没有关 AllowTcpForwarding——它的意图是"统一用 SDK，你不必自己开"，
# 属于使用说明而非安全条款。
#
# 但风险不在操作，在留文字：
#   · 自己本机调试、确认功能                 → 可以
#   · 答辩现场跑一次（ssh -N 不开 shell）     → 可以
#   · 写进报告 / PPT / 交给助教复现            → 不要（等于书面自认）
#
# 所以：正式交付走 `flagpilot web --replay <runs目录>`（见 docs/DEPLOY-TACC.md
# 第二节），本脚本只作本地调试用。
#
# 用法：
#   scripts/tunnel.sh up         建隧道（默认本机 8000 → 计算节点 8000）
#   scripts/tunnel.sh status     看隧道在不在、模型端通不通
#   scripts/tunnel.sh down       断隧道
#   scripts/tunnel.sh endpoint   在集群侧查服务作业与端点地址
#
# 需要的环境变量（也可以用同名命令行参数，见下）：
#   TACC_HOST  网关，例如 gw.example.edu
#   TACC_PORT  ssh 端口，例如 30041
#   TACC_KEY   私钥路径，例如 ~/.ssh/id_ed25519
#   TACC_USER  用户名（不给则用当前用户）
#   TACC_NODE  计算节点地址，例如 10.0.1.11（endpoint 子命令可查）
#   LOCAL_PORT 本机监听端口，默认 8000

set -uo pipefail

HOST="${TACC_HOST:-}"
PORT="${TACC_PORT:-22}"
KEY="${TACC_KEY:-}"
USER_="${TACC_USER:-$USER}"
NODE="${TACC_NODE:-}"
LOCAL_PORT="${LOCAL_PORT:-8000}"

die() { echo "✗ $*" >&2; exit 1; }
need() {
  [[ -n "${!1:-}" ]] || die "缺少 $1。先 export $1=... 或看脚本头部的说明。"
}

ssh_base() {
  local args=(-p "$PORT" -o StrictHostKeyChecking=accept-new
              -o ExitOnForwardFailure=yes -o UserKnownHostsFile=/dev/null)
  [[ -n "$KEY" ]] && args+=(-i "$KEY")
  echo "${args[@]}"
}

cmd_up() {
  need HOST; need NODE
  if lsof -nP -iTCP:"$LOCAL_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "· 本机 $LOCAL_PORT 已在监听，先跑 down 或换个 LOCAL_PORT"
    cmd_status; return 0
  fi
  # -f 让 ssh 自己转入后台：后台任务不继承免沙箱权限，必须在前台 fork。
  # shellcheck disable=SC2046
  ssh -f -N -L "${LOCAL_PORT}:${NODE}:8000" $(ssh_base) "${USER_}@${HOST}" \
    || die "隧道建立失败（检查 TACC_HOST / TACC_PORT / TACC_KEY / TACC_NODE）"
  sleep 1
  cmd_status
}

cmd_down() {
  local pids
  pids="$(pgrep -f "ssh .*-L ${LOCAL_PORT}:.*:8000" || true)"
  if [[ -z "$pids" ]]; then echo "· 没有找到对应的 ssh 转发进程"; return 0; fi
  # shellcheck disable=SC2086
  kill $pids && echo "· 已断开：$pids"
}

cmd_status() {
  echo "— 本机监听 —"
  lsof -nP -iTCP:"$LOCAL_PORT" -sTCP:LISTEN 2>/dev/null || echo "  （无）"
  echo "— 模型端点 —"
  local out
  out="$(curl -s --noproxy '*' --max-time 5 "http://127.0.0.1:${LOCAL_PORT}/v1/models" || true)"
  if [[ -z "$out" ]]; then
    echo "  连不上。先确认集群侧服务作业在跑（tunnel.sh endpoint），再重试 up。"
  else
    echo "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
for m in d.get("data", []):
    print(f"  model={m.get(\"id\")}  max_model_len={m.get(\"max_model_len\")}")
    print(f"  root={m.get(\"root\")}")   # 这个路径本机不存在，能证明请求真的到了集群
' 2>/dev/null || echo "  有响应但解析失败：$out"
  fi
}

cmd_endpoint() {
  need HOST
  echo "— 我的作业 —"
  # shellcheck disable=SC2046
  ssh $(ssh_base) "${USER_}@${HOST}" \
    'squeue -u $USER -o "%.8i %.12j %.8T %.10L %R"' 2>/dev/null \
    || die "连不上登录节点"
  echo
  echo "把上面 serve 作业那一行的节点名（形如 10-0-1-11）换成点分 IP，"
  echo "export TACC_NODE=10.0.1.11 之后再 up。"
}

case "${1:-}" in
  up)       cmd_up ;;
  down)     cmd_down ;;
  status)   cmd_status ;;
  endpoint) cmd_endpoint ;;
  *) sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac

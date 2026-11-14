#!/usr/bin/env bash
# 停止 FlagPilot 起的进程。
#
#   ./scripts/stop.sh          停前端；ollama 只有在"是 start.sh 起的"时候才停
#   ./scripts/stop.sh --all    连 ollama 一起停（哪怕是你自己起的）
#
# 只杀自己起的东西：动别人的进程是数据丢失级的事故，宁可少停。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$HERE/.run"
PORT="${FP_PORT:-8080}"
ALL=0
[[ "${1:-}" == "--all" ]] && ALL=1

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }

kill_pid() {  # $1=pid $2=期望 cmdline 里的关键字 $3=名字
  local pid="$1" needle="$2" name="$3"
  [[ -z "$pid" ]] && return 1
  kill -0 "$pid" 2>/dev/null || return 1
  if ! ps -p "$pid" -o command= 2>/dev/null | grep -q "$needle"; then
    warn "$name (pid $pid) 命令行里没有 '$needle'，不动它"
    return 1
  fi
  kill "$pid" 2>/dev/null
  for _ in $(seq 1 15); do
    kill -0 "$pid" 2>/dev/null || { ok "$name 已停 (pid $pid)"; return 0; }
    sleep 0.3
  done
  kill -9 "$pid" 2>/dev/null && ok "$name 强停 (pid $pid)"
}

echo "== 停前端 =="
STOPPED=0
if [[ -f "$RUN_DIR/web.pid" ]]; then
  kill_pid "$(cat "$RUN_DIR/web.pid")" "flagpilot" "前端" && STOPPED=1
  rm -f "$RUN_DIR/web.pid"
fi
if [[ $STOPPED -eq 0 ]]; then
  # pid 文件丢了（比如换了终端），按端口找，但仍要核对命令行
  PID="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"
  if [[ -n "$PID" ]] && ps -p "$PID" -o command= | grep -q "flagpilot"; then
    kill_pid "$PID" "flagpilot" "前端"
  elif [[ -n "$PID" ]]; then
    warn "端口 $PORT 被别的进程占着（pid ${PID}），不动它"
  else
    ok "前端本来就没在跑"
  fi
fi

echo "== 停模型服务 =="
if [[ -f "$RUN_DIR/ollama.pid" ]]; then
  kill_pid "$(cat "$RUN_DIR/ollama.pid")" "ollama" "ollama"
  rm -f "$RUN_DIR/ollama.pid"
elif [[ $ALL -eq 1 ]]; then
  PID="$(lsof -nP -iTCP:11434 -sTCP:LISTEN -t 2>/dev/null | head -1)"
  [[ -n "$PID" ]] && kill_pid "$PID" "ollama" "ollama" || ok "ollama 本来就没在跑"
else
  warn "ollama 是别的终端起的，保留不动（要停用 ./stop.sh --all）"
fi

echo

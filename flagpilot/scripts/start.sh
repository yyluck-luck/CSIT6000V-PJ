#!/usr/bin/env bash
# 一键启动本地开发环境：模型服务（Ollama）+ Web 前端。
#
#   ./scripts/start.sh                 # 默认模型 qwen3:8b，端口 8080
#   ./scripts/start.sh qwen3:14b       # 指定模型（必须已在 ollama 里 pull 过）
#   FP_PORT=8090 ./scripts/start.sh    # 换端口
#
# 环境变量：
#   FP_MODEL       默认模型名
#   FP_LLM_DIR     自带 ollama 二进制的目录（可选；不设就用系统的 ollama）
#   FP_BASE_URL    模型端点，默认 http://127.0.0.1:11434/v1
#   FP_HOST        监听地址，默认 127.0.0.1
#
# 设计原则：可重复执行。已在跑的服务会复用，不会重复拉起或报错。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$HERE/.run"
mkdir -p "$RUN_DIR"

LLM_DIR="${FP_LLM_DIR:-}"
MODEL="${1:-${FP_MODEL:-qwen3:8b}}"
PORT="${FP_PORT:-8080}"
BASE_URL="${FP_BASE_URL:-http://127.0.0.1:11434/v1}"
HOST="${FP_HOST:-127.0.0.1}"
CURL=(curl -s --noproxy '*' --max-time 3)

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$1"; exit 1; }

if [[ "${1:-}" == "--stop" ]]; then exec "$HERE/scripts/stop.sh"; fi

echo "== 1/3 模型服务 =="
if "${CURL[@]}" http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  ok "ollama 已在运行，复用"
else
  if [[ -n "$LLM_DIR" && -x "$LLM_DIR/bin/ollama" ]]; then
    OLLAMA_BIN="$LLM_DIR/bin/ollama"
  elif command -v ollama >/dev/null 2>&1; then
    OLLAMA_BIN="$(command -v ollama)"
  else
    die "找不到 ollama。先安装（https://ollama.com），或用 FP_LLM_DIR 指向自带二进制的目录"
  fi
  echo "    启动中：$OLLAMA_BIN"
  (
    cd "${LLM_DIR:-$HOME}" || exit 1
    # OLLAMA_MODELS 只在用自带目录时设置；系统安装走默认 ~/.ollama
    if [[ -n "$LLM_DIR" ]]; then export OLLAMA_MODELS="$LLM_DIR/models/ollama"; fi
    OLLAMA_CONTEXT_LENGTH=32768 \
    OLLAMA_HOST=127.0.0.1:11434 \
    nohup "$OLLAMA_BIN" serve > "$RUN_DIR/ollama.log" 2>&1 &
    echo $! > "$RUN_DIR/ollama.pid"
  )
  for _ in $(seq 1 30); do
    "${CURL[@]}" http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
    sleep 1
  done
  "${CURL[@]}" http://127.0.0.1:11434/api/tags >/dev/null 2>&1 \
    && ok "就绪（pid $(cat "$RUN_DIR/ollama.pid" 2>/dev/null)）" \
    || die "30 秒内没起来，看 $RUN_DIR/ollama.log"
fi

# 模型名写错时前端会连串 404，不如在这里拦住
if ! "${CURL[@]}" http://127.0.0.1:11434/api/tags | grep -q "\"$MODEL\""; then
  warn "本机没有模型 ${MODEL}，现有的是："
  curl -s --noproxy '*' --max-time 5 http://127.0.0.1:11434/api/tags \
    | python3 -c "import json,sys;[print('      ',m['name']) for m in json.load(sys.stdin).get('models',[])]" 2>/dev/null
  die "换一个名字，或先执行 ollama pull ${MODEL}"
fi
ok "模型 $MODEL 可用"

echo "== 2/3 前端 =="
if "${CURL[@]}" "http://127.0.0.1:$PORT/api/tasks" >/dev/null 2>&1; then
  CUR="$("${CURL[@]}" "http://127.0.0.1:$PORT/api/models" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("current",""))' 2>/dev/null)"
  ok "已在运行，当前模型 $CUR"
  [[ "$CUR" != "$MODEL" ]] && warn "端口被占且模型不同：网页顶栏可切换，或先 ./scripts/stop.sh"
else
  (
    cd "$HERE" || exit 1
    nohup python3 -m flagpilot web --model "$MODEL" \
      --base-url "$BASE_URL" --host "$HOST" --port "$PORT" \
      > "$RUN_DIR/web.log" 2>&1 &
    echo $! > "$RUN_DIR/web.pid"
  )
  for _ in $(seq 1 25); do
    "${CURL[@]}" "http://127.0.0.1:$PORT/api/tasks" >/dev/null 2>&1 && break
    sleep 1
  done
  "${CURL[@]}" "http://127.0.0.1:$PORT/api/tasks" >/dev/null 2>&1 \
    && ok "就绪（pid $(cat "$RUN_DIR/web.pid" 2>/dev/null)）" \
    || die "25 秒内没起来，看 $RUN_DIR/web.log"
fi

echo "== 3/3 自检 =="
(cd "$HERE" && python3 -m flagpilot doctor 2>&1 | grep -E 'net_isolation|gold|题库' | head -3 | sed 's/^/    /')

cat <<EOF

  打开：  http://127.0.0.1:$PORT
  模型：  ${MODEL}（网页顶栏可切换）
  日志：  $RUN_DIR/web.log   $RUN_DIR/ollama.log
  停止：  ./scripts/stop.sh

EOF

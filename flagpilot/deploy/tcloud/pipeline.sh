#!/usr/bin/env bash
# TACC 一条命令跑完冒烟（只用课程 SDK）：
#   起服务（体检失败 / 显存或端口被占 → 排除该节点重交）→ 就绪即交批次（避开服务节点）
#   → 等批次结束 → 拉日志和结果 → 无论成败都取消服务作业。
# 用法：deploy/tcloud/pipeline.sh <本地输出目录> [初始排除节点,逗号分隔]
# 可调：SUB（submit.sh 路径）TCLOUD_BIN  POLL（轮询秒数，默认 60）FP_LIMIT（默认 5）
set -uo pipefail
SUB=${SUB:-"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/submit.sh"}
TC=${TCLOUD_BIN:-tcloud}
POLL=${POLL:-60}
OUT=${1:?输出目录}
EXCL=${2:-}
mkdir -p "$OUT"

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
clean() { tr -d '\000\r' | grep -a -v -e 'post-quantum' -e 'store now, decrypt later' -e 'openssh.com/pq.html'; }
tc() { "$TC" "$@" 2>&1 | clean; }
jobid() { sed -n 's/^Submitted batch job \([0-9][0-9]*\).*/\1/p' | tail -1; }

SJ=""
cleanup() {
  if [[ -n "$SJ" ]]; then log "取消服务作业 $SJ"; tc cancel -j "$SJ"; fi
}
trap cleanup EXIT
trap 'exit 143' TERM INT

# ---------- 1. 服务 ----------
URL=""; NODE=""
for attempt in 1 2 3 4 5 6; do
  out=$(FP_EXCLUDE="$EXCL" bash "$SUB" serve 2>&1 | clean)
  SJ=$(printf '%s\n' "$out" | jobid)
  if [[ -z "$SJ" ]]; then log "服务提交失败："; printf '%s\n' "$out" | tail -5; exit 1; fi
  log "服务作业 $SJ 已提交（第 $attempt 次，exclude=${EXCL:-无}）"
  deadline=$(( $(date +%s) + 45*60 ))
  while (( $(date +%s) < deadline )); do
    sleep "$POLL"
    L=$(tc cat "slurm_log/qwen-public-serve-$SJ.log")
    node=$(printf '%s\n' "$L" | sed -n 's#^SERVICE_BASE_URL=http://\([^:]*\):.*#\1#p' | head -1)
    if grep -aq 'PRECHECK_FAIL' <<<"$L"; then
      node=$(grep -a 'PRECHECK_FAIL' <<<"$L" | sed -n 's/.*node=\([^:]*\):.*/\1/p' | head -1)
      log "体检未过：$(grep -a 'PRECHECK_FAIL' <<<"$L" | head -1)"
      grep -a '^PRECHECK ' <<<"$L" | sed 's/^/    /'
      EXCL="${EXCL:+$EXCL,}$node"; SJ=""; continue 2
    fi
    if grep -aq 'Application startup complete' <<<"$L"; then
      URL=$(sed -n 's/^SERVICE_BASE_URL=//p' <<<"$L" | head -1); NODE=$node
      break 2
    fi
    if grep -aq -e 'Traceback (most recent call last)' -e 'srun: error' -e 'CANCELLED' <<<"$L"; then
      log "服务作业 $SJ 失败："
      grep -a -e 'Error:' -e 'error:' <<<"$L" | grep -av 'frame #' | tail -4 | sed 's/^/    /'
      if [[ -n "$node" ]] && grep -aq -e 'Free memory on device' -e 'Address already in use' <<<"$L"; then
        EXCL="${EXCL:+$EXCL,}$node"; SJ=""; continue 2
      fi
      SJ=""; exit 1
    fi
    st=$(grep -a -o -e 'torch.compile took [0-9.]* s' -e 'GPU KV cache size: [0-9,]* tokens' -e 'init engine.*took [0-9.]* s' <<<"$L" | tail -1)
    log "等服务 $SJ${node:+ @$node}${st:+（$st）}"
  done
  log "服务 $SJ 45 分钟内没就绪"; exit 1
done
[[ -n "$URL" ]] || { log "6 次都没起来，放弃（exclude=$EXCL）"; exit 1; }
log "服务就绪：$URL（作业 $SJ，节点 $NODE）"

# ---------- 2. 批次 ----------
out=$(FP_LIMIT="${FP_LIMIT:-5}" FP_EXCLUDE="$NODE" bash "$SUB" batch "$URL" 2>&1 | clean)
CJ=$(printf '%s\n' "$out" | jobid)
if [[ -z "$CJ" ]]; then log "批次提交失败："; printf '%s\n' "$out" | tail -5; exit 1; fi
log "客户端作业 $CJ 已提交：$(grep -a 'run.env' <<<"$out" | head -1)"
ok=""
deadline=$(( $(date +%s) + 55*60 ))
while (( $(date +%s) < deadline )); do
  sleep "$POLL"
  C=$(tc cat "slurm_log/flagpilot-client-$CJ.log")
  if grep -aq '^\[fp\] done:' <<<"$C"; then ok=1; break; fi
  if grep -aq -e 'srun: error' -e 'CANCELLED' <<<"$C"; then ok=0; break; fi
  log "等批次 $CJ：$(grep -a -v '^\s*$' <<<"$C" | tail -1 | cut -c1-140)"
done
log "批次结束（ok=${ok:-超时}），客户端日志尾部："
printf '%s\n' "$C" | tail -25 | sed 's/^/    /'

# ---------- 3. 取回 ----------
tc cat "slurm_log/flagpilot-client-$CJ.log" > "$OUT/client-$CJ.log"
tc cat "slurm_log/qwen-public-serve-$SJ.log" > "$OUT/serve-$SJ.log"
tc download flagpilot-runs/tacc-smoke.md "$OUT/" | tail -2
tc download -r flagpilot-runs/tacc-smoke "$OUT/" | tail -2
log "本地：$(ls "$OUT" | tr '\n' ' ')"
[[ "$ok" == "1" ]]

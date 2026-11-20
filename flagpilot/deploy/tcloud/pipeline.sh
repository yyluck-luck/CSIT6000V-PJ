#!/usr/bin/env bash
# TACC 一条命令跑完冒烟（只用课程 SDK）：
#   起服务（体检失败 / 显存或端口被占 → 排除该节点重交）→ 就绪即交批次（避开服务节点）
#   → 等批次结束 → 拉日志和结果 → 无论成败都取消服务作业。
# 用法：deploy/tcloud/pipeline.sh <本地输出目录> [初始排除节点,逗号分隔]
# 可调：SUB（submit.sh 路径）TCLOUD_BIN  POLL（轮询秒数，默认 60）FP_LIMIT（默认 5；0=不限）
#       FP_BATCH（默认 tacc-smoke）FP_ABLATIONS（如 B0,B1,B2,B3：一个服务作业里依次跑多档，省掉每档 5–12 分钟开服）
set -uo pipefail
SUB=${SUB:-"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/submit.sh"}
TC=${TCLOUD_BIN:-tcloud}
POLL=${POLL:-60}
OUT=${1:?usage: pipeline.sh OUTPUT_DIR [EXCLUDE_NODES]}
EXCL=${2:-}
mkdir -p "$OUT"

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
clean() { tr -d '\000\r' | grep -a -v -e 'post-quantum' -e 'store now, decrypt later' -e 'openssh.com/pq.html'; }
tc() { "$TC" "$@" 2>&1 | clean; }
jobid() { sed -n 's/^Submitted batch job \([0-9][0-9]*\).*/\1/p' | tail -1; }

SJ=""
cleanup() {
  if [[ -n "$SJ" ]]; then log "cancel serve job $SJ"; tc cancel -j "$SJ"; fi
}
trap cleanup EXIT
trap 'exit 143' TERM INT

# ---------- 1. 服务 ----------
BATCH=${FP_BATCH:-tacc-smoke}
ABL=${FP_ABLATIONS:-}
URL=""; NODE=""; SERVE_T0=0
for attempt in 1 2 3 4 5 6; do
  SERVE_T0=$(date +%s)        # 偏早的估计：服务作业不会早于提交时刻开跑，所以算出的截止时刻只会偏保守
  out=$(FP_EXCLUDE="$EXCL" bash "$SUB" serve 2>&1 | clean)
  SJ=$(printf '%s\n' "$out" | jobid)
  if [[ -z "$SJ" ]]; then log "serve submission failed:"; printf '%s\n' "$out" | tail -5; exit 1; fi
  log "serve job $SJ submitted (attempt $attempt, exclude=${EXCL:-none})"
  deadline=$(( $(date +%s) + 45*60 ))
  while (( $(date +%s) < deadline )); do
    sleep "$POLL"
    L=$(tc cat "slurm_log/qwen-public-serve-$SJ.log")
    printf '%s\n' "$L" > "$OUT/serve-live.log"
    node=$(printf '%s\n' "$L" | sed -n 's#^SERVICE_BASE_URL=http://\([^:]*\):.*#\1#p' | head -1)
    if grep -aq 'PRECHECK_FAIL' <<<"$L"; then
      node=$(grep -a 'PRECHECK_FAIL' <<<"$L" | sed -n 's/.*node=\([^:]*\):.*/\1/p' | head -1)
      log "precheck failed: $(grep -a 'PRECHECK_FAIL' <<<"$L" | head -1 | sed 's/.*PRECHECK_FAIL //')"
      grep -a '^PRECHECK ' <<<"$L" | sed 's/^/    /'
      EXCL="${EXCL:+$EXCL,}$node"; SJ=""; continue 2
    fi
    if grep -aq 'Application startup complete' <<<"$L"; then
      URL=$(sed -n 's/^SERVICE_BASE_URL=//p' <<<"$L" | head -1); NODE=$node
      break 2
    fi
    if grep -aq -e 'Traceback (most recent call last)' -e 'srun: error' -e 'CANCELLED' <<<"$L"; then
      log "serve job $SJ failed:"
      grep -a -e 'Error:' -e 'error:' <<<"$L" | grep -av 'frame #' | tail -4 | sed 's/^/    /'
      if [[ -n "$node" ]] && grep -aq -e 'Free memory on device' -e 'Address already in use' <<<"$L"; then
        EXCL="${EXCL:+$EXCL,}$node"; SJ=""; continue 2
      fi
      SJ=""; exit 1
    fi
    st=$(grep -a -o -e 'torch.compile took [0-9.]* s' -e 'GPU KV cache size: [0-9,]* tokens' -e 'init engine.*took [0-9.]* s' <<<"$L" | tail -1)
    log "waiting for serve $SJ${node:+ @$node}${st:+ ($st)}"
  done
  log "serve $SJ not ready within 45 min"; exit 1
done
[[ -n "$URL" ]] || { log "no server after 6 attempts, giving up (exclude=$EXCL)"; exit 1; }
log "serve ready: $URL (job $SJ, node $NODE)"

# ---------- 2. 批次 ----------
# 服务作业时限 1 小时；客户端在服务停之前 4 分钟收手（见 client/job.sh 的时间账）
out=$(FP_LIMIT="${FP_LIMIT:-5}" FP_EXCLUDE="$NODE" FP_BATCH="$BATCH" FP_ABLATIONS="$ABL" \
      FP_DEADLINE_EPOCH=$(( SERVE_T0 + 3600 - 240 )) bash "$SUB" batch "$URL" 2>&1 | clean)
CJ=$(printf '%s\n' "$out" | jobid)
if [[ -z "$CJ" ]]; then log "batch submission failed:"; printf '%s\n' "$out" | tail -5; exit 1; fi
log "client job $CJ submitted"
ok=""
deadline=$(( $(date +%s) + 55*60 ))
while (( $(date +%s) < deadline )); do
  sleep "$POLL"
  C=$(tc cat "slurm_log/flagpilot-client-$CJ.log")
  if grep -aq '^\[fp\] done:' <<<"$C"; then ok=1; break; fi
  if grep -aq -e 'srun: error' -e 'CANCELLED' <<<"$C"; then ok=0; break; fi
  printf '%s\n' "$C" > "$OUT/client-live.log"
  log "waiting for batch $CJ: $(grep -a -E '^\[[0-9]+/[0-9]+\]|^\[fp\] tier' <<<"$C" | tail -1 | cut -c1-140)"
done
printf '%s\n' "$C" > "$OUT/client-live.log"
log "batch finished (ok=${ok:-timeout}); client log tail:"
printf '%s\n' "$C" | tail -25 | sed 's/^/    /'

# ---------- 3. 取回 ----------
tc cat "slurm_log/flagpilot-client-$CJ.log" > "$OUT/client-$CJ.log"
tc cat "slurm_log/qwen-public-serve-$SJ.log" > "$OUT/serve-$SJ.log"
NAMES="$BATCH"
if [[ -n "$ABL" ]]; then NAMES=""; for ab in ${ABL//,/ }; do NAMES="$NAMES $BATCH-$ab"; done; fi
for n in $NAMES; do
  if grep -aq -e "tier-done $n " -e "^\[fp\] done:" <<<"$C" && ! grep -aq "skip ${n##*-}:" <<<"$C"; then
    tc download "flagpilot-runs/$n.md" "$OUT/" | tail -1
    tc download -r "flagpilot-runs/$n" "$OUT/" | tail -1
  else
    log "no results for $n (skipped or unfinished)"
  fi
done
log "local: $(ls "$OUT" | tr '\n' ' ')"
[[ "$ok" == "1" ]]

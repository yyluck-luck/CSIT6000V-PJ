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
W=${PIPE_WAIT:-5}             # 重试 / 核实之间的等待（秒）；测试里设成 0
OUT=${1:?usage: pipeline.sh OUTPUT_DIR [EXCLUDE_NODES]}
EXCL=${2:-}
mkdir -p "$OUT"

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
# byte-wise on purpose: SDK output may carry non-UTF-8 bytes, and a UTF-8 locale makes tr abort on them
clean() { LC_ALL=C tr -d '\000\r' | LC_ALL=C grep -a -v -e 'post-quantum' -e 'store now, decrypt later' -e 'openssh.com/pq.html'; }
tc() { "$TC" "$@" 2>&1 | clean; }
jobid() { sed -n 's/^Submitted batch job \([0-9][0-9]*\).*/\1/p' | tail -1; }
# Is job $1 in Slurm's list? 0 = yes, 1 = no, 2 = could not tell (TACC's login gateway drops SSH handshakes when busy:
# an SDK error must never read as "the job has gone")
in_ps() {
  local out; out=$(tc ps)
  grep -aq 'JOBID' <<<"$out" || return 2
  grep -aqE "^ *$1 " <<<"$out"
}
# cancel and confirm it is gone (an unconfirmed cancel once left a serve job holding the GPUs and a job slot for an hour)
cancel_job() {
  local j=$1 k r
  for k in 1 2 3; do
    tc cancel -j "$j" >/dev/null
    for r in 1 2 3; do sleep "$W"; in_ps "$j"; [[ $? == 1 ]] && return 0; done
  done
  log "job $j may still be running: tcloud cancel -j $j"
  return 1
}
# a file from TACC, retried until it holds what it should (an SDK error in place of the log is no log)
fetch_log() {   # $1 remote path  $2 local file
  local k
  for k in 1 2 3 4; do
    tc cat "$1" > "$2"
    grep -aq -e 'SDK_BOOTSTRAP=' -e '^\[fp\]' "$2" && return 0
    sleep $(( k * W ))
  done
  return 1
}
fetch_dir() {   # $1 remote path  $2 local dir
  local k
  for k in 1 2 3 4; do
    tc download -r "$1" "$2/" | tail -1
    [[ -e "$2/$(basename "$1")" ]] && return 0
    sleep $(( k * W ))
  done
  log "could not download $1 (later: tcloud download -r $1 $2/)"
  return 1
}

SJ=""
cleanup() {
  if [[ -n "$SJ" ]]; then log "cancel serve job $SJ"; cancel_job "$SJ"; fi
}
trap cleanup EXIT
trap 'exit 143' TERM INT

# ---------- 1. 服务 ----------
BATCH=${FP_BATCH:-tacc-smoke}
ABL=${FP_ABLATIONS:-}
URL=""; NODE=""; SERVE_T0=0
for attempt in 1 2 3 4 5 6; do
  SERVE_T0=$(date +%s)        # 偏早的估计：服务作业不会早于提交时刻开跑，所以算出的截止时刻只会偏保守
  QUIET=$SERVE_T0; RUNNING=""  # 看到 SDK_BOOTSTRAP 后改用"最后一次还没输出的查询 − 60 秒"，排队时间不算进 1 小时
  out=$(FP_EXCLUDE="$EXCL" bash "$SUB" serve 2>&1 | clean)
  SJ=$(printf '%s\n' "$out" | jobid)
  if [[ -z "$SJ" ]]; then log "serve submission failed:"; printf '%s\n' "$out" | tail -5; exit 1; fi
  log "serve job $SJ submitted (attempt $attempt, exclude=${EXCL:-none})"
  deadline=$(( $(date +%s) + 45*60 )); GONE=0
  while (( $(date +%s) < deadline )); do
    sleep "$POLL"
    ASK_T=$(date +%s)
    L=$(tc cat "slurm_log/qwen-public-serve-$SJ.log")
    printf '%s\n' "$L" > "$OUT/serve-live.log"
    if [[ -z "$RUNNING" ]]; then
      if grep -aq '^SDK_BOOTSTRAP=' <<<"$L"; then
        RUNNING=1; (( QUIET - 60 > SERVE_T0 )) && SERVE_T0=$(( QUIET - 60 ))
      else
        QUIET=$ASK_T
      fi
    fi
    node=$(printf '%s\n' "$L" | sed -n 's#^SERVICE_BASE_URL=http://\([^:]*\):.*#\1#p' | head -1)
    if grep -aq 'PRECHECK_FAIL' <<<"$L"; then
      node=$(grep -a 'PRECHECK_FAIL' <<<"$L" | sed -n 's/.*node=\([^:]*\):.*/\1/p' | head -1)
      log "precheck failed: $(grep -a 'PRECHECK_FAIL' <<<"$L" | head -1 | sed 's/.*PRECHECK_FAIL //')"
      grep -a '^PRECHECK ' <<<"$L" | sed 's/^/    /'
      cancel_job "$SJ"                   # retry elsewhere, but stop this one first: a failed serve job kept a slot for 20+ min
      EXCL="${EXCL:+$EXCL,}$node"; SJ=""; continue 2
    fi
    if grep -aq 'Application startup complete' <<<"$L"; then
      URL=$(sed -n 's/^SERVICE_BASE_URL=//p' <<<"$L" | head -1); NODE=$node
      break 2
    fi
    # Ended = Slurm's end markers, or the job gone from the list twice running. Never "Traceback": every vLLM start-up
    # prints a harmless one (a deep_gemm import warning) about 12 s before it is ready; treating it as a crash killed
    # healthy servers (2026-11-21: jobs 30270, 30272, 30281, 30442, 30446).
    in_ps "$SJ"; case $? in 0) GONE=0 ;; 1) GONE=$(( GONE + 1 )) ;; esac
    if grep -aq -e 'srun: error' -e 'CANCELLED AT' -e 'DUE TO TIME LIMIT' -e 'SUPERVISE stopped group' <<<"$L" || (( GONE >= 2 )); then
      log "serve job $SJ ended before it was ready:"
      grep -a -e 'Error: ' -e 'srun: error' -e 'CANCELLED AT' <<<"$L" | grep -av -e 'frame #' -e WARNING | tail -4 | sed 's/^/    /'
      if [[ -n "$node" ]] && grep -aqE 'Address already in use|Free memory on device .{0,80}less than desired|CUDA out of memory' <<<"$L"; then
        cancel_job "$SJ"
        EXCL="${EXCL:+$EXCL,}$node"; SJ=""; continue 2
      fi
      exit 1                              # the EXIT trap cancels $SJ
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
ok=""; C=""; GONE=0
deadline=$(( $(date +%s) + 55*60 ))
while (( $(date +%s) < deadline )); do
  sleep "$POLL"
  C2=$(tc cat "slurm_log/flagpilot-client-$CJ.log")
  grep -aq -e 'SDK_BOOTSTRAP=' -e '^\[fp\]' <<<"$C2" && C=$C2     # an SDK error in place of the log: keep the last good copy
  if grep -aq '^\[fp\] done:' <<<"$C"; then ok=1; break; fi
  if grep -aq -e 'srun: error' -e 'CANCELLED AT' -e 'DUE TO TIME LIMIT' <<<"$C"; then ok=0; break; fi
  in_ps "$CJ"; case $? in 0) GONE=0 ;; 1) GONE=$(( GONE + 1 )) ;; esac
  if (( GONE >= 2 )); then log "client job $CJ is no longer in Slurm's list"; ok=0; break; fi
  printf '%s\n' "$C" > "$OUT/client-live.log"
  log "waiting for batch $CJ: $(grep -a -E '^\[[0-9]+/[0-9]+\]|^\[fp\] tier' <<<"$C" | tail -1 | cut -c1-140)"
done
printf '%s\n' "$C" > "$OUT/client-live.log"
log "batch finished (ok=${ok:-timeout}); client log tail:"
printf '%s\n' "$C" | tail -25 | sed 's/^/    /'

# ---------- 3. 取回 ----------
fetch_log "slurm_log/flagpilot-client-$CJ.log" "$OUT/client-$CJ.log" && C=$(cat "$OUT/client-$CJ.log")
fetch_log "slurm_log/qwen-public-serve-$SJ.log" "$OUT/serve-$SJ.log"
NAMES="$BATCH"
if [[ -n "$ABL" ]]; then NAMES=""; for ab in ${ABL//,/ }; do NAMES="$NAMES $BATCH-$ab"; done; fi
for n in $NAMES; do
  if grep -aq -e "tier-done $n " -e "^\[fp\] done:" <<<"$C" && ! grep -aq "skip ${n##*-}:" <<<"$C"; then
    tc download "flagpilot-runs/$n.md" "$OUT/" | tail -1
    fetch_dir "flagpilot-runs/$n" "$OUT"
  else
    log "no results for $n (skipped or unfinished)"
  fi
done
log "local: $(ls "$OUT" | tr '\n' ' ')"
[[ "$ok" == "1" ]]

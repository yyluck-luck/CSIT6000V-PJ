#!/usr/bin/env bash
#SBATCH -J fp-run
#SBATCH -A REPLACE_ALLOCATION
#SBATCH -p REPLACE_PARTITION
#SBATCH -N 1
#SBATCH -n 1
#SBATCH -c 8
#SBATCH -t 02:00:00
#SBATCH --gres=gpu:2
#SBATCH -o logs/run-%j.out
#SBATCH -e logs/run-%j.err
#
# 一体式：同一个作业里先起 vLLM，再用 localhost 跑评测，跑完自动退出。
#
# 为什么默认推荐这个而不是 serve/client 分开：
#   两个作业要靠队列调度对齐，"服务好了但客户端还在排队"是最常见的浪费。
#   一体式没有跨作业通信问题，也不需要 ssh 隧道。代价是一个作业只能跑一批，
#   中途挂了要重交——所以 runs.jsonl 的续跑必须开（默认就是开的）。
#
# 用法：
#   sbatch --account=<alloc> --partition=<part> deploy/run.slurm
#   sbatch --export=ALL,FP_BATCH=smoke,FP_LIMIT=20 deploy/run.slurm
#   # E1 四档一次跑完（同一作业内复用同一个服务，省三次排队）：
#   sbatch --export=ALL,FP_BATCH=e1,FP_LIMIT=20,FP_WORKERS=1,FP_ABLATIONS="B0 B1 B2 B3" deploy/run.slurm
#
# 注意 FP_WORKERS=1：E1/E3 要读服务端 cache 计数器，并发会摊薄差值。
# 只跑基线时可以开到 4。

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"
source deploy/env.sh
mkdir -p logs runs

export FLAGPILOT_BASE_URL="http://127.0.0.1:${FP_PORT}/v1"
export FP_METRICS_URL="http://127.0.0.1:${FP_PORT}/metrics"

echo "[run] job=${SLURM_JOB_ID} node=$(hostname -f)"
nvidia-smi --query-gpu=index,name,memory.total --format=csv || true

# --- 运行环境：按教程改 ---
# module load cuda/12.x && module load python/3.11
# source "$HOME/miniconda3/etc/profile.d/conda.sh" && conda activate vllm

command -v vllm >/dev/null || { echo "[run] 找不到 vllm" >&2; exit 1; }

# vLLM 默认就开 prefix cache；这里显式写出来，同时保证关闭时不会两个开关一起传。
PC_FLAG="--enable-prefix-caching"
[[ "${FP_PREFIX_CACHE:-1}" == "0" ]] && PC_FLAG="--no-enable-prefix-caching"

echo "[run] 启动服务 model=${FP_MODEL_PATH} tp=${FP_TP} maxlen=${FP_MAX_LEN} seqs=${FP_MAX_SEQS}"
vllm serve "${FP_MODEL_PATH}" \
  --served-model-name "${FP_MODEL_NAME}" \
  --tensor-parallel-size "${FP_TP}" \
  --max-model-len "${FP_MAX_LEN}" \
  --max-num-seqs "${FP_MAX_SEQS}" \
  --gpu-memory-utilization "${FP_GPU_UTIL}" \
  ${PC_FLAG} \
  --host 127.0.0.1 --port "${FP_PORT}" \
  > "logs/vllm-${SLURM_JOB_ID}.log" 2>&1 &
VLLM_PID=$!
trap 'kill $VLLM_PID 2>/dev/null || true' EXIT

echo "[run] 等待模型加载（pid=${VLLM_PID}）…"
for i in $(seq 1 150); do
  curl -sf "${FLAGPILOT_BASE_URL}/models" >/dev/null 2>&1 && { echo "[run] 就绪"; break; }
  kill -0 "$VLLM_PID" 2>/dev/null || { echo "[run] 服务挂了看日志" >&2; tail -50 "logs/vllm-${SLURM_JOB_ID}.log" >&2; exit 1; }
  sleep 5
done
curl -sf "${FLAGPILOT_BASE_URL}/models" >/dev/null || { echo "[run] 超时仍未就绪" >&2; exit 1; }

echo
echo "[run] 环境自检"
python3 -m flagpilot doctor || true

BATCH="${FP_BATCH:-dev}"
EXTRA=""
[[ -n "${FP_LIMIT:-}" ]] && EXTRA="$EXTRA --limit ${FP_LIMIT}"
[[ -n "${FP_SHARD:-}" ]] && EXTRA="$EXTRA --shard ${FP_SHARD}"

# E2：记忆探针必须在同一个作业里跑——计算节点没有外网，外部连不进来。
if [[ "${FP_PROBE:-0}" == "1" ]]; then
  echo
  echo "[run] 记忆探针 mode=${FP_PROBE_MODE:-brief}"
  PROBE_EXTRA=""
  [[ -n "${FP_LIMIT:-}" ]] && PROBE_EXTRA="--limit ${FP_LIMIT}"
  python3 -m flagpilot probe \
    --batch "${BATCH}-probe" \
    --mode "${FP_PROBE_MODE:-brief}" \
    --model "${FP_MODEL_NAME}" \
    --base-url "${FLAGPILOT_BASE_URL}" \
    --scope "${FP_SCOPE:-core}" \
    --workers 4 ${PROBE_EXTRA} || true
fi

run_one() {  # $1=batch 名  $2=可选消融档位
  local batch="$1" ablate="${2:-}"
  local abl_flag=""
  [[ -n "$ablate" ]] && abl_flag="--ablate ${ablate}"
  echo
  echo "[run] 开跑 batch=${batch} ablate=${ablate:-default} limit=${FP_LIMIT:-all} " \
       "shard=${FP_SHARD:-1/1} workers=${FP_WORKERS}"
  python3 -m flagpilot batch \
    --batch "${batch}" \
    --track L \
    --model "${FP_MODEL_NAME}" \
    --base-url "${FLAGPILOT_BASE_URL}" \
    --metrics-url "${FP_METRICS_URL}" \
    --scope "${FP_SCOPE:-core}" \
    --workers "${FP_WORKERS}" \
    --turns "${FP_TURNS}" \
    --seeds "${FP_SEEDS}" \
    --action-timeout "${FP_ACTION_TIMEOUT}" \
    --replay-timeout "${FP_REPLAY_TIMEOUT}" \
    ${abl_flag} ${EXTRA}
}

if [[ -n "${FP_ABLATIONS:-}" ]]; then
  # E1：一组作业跑完四档，省掉四次排队。每档独立 batch 目录，互不覆盖。
  LIST=""
  for b in ${FP_ABLATIONS}; do
    run_one "${BATCH}-${b}" "$b"
    LIST="${LIST:+$LIST,}${BATCH}-${b}"
  done
  echo
  echo "[run] 四档对比（直接把这段贴进报告）"
  python3 -m flagpilot compare --batches "${LIST}" --md "runs/e1-${SLURM_JOB_ID}.md" || true
else
  run_one "${BATCH}" "${FP_ABLATE:-}"
  echo
  echo "[run] 汇总"
  python3 -m flagpilot report --batch "${BATCH}" --md "runs/report-${SLURM_JOB_ID}.md" || true
fi

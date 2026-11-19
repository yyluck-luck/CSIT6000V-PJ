#!/usr/bin/env bash
#SBATCH -J fp-run
#SBATCH -A REPLACE_ALLOCATION
#SBATCH -p REPLACE_PARTITION
#SBATCH -N 1
#SBATCH -n 1
#SBATCH -c 8
# 课程 TACC 的 tacc 分区上限是 1 小时（课程材料写的 2 小时是错的，实测提交 2h 会被
# PartitionTimeLimit 拒掉）。这里按 1 小时写；换成别的集群请按实际 TIMELIMIT 改。
#SBATCH -t 01:00:00
#SBATCH --gres=gpu:2
#SBATCH -o logs/run-%j.out
#SBATCH -e logs/run-%j.err
#
# 一体式：同一个作业里先起 vLLM，再用 localhost 跑评测，跑完自动退出。
#
# 适用范围：**通用 Slurm 集群**——即你有权 `module load vllm` 或自带 conda 环境、
# 用 `sbatch` 直接提交。课程 TACC 平台不适用：那里没有 vllm 模块，提交要走
# `tcloud submit` + `tuxiv.conf`（见 docs/TACC.md）。本文件保留为可移植模板。
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
#   # E3 网格（prefix cache 开/关各交一个作业，同一作业内跑策略 × 轮次 × 并发）：
#   sbatch --export=ALL,FP_E3=1,FP_PREFIX_CACHE=1,FP_MAX_SEQS=8,FP_LIMIT=20,FP_SPLIT=dev deploy/run.slurm
#   sbatch --export=ALL,FP_E3=1,FP_PREFIX_CACHE=0,FP_MAX_SEQS=8,FP_LIMIT=20,FP_SPLIT=dev deploy/run.slurm
#   # 定稿后唯一一次 test（守卫记账：同名批次重交 = 续跑；同一配置换名重跑会被拒）：
#   sbatch --export=ALL,FP_BATCH=final,FP_SPLIT=test,FP_CONFIRM_TEST=1 deploy/run.slurm
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

# --- GPU 利用率采样（Track L 成本：busy GPU 秒）---
# 每行 epoch,卡号,利用率%,显存MiB；runner 按每个批次的时间窗切片记进 runs/<batch>/gpu.jsonl。
# 申请口径的 GPU 秒（卡数 × 墙钟）由 runner 从 SLURM_GPUS_ON_NODE / CUDA_VISIBLE_DEVICES 自己算。
export FP_GPU_UTIL_CSV="$PWD/runs/gpu_util-${SLURM_JOB_ID:-local}.csv"
(
  while true; do
    ts=$(date +%s)
    nvidia-smi --query-gpu=index,utilization.gpu,memory.used --format=csv,noheader,nounits 2>/dev/null \
      | awk -v ts="$ts" -F', *' 'NF>=2 {print ts "," $1 "," $2 "," $3}' >> "${FP_GPU_UTIL_CSV}"
    sleep "${FP_GPU_SAMPLE_S:-2}"
  done
) &
SAMPLER_PID=$!
trap 'kill $SAMPLER_PID 2>/dev/null || true' EXIT

# --- 运行环境：按教程改 ---
# module load cuda/12.x && module load python/3.11
# source "$HOME/miniconda3/etc/profile.d/conda.sh" && conda activate vllm

command -v vllm >/dev/null || { echo "[run] 找不到 vllm" >&2; exit 1; }

# dev / test 划分（docs/TACC.md 第 3 节）：冒烟、调参、选策略一律 FP_SPLIT=dev；
# 定稿后唯一一次 test 要同时给 FP_SPLIT=test FP_CONFIRM_TEST=1。不设 = 全部题（旧行为）。
SPLIT_FLAGS=""
if [[ -n "${FP_SPLIT:-}" && "${FP_SPLIT}" != "all" ]]; then
  SPLIT_FLAGS="--split ${FP_SPLIT}"
  [[ "${FP_CONFIRM_TEST:-0}" == "1" ]] && SPLIT_FLAGS="${SPLIT_FLAGS} --confirm-test"
  # 划分文件缺失 / dev 与 test 重叠时，在起 vLLM 之前就失败，别白占 GPU。
  python3 -c 'from flagpilot import splits; splits.load_split(splits.default_split_path())' \
    || { echo "[run] 划分文件不可用：先跑 scripts/make_split.py，或用 FLAGPILOT_SPLITS 指定" >&2; exit 1; }
fi

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
trap 'kill $VLLM_PID $SAMPLER_PID 2>/dev/null || true' EXIT

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
       "shard=${FP_SHARD:-1/1} workers=${FP_WORKERS} split=${FP_SPLIT:-all}"
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
    ${abl_flag} ${EXTRA} ${SPLIT_FLAGS}
}

run_e3() {  # $1=batch $2=观察策略 $3=轮次 $4=并发
  local batch="$1" pol="$2" turns="$3" workers="$4" pol_flags=""
  case "$pol" in
    trunc) pol_flags="--no-compact" ;;   # 只做追加时截断（append-time cut）
    summ)  pol_flags="--no-truncate" ;;  # 只做回溯式压缩（retroactive summarisation）
    both)  pol_flags="" ;;               # 默认系统：两者都开
    *) echo "[e3] 未知策略 $pol" >&2; return 1 ;;
  esac
  if (( workers > FP_MAX_SEQS )); then
    # 超过服务端并发闸门的格子会在 vLLM 队列里排队，测到的是排队不是并发——跳过并说明。
    echo "[e3] 跳过 ${batch}：workers=${workers} > max-num-seqs=${FP_MAX_SEQS}（提交时设 FP_MAX_SEQS）"
    return 0
  fi
  echo
  echo "[e3] 开跑 ${batch}  policy=${pol} turns=${turns} workers=${workers} cache=${FP_PREFIX_CACHE:-1} split=${FP_SPLIT:-all}"
  python3 -m flagpilot batch \
    --batch "${batch}" --track L \
    --model "${FP_MODEL_NAME}" --base-url "${FLAGPILOT_BASE_URL}" --metrics-url "${FP_METRICS_URL}" \
    --scope "${FP_SCOPE:-core}" --workers "${workers}" --turns "${turns}" --seeds "${FP_SEEDS}" \
    --limit "${FP_LIMIT:-20}" --action-timeout "${FP_ACTION_TIMEOUT}" \
    --replay-timeout "${FP_REPLAY_TIMEOUT}" ${pol_flags} ${SPLIT_FLAGS}
  E3_LIST="${E3_LIST:+$E3_LIST,}${batch}"
}

if [[ "${FP_E3:-0}" == "1" ]]; then
  # E3：prefix cache 开/关要重启 vLLM，所以一个作业只测一种 cache 设置（交两个作业）。
  # 同一作业内：(a) 观察策略单并发对照 → (b) 最佳策略上的轮次预算 → (c) 最佳策略上的并发。
  # 缓存命中率只在单并发格子里报（并发时服务端计数器会被摊薄，metrics 会自动置空）。
  PC="pc${FP_PREFIX_CACHE:-1}"
  BEST="${FP_E3_BEST:-trunc}"
  E3_LIST=""
  for pol in ${FP_E3_POLICIES:-trunc summ}; do run_e3 "e3-${PC}-${pol}-t20-w1" "$pol" 20 1; done
  for t in ${FP_E3_TURNS:-40 60}; do run_e3 "e3-${PC}-${BEST}-t${t}-w1" "$BEST" "$t" 1; done
  for w in ${FP_E3_WORKERS:-4 8}; do run_e3 "e3-${PC}-${BEST}-t20-w${w}" "$BEST" 20 "$w"; done
  echo
  echo "[e3] 汇总（参照格 = e3-${PC}-${BEST}-t20-w1，护栏 5 个点）"
  python3 -m flagpilot compare --batches "${E3_LIST}" --e3 --ref "e3-${PC}-${BEST}-t20-w1" \
    --md "runs/e3-${PC}-${SLURM_JOB_ID}.md" || true
elif [[ -n "${FP_ABLATIONS:-}" ]]; then
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

# --- 结果归档（可选）---
# 有些集群的提交目录在 scratch 上、会被定期清理，作业结束几天后 runs/ 就没了。
# 设 FP_ARCHIVE_DIR 指向持久盘，把整批产物拷一份出来（含 trace、replay、writeup）。
if [[ -n "${FP_ARCHIVE_DIR:-}" && -d runs ]]; then
  ARCH="${FP_ARCHIVE_DIR}/${SLURM_JOB_ID:-local}"
  mkdir -p "$ARCH" && cp -a runs/. "$ARCH/" \
    && echo "[run] 产物已归档到 ${ARCH}（$(find "$ARCH" -type f | wc -l | tr -d ' ') 个文件）" \
    || echo "[run] 归档失败，检查 FP_ARCHIVE_DIR=${FP_ARCHIVE_DIR}" >&2
fi

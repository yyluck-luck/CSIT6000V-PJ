#!/usr/bin/env bash
#SBATCH -J fp-client
#SBATCH -A REPLACE_ALLOCATION
#SBATCH -p REPLACE_PARTITION
#SBATCH -N 1
#SBATCH -n 1
#SBATCH -c 4
#SBATCH -t 01:00:00
#SBATCH -o logs/client-%j.out
#SBATCH -e logs/client-%j.err
#
# 批量评测客户端。提交示例：
#   sbatch --account=<alloc> --partition=<part> \
#          --export=ALL,FP_BATCH=dev,FP_SHARD=0/3,FP_LIMIT=20 \
#          deploy/client.slurm
#
# 关键点：
#   * 单作业 1h（tacc 分区上限），跑不完靠 runs.jsonl 续跑：同一个 FP_BATCH 再交一次会自动跳过已完成的
#   * 分片 FP_SHARD=i/n，多份作业并行不重叠
#   * 先 ping 服务，连不上立刻失败，别白占 GPU 队列

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"
source deploy/env.sh
mkdir -p logs runs

BASELINE="${FLAGPILOT_BASE_URL}"
echo "[client] job=${SLURM_JOB_ID} node=$(hostname -f)"
echo "[client] base_url=${BASELINE}"

if ! curl -sf "${BASELINE}/models" >/dev/null 2>&1; then
  echo "[client] 连不上模型服务：${BASELINE}/models" >&2
  echo "[client] 排查：服务作业是否还在跑；端口是否 ${FP_PORT}；是否需要隧道" >&2
  exit 1
fi
echo "[client] 服务在"

BATCH="${FP_BATCH:-dev}"
SCOPE="${FP_SCOPE:-core}"
LIMIT="${FP_LIMIT:-}"
SHARD="${FP_SHARD:-}"
EXTRA=""
if [[ -n "$LIMIT" ]]; then EXTRA="$EXTRA --limit $LIMIT"; fi
if [[ -n "$SHARD" ]]; then EXTRA="$EXTRA --shard $SHARD"; fi
# dev / test 划分（docs/TACC.md 第 3 节）。test 的配置指纹含服务地址，分离式每次地址不同，
# 续跑会被守卫当成换了配置——定稿 test 请用一体式 run.slurm。
if [[ -n "${FP_SPLIT:-}" ]]; then EXTRA="$EXTRA --split ${FP_SPLIT}"; fi
if [[ "${FP_CONFIRM_TEST:-0}" == "1" ]]; then EXTRA="$EXTRA --confirm-test"; fi

echo "[client] batch=${BATCH} scope=${SCOPE} limit=${LIMIT:-all} shard=${SHARD:-1/1} split=${FP_SPLIT:-all}"

python3 -m flagpilot batch \
  --batch "${BATCH}" \
  --track "${FP_TRACK:-L}" \
  --model "${FP_MODEL_NAME}" \
  --base-url "${BASELINE}" \
  --metrics-url "${FP_METRICS_URL}" \
  --scope "${SCOPE}" \
  --workers "${FP_WORKERS}" \
  --turns "${FP_TURNS}" \
  --seeds "${FP_SEEDS}" \
  --action-timeout "${FP_ACTION_TIMEOUT}" \
  --replay-timeout "${FP_REPLAY_TIMEOUT}" \
  ${EXTRA}

echo
echo "[client] 汇总："
python3 -m flagpilot report --batch "${BATCH}"

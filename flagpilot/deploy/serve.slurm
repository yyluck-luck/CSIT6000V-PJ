#!/usr/bin/env bash
#SBATCH -J fp-serve
#SBATCH -A REPLACE_ALLOCATION
#SBATCH -p REPLACE_PARTITION
#SBATCH -N 1
#SBATCH -n 1
#SBATCH -c 8
#SBATCH -t 02:00:00
#SBATCH --gres=gpu:2
#SBATCH -o logs/serve-%j.out
#SBATCH -e logs/serve-%j.err
#
# 起 vLLM 服务。命令行参数会覆盖上面的 #SBATCH 占位符，推荐这样提交：
#   sbatch --account=<alloc> --partition=<part> deploy/serve.slurm
#
# 环境变量（在提交前 export，或用 --export=ALL,FP_XXX=...）：
#   FP_MODEL_PATH   模型权重路径 / HF id
#   FP_MODEL_NAME   对外暴露的名字（客户端用它）
#   FP_TP           tensor parallel，默认 2
#   FP_MAX_LEN      上下文上限，默认 32768
#   FP_MAX_SEQS     并发闸门，默认 4
#   FP_PREFIX_CACHE 1/0 —— E3 的消融开关

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"
source deploy/env.sh
mkdir -p logs runs

echo "[serve] node=$(hostname -f) job=${SLURM_JOB_ID} gpus=${SLURM_GPUS_ON_NODE:-?}"
nvidia-smi --query-gpu=index,name,memory.total --format=csv || true

# --- 运行环境：按课程教程修改，二选一 ---
# module load cuda/12.x  &&  module load python/3.11
# source "$HOME/miniconda3/etc/profile.d/conda.sh" && conda activate vllm

if ! command -v vllm >/dev/null 2>&1; then
  echo "[serve] 找不到 vllm。先确认环境：which vllm / pip show vllm" >&2
  exit 1
fi

PC_FLAG=""
if [[ "${FP_PREFIX_CACHE:-1}" == "0" ]]; then
  # 不同 vLLM 版本这个开关名字不一样，先确认：
  #   vllm serve --help | grep -i prefix
  PC_FLAG="--no-enable-prefix-caching"
fi

echo "[serve] model=${FP_MODEL_PATH} tp=${FP_TP} maxlen=${FP_MAX_LEN} seqs=${FP_MAX_SEQS} prefix_cache=${FP_PREFIX_CACHE:-1}"

# --enable-prefix-caching 在较新版本已默认开启；显式写上便于对齐实验口径。
vllm serve "${FP_MODEL_PATH}" \
  --served-model-name "${FP_MODEL_NAME}" \
  --tensor-parallel-size "${FP_TP}" \
  --max-model-len "${FP_MAX_LEN}" \
  --max-num-seqs "${FP_MAX_SEQS}" \
  --gpu-memory-utilization "${FP_GPU_UTIL}" \
  --enable-prefix-caching ${PC_FLAG} \
  --host 0.0.0.0 \
  --port "${FP_PORT}" \
  > logs/vllm-${SLURM_JOB_ID}.log 2>&1 &

VLLM_PID=$!
echo "[serve] vllm pid=${VLLM_PID}，日志 logs/vllm-${SLURM_JOB_ID}.log"

# 等模型加载完（大模型冷启动可能要几分钟）
for i in $(seq 1 120); do
  if curl -sf "http://127.0.0.1:${FP_PORT}/v1/models" >/dev/null 2>&1; then
    echo "[serve] 就绪：http://$(hostname -f):${FP_PORT}/v1"
    curl -s "http://127.0.0.1:${FP_PORT}/v1/models" | head -c 400; echo
    break
  fi
  if ! kill -0 "$VLLM_PID" 2>/dev/null; then
    echo "[serve] vllm 进程已退出，看日志：" >&2
    tail -40 "logs/vllm-${SLURM_JOB_ID}.log" >&2
    exit 1
  fi
  sleep 5
done

# 把地址写下来，方便客户端作业和人工排查
echo "http://$(hostname -f):${FP_PORT}/v1" > runs/service_url.txt
echo "[serve] SERVICE_URL 已写入 runs/service_url.txt"

# 保持作业存活到时限；kill 时清理子进程
trap 'echo "[serve] 收到终止信号，关闭 vllm"; kill $VLLM_PID 2>/dev/null || true' TERM INT
wait $VLLM_PID

#!/usr/bin/env bash
# 课程 qwen-public-serve/job.sh 的副本：激活公共环境，启动课程固定的 vLLM 服务脚本。
# 改动只有两处：下面的“开服前体检”，和经 supervise.sh 启动（作业结束时停掉整个 vLLM 进程组）。服务参数一个没动。
set -euo pipefail
umask 077
: "${TACC_WORKDIR:?Submit with tcloud submit}"
: "${TACC_USERDIR:?Submit with tcloud submit}"
: "${SLURM_JOB_ID:?Submit with tcloud submit}"
: "${CONDA_PREFIX:?SDK must activate the course management environment}"
test "$CONDA_PREFIX" = /mnt/home/public/course-envs/CSIT6000V/assignment1/mpi-gemm-v1
printf 'SDK_BOOTSTRAP=%s\n' "$CONDA_PREFIX"
# 开服前体检：分到的节点上有 Slurm 管不到的进程占着显存或 8000 端口（作业 29239 / 29243 / 29244），
# vLLM 要等约 5 分钟才报错。先看一眼，被占用就立刻退出，证据留在日志里（可直接转给 TA）。
if command -v nvidia-smi >/dev/null; then
  nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader | sed "s/^/PRECHECK gpu $(hostname) /"
  nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader | sed 's/^/PRECHECK app /' || true
  busy=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | awk '$1 > 1024' | wc -l)
  if (( busy > 0 )); then
    echo "PRECHECK_FAIL node=$(hostname): ${busy} GPU(s) already hold memory before start; exclude this node (FP_EXCLUDE=$(hostname))" >&2
    exit 1
  fi
fi
if (exec 3<>/dev/tcp/127.0.0.1/8000) 2>/dev/null; then
  echo "PRECHECK_FAIL node=$(hostname): port 8000 is already in use; exclude this node (FP_EXCLUDE=$(hostname))" >&2
  exit 1
fi
source /mnt/home/public/course-envs/CSIT6000V/project/activate.sh
trap 'rm -rf -- "$QWEN_RUN_DIR"' EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
export QWEN_CACHE_HOME="$TACC_USERDIR/CSIT6000V-project-cache"
mkdir -p -m 700 "$QWEN_CACHE_HOME"
hostname
printf 'SERVICE_BASE_URL=http://%s:8000/v1\n' "$(hostname)"
# 第二处改动（2026-11-21）：经 supervise.sh 启动。作业不管怎么结束（vLLM 自己退出、被取消、到时限），
# 都把 vLLM 整个进程组（API 服务 + GPU worker）停掉、等显存放出来再退出，并把停后的显存打进日志。
# 课程同期给 cinecontext-qwen-e2e 发的清理更新修的就是 worker 在作业结束后还占着显存。服务参数不变。
bash "$TACC_WORKDIR/supervise.sh" bash "$QWEN_PROJECT_ROOT/serve.sh"

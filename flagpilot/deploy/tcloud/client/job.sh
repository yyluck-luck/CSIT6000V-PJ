#!/usr/bin/env bash
# FlagPilot 的 tcloud 客户端作业（CPU）。由 deploy/tcloud/submit.sh 暂存后 `tcloud submit`。
# 前半段与课程 qwen-public-client/job.sh 相同（SDK 引导 → 课程项目环境）；
# 后半段按 run.env 的 FP_MODE 跑 doctor 或一个批次。
set -euo pipefail
: "${TACC_WORKDIR:?Submit with tcloud submit}"
: "${TACC_USERDIR:?Submit with tcloud submit}"
: "${SLURM_JOB_ID:?Submit with tcloud submit}"
: "${CONDA_PREFIX:?SDK must activate the course management environment}"
test "$CONDA_PREFIX" = /mnt/home/public/course-envs/CSIT6000V/assignment1/mpi-gemm-v1
printf 'SDK_BOOTSTRAP=%s\n' "$CONDA_PREFIX"
source /mnt/home/public/course-envs/CSIT6000V/project/activate.sh
trap 'rm -rf -- "$QWEN_RUN_DIR"' EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
cd "$TACC_WORKDIR"
# shellcheck disable=SC1091
source ./run.env

# WORKDIR 是 SDK 的临时目录；结果写 USERDIR 才能留下来（tcloud download -r flagpilot-runs/<batch>）。
export FLAGPILOT_TASKS="$TACC_WORKDIR/tasks"
export FLAGPILOT_RUNS="$TACC_USERDIR/flagpilot-runs"
mkdir -p -m 700 "$FLAGPILOT_RUNS"
# 服务在集群内网；别让环境里的代理变量把请求带走（沙箱子进程另有自己的死端口代理）。
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
# 课程运行时是 PRoot 里的 Ubuntu 22 rootfs，PATH 上没有 python3/python（作业 29238 的 doctor 实测）。
# 沙箱的 python 动作和 agent 在 shell 里敲的 python3 都按 PATH 找，这里垫一层，指向 qwen-python
# 同一个解释器。放在 QWEN_RUN_DIR（/tmp，已绑进 PRoot），作业结束随上面的 trap 一起删掉。
# sandbox.py 自 79c1b49 起也会把解释器目录放进沙箱 PATH；这层垫片让 doctor 的 PATH 检查也如实。
FP_BIN="$QWEN_RUN_DIR/fp-bin"
mkdir -p "$FP_BIN"
for n in python3 python; do
  printf '#!/bin/sh\nexec %s "$@"\n' "$QWEN_PROJECT_ROOT/vllm-env/bin/python" > "$FP_BIN/$n"
  chmod 755 "$FP_BIN/$n"
done
export PATH="$FP_BIN:$TACC_WORKDIR/bin:$PATH"
# submit.sh 随包带来的：bin/ 里的 xxd / exiftool 替身（纯 Python），vendor/py 里的 pycryptodome / gmpy2。
# 沙箱的环境是白名单，库路径只经 FLAGPILOT_SANDBOX_PYTHONPATH 进去（动作与重放同一条路）；
# PYTHONPATH 给本进程，让 doctor 的"缺工具"检查如实反映沙箱能用到什么。
if [[ -d "$TACC_WORKDIR/vendor/py" ]]; then
  export FLAGPILOT_SANDBOX_PYTHONPATH="$TACC_WORKDIR/vendor/py"
  export PYTHONPATH="$TACC_WORKDIR/vendor/py${PYTHONPATH:+:$PYTHONPATH}"
fi

fp() { qwen-python -u run_flagpilot.py "$@"; }

echo "[fp] job=${SLURM_JOB_ID} node=$(hostname) mode=${FP_MODE} batch=${FP_BATCH:-}"
qwen-python -c 'import sys; print("[fp] python", sys.version.split()[0], sys.executable)'
# 按沙箱的方式（PATH 查找 + 子进程）真起一次 python3，确认垫片在 PRoot 里可用。
qwen-python -c '
import shutil, subprocess
p = shutil.which("python3")
try:
    r = subprocess.run([p or "python3", "-c", "import sys; print(sys.version.split()[0])"],
                       capture_output=True, text=True, timeout=60)
    out = r.stdout.strip() or r.stderr.strip()
except Exception as e:
    out = "失败: %s" % e
print("[fp] sandbox python3:", p, "->", out)
'
echo "[fp] shims: xxd=$(command -v xxd || echo missing) exiftool=$(command -v exiftool || echo missing)"
# 按沙箱的方式（干净环境 + 只给 FLAGPILOT_SANDBOX_PYTHONPATH）确认随包的库真能 import
env -i PATH="$PATH" PYTHONPATH="${FLAGPILOT_SANDBOX_PYTHONPATH:-}" python3 -c \
  'import Crypto, gmpy2; print("[fp] vendored: pycryptodome", Crypto.__version__, "gmpy2", gmpy2.version())' \
  || echo "[fp] vendored: unavailable (tasks needing pycryptodome/gmpy2 will report the missing library)"
echo "[fp] doctor"
fp doctor || true          # 自检结果进日志；不在这里拦作业，判定看下面的批次

[[ "${FP_MODE}" == "doctor" ]] && exit 0

BASE="$(cat server-url.txt)"
case "$BASE" in *NODE*|'') echo "[fp] server-url.txt does not hold this service address" >&2; exit 1;; esac
# PRoot 只绑了宿主机的 /etc/resolv.conf，没绑 /etc/hosts：节点名（10-0-3-11）进了 PRoot 就解析不了
# （作业 29314：Temporary failure in name resolution）。在宿主机上先换成 IP。
# 宿主机 hosts 也不全（作业 29319：10-0-1-10 上 getent 解析不了 10-0-4-11），再按本集群的命名规则
# 换：节点名就是 IP 把点换成横线（作业 29316：getent 把 10-0-1-11 解析为 10.0.1.11）。
SVC_HOST=${BASE#*://}; SVC_HOST=${SVC_HOST%%[:/]*}
SVC_IP=$(getent ahostsv4 "$SVC_HOST" | awk 'NR==1{print $1}') || SVC_IP=""
if [[ -z "$SVC_IP" && "$SVC_HOST" =~ ^[0-9]{1,3}(-[0-9]{1,3}){3}$ ]]; then
  SVC_IP=${SVC_HOST//-/.}
fi
if [[ -n "$SVC_IP" ]]; then
  BASE=${BASE/"$SVC_HOST"/"$SVC_IP"}
fi
echo "[fp] service=${BASE} (${SVC_HOST})"
# 先确认服务就绪，连不上立刻失败，别空跑一批 error。
qwen-python -c '
import json, sys, urllib.request
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
ids = [m["id"] for m in json.load(op.open(sys.argv[1].rstrip("/") + "/models", timeout=15))["data"]]
print("[fp] models:", ids)
' "$BASE"

# 时间账：客户端自己的时限（FP_CLIENT_SECONDS，留 2 分钟收尾）与服务作业的结束时刻取较早者。
# 服务一停，后面的 attempt 全会变成连接错误、污染结果——所以宁可整档跳过，留给下次续跑。
T0=$(date +%s)
END=$(( T0 + ${FP_CLIENT_SECONDS:-2700} - 120 ))
if [[ "${FP_DEADLINE_EPOCH:-0}" =~ ^[0-9]+$ ]] && (( FP_DEADLINE_EPOCH > 0 && FP_DEADLINE_EPOCH < END )); then
  END=$FP_DEADLINE_EPOCH
fi
LAST=0
run_batch() {   # $1 = 批次名  $2 = 额外参数（--ablate Bx）
  local s
  s=$(date +%s)
  # shellcheck disable=SC2086
  fp batch --batch "$1" --track L --model qwen3.8-27b \
    --base-url "${BASE}" --metrics-url "${BASE%/v1}/metrics" --scope core ${FP_ARGS} $2 \
    || echo "[fp] batch $1 exited non-zero"
  fp report --batch "$1" --md "${FLAGPILOT_RUNS}/$1.md" || true
  LAST=$(( $(date +%s) - s ))
  echo "[fp] tier-done $1 ${LAST}s"
}
if [[ -z "${FP_ABLATIONS:-}" ]]; then
  run_batch "${FP_BATCH}" ""
else
  for ab in ${FP_ABLATIONS//,/ }; do
    left=$(( END - $(date +%s) ))
    need=240
    if (( LAST > 0 )); then need=$(( LAST * 13 / 10 + 60 )); fi
    if (( left < need )); then
      echo "[fp] skip ${ab}: ${left}s left, needs ~${need}s (resume with FP_ABLATIONS=${ab}; finished attempts are skipped)"
      continue
    fi
    echo "[fp] tier ${ab} -> ${FP_BATCH}-${ab} (${left}s left)"
    run_batch "${FP_BATCH}-${ab}" "--ablate ${ab}"
  done
fi
echo "[fp] done: ${FLAGPILOT_RUNS}/${FP_BATCH}"

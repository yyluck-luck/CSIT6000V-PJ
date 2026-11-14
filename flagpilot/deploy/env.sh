# 所有作业共享的环境变量。用 `source deploy/env.sh` 载入，或在 .slurm 里直接 source。
# 尖括号 <> 里的值必须按课程 TACC 教程填写，不要照抄。

# ---- Slurm ----
export FP_ALLOCATION="${FP_ALLOCATION:-<你的 allocation>}"
export FP_PARTITION="${FP_PARTITION:-<GPU 分区名>}"
export FP_TIME="${FP_TIME:-02:00:00}"

# ---- 路径 ----
export FP_ROOT="${FP_ROOT:-$HOME/flagpilot}"
export FP_TASKS="${FLAGPILOT_TASKS:-$FP_ROOT/tasks}"
export FLAGPILOT_TASKS="$FP_TASKS"
export FLAGPILOT_RUNS="${FLAGPILOT_RUNS:-$FP_ROOT/runs}"

# ---- 模型服务 ----
# 课程发放的模型路径 / 名称，按 TA 回复填。≤14B 的理由要能在答辩时讲出来（见 docs/DESIGN.md）。
export FP_MODEL_PATH="${FP_MODEL_PATH:-<模型权重路径或 HF id>}"
export FP_MODEL_NAME="${FP_MODEL_NAME:-local-model}"
export FP_TP="${FP_TP:-2}"                 # tensor parallel，通常 = 申请到的 GPU 数
export FP_MAX_LEN="${FP_MAX_LEN:-32768}"   # 与课程给的上下文一致
export FP_PORT="${FP_PORT:-8000}"
export FP_MAX_SEQS="${FP_MAX_SEQS:-4}"     # 并发闸门，别超过集群允许值
export FP_GPU_UTIL="${FP_GPU_UTIL:-0.90}"

# 服务起来后，客户端用这个地址连。作业内可以用节点名直接访问；
# 本地调试需要 ssh 隧道（见 docs/TACC部署.md 第 5 节）。
export FP_HOST="$(hostname -f 2>/dev/null || hostname)"
export FLAGPILOT_BASE_URL="${FLAGPILOT_BASE_URL:-http://${FP_HOST}:${FP_PORT}/v1}"
export FP_METRICS_URL="${FP_METRICS_URL:-http://${FP_HOST}:${FP_PORT}/metrics}"
export FLAGPILOT_API_KEY="${FLAGPILOT_API_KEY:-EMPTY}"

# ---- 运行参数 ----
export FP_WORKERS="${FP_WORKERS:-4}"
export FP_TURNS="${FP_TURNS:-20}"
export FP_SEEDS="${FP_SEEDS:-1}"
export FP_ACTION_TIMEOUT="${FP_ACTION_TIMEOUT:-60}"
export FP_REPLAY_TIMEOUT="${FP_REPLAY_TIMEOUT:-300}"

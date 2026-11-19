#!/usr/bin/env bash
# 用课程官方 tcloud SDK 在 TACC 上跑 FlagPilot：不登录服务器、不建 SSH 转发（课程 STUDENT-COMMANDS.md）。
#
#   deploy/tcloud/submit.sh serve                        # 起课程模型服务 qwen3.8-27b（2×3090，≤1h）
#   deploy/tcloud/submit.sh doctor                       # CPU 自检：SDK 引导、课程 Python、题库泄漏检查
#   deploy/tcloud/submit.sh batch http://NODE:8000/v1    # 对正在运行的服务跑一个批次
#
# batch 的参数用环境变量给：
#   FP_BATCH（默认 tacc-smoke）  FP_SPLIT（默认 dev）  FP_LIMIT  FP_TASKS（逗号分隔题名）
#   FP_WORKERS（默认 4，不能超过服务端 --max-num-seqs 4）  FP_TURNS（20）  FP_SEEDS（1）
#   FP_TIME（客户端时长，默认 00:45:00，不能超过分区上限 1h）  FP_DRY_RUN=1（只暂存不提交）
# 任一模式都可加 FP_EXCLUDE=节点[,节点]：课程 serve.sh 写死 8000 端口，某节点的 8000 被占时
# 服务会 "Address already in use" 秒退（作业 29239），用它换节点。
# 结果留在 TACC 的 USERDIR/flagpilot-runs/<batch>/，取回：
#   tcloud download flagpilot-runs/<batch>.md ./     和     tcloud download -r flagpilot-runs/<batch> ./
set -euo pipefail

MODE="${1:?用法: deploy/tcloud/submit.sh serve|doctor|batch [SERVICE_BASE_URL]}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
TASKS="${FLAGPILOT_TASKS:-$ROOT/tasks}"
command -v tcloud >/dev/null || { echo "找不到 tcloud（课程 SDK v0.4.1）" >&2; exit 1; }

# SDK 拿本地目录名当集群上的 WORKDIR 名：名字固定，重复提交覆盖同一份，
# 不会每交一次就在 /mnt/home（已满）多留 73M。代价是同一时间只能跑一个客户端作业。
case "$MODE" in serve) NAME=qwen-public-serve ;; *) NAME=flagpilot-client ;; esac
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/fp-tcloud-XXXXXX")/${NAME}"
mkdir -p "$STAGE"
case "$MODE" in
  serve)
    cp "$HERE"/serve/tuxiv.conf "$HERE"/serve/job.sh "$HERE"/serve/main.py "$STAGE"/
    ;;
  doctor|batch)
    cp "$HERE"/client/tuxiv.conf "$HERE"/client/job.sh "$HERE"/client/run_flagpilot.py "$STAGE"/
    [[ -f "$TASKS/index/tasks.jsonl" ]] || { echo "题库索引不存在：$TASKS（见 docs/DATASET.md）" >&2; exit 1; }
    # 代码 + 题库。答案表与题解目录一律不进包——和 deploy/upload.sh 同一条硬规则。
    tar -C "$ROOT" --exclude='__pycache__' --exclude='*.pyc' -cf - flagpilot | tar -C "$STAGE" -xf -
    mkdir -p "$STAGE/tasks"
    tar -C "$TASKS" --exclude='_private' --exclude='with-solutions' --exclude='solution' \
        --exclude='__pycache__' --exclude='.DS_Store' -cf - . | tar -C "$STAGE/tasks" -xf -
    if find "$STAGE" \( -name '_private' -o -name 'with-solutions' -o -name 'solution' \
                        -o -name 'gold_private*' \) | grep -q .; then
      echo "✗ 暂存目录里出现答案相关路径，已中止：$STAGE" >&2; exit 1
    fi
    SPLIT="${FP_SPLIT:-dev}"
    if [[ "$SPLIT" != "all" && ! -f "$STAGE/tasks/index/splits.json" ]]; then
      # 题库里没有划分文件（团队尚未冻结）：用同一个确定性生成器现场生成 proposed 划分。
      python3 "$ROOT/scripts/make_split.py" --root "$STAGE/tasks" --seed 0 \
        --out "$STAGE/tasks/index/splits.json" >/dev/null
    fi
    WORKERS="${FP_WORKERS:-4}"
    (( WORKERS <= 4 )) || { echo "FP_WORKERS=$WORKERS 超过课程服务的 4 并发上限" >&2; exit 1; }
    ARGS="--split ${SPLIT} --workers ${WORKERS} --turns ${FP_TURNS:-20} --seeds ${FP_SEEDS:-1}"
    [[ -n "${FP_LIMIT:-}" ]] && ARGS="$ARGS --limit ${FP_LIMIT}"
    [[ -n "${FP_TASKS:-}" ]] && ARGS="$ARGS --tasks ${FP_TASKS}"
    printf 'FP_MODE=%q\nFP_BATCH=%q\nFP_ARGS=%q\n' "$MODE" "${FP_BATCH:-tacc-smoke}" "$ARGS" \
      > "$STAGE/run.env"
    if [[ "$MODE" == "batch" ]]; then
      URL="${2:?batch 需要服务地址，例如 http://10-0-1-10:8000/v1（见服务日志的 SERVICE_BASE_URL）}"
      # tcloud cat 按 CRLF 输出，从里面截出来的地址带 \r（作业 29311 因此在 /models 检查处 InvalidURL）。
      URL="${URL//[$'\r\n\t ']/}"; URL="${URL%/}"
      [[ "$URL" =~ ^https?://[A-Za-z0-9._-]+(:[0-9]+)?/v1$ ]] \
        || { echo "服务地址格式不对：$(printf '%q' "$URL")（例：http://10-0-3-11:8000/v1）" >&2; exit 1; }
      printf '%s\n' "$URL" > "$STAGE/server-url.txt"
    else
      printf '%s\n' 'http://NODE:8000/v1' > "$STAGE/server-url.txt"
    fi
    if [[ -n "${FP_TIME:-}" ]]; then
      perl -pi -e "s/time=00:45:00/time=${FP_TIME}/" "$STAGE/tuxiv.conf"
    fi
    echo "[tcloud] 包大小 $(du -sh "$STAGE" | cut -f1)；run.env：$(tr '\n' ' ' < "$STAGE/run.env")"
    ;;
  *)
    echo "未知模式 $MODE（serve | doctor | batch）" >&2; exit 1 ;;
esac

if [[ -n "${FP_EXCLUDE:-}" ]]; then
  [[ "$FP_EXCLUDE" =~ ^[A-Za-z0-9._-]+(,[A-Za-z0-9._-]+)*$ ]] \
    || { echo "FP_EXCLUDE 格式不对：$FP_EXCLUDE（例：10-0-1-10 或 a,b）" >&2; exit 1; }
  # SDK 把 job.general 的每一项原样转成 #SBATCH --<项>；general 是 tuxiv.conf 的最后一段。
  printf '    - exclude=%s\n' "$FP_EXCLUDE" >> "$STAGE/tuxiv.conf"
fi

if [[ "${FP_DRY_RUN:-0}" == "1" ]]; then echo "[tcloud] dry run，未提交：$STAGE"; exit 0; fi
echo "[tcloud] 暂存目录 $STAGE，提交中…"
cd "$STAGE"
tcloud submit

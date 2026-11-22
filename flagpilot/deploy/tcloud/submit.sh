#!/usr/bin/env bash
# 用课程官方 tcloud SDK 在 TACC 上跑 FlagPilot：不登录服务器、不建 SSH 转发（课程 STUDENT-COMMANDS.md）。
#
#   deploy/tcloud/submit.sh serve                        # 起课程模型服务 qwen3.8-27b（2×3090，≤1h）
#   deploy/tcloud/submit.sh doctor                       # CPU 自检：SDK 引导、课程 Python、题库泄漏检查
#   deploy/tcloud/submit.sh batch http://NODE:8000/v1    # 对正在运行的服务跑一个批次
#   FP_DROP="批次 …" deploy/tcloud/submit.sh cleanup     # 清理 TACC 家目录（盘满时也交得上去，见 cleanup/job.sh）
#
# batch 的参数用环境变量给：
#   FP_BATCH（默认 tacc-smoke）  FP_SPLIT（默认 dev）  FP_LIMIT  FP_TASKS（逗号分隔题名）
#   FP_WORKERS（默认 4，不能超过服务端 --max-num-seqs 4）  FP_TURNS（20）  FP_SEEDS（1）
#   FP_TIME（客户端时长，默认 00:45:00，不能超过分区上限 1h）  FP_DRY_RUN=1（只暂存不提交，暂存目录保留）
#   FP_KEEP_STAGE=1（提交后保留本机暂存目录；默认交完即删）
#   FP_ABLATIONS=B0,B1,B2,B3（同一个服务上依次跑多档，批次名 <FP_BATCH>-<档>；空=单批、默认档）
#   FP_ORDER（lpt|fifo，默认 lpt：预计最慢的题先跑）  FP_LIMIT=0 表示不限题数
#   FP_DEADLINE_EPOCH（服务作业的结束时刻；pipeline.sh 自动给，客户端据此决定下一档还来不来得及）
#   FP_VENDOR=0（不随包带 pycryptodome / gmpy2；默认带，见下）
#   FP_MAX_TOKENS（每轮输出上限；不给就用 CLI 默认 4096。2048 时 dev 上有截断，依据见 flagpilot/config.py 的 ModelSpec）
#   FP_SAMPLE_SEED（采样种子：第 s 次尝试的每个请求带 seed=N+s，vLLM 照此采样；不给就不发，v5–v7 都没发）
#   FP_REPLY_HISTORY=elide  FP_CONTINUE_CUT=1  FP_FENCE_OUTPUT=1（回复中途结束的三个对策，默认都关，见 README 已知局限 16）
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
STAGE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/fp-tcloud-XXXXXX")"
# 本机暂存用完即删（中途出错退出也删）：网页的每条答疑、每次单题都会交一次，不删会在 TMPDIR 里
# 越积越多（单题约 15M，整批约 88M）。dry run 与 FP_KEEP_STAGE=1 保留，便于排查。
[[ "${FP_KEEP_STAGE:-0}" == "1" ]] || trap '[[ "${STAGE_ROOT:-}" == */fp-tcloud-* && -d "$STAGE_ROOT" ]] && rm -rf "$STAGE_ROOT"' EXIT
STAGE="${STAGE_ROOT}/${NAME}"
mkdir -p "$STAGE"
case "$MODE" in
  serve)
    cp "$HERE"/serve/tuxiv.conf "$HERE"/serve/job.sh "$HERE"/serve/main.py "$HERE"/serve/supervise.sh "$STAGE"/
    ;;
  doctor|batch|chat)
    cp "$HERE"/client/tuxiv.conf "$HERE"/client/job.sh "$HERE"/client/run_flagpilot.py "$STAGE"/
    # 课程运行时（PRoot Ubuntu 22 rootfs）没有 xxd / exiftool：带上纯 Python 替身（tests/test_tacc_shims.py）
    mkdir -p "$STAGE/bin"
    cp "$HERE"/client/bin/xxd "$HERE"/client/bin/exiftool "$STAGE/bin/"
    chmod 755 "$STAGE"/bin/*
    # pycryptodome / gmpy2：课程 Python 3.12 没装、计算节点也不一定能上网。在本机下好 manylinux
    # wheel（缓存复用），解进 vendor/py，作业里经 FLAGPILOT_SANDBOX_PYTHONPATH 交给沙箱（动作与重放一致）。
    # 答疑作业不挂题时用不上 pycryptodome / gmpy2：默认不带，包小、提交快
    VENDOR_DEFAULT=1; [[ "$MODE" == "chat" && -z "${FP_CHAT_TASK:-}" ]] && VENDOR_DEFAULT=0
    if [[ "${FP_VENDOR:-$VENDOR_DEFAULT}" == "1" ]]; then
      WH="${FP_WHEEL_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/flagpilot/wheels-cp312-manylinux}"
      mkdir -p "$WH"
      if ! ls "$WH"/pycryptodome-*.whl "$WH"/gmpy2-*.whl >/dev/null 2>&1; then
        python3 -m pip download --quiet --disable-pip-version-check --only-binary=:all: \
          --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 --platform manylinux_2_28_x86_64 \
          --python-version 3.12 --implementation cp --abi cp312 --abi abi3 --abi none \
          pycryptodome gmpy2 -d "$WH" >/dev/null 2>&1 \
          || echo "[tcloud] ⚠ 没下到 pycryptodome/gmpy2 的 wheel，照常提交（用到它们的题会如实报缺库）" >&2
      fi
      python3 - "$WH" "$STAGE/vendor/py" <<'PY'
import pathlib, sys, zipfile
src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
whls = sorted(src.glob("pycryptodome-*.whl")) + sorted(src.glob("gmpy2-*.whl"))
if whls:
    dst.mkdir(parents=True, exist_ok=True)
for w in whls:
    with zipfile.ZipFile(w) as z:
        # 自带的 SelfTest 测试向量用不上；extract 会丢掉绝对路径与 ..，不会写出 dst
        for m in z.namelist():
            if not m.startswith("Crypto/SelfTest/"):
                z.extract(m, dst)
print("[tcloud] 随包 Python 库：" + (", ".join("-".join(w.name.split("-")[:2]) for w in whls) or "无"))
PY
    fi
    # 代码 + 题库。答案表与题解目录一律不进包——和 deploy/upload.sh 同一条硬规则。
    # 代码打成一个 flagpilot.zip，Python 直接从 zip 里 import（run_flagpilot.py）。SDK 上传是按文件一个个走的：
    # 2026-11-20 实测答疑作业 37 个文件、提交 35 s，其中 28 个是这个包——合成一个文件就省掉了大半。
    python3 - "$ROOT/flagpilot" "$STAGE/flagpilot.zip" <<'PY'
import sys, zipfile
from pathlib import Path
src, out = Path(sys.argv[1]), sys.argv[2]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted(src.rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
            z.write(f, "flagpilot/" + f.relative_to(src).as_posix())
PY
    ONLY="${FP_TASKS:-}"; [[ "$MODE" == "chat" ]] && ONLY="${FP_CHAT_TASK:-}"
    if [[ "$MODE" == "chat" && -z "$ONLY" ]]; then
      :   # 不挂题的答疑：不带题库
    elif [[ -n "$ONLY" && "${FP_SPLIT:-dev}" == "all" ]]; then
      # 只跑点名的几道题：只带这几道题的目录与过滤后的索引，包从 ~90M 降到几 M，提交快得多
      [[ "$ONLY" =~ ^[A-Za-z0-9._-]+(,[A-Za-z0-9._-]+)*$ ]] || { echo "FP_TASKS / FP_CHAT_TASK: names separated by commas" >&2; exit 1; }
      mkdir -p "$STAGE/tasks/index"
      python3 - "$TASKS" "$STAGE/tasks" "$ONLY" <<'PY'
import json, shutil, sys
from pathlib import Path
src, dst, want = Path(sys.argv[1]), Path(sys.argv[2]), set(sys.argv[3].split(","))
rows = [json.loads(l) for l in (src / "index" / "tasks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
keep = [r for r in rows if r.get("name") in want]
if len(keep) != len(want):
    sys.exit("unknown challenge(s): " + ", ".join(sorted(want - {r.get("name") for r in keep})))
(dst / "index" / "tasks.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep), encoding="utf-8")
for r in keep:
    d = r.get("assets_dir") or r["name"]
    shutil.copytree(src / d, dst / d, ignore=shutil.ignore_patterns("_private", "with-solutions", "solution", "__pycache__", ".DS_Store"))
PY
    else
      [[ -f "$TASKS/index/tasks.jsonl" ]] || { echo "题库索引不存在：${TASKS}（见 docs/DATASET.md）" >&2; exit 1; }
      mkdir -p "$STAGE/tasks"
      tar -C "$TASKS" --exclude='_private' --exclude='with-solutions' --exclude='solution' \
          --exclude='__pycache__' --exclude='.DS_Store' -cf - . | tar -C "$STAGE/tasks" -xf -
    fi
    if [[ "$MODE" == "chat" ]]; then
      # 这一轮的提问与历史问答（本机网页写好放在 FP_CHAT_DIR）
      [[ -f "${FP_CHAT_DIR:-}/msg.txt" ]] || { echo "chat mode needs FP_CHAT_DIR with msg.txt" >&2; exit 1; }
      mkdir -p "$STAGE/chat"; cp "${FP_CHAT_DIR}/msg.txt" "$STAGE/chat/"
      [[ -f "${FP_CHAT_DIR}/history.json" ]] && cp "${FP_CHAT_DIR}/history.json" "$STAGE/chat/" || printf '[]' > "$STAGE/chat/history.json"
    fi
    if find "$STAGE" \( -name '_private' -o -name 'with-solutions' -o -name 'solution' \
                        -o -name 'gold_private*' \) | grep -q .; then
      echo "✗ 暂存目录里出现答案相关路径，已中止：$STAGE" >&2; exit 1
    fi
    SPLIT="${FP_SPLIT:-dev}"
    if [[ "$MODE" != "chat" && "$SPLIT" != "all" && ! -f "$STAGE/tasks/index/splits.json" ]]; then
      if [[ -f "$ROOT/docs/splits-picoctf-53.json" ]]; then
        # 团队冻结的划分（docs/EVALUATION_BASELINE.md）原样带上。题库索引若与它不符，
        # 作业里的 CLI 会发现：dev 警告，test 拒跑——现场重新生成就会悄悄换成另一种划分。
        cp "$ROOT/docs/splits-picoctf-53.json" "$STAGE/tasks/index/splits.json"
      else
        # 还没有冻结的划分：用同一个确定性生成器现场生成 proposed 划分。
        python3 "$ROOT/scripts/make_split.py" --root "$STAGE/tasks" --seed 0 \
          --out "$STAGE/tasks/index/splits.json" >/dev/null
      fi
    fi
    WORKERS="${FP_WORKERS:-4}"
    (( WORKERS <= 4 )) || { echo "FP_WORKERS=$WORKERS 超过课程服务的 4 并发上限" >&2; exit 1; }
    ORDER="${FP_ORDER:-lpt}"
    [[ "$ORDER" == lpt || "$ORDER" == fifo ]] || { echo "FP_ORDER 只能是 lpt 或 fifo：$ORDER" >&2; exit 1; }
    ARGS="--split ${SPLIT} --workers ${WORKERS} --turns ${FP_TURNS:-20} --seeds ${FP_SEEDS:-1} --order ${ORDER}"
    if [[ -n "${FP_LIMIT:-}" && "${FP_LIMIT}" != "0" ]]; then ARGS="$ARGS --limit ${FP_LIMIT}"; fi
    if [[ -n "${FP_TASKS:-}" ]]; then ARGS="$ARGS --tasks ${FP_TASKS}"; fi
    if [[ "${FP_EVENTS:-0}" == "1" ]]; then ARGS="$ARGS --events-stdout"; fi
    CHAT_ARGS=""
    if [[ -n "${FP_CHAT_TASK:-}" ]]; then CHAT_ARGS="--task ${FP_CHAT_TASK}"; fi
    if [[ "${FP_CHAT_TOOLS:-1}" != "1" ]]; then CHAT_ARGS="$CHAT_ARGS --no-tools"; fi
    if [[ -n "${FP_MAX_TOKENS:-}" ]]; then
      [[ "$FP_MAX_TOKENS" =~ ^[0-9]{3,5}$ ]] || { echo "FP_MAX_TOKENS must be a number such as 4096" >&2; exit 1; }
      ARGS="$ARGS --max-tokens ${FP_MAX_TOKENS}"
    fi
    if [[ -n "${FP_SAMPLE_SEED:-}" ]]; then
      [[ "$FP_SAMPLE_SEED" =~ ^[0-9]{1,9}$ ]] || { echo "FP_SAMPLE_SEED must be a whole number such as 1234" >&2; exit 1; }
      ARGS="$ARGS --sample-seed ${FP_SAMPLE_SEED}"
    fi
    case "${FP_REPLY_HISTORY:-cut}" in
      cut) ;;
      elide) ARGS="$ARGS --reply-history elide" ;;
      *) echo "FP_REPLY_HISTORY must be cut or elide" >&2; exit 1 ;;
    esac
    if [[ "${FP_CONTINUE_CUT:-0}" == "1" ]]; then ARGS="$ARGS --continue-cut"; fi
    if [[ "${FP_FENCE_OUTPUT:-0}" == "1" ]]; then ARGS="$ARGS --fence-output"; fi
    ABL="${FP_ABLATIONS:-}"
    ABL="${ABL// /,}"
    if [[ -n "$ABL" && ! "$ABL" =~ ^B[0-3](,B[0-3])*$ ]]; then
      echo "FP_ABLATIONS 格式不对：${ABL}（例：B0,B1,B2,B3）" >&2; exit 1
    fi
    T="${FP_TIME:-00:45:00}"
    [[ "$T" =~ ^([0-9]{2}):([0-5][0-9]):([0-5][0-9])$ ]] || { echo "FP_TIME 格式不对：${T}（HH:MM:SS）" >&2; exit 1; }
    SECS=$(( 10#${BASH_REMATCH[1]} * 3600 + 10#${BASH_REMATCH[2]} * 60 + 10#${BASH_REMATCH[3]} ))
    (( SECS <= 3600 )) || { echo "FP_TIME=$T 超过分区上限 1 小时" >&2; exit 1; }
    DL="${FP_DEADLINE_EPOCH:-0}"
    [[ "$DL" =~ ^[0-9]+$ ]] || { echo "FP_DEADLINE_EPOCH 必须是整数秒：$DL" >&2; exit 1; }
    printf 'FP_MODE=%q\nFP_BATCH=%q\nFP_ARGS=%q\nFP_ABLATIONS=%q\nFP_CLIENT_SECONDS=%q\nFP_DEADLINE_EPOCH=%q\nFP_CHAT_ARGS=%q\n' \
      "$MODE" "${FP_BATCH:-tacc-smoke}" "$ARGS" "$ABL" "$SECS" "$DL" "$CHAT_ARGS" > "$STAGE/run.env"
    if [[ "$MODE" == "batch" || "$MODE" == "chat" ]]; then
      URL="${2:?batch 需要服务地址，例如 http://10-0-1-10:8000/v1（见服务日志的 SERVICE_BASE_URL）}"
      # tcloud cat 按 CRLF 输出，从里面截出来的地址带 \r（作业 29311 因此在 /models 检查处 InvalidURL）。
      URL="${URL//[$'\r\n\t ']/}"; URL="${URL%/}"
      [[ "$URL" =~ ^https?://[A-Za-z0-9._-]+(:[0-9]+)?/v1$ ]] \
        || { echo "服务地址格式不对：$(printf '%q' "$URL")（例：http://10-0-3-11:8000/v1）" >&2; exit 1; }
      printf '%s\n' "$URL" > "$STAGE/server-url.txt"
    else
      printf '%s\n' 'http://NODE:8000/v1' > "$STAGE/server-url.txt"
    fi
    if [[ "$T" != "00:45:00" ]]; then
      perl -pi -e "s/time=00:45:00/time=${T}/" "$STAGE/tuxiv.conf"
    fi
    echo "[tcloud] 包大小 $(du -sh "$STAGE" | cut -f1)；run.env：$(tr '\n' ' ' < "$STAGE/run.env")"
    ;;
  cleanup)
    # 只交 job.sh + tuxiv.conf + run.env（几 KB）：家目录满了也上得去。复用 flagpilot-client 这个 WORKDIR，
    # 上一批解开的题库和随包库被这几个小文件替换掉。
    cp "$HERE"/client/tuxiv.conf "$STAGE"/
    cp "$HERE"/cleanup/job.sh "$STAGE"/job.sh
    [[ -z "${FP_DROP:-}" || "$FP_DROP" =~ ^[A-Za-z0-9._-]+([[:space:]]+[A-Za-z0-9._-]+)*$ ]] \
      || { echo "FP_DROP: batch names separated by spaces" >&2; exit 1; }
    [[ "${FP_PRUNE_LOGS:-0}" =~ ^[01]$ ]] || { echo "FP_PRUNE_LOGS: 0 or 1" >&2; exit 1; }
    printf 'FP_MODE=cleanup\nFP_DROP=%q\nFP_PRUNE_LOGS=%s\n' "${FP_DROP:-}" "${FP_PRUNE_LOGS:-0}" > "$STAGE/run.env"
    perl -pi -e "s/time=00:45:00/time=00:10:00/" "$STAGE/tuxiv.conf"
    ;;
  *)
    echo "未知模式 ${MODE}（serve | doctor | batch | chat | cleanup）" >&2; exit 1 ;;
esac

if [[ -n "${FP_EXCLUDE:-}" ]]; then
  [[ "$FP_EXCLUDE" =~ ^[A-Za-z0-9._-]+(,[A-Za-z0-9._-]+)*$ ]] \
    || { echo "FP_EXCLUDE 格式不对：${FP_EXCLUDE}（例：10-0-1-10 或 a,b）" >&2; exit 1; }
  # SDK 把 job.general 的每一项原样转成 #SBATCH --<项>；general 是 tuxiv.conf 的最后一段。
  printf '    - exclude=%s\n' "$FP_EXCLUDE" >> "$STAGE/tuxiv.conf"
fi

if [[ "${FP_DRY_RUN:-0}" == "1" ]]; then trap - EXIT; echo "[tcloud] dry run，未提交：$STAGE"; exit 0; fi
if [[ "$MODE" != serve && "$MODE" != cleanup ]]; then
  # SDK 上传是按文件逐个走一趟的（2026-11-20 实测每个约 0.7 s；带随包库的单题作业有 200 多个文件，光传就要两分钟）。
  # 除了 SDK 自己要读的 tuxiv.conf、作业入口 job.sh 和 run.env，其余打成一个 payload.tgz，job.sh 开头解开。
  # COPYFILE_DISABLE：macOS 的 tar 默认给每个文件附一份 ._* 扩展属性，解到 Linux 上全是垃圾（GNU tar 忽略这个变量）
  (cd "$STAGE" && set -- $(ls -A | grep -v -x -e tuxiv.conf -e job.sh -e run.env) && COPYFILE_DISABLE=1 tar -czf payload.tgz "$@" && rm -rf -- "$@")
  # job.sh 先核对包到齐了没有（SDK 交上来过包没到的作业，2026-11-21 作业 30538）
  printf 'FP_PAYLOAD=%s\n' "$( (sha256sum "$STAGE/payload.tgz" 2>/dev/null || shasum -a 256 "$STAGE/payload.tgz") | cut -d' ' -f1)" >> "$STAGE/run.env"
fi
echo "[tcloud] 暂存目录 ${STAGE}，提交中…"
cd "$STAGE"
# TACC 的登录网关忙时会直接断开 SSH 握手（"Failed to create remote session"，2026-11-20/21 反复出现）：
# 这时什么都还没交上去，等一会儿再交不会重复。别的错误原样报出、不重试。FP_SUBMIT_TRIES=1 关掉重试
# （网页自己重试，好把每次等待显示在页面上）。
TRIES="${FP_SUBMIT_TRIES:-5}"
[[ "$TRIES" =~ ^[0-9]+$ ]] && (( TRIES >= 1 )) || TRIES=5
for (( i = 1; i <= TRIES; i++ )); do
  rc=0; out=$(tcloud submit 2>&1) || rc=$?
  printf '%s\n' "$out"
  grep -q 'Submitted batch job' <<<"$out" && exit "$rc"      # submitted: report tcloud's own status, as before
  # 上传途中断线（"Failed to upload env repository"）同样还没交上去；卷满了（No space left on device）重试没用
  if (( i < TRIES )) && grep -q -e 'Failed to create remote session' -e 'ssh: handshake failed' -e 'Failed to upload' <<<"$out" \
     && ! grep -q -e 'No space left on device' -e 'Disk quota exceeded' <<<"$out"; then
    wait_s=$(( i * i * ${FP_SUBMIT_WAIT:-5} ))
    echo "[tcloud] TACC's login gateway dropped the connection; trying again in ${wait_s} s ($(( i + 1 ))/${TRIES})" >&2
    sleep "$wait_s"
    continue
  fi
  exit "$(( rc ? rc : 1 ))"
done

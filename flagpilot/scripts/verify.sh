#!/usr/bin/env bash
# 一键自检：不花 GPU、不联网、不需要模型权重。
#
# 它验证的不是"模型强不强"，而是"实验装置准不准"——
# 判据、消融开关、探针、归因、口径，任何一处坏了，跑出来的数都是假的。
#
#   bash scripts/verify.sh            # 全部检查
#   bash scripts/verify.sh --keep     # 保留 runs/verify* 产物
#
# 退出码 0 = 全过；非 0 = 有 FAIL，先修再看结果。

set -uo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
BATCH="verify"
KEEP=0
[[ "${1:-}" == "--keep" ]] && KEEP=1

# 自检用的是 mock 模型，它的"经验"不能写进真实的 runs/memory——
# 否则下次真解题时 prompt 里会混进一堆 mock 记录。指到临时目录，跑完就丢。
export FLAGPILOT_MEMORY="${FLAGPILOT_MEMORY:-$(mktemp -d -t fp-verify-memory-)}"

PASS=0
FAIL=0
ROWS=()

record() {  # $1=名称 $2=PASS/FAIL $3=说明
  local tag="$2"
  if [[ "$tag" == "PASS" ]]; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); fi
  ROWS+=("$(printf '%-6s %s — %s' "[$tag]" "$1" "$3")")
}

json_get() {  # $1=file $2=python 表达式（r 为记录）
  "$PY" - "$1" "$2" <<'PY' 2>/dev/null
import json, sys
path, expr = sys.argv[1], sys.argv[2]
rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
print(eval(expr, {"json": json, "rows": rows}))
PY
}

echo "FlagPilot 自检 · $(date '+%Y-%m-%d %H:%M')"
echo "python: $("$PY" -c 'import sys;print(sys.version.split()[0])')   cwd: $PWD"
echo

[[ "$KEEP" == "0" ]] && rm -rf runs/verify runs/verify-*

# ---------------------------------------------------------------- 1. 单测
OUT="$("$PY" -m unittest discover -s tests -t . 2>&1)"
RC=$?
N="$(echo "$OUT" | grep -oE 'Ran [0-9]+ test' | grep -oE '[0-9]+' | head -1)"
if [[ $RC -eq 0 ]]; then
  record "单元测试" PASS "${N:-?} 条全绿"
else
  record "单元测试" FAIL "见 $PY -m unittest discover -s tests -t . 的报错"
  echo "$OUT" | tail -20
fi

# ---------------------------------------------------------------- 2. 单题全链路
"$PY" -m flagpilot run --task general-004 --model mock --batch "$BATCH" >/dev/null 2>&1
if [[ -f "runs/$BATCH/runs.jsonl" ]]; then
  V="$(json_get "runs/$BATCH/runs.jsonl" "rows[-1]['verdict']")"
  T="$(json_get "runs/$BATCH/runs.jsonl" "rows[-1]['turns']")"
  if [[ "$V" == "verified" ]]; then
    record "单题全链路（提交→重放）" PASS "general-004 verified / ${T} 轮"
  else
    record "单题全链路（提交→重放）" FAIL "期望 verified，实际 $V"
  fi
else
  record "单题全链路（提交→重放）" FAIL "没有生成 runs/$BATCH/runs.jsonl"
fi

# ---------------------------------------------------------------- 3. 四档消融
ABL_OK=0
ABL_DETAIL=""
for b in B0 B1 B2 B3; do
  "$PY" -m flagpilot batch --batch "$BATCH-$b" --tasks general-004,crypto-005 \
      --model mock --ablate "$b" --turns 6 --workers 1 >/dev/null 2>&1
  F="runs/$BATCH-$b/runs.jsonl"
  if [[ -f "$F" ]]; then
    GOT="$(json_get "$F" "sorted({r['ablation'] for r in rows})")"
    CNT="$(json_get "$F" "len(rows)")"
    if [[ "$GOT" == "['$b']" && "$CNT" == "2" ]]; then
      ABL_OK=$((ABL_OK + 1))
    else
      ABL_DETAIL="$ABL_DETAIL $b(ablation=$GOT n=$CNT)"
    fi
  else
    ABL_DETAIL="$ABL_DETAIL $b(无产物)"
  fi
done
if [[ $ABL_OK -eq 4 ]]; then
  record "四档消融各跑 2 题" PASS "B0–B3 均按档位落盘"
else
  record "四档消融各跑 2 题" FAIL "${ABL_OK}/4 通过；$ABL_DETAIL"
fi

# ---------------------------------------------------------------- 4. B0 真的没截断
POL="$("$PY" - <<'PY' 2>/dev/null
import json
from flagpilot.config import ABLATIONS
for k in ("B0", "B3"):
    a = ABLATIONS[k]
    print(k, "truncate=" + str(a.truncate))
PY
)"
if echo "$POL" | grep -q "B0 truncate=False" && echo "$POL" | grep -q "B3 truncate=True"; then
  record "消融开关生效（B0 无截断）" PASS "B0=False, B3=True"
else
  record "消融开关生效（B0 无截断）" FAIL "$POL"
fi

# ---------------------------------------------------------------- 5. 判据负向
NEG="$("$PY" - <<'PY' 2>/dev/null
import hashlib
from pathlib import Path
from flagpilot import verify
from flagpilot.tasks import Task

flag = "picoCTF{synthetic_negative_check}"
t = Task(task_id=-1, name="synthetic", category="X", title="", query="",
         gold_sha256=hashlib.sha256(flag.encode()).hexdigest(),
         assets=[], assets_dir="tasks/none", suggested_tools=[], scope="core")

a = verify.evaluate(t, flag, f'print("{flag}")', Path("."), timeout_s=10)
b = verify.evaluate(t, "picoCTF{wrong}", "print(1)", Path("."), timeout_s=10)
c = verify.evaluate(t, flag, 'print("picoCTF{synthetic_negative_check}")', Path("."), timeout_s=10)
print(f"{a.verdict}/{a.reason} {b.verdict}/{b.reason}")
PY
)"
if [[ "$NEG" == "unverified/literal_flag wrong/wrong_flag" ]]; then
  record "判据负向（硬编码 / 错答案）" PASS "硬编码→unverified，错答案→wrong"
else
  record "判据负向（硬编码 / 错答案）" FAIL "实际：$NEG"
fi

# ---------------------------------------------------------------- 6. 记忆探针
"$PY" -m flagpilot probe --limit 2 --model mock --batch "$BATCH-probe" --mode brief >/dev/null 2>&1
if [[ -f "runs/$BATCH-probe/probe.jsonl" ]]; then
  PH="$(json_get "runs/$BATCH-probe/probe.jsonl" "sum(1 for r in rows if r['hit'])")"
  PN="$(json_get "runs/$BATCH-probe/probe.jsonl" "len(rows)")"
  PR="$(json_get "runs/$BATCH-probe/probe.jsonl" "sum(1 for r in rows if r['refused'])")"
  if [[ "$PN" == "2" && "$PH" == "0" && "$PR" == "2" ]]; then
    record "记忆探针（mock 应全 UNKNOWN）" PASS "2 题，命中 0，refused 2"
  else
    record "记忆探针（mock 应全 UNKNOWN）" FAIL "n=$PN hit=$PH refused=$PR"
  fi
else
  record "记忆探针（mock 应全 UNKNOWN）" FAIL "没有生成 probe.jsonl"
fi

# ---------------------------------------------------------------- 7. 报告与对比
"$PY" -m flagpilot report --batch "$BATCH-B3" --md "runs/$BATCH/report.md" >/dev/null 2>&1
CMP="$("$PY" -m flagpilot compare --batches "$BATCH-B0,$BATCH-B1,$BATCH-B2,$BATCH-B3" 2>/dev/null)"
if [[ -f "runs/$BATCH/report.md" ]] && grep -q "95% CI" "runs/$BATCH/report.md" \
   && [[ $(echo "$CMP" | grep -c "^| $BATCH-B") -eq 4 ]]; then
  record "报告导出与四档对比" PASS "Markdown 含 CI；compare 4 行"
else
  record "报告导出与四档对比" FAIL "report.md 缺失或无 CI，或 compare 行数不对"
fi

# ---------------------------------------------------------------- 8. 失败归因
TAG="$("$PY" - <<'PY' 2>/dev/null
from flagpilot import tagger
rec = {"verdict": "unverified", "reason": "literal_flag", "turns": 3, "invalids": 0,
       "replay": {"reason": "literal_flag", "detail": "exact"}}
print(tagger.classify_run(None, rec)["primary"])
PY
)"
if [[ "$TAG" == "hardcoded" ]]; then
  record "失败归因（硬编码→hardcoded）" PASS "primary=hardcoded"
else
  record "失败归因（硬编码→hardcoded）" FAIL "primary=$TAG"
fi

# ---------------------------------------------------------------- 10. 前端接口
WOUT="$("$PY" -m unittest tests.test_web 2>&1)"
WRC=$?
if [[ $WRC -eq 0 ]]; then
  record "前端接口（四档/探针/模型切换）" PASS "控件齐、档位透传、模型可切、断线可回放"
else
  record "前端接口（四档/探针/模型切换）" FAIL "见 $PY -m unittest tests.test_web"
  echo "$WOUT" | tail -12
fi

# ---------------------------------------------------------------- 10b. 上传 + 答疑
UOUT="$("$PY" -m unittest tests.test_custom tests.test_chat 2>&1)"
URC=$?
if [[ $URC -eq 0 ]]; then
  record "上传题目 + 答疑模式" PASS "无 gold 只能判 reproduced；<run> 真执行"
else
  record "上传题目 + 答疑模式" FAIL "见 $PY -m unittest tests.test_custom tests.test_chat"
  echo "$UOUT" | tail -14
fi

# ---------------------------------------------------------------- 11. 环境（只提示）
CAPS="$("$PY" -c "
from flagpilot.sandbox import probe_capabilities
c=probe_capabilities(force=True)
print(c.net_isolation)" 2>/dev/null)"
if [[ "$CAPS" == "netns" ]]; then
  record "网络隔离等级" PASS "netns（可作强隔离证据）"
else
  record "网络隔离等级" PASS "proxy-only（只提示，不阻断；集群上必须复验）"
fi

# ---------------------------------------------------------------- 汇总
echo
printf '%s\n' "${ROWS[@]}"
echo
echo "通过 $PASS 项，失败 $FAIL 项"
if [[ "$FAIL" -gt 0 ]]; then
  echo "→ 先修失败项，再谈跑实验。装置不对，数字没有意义。"
  exit 1
fi
echo "→ 装置可用。下一步：本地 8B 跑 3 道题验行为，或直接交 TACC 作业。"
[[ "$KEEP" == "0" ]] && rm -rf runs/verify runs/verify-*
exit 0

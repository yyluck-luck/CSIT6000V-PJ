# Implementation Status

Last updated: **2026-11-20**. This document is the primary reference for project progress and the remaining TODO list. README files summarize it; new proposals should be identified separately from existing project commitments.

[PR #3](https://github.com/yyluck-luck/CSIT6000V-PJ/pull/3) merged the API integration, English writeups, and optional encrypted storage into `main` (`3d496bb`). Usage: [writeups and storage](WRITEUPS_AND_STORAGE.md).

Legend: ✅ implemented and exercised | 🟡 partially complete | ❌ pending

---

## 一、课程要求

| 要求 | 状态 | 证据 / 缺口 |
|---|---|---|
| Task scenario | ✅ | Offline CTF solving. The currently supplied local dataset has 53 tasks: General Skills 25 / Cryptography 16 / Forensics 12 (43 `scope=core` + 10 `scope=extra-gs`). |
| Frontend: input, progress, and result review | ✅ | Task selection/upload, SSE events, tool observations, flag/script/replay review, saved-run recovery, and Writeup download. |
| 后端：agent 工作流、模型 API、工具与数据 | ✅ | `agent.py` + `client.py` + `tools.py` + `sandbox.py` + `context.py` + `memory.py` |
| Evaluation and failure analysis | 🟡 | DeepSeek Flash B3 attempted 43 of 53 frozen tasks. It returned 25 correct flags (58.1%) and verified 22 solves by replay (51.2%). Formal E1/E2/E3 comparisons and the evaluation report remain pending. |
| Demo & Final Presentation | 🟡 | 实时 demo 可跑（`scripts/start-ollama.sh`）；**slides / 录屏 / 报告未做** |
| Execution on TACC | 🟡 | The teammate report dated 2026-11-17 records `crypto-005` as `verified` in job 29430 and through a local Web/SSH-tunnel run. Course-platform launch alignment and persistent result/GPU ledgers remain pending. |
| 交付物：repo / report / demo / slides | 🟡 | repo ✅、demo ✅、report ❌、slides ❌ |

The application and demo workflow are implemented. Formal experiment results, the final report, slides, and the recorded demo remain project deliverables.

---

## 二、Proposal 承诺

### 判据与流程

| 承诺 | 状态 | 说明 |
|---|---|---|
| 重放验证器（干净沙箱重跑 + 字面量检查 + 四态） | ✅ | `verify.py`；含 `material_text()` 豁免与 `reproduced` 降级 |
| 提交前自检（pre-submit dry run） | ✅ | 新增 `run_script` 动作：在只含 `./challenge/` 的干净目录里先跑一遍候选 `solve.py`，把"脚本能不能跑通"提前到提交之前。代价一轮，而不是一次被拒（被拒还可能在最后一轮、拿不到修复额度）。`--no-self-check` 可关 |
| 材料缺失重放 | ✅ | 第四道判据，**两段**：① 清空 `challenge/` 再跑；② 换成同名等长假材料再跑。任一段仍打印出提交的 flag 就判 `unverified / hardcoded_no_material`。第二段是 30052 实跑暴露的漏洞补丁——模型被拒一次后补了一行 `open('./challenge/rot13.py')` 却从不使用读到的内容，清空能挡（open 抛异常）、假材料挡不住，故两段都要跑。抓的是"字面量检查漏掉的编码形态"与"读了不用"，与编码无关，只问答案是否由材料推出。无附件的题自动跳过，`--no-material-check` 可关 |
| Six-stage workflow including writeup | ✅ | Completed attempts generate an English trace-based report with CLI regeneration and Web download. |
| Typed submission feedback and bounded retries | ✅ | Replay rejections now return typed feedback (reason, traceback tail, replay stdout tail, per-reason hint) and allow one flag retry plus one independent script repair. Repairs are **granted even on the last turn** (`repair_extends_budget`, default on) so the proposal's "a rejection returns feedback" holds even when the model submits at the turn limit; borrowed turns are recorded separately as `bonus_turns`. See `repair_message()` in `agent.py`. |
| Attempt isolation and resource limits | 🟡 | Container backend implemented: probes apptainer/singularity/docker/podman, runs each attempt in a container when a local image is available, otherwise falls back to process-level isolation. The effective level is recorded in `caps.isolation`. **Not yet exercised with a real container runtime** — neither this machine nor the course nodes currently have one. |

### 工具箱

| 承诺 | 状态 | 说明 |
|---|---|---|
| Python + crypto / math | ✅ | `python` 动作，标准库可用 |
| shell 通用命令 | ✅ | `shell` 动作，白名单环境变量 |
| file / strings / decode | ✅ | `decode` 支持 rot13 / base64 / hex / url |
| SMT（z3） | ✅ | `smt` 动作，自动注入 `from z3 import *`。`z3-solver` 已在 `requirements.txt`（纯 Python 轮子，可随仓库交付），本机实测端到端可用（解出 `x>10 ∧ x<13` → 11）。没装时走"明确报错 + 建议改用 python"分支，不静默失败 |
| binutils / 反汇编 / 调试器 | 🟡 | `disasm` 动作已实现（`objdump -d`，可选 section，输出截断到 400 行），本机 objdump 可用。**gdb 未安装**，调试只能靠 `shell` 调外部工具——它是系统级二进制，不能随仓库交付，已在 DEPLOY-TACC.md 列明获取方式 |

### 模型与轨道

| 承诺 | 状态 | 说明 |
|---|---|---|
| Track L: served model | 🟡 | The teammate report records a successful course-platform run with Qwen3.8-27B-AWQ-INT4. Align the proposal model scope and deployment guide with the course service, its one-hour limit, and its fixed serving parameters. |
| Track H: DeepSeek Flash | 🟡 | DeepSeek Flash B3 attempted 43 of 53 frozen tasks. It returned 25 correct flags (58.1%) and verified 22 solves by replay (51.2%). The separate synthetic Web smoke test verified the Writeup download. See the [baseline results](EVALUATION_BASELINE.md#track-h-offline-baseline). |
| Hybrid 自动升级 + stall 检测 + 升级预算 | 🟡 | 已实现：连续 k 轮无新证据（工具输出指纹去重）触发 L→H 升级，受 `max_escalations` 与 `max_hosted_tokens` 约束；L/H 的 token 与成本分开记账（`summary["hybrid"]`、`cost_usd_total`）。CLI：`--hybrid --stall-turns --max-escalations --max-hosted-tokens`。**未用真实 DeepSeek key 实跑**，目前由桩 client 的单测覆盖 |

### 日志与指标

| 承诺 | 状态 | 说明 |
|---|---|---|
| Track, timings, tokens, cache, and estimated cost | ✅ | The baseline recorded 323,540 input and 37,255 output tokens. FlagPilot estimated $0.033929 from its configured price table. Provider billing reconciliation remains pending. |
| GPU seconds | 🟡 | Collection and report support are implemented. The teammate TACC report records lost temporary run outputs and no persistent GPU ledger; course-platform persistence and ledger collection remain pending. |
| p50 / p95 延迟分位 | ✅ | 端到端墙钟 p50/p95（全部 attempt）、解题时间 p50/p95（仅 verified）、TTFT p50 |
| Encrypted storage | ✅ | Optional Fernet encryption covers records, writeups, and completed attempt files. Local history migration, compatible readers, and encrypted replay/downloads were exercised. Keys stay outside Git; [setup](WRITEUPS_AND_STORAGE.md). |

### 实验

| 实验 | 状态 | 说明 |
|---|---|---|
| E1: task quality and B0–B3 ablations | 🟡 | A single DeepSeek B3 baseline is recorded. The matched B0–B3 comparison with repeats remains pending. |
| E2: verification, probes, and failure classification | 🟡 | Probes and labels are implemented. Failure attribution now separates **output truncation** (`finish_reason=length`, or reported tokens hitting `max_tokens`) from genuine protocol violations — the two used to be merged, which mislabelled 2 of 3 "protocol" failures in the 27B field run. The classifier also re-reads old traces (token counts per turn) so past runs can be re-labelled without re-execution. TACC and DeepSeek reports contain correct-flag/replay-failure examples; systematic evaluation and manual review of at least 30 cases remain pending. |
| E3: efficiency experiments | 🟡 | Client grids, latency summaries, comparisons, and GPU accounting are implemented. The teammate report says the course service fixes concurrency at 4 and enables prefix caching; adapt those experiment dimensions before collecting formal results. |
| E4: escalation strategy | 🟡 | Hybrid 装置已实现（stall 检测 + 升级预算 + 双轨记账），单测覆盖；**真实 L→H 实跑未做**，需要 H 轨 key 与 L 轨端点 |

### 数据集

| 承诺 | 状态 | 说明 |
|---|---|---|
| NYU CTF Bench dev21 / test103 | 🟡 | Loader 已实现（`flagpilot/datasets.py`，CLI `dataset`）。上游运行时 clone、不入库、产物只留 sha256。按 proposal 的 "only offline challenges" 筛选后：**dev 16 / test 46**；被排除的 5 / 57 题需要 docker compose 起服务，逐条记在 `EXCLUDED.md` |
| InterCode-CTF scope | ✅ | The freeze covers 53 offline picoCTF tasks derived from InterCode (43 core and 10 extra General Skills). The index hash and the exclusion of `general-041` are recorded in the [evaluation baseline](EVALUATION_BASELINE.md#frozen-picoctf-split). |
| Cybench 21 | ❌ | 未接入（proposal 标为 stretch，且 cut order 里排第二） |
| RE 子集 | ✅ | 已覆盖：NYU dev 9 题 / test 31 题。此前题库**一道 RE 题都没有** |
| Current local dataset | ✅ | 两套题库并存：picoCTF 派生 53 题（General 25 / Crypto 16 / Forensics 12；core 43 + extra-gs 10），以及 NYU CTF Bench 离线 crypto+RE（dev 16 / test 46）。都用 `FLAGPILOT_TASKS` 切换 |

> 口径差异必须写进报告：要么补 loader，要么明确改为 picoCTF 并说明原因。
> 不能一边用 picoCTF 跑、一边写"在 NYU CTF Bench 上评测"。

题库的接入方式、为什么不随仓库分发、来源与授权边界：
见 **[DATASET.md](DATASET.md)**。上游是 InterCode（MIT），题目原始出处是 picoCTF（CMU），
两边的授权不同，仓库里用 `NOTICE` 分别声明。

### 工程与交付

| 承诺 | 状态 | 说明 |
|---|---|---|
| Repeated attempts / seeds | 🟡 | `--seeds` creates repeated attempts and records their identifiers. The current model request does not send a sampling seed; distinguish repetitions from provider seed control in the evaluation. |
| Wilson 置信区间 | ✅ | `metrics.py` |
| 配对比较 | ✅ | `pair` 子命令：按 (题, seed) 配对，给出修好 / 弄坏 / 都对 / 都错与精确 McNemar p 值 |
| Dev/test mechanism and test-run guard | ✅ | The [frozen 53-task split](splits-picoctf-53.json) has 27 dev and 26 test tasks. Membership is deterministic and tied to the dataset index SHA-256. The existing guard requires confirmation for final test batches. |
| Track launch paths | ✅ | 三条路径都写进 [DEPLOY-TACC.md](DEPLOY-TACC.md)：① 离线回放（助教零依赖）；② 集群内跑完再拉回 `runs/`；③ 本机前端 + 集群后端（隧道，**仅自用，不写进交付物**——原因与合规边界见文档第四节）。启动脚本 `scripts/tunnel.sh` |
| 录屏 demo / report / slides | ❌ | 未做。离线回放（`flagpilot web --replay`）已经把"演示"的技术前提做完了：不接模型也能把每一次真实运行逐轮回放 |

---

## 三、Remaining Project TODOs

Use this list for future task selection. Entries come from the project commitments and remaining gaps in the recorded runs; implementation suggestions outside this scope should be labeled separately.

| ID | Remaining task | Deliverable |
|---|---|---|
| 2 | Run E1 B0–B3 comparisons | Per-setting results, repeated-attempt configuration, confidence intervals, and paired comparisons |
| 3 | Finalize the TACC execution path and persistent outputs | Course-compatible launch instructions, persisted traces, and GPU-seconds ledger; include the reported interpreter/PATH fix |
| 4 | Reconcile Track H estimates with provider billing | Compare recorded API usage and FlagPilot estimates with the provider bill. The synthetic smoke test and the 43-task subset baseline are recorded. |
| 5 | Run E2 probes and review at least 30 classifications | Raw/verified comparison, probe statistics, reviewed labels, and failure analysis |
| 6 | Adapt and run E3 under the available serving constraints | Agreed experiment grid, quality guard, timings, and available cost/cache/GPU measurements |
| 7 | Prepare the report, slides, and recorded demo | Final course deliverables |
| 8 | Align benchmark and model descriptions with the actual evaluation | Consistent proposal/report/dataset/model scope |
| 10 | Run E4 (Track L vs H vs hybrid) | Hybrid implementation is ready; needs a real DeepSeek key and L-track access, then report verified solves vs hosted tokens/cost and escalation rate |
| 12 | Validate the container backend on a real runtime | One run on TACC (apptainer) or a host with docker, confirming `caps.isolation == "container"` and that replay still passes inside the container |
| 13 | Install optional deps for the full toolbox | `z3-solver` for the `smt` action, `gdb` for debugging; then re-run `doctor` and record which toolbox items are actually available |

Completed from the previous gap list:
- **Item 1, picoCTF dataset and dev/test freeze.** The [evaluation baseline](EVALUATION_BASELINE.md) records the 53-task scope, index hash, and frozen split.
- **Item 4 (smoke test).** The real DeepSeek CLI and Web checks passed on the bundled synthetic fixtures.
- **item 9, Writeup generation** — merged in PR #3.
- **item 11, typed replay feedback** — replay rejections now return reason + traceback tail + replay stdout + a per-reason hint, with one flag retry and one independent script repair.
- **item 10 (implementation half), Hybrid** — stall detector, escalation budget, and split L/H accounting are in place; only the real-key run remains.
- **NYU CTF Bench loader** — `flagpilot dataset` clones upstream at run time, keeps only offline crypto+RE, and writes `EXCLUDED.md` for every exclusion.

---

## 四、Recorded Verification

| Scope | Recorded result |
|---|---|
| Focused feature checks | 307 unit and integration tests pass, using synthetic data, mock models, and local HTTP servers. |
| Complete offline suite | All 307 pass. `scripts/verify.sh` passes 12 of 12 end-to-end checks: CLI surface, verdict logic, replay paths, web API, offline replay, and packaging rules. |
| Mock CLI and Web | `general-004` reached `verified` in 3 turns. |
| Real DeepSeek Flash, synthetic smoke | Three CLI fixtures and one separate Web run reached `verified / match`. Provider-reported token totals matched the traces, and the Web Writeup download returned HTTP 200. See the [run summary](EVALUATION_BASELINE.md#track-h-smoke-test). |
| Real DeepSeek Flash, B3 baseline | DeepSeek Flash B3 attempted 43 of 53 frozen tasks. It returned 25 correct flags (58.1%) and verified 22 solves by replay (51.2%). See the [split-level results and task verdicts](EVALUATION_BASELINE.md#track-h-offline-baseline). |
| Earlier DeepSeek Flash benchmark smoke | `crypto-005`: 2 turns, about 2.84 s, correct flag, `unverified / script_error` because the submitted reproduction script failed. |
| Writeup and storage | Local history migration covered 15 record files. The previous real run was recovered after restart and its encrypted Writeup downloaded with the original verdict preserved. |
| Team-reported TACC result | The 2026-11-17 report records job 29430 as `verified`, prefix-cache samples of 20–27%, and a successful local Web/tunnel run. Persistent traces/GPU accounting and formal experiments remain separate follow-up work. |

Reproduce the local checks from the `flagpilot/` directory:

```bash
# Unit and integration tests (configure the external dataset first).
python3 -m unittest discover -s tests -t . -q

# Focused writeup/encryption checks; install the optional encryption dependency.
python3 -m unittest tests.test_storage_writeup tests.test_writeup_web -q

# Environment and dataset checks.
python3 -m flagpilot doctor

# H-track example; loads credentials from the ignored local .env.
scripts/track_h.sh h-smoke --tasks crypto-005 --workers 1 --turns 6
```

Evidence sources: merged PR #3, local trace/test results, and the team-supplied report
`TACC-实机验证与v2推进状况(1).md` dated 2026-11-17. TACC entries above reflect that
reported snapshot; its uncommitted platform changes still need to be reconciled with this repository.
